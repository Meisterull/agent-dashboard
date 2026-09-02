"""llm.py, Ollama-Pfad (Review-Struktur-H: komplett ungetestet).

Standardlib pur: der HTTP-Teil läuft gegen einen lokalen http.server-Thread,
der eine Ollama-/api/chat-Antwort nachstellt. Damit sind abgedeckt:
Nachrichten-/Tool-Übersetzung, das Parsen von tool_calls (arguments als Dict
UND als JSON-String UND kaputt), die rundenübergreifende Eindeutigkeit der
Call-IDs (Review N: "call_0" kollidierte — Provider-Wechsel-Falle),
repariere_history und kappe_tool_ergebnis.

    cd backend && python -m tests.test_llm_ollama
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app import llm  # noqa: E402

ANTWORT = {
    "message": {
        "content": "mache ich",
        "tool_calls": [
            {"function": {"name": "send_task", "arguments": {"to": "a"}}},
            {"function": {"name": "ask", "arguments": '{"frage": "ok?"}'}},
            {"function": {"name": "kaputt", "arguments": "kein json {"}},
        ],
    }
}


class _Handler(BaseHTTPRequestHandler):
    letzter_body: dict = {}

    def do_POST(self):  # noqa: N802 — Vorgabe der Basisklasse
        laenge = int(self.headers.get("Content-Length", 0))
        _Handler.letzter_body = json.loads(self.rfile.read(laenge))
        daten = json.dumps(ANTWORT).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(daten)))
        self.end_headers()
        self.wfile.write(daten)

    def log_message(self, *args):  # still
        pass


class OllamaCallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.cfg = {"provider": "ollama", "model": "m",
                   "base_url": f"http://127.0.0.1:{cls.server.server_port}"}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_parst_tool_calls_und_ids_sind_eindeutig(self):
        r1 = llm._ollama_call_sync(self.cfg, "sys", [{"role": "user", "content": "hi"}], [])
        r2 = llm._ollama_call_sync(self.cfg, "sys", [{"role": "user", "content": "hi"}], [])
        self.assertEqual(r1["text"], "mache ich")
        self.assertEqual(len(r1["tool_calls"]), 3)
        self.assertEqual(r1["tool_calls"][0]["input"], {"to": "a"})
        self.assertEqual(r1["tool_calls"][1]["input"], {"frage": "ok?"})   # String-JSON
        self.assertEqual(r1["tool_calls"][2]["input"], {})                 # kaputt → leer
        ids = [tc["id"] for tc in r1["tool_calls"] + r2["tool_calls"]]
        self.assertEqual(len(ids), len(set(ids)),
                         "Call-IDs müssen rundenübergreifend eindeutig sein (Review N)")

    def test_uebersetzung_der_history(self):
        llm._ollama_call_sync(self.cfg, "SYSTEM", [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "name": "inbox", "input": {}}]},
            {"role": "tool", "tool_call_id": "c1", "name": "inbox", "content": "[]"},
        ], [{"name": "inbox", "description": "d", "input_schema": {"type": "object"}}])
        msgs = _Handler.letzter_body["messages"]
        self.assertEqual(msgs[0], {"role": "system", "content": "SYSTEM"})
        self.assertEqual(msgs[2]["tool_calls"][0]["function"]["name"], "inbox")
        self.assertEqual(msgs[3], {"role": "tool", "tool_name": "inbox", "content": "[]"})
        werkzeug = _Handler.letzter_body["tools"][0]
        self.assertEqual(werkzeug["type"], "function")
        self.assertEqual(werkzeug["function"]["name"], "inbox")


class HistoryTests(unittest.TestCase):
    def test_repariere_traegt_offene_calls_nach(self):
        h = [
            {"role": "assistant", "tool_calls": [{"id": "a", "name": "x"}]},
            {"role": "tool", "tool_call_id": "a", "name": "x", "content": "ok"},
            {"role": "assistant", "tool_calls": [{"id": "b", "name": "y"}]},
        ]
        out = llm.repariere_history(h)
        self.assertEqual(len(out), 4)
        self.assertEqual(out[-1]["role"], "tool")
        self.assertEqual(out[-1]["tool_call_id"], "b")
        self.assertIn("Abgebrochen", out[-1]["content"])
        # nichts offen → unverändert
        self.assertEqual(len(llm.repariere_history(out)), 4)

    def test_kappen_ist_sichtbar(self):
        self.assertEqual(llm.kappe_tool_ergebnis("kurz"), "kurz")
        lang = "x" * (llm.MAX_TOOL_RESULT_CHARS + 50)
        out = llm.kappe_tool_ergebnis(lang)
        self.assertLess(len(out), len(lang))
        self.assertIn("gekürzt", out)
        self.assertIn(str(len(lang)), out)


if __name__ == "__main__":
    unittest.main()
