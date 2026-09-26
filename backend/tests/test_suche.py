"""Tests für die Datei-Suche (Issue #45):

    cd backend && python -m tests.test_suche

Abgedeckt: Workspace-Suche (Namen, Inhalt, gesperrte Bereiche, Punkt-Ordner,
Trefferdeckel, Binärdateien), Suchbegriff-Prüfung, Remote-Suche gegen eine
SSH-Attrappe (find/grep-Zeile, shlex-Quoting, Zeitdeckel tötet den Prozess,
Windows-Zweig mit dir /s /b und Zeichen-Whitelist, kein Inhalt auf Windows).
"""
from __future__ import annotations

import asyncio
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_WS = Path(tempfile.mkdtemp(prefix="suche-ws-"))
os.environ["WORKSPACE_DIR"] = str(_WS)

from app import files  # noqa: E402
from app import remote_files as rf  # noqa: E402
from app.files import FilesError  # noqa: E402
from tests.test_remote_files import _FakeConn, _Umgebung  # noqa: E402


def _lege(pfad: Path, inhalt: bytes = b"x") -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_bytes(inhalt)


class TestWorkspaceSuche(unittest.TestCase):
    def setUp(self) -> None:
        for p in list(_WS.rglob("*"))[::-1]:
            (p.rmdir if p.is_dir() else p.unlink)()
        _lege(_WS / "proj" / "logs" / "fehler.log", b"zeile1\nHier steht ein Fehler-String\n")
        _lege(_WS / "proj" / "Fehler Uebersicht.txt", b"nix")
        _lege(_WS / "proj" / ".git" / "fehler", b"versteckt")
        _lege(_WS / "proj" / "node_modules" / "fehler.js", b"versteckt")
        _lege(_WS / "keys" / "fehler_ed25519", b"GEHEIM fehler-string")
        _lege(_WS / "proj" / "bin.dat", b"\x00\x01fehler-string")
        (_WS / "proj" / "fehlerordner").mkdir()

    def test_namenssuche_relativ_und_ohne_versteckte(self):
        erg = files.suche("", "FEHLER")
        pfade = sorted(t["path"] for t in erg["treffer"])
        self.assertEqual(pfade, ["proj/Fehler Uebersicht.txt", "proj/fehlerordner",
                                 "proj/logs/fehler.log"], pfade)
        typen = {t["path"]: t["type"] for t in erg["treffer"]}
        self.assertEqual(typen["proj/fehlerordner"], "dir")
        self.assertEqual(typen["proj/logs/fehler.log"], "file")
        self.assertFalse(erg["gekuerzt"])
        self.assertEqual(erg["path"], "")

    def test_suche_ab_unterordner(self):
        erg = files.suche("proj/logs", "fehler")
        self.assertEqual([t["path"] for t in erg["treffer"]], ["proj/logs/fehler.log"])
        self.assertEqual(erg["path"], "proj/logs")
        with self.assertRaises(FilesError):
            files.suche("keys", "fehler")          # gesperrter Bereich
        with self.assertRaises(FilesError):
            files.suche("../..", "fehler")         # Ausbruch

    def test_inhaltssuche_erste_zeile_ohne_binaer(self):
        erg = files.suche("", "fehler-STRING", inhalt=True)
        self.assertEqual(len(erg["treffer"]), 1, erg)
        t = erg["treffer"][0]
        self.assertEqual((t["path"], t["zeile"], t["text"]),
                         ("proj/logs/fehler.log", 2, "Hier steht ein Fehler-String"))
        self.assertEqual(t["size"], 36)

    def test_trefferdeckel_und_begriff(self):
        for i in range(5):
            _lege(_WS / "viele" / f"fehler{i}.txt")
        erg = files.suche("", "fehler", limit=3)
        self.assertEqual(len(erg["treffer"]), 3)
        self.assertTrue(erg["gekuerzt"])
        for schlecht in ("", "   ", "a\nb", "x" * 201):
            with self.assertRaises(FilesError):
                files.suche("", schlecht)
        self.assertEqual(files.pruefe_suchbegriff("  ab  "), "ab")


class _SuchConn(_FakeConn):
    """SSH-Attrappe mit create_process: liefert je Befehl vorbereitete Ausgabe."""

    def __init__(self) -> None:
        super().__init__()
        self.befehle: list[str] = []
        self.antworten: list[tuple[bytes, bytes]] = []
        self.verzoegerung = 0.0
        self.getoetet = 0

    async def create_process(self, befehl: str, encoding=None):
        self.befehle.append(befehl)
        out, err = self.antworten.pop(0) if self.antworten else (b"", b"")
        conn = self

        class _Strom:
            def __init__(self, daten: bytes) -> None:
                self.daten = daten

            async def read(self) -> bytes:
                if conn.verzoegerung:
                    await asyncio.sleep(conn.verzoegerung)
                return self.daten

        class _Proc:
            stdout = _Strom(out)
            stderr = _Strom(err)

            def kill(self_inner) -> None:
                conn.getoetet += 1

            def close(self_inner) -> None:
                pass

        return _Proc()


