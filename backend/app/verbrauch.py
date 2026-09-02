"""Verbrauchszähler je Agent (Dashboard-Paket, Stufe 3).

Der Watcher liest `usage` und `total_cost_usd` aus dem result-Event des
Claude-Laufs und liefert sie mit dem Ergebnis ab; write_response legt sie als
`verbrauch` in die Outbox-Response. Aggregiert wird ON-READ über genau diese
Outbox-Dateien — bewusst KEINE eigene Persistenz: Der Datei-Transport-Watcher
schreibt seine Responses remote am Server-Code vorbei, eine mitgeschriebene
Historie liefe dort ins Leere. Die Outbox rotiert nach MAILBOX_ARCHIV_TAGE
(Default 30 Tage) — mehr als die angezeigten 7 Tage braucht niemand.

EHRLICHE MESSUNG, KEIN OFFIZIELLES LIMIT-%: Die Abo-Limits von Claude Code
(5-h-Fenster/Woche) sind headless nicht abfragbar (Stand 09/2026). Der Zähler
misst, was DIESE Tasks verbraucht haben; die optionale Schwelle
(`verbrauch_schwelle_5h` in den Settings, Tokens je Agent im rollierenden
5-h-Fenster) ist eine selbst gewählte Bremse: darüber färbt sich der Zähler
im Panel und der Planer pausiert GEPLANTE Tasks dieses Agenten (der
▶-Sofort-Knopf und normale Chat-Delegation laufen weiter — bewusst: die
Schwelle ist eine Automatik-Bremse, kein Verbot).
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

MAILBOX_ROOT = Path(os.environ.get("WORKSPACE_DIR", "/workspace")) / "mailboxes"

FENSTER_5H = 5 * 3600
TAGE_ANZEIGE = 7
TOKEN_FELDER = ("input_tokens", "output_tokens",
                "cache_creation_input_tokens", "cache_read_input_tokens")


def _leer() -> dict[str, Any]:
    return {"tasks": 0, "tokens": 0, "kosten": 0.0}


def _addiere(summe: dict[str, Any], verbrauch: dict[str, Any] | None) -> None:
    summe["tasks"] += 1
    if not isinstance(verbrauch, dict):
        return  # Response ohne Messung (dry-run, alter Watcher): zählt als Task
    summe["tokens"] += sum(
        int(verbrauch[f]) for f in TOKEN_FELDER
        # isfinite (Review N): float("inf") aus einer manipulierten
        # Response ließe int() mit OverflowError das ganze Panel killen.
        if isinstance(verbrauch.get(f), (int, float)) and math.isfinite(verbrauch[f])
    )
    kosten = verbrauch.get("total_cost_usd")
    if isinstance(kosten, (int, float)) and math.isfinite(kosten):
        summe["kosten"] += float(kosten)


def aggregiere(responses: list[dict[str, Any]],
               jetzt: datetime | None = None,
               schwelle: int = 0) -> dict[str, Any]:
    """Heute, rollierendes 5-h-Fenster und die letzten 7 Tage aus einer Liste
    von Outbox-Responses — pure Funktion, damit /api/agents/{name}/tasks sie
    auf seiner OHNEHIN gelesenen Outbox aufrufen kann (kein Doppel-I/O)."""
    jetzt = jetzt or datetime.now().astimezone()
    fenster_ab = jetzt - timedelta(seconds=FENSTER_5H)
    heute = _leer()
    fenster = _leer()
    tage: dict[str, dict[str, Any]] = {}
    for zurueck in range(TAGE_ANZEIGE):
        tage[(jetzt - timedelta(days=zurueck)).date().isoformat()] = _leer()
    for r in responses:
        if not isinstance(r, dict):
            continue
        try:
            wann = datetime.fromisoformat(str(r.get("responded_at")))
        except (TypeError, ValueError):
            continue
        # IMMER in die Server-Zone (Review P2): responded_at schreibt der
        # Watcher auf dem Agenten-PC — dessen Kalendertag ist nicht unserer.
        wann = wann.astimezone()
        tag = wann.date().isoformat()
        if tag in tage:
            _addiere(tage[tag], r.get("verbrauch"))
        if wann.date() == jetzt.date():
            _addiere(heute, r.get("verbrauch"))
        if wann >= fenster_ab:
            _addiere(fenster, r.get("verbrauch"))
    return {
        "heute": heute,
        "fenster5h": fenster,
        "tage": [{"datum": d, **tage[d]} for d in sorted(tage, reverse=True)],
        "schwelle": int(schwelle or 0),
        "ueber_schwelle": bool(schwelle) and fenster["tokens"] >= int(schwelle),
    }


def lade(agent: str, schwelle: int = 0) -> dict[str, Any]:
    """Aggregat direkt aus der Outbox eines Agenten (für den Planer)."""
    import time as _zeit

    # mtime-Vorfilter (Review P2): fürs 5-h-Fenster reicht der letzte Tag —
    # 30 Tage Task-Ergebnisse zu parsen blockierte den Planer-Tick spürbar.
    grenze = _zeit.time() - 2 * 86400
    responses: list[dict[str, Any]] = []
    outbox = MAILBOX_ROOT / agent / "outbox"
    if outbox.is_dir():
        for p in outbox.glob("*-response.json"):
            try:
                if p.stat().st_mtime < grenze:
                    continue
                responses.append(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                continue
    return aggregiere(responses, schwelle=schwelle)


def ist_ueber_schwelle(agent: str, schwelle: int) -> bool:
    if not schwelle:
        return False
    return lade(agent, schwelle)["ueber_schwelle"]
