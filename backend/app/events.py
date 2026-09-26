"""Live-Events (F4) + Push-Auslöser (F10): ein Wächter über den Mailboxen.

Ersetzt das 5–8-s-Polling des Frontends als primären Weg: ändert sich unter
/workspace/mailboxes etwas, geht sofort ein Event an alle /api/events-Streams
(SSE) — Agenten-Panel und Rückfragen-Banner laden dann direkt nach. Das
bestehende Polling bleibt als Fallback bestehen (SSE weg → spätestens nach
einem Poll-Intervall stimmt die Anzeige wieder).

Derselbe Wächter ist der Auslöser für Web-Push (app/push.py): nach jeder
Änderung wird ein kleiner Schnappschuss gebaut (offene Rückfragen an den
Menschen + Responses in der Orchestrator-Inbox) und gegen den vorigen
verglichen — nur NEUE Einträge lösen eine Benachrichtigung aus, der Bestand
beim Start nie (gleiche Idee wie prevIdsRef im QuestionsBanner).

Dateisystem-Beobachtung: watchfiles (inotify) wenn vorhanden — steht in den
requirements, fehlt aber z. B. auf dem Host; dann fällt der Wächter auf einen
mtime-Scan alle 2 s zurück. Beide Wege liefern „diese Agenten sind betroffen".
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator

from app import ereignisse, push
from app.mailbox import ORCHESTRATOR

MAILBOXES = Path(os.environ.get("WORKSPACE_DIR", "/workspace")) / "mailboxes"

# Wie lange nach der ersten Änderung gesammelt wird, bevor EIN Event rausgeht:
# ein Task-Abschluss fasst mehrere Dateien an (Outbox-Response, Inbox-Zustellung,
# Abräumen) — ohne Sammelfenster käme dreimal dasselbe Event.
SAMMEL_MS = int(os.environ.get("EVENTS_SAMMEL_MS", "300"))
FALLBACK_SCAN_S = float(os.environ.get("EVENTS_FALLBACK_SCAN_S", "2"))


class Broadcaster:
    """Verteilt Events an alle offenen /api/events-Streams (asyncio-Queues)."""

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        # Begrenzte Queue: ein toter Client, dessen Stream nicht mehr liest,
        # darf keinen unbegrenzten Speicher ansammeln — Überlauf verwirft.
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Client hängt — das Polling-Fallback fängt ihn auf


broadcaster = Broadcaster()
_task: asyncio.Task | None = None


# --- Schnappschuss + Diff (rein, stdlib — getestet in tests/test_events_push) --

def _lese_inbox(agent_dir: Path) -> list[dict[str, Any]]:
    out = []
    inbox = agent_dir / "inbox"
    if inbox.is_dir():
        for p in sorted(inbox.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue  # halb geschrieben/kaputt — nächster Durchlauf
    return out


def lies_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    """Was den Menschen am Dashboard interessiert, als {id: info}.

    fragen:    offene Rückfragen AN DEN MENSCHEN (Orchestrator-Inbox,
               kind=question + needs_confirm) — dieselbe Definition wie
               `fuer_mensch` in /api/questions.
    antworten: Responses in der Orchestrator-Inbox (= ein Task ist fertig
               oder gescheitert und das Ergebnis liegt zur Abholung bereit).
    nachrichten: Hinweise (kind=message) in der Orchestrator-Inbox — die
               schrieb bisher niemand mit, obwohl sie an den Menschen gehen
               (Issue #33). Nachrichten ZWISCHEN Agenten bleiben bewusst
               draußen: die gehen den Menschen am Handy nichts an, und im
               Dashboard zeigt sie das Agenten-Panel live an.
    """
    fragen: dict[str, Any] = {}
    antworten: dict[str, Any] = {}
    nachrichten: dict[str, Any] = {}
    orch = root / ORCHESTRATOR
    for env in _lese_inbox(orch):
        env_id = env.get("id")
        if not env_id:
            continue
        kind = env.get("kind")
        if kind == "question" and env.get("status") == "needs_confirm":
            fragen[env_id] = {
                "sender": env.get("sender") or "?",
                "text": str(env.get("text") or ""),
                # Vorgegebene Antworten (Issue #30) — werden zu Knöpfen in der
                # Push-Benachrichtigung. Fehlen sie, bleibt nur "Öffnen".
                "options": env.get("options") or [],
            }
        elif kind == "response":
            antworten[env_id] = {
                "sender": env.get("sender") or "?",
                "status": env.get("status") or "done",
                # `hinweis` (Issue #38) schlägt den Ergebnistext: bei einem
                # Timeout-Abbruch zählt in der Push-Zeile der Grund, nicht
                # der Anfang des Fortschrittsprotokolls.
                "text": str(env.get("hinweis") or env.get("text") or ""),
            }
        elif kind == "message":
            nachrichten[env_id] = {
                "sender": env.get("sender") or "?",
                "text": str(env.get("text") or ""),
            }
    return {"fragen": fragen, "antworten": antworten, "nachrichten": nachrichten}


def neue_meldungen(
    alt: dict[str, dict[str, Any]], neu: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    """Nur NEUE Einträge werden zur Benachrichtigung — nie der Bestand.

    tag = Envelope-ID: erreicht dieselbe Meldung ein Gerät doppelt (Wächter
    neu gestartet), ersetzt die Notification sich selbst statt sich zu stapeln.
    """
    meldungen: list[dict[str, Any]] = []
    offen = len(neu["fragen"])
    for qid, q in neu["fragen"].items():
        if qid not in alt["fragen"]:
            meldungen.append(
                {
                    "titel": f"Rückfrage von {q['sender']}",
                    "text": q["text"][:180],
                    "tag": qid,
                    # Alles, was der Service Worker zum Beantworten direkt aus
                    # der Meldung heraus braucht (Issue #30).
                    "art": "frage",
                    "agent": ORCHESTRATOR,
                    "qid": qid,
                    "optionen": q.get("options") or [],
                    "url": f"/?tab=chat&frage={qid}",
                    "offen": offen,
                }
            )
    for mid, m in neu.get("nachrichten", {}).items():
        if mid not in alt.get("nachrichten", {}):
            meldungen.append(
                {
                    "titel": f"Nachricht von {m['sender']}",
                    "text": m["text"][:180],
                    "tag": mid,
                    "art": "nachricht",
                    "url": "/?tab=agenten",
                    "offen": offen,
                }
            )
    for rid, r in neu["antworten"].items():
        if rid not in alt["antworten"]:
            wie = "fehlgeschlagen" if r["status"] == "error" else "fertig"
            meldungen.append(
                {
                    "titel": f"Task {wie}: {r['sender']}",
                    "text": r["text"][:180],
                    "tag": rid,
                    "art": "antwort",
                    "url": "/?tab=agenten",
                    "offen": offen,
                }
            )
    return meldungen


# --- Dateisystem beobachten -------------------------------------------------

# --- Push-Bündler für das Ereignis-Log (Beobachtbarkeit, 26.09.2026) --------
# Verweigerungen, Timeouts, Kontext-Neustarts, Fehlserien, Watcher-Abrisse
# sollen aufs Handy — aber eine Verweigerungs-Serie darf nicht zehnmal
# vibrieren: höchstens EINE Meldung je Agent alle 10 Minuten, weitere
# Ereignisse werden gezählt und mit der nächsten Meldung genannt. Fehlserie
# und Watcher-Abriss durchbrechen die Sperre immer (dann steht die Automatik).
# Gelesen wird die JSONL-Datei ab dem letzten Offset — so kommen auch die
# Ereignisse aus dem MCP-Prozess an (kein In-Process-Hook möglich).
PUSH_SPERRE_S = float(os.environ.get("EREIGNIS_PUSH_SPERRE_S", "600"))
_RANG = {"fehler": 2, "warnung": 1, "info": 0}


class EreignisPush:
    def __init__(self, sperre_s: float = PUSH_SPERRE_S) -> None:
        self.sperre_s = sperre_s
        self.offsets: dict[str, int] = {}
        self.zuletzt: dict[str, float] = {}
        self.aufgelaufen: dict[str, int] = {}

    def baseline(self, root: Path) -> None:
        """Bestand beim Start NICHT melden — nur, was danach dazukommt."""
        try:
            for d in root.iterdir():
                p = d / ereignisse.DATEI
                if d.is_dir() and p.exists():
                    self.offsets[d.name] = p.stat().st_size
        except OSError:
            pass

    def entscheide(self, agent: str, probleme: list[dict[str, Any]],
                   jetzt: float) -> dict[str, Any] | None:
        """Rein: aus neuen Problem-Ereignissen eines Agenten eine Meldung machen
        — oder None (Sperre aktiv → nur zählen)."""
        if not probleme:
            return None
        stets = any(e.get("art") in ereignisse.STETS_PUSHEN for e in probleme)
        letzte = self.zuletzt.get(agent)
        if not stets and letzte is not None and jetzt - letzte < self.sperre_s:
            self.aufgelaufen[agent] = self.aufgelaufen.get(agent, 0) + len(probleme)
            return None
        schwerstes = max(probleme, key=lambda e: (_RANG.get(e.get("schwere"), 0),
                                                  str(e.get("zeit") or "")))
        weitere = self.aufgelaufen.get(agent, 0) + len(probleme) - 1
        self.aufgelaufen[agent] = 0
        self.zuletzt[agent] = jetzt
        text = str(schwerstes.get("text") or schwerstes.get("art"))
        if weitere:
            text += f" · +{weitere} weitere Ereignisse"
        return {
            "titel": f"{'⛔' if schwerstes.get('schwere') == 'fehler' else '⚠'} {agent}",
            "text": text, "tag": f"ereignis-{agent}", "url": "/",
            "art": "ereignis", "agent": agent,
        }

    def verarbeite(self, root: Path, agents: set[str], jetzt: float | None = None) -> list[dict[str, Any]]:
        meldungen = []
        jetzt = time.time() if jetzt is None else jetzt
        for agent in sorted(agents):
            try:
                neue, offset = ereignisse.lies_ab(root, agent, self.offsets.get(agent, 0))
            except ValueError:
                continue
            self.offsets[agent] = offset
            probleme = [e for e in neue if e.get("schwere") in ("warnung", "fehler")]
            m = self.entscheide(agent, probleme, jetzt)
            if m:
                meldungen.append(m)
        return meldungen


_ereignis_push = EreignisPush()


def _agent_aus_pfad(pfad: str) -> str | None:
    try:
        rel = Path(pfad).relative_to(MAILBOXES)
    except ValueError:
        return None
    return rel.parts[0] if rel.parts else None


async def _aenderungen() -> AsyncIterator[set[str]]:
    """Liefert je Änderungsschub die betroffenen Agenten-Namen."""
    try:
        from watchfiles import awatch
    except ImportError:
        # Fallback ohne inotify: mtime der Mailbox-Ordner alle paar Sekunden.
        # Verzeichnis-mtimes ändern sich bei jedem Anlegen/Ersetzen/Löschen
        # darin — Dateiinhalte selbst muss der Scan nicht anfassen.
        stand: dict[str, float] = {}
        while True:
            neu: dict[str, float] = {}
            if MAILBOXES.is_dir():
                for agent_dir in MAILBOXES.iterdir():
                    if not agent_dir.is_dir():
                        continue
                    zeiten = []
                    for unter in ("inbox", "inbox/.processing", "outbox", ereignisse.DATEI):
                        try:
                            zeiten.append((agent_dir / unter).stat().st_mtime)
                        except OSError:
                            pass
                    if zeiten:
                        neu[agent_dir.name] = max(zeiten)
            betroffen = {a for a, t in neu.items() if stand.get(a) != t}
            betroffen |= set(stand) - set(neu)  # gelöschte Mailbox
            if betroffen and stand:
                yield betroffen
            stand = neu
            await asyncio.sleep(FALLBACK_SCAN_S)
        return
    MAILBOXES.mkdir(parents=True, exist_ok=True)
    async for changes in awatch(MAILBOXES, step=SAMMEL_MS):
        agents = {
            a
            for _, pfad in changes
            if (a := _agent_aus_pfad(pfad)) is not None
        }
        if agents:
            yield agents


# Baseline über Wächter-Neustarts hinweg (Review P2): stirbt die Schleife
# an einem Dateisystem-Schluckauf, darf der Neustart NICHT alles, was in
# der Lücke einging, als Bestand verbuchen — die needs_confirm-Rückfrage
# aus genau diesem Moment bekäme sonst nie einen Push.
_letzter_stand: dict | None = None


async def _watch_schleife() -> None:
    global _letzter_stand
    # Baseline OHNE Meldung: was beim ERSTEN Start schon da liegt, hat sein
    # Push-Fenster gehabt — sonst klingelt jeder Container-Neustart alle
    # Handys. Nur der allererste Lauf setzt sie; Neustarts erben den Stand.
    if _letzter_stand is None:
        _letzter_stand = await asyncio.to_thread(lies_snapshot, MAILBOXES)
        _ereignis_push.baseline(MAILBOXES)
    alt = _letzter_stand
    async for betroffene in _aenderungen():
        broadcaster.publish({"type": "mailbox", "agents": sorted(betroffene)})
        try:
            neu = await asyncio.to_thread(lies_snapshot, MAILBOXES)
            meldungen = neue_meldungen(alt, neu)
            meldungen += await asyncio.to_thread(_ereignis_push.verarbeite, MAILBOXES, set(betroffene))
            for m in meldungen:
                await push.sende_an_alle(
                    m["titel"],
                    m["text"],
                    tag=m["tag"],
                    url=m.get("url", "/"),
                    extra={
                        k: m[k]
                        for k in ("art", "agent", "qid", "optionen", "offen")
                        if k in m
                    },
                )
            alt = neu
            _letzter_stand = neu
        except Exception as exc:  # noqa: BLE001 — Push darf den Wächter nie killen
            print(f"[events] Push-Auslöser fehlgeschlagen: {exc}", flush=True)


async def _watch_mit_neustart() -> None:
    """Wächter am Leben halten — ein Dateisystem-Schluckauf beendet ihn nicht."""
    while True:
        try:
            await _watch_schleife()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[events] Wächter neu gestartet nach Fehler: {exc}", flush=True)
        await asyncio.sleep(5)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_watch_mit_neustart())


def stop() -> None:
    if _task is not None:
        _task.cancel()
