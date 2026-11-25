"""Zustellung der Automatik in die Terminal-Sitzung (Issue #67).

Je Agent in settings.json `zustellung: {agent: {sitzung, push}}` — im
Dashboard umschaltbar (Dialog „Freigaben" im Agenten-Panel), bewusst nicht in
agents.yaml. `sitzung` = der Watcher tippt Tasks in die laufende Claude-Code-
Sitzung des Projekt-Terminals (tmux auf dem Agenten-PC) und läuft nur als
Rückfall headless; `push` = Push „Task in Sitzung gestartet". Beides wirkt
beim nächsten Start der Automatik (wie die Freigaben).

Der Watcher meldet sich über Zeilen `@@zustellung {json}` auf stdout; der
Automatik-Manager macht daraus Ereignisse der Art `zustellung` (`verarbeite_zeile`).

Nur Standardlib.
"""
from __future__ import annotations

import json
from typing import Any

from app import config, ereignisse
from app.mailbox import AGENT_NAME_RE

PRAEFIX = "@@zustellung "
ZUSTAENDE = {"wartet", "zugestellt", "unbestaetigt", "erinnert", "abgeschlossen",
             "offen", "rueckfall", "zurueck", "hooks"}


class ZustellFehler(ValueError):
    pass


def einstellung(agent: str, settings: dict[str, Any] | None = None) -> dict[str, bool]:
    roh = ((settings or config.load_settings()).get("zustellung") or {}).get(agent) or {}
    return {"sitzung": bool(roh.get("sitzung")), "push": bool(roh.get("push"))}


def setze_einstellung(agent: str, sitzung: bool | None = None,
                      push: bool | None = None) -> dict[str, bool]:
    if not AGENT_NAME_RE.fullmatch(agent or ""):
        raise ZustellFehler(f"ungültiger Agentenname: {agent!r}")
    alle = dict(config.load_settings().get("zustellung") or {})
    neu = einstellung(agent, {"zustellung": alle})
    if sitzung is not None:
        neu["sitzung"] = bool(sitzung)
    if push is not None:
        neu["push"] = bool(push)
    alle[agent] = neu
    config.save_settings({"zustellung": alle})
    return neu


def flags(agent: str, settings: dict[str, Any] | None = None) -> str:
    """Zusatz für die Watcher-Kommandozeile (feste Wörter, nichts Freies)."""
    e = einstellung(agent, settings)
    if not e["sitzung"]:
        return ""
    return " --zustellung sitzung" + (" --zustellung-push" if e["push"] else "")


def verarbeite_zeile(root: Any, agent: str, zeile: str) -> str | None:
    """Watcher-Zeile `@@zustellung {…}` → Ereignis. Gibt None zurück, wenn
    die Zeile keine Zustell-Meldung ist; sonst den Klartext (fürs Log)."""
    if not zeile.startswith(PRAEFIX):
        return None
    try:
        d = json.loads(zeile[len(PRAEFIX):])
    except json.JSONDecodeError:
        return ""
    if not isinstance(d, dict) or d.get("zustand") not in ZUSTAENDE:
        return ""
    schwere = d.get("schwere") if d.get("schwere") in ereignisse.SCHWEREN else "info"
    text = str(d.get("text") or d["zustand"])[:300]
    details = {k: v for k, v in d.items()
               if k in ("zustand", "sitzung", "projekt_pfad", "push")
               and isinstance(v, (str, bool, int))}
    task_id = d.get("task_id") if isinstance(d.get("task_id"), str) else None
    try:
        ereignisse.schreibe(root, agent, "zustellung", text, schwere=schwere,
                            task_id=task_id, **details)
    except ValueError:
        pass
    return text
