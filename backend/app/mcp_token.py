"""Token-Anmeldung für MCP-Kanäle über HTTPS (Issue #32).

WOFÜR: Agenten, zu denen das Dashboard keinen SSH-Tunnel aufbauen kann — ein
Windows-Notebook mit Claude Desktop, ein Gerät hinter NAT, eines das mal im LAN
und mal im VPN hängt. Sie erreichen ihre Mailbox über denselben HTTPS-Zugang,
über den auch der Browser kommt, und weisen sich mit einem Token aus.

DAS SICHERHEITSMODELL BLEIBT DASSELBE wie bei den SSH-Kanälen (Issue #13): Die
Identität kommt aus dem KANAL, nicht aus einem Parameter. Ein Token gehört zu
genau einem Agenten und öffnet nur dessen gebundenen Port; wer Token X hat,
kann nicht als Y auftreten, weil er Y's Port gar nicht erreicht.

Der Token steht NIE in agents.yaml, sondern in einer Datei daneben — wie schon
die SSH-Schlüssel (`key_file`). So bleibt die Konfiguration teilbar.
"""
from __future__ import annotations

import hmac
import time
from pathlib import Path
from typing import Any

from app.config import load_agents_full

# Kürzere Token lehnen wir ab: 32 Zeichen sind die Untergrenze, unterhalb derer
# Raten aussichtsreich wird. Erzeugt werden sie mit scripts/make_agent_token.sh.
MIN_LAENGE = 32

# Fehlversuche je Agent — gegen Durchprobieren, unabhängig von nginx.
_FEHLVERSUCHE: dict[str, list[float]] = {}
_SPERRE_AB = 10          # Versuche
_ZEITFENSTER = 60.0      # Sekunden


class TokenFehler(Exception):
    """Anmeldung abgelehnt — mit einem Grund fürs Log, nicht für den Client."""


def _token_datei(agent: dict[str, Any]) -> Path | None:
    conn = agent.get("connection") or {}
    if conn.get("type") != "token":
        return None
    pfad = conn.get("token_file")
    return Path(str(pfad)) if pfad else None


def erwarteter_token(name: str) -> str | None:
    """Hinterlegter Token eines Agenten — None, wenn keiner konfiguriert ist."""
    for agent in load_agents_full():
        if agent.get("name") != name:
            continue
        datei = _token_datei(agent)
        if datei is None:
            return None
        try:
            return datei.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    return None


def _gesperrt(name: str) -> bool:
    jetzt = time.monotonic()
    versuche = [t for t in _FEHLVERSUCHE.get(name, []) if jetzt - t < _ZEITFENSTER]
    _FEHLVERSUCHE[name] = versuche
    return len(versuche) >= _SPERRE_AB


def _fehlversuch(name: str) -> None:
    # Deckel (Review P3): der Name kommt aus einem unauthentifizierten
    # URL-Segment — ohne Grenze wüchse das Dict unbegrenzt.
    if name not in _FEHLVERSUCHE and len(_FEHLVERSUCHE) >= 512:
        _FEHLVERSUCHE.clear()
    _FEHLVERSUCHE.setdefault(name, []).append(time.monotonic())


def pruefe(name: str, kopfzeile: str | None) -> None:
    """Prüft `Authorization: Bearer …` gegen den Token des Agenten.

    Wirft TokenFehler. Der Vergleich läuft in konstanter Zeit — sonst verrät
    die Antwortzeit, wie viele Zeichen des Tokens schon stimmen.
    """
    if _gesperrt(name):
        raise TokenFehler(f"{name}: zu viele Fehlversuche, gesperrt")

    erwartet = erwarteter_token(name)
    if not erwartet:
        _fehlversuch(name)
        raise TokenFehler(f"{name}: kein Token konfiguriert")
    if len(erwartet) < MIN_LAENGE:
        raise TokenFehler(
            f"{name}: hinterlegter Token ist zu kurz "
            f"({len(erwartet)} < {MIN_LAENGE} Zeichen) — mit "
            "scripts/make_agent_token.sh neu erzeugen"
        )

    vorgelegt = ""
    if kopfzeile and kopfzeile.lower().startswith("bearer "):
        vorgelegt = kopfzeile[7:].strip()
    # Bytes-Vergleich (Review P1-8): compare_digest wirft bei Nicht-ASCII
    # TypeError — ein Client-gesteuerter 500er statt sauberem 401.
    if not vorgelegt or not hmac.compare_digest(
        vorgelegt.encode("utf-8"), erwartet.encode("utf-8")
    ):
        _fehlversuch(name)
        raise TokenFehler(f"{name}: Token stimmt nicht")
