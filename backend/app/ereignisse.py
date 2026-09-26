"""Ereignis-Log je Agent (Beobachtbarkeit der Automatik, 26.09.2026).

Bis dahin war die Automatik eine Blackbox: ob ein Lauf lange dauerte, teuer
war, seine Sitzung verlor (Issue #44), Werkzeuge verweigert bekam oder der
Watcher in einer Fehlerserie stehen blieb, stand verstreut in Task-Antworten,
im 20-Zeilen-Speicher-Log des Managers und im Container-Log. Hier landet je
Agent EINE dauerhafte Zeitleiste: `mailboxes/<agent>/ereignisse.jsonl`, ein
JSON-Objekt je Zeile.

Nur der SERVER schreibt (API- und MCP-Prozess) — der Watcher bleibt
unverändert, das Format hat eine Stelle, und ältere Watcher-Stände liefern
trotzdem Ereignisse. Anhängen einer Zeile unter 4 KiB mit O_APPEND ist auf
POSIX atomar; die beiden Prozesse brauchen deshalb keinen gemeinsamen Lock.

Der Push-Bündler (app/events.py) liest die Datei als Zweiter — über den
Datei-Wächter, denn Ereignisse aus dem MCP-Prozess erreichen keinen
In-Process-Hook des API-Prozesses.

Eintrag:
  zeit      ISO-Zeit (lokal, mit Offset)
  art       lauf | sitzung | verweigert | timeout | fehlserie | watcher_abriss |
            watcher_start | weckruf | send_file | integration
  schwere   info | warnung | fehler
  task_id   optional
  text      eine Zeile für Menschen
  details   Zahlen/Namen für die Anzeige (Dauer, Tokens, Kosten, Kontext, …)
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

DATEI = "ereignisse.jsonl"
SCHWEREN = ("info", "warnung", "fehler")
ARTEN = frozenset({
    "lauf", "sitzung", "verweigert", "timeout", "fehlserie", "watcher_abriss",
    "watcher_start", "weckruf", "send_file", "integration",
})
# Immer pushen, auch innerhalb der Bündel-Sperre: dann steht die Automatik.
STETS_PUSHEN = frozenset({"fehlserie", "watcher_abriss"})
MAX_ZEILEN = 5000          # Deckel neben der Zeit-Rotation (gesprächige Agenten)
MAX_ZEILE_BYTES = 3500     # unter PIPE_BUF (4096): das Anhängen bleibt atomar
STANDARD_LIMIT = 50
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _jetzt() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def pfad(root: str | os.PathLike, agent: str) -> Path:
    if not _NAME_RE.match(agent or ""):
        raise ValueError(f"ungültiger Agentenname: {agent!r}")
    return Path(root) / agent / DATEI


def schreibe(root: str | os.PathLike, agent: str, art: str, text: str,
             schwere: str = "info", task_id: str | None = None,
             **details: Any) -> dict[str, Any]:
    """Einen Eintrag anhängen. Best-effort: ein Schreibfehler darf nie den
    eigentlichen Vorgang (Task-Abschluss, Watcher-Neustart) scheitern lassen —
    deshalb werden OSError hier geschluckt (der Eintrag geht dann verloren)."""
    if art not in ARTEN:
        raise ValueError(f"unbekannte Ereignis-Art: {art}")
    if schwere not in SCHWEREN:
        raise ValueError(f"unbekannte Schwere: {schwere}")
    eintrag: dict[str, Any] = {
        "zeit": _jetzt(), "art": art, "schwere": schwere,
        "task_id": task_id or None, "text": str(text or "").strip()[:500],
        "details": {k: v for k, v in details.items() if v is not None},
    }
    zeile = json.dumps(eintrag, ensure_ascii=False, separators=(",", ":"))
    if len(zeile.encode("utf-8")) > MAX_ZEILE_BYTES:
        eintrag["details"] = {}
        zeile = json.dumps(eintrag, ensure_ascii=False, separators=(",", ":"))
    try:
        p = pfad(root, agent)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(zeile + "\n")
    except OSError:
        pass
    return eintrag


def _parse(zeile: str) -> dict[str, Any] | None:
    try:
        e = json.loads(zeile)
    except json.JSONDecodeError:
        return None
    return e if isinstance(e, dict) and e.get("zeit") and e.get("art") else None


def _alle(root: str | os.PathLike, agent: str) -> list[dict[str, Any]]:
    try:
        text = pfad(root, agent).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    return [e for z in text.splitlines() if (e := _parse(z))]


def lies(root: str | os.PathLike, agent: str, limit: int = STANDARD_LIMIT,
         vor: str | None = None, seit: str | None = None,
         art: str | Iterable[str] | None = None,
         nur_probleme: bool = False) -> dict[str, Any]:
    """Neueste zuerst. `vor` = nur Einträge mit zeit < vor („mehr laden"),
    `seit` = nur zeit >= seit, `art` = eine Art oder Liste, `nur_probleme` =
    Schwere warnung/fehler. → {"eintraege", "mehr" (ältere vorhanden), "gesamt"}."""
    arten = {art} if isinstance(art, str) else (set(art) if art else None)
    alle = _alle(root, agent)
    gefiltert = [
        e for e in alle
        if (not vor or e["zeit"] < vor)
        and (not seit or e["zeit"] >= seit)
        and (arten is None or e["art"] in arten)
        and (not nur_probleme or e.get("schwere") in ("warnung", "fehler"))
    ]
    gefiltert.reverse()
    limit = max(1, int(limit or STANDARD_LIMIT))
    return {"eintraege": gefiltert[:limit], "mehr": len(gefiltert) > limit,
            "gesamt": len(alle)}


def lies_ab(root: str | os.PathLike, agent: str, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Neue Einträge ab Byte-Offset (für den Push-Bündler im API-Prozess).
    → (Einträge in Schreibreihenfolge, neuer Offset). Schrumpft die Datei
    (Rotation), beginnt der Leser vorn — und meldet NICHT den ganzen Bestand,
    sondern setzt den Offset still ans Ende."""
    try:
        p = pfad(root, agent)
        groesse = p.stat().st_size
    except (OSError, ValueError):
        return [], 0
    if groesse < offset:
        return [], groesse
    if groesse == offset:
        return [], offset
    with open(p, "rb") as f:
        f.seek(offset)
        roh = f.read()
    # nur vollständige Zeilen verbuchen — eine halb geschriebene bleibt für
    # den nächsten Durchlauf liegen
    ende = roh.rfind(b"\n")
    if ende < 0:
        return [], offset
    text = roh[: ende + 1].decode("utf-8", "replace")
    return [e for z in text.splitlines() if (e := _parse(z))], offset + ende + 1


def rotiere(root: str | os.PathLike, agent: str, tage: float = 30.0,
            max_zeilen: int = MAX_ZEILEN) -> int:
    """Alte Einträge entfernen (älter als `tage`) und auf `max_zeilen` deckeln.
    → Zahl der entfernten Einträge. Neu geschrieben wird nur, wenn etwas weg
    muss (atomar über tmp + replace, damit ein Leser nie eine halbe Datei sieht)."""
    try:
        p = pfad(root, agent)
        text = p.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return 0
    zeilen = text.splitlines()
    grenze = (datetime.now().astimezone() - timedelta(days=float(tage))).isoformat(timespec="seconds")
    behalten = []
    for z in zeilen:
        e = _parse(z)
        if e is None:
            continue
        if tage > 0 and e["zeit"] < grenze:
            continue
        behalten.append(z)
    if max_zeilen > 0 and len(behalten) > max_zeilen:
        behalten = behalten[-max_zeilen:]
    entfernt = len(zeilen) - len(behalten)
    if entfernt <= 0:
        return 0
    tmp = p.with_suffix(".jsonl.tmp")
    try:
        tmp.write_text("".join(z + "\n" for z in behalten), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return 0
    return entfernt


def rotiere_alle(root: str | os.PathLike, tage: float = 30.0) -> int:
    """Alle Agenten-Logs unter root rotieren (aus der Mailbox-Pflege)."""
    gesamt = 0
    try:
        ordner = [d for d in Path(root).iterdir() if d.is_dir()]
    except OSError:
        return 0
    for d in ordner:
        if (d / DATEI).exists() and _NAME_RE.match(d.name):
            gesamt += rotiere(root, d.name, tage)
    return gesamt


# --- Hilfen für die Anschlussstellen ---------------------------------------

def _dauer_sekunden(von: str | None, bis: str | None) -> float | None:
    try:
        a = datetime.fromisoformat(str(von))
        b = datetime.fromisoformat(str(bis))
    except (TypeError, ValueError):
        return None
    if a.tzinfo is None or b.tzinfo is None:
        a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)
    s = (b - a).total_seconds()
    return round(s, 1) if s >= 0 else None


def _tokens(verbrauch: dict[str, Any] | None) -> int:
    if not isinstance(verbrauch, dict):
        return 0
    summe = 0
    for feld in ("input_tokens", "output_tokens",
                 "cache_creation_input_tokens", "cache_read_input_tokens"):
        try:
            summe += int(verbrauch.get(feld) or 0)
        except (TypeError, ValueError):
            pass
    return summe


def _kosten(verbrauch: dict[str, Any] | None) -> float | None:
    if not isinstance(verbrauch, dict):
        return None
    k = verbrauch.get("total_cost_usd")
    return round(float(k), 4) if isinstance(k, (int, float)) else None


def _dauer_text(s: float | None) -> str:
    if s is None:
        return ""
    if s < 90:
        return f"{s:.0f} s"
    return f"{s / 60:.0f} min"


def lauf_ereignisse(root: str | os.PathLike, agent: str, task_id: str, status: str,
                    claimed_at: str | None, responded_at: str | None,
                    verbrauch: dict[str, Any] | None, lauf: dict[str, Any] | None,
                    nachgetragen: bool = False) -> list[dict[str, Any]]:
    """Alles, was ein Task-Abschluss an Ereignissen hergibt: den Lauf selbst
    (Status, Dauer, Tokens, Kosten, Kontext, Modell) und — wenn `lauf` da ist —
    Sitzung, Verweigerungen, Timeout. `nachgetragen=True` (Issue #43: der
    Watcher liefert die Lauf-Daten NACH dem Eigenabschluss des Kindes)
    schreibt den Lauf-Eintrag nur, wenn er dadurch Zahlen bekommt."""
    lauf = lauf if isinstance(lauf, dict) else {}
    geschrieben: list[dict[str, Any]] = []
    tokens = _tokens(verbrauch)
    kosten = _kosten(verbrauch)
    dauer = _dauer_sekunden(claimed_at, responded_at)
    if not nachgetragen or tokens or kosten is not None or lauf.get("kontext"):
        teile = [status]
        if dauer is not None:
            teile.append(_dauer_text(dauer))
        if tokens:
            teile.append(f"{tokens / 1000:.0f}k Tok")
        if kosten is not None:
            teile.append(f"{kosten:.2f} $")
        if lauf.get("kontext"):
            teile.append(f"Kontext {int(lauf['kontext']) // 1000}k")
        text = ("Lauf-Daten nachgetragen: " if nachgetragen else "Lauf ") + " · ".join(teile)
        geschrieben.append(schreibe(
            root, agent, "lauf", text,
            schwere="fehler" if status == "error" else "info", task_id=task_id,
            status=status, dauer=dauer, tokens=tokens or None, kosten=kosten,
            kontext=lauf.get("kontext"), modell=lauf.get("modell"),
            session_id=lauf.get("session_id"), nachgetragen=nachgetragen or None))
    if lauf.get("sitzung"):
        grund = str(lauf.get("sitzung_grund") or "")
        kontext_neustart = lauf["sitzung"] == "neu" and "Grenze" in grund
        geschrieben.append(schreibe(
            root, agent, "sitzung",
            ("neue Sitzung" if lauf["sitzung"] == "neu" else "Sitzung fortgesetzt")
            + (f": {grund}" if grund else ""),
            schwere="warnung" if kontext_neustart else "info", task_id=task_id,
            sitzung=lauf["sitzung"], grund=grund or None,
            kontext_neustart=kontext_neustart or None, thread=lauf.get("thread")))
    verweigert = lauf.get("verweigert")
    if isinstance(verweigert, list) and verweigert:
        werkzeuge = sorted({str(v.get("tool") or "?") for v in verweigert if isinstance(v, dict)})
        geschrieben.append(schreibe(
            root, agent, "verweigert",
            f"{len(verweigert)} verweigerte Aufrufe: {', '.join(werkzeuge)}",
            schwere="warnung", task_id=task_id, anzahl=len(verweigert),
            werkzeuge=werkzeuge,
            beispiel=str((verweigert[0] or {}).get("eingabe") or "")[:200] or None))
    if lauf.get("timeout"):
        geschrieben.append(schreibe(
            root, agent, "timeout", f"abgebrochen: {lauf['timeout']}"
            + (" · fortsetzbar" if lauf.get("fortsetzbar") else ""),
            schwere="warnung", task_id=task_id, grund=str(lauf["timeout"]),
            fortsetzbar=bool(lauf.get("fortsetzbar")) or None))
    return geschrieben
