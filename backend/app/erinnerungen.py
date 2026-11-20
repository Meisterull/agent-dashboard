"""Erinnerungs-Postfach je Agent (Issue #52).

Ein Agent hinterlegt sich selbst eine Notiz mit Fälligkeit („am 26.11. prüfen,
ob der Backup-Job durchlief"); zum Termin landet sie in SEINER Inbox — als
Task (die Automatik arbeitet sie ab) oder als Nachricht. Die Zeitpläne
(app/zeitplaene.py) decken das nicht: die pflegt nur der Mensch, sie
wiederholen sich, und einen einmaligen Termin kennen sie nicht.

  * Ablage `mailboxes/<agent>/erinnerungen.json`, geschrieben unter dem
    Mailbox-Lock des Agenten.
  * `when` = ISO-Datum oder Datum mit Uhrzeit in SERVERZEIT (TZ steht in
    docker-compose); ohne Uhrzeit gilt 08:00.
  * Verpasste Termine (Server war aus) werden NACHGEHOLT — eine vergessene
    Erinnerung ist schlimmer als eine verspätete.
  * `als`: "task" | "message"; ohne Angabe entscheidet sich das bei der
    Zustellung: Task, wenn die Automatik des Agenten an ist, sonst Nachricht.
  * Zugestellt wird mit Absender `orchestrator`: das Ergebnis eines
    Erinnerungs-Tasks erreicht so den Menschen (Panel + Push).

Nur Standardlib.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, time as uhrzeit
from pathlib import Path
from typing import Any, Callable

from app import ereignisse
from app.mailbox import ORCHESTRATOR, Mailbox, Task, atomic_write_json, new_id

DATEI = "erinnerungen.json"
STANDARD_UHRZEIT = uhrzeit(8, 0)
MAX_OFFEN = 200
MAX_TEXT = 4000
ERLEDIGT_TAGE = 30.0
_ID_RE = re.compile(r"^erin-[0-9a-f]{10}$")


class ErinnerungFehler(Exception):
    """Fachlicher Fehler mit Klartext."""


def parse_wann(when: Any) -> datetime:
    """ISO-Datum/-Zeitpunkt → tz-behafteter Zeitpunkt in Serverzeit."""
    roh = str(when or "").strip()
    if not roh:
        raise ErinnerungFehler("when fehlt — z. B. 2026-11-26 oder 2026-11-26T09:00")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", roh):
            ziel = datetime.combine(datetime.fromisoformat(roh).date(), STANDARD_UHRZEIT)
        else:
            ziel = datetime.fromisoformat(roh.replace("Z", "+00:00"))
    except ValueError:
        raise ErinnerungFehler(
            f"when muss ein ISO-Datum sein (2026-11-26 oder 2026-11-26T09:00), "
            f"nicht {when!r}") from None
    return ziel.astimezone()


def _lade(box: Mailbox) -> list[dict[str, Any]]:
    try:
        daten = json.loads((box.base / DATEI).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    return [e for e in daten if isinstance(e, dict) and e.get("id")] if isinstance(daten, list) else []


def _speichere(box: Mailbox, eintraege: list[dict[str, Any]]) -> None:
    atomic_write_json(box.base / DATEI, eintraege)  # type: ignore[arg-type]


def lege_an(root: str | os.PathLike, agent: str, when: Any, text: str,
            project: str | None = None, als: str | None = None,
            von: str | None = None, thread: str | None = None) -> dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        raise ErinnerungFehler("text fehlt — woran soll erinnert werden?")
    if als not in (None, "", "task", "message"):
        raise ErinnerungFehler('als muss "task" oder "message" sein')
    ziel = parse_wann(when)
    box = Mailbox(root, agent)
    eintrag = {
        "id": "erin-" + secrets.token_hex(5), "agent": agent,
        "faellig": ziel.isoformat(timespec="minutes"), "text": text[:MAX_TEXT],
        "project": (str(project).strip() or None) if project else None,
        "thread": thread or None, "als": als or None, "von": von or agent,
        "angelegt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "offen",
    }
    with box._lock():
        eintraege = _lade(box)
        if sum(1 for e in eintraege if e.get("status") == "offen") >= MAX_OFFEN:
            raise ErinnerungFehler(f"schon {MAX_OFFEN} offene Erinnerungen — erst aufräumen")
        eintraege.append(eintrag)
        _speichere(box, eintraege)
    ereignisse.schreibe(root, agent, "erinnerung",
                        f"Erinnerung angelegt für {eintrag['faellig'][:16].replace('T', ' ')}: "
                        f"{text[:120]}", erinnerung_id=eintrag["id"], zustand="angelegt")
    return eintrag


def liste(root: str | os.PathLike, agent: str,
          mit_erledigten: bool = False) -> list[dict[str, Any]]:
    if not (Path(root) / agent).is_dir():
        return []
    eintraege = _lade(Mailbox(root, agent))
    if not mit_erledigten:
        eintraege = [e for e in eintraege if e.get("status") == "offen"]
    return sorted(eintraege, key=lambda e: str(e.get("faellig") or ""))


def _aendere(root, agent: str, eid: str, fn: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    if not _ID_RE.fullmatch(eid or ""):
        raise ErinnerungFehler(f"ungültige Erinnerungs-ID: {eid!r}")
    box = Mailbox(root, agent)
    with box._lock():
        eintraege = _lade(box)
        treffer = next((e for e in eintraege if e["id"] == eid), None)
        if treffer is None or treffer.get("status") != "offen":
            raise ErinnerungFehler(f"keine offene Erinnerung {eid} bei {agent}")
        fn(treffer)
        _speichere(box, eintraege)
    return treffer


def loesche(root: str | os.PathLike, agent: str, eid: str) -> dict[str, Any]:
    e = _aendere(root, agent, eid, lambda e: e.update(
        status="geloescht", erledigt=datetime.now().astimezone().isoformat(timespec="seconds")))
    ereignisse.schreibe(root, agent, "erinnerung", f"Erinnerung gelöscht: {e['text'][:120]}",
                        erinnerung_id=eid, zustand="geloescht")
    return e


def verschiebe(root: str | os.PathLike, agent: str, eid: str, when: Any) -> dict[str, Any]:
    ziel = parse_wann(when).isoformat(timespec="minutes")
    return _aendere(root, agent, eid, lambda e: e.update(faellig=ziel))


def _auftrag(e: dict[str, Any]) -> str:
    return (f"[Erinnerung {e['id']}, angelegt am {str(e.get('angelegt'))[:16].replace('T', ' ')} "
            f"von {e.get('von') or e['agent']}, fällig {e['faellig'][:16].replace('T', ' ')}]\n\n"
            f"{e['text']}")


def stelle_faellige_zu(root: str | os.PathLike, automatik_an: Callable[[str], bool],
                       jetzt: datetime | None = None) -> list[dict[str, Any]]:
    """Alle fälligen Erinnerungen in die Inbox ihres Agenten legen (auch
    verspätete). → die zugestellten Einträge."""
    jetzt = jetzt or datetime.now().astimezone()
    zugestellt: list[dict[str, Any]] = []
    try:
        ordner = [d for d in Path(root).iterdir() if d.is_dir() and (d / DATEI).is_file()]
    except OSError:
        return zugestellt
    for d in ordner:
        try:
            box = Mailbox(root, d.name)
        except ValueError:
            continue
        with box._lock():
            eintraege = _lade(box)
            geaendert = False
            for e in eintraege:
                if e.get("status") != "offen":
                    continue
                try:
                    if datetime.fromisoformat(str(e["faellig"])).astimezone() > jetzt:
                        continue
                except (ValueError, KeyError):
                    continue
                art = e.get("als") or ("task" if automatik_an(d.name) else "message")
                if art == "task":
                    task = Task(task_id=new_id("task"), agent=d.name, sender=ORCHESTRATOR,
                                instruction=_auftrag(e), project=e.get("project"),
                                thread=e.get("thread"))
                    box.put_task(task)
                    e["zugestellt_als"] = task.task_id
                else:
                    env = box.post({"kind": "message", "sender": ORCHESTRATOR, "to": d.name,
                                    "text": "⏰ " + _auftrag(e), "erinnerung": e["id"]})
                    e["zugestellt_als"] = env.get("id")
                e.update(status="zugestellt", art=art,
                         erledigt=jetzt.isoformat(timespec="seconds"))
                geaendert = True
                zugestellt.append(dict(e))
            # Erledigtes nach einer Weile vergessen
            grenze = jetzt.timestamp() - ERLEDIGT_TAGE * 86400
            behalten = []
            for e in eintraege:
                try:
                    alt = (e.get("status") != "offen" and e.get("erledigt")
                           and datetime.fromisoformat(str(e["erledigt"])).timestamp() < grenze)
                except ValueError:
                    alt = False
                if alt:
                    geaendert = True
                else:
                    behalten.append(e)
            if geaendert:
                _speichere(box, behalten)
    for e in zugestellt:
        ereignisse.schreibe(root, e["agent"], "erinnerung",
                            f"Erinnerung zugestellt als {e['art']}: {e['text'][:120]}",
                            task_id=e["zugestellt_als"] if e["art"] == "task" else None,
                            erinnerung_id=e["id"], zustand="zugestellt")
    return zugestellt
