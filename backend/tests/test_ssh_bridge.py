"""Tests für app/ssh_bridge.py (Terminal-Puffer + Reattach-Replay).

    cd backend && python -m tests.test_ssh_bridge

Reine Standardlib: asyncssh wird nicht gebraucht — die Session wird mit
Attrappen für conn/proc gebaut. Abgedeckt (T8): strip_ansi, _buffer_append
inklusive BUFFER_LIMIT, und die Replay-Logik beim Reattach (N10: Snapshot
statt laufendem Index, keine unsterbliche Session nach fehlgeschlagenem
Replay).
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ssh_bridge  # noqa: E402
from app.ssh_bridge import _buffer_append, _Session, strip_ansi  # noqa: E402


class TestStripAnsi(unittest.TestCase):
    def test_farben_und_cursor_raus(self):
        self.assertEqual(strip_ansi("\x1b[31mrot\x1b[0m fertig"), "rot fertig")
        self.assertEqual(strip_ansi("\x1b[2J\x1b[HStart"), "Start")

    def test_osc_titel_raus(self):
        self.assertEqual(strip_ansi("\x1b]0;mein titel\x07text"), "text")

    def test_carriage_return_letzter_stand_gewinnt(self):
        self.assertEqual(strip_ansi("10%\r50%\r100%"), "100%")
        self.assertEqual(strip_ansi("zeile1\r\nzeile2"), "zeile1\nzeile2")

    def test_tabs_bleiben_steuerzeichen_nicht(self):
        self.assertEqual(strip_ansi("a\tb\x07c"), "a\tbc")

    def test_zeichensatzwahl_raus(self):
        self.assertEqual(strip_ansi("\x1b(Bnormal"), "normal")


class _FakeProc:
    def __init__(self) -> None:
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True


class _FakeConn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeWs:
    """WebSocket-Attrappe: sammelt Ausgaben, kann beim Senden scheitern."""

    def __init__(self, sid: str = "main", fehler_ab: int | None = None,
                 beim_senden=None) -> None:
        self.query_params = {"sid": sid}
        self.gesendet: list[str] = []
        self.fehler_ab = fehler_ab
        self.beim_senden = beim_senden
        self.geschlossen: list[int | None] = []

    async def accept(self) -> None:
        pass

    async def send_text(self, text: str) -> None:
        if self.fehler_ab is not None and len(self.gesendet) >= self.fehler_ab:
            raise ConnectionResetError("Client weg")
        self.gesendet.append(text)
        if self.beim_senden:
            self.beim_senden(len(self.gesendet))

    async def receive_text(self) -> str:
        raise ConnectionResetError("Client weg")

    async def close(self, code: int | None = None) -> None:
        self.geschlossen.append(code)


def _session(key: str = "erp:main", chunks: tuple[str, ...] = ()) -> _Session:
    sess = _Session(key, "erp", "main", _FakeConn(), _FakeProc())
    for c in chunks:
        _buffer_append(sess, c)
    ssh_bridge._sessions[key] = sess
    return sess


class TestPuffer(unittest.TestCase):
    def tearDown(self) -> None:
        ssh_bridge._sessions.clear()

    def test_laenge_stimmt(self):
        sess = _session(chunks=("abc", "de"))
        self.assertEqual(sess.buf_len, 5)
        self.assertEqual("".join(sess.buffer), "abcde")

    def test_limit_wirft_von_links_weg(self):
        alt = ssh_bridge.BUFFER_LIMIT
        ssh_bridge.BUFFER_LIMIT = 10
        try:
            sess = _session()
            for teil in ("aaaa", "bbbb", "cccc", "dddd"):
                _buffer_append(sess, teil)
            self.assertLessEqual(sess.buf_len, 10)
            self.assertEqual(sess.buf_len, len("".join(sess.buffer)))
            self.assertEqual("".join(sess.buffer), "ccccdddd")  # ältestes weg
        finally:
            ssh_bridge.BUFFER_LIMIT = alt


class TestReattachReplay(unittest.TestCase):
    def setUp(self) -> None:
        self.grace = ssh_bridge.GRACE_SECONDS
        ssh_bridge._sessions.clear()

    def tearDown(self) -> None:
        ssh_bridge.GRACE_SECONDS = self.grace
        ssh_bridge._sessions.clear()

    def test_replay_nutzt_snapshot(self):
        """N10: _pump darf nebenher anhängen/verwerfen, ohne dass der Replay
        Stücke überspringt oder doppelt sendet."""
        sess = _session(chunks=("eins", "zwei", "drei", "vier"))
        sess.detached_at = 1.0

        def nebenlauf(_n: int) -> None:  # simuliert _pump während des Replays
            sess.buffer.append("neu")
            sess.buffer.popleft()

        ws = _FakeWs(beim_senden=nebenlauf)
        asyncio.run(ssh_bridge.bridge(ws, "erp"))
        self.assertEqual(ws.gesendet, ["eins", "zwei", "drei", "vier"])

    def test_session_nach_gescheitertem_replay_nicht_unsterblich(self):
        """N10: bricht der Replay ab, muss die Verfallsuhr wieder laufen —
        sonst bleibt die Session ohne Client für immer stehen."""
        ssh_bridge.GRACE_SECONDS = 0.05

        async def szenario() -> None:
            sess = _session(chunks=("alt1", "alt2"))
            sess.detached_at = 1.0
            ws = _FakeWs(fehler_ab=0)          # schon der erste Send scheitert
            await ssh_bridge.bridge(ws, "erp")
            self.assertIsNotNone(sess.expire_task, "keine Verfallsuhr gestartet")
            self.assertIsNotNone(sess.detached_at)
            await asyncio.sleep(0.2)           # Verfall abwarten
            self.assertNotIn("erp:main", ssh_bridge._sessions)
            self.assertTrue(sess.closed)
            self.assertTrue(sess.conn.closed and sess.proc.terminated)

        asyncio.run(szenario())

    def test_alter_client_wird_uebernommen(self):
        """Reattach wirft den alten Client mit 4000 raus (kein Doppel-Client)."""
        async def szenario() -> None:
            sess = _session(chunks=("x",))
            alt = _FakeWs()
            sess.ws = alt
            neu = _FakeWs()
            await ssh_bridge.bridge(neu, "erp")
            self.assertEqual(alt.geschlossen[:1], [4000])
            self.assertEqual(neu.gesendet, ["x"])

        asyncio.run(szenario())


if __name__ == "__main__":
    unittest.main(verbosity=2)
