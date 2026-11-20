"""Freigaben der Automatik über das Dashboard (Issue #56).

Im headless-Lauf (`claude --print`) konnte niemand eine Berechtigungs-Rückfrage
beantworten — der Aufruf wurde still abgelehnt, der Agent arbeitete drumherum.
Mit `--permission-prompt-tool mcp__dashboard__freigabe` ruft Claude Code
stattdessen das MCP-Tool `freigabe` auf (Argumente `tool_name`, `input`,
`tool_use_id` — am 20.11.2026 mit Claude Code 2.1.326 nachgemessen) und
erwartet als Text ein JSON `{"behavior": "allow", "updatedInput": {...}}`
bzw. `{"behavior": "deny", "message": "..."}`.

Ablauf in `pruefe` (läuft im MCP-Prozess, in einem eigenen Threadpool):
  1. „Nie erlauben"-Liste des Agenten → sofort ablehnen, OHNE Anfrage (niemand
     soll am Handy versehentlich „Erlauben" tippen).
  2. „Immer erlauben"-Liste → sofort erlauben.
  3. Sonst eine Anfrage anlegen (`<mailbox>/freigaben/<id>.json`), im
     Ereignis-Log vermerken (→ Banner im Dashboard + Push mit Knöpfen) und auf
     die Entscheidung WARTEN — bis `timeout` (Default 15 min) oder Not-Aus,
     beides zählt als Ablehnung.

Einstellungen je Agent liegen in settings.json `freigaben: {agent: {an,
timeout, immer: [...], nie: [...]}}` — im Dashboard einsehbar und widerrufbar,
bewusst nicht in agents.yaml. Muster in Claude-Code-Schreibweise:
`Bash(docker exec backend-1:*)` (Präfix), `Edit(/pfad/datei)` (genau),
`Bash(git * main)` (Platzhalter), `WebFetch` (ganzes Werkzeug).

Nur Standardlib.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Callable

from app import config, ereignisse
from app.mailbox import AGENT_NAME_RE, atomic_write_json

ORDNER = "freigaben"
TIMEOUT_STANDARD = 900          # s Wartezeit auf den Menschen
TIMEOUT_MAX = 6 * 3600
TAKT = 1.0                      # s zwischen zwei Blicken auf die Anfrage
ERLEDIGT_BEHALTEN = 3 * 86400   # s, danach räumt `aufraeumen` Entschiedenes weg
TOOL_NAME = "mcp__dashboard__freigabe"
_ID_RE = re.compile(r"^frei-[0-9a-f]{12}$")
_MUSTER_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*(?:\((.*)\))?\s*$", re.S)
# sieht nach Zugangsdaten aus (Issue #64): lange Zufallsfolgen, key:secret, Bearer …
_GEHEIM_RE = re.compile(
    r"(?i)(?:bearer\s+|token\s+|password\s*[=:]\s*|passwd\s*[=:]\s*|api[_-]?key\s*[=:]\s*)\S{8,}"
    r"|[A-Za-z0-9+/_-]{32,}|\b[0-9a-f]{12,}:[0-9a-f]{12,}\b")


class FreigabeFehler(Exception):
    """Fachlicher Fehler mit Klartext."""


# --- Einstellungen ----------------------------------------------------------

def _liste(wert: Any) -> list[str]:
    if isinstance(wert, str):
        wert = wert.splitlines()
    return [str(m).strip() for m in (wert or []) if str(m).strip()][:200]


def einstellung(agent: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    roh = ((settings or config.load_settings()).get("freigaben") or {}).get(agent) or {}
    try:
        frist = int(roh.get("timeout") or TIMEOUT_STANDARD)
    except (TypeError, ValueError):
        frist = TIMEOUT_STANDARD
    return {"an": bool(roh.get("an")),
            "timeout": max(30, min(frist, TIMEOUT_MAX)),
            "immer": _liste(roh.get("immer")), "nie": _liste(roh.get("nie"))}


def setze_einstellung(agent: str, **felder: Any) -> dict[str, Any]:
    if not AGENT_NAME_RE.fullmatch(agent or ""):
        raise FreigabeFehler(f"ungültiger Agentenname: {agent!r}")
    alle = dict(config.load_settings().get("freigaben") or {})
    neu = einstellung(agent, {"freigaben": alle})
    for feld in ("an", "timeout", "immer", "nie"):
        if felder.get(feld) is not None:
            neu[feld] = felder[feld]
    for feld in ("immer", "nie"):
        neu[feld] = _liste(neu[feld])
        for muster in neu[feld]:
            if not _MUSTER_RE.match(muster):
                raise FreigabeFehler(
                    f"kein gültiges Muster: {muster!r} — z. B. Bash(git status:*) oder Edit")
    alle[agent] = neu
    config.save_settings({"freigaben": alle})
    return einstellung(agent)


# --- Muster -----------------------------------------------------------------

def haupt_eingabe(tool: str, eingabe: Any) -> str:
    """Das, woran ein Mensch den Aufruf erkennt: Befehl, Pfad, Adresse."""
    if not isinstance(eingabe, dict):
        return str(eingabe or "")
    for feld in ("command", "file_path", "path", "notebook_path", "url", "pattern", "query"):
        if isinstance(eingabe.get(feld), str) and eingabe[feld].strip():
            return eingabe[feld].strip()
    return json.dumps(eingabe, ensure_ascii=False, sort_keys=True)


def passt(muster: str, tool: str, eingabe: Any) -> bool:
    m = _MUSTER_RE.match(muster or "")
    if not m or m.group(1) != tool:
        return False
    regel = m.group(2)
    if regel is None or regel.strip() in ("", "*"):
        return True
    text = haupt_eingabe(tool, eingabe)
    regel = regel.strip()
    if regel.endswith(":*"):
        praefix = regel[:-2]
        return text == praefix or text.startswith(praefix + " ") or (
            text.startswith(praefix) and not praefix[-1:].isalnum())
    return text == regel or fnmatch.fnmatchcase(text, regel)


def vorschlag(tool: str, eingabe: Any) -> str:
    """Muster-Vorschlag für „Immer erlauben": bei Bash die ersten zwei Wörter
    als Präfix, sonst das ganze Werkzeug. Der Mensch kann ihn ändern."""
    text = haupt_eingabe(tool, eingabe)
    if tool == "Bash" and text:
        woerter = text.split()
        kopf = " ".join(woerter[:2]) if len(woerter) > 1 else woerter[0]
        if not re.search(r"[;&|<>$`(){}\n]", kopf):
            return f"Bash({kopf}:*)"
        return f"Bash({text})"
    return tool


def sieht_nach_geheimnis(text: str) -> bool:
    return bool(_GEHEIM_RE.search(str(text or "")))


# --- Anfragen ---------------------------------------------------------------

def _ordner(root: str | os.PathLike, agent: str) -> Path:
    if not AGENT_NAME_RE.fullmatch(agent or ""):
        raise FreigabeFehler(f"ungültiger Agentenname: {agent!r}")
    return Path(root) / agent / ORDNER


def _pfad(root: str | os.PathLike, agent: str, fid: str) -> Path:
    if not _ID_RE.fullmatch(fid or ""):
        raise FreigabeFehler(f"ungültige Freigabe-ID: {fid!r}")
    return _ordner(root, agent) / f"{fid}.json"


def _lies(pfad: Path) -> dict[str, Any] | None:
    try:
        d = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return d if isinstance(d, dict) else None


def _laufender_task(root: str | os.PathLike, agent: str) -> str | None:
    """Der Task, den der Watcher gerade bearbeitet (einer je Agent)."""
    try:
        for p in sorted((Path(root) / agent / "inbox" / ".processing").glob("*.json")):
            env = _lies(p) or {}
            if env.get("claim_von") == "watcher" and env.get("status") != "needs_confirm":
                return p.stem
    except OSError:
        pass
    return None


def lege_an(root: str | os.PathLike, agent: str, tool: str, eingabe: Any,
            tool_use_id: str | None = None, timeout: int = TIMEOUT_STANDARD) -> dict[str, Any]:
    anfrage = {
        "id": "frei-" + secrets.token_hex(6), "agent": agent, "tool": str(tool),
        "eingabe": eingabe if isinstance(eingabe, dict) else {"wert": eingabe},
        "text": haupt_eingabe(str(tool), eingabe)[:4000],
        "vorschlag": vorschlag(str(tool), eingabe),
        "task_id": _laufender_task(root, agent), "tool_use_id": tool_use_id,
        "status": "offen", "erstellt": time.time(), "frist": time.time() + timeout,
    }
    ordner = _ordner(root, agent)
    ordner.mkdir(parents=True, exist_ok=True)
    atomic_write_json(ordner / f"{anfrage['id']}.json", anfrage)
    return anfrage


def offene(root: str | os.PathLike) -> list[dict[str, Any]]:
    """Alle offenen Anfragen über alle Agenten, älteste zuerst (fürs Banner)."""
    out = []
    try:
        ordner = [d for d in Path(root).iterdir() if d.is_dir()]
    except OSError:
        return out
    jetzt = time.time()
    for d in ordner:
        for p in (d / ORDNER).glob("frei-*.json"):
            a = _lies(p)
            if a and a.get("status") == "offen" and float(a.get("frist") or 0) > jetzt:
                out.append({**a, "geheimnis_verdacht": sieht_nach_geheimnis(a.get("text"))})
    out.sort(key=lambda a: float(a.get("erstellt") or 0))
    return out


def entscheide(root: str | os.PathLike, agent: str, fid: str, erlauben: bool,
               wer: str = "dashboard", immer: str | None = None,
               nie: str | None = None) -> dict[str, Any]:
    """Entscheidung des Menschen eintragen. `immer`/`nie` = Muster, das
    zusätzlich in die jeweilige Liste des Agenten wandert."""
    pfad = _pfad(root, agent, fid)
    anfrage = _lies(pfad)
    if anfrage is None:
        raise FreigabeFehler("Diese Freigabe-Anfrage gibt es nicht (mehr).")
    if anfrage.get("status") != "offen":
        raise FreigabeFehler(f"Schon entschieden: {anfrage.get('status')}.")
    regel = (immer if erlauben else nie) or None
    if regel:
        if not _MUSTER_RE.match(regel) or not passt(regel, anfrage["tool"], anfrage["eingabe"]):
            raise FreigabeFehler(f"Das Muster {regel!r} passt nicht auf diesen Aufruf.")
        liste = "immer" if erlauben else "nie"
        aktuell = einstellung(agent)
        if regel not in aktuell[liste]:
            setze_einstellung(agent, **{liste: aktuell[liste] + [regel]})
    anfrage.update(status="erlaubt" if erlauben else "abgelehnt", entschieden_von=wer,
                   entschieden=time.time(), regel=regel)
    atomic_write_json(pfad, anfrage)
    return anfrage


def aufraeumen(root: str | os.PathLike, jetzt: float | None = None) -> int:
    jetzt = time.time() if jetzt is None else jetzt
    weg = 0
    try:
        ordner = [d for d in Path(root).iterdir() if d.is_dir()]
    except OSError:
        return 0
    for d in ordner:
        for p in (d / ORDNER).glob("frei-*.json"):
            try:
                if jetzt - p.stat().st_mtime > ERLEDIGT_BEHALTEN:
                    p.unlink()
                    weg += 1
            except OSError:
                continue
    return weg


# --- Der Weg des Tools -------------------------------------------------------

def _log(root, agent: str, zustand: str, anfrage: dict[str, Any], text: str,
         schwere: str = "info", **mehr: Any) -> None:
    ereignisse.schreibe(
        root, agent, "freigabe", text, schwere=schwere, task_id=anfrage.get("task_id"),
        zustand=zustand, freigabe_id=anfrage.get("id"), werkzeug=anfrage.get("tool"),
        eingabe=str(anfrage.get("text") or "")[:300], **mehr)


def pruefe(root: str | os.PathLike, agent: str, tool: str, eingabe: Any,
           tool_use_id: str | None = None,
           abbruch: Callable[[], bool] | None = None,
           takt: float = TAKT) -> dict[str, Any]:
    """Antwort für Claude Code: {"behavior": "allow"|"deny", …}. Blockiert,
    bis der Mensch entschieden hat, die Frist um ist oder `abbruch()` wahr wird."""
    tool = str(tool or "")
    einst = einstellung(agent)
    kurz = {"tool": tool, "text": haupt_eingabe(tool, eingabe)[:4000], "id": None,
            "task_id": _laufender_task(root, agent)}
    for muster in einst["nie"]:
        if passt(muster, tool, eingabe):
            _log(root, agent, "abgelehnt", kurz,
                 f"Freigabe abgelehnt (Regel „nie“: {muster}): {tool} · {kurz['text'][:120]}",
                 schwere="warnung", regel=muster, wer="regel")
            return {"behavior": "deny",
                    "message": f"Vom Betreiber dauerhaft untersagt (Regel {muster}). Nicht "
                               f"umgehen — melde im Ergebnis, dass dieser Schritt fehlt."}
    for muster in einst["immer"]:
        if passt(muster, tool, eingabe):
            _log(root, agent, "erlaubt", kurz,
                 f"Freigabe erlaubt (Regel „immer“: {muster}): {tool}", regel=muster,
                 wer="regel")
            return {"behavior": "allow",
                    "updatedInput": eingabe if isinstance(eingabe, dict) else {}}
    anfrage = lege_an(root, agent, tool, eingabe, tool_use_id, einst["timeout"])
    _log(root, agent, "angefragt", anfrage,
         f"Freigabe angefragt: {tool} · {anfrage['text'][:160]}", schwere="warnung")
    pfad = _pfad(root, agent, anfrage["id"])
    while True:
        stand = _lies(pfad) or anfrage
        if stand.get("status") in ("erlaubt", "abgelehnt"):
            wer = stand.get("entschieden_von") or "dashboard"
            if stand["status"] == "erlaubt":
                _log(root, agent, "erlaubt", stand, f"Freigabe erlaubt von {wer}: {tool}",
                     wer=wer, regel=stand.get("regel"))
                return {"behavior": "allow",
                        "updatedInput": eingabe if isinstance(eingabe, dict) else {}}
            _log(root, agent, "abgelehnt", stand, f"Freigabe abgelehnt von {wer}: {tool}",
                 wer=wer, regel=stand.get("regel"))
            return {"behavior": "deny",
                    "message": "Vom Menschen im Dashboard abgelehnt. Nicht umgehen — "
                               "melde im Ergebnis, dass dieser Schritt fehlt."}
        notaus = bool(abbruch and abbruch())
        if notaus or time.time() >= float(anfrage["frist"]):
            grund = "Not-Aus" if notaus else f"keine Antwort in {einst['timeout'] // 60} min"
            stand.update(status="abgelaufen", entschieden=time.time(), entschieden_von=grund)
            try:
                atomic_write_json(pfad, stand)
            except OSError:
                pass
            _log(root, agent, "abgelaufen", stand,
                 f"Freigabe abgelaufen ({grund}): {tool} · {anfrage['text'][:120]}",
                 schwere="warnung", wer=grund)
            return {"behavior": "deny",
                    "message": f"Keine Freigabe erhalten ({grund}). Nicht umgehen — melde "
                               f"im Ergebnis, dass dieser Schritt auf eine Freigabe wartet."}
        time.sleep(takt)
