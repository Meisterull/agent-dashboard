"""Port-Stabilität der Kanal-Bindung (M10) — Ergänzung zu test_mcp_scope.py:

    cd backend && python -m tests.test_mcp_scope_ports

Ein neuer Eintrag OBEN in agents.yaml darf die Ports der übrigen Agenten nicht
verschieben: sonst forwardet ein noch offener Tunnel bis zu 60 s lang auf den
gebundenen Kanal eines ANDEREN Agenten (fremde Identität, fremde Allowlist).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import mcp_scope  # noqa: E402
from app.mcp_scope import compute_scopes  # noqa: E402


def _agent(name: str, **extra) -> dict:
    conn = {"type": "ssh", "host": "h", **extra.pop("connection", {})}
    return {"name": name, "connection": conn, **extra}


class TestPortStabilitaet(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.alt = mcp_scope.PORT_MAP_PATH
        mcp_scope.PORT_MAP_PATH = Path(self.tmp.name) / "mcp_ports.json"

    def tearDown(self) -> None:
        mcp_scope.PORT_MAP_PATH = self.alt
        self.tmp.cleanup()

    def test_neuer_agent_oben_verschiebt_nichts(self):
        alt_scopes, _ = compute_scopes([_agent("a"), _agent("b")])
        mcp_scope.write_port_map(alt_scopes)

        neu, warnungen = compute_scopes([_agent("neu"), _agent("a"), _agent("b")])
        self.assertEqual(neu["a"]["port"], alt_scopes["a"]["port"])
        self.assertEqual(neu["b"]["port"], alt_scopes["b"]["port"])
        self.assertNotIn(neu["neu"]["port"],
                         {alt_scopes["a"]["port"], alt_scopes["b"]["port"]})
        self.assertEqual(warnungen, [])

    def test_expliziter_port_sticht_alte_map(self):
        mcp_scope.write_port_map({"a": {"port": 9555}})
        scopes, _ = compute_scopes([
            _agent("b", connection={"mcp_local_port": 9555}),
            _agent("a"),
        ])
        self.assertEqual(scopes["b"]["port"], 9555)
        self.assertNotEqual(scopes["a"]["port"], 9555)

    def test_port_eines_entfernten_agenten_wird_frei(self):
        mcp_scope.write_port_map({"weg": {"port": mcp_scope.SCOPED_PORT_BASE}})
        scopes, _ = compute_scopes([_agent("a")])
        self.assertEqual(scopes["a"]["port"], mcp_scope.SCOPED_PORT_BASE)

    def test_freier_port_aus_der_map_wird_ignoriert(self):
        mcp_scope.write_port_map({"a": {"port": mcp_scope.MCP_PORT}})
        scopes, _ = compute_scopes([_agent("a")])
        self.assertNotEqual(scopes["a"]["port"], mcp_scope.MCP_PORT)

    def test_ohne_map_wie_bisher(self):
        scopes, _ = compute_scopes([_agent("a"), _agent("b")])
        self.assertEqual(scopes["a"]["port"], mcp_scope.SCOPED_PORT_BASE)
        self.assertEqual(scopes["b"]["port"], mcp_scope.SCOPED_PORT_BASE + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
