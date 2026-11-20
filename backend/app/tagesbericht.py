"""Tagesbericht je Agent (Issue #63).

Einmal am Tag eine feste Auswertung — kein LLM: alles steht schon in
ereignisse.jsonl, Inbox/Outbox/.failed, dem Verbrauchszähler und den
gemeldeten Paketen (#61). Als Seite im Dashboard (Dialog „Tagesbericht") und,
wenn gewünscht, als Push mit einer Kurzzeile je Agent:

    deverp: 9 erledigt · 50,20 $ · 1 Rückfrage offen · 2 Tasks liegen

Abschaltbar (Pflicht laut Issue), Default AUS:
  settings.json `tagesbericht`: {an, push, zeit "17:30", tage [0..4],
  woechentlich (nur am letzten gewählten Wochentag), nur_wenn_etwas, ohne
  [Agenten], pause_bis (ISO — Urlaub; auch aus dem Push heraus setzbar)}.
Die Seite gibt es immer, auch ohne dass ein Push kommt.

Nur Standardlib.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app import ereignisse, freigaben, liegezeit, pakete
from app.mailbox import ORCHESTRATOR, _lese_ordner

STANDARD: dict[str, Any] = {
    "an": False, "push": True, "zeit": "17:30", "tage": [0, 1, 2, 3, 4],
    "woechentlich": False, "nur_wenn_etwas": True, "ohne": [], "pause_bis": None,
}
ZUSTAND = "tagesbericht.json"
AUFFAELLIG = ("verweigert", "timeout", "fehlserie", "watcher_abriss", "rueckreihung",
              "aufgegeben", "fehlstart", "kosten", "ungeweckt")


def einstellung(settings: dict[str, Any] | None) -> dict[str, Any]:
    roh = (settings or {}).get("tagesbericht")
    aus = dict(STANDARD)
    if isinstance(roh, dict):
        aus.update({k: v for k, v in roh.items() if k in STANDARD})
    return aus


def _am(wert: Any, tag: date) -> bool:
    try:
        return datetime.fromisoformat(str(wert)).astimezone().date() == tag
    except (TypeError, ValueError):
        return False


def erstelle(root: str | os.PathLike, agent: str, tag: date | None = None,
             settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Der Bericht EINES Agenten für einen Kalendertag (Serverzeit)."""
    tag = tag or datetime.now().astimezone().date()
    basis = Path(root) / agent
    erledigt: list[dict[str, Any]] = []
    kosten = tokens = laufzeit = 0.0
    groesster: dict[str, Any] | None = None
    for _p, r in _lese_ordner(basis / "outbox"):
        if not _am(r.get("responded_at"), tag):
            continue
        v = r.get("vorgang") if isinstance(r.get("vorgang"), dict) else {}
        verbrauch = r.get("verbrauch") if isinstance(r.get("verbrauch"), dict) else {}
        lauf = r.get("lauf") if isinstance(r.get("lauf"), dict) else {}
        k = verbrauch.get("total_cost_usd")
        k = float(k) if isinstance(k, (int, float)) and k == k else 0.0
        kosten += k
        tokens += sum(float(verbrauch.get(f) or 0) for f in (
            "input_tokens", "output_tokens", "cache_creation_input_tokens",
            "cache_read_input_tokens") if isinstance(verbrauch.get(f), (int, float)))
        try:
            dauer = (datetime.fromisoformat(str(r["responded_at"])).astimezone()
                     - datetime.fromisoformat(str(v["claimed_at"])).astimezone()).total_seconds()
        except (KeyError, ValueError, TypeError):
            dauer = 0.0
        if verbrauch:   # nur echte Läufe zählen als Laufzeit, nicht die Liegezeit im Handbetrieb
            laufzeit += max(0.0, dauer)
        eintrag = {
            "task_id": r.get("task_id"),
            "status": ("error" if r.get("status") == "error"
                       else "eingeschraenkt" if lauf.get("verweigert") or lauf.get("timeout")
                       else "done"),
            "titel": (str(v.get("titel") or r.get("instruction") or r.get("result") or "")
                      .strip().splitlines() or [""])[0][:140],
            "kosten": round(k, 2) if k else None, "von": r.get("to") or v.get("sender"),
            "vorgang": v.get("vorgang"),
        }
        erledigt.append(eintrag)
        if k and (groesster is None or k > groesster["kosten"]):
            groesster = {"task_id": r.get("task_id"), "kosten": round(k, 2),
                         "titel": eintrag["titel"]}
    # Offenes: Rückfragen DIESES Agenten an den Menschen und an ihn gestellte
    rueckfragen = []
    for ordner, richtung in ((Path(root) / ORCHESTRATOR / "inbox", "an dich"),
                             (basis / "inbox", "an " + agent)):
        for _p, env in _lese_ordner(ordner):
            if env.get("kind") != "question" or env.get("status") != "needs_confirm":
                continue
            if richtung == "an dich" and env.get("sender") != agent:
                continue
            rueckfragen.append({"id": env.get("id"), "richtung": richtung,
                                "von": env.get("sender"), "text": str(env.get("text") or "")[:200]})
    offene_freigaben = [a for a in freigaben.offene(root) if a.get("agent") == agent]
    stand = liegezeit.liegende(root, agent, liegezeit.einstellung(settings))
    fehlgeschlagen = [
        {"task_id": env.get("task_id") or p.stem,
         "titel": (str(env.get("instruction") or "").strip().splitlines() or [""])[0][:140]}
        for p, env in _lese_ordner(basis / "inbox" / ".failed")
        if datetime.fromtimestamp(p.stat().st_mtime).astimezone().date() == tag]
    auffaellig: dict[str, int] = {}
    beispiele: list[str] = []
    try:
        for e in ereignisse.lies(root, agent, limit=5000)["eintraege"]:
            if _am(e.get("zeit"), tag) and e.get("art") in AUFFAELLIG:
                auffaellig[e["art"]] = auffaellig.get(e["art"], 0) + 1
                if len(beispiele) < 5 and e.get("schwere") in ("warnung", "fehler"):
                    beispiele.append(str(e.get("text") or "")[:160])
    except ValueError:
        pass
    offene_pakete = [p for p in pakete.liste(nur_offene=True)
                     if agent in (p.get("ausgestellt_von"),
                                  *[v.get("von") for v in p.get("verlauf") or []])]
    bericht = {
        "agent": agent, "tag": tag.isoformat(),
        "erledigt": sorted(erledigt, key=lambda e: str(e.get("task_id"))),
        "zahl": {"done": sum(1 for e in erledigt if e["status"] == "done"),
                 "error": sum(1 for e in erledigt if e["status"] == "error"),
                 "eingeschraenkt": sum(1 for e in erledigt if e["status"] == "eingeschraenkt")},
        "kosten": round(kosten, 2), "tokens": int(tokens), "laufzeit": int(laufzeit),
        "groesster_lauf": groesster,
        "rueckfragen": rueckfragen, "freigaben": len(offene_freigaben),
        "liegend": stand["wartend"], "still": stand["still"],
        "fehlgeschlagen": fehlgeschlagen,
        "pakete": [{"token": p["token"], "kurz": pakete.kurztext(p), "phase": p["phase"],
                    "flags": p.get("flags") or []} for p in offene_pakete],
        "auffaellig": auffaellig, "beispiele": beispiele,
    }
    bericht["etwas_passiert"] = bool(
        erledigt or rueckfragen or offene_freigaben or stand["wartend"] or stand["still"]
        or fehlgeschlagen or auffaellig or offene_pakete)
    bericht["kurz"] = kurztext(bericht)
    return bericht


