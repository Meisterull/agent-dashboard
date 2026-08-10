"""Tests für app/mcp_scope.py — reine Stdlib, laufen ohne mcp/fastapi:

    cd backend && python -m tests.test_mcp_scope
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import mcp_scope
from app.mcp_scope import ScopeError, compute_scopes, resolve_ident


def _agent(name: str, typ: str = "ssh", **extra) -> dict:
    conn = {"type": typ, "host": "h", **extra.pop("connection", {})}
    return {"name": name, "connection": conn, **extra}


class TestComputeScopes(unittest.TestCase):
    def test_auto_ports_fortlaufend_und_deterministisch(self):
        agents = [_agent("a"), _agent("b"), _agent("c")]
        scopes, warnungen = compute_scopes(agents)
        base = mcp_scope.SCOPED_PORT_BASE
        self.assertEqual(scopes["a"]["port"], base)
        self.assertEqual(scopes["b"]["port"], base + 1)
        self.assertEqual(scopes["c"]["port"], base + 2)
        self.assertEqual(warnungen, [])
        # gleicher Input -> gleiche Zuordnung
        self.assertEqual(compute_scopes(agents)[0], scopes)

    def test_expliziter_port_gewinnt(self):
        agents = [_agent("a"), _agent("b", connection={"mcp_local_port": 9555})]
        scopes, _ = compute_scopes(agents)
        self.assertEqual(scopes["b"]["port"], 9555)
        self.assertNotEqual(scopes["a"]["port"], 9555)

    def test_port_kollision_wird_aufgeloest(self):
        agents = [
            _agent("a", connection={"mcp_local_port": 9555}),
            _agent("b", connection={"mcp_local_port": 9555}),
        ]
        scopes, warnungen = compute_scopes(agents)
        self.assertNotEqual(scopes["a"]["port"], scopes["b"]["port"])
        self.assertTrue(any("9555" in w for w in warnungen))

    def test_freier_port_nie_vergeben(self):
        agents = [_agent("a", connection={"mcp_local_port": mcp_scope.MCP_PORT})]
        scopes, warnungen = compute_scopes(agents)
        self.assertNotEqual(scopes["a"]["port"], mcp_scope.MCP_PORT)
        self.assertTrue(warnungen)

    def test_nur_ssh_agenten(self):
        scopes, _ = compute_scopes([_agent("a"), _agent("lokal", typ="local")])
        self.assertIn("a", scopes)
        self.assertNotIn("lokal", scopes)

    def test_tools_allowlist_und_tippfehler(self):
        agents = [_agent("a", tools=["inbox", "gibtsnicht", "mark_read"])]
        scopes, warnungen = compute_scopes(agents)
        self.assertEqual(scopes["a"]["tools"], ["inbox", "mark_read"])
        self.assertTrue(any("gibtsnicht" in w for w in warnungen))

    def test_tools_default_alle_und_leer_ist_keine(self):
        scopes, _ = compute_scopes([_agent("a"), _agent("b", tools=[])])
        self.assertIsNone(scopes["a"]["tools"])   # nicht konfiguriert = alle
        self.assertEqual(scopes["b"]["tools"], [])  # leer = keine

    def test_tools_in_connection_auch_erlaubt(self):
        scopes, _ = compute_scopes([_agent("a", connection={"tools": ["inbox"]})])
        self.assertEqual(scopes["a"]["tools"], ["inbox"])


class TestResolveIdent(unittest.TestCase):
    def test_none_und_eigener_name_ok(self):
        self.assertEqual(resolve_ident("erp", None, "agent"), "erp")
        self.assertEqual(resolve_ident("erp", "", "agent"), "erp")
        self.assertEqual(resolve_ident("erp", "erp", "agent"), "erp")

    def test_fremder_name_abgelehnt(self):
        with self.assertRaises(ScopeError):
            resolve_ident("erp", "deverp", "agent")


class TestPortMap(unittest.TestCase):
    def test_roundtrip_und_fehlend(self):
        with tempfile.TemporaryDirectory() as tmp:
            pfad = Path(tmp) / "mcp_ports.json"
            alt = mcp_scope.PORT_MAP_PATH
            mcp_scope.PORT_MAP_PATH = pfad
            try:
                self.assertEqual(mcp_scope.read_port_map(), {})
                mcp_scope.write_port_map({"a": {"port": 9100}, "b": {"port": 9101}})
                self.assertEqual(mcp_scope.read_port_map(), {"a": 9100, "b": 9101})
                # kaputtes JSON -> leer statt Crash
                pfad.write_text("{kaputt", encoding="utf-8")
                self.assertEqual(mcp_scope.read_port_map(), {})
            finally:
                mcp_scope.PORT_MAP_PATH = alt


if __name__ == "__main__":
    unittest.main(verbosity=2)
