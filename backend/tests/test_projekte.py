"""Tests für app/projekte.py + die Projekt-Pfade der SSH-Brücke (11.10.2026).

    cd backend && python -m tests.test_projekte

Reine Standardlib (+ PyYAML, das config.py ohnehin braucht). Abgedeckt:
Prüfung der Einträge (Name/Pfad/Befehl), Laden/Speichern/Entfernen in einer
temporären projekte.yaml, die Startkommandos (tmux-Zeile, Rückfall,
Windows), und in der Brücke: kein Replay für Projekt-Terminals, der
Resize-Wackler nach Reattach, tmux-kill nur beim ausdrücklichen Beenden.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_tmp = tempfile.mkdtemp(prefix="projekte-test-")
os.environ["DATA_CONFIG_DIR"] = _tmp

from app import projekte, ssh_bridge  # noqa: E402
from app.projekte import ProjektFehler  # noqa: E402


class TestPruefung(unittest.TestCase):
    def test_standardbefehl_und_trim(self):
        p = projekte.pruefe_projekt({"name": " zeitalter ", "pfad": " /home/x/zeitalter "})
        self.assertEqual(p, {"name": "zeitalter", "pfad": "/home/x/zeitalter",
                             "befehl": projekte.STANDARD_BEFEHL})

    def test_name_regeln(self):
        for schlecht in ("", "a:b", "a.b", "a b", "x" * 33, "ä"):
            with self.assertRaises(ProjektFehler, msg=schlecht):
                projekte.pruefe_projekt({"name": schlecht, "pfad": "/x"})
        projekte.pruefe_projekt({"name": "bdsm-bot_2", "pfad": "/x"})

    def test_pfad_und_befehl_ohne_steuerzeichen(self):
        with self.assertRaises(ProjektFehler):
            projekte.pruefe_projekt({"name": "a", "pfad": ""})
        with self.assertRaises(ProjektFehler):
            projekte.pruefe_projekt({"name": "a", "pfad": "/x\n/y"})
        with self.assertRaises(ProjektFehler):
            projekte.pruefe_projekt({"name": "a", "pfad": "/x", "befehl": "claude\nrm -rf /"})

    def test_liste_doppelte_namen_und_deckel(self):
        with self.assertRaises(ProjektFehler):
            projekte.pruefe_liste([{"name": "a", "pfad": "/x"}, {"name": "A", "pfad": "/y"}])
        with self.assertRaises(ProjektFehler):
            projekte.pruefe_liste([{"name": f"p{i}", "pfad": "/x"}
                                   for i in range(projekte.MAX_JE_VERBINDUNG + 1)])
        self.assertEqual(projekte.pruefe_liste(None), [])

    def test_sid(self):
        self.assertTrue(projekte.ist_projekt_sid("p:zeitalter"))
        self.assertFalse(projekte.ist_projekt_sid("p:"))
        self.assertFalse(projekte.ist_projekt_sid("main"))
        self.assertFalse(projekte.ist_projekt_sid("2"))
        self.assertEqual(projekte.projekt_name_aus_sid("p:zeitalter"), "zeitalter")


class TestDatei(unittest.TestCase):
    def setUp(self):
        if projekte.PROJEKTE_YAML.exists():
            projekte.PROJEKTE_YAML.unlink()

    def test_rundlauf(self):
        self.assertEqual(projekte.lade_alle(), {})
        projekte.speichere("werkstatt", [
            {"name": "zeitalter", "pfad": "/home/x/zeitalter"},
            {"name": "bot", "pfad": "/home/x/bot", "befehl": "claude"},
        ])
        projekte.speichere("laptop", [{"name": "a", "pfad": "C:/p/a"}])
        alle = projekte.lade_alle()
        self.assertEqual(sorted(alle), ["laptop", "werkstatt"])
        self.assertEqual(alle["werkstatt"][1]["befehl"], "claude")
        self.assertEqual(projekte.finde("werkstatt", "bot")["pfad"], "/home/x/bot")
        self.assertIsNone(projekte.finde("werkstatt", "nix"))
        self.assertEqual(projekte.lade("unbekannt"), [])
        # leere Liste nimmt die Verbindung raus
        projekte.speichere("laptop", [])
        self.assertNotIn("laptop", projekte.lade_alle())
        projekte.entferne_verbindung("werkstatt")
        self.assertEqual(projekte.lade_alle(), {})

    def test_kaputte_datei_leert_nicht_alles(self):
        projekte.PROJEKTE_YAML.write_text(
            "werkstatt:\n  - name: ok\n    pfad: /x\n  - name: 'a:b'\n    pfad: /y\n"
            "  - nur_text\nlaptop: kein_liste\n",
            encoding="utf-8",
        )
        alle = projekte.lade_alle()
        self.assertEqual([p["name"] for p in alle["werkstatt"]], ["ok"])
        self.assertNotIn("laptop", alle)
        projekte.PROJEKTE_YAML.write_text(": [kaputt", encoding="utf-8")
        self.assertEqual(projekte.lade_alle(), {})

    def test_ungueltig_wird_abgelehnt_und_nichts_geschrieben(self):
        with self.assertRaises(ProjektFehler):
            projekte.speichere("werkstatt", [{"name": "a b", "pfad": "/x"}])
        self.assertFalse(projekte.PROJEKTE_YAML.exists())


class TestKommandos(unittest.TestCase):
    P = {"name": "zeit-alter", "pfad": "/home/x/mein projekt", "befehl": "claude --continue"}

    def test_posix_tmux_zeile(self):
        z = projekte.posix_startkommando(self.P)
        self.assertIn("command -v tmux", z)
        self.assertIn("has-session -t =zeit-alter", z)
        self.assertIn("new-session -d -s zeit-alter -c '/home/x/mein projekt'", z)
        self.assertIn("send-keys -t zeit-alter -l 'claude --continue'", z)
        self.assertIn("send-keys -t zeit-alter Enter", z)
        self.assertIn("status off", z)
        self.assertIn("mouse on", z)
        self.assertIn("exec tmux attach-session -d -t zeit-alter", z)
        # Rückfall ohne tmux: cd + Befehl, Shell bleibt
        self.assertIn("cd '/home/x/mein projekt' || exit 1; claude --continue; exec", z)
        self.assertIn("tmux fehlt", z)

    def test_pfad_wird_gequotet(self):
        boese = {"name": "x", "pfad": "/tmp'; rm -rf / #", "befehl": "claude"}
        z = projekte.posix_startkommando(boese)
        self.assertIn("-c '/tmp'\"'\"'; rm -rf / #'", z)

    def test_windows_und_kill_und_verlauf(self):
        self.assertEqual(projekte.windows_startkommando(self.P),
                         'cd /d "/home/x/mein projekt" && claude --continue')
        self.assertEqual(projekte.tmux_kill_kommando(self.P), "tmux kill-session -t =zeit-alter")
        self.assertIn("has-session -t =zeit-alter 2>/dev/null && tmux capture-pane -p -J -S - -t zeit-alter", projekte.tmux_verlauf_kommando(self.P))


# --- Brücke: Attrappen wie in test_ssh_bridge.py ------------------------------

class _Proc:
    def __init__(self):
        self.groessen = []
        self.terminated = False
        self.stdin = self
        self.geschrieben = []

    def change_terminal_size(self, c, r):
        self.groessen.append((c, r))

    def write(self, d):
        self.geschrieben.append(d)

    def terminate(self):
        self.terminated = True


class _Ergebnis:
    def __init__(self, status=0, stdout=""):
        self.exit_status, self.stdout = status, stdout


class _Conn:
    def __init__(self):
        self.befehle = []
        self.closed = False
        self.antwort = _Ergebnis()

    async def run(self, befehl, check=False, timeout=None):
        self.befehle.append(befehl)
        return self.antwort

    def close(self):
        self.closed = True


class _WS:
    def __init__(self, nachrichten):
        self.gesendet = []
        self.closed = None
        self._rein = list(nachrichten)

    async def accept(self):
        pass

    async def send_text(self, t):
        self.gesendet.append(t)

    async def send_bytes(self, b):
        self.gesendet.append(b)

    async def close(self, code=None):
        self.closed = code

    async def receive_text(self):
        if not self._rein:
            raise RuntimeError("zu")
        return json.dumps(self._rein.pop(0))

    @property
    def query_params(self):
        return self._qp

    def query_params_setzen(self, sid):
        self._qp = {"sid": sid}


def _session(sid, projekt=None, tmux=False):
    key = f"werkstatt:{sid}"
    s = ssh_bridge._Session(key, "werkstatt", sid, _Conn(), _Proc())
    s.projekt, s.tmux = projekt, tmux
    s.buffer.append("alter output")
    s.buf_len = len("alter output")
    ssh_bridge._sessions[key] = s
    return s


class TestBruecke(unittest.TestCase):
    def setUp(self):
        ssh_bridge._sessions.clear()

    def _reattach(self, sess, nachrichten):
        ws = _WS(nachrichten)
        ws.query_params_setzen(sess.sid)
        asyncio.run(ssh_bridge.bridge(ws, "werkstatt"))
        return ws

    def test_normale_session_bekommt_replay_und_wackler(self):
        s = _session("main")
        ws = self._reattach(s, [{"type": "resize", "cols": 120, "rows": 40},
                                {"type": "resize", "cols": 100, "rows": 40}])
        self.assertIn("alter output", ws.gesendet)
        # erster resize nach Reattach: Wackler (119) + echte Größe; der zweite ohne
        self.assertEqual(s.proc.groessen, [(119, 40), (120, 40), (100, 40)])
        self.assertFalse(s.neuzeichnen)

    def test_projekt_session_ohne_replay(self):
        s = _session("p:zeitalter", {"name": "zeitalter", "pfad": "/x", "befehl": "claude"}, tmux=True)
        ws = self._reattach(s, [{"type": "resize", "cols": 80, "rows": 24}])
        self.assertNotIn("alter output", ws.gesendet)
        self.assertEqual(s.proc.groessen, [(79, 24), (80, 24)])

    def test_kill_nimmt_tmux_nur_ausdruecklich_mit(self):
        p = {"name": "zeitalter", "pfad": "/x", "befehl": "claude"}
        s = _session("p:zeitalter", p, tmux=True)
        asyncio.run(ssh_bridge._kill(s))                     # Shell-Exit / Detach
        self.assertEqual(s.conn.befehle, [])
        s2 = _session("p:zeitalter", p, tmux=True)
        self.assertTrue(asyncio.run(ssh_bridge.kill_session("werkstatt", "p:zeitalter")))
        self.assertEqual(s2.conn.befehle, ["tmux kill-session -t =zeitalter"])
        self.assertTrue(s2.proc.terminated and s2.conn.closed)
        # ohne tmux (Windows-Rückfall) kein tmux-Befehl
        s3 = _session("p:zeitalter", p, tmux=False)
        asyncio.run(ssh_bridge.kill_session("werkstatt", "p:zeitalter"))
        self.assertEqual(s3.conn.befehle, [])

    def test_verlauf_aus_tmux_oder_puffer(self):
        p = {"name": "zeitalter", "pfad": "/x", "befehl": "claude"}
        s = _session("p:zeitalter", p, tmux=True)
        s.conn.antwort = _Ergebnis(0, "zeile1\nzeile2\n")
        self.assertEqual(asyncio.run(ssh_bridge.hole_verlauf("werkstatt", "p:zeitalter")), "zeile1\nzeile2")
        self.assertIn("capture-pane", s.conn.befehle[0])
        s.conn.antwort = _Ergebnis(1, "")
        self.assertEqual(asyncio.run(ssh_bridge.hole_verlauf("werkstatt", "p:zeitalter")), "alter output")
        n = _session("main")
        self.assertEqual(asyncio.run(ssh_bridge.hole_verlauf("werkstatt", "main")), "alter output")
        self.assertIsNone(asyncio.run(ssh_bridge.hole_verlauf("werkstatt", "nix")))

    def test_list_sessions_nennt_projekt(self):
        _session("main")
        _session("p:zeitalter", {"name": "zeitalter", "pfad": "/x", "befehl": "c"}, True)
        liste = {s["sid"]: s["projekt"] for s in ssh_bridge.list_sessions()}
        self.assertEqual(liste, {"main": None, "p:zeitalter": "zeitalter"})

    def test_unbekanntes_projekt_schliesst_mit_4404(self):
        from app import config
        orig = ssh_bridge._agent_connection
        ssh_bridge._agent_connection = lambda name: {"host": "h"}
        try:
            ws = _WS([])
            ws.query_params_setzen("p:gibtsnicht")
            asyncio.run(ssh_bridge.bridge(ws, "werkstatt"))
            self.assertEqual(ws.closed, 4404)
            self.assertTrue(any("Kein Projekt 'gibtsnicht'" in m for m in ws.gesendet))
        finally:
            ssh_bridge._agent_connection = orig


if __name__ == "__main__":
    unittest.main()