def kurztext(b: dict[str, Any]) -> str:
    """Eine Zeile für den Push: „deverp: 9 erledigt · 50,20 $ · 1 Rückfrage offen"."""
    teile = []
    n = len(b["erledigt"])
    if n:
        teile.append(f"{n} erledigt" + (f" ({b['zahl']['error']} Fehler)" if b["zahl"]["error"] else ""))
    if b["kosten"]:
        teile.append(f"{b['kosten']:.2f} $".replace(".", ","))
    if b["rueckfragen"]:
        m = len(b["rueckfragen"])
        teile.append(f"{m} Rückfrage{'n' if m != 1 else ''} offen")
    if b["freigaben"]:
        teile.append(f"{b['freigaben']} Freigabe{'n' if b['freigaben'] != 1 else ''} offen")
    if b["liegend"] or b["still"]:
        m = len(b["liegend"]) + len(b["still"])
        teile.append(f"{m} Task{'s' if m != 1 else ''} lieg{'en' if m != 1 else 't'}")
    for p in b["pakete"][:2]:
        teile.append("Deploy " + p["token"] + (f" ({' + '.join(p['flags'])})" if p["flags"] else ""))
    stoerung = sum(b["auffaellig"].get(a, 0) for a in ("fehlserie", "watcher_abriss", "aufgegeben"))
    if stoerung:
        teile.append(f"{stoerung} Störung{'en' if stoerung != 1 else ''}")
    return f"{b['agent']}: " + (" · ".join(teile) or "nichts passiert")


