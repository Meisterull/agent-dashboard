"""Web-Push (F10): Rückfragen und fertige Tasks erreichen das Handy.

Warum Push statt Polling: mobil schläft das Frontend-Polling, sobald die PWA
im Hintergrund ist — eine Rückfrage (needs_confirm) blockiert dann stundenlang
ihren geparkten Task, ohne dass es jemand merkt. Web Push weckt das Gerät über
den Browser-Pushdienst, ganz ohne offene Verbindung zur PWA.

Bausteine:
- VAPID-Schlüsselpaar: einmalig erzeugt und in DATA_CONFIG_DIR/vapid.json
  abgelegt (P-256; braucht `cryptography` — via asyncssh ohnehin im Image).
- Subscriptions: je Gerät eine, vom Browser geliefert, in
  DATA_CONFIG_DIR/push_subscriptions.json (atomar geschrieben, dedupliziert
  über den Endpoint). Abgelaufene (404/410 beim Senden) fliegen automatisch.
- Versand: pywebpush (lazy importiert). Fehlt das Paket — etwa auf dem Host
  oder in einem Image von vor diesem Feature — wird still übersprungen:
  Subscriptions sammeln geht trotzdem, versandt wird nach dem Rebuild.

Ausgelöst wird der Versand vom Mailbox-Wächter in app/events.py.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
from pathlib import Path
from typing import Any

from app.mailbox import atomic_write_json

DATA_CONFIG_DIR = Path(os.environ.get("DATA_CONFIG_DIR", "/workspace/config"))
VAPID_PATH = DATA_CONFIG_DIR / "vapid.json"
SUBS_PATH = DATA_CONFIG_DIR / "push_subscriptions.json"

# VAPID verlangt einen Kontakt (mailto:/https:) — rein formal, aber Pflicht.
VAPID_SUB = os.environ.get("PUSH_VAPID_SUB", "mailto:admin@agent-dashboard.local")

# Ein Lock für Lesen-Ändern-Schreiben auf der Subscription-Datei: Subscribe
# vom Handy und Aufräumen toter Endpoints (Sende-Thread) laufen nebenläufig.
_subs_lock = threading.Lock()

_pywebpush_fehlt_gemeldet = False


# --- VAPID-Schlüssel --------------------------------------------------------

def _erzeuge_vapid() -> dict[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    priv_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    # applicationServerKey fürs Frontend: unkomprimierter P-256-Punkt (65 B)
    # als base64url ohne Padding — genau das Format, das pushManager.subscribe
    # erwartet.
    pub = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return {
        "private_pem": priv_pem,
        "public_b64": base64.urlsafe_b64encode(pub).rstrip(b"=").decode(),
    }


def vapid_daten() -> dict[str, str] | None:
    """Schlüsselpaar laden, beim ersten Aufruf erzeugen. None ohne cryptography."""
    if VAPID_PATH.exists():
        try:
            daten = json.loads(VAPID_PATH.read_text(encoding="utf-8"))
            if daten.get("private_pem") and daten.get("public_b64"):
                return daten
        except (json.JSONDecodeError, OSError):
            pass  # kaputt → neu erzeugen (alte Subscriptions werden damit ungültig)
    try:
        daten = _erzeuge_vapid()
    except ImportError:
        return None
    atomic_write_json(VAPID_PATH, daten)
    try:
        VAPID_PATH.chmod(0o600)  # privater Schlüssel
    except OSError:
        pass
    return daten


def public_key() -> str | None:
    daten = vapid_daten()
    return daten["public_b64"] if daten else None


# --- Subscriptions ----------------------------------------------------------

def lade_subscriptions() -> list[dict[str, Any]]:
    try:
        daten = json.loads(SUBS_PATH.read_text(encoding="utf-8"))
        return [s for s in daten if isinstance(s, dict) and s.get("endpoint")]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def add_subscription(sub: dict[str, Any]) -> int:
    """Subscription eintragen (dedupliziert über endpoint). Liefert die Anzahl."""
    if not sub.get("endpoint"):
        raise ValueError("Subscription ohne endpoint")
    with _subs_lock:
        subs = [s for s in lade_subscriptions() if s["endpoint"] != sub["endpoint"]]
        subs.append(sub)
        atomic_write_json(SUBS_PATH, subs)
    return len(subs)


def remove_subscription(endpoint: str) -> bool:
    with _subs_lock:
        subs = lade_subscriptions()
        rest = [s for s in subs if s["endpoint"] != endpoint]
        if len(rest) == len(subs):
            return False
        atomic_write_json(SUBS_PATH, rest)
    return True


def anzahl_subscriptions() -> int:
    return len(lade_subscriptions())


def versand_verfuegbar() -> bool:
    """Kann dieses Backend tatsächlich senden? (pywebpush erst nach Rebuild da.)"""
    try:
        import pywebpush  # noqa: F401
        return True
    except ImportError:
        return False


# --- Versand ----------------------------------------------------------------

def _sende_sync(sub: dict[str, Any], payload: str, priv_pem: str) -> bool:
    """Eine Subscription bedienen (blockierend — pywebpush nutzt requests)."""
    global _pywebpush_fehlt_gemeldet
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        if not _pywebpush_fehlt_gemeldet:
            _pywebpush_fehlt_gemeldet = True
            print("[push] pywebpush fehlt — Versand übersprungen (Image neu bauen)", flush=True)
        return False
    try:
        webpush(
            subscription_info=sub,
            data=payload,
            vapid_private_key=priv_pem,
            vapid_claims={"sub": VAPID_SUB},
        )
        return True
    except WebPushException as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        if code in (404, 410):
            # Gerät hat die Subscription verloren (App neu installiert,
            # Berechtigung entzogen) — Karteileiche entfernen.
            remove_subscription(sub["endpoint"])
        else:
            print(f"[push] Versand fehlgeschlagen ({code}): {exc}", flush=True)
        return False
    except Exception as exc:  # noqa: BLE001 — Versand darf nie den Wächter killen
        print(f"[push] Versand fehlgeschlagen: {exc}", flush=True)
        return False


async def sende_an_alle(
    titel: str,
    text: str,
    tag: str = "",
    url: str = "/",
    extra: dict | None = None,
) -> int:
    """Benachrichtigung an alle Geräte. Liefert die Zahl erfolgreicher Sends.

    `extra` landet unverändert im Payload — darüber bekommt der Service Worker
    alles, was er zum Beantworten direkt aus der Meldung braucht (Issue #30):
    Agent, Frage-ID und die angebotenen Antworten.
    """
    subs = lade_subscriptions()
    if not subs:
        return 0
    daten = vapid_daten()
    if daten is None:
        return 0
    payload = json.dumps(
        {"title": titel, "body": text, "tag": tag, "url": url, **(extra or {})},
        ensure_ascii=False,
    )
    ergebnisse = await asyncio.gather(
        *[asyncio.to_thread(_sende_sync, s, payload, daten["private_pem"]) for s in subs]
    )
    return sum(1 for ok in ergebnisse if ok)
