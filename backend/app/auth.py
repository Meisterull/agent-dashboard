"""Session-Auth für die Dashboard-API.

Ein Passwort (ADMIN_INITIAL_PASSWORD aus .env), ein HMAC-signierter
Session-Token im HttpOnly-Cookie. Kein User-Modell — das Dashboard ist ein
Einzelbenutzer-Werkzeug; geschützt wird der Zugriff aus dem Netz (Terminals,
SFTP, Editor sind faktisch Vollzugriff auf alle Agenten-PCs).

Ist KEIN Passwort konfiguriert, bleibt die API offen (Dev-Betrieb) — der
Zustand wird beim Start deutlich geloggt.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

COOKIE_NAME = "dash_session"
SESSION_TTL = 30 * 24 * 3600  # 30 Tage — Handy-Komfort


def _password() -> str:
    return os.environ.get("ADMIN_INITIAL_PASSWORD", "").strip()


def _secret() -> bytes:
    """Signier-Secret: SESSION_SECRET aus .env, sonst vom Passwort abgeleitet.

    Die Ableitung hält Sessions über Container-Neustarts gültig, ohne einen
    zweiten Pflicht-Konfigwert zu erzwingen.
    """
    explicit = os.environ.get("SESSION_SECRET", "").strip()
    if explicit:
        return explicit.encode()
    return hashlib.sha256(("dash-session:" + _password()).encode()).digest()


def enabled() -> bool:
    return bool(_password())


def verify_password(candidate: str) -> bool:
    return enabled() and hmac.compare_digest(candidate, _password())


def make_token() -> str:
    ts = str(int(time.time()))
    sig = hmac.new(_secret(), ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def check_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    ts, sig = token.split(".", 1)
    expect = hmac.new(_secret(), ts.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return False
    try:
        return int(ts) + SESSION_TTL > time.time()
    except ValueError:
        return False
