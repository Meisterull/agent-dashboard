"""Zwischenspeicher für den Dateiaustausch mit Token-Maschinen (Issue #57).

Eine Token-Maschine (#32) erreicht das Dashboard nicht per SFTP — sie muss
Dateien selbst ABHOLEN bzw. HOCHLADEN. Dazwischen liegen sie hier:

    <workspace>/austausch-speicher/<id>/meta.json + inhalt

  * `send_file(to=<Token-Maschine>)` und `deliver_upload` legen eine Datei ab
    (Status `bereit`); der Empfänger bekommt die übliche Nachricht mit
    `abholen: [id]` und holt per MCP-Tool `fetch_file(id)` eine Download-URL.
  * `upload_url(to, name)` reserviert einen Eintrag (Status `erwartet`); die
    Maschine lädt per PUT hoch, das Dashboard reicht weiter.

Der Zugriff über HTTP (`/mcp/<agent>/datei/<id>?schluessel=…`) hängt an einem
zufälligen Schlüssel je Eintrag — er steht nur in der Antwort des MCP-Tools,
das nur der adressierte Agent auf seinem gebundenen Kanal aufrufen kann
(Kanal-Identität, #13). Einträge verfallen nach `AUSTAUSCH_ABHOL_TAGE`.

Nur Standardlib.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))
ROOT = WORKSPACE / "austausch-speicher"
ABHOL_TAGE = float(os.environ.get("AUSTAUSCH_ABHOL_TAGE", "7"))
ERWARTET_STUNDEN = 24.0   # ein reservierter, nie genutzter Upload verfällt schneller
BASIS_DATEI = "basis.json"  # {agent: "https://host:port"} — von der API gemerkt
_ID_RE = re.compile(r"^datei-[0-9a-f]{16}$")


class SpeicherFehler(Exception):
    """Fachlicher Fehler mit Klartext."""


def _ordner(datei_id: str) -> Path:
    if not _ID_RE.fullmatch(datei_id or ""):
        raise SpeicherFehler(f"ungültige Datei-ID: {datei_id!r}")
    return ROOT / datei_id


def _schreibe_meta(meta: dict[str, Any]) -> None:
    ordner = _ordner(meta["id"])
    ordner.mkdir(parents=True, exist_ok=True)
    tmp = ordner / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, ordner / "meta.json")


def lege_an(an: str, von: str, name: str, status: str = "bereit",
            weiter_an: str | None = None, notiz: str | None = None) -> dict[str, Any]:
    """Neuen Eintrag anlegen. `an` = wer per URL zugreifen darf (bei `bereit`
    der Abholer, bei `erwartet` der Hochlader); `weiter_an` = wohin ein
    hochgeladener Eintrag danach geht."""
    meta = {
        "id": "datei-" + secrets.token_hex(8),
        "schluessel": secrets.token_urlsafe(24),
        "an": an, "von": von, "name": name, "status": status,
        "weiter_an": weiter_an, "notiz": notiz,
        "bytes": 0, "erstellt": time.time(), "abgeholt": 0,
    }
    _schreibe_meta(meta)
    return meta


def lies(datei_id: str) -> dict[str, Any]:
    try:
        meta = json.loads((_ordner(datei_id) / "meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise SpeicherFehler(f"Datei {datei_id} gibt es nicht (mehr) — "
                             f"abgelaufen oder nie angelegt.") from None
    if not isinstance(meta, dict) or meta.get("id") != datei_id:
        raise SpeicherFehler(f"Datei {datei_id} ist beschädigt.")
    return meta


def inhalt(datei_id: str) -> Path:
    return _ordner(datei_id) / "inhalt"


def pruefe(agent: str, datei_id: str, schluessel: str | None) -> dict[str, Any]:
    """Zugriff per URL prüfen: richtiger Agent UND richtiger Schlüssel
    (Vergleich in konstanter Zeit). Wirft SpeicherFehler ohne Unterschied,
    woran es lag — das geht einen fremden Aufrufer nichts an."""
    try:
        meta = lies(datei_id)
    except SpeicherFehler:
        raise SpeicherFehler("nicht berechtigt") from None
    if meta.get("an") != agent or not hmac.compare_digest(
            str(meta.get("schluessel") or ""), str(schluessel or "")):
        raise SpeicherFehler("nicht berechtigt")
    return meta


def aktualisiere(datei_id: str, **felder: Any) -> dict[str, Any]:
    meta = lies(datei_id)
    meta.update(felder)
    _schreibe_meta(meta)
    return meta


def loesche(datei_id: str) -> None:
    shutil.rmtree(_ordner(datei_id), ignore_errors=True)


def liste(an: str | None = None, status: str | None = "bereit") -> list[dict[str, Any]]:
    """Einträge (ohne Schlüssel), neueste zuerst — fürs Panel und die Tools."""
    out = []
    if not ROOT.is_dir():
        return out
    for d in ROOT.iterdir():
        if not d.is_dir() or not _ID_RE.fullmatch(d.name):
            continue
        try:
            meta = lies(d.name)
        except SpeicherFehler:
            continue
        if (an is None or meta.get("an") == an) and (status is None or meta.get("status") == status):
            out.append({k: v for k, v in meta.items() if k != "schluessel"})
    out.sort(key=lambda m: -float(m.get("erstellt") or 0))
    return out


def aufraeumen(tage: float | None = None, jetzt: float | None = None) -> int:
    """Abgelaufene Einträge löschen (aus der Mailbox-Pflege). → Anzahl."""
    tage = ABHOL_TAGE if tage is None else tage
    jetzt = time.time() if jetzt is None else jetzt
    weg = 0
    if not ROOT.is_dir():
        return 0
    for d in list(ROOT.iterdir()):
        if not d.is_dir() or not _ID_RE.fullmatch(d.name):
            continue
        try:
            meta = lies(d.name)
            alter = jetzt - float(meta.get("erstellt") or 0)
            frist = (ERWARTET_STUNDEN * 3600 if meta.get("status") == "erwartet"
                     else tage * 86400)
        except (SpeicherFehler, TypeError, ValueError):
            alter, frist = jetzt - d.stat().st_mtime, tage * 86400
        if alter > frist:
            shutil.rmtree(d, ignore_errors=True)
            weg += 1
    return weg


# --- Wie erreicht der Agent das Dashboard? ----------------------------------
# Der MCP-Prozess kennt die Außenadresse nicht. Die API sieht sie aber bei
# jeder Anfrage auf /mcp/<agent> (Host-Kopfzeile) und merkt sie hier.

def merke_basis(agent: str, basis: str) -> None:
    if not re.fullmatch(r"https?://[A-Za-z0-9.\-\[\]:]+", basis or ""):
        return
    pfad = ROOT / BASIS_DATEI
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        daten = {}
    if not isinstance(daten, dict):
        daten = {}
    if daten.get(agent) == basis:
        return
    daten[agent] = basis
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(daten), encoding="utf-8")
    os.replace(tmp, pfad)


def basis(agent: str) -> str:
    """Außenadresse des Dashboards aus Sicht dieses Agenten: `DASHBOARD_URL`
    (falls gesetzt), sonst die zuletzt gesehene — sonst leer (dann nennt das
    Tool nur den Pfad und der Agent setzt den Host seiner MCP-URL davor)."""
    fest = os.environ.get("DASHBOARD_URL", "").strip().rstrip("/")
    if fest:
        return fest
    try:
        daten = json.loads((ROOT / BASIS_DATEI).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return ""
    return str(daten.get(agent) or "") if isinstance(daten, dict) else ""


def url(meta: dict[str, Any]) -> dict[str, str]:
    """URL-Angaben zu einem Eintrag: `pfad` immer, `url` wenn die Außenadresse
    bekannt ist."""
    pfad = f"/mcp/{meta['an']}/datei/{meta['id']}?schluessel={meta['schluessel']}"
    b = basis(meta["an"])
    return {"pfad": pfad, **({"url": b + pfad} if b else {})}
