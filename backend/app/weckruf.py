"""Automatik-Weckruf (Issue #36): Nachrichten wecken einen Automatik-Agenten.

Der Automatik-Watcher führt nur `kind: task` aus. Agenten reden aber
überwiegend per send_message/ask miteinander — eine Nachforderung („Punkt d
fehlt noch") oder das Ergebnis eines delegierten Tasks blieb in der Automatik
liegen, bis zufällig der nächste Task kam. Im Handbetrieb („schau in die
Inbox") gab es das Problem nicht.

Lösung ohne Sonderweg im Watcher: der SERVER bündelt ungelesene Einträge je
Absender zu einem normalen Task (`weckruf: true`, Absender = der ursprüngliche
Absender). Damit greift alles, was Tasks schon können — Sitzung fortsetzen,
Timeout, Verbrauchszähler, Panel-Anzeige, `.failed`, Rückfragen-Parken — und
das Abschluss-Ergebnis geht von selbst als `response` an den Absender zurück.

Je Agent einstellbar in agents.yaml: `automatik_weckt: [task, message,
response, question]`. Default ist wie bisher nur `task` (= kein Weckruf).

Schleifenschutz, zweistufig:
  * Das Ergebnis eines Weckruf-Tasks trägt `weckt: false` (mailbox.py) — zwei
    Automatik-Agenten spielen sich keine Abschlussmeldungen hin und her.
  * Höchstens WECK_MAX_JE_STUNDE Weckrufe je Absender und Stunde; darüber
    bleibt die Post liegen und der Mensch bekommt EINE Meldung (Panel + Push
    über die Orchestrator-Inbox).

Aufgerufen wird `pruefe` vom Automatik-Manager (auto_watcher.py) im
Reconcile-Takt — nur für Agenten, deren Automatik eingeschaltet ist.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from app.mailbox import ORCHESTRATOR, Mailbox, Task, _lese_ordner, atomic_write_json, new_id

# `answer` fehlt bewusst: die Antwort auf die Rückfrage eines TASKS stößt den
# geparkten Task ohnehin neu an (Issue #17) — ein zweiter Lauf wäre doppelt.
WECK_ARTEN = ("message", "response", "question")
WECK_MAX_JE_STUNDE = int(os.environ.get("WECK_MAX_JE_STUNDE", "6"))
GESEHEN_TAGE = 14.0
TEXT_DECKEL = 6000   # Zeichen je Eintrag im gebündelten Auftrag
BUENDEL_MAX = 20     # Einträge je Weckruf — der Rest kommt mit dem nächsten

_BEZEICHNUNG = {"message": "Nachricht", "response": "Ergebnis eines delegierten Tasks",
                "question": "Rückfrage an dich"}


def weck_arten(agent_cfg: dict[str, Any] | None) -> list[str]:
    """`automatik_weckt` eines Agenten → gültige Nicht-Task-Arten (sortiert)."""
    roh = (agent_cfg or {}).get("automatik_weckt")
    if roh is None:
        roh = ((agent_cfg or {}).get("connection") or {}).get("automatik_weckt")
    if isinstance(roh, str):
        roh = [teil.strip() for teil in roh.split(",")]
    if not isinstance(roh, (list, tuple)):
        return []
    return sorted({str(a).strip().lower() for a in roh} & set(WECK_ARTEN))


def _zustand_pfad(box: Mailbox) -> Path:
    return box.base / ".weckruf.json"


def _lade_zustand(box: Mailbox) -> dict[str, Any]:
    try:
        z = json.loads(_zustand_pfad(box).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        z = {}
    if not isinstance(z, dict):
        z = {}
    for feld in ("gesehen", "laeufe", "gemeldet"):
        if not isinstance(z.get(feld), dict):
            z[feld] = {}
    return z


def _auftrag(agent: str, sender: str, eintraege: list[dict[str, Any]], rest: int) -> str:
    teile = [
        f"[Automatik-Weckruf] In deiner Inbox liegen {len(eintraege)} ungelesene "
        f"Einträge von '{sender}'. Bearbeite sie der Reihe nach:"
    ]
    for nr, env in enumerate(eintraege, 1):
        kind = env.get("kind", "message")
        kopf = f"{nr}. {_BEZEICHNUNG.get(kind, kind)} (id {env.get('id')}"
        if env.get("created_at"):
            kopf += f", {env['created_at']}"
        if env.get("reply_to"):
            kopf += f", zu {env['reply_to']}"
        if kind == "response" and env.get("status"):
            kopf += f", Status {env['status']}"
        text = str(env.get("text") or env.get("result") or "")
        if len(text) > TEXT_DECKEL:
            text = text[:TEXT_DECKEL] + f"\n…[gekürzt — voller Text per inbox('{agent}')]"
        teile.append(kopf + "):\n" + text)
    if rest > 0:
        teile.append(f"(Weitere {rest} Einträge dieses Absenders folgen mit dem nächsten Weckruf.)")
    teile.append(
        "Regeln für diesen Lauf: Dein Abschluss-Ergebnis geht automatisch als Antwort an "
        f"'{sender}' — schicke dasselbe nicht zusätzlich per send_message. Eine Rückfrage "
        "(kind=question) beantwortest du mit answer(<id>, …). Archiviere jeden verarbeiteten "
        f"Eintrag mit mark_read('{agent}', <id>), sonst liegt er weiter in deiner Inbox. "
        "Brauchst du selbst eine Entscheidung, frage per ask statt zu raten."
    )
    return "\n\n".join(teile)


def pruefe(root: str | os.PathLike, agent: str, arten: list[str],
           jetzt: float | None = None, max_je_stunde: int | None = None) -> dict[str, Any]:
    """Ungelesene Einträge der gewünschten Arten zu Weckruf-Tasks bündeln.

    Gibt {"geweckt": [task_id…], "gebremst": [absender…]} zurück. Jeder Eintrag
    weckt höchstens EINMAL (Zustand in <mailbox>/.weckruf.json) — auch wenn der
    Lauf ihn nicht archiviert, entsteht kein zweiter Lauf daraus."""
    bericht: dict[str, Any] = {"geweckt": [], "gebremst": []}
    arten = [a for a in arten if a in WECK_ARTEN]
    if not arten:
        return bericht
    if not (Path(root) / agent / "inbox").is_dir():
        return bericht  # keine Mailbox = keine Post; Mailbox() legte sie sonst an
    jetzt = time.time() if jetzt is None else jetzt
    grenze = WECK_MAX_JE_STUNDE if max_je_stunde is None else max_je_stunde
    box = Mailbox(root, agent)
    meldungen: list[str] = []
    with box._lock():
        z = _lade_zustand(box)
        je_absender: dict[str, list[dict[str, Any]]] = {}
        for _pfad, env in _lese_ordner(box.inbox):
            env_id = env.get("id")
            if (not env_id or env.get("kind") not in arten or env.get("weckt") is False
                    or env_id in z["gesehen"]):
                continue
            if env.get("kind") == "question" and env.get("status") != "needs_confirm":
                continue  # schon beantwortet/geschlossen
            absender = str(env.get("sender") or env.get("from") or ORCHESTRATOR)
            if absender == agent:
                continue  # Selbstgespräch weckt nicht
            je_absender.setdefault(absender, []).append(env)
        for absender, eintraege in je_absender.items():
            eintraege.sort(key=lambda e: str(e.get("created_at") or ""))
            laeufe = [ts for ts in z["laeufe"].get(absender, [])
                      if isinstance(ts, (int, float)) and jetzt - ts < 3600]
            if len(laeufe) >= grenze:
                bericht["gebremst"].append(absender)
                if jetzt - float(z["gemeldet"].get(absender) or 0) >= 3600:
                    z["gemeldet"][absender] = jetzt
                    meldungen.append(
                        f"[Schleifenschutz] {agent} wurde in der letzten Stunde schon "
                        f"{len(laeufe)}× durch Post von {absender} geweckt. "
                        f"{len(eintraege)} weitere Einträge bleiben liegen, bis die "
                        f"Stunde um ist — bitte nachsehen, ob sich die beiden im Kreis drehen.")
                z["laeufe"][absender] = laeufe
                continue
            buendel, rest = eintraege[:BUENDEL_MAX], max(0, len(eintraege) - BUENDEL_MAX)
            task = Task(
                task_id=new_id("task"), agent=agent, sender=absender,
                instruction=_auftrag(agent, absender, buendel, rest),
                weckruf=True, weck_ids=[str(e["id"]) for e in buendel],
            )
            box.put_task(task)
            for env in buendel:
                z["gesehen"][str(env["id"])] = jetzt
            z["laeufe"][absender] = laeufe + [jetzt]
            bericht["geweckt"].append(task.task_id)
        # Aufräumen + nur schreiben, wenn sich etwas getan hat
        alt_grenze = jetzt - GESEHEN_TAGE * 86400
        z["gesehen"] = {k: v for k, v in z["gesehen"].items()
                        if isinstance(v, (int, float)) and v >= alt_grenze}
        if bericht["geweckt"] or bericht["gebremst"]:
            try:
                atomic_write_json(_zustand_pfad(box), z)
            except OSError:
                pass
    # Außerhalb des Locks: die Orchestrator-Inbox ist eine ANDERE Mailbox.
    for text in meldungen:
        try:
            Mailbox(root, ORCHESTRATOR).post(
                {"kind": "message", "sender": agent, "weckt": False, "text": text})
        except (ValueError, OSError):
            pass
    return bericht


def ungeweckt(root: str | os.PathLike, agent: str, arten: list[str]) -> int:
    """Wie viele ungelesene Nicht-Task-Einträge lösen bei diesem Agenten
    KEINEN Lauf aus? Fürs Panel: „3 ungelesene Nachrichten, Automatik
    reagiert darauf nicht" (Issue #36)."""
    zahl = 0
    inbox = Path(root) / agent / "inbox"
    if not inbox.is_dir():
        return 0
    for _pfad, env in _lese_ordner(inbox):
        kind = env.get("kind", "task")
        if kind in WECK_ARTEN and kind not in arten and env.get("weckt") is not False:
            zahl += 1
    return zahl