class TestRemoteSuche(unittest.TestCase):
    def _lauf(self, coro):
        return asyncio.run(coro)

    def _umgebung(self):
        umg = _Umgebung()
        umg.__enter__()
        conn = _SuchConn()

        async def connect(_cfg, **_extra):
            umg.verbindungen.append(conn)
            return conn

        rf.connect_ssh = connect
        rf._betriebssystem.clear()
        self.addCleanup(lambda: umg.__exit__(None, None, None))
        self.addCleanup(rf._betriebssystem.clear)
        return conn

    def test_posix_namen_und_quoting(self):
        conn = self._umgebung()
        conn.antworten = [
            (b"Linux\n", b""),  # uname
            ("d\t0\t/home/agent/proj/logs\nf\t36\t/home/agent/proj/logs/fehler.log\n"
             "f\t3\t/home/agent/proj/Fehler Übersicht.txt\n".encode("utf-8"), b""),
        ]
        erg = self._lauf(rf.suche("erp", "", "a'b$(x) fehler"))
        self.assertEqual(conn.befehle[0], "uname -s")
        find = conn.befehle[1]
        # Suchbegriff und Pfad stehen NUR shell-quotiert im Befehl
        self.assertIn(shlex.quote("*a'b$(x) fehler*"), find)
        self.assertNotIn(" *a'b", find)
        self.assertIn("-prune", find)
        self.assertIn("| head -n 501", find)
        self.assertEqual([t["type"] for t in erg["treffer"]], ["dir", "file", "file"])
        self.assertEqual(erg["treffer"][1]["size"], 36)
        self.assertEqual(erg["treffer"][2]["name"], "Fehler Übersicht.txt")
        self.assertEqual(erg["path"], "/home/agent")
        self.assertFalse(erg["gekuerzt"])
        # Betriebssystem ist je Verbindung gemerkt: kein zweites uname
        conn.antworten = [(b"", b"")]
        self._lauf(rf.suche("erp", "", "x"))
        self.assertEqual(conn.befehle.count("uname -s"), 1)

    def test_posix_inhalt_und_gekuerzt(self):
        conn = self._umgebung()
        zeilen = "".join(f"/home/agent/f{i}.txt:{i + 1}:  Fehler-String {i}\n" for i in range(3))
        conn.antworten = [(b"Linux\n", b""), (zeilen.encode(), b"")]
        erg = self._lauf(rf.suche("erp", "/home/agent", "fehler-string", inhalt=True, limit=2))
        self.assertIn("grep -rIn -i -m 1", conn.befehle[1])
        self.assertIn(shlex.quote("fehler-string"), conn.befehle[1])
        self.assertEqual(len(erg["treffer"]), 2)
        self.assertTrue(erg["gekuerzt"])
        self.assertEqual(erg["treffer"][0], {"name": "f0.txt", "path": "/home/agent/f0.txt",
                                             "type": "file", "size": None, "zeile": 1,
                                             "text": "Fehler-String 0"})

    def test_bsd_find_ohne_printf_faellt_zurueck(self):
        conn = self._umgebung()
        conn.sftp.typen["/home/agent/proj/logs"] = 0o40755
        conn.antworten = [(b"Darwin\n", b""),
                          (b"", b"find: -printf: unknown primary or operator\n"),
                          (b"/home/agent/proj/logs\n/home/agent/proj/logs/fehler.log\n", b"")]
        erg = self._lauf(rf.suche("erp", "", "fehler"))
        self.assertEqual(len(conn.befehle), 3)
        self.assertIn("-print 2>", conn.befehle[2])
        self.assertEqual([t["type"] for t in erg["treffer"]], ["dir", "file"])

    def test_zeitdeckel_toetet_den_prozess(self):
        conn = self._umgebung()
        conn.antworten = [(b"Linux\n", b""), (b"", b"")]
        conn.verzoegerung = 0.3
        rf._betriebssystem["erp"] = "posix"  # uname überspringen
        conn.antworten = [(b"", b"")]
        erg = self._lauf(rf.suche("erp", "", "fehler", frist=0.05))
        self.assertTrue(erg["gekuerzt"])
        self.assertGreaterEqual(conn.getoetet, 1)
        self.assertEqual(erg["treffer"], [])

    def test_windows_dir_und_whitelist(self):
        conn = self._umgebung()

        async def realpath(_p):
            return "/C:/Users/erp"
        conn.sftp.realpath = realpath
        conn.antworten = [(b"", b"'uname' is not recognized"),          # kein uname → Windows
                          ("C:\\Users\\erp\\proj\\fehlerordner\r\n".encode("cp1252"), b""),
                          ("C:\\Users\\erp\\proj\\Fehler \xdcbersicht.txt\r\n".encode("cp1252"), b"")]
        erg = self._lauf(rf.suche("erp", "", "fehler"))
        self.assertEqual(conn.befehle[1], 'dir /s /b /ad "C:\\Users\\erp\\*fehler*"')
        self.assertEqual(conn.befehle[2], 'dir /s /b /a-d "C:\\Users\\erp\\*fehler*"')
        self.assertEqual([(t["path"], t["type"]) for t in erg["treffer"]],
                         [("/C:/Users/erp/proj/fehlerordner", "dir"),
                          ("/C:/Users/erp/proj/Fehler Übersicht.txt", "file")])
        self.assertEqual(erg["treffer"][1]["name"], "Fehler Übersicht.txt")
        # Zeichen, die in cmd.exe etwas bedeuten, kommen gar nicht erst in die Zeile
        for boese in ('a"b', "a&b", "a|b", "a%b", "a^b", "a<b"):
            with self.assertRaises(rf.RemoteFilesError):
                self._lauf(rf.suche("erp", "", boese))
        self.assertEqual(len(conn.befehle), 3)
        with self.assertRaises(rf.RemoteFilesError):
            self._lauf(rf.suche("erp", "", "fehler", inhalt=True))


if __name__ == "__main__":
    unittest.main(verbosity=1)
