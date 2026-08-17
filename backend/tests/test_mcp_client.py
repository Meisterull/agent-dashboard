"""Tests für den MCP-Client des Watchers (scripts/agent_watcher.McpClient).

    cd backend && python -m tests.test_mcp_client

Der Client spricht Streamable-HTTP mit reiner Standardlib; getestet wird er
gegen einen Stub-http.server (T7): Session-Id, Protokoll-Aushandlung,
JSON- und SSE-Antworten, structuredContent-Auspacken, isError und JSON-RPC-
Fehler. Dazu die Ablieferungs-Retry-Schleife liefere_ergebnis (M8).
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import agent_watcher as aw  # noqa: E402

SESSION_ID = "sess-4711"


class _Stub(BaseHTTPRequestHandler):
    """Minimaler MCP-Server: antwortet je nach Tool-Name unterschiedlich."""

    protokoll = "2025-06-18"
    aufrufe: list[dict] = []
    kopfzeilen: list[dict] = []
    ausfaelle = {"anzahl": 0}  # so viele erste tools/call-Aufrufe scheitern lassen

    def log_message(self, *_args) -> None:  # keine Testrauschen-Ausgabe
        pass

    def _sende(self, body: str, ctype: str, session: bool = False) -> None:
        daten = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(daten)))
        if session:
            self.send_header("Mcp-Session-Id", SESSION_ID)
        self.end_headers()
        self.wfile.write(daten)

    def do_POST(self) -> None:  # noqa: N802 — http.server-API
        laenge = int(self.headers.get("Content-Length") or 0)
        msg = json.loads(self.rfile.read(laenge) or b"{}")
        _Stub.aufrufe.append(msg)
        _Stub.kopfzeilen.append({k.lower(): v for k, v in self.headers.items()})
        method = msg.get("method")

        if method == "initialize":
            self._sende(json.dumps({
                "jsonrpc": "2.0", "id": msg["id"],
                "result": {"protocolVersion": self.protokoll, "capabilities": {}},
            }), "application/json", session=True)
            return
        if msg.get("id") is None:  # Notification (notifications/initialized)
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        tool = (msg.get("params") or {}).get("name")
        if _Stub.ausfaelle["anzahl"] > 0:
            _Stub.ausfaelle["anzahl"] -= 1
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if tool == "inbox":  # SSE-Antwort mit Liste im structuredContent
            nutzlast = {
                "jsonrpc": "2.0", "id": msg["id"],
                "result": {"structuredContent": {"result": [
                    {"id": "t1", "kind": "task"}, {"id": "t2", "kind": "message"}]}},
            }
            body = ("event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": 999,
                                                           "method": "notifications/ping"})
                    + "\n\nevent: message\ndata: " + json.dumps(nutzlast) + "\n\n")
            self._sende(body, "text/event-stream; charset=utf-8")
            return
        if tool == "claim_task":  # klassischer content-Block mit JSON-Text
            self._sende(json.dumps({
                "jsonrpc": "2.0", "id": msg["id"],
                "result": {"content": [{"type": "text", "text": json.dumps(
                    {"claimed": "t1", "instruction": "mach was", "project": "repo"})}]},
            }), "application/json")
            return
        if tool == "complete_task":
            self._sende(json.dumps({
                "jsonrpc": "2.0", "id": msg["id"],
                "result": {"structuredContent": {"ok": True, "parked": False}},
            }), "application/json")
            return
        if tool == "kaputt":  # Tool meldet Fehler
            self._sende(json.dumps({
                "jsonrpc": "2.0", "id": msg["id"],
                "result": {"isError": True,
                           "content": [{"type": "text", "text": "so nicht"}]},
            }), "application/json")
            return
        # unbekanntes Tool -> JSON-RPC-Fehler
        self._sende(json.dumps({
            "jsonrpc": "2.0", "id": msg["id"],
            "error": {"code": -32602, "message": "unbekanntes Tool"},
        }), "application/json")


class TestMcpClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/mcp"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        _Stub.aufrufe.clear()
        _Stub.kopfzeilen.clear()
        _Stub.ausfaelle["anzahl"] = 0
        aw.HART.clear()

    def tearDown(self) -> None:
        aw.HART.clear()

    def _client(self) -> aw.McpClient:
        c = aw.McpClient(self.url, timeout=10)
        c.connect()
        return c

    def test_connect_uebernimmt_session_und_protokoll(self):
        c = self._client()
        self.assertEqual(c.session_id, SESSION_ID)
        self.assertEqual(c.protocol, _Stub.protokoll)
        # initialize + notifications/initialized
        self.assertEqual([m.get("method") for m in _Stub.aufrufe],
                         ["initialize", "notifications/initialized"])
        # Folgeaufruf trägt die Session-Id
        c.call("claim_task", {"task_id": "t1"})
        self.assertEqual(_Stub.kopfzeilen[-1].get("mcp-session-id"), SESSION_ID)
        self.assertEqual(_Stub.kopfzeilen[-1].get("mcp-protocol-version"),
                         _Stub.protokoll)

    def test_sse_antwort_wird_geparst(self):
        envelopes = self._client().call("inbox", {"kind": "task"})
        self.assertEqual([e["id"] for e in envelopes], ["t1", "t2"])

    def test_content_text_wird_json_geparst(self):
        claimed = self._client().call("claim_task", {"task_id": "t1"})
        self.assertEqual(claimed["instruction"], "mach was")
        self.assertEqual(claimed["project"], "repo")

    def test_structured_content_bleibt_dict(self):
        fertig = self._client().call("complete_task", {"task_id": "t1"})
        self.assertEqual(fertig, {"ok": True, "parked": False})

    def test_tool_fehler_wird_ausnahme(self):
        with self.assertRaises(aw.McpFehler) as ctx:
            self._client().call("kaputt", {})
        self.assertIn("so nicht", str(ctx.exception))

    def test_jsonrpc_fehler_wird_ausnahme(self):
        with self.assertRaises(aw.McpFehler):
            self._client().call("gibtsnicht", {})


class TestAblieferung(unittest.TestCase):
    """M8: das Ergebnis eines Laufs darf nicht an einem Netz-Blip verloren gehen."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/mcp"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        _Stub.aufrufe.clear()
        _Stub.ausfaelle["anzahl"] = 0
        aw.HART.clear()
        aw.STOP.clear()
        self.pause = aw.ABLIEFER_PAUSE
        aw.ABLIEFER_PAUSE = 0.05

    def tearDown(self) -> None:
        aw.ABLIEFER_PAUSE = self.pause
        aw.HART.clear()
        aw.STOP.clear()

    def test_erfolg_beim_ersten_versuch(self):
        antwort, client, fehler = aw.liefere_ergebnis(None, self.url, "t1", "fertig",
                                                      "done", "")
        self.assertIsNone(fehler)
        self.assertEqual(antwort, {"ok": True, "parked": False})
        self.assertIsNotNone(client)

    def test_retry_trotz_gesetztem_stopp(self):
        """Sanft-Stopp darf die Ablieferung NICHT abwürgen (30 min Arbeit!)."""
        aw.STOP.set()
        _Stub.ausfaelle["anzahl"] = 2
        antwort, _client, fehler = aw.liefere_ergebnis(None, self.url, "t1", "fertig",
                                                       "done", "")
        self.assertIsNone(fehler)
        self.assertEqual(antwort, {"ok": True, "parked": False})

    def test_aufgeben_meldet_fehler(self):
        _Stub.ausfaelle["anzahl"] = aw.ABLIEFER_VERSUCHE + 1
        antwort, client, fehler = aw.liefere_ergebnis(None, self.url, "t1", "x",
                                                      "done", "")
        self.assertIsNone(antwort)
        self.assertIsNone(client)
        self.assertIn("HTTPError", fehler or "")

    def test_notaus_bricht_retry_sofort_ab(self):
        aw.HART.set()
        _Stub.ausfaelle["anzahl"] = aw.ABLIEFER_VERSUCHE + 1
        vorher = len(_Stub.aufrufe)
        _antwort, _client, fehler = aw.liefere_ergebnis(None, self.url, "t1", "x",
                                                        "done", "")
        self.assertIsNotNone(fehler)
        # ein Versuch (initialize + notification + tools/call), kein zweiter
        self.assertLessEqual(len(_Stub.aufrufe) - vorher, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
