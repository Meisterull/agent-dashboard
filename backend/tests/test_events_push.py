"""Tests für app/events.py (Snapshot/Diff), app/push.py (Subscription-Store)
und den Nutzer-Abbruch llm.TurnAbbruch (F3/F4/F10) — reine Stdlib:

    cd backend && python -m tests.test_events_push

Abgedeckt:
  * lies_snapshot/neue_meldungen: Rückfrage an den Menschen wird gemeldet,
    Agent-↔-Agent-Frage nicht, Response = "Task fertig/fehlgeschlagen",
    Nachricht an den Menschen ebenfalls (#33), Bestand meldet sich nie
    doppelt (Baseline-Prinzip)
  * push: add/dedupe/remove der Subscriptions, ValueError ohne endpoint,
    sende_an_alle fällt ohne pywebpush bzw. gegen eine Fake-URL leise auf 0
    (Wächter darf am Versand nie sterben)
  * llm.TurnAbbruch wird NICHT als Tool-Fehler geschluckt, sondern reicht
    durch — und repariere_history stopft danach die dangling tool_calls
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Env VOR den App-Imports setzen: push.py liest DATA_CONFIG_DIR beim Import.
_TMP = tempfile.mkdtemp(prefix="events_push_test_")
os.environ["WORKSPACE_DIR"] = _TMP
os.environ["DATA_CONFIG_DIR"] = str(Path(_TMP) / "config")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import events, llm, push  # noqa: E402
from app.mailbox import ORCHESTRATOR, Mailbox  # noqa: E402


class TestSnapshotDiff(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="mb_"))

    def test_frage_fuer_mensch_wird_gemeldet(self) -> None:
        orch = Mailbox(self.root, ORCHESTRATOR)
        alt = events.lies_snapshot(self.root)  # Baseline: leer
        orch.post(
            {"kind": "question", "status": "needs_confirm",
             "sender": "deverp", "text": "Welche DB soll ich nehmen?"}
        )
        neu = events.lies_snapshot(self.root)
        meldungen = events.neue_meldungen(alt, neu)
        self.assertEqual(len(meldungen), 1)
        self.assertIn("deverp", meldungen[0]["titel"])
        self.assertEqual(meldungen[0]["text"], "Welche DB soll ich nehmen?")
        # Bestand meldet sich beim nächsten Diff NICHT erneut
        self.assertEqual(
            events.neue_meldungen(neu, events.lies_snapshot(self.root)), []
        )

    def test_frage_zwischen_agenten_ist_kein_mensch_ping(self) -> None:
        Mailbox(self.root, "erp").post(
            {"kind": "question", "status": "needs_confirm",
             "sender": "deverp", "text": "intern"}
        )
        self.assertEqual(events.lies_snapshot(self.root)["fragen"], {})

    def test_response_ist_task_fertig(self) -> None:
        orch = Mailbox(self.root, ORCHESTRATOR)
        alt = events.lies_snapshot(self.root)
        orch.post({"kind": "response", "sender": "erp", "status": "done", "text": "erledigt"})
        m = events.neue_meldungen(alt, events.lies_snapshot(self.root))
        self.assertEqual(len(m), 1)
        self.assertIn("fertig", m[0]["titel"])
        self.assertIn("erp", m[0]["titel"])

    def test_response_error_meldet_fehlgeschlagen(self) -> None:
        orch = Mailbox(self.root, ORCHESTRATOR)
        alt = events.lies_snapshot(self.root)
        orch.post({"kind": "response", "sender": "erp", "status": "error", "text": "kaputt"})
        m = events.neue_meldungen(alt, events.lies_snapshot(self.root))
        self.assertEqual(len(m), 1)
        self.assertIn("fehlgeschlagen", m[0]["titel"])


    def test_nachricht_an_den_menschen_wird_gemeldet(self) -> None:
        """#33: send_message an den Orchestrator ist eine Push-Meldung wert."""
        orch = Mailbox(self.root, ORCHESTRATOR)
        alt = events.lies_snapshot(self.root)
        orch.post({"kind": "message", "sender": "PMNB029", "text": "bin fertig"})
        m = events.neue_meldungen(alt, events.lies_snapshot(self.root))
        self.assertEqual(len(m), 1, m)
        self.assertIn("PMNB029", m[0]["titel"])
        self.assertEqual(m[0]["art"], "nachricht")
        self.assertEqual(m[0]["text"], "bin fertig")
        # Bestand meldet sich nicht erneut
        self.assertEqual(
            events.neue_meldungen(
                events.lies_snapshot(self.root), events.lies_snapshot(self.root)
            ),
            [],
        )

    def test_nachricht_zwischen_agenten_ist_kein_mensch_ping(self) -> None:
        """Agent → Agent klingelt NICHT am Handy (wie bei Rückfragen, #22)."""
        Mailbox(self.root, "erp").post(
            {"kind": "message", "sender": "deverp", "text": "intern"}
        )
        self.assertEqual(events.lies_snapshot(self.root)["nachrichten"], {})


