"""auto_watcher: die reine Logik (Review-Struktur-H: 422 Zeilen ungetestet).

Der Netz-Teil (asyncssh-Verbindung, Remote-Prozess) bleibt bewusst draußen —
er braucht einen echten SSH-Endpunkt. Getestet wird, was ohne Netz trägt:
das Modul lädt OHNE asyncssh (der Import ist lazy — auf dem Host fehlt das
Paket), die Start-Config-Ableitung aus agents.yaml, der Script-Hash und das
Zustandsmodell des _Watcher (Status/seit/als_dict inkl. gesperrt).

    cd backend && python -m tests.test_auto_watcher
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app import auto_watcher as aw  # noqa: E402 — muss ohne asyncssh klappen


class SshCfgTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = Path(tempfile.mkstemp(prefix="aw-key-")[1])

    def tearDown(self) -> None:
        self.key.unlink(missing_ok=True)

    def agent(self, **conn_extra):
        return {
            "name": "a",
            "connection": {"type": "ssh", "host": "h", "user": "u",
                           "key_file": str(self.key), **conn_extra},
        }

    def test_vollstaendig(self):
        cfg = aw._ssh_cfg(self.agent(port=2222, mcp_port=9105))
        self.assertEqual(cfg["host"], "h")
        self.assertEqual(cfg["port"], 2222)
        self.assertEqual(cfg["mcp_port"], 9105)
        self.assertEqual(cfg["python"], "python3")

    def test_defaults(self):
        cfg = aw._ssh_cfg(self.agent())
        self.assertEqual(cfg["port"], 22)
        self.assertEqual(cfg["mcp_port"], aw.REMOTE_MCP_PORT_DEFAULT)
        self.assertIsNone(cfg["workdir"])

    def test_agent_felder_schlagen_connection(self):
        a = self.agent(workdir="/conn", claude_bin="conn-claude")
        a["workdir"] = "/agent"
        cfg = aw._ssh_cfg(a)
        self.assertEqual(cfg["workdir"], "/agent")
        self.assertEqual(cfg["claude_bin"], "conn-claude")

    def test_nicht_startbar(self):
        self.assertIsNone(aw._ssh_cfg({"name": "a"}))
        self.assertIsNone(aw._ssh_cfg({"connection": {"type": "ssh", "host": "h"}}))
        self.assertIsNone(aw._ssh_cfg(
            {"connection": {"type": "ssh", "host": "h",
                            "key_file": "/gibt/es/nicht"}}))
        a = self.agent()
        a["connection"]["type"] = "local"
        self.assertIsNone(aw._ssh_cfg(a))


class ScriptHashTests(unittest.TestCase):
    def test_stabil_und_inhaltsabhaengig(self):
        d = Path(tempfile.mkdtemp(prefix="aw-hash-"))
        p = d / "s.py"
        p.write_text("print(1)\n", encoding="utf-8")
        h1 = aw._script_hash(p)
        self.assertEqual(h1, aw._script_hash(p))
        p.write_text("print(2)\n", encoding="utf-8")
        self.assertNotEqual(h1, aw._script_hash(p))


class WatcherZustandTests(unittest.TestCase):
    def test_seit_nur_bei_statuswechsel(self):
        w = aw._Watcher("a")
        w.seit = 0
        w.setze("an")
        self.assertGreater(w.seit, 0)
        merk = w.seit
        w.setze("an", "läuft")  # gleicher Status: Zeitstempel bleibt
        self.assertEqual(w.seit, merk)
        self.assertEqual(w.detail, "läuft")

    def test_als_dict_traegt_gesperrt(self):
        w = aw._Watcher("a")
        w.gesperrt = True
        w.log.append("zeile")
        d = w.als_dict()
        self.assertTrue(d["gesperrt"])
        self.assertEqual(d["log"], ["zeile"])
        self.assertIn(d["status"], ("startet",))


if __name__ == "__main__":
    unittest.main()
