"""Geheimnis-Speicher je Agent (Issue #64).

Wer in Claude Code „immer erlauben" wählt, schreibt den GANZEN Befehl samt
Token in die Freigabeliste — und Tokens stehen in Task-Texten, Antworten und
Logs, sobald ein Agent sie in einen Befehl schreibt. Hier legt der Mensch sie
stattdessen einmal ab; die Automatik-Läufe bekommen sie als
UMGEBUNGSVARIABLEN (`$ERP_DEV_TOKEN`), Freigaben und Logs enthalten nur noch
den Namen.

  * Ablage: `<workspace>/keys/geheimnisse.json` (der Ordner ist fürs
    Datei-Panel gesperrt, Datei 0600), die Werte Fernet-verschlüsselt. Der
    Schlüssel kommt aus `GEHEIMNIS_SCHLUESSEL` (Umgebung/Docker-Secret) oder
    liegt als `keys/geheimnis.key` (0600) daneben. Ehrlich: liegt der
    Schlüssel daneben, schützt das vor dem beiläufigen Blick und vor
    Backups der JSON-Datei allein — nicht vor jemandem mit vollem Zugriff
    auf das Volume. Für mehr: Schlüssel per Umgebung setzen.
  * Die API gibt Werte NIE zurück — nur Namen. Setzen und Ersetzen geht,
    Lesen nicht.
  * Der Automatik-Manager reicht die Werte über den stdin-Kanal an den
    Watcher (`env <base64>`), nicht als Argument (sonst stünden sie in `ps`).
  * `schwaerze` ersetzt bekannte Werte in Texten durch `***NAME***`.

`cryptography` wird erst beim Ver-/Entschlüsseln geladen (im Image vorhanden,
asyncssh braucht es ohnehin); der Rest ist Standardlib.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from app.config import KEYS_DIR
from app.mailbox import AGENT_NAME_RE

DATEI = "geheimnisse.json"
SCHLUESSEL_DATEI = "geheimnis.key"
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
# Variablen, über die sich der Lauf selbst kapern ließe — nie als Geheimnis.
VERBOTEN = {"PATH", "HOME", "SHELL", "USER", "IFS", "PWD", "LD_PRELOAD", "LD_LIBRARY_PATH",
            "PYTHONPATH", "PYTHONSTARTUP", "NODE_OPTIONS", "BASH_ENV", "ENV", "MCP_TOKEN"}
MAX_WERT = 8192
MIN_SCHWAERZEN = 6   # kürzere Werte träfen beim Schwärzen zu viel Harmloses


class GeheimnisFehler(Exception):
    """Fachlicher Fehler mit Klartext."""


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover — im Image vorhanden
        raise GeheimnisFehler("Paket »cryptography« fehlt — Geheimnisse nicht verfügbar.") from exc
    roh = os.environ.get("GEHEIMNIS_SCHLUESSEL", "").strip()
    if roh:
        schluessel = base64.urlsafe_b64encode(hashlib.sha256(roh.encode("utf-8")).digest())
    else:
        pfad = KEYS_DIR / SCHLUESSEL_DATEI
        try:
            schluessel = pfad.read_bytes().strip()
        except OSError:
            KEYS_DIR.mkdir(parents=True, exist_ok=True)
            schluessel = Fernet.generate_key()
            fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(schluessel)
    return Fernet(schluessel)


def verfuegbar() -> bool:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return False
    return True


def _lade() -> dict[str, dict[str, str]]:
    try:
        daten = json.loads((KEYS_DIR / DATEI).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(daten, dict):
        return {}
    return {str(a): {str(n): str(w) for n, w in e.items()}
            for a, e in daten.items() if isinstance(e, dict)}


def _speichere(daten: dict[str, dict[str, str]]) -> None:
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    pfad = KEYS_DIR / DATEI
    tmp = pfad.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, pfad)


def namen(agent: str | None = None) -> Any:
    """Nur die NAMEN — je Agent sortiert, oder {agent: [namen]} für alle."""
    daten = _lade()
    if agent is not None:
        return sorted(daten.get(agent, {}))
    return {a: sorted(e) for a, e in daten.items() if e}


def _pruefe(agent: str, name: str) -> None:
    if not AGENT_NAME_RE.fullmatch(agent or ""):
        raise GeheimnisFehler(f"ungültiger Agentenname: {agent!r}")
    if not NAME_RE.fullmatch(name or "") or name in VERBOTEN:
        raise GeheimnisFehler(
            f"ungültiger Name {name!r} — GROSSBUCHSTABEN, Ziffern, Unterstrich "
            f"(z. B. ERP_DEV_TOKEN); Systemvariablen wie PATH sind nicht erlaubt.")


def setze(agent: str, name: str, wert: str) -> None:
    _pruefe(agent, name)
    wert = str(wert or "")
    if not wert or len(wert.encode("utf-8")) > MAX_WERT:
        raise GeheimnisFehler(f"Wert fehlt oder ist länger als {MAX_WERT} Bytes.")
    if re.search(r"[\x00\r\n]", wert):
        raise GeheimnisFehler("Zeilenumbrüche sind im Wert nicht erlaubt.")
    daten = _lade()
    daten.setdefault(agent, {})[name] = _fernet().encrypt(wert.encode("utf-8")).decode("ascii")
    _speichere(daten)


def loesche(agent: str, name: str) -> bool:
    _pruefe(agent, name)
    daten = _lade()
    if name not in daten.get(agent, {}):
        return False
    del daten[agent][name]
    if not daten[agent]:
        del daten[agent]
    _speichere(daten)
    return True


def werte(agent: str) -> dict[str, str]:
    """Klartext-Werte eines Agenten — NUR serverseitig (Watcher-Übergabe,
    Schwärzen). Unlesbare Einträge (Schlüssel gewechselt) fallen still weg."""
    eintraege = _lade().get(agent, {})
    if not eintraege:
        return {}
    try:
        f = _fernet()
    except GeheimnisFehler:
        return {}
    aus = {}
    for name, chiffre in eintraege.items():
        try:
            aus[name] = f.decrypt(chiffre.encode("ascii")).decode("utf-8")
        except Exception:  # noqa: BLE001 — falscher Schlüssel/kaputter Eintrag
            continue
    return aus


def signatur(agent: str) -> str:
    """Ändert sich, sobald sich die Geheimnisse des Agenten ändern — daran
    erkennt der Manager, dass er dem Watcher neue Werte reichen muss."""
    eintraege = _lade().get(agent, {})
    roh = json.dumps(eintraege, sort_keys=True).encode("utf-8")
    return hashlib.sha256(roh).hexdigest()[:16] if eintraege else ""


def env_zeile(agent: str) -> str:
    """Die stdin-Zeile für den Watcher: `env <base64(JSON)>`."""
    daten = json.dumps(werte(agent), ensure_ascii=False).encode("utf-8")
    return "env " + base64.b64encode(daten).decode("ascii") + "\n"


def env_datei(agent: str) -> str:
    """Inhalt von ~/.agent-dashboard/env für interaktive Sitzungen
    (`. ~/.agent-dashboard/env` in der Shell-Startdatei)."""
    zeilen = ["# vom Agent-Dashboard gepflegt (Geheimnisse) — nicht von Hand ändern"]
    for name, wert in sorted(werte(agent).items()):
        zeilen.append("export %s='%s'" % (name, wert.replace("'", "'\\''")))
    return "\n".join(zeilen) + "\n"


def schwaerze_mit(text: Any, bekannte: dict[str, str]) -> Any:
    if not isinstance(text, str) or not text or not bekannte:
        return text
    for name, wert in sorted(bekannte.items(), key=lambda kv: -len(kv[1])):
        if len(wert) >= MIN_SCHWAERZEN and wert in text:
            text = text.replace(wert, f"***{name}***")
    return text


def schwaerze(agent: str, text: Any) -> Any:
    """Bekannte Geheimniswerte des Agenten in einem Text unkenntlich machen."""
    if not isinstance(text, str) or not text:
        return text
    return schwaerze_mit(text, werte(agent))
