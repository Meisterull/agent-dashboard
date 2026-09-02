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

# (Passwort, abgeleitetes Secret) — siehe _secret().
_abgeleitet: tuple[str, bytes] | None = None


def _password() -> str:
    return os.environ.get("ADMIN_INITIAL_PASSWORD", "").strip()


def _secret() -> bytes:
    """Signier-Secret: SESSION_SECRET aus .env, sonst vom Passwort abgeleitet.

    Die Ableitung hält Sessions über Container-Neustarts gültig, ohne einen
    zweiten Pflicht-Konfigwert zu erzwingen. Sie läuft über PBKDF2 statt über
    ein einzelnes SHA-256: aus einem geleakten Token (Log, Screenshot,
    Browser-Sync) wäre das Admin-Passwort sonst offline in Sekunden zu raten.
    Besser trotzdem SESSION_SECRET setzen — dann hängt gar nichts am Passwort.
    """
    global _abgeleitet
    explicit = os.environ.get("SESSION_SECRET", "").strip()
    if explicit:
        return explicit.encode()
    pw = _password()
    # Ableitung cachen: _secret() läuft bei JEDEM Request (check_token), und
    # 200k PBKDF2-Runden pro Request wären eine Selbst-DoS.
    if _abgeleitet is None or _abgeleitet[0] != pw:
        _abgeleitet = (pw, hashlib.pbkdf2_hmac("sha256", pw.encode(), b"dash-session", 200_000))
    return _abgeleitet[1]


def enabled() -> bool:
    return bool(_password())


def verify_password(candidate: str) -> bool:
    # Als BYTES vergleichen (Review P1-8): compare_digest wirft bei Nicht-ASCII-
    # Strings TypeError — ein Umlaut im Passwort (oder im Login-Versuch) machte
    # aus dem Login sonst dauerhaft einen 500er.
    return enabled() and hmac.compare_digest(
        candidate.encode("utf-8"), _password().encode("utf-8")
    )


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