class TestPushStore(unittest.TestCase):
    def test_add_dedupe_remove(self) -> None:
        n = push.add_subscription({"endpoint": "https://a/1", "keys": {"p256dh": "x", "auth": "y"}})
        self.assertEqual(n, 1)
        # gleiche Zustelladresse ersetzt, statt sich zu doppeln
        n = push.add_subscription({"endpoint": "https://a/1", "keys": {"p256dh": "x2", "auth": "y2"}})
        self.assertEqual(n, 1)
        push.add_subscription({"endpoint": "https://a/2", "keys": {}})
        self.assertEqual(push.anzahl_subscriptions(), 2)
        self.assertTrue(push.remove_subscription("https://a/1"))
        self.assertFalse(push.remove_subscription("https://a/1"))
        self.assertEqual(push.anzahl_subscriptions(), 1)

    def test_ohne_endpoint_abgelehnt(self) -> None:
        with self.assertRaises(ValueError):
            push.add_subscription({"keys": {}})

    def test_senden_faellt_leise_auf_null(self) -> None:
        # Host ohne pywebpush → Versand übersprungen; mit pywebpush scheitert
        # die Fake-URL → gezählt wird in beiden Fällen 0, geworfen wird nie
        # (der Mailbox-Wächter ruft das und darf daran nicht sterben).
        push.add_subscription(
            {"endpoint": "https://push.invalid/x", "keys": {"p256dh": "QUJD", "auth": "QUJD"}}
        )
        n = asyncio.run(push.sende_an_alle("Titel", "Text", tag="t"))
        self.assertEqual(n, 0)


class TestTurnAbbruch(unittest.TestCase):
    def test_abbruch_reicht_durch_und_history_ist_reparierbar(self) -> None:
        async def lauf() -> None:
            async def fake_complete(cfg, system, messages, tools):
                # Modell "will" ein Tool — der Abbruch muss VOR der Ausführung greifen
                return {"text": "", "tool_calls": [{"id": "c1", "name": "send_task", "input": {}}]}

            orig = llm._complete
            llm._complete = fake_complete
            try:
                messages = [{"role": "user", "content": "hi"}]

                async def call_tool(name, inp):
                    raise llm.TurnAbbruch("test")

                with self.assertRaises(llm.TurnAbbruch):
                    await llm.run_turn({"provider": "ollama"}, "sys", messages, [], call_tool)
                repariert = llm.repariere_history(messages)
                # der dangling tool_call hat jetzt ein tool-Result — die Session
                # bleibt für den nächsten Provider-Call gültig
                self.assertEqual(repariert[-1]["role"], "tool")
                self.assertEqual(repariert[-1]["tool_call_id"], "c1")
            finally:
                llm._complete = orig

        asyncio.run(lauf())


if __name__ == "__main__":
    unittest.main()
