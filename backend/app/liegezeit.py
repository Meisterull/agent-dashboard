"""Hinweis auf liegende Tasks im Handbetrieb (Issue #59).

Ist die Automatik eines Agenten aus, warten Tasks, bis eine interaktive
Sitzung sie aufgreift — im Echtbetrieb 20–50 min, einmal 193 min, ohne dass
es jemand merkte. Dieses Modul meldet das:

  * `liegt` (Warnung im Ereignis-Log + EIN gebündelter Push je Agent):
    Tasks, die länger als die Schwelle unberührt in der Inbox liegen. Jeder
    Task wird einmal gemeldet (Zustand `<mailbox>/.liegt.json`); „später"
    stellt die Meldung für eine Stunde zurück.
  * dasselbe, früher als die Pflege (24 h): beanspruchte Tasks einer
    SITZUNG ohne Lebenszeichen seit `still_min`.
  * Einmal-Lauf: „per Automatik abarbeiten" schaltet die Automatik des
    Agenten ein und — sobald Inbox und Arbeit leer sind — wieder aus.

Abschaltbar (Kommentar im Issue): global, je Agent, pausierbar (auch direkt
aus dem Push: 24 h Ruhe für den Agenten) und nur zu den eingestellten Zeiten
(Default Mo–Fr 07–18 Uhr — nachts kommt kein Push, nur weil um 22 Uhr noch
jemand etwas einstellt; gemeldet wird dann am nächsten Morgen).

Einstellungen: settings.json `liegezeit` (Dialog Einstellungen →
Benachrichtigungen). Nur Standardlib.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app import ereignisse
from app.mailbox import Mailbox, _lese_ordner, atomic_write_json, zu_frueh

STANDARD: dict[str, Any] = {
    "an": True,
    "schwelle_min": 20,        # unberührt in der Inbox
    "still_min": 120,          # beansprucht (Sitzung), aber kein Lebenszeichen
    "von": "07:00", "bis": "18:00",
    "tage": [0, 1, 2, 3, 4],   # Mo=0 … So=6
    "ohne": [],                # Agenten, für die nie gemeldet wird
    "agents": {},              # {name: {"schwelle_min": 45}}
    "pause_bis": {},           # {name | "*": ISO-Zeit} — bis dahin Ruhe
}
MAX_ALTER_H = 48.0     # Älteres ist Bestand, keine Neuigkeit mehr
SPAETER_MIN = 60
PAUSE_STUNDEN = 24


def einstellung(settings: dict[str, Any] | None) -> dict[str, Any]:
    roh = (settings or {}).get("liegezeit")
    aus = dict(STANDARD)
    if isinstance(roh, dict):
        aus.update({k: v for k, v in roh.items() if k in STANDARD and v is not None})
    return aus


def _minuten(text: Any, ersatz: int) -> int:
    try:
        h, m = str(text).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return ersatz


def in_meldezeit(einst: dict[str, Any], jetzt: datetime) -> bool:
    tage = einst.get("tage")
    if isinstance(tage, list) and tage and jetzt.weekday() not in tage:
        return False
    von, bis = _minuten(einst.get("von"), 0), _minuten(einst.get("bis"), 24 * 60)
    minute = jetzt.hour * 60 + jetzt.minute
    return von <= minute < bis if von < bis else True


def pausiert(einst: dict[str, Any], agent: str, jetzt: datetime) -> bool:
    for schluessel in (agent, "*"):
        bis = (einst.get("pause_bis") or {}).get(schluessel)
        try:
            if bis and datetime.fromisoformat(str(bis)).astimezone() > jetzt:
                return True
        except ValueError:
            continue
    return False


def schwelle_min(einst: dict[str, Any], agent: str) -> int:
    eigen = ((einst.get("agents") or {}).get(agent) or {}).get("schwelle_min")
    try:
        return max(1, int(eigen or einst.get("schwelle_min") or 20))
    except (TypeError, ValueError):
        return 20


def _alter_min(env: dict[str, Any], pfad: Path, felder: tuple[str, ...], jetzt: float) -> float:
    for feld in felder:
        try:
            return (jetzt - datetime.fromisoformat(str(env[feld])).astimezone().timestamp()) / 60
        except (KeyError, ValueError, TypeError):
            continue
    try:
        return (jetzt - pfad.stat().st_mtime) / 60
    except OSError:
        return 0.0


def _zustand(box: Mailbox) -> dict[str, Any]:
    try:
        z = json.loads((box.base / ".liegt.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        z = {}
    if not isinstance(z, dict):
        z = {}
    z.setdefault("gemeldet", {})
    return z


def liegende(root: str | os.PathLike, agent: str, einst: dict[str, Any],
             jetzt: float | None = None) -> dict[str, list[dict[str, Any]]]:
    """Was liegt gerade über der Schwelle? → {"wartend": [...], "still": [...]}
    (ohne Rücksicht darauf, was schon gemeldet wurde — fürs Panel)."""
    jetzt = time.time() if jetzt is None else jetzt
    aus: dict[str, list[dict[str, Any]]] = {"wartend": [], "still": []}
    basis = Path(root) / agent
    if not (basis / "inbox").is_dir():
        return aus
    grenze = schwelle_min(einst, agent)
    for pfad, env in _lese_ordner(basis / "inbox"):
        if env.get("kind", "task") != "task" or zu_frueh(env, jetzt):
            continue
        alter = _alter_min(env, pfad, ("created_at",), jetzt)
        if grenze <= alter <= MAX_ALTER_H * 60:
            aus["wartend"].append({"id": env.get("id") or env.get("task_id") or pfad.stem,
                                   "minuten": int(alter), "sender": env.get("sender")})
    still = max(5, int(einst.get("still_min") or 120))
    for pfad, env in _lese_ordner(basis / "inbox" / ".processing"):
        if (env.get("kind", "task") != "task" or env.get("status") == "needs_confirm"
                or env.get("claim_von") == "watcher"):
            continue
        alter = _alter_min(env, pfad, ("zuletzt_aktiv", "claimed_at"), jetzt)
        if still <= alter <= MAX_ALTER_H * 60:
            aus["still"].append({"id": env.get("id") or env.get("task_id") or pfad.stem,
                                 "minuten": int(alter), "sender": env.get("sender")})
    return aus


def pruefe(root: str | os.PathLike, agent: str, settings: dict[str, Any],
           automatik_an: bool, jetzt: datetime | None = None) -> dict[str, Any] | None:
    """Einmal je Takt und Agent. Schreibt bei NEU über die Schwelle
    gekommenen Tasks EIN Ereignis `liegt` und gibt es zurück (sonst None)."""
    einst = einstellung(settings)
    jetzt = jetzt or datetime.now().astimezone()
    if (not einst.get("an") or agent in (einst.get("ohne") or [])
            or not in_meldezeit(einst, jetzt) or pausiert(einst, agent, jetzt)):
        return None
    if not (Path(root) / agent / "inbox").is_dir():
        return None
    stand = liegende(root, agent, einst, jetzt.timestamp())
    if automatik_an:
        stand["wartend"] = []   # die Automatik holt sie ab — das ist kein Liegenbleiben
    # Schlüssel je ART: ein als „wartend" gemeldeter Task, der danach
    # beansprucht wird und verstummt, ist eine neue Meldung wert.
    alle = ([{**t, "schluessel": t["id"]} for t in stand["wartend"]]
            + [{**t, "schluessel": "still:" + t["id"]} for t in stand["still"]])
    box = Mailbox(root, agent)
    with box._lock():
        z = _zustand(box)
        try:
            spaeter = float(z.get("spaeter_bis") or 0)
        except (TypeError, ValueError):
            spaeter = 0.0
        if jetzt.timestamp() < spaeter:
            return None
        ids = {t["schluessel"] for t in alle}
        neu = [t for t in alle if t["schluessel"] not in z["gemeldet"]]
        vorher = set(z["gemeldet"])
        z["gemeldet"] = {i: v for i, v in z["gemeldet"].items() if i in ids}
        for t in neu:
            z["gemeldet"][t["schluessel"]] = jetzt.timestamp()
        if neu or set(z["gemeldet"]) != vorher:
            try:
                atomic_write_json(box.base / ".liegt.json", z)
            except OSError:
                pass
    if not neu:
        return None
    teile = []
    if stand["wartend"]:
        absender = sorted({str(t["sender"]) for t in stand["wartend"] if t.get("sender")})
        teile.append(f"{len(stand['wartend'])} Task(s) warten seit "
                     f"{max(t['minuten'] for t in stand['wartend'])} min"
                     + (f" (von {', '.join(absender)})" if absender else ""))
    if stand["still"]:
        teile.append(f"{len(stand['still'])} beanspruchte(r) Task(s) ohne Lebenszeichen seit "
                     f"{max(t['minuten'] for t in stand['still'])} min")
    return ereignisse.schreibe(
        root, agent, "liegt", " · ".join(teile), schwere="warnung",
        wartend=len(stand["wartend"]), still=len(stand["still"]),
        tasks=[t["id"] for t in alle][:20],
        laengste=max(t["minuten"] for t in alle))


def spaeter(root: str | os.PathLike, agent: str, minuten: int = SPAETER_MIN) -> float:
    """„Später": die Meldung für diesen Agenten zurückstellen und danach
    erneut bringen (gemeldete Tasks gelten dann wieder als neu)."""
    box = Mailbox(root, agent)
    bis = time.time() + minuten * 60
    with box._lock():
        atomic_write_json(box.base / ".liegt.json", {"gemeldet": {}, "spaeter_bis": bis})
    return bis


def einmal_fertig(root: str | os.PathLike, agent: str) -> bool:
    """Einmal-Lauf der Automatik: ist nichts mehr zu tun? (keine fälligen
    Tasks in der Inbox, nichts in Arbeit außer Geparktem)"""
    basis = Path(root) / agent / "inbox"
    for _p, env in _lese_ordner(basis):
        if env.get("kind", "task") == "task" and not zu_frueh(env):
            return False
    for _p, env in _lese_ordner(basis / ".processing"):
        if env.get("kind", "task") == "task" and env.get("status") != "needs_confirm":
            return False
    return True