def alle(root: str | os.PathLike, tag: date | None = None,
         settings: dict[str, Any] | None = None, mit_ruhigen: bool = True) -> list[dict[str, Any]]:
    """Berichte aller Agenten mit Mailbox (ohne den Orchestrator = Mensch)."""
    aus = []
    try:
        namen = sorted(d.name for d in Path(root).iterdir()
                       if d.is_dir() and (d / "inbox").is_dir() and d.name != ORCHESTRATOR)
    except OSError:
        return aus
    for name in namen:
        try:
            b = erstelle(root, name, tag, settings)
        except (OSError, ValueError):
            continue
        if mit_ruhigen or b["etwas_passiert"]:
            aus.append(b)
    return aus


# --- Fälligkeit des Push ------------------------------------------------------

def _zustand_pfad(root: str | os.PathLike) -> Path:
    return Path(root).parent / ZUSTAND


def faellig(settings: dict[str, Any] | None, root: str | os.PathLike,
            jetzt: datetime | None = None) -> bool:
    """Soll JETZT der Tages-Push raus? (eingeschaltet, Push gewünscht, nicht
    pausiert, richtiger Wochentag, Uhrzeit erreicht, heute noch nicht gesendet)"""
    einst = einstellung(settings)
    jetzt = jetzt or datetime.now().astimezone()
    if not einst["an"] or not einst["push"]:
        return False
    try:
        if einst["pause_bis"] and datetime.fromisoformat(
                str(einst["pause_bis"])).astimezone() > jetzt:
            return False
    except ValueError:
        pass
    tage = [t for t in (einst.get("tage") or []) if isinstance(t, int)] or list(range(7))
    if einst["woechentlich"]:
        tage = [max(tage)]
    if jetzt.weekday() not in tage:
        return False
    try:
        h, m = str(einst["zeit"]).split(":")
        if (jetzt.hour, jetzt.minute) < (int(h), int(m)):
            return False
    except ValueError:
        return False
    try:
        gesendet = json.loads(_zustand_pfad(root).read_text(encoding="utf-8")).get("gesendet")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        gesendet = None
    return gesendet != jetzt.date().isoformat()


def merke_gesendet(root: str | os.PathLike, jetzt: datetime | None = None) -> None:
    jetzt = jetzt or datetime.now().astimezone()
    pfad = _zustand_pfad(root)
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps({"gesendet": jetzt.date().isoformat()}), encoding="utf-8")
    os.replace(tmp, pfad)


def push_text(root: str | os.PathLike, settings: dict[str, Any] | None,
              jetzt: datetime | None = None) -> str | None:
    """Text des Tages-Push — None, wenn (bei `nur_wenn_etwas`) nichts war."""
    einst = einstellung(settings)
    tag = (jetzt or datetime.now().astimezone()).date()
    berichte = [b for b in alle(root, tag, settings, mit_ruhigen=False)
                if b["agent"] not in (einst.get("ohne") or [])]
    if not berichte:
        return None if einst["nur_wenn_etwas"] else "Heute ist nichts passiert."
    return "\n".join(b["kurz"] for b in berichte)[:900]


def pausiere(settings: dict[str, Any] | None, tage: int) -> dict[str, Any]:
    """Neue `tagesbericht`-Einstellung mit Pause bis in `tage` Tagen
    (tage <= 0 = abbestellen: `an` aus)."""
    einst = einstellung(settings)
    if tage <= 0:
        einst["an"] = False
    else:
        einst["pause_bis"] = (datetime.now().astimezone() + timedelta(days=tage)
                              ).isoformat(timespec="minutes")
    return einst
