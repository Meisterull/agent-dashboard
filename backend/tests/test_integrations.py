"""integrations.py (Review-Testlücke): Allowlists + echter HTTP-Roundtrip.

Die Sicherheits-Guards (unbekannte Integration, Methoden-Allowlist, kein
absoluter URL-Override) laufen VOR dem lazy httpx-Import und sind damit
Standardlib-pur testbar. Der Roundtrip selbst (Auth-Injektion, Body-Kappung)
läuft gegen einen lokalen http.server und wird übersprungen, wo httpx fehlt
(Host); im Container läuft er mit.

    cd backend && python -m tests.test_integrations
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app import integrations as ig  # noqa: E402

try:
    import httpx  # noqa: F401
    HTTPX = True
except ImportError:
    HTTPX = False


def _yaml_schreiben(port: int) -> Path:
    d = Path(tempfile.mkdtemp(prefix="integ-test-"))
    p = d / "integrations.yaml"
    p.write_text(
        "integrations:\n"
        "  dienst:\n"
        f"    base_url: http://127.0.0.1:{port}/api\n"
        "    auth_env: INTEG_TEST_TOKEN\n"
        "    auth_prefix: 'token '\n"
        "    allowed_methods: [GET]\n",
        encoding="utf-8",
    )
    return p


class _Handler(BaseHTTPRequestHandler):
    letzte_header: dict = {}

    def do_GET(self):  # noqa: N802
        _Handler.letzte_header = dict(self.headers)
        daten = json.dumps({"pfad": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(daten)))
        self.end_headers()
        self.wfile.write(daten)

    def log_message(self, *args):
        pass


class GuardTests(unittest.TestCase):
    """Laufen ohne httpx — genau die Reihenfolge im Code garantiert das."""

    def setUp(self) -> None:
        self._alt = ig.INTEGRATIONS_YAML
        ig.INTEGRATIONS_YAML = _yaml_schreiben(1)

    def tearDown(self) -> None:
        ig.INTEGRATIONS_YAML = self._alt

    def test_unbekannte_integration(self):
        with self.assertRaises(ig.IntegrationError):
            ig.call_integration("gibtsnicht")

    def test_methoden_allowlist(self):
        with self.assertRaises(ig.IntegrationError):
            ig.call_integration("dienst", method="POST")

    def test_kein_url_override(self):
        with self.assertRaises(ig.IntegrationError):
            ig.call_integration("dienst", path="http://boese.example/x")

    def test_listing_ohne_secrets(self):
        liste = ig.list_integrations()
        self.assertEqual(liste[0]["name"], "dienst")
        self.assertNotIn("INTEG_TEST_TOKEN", json.dumps(liste))


@unittest.skipUnless(HTTPX, "httpx nur im Container")
class RoundtripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self) -> None:
        self._alt = ig.INTEGRATIONS_YAML
        ig.INTEGRATIONS_YAML = _yaml_schreiben(self.server.server_port)
        os.environ["INTEG_TEST_TOKEN"] = "geheim123"

    def tearDown(self) -> None:
        ig.INTEGRATIONS_YAML = self._alt
        os.environ.pop("INTEG_TEST_TOKEN", None)

    def test_auth_wird_serverseitig_injiziert(self):
        r = ig.call_integration("dienst", path="/dinge")
        self.assertEqual(r["status"], 200)
        self.assertIn("/api/dinge", r["body"])
        self.assertEqual(_Handler.letzte_header.get("Authorization"), "token geheim123")
        self.assertFalse(r["truncated"])


if __name__ == "__main__":
    unittest.main()
