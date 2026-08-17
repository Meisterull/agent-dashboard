"""Tests für app/remote_files.py gegen eine SFTP-Attrappe (kein asyncssh nötig):

    cd backend && python -m tests.test_remote_files

Abgedeckt: Verbindungs-Cache inkl. Invalidierung (M15), Upload-Dateiname
(N12: Backslash-Schmuggel von Windows-Clients), delete folgt keinem Symlink
(N13).
"""
from __future__ import annotations

import asyncio
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import remote_files as rf  # noqa: E402


class _Attrs:
    def __init__(self, permissions: int, size: int = 0) -> None:
        self.permissions = permissions
        self.size = size


class _Datei:
    """Async-Context-Manager wie sftp.open()."""

    def __init__(self, senke: list[bytes], inhalt: bytes = b"") -> None:
        self.senke = senke
        self.inhalt = inhalt

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def read(self, n: int = -1) -> bytes:
        daten, self.inhalt = self.inhalt, b""
        return daten

    async def write(self, daten) -> None:
        self.senke.append(daten)


class _FakeSftp:
    def __init__(self) -> None:
        self.aufrufe: list[tuple] = []
        self.geschrieben: list[bytes] = []
        self.typen: dict[str, int] = {}   # pfad -> st_mode-Bits

    async def realpath(self, pfad: str) -> str:
        return pfad if pfad.startswith("/") else "/home/agent"

    async def readdir(self, pfad: str) -> list:
        self.aufrufe.append(("readdir", pfad))
        return []

    async def lstat(self, pfad: str) -> _Attrs:
        self.aufrufe.append(("lstat", pfad))
        return _Attrs(self.typen.get(pfad, stat.S_IFREG | 0o644))

    async def stat(self, pfad: str) -> _Attrs:
        self.aufrufe.append(("stat", pfad))
        return _Attrs(self.typen.get(pfad, stat.S_IFREG | 0o644))

    async def remove(self, pfad: str) -> None:
        self.aufrufe.append(("remove", pfad))

    async def rmdir(self, pfad: str) -> None:
        self.aufrufe.append(("rmdir", pfad))

    def open(self, pfad: str, modus: str = "rb"):
        self.aufrufe.append(("open", pfad, modus))
        return _Datei(self.geschrieben)

    def exit(self) -> None:
        pass


class _FakeConn:
    def __init__(self) -> None:
        self.sftp = _FakeSftp()
        self.geschlossen = asyncio.Event()

    async def start_sftp_client(self) -> _FakeSftp:
        return self.sftp

    def close(self) -> None:
        self.geschlossen.set()

    def is_closed(self) -> bool:
        return self.geschlossen.is_set()

    async def wait_closed(self) -> None:
        await self.geschlossen.wait()


class _Quelle:
    """UploadFile-Attrappe."""

    def __init__(self, daten: bytes) -> None:
        self.daten = daten

    async def read(self, _n: int) -> bytes:
        daten, self.daten = self.daten, b""
        return daten


class _Umgebung:
    """Patcht Verbindungsaufbau + agents.yaml-Lookup von remote_files."""

    def __init__(self) -> None:
        self.verbindungen: list[_FakeConn] = []

    def __enter__(self):
        self._connect = rf.connect_ssh
        self._cfg = rf._agent_connection
        rf._agent_connection = lambda name: {"host": "h", "user": "u"}

        async def connect(_cfg, **_extra):
            conn = _FakeConn()
            self.verbindungen.append(conn)
            return conn

        rf.connect_ssh = connect
        rf._cache.clear()
        return self

    def __exit__(self, *_exc):
        rf.connect_ssh = self._connect
        rf._agent_connection = self._cfg
        rf._cache.clear()
        return False


class TestVerbindungsCache(unittest.TestCase):
    def test_zweite_operation_nutzt_dieselbe_verbindung(self):
        async def szenario(u: _Umgebung) -> None:
            await rf.list_dir("erp")
            await rf.list_dir("erp", "/tmp")
            self.assertEqual(len(u.verbindungen), 1, "zweiter Handshake statt Cache")

        with _Umgebung() as u:
            asyncio.run(szenario(u))

    def test_tote_verbindung_wird_ersetzt(self):
        async def szenario(u: _Umgebung) -> None:
            await rf.list_dir("erp")
            u.verbindungen[0].close()          # Netzabriss
            await asyncio.sleep(0)             # Wächter darf laufen
            await rf.list_dir("erp")
            self.assertEqual(len(u.verbindungen), 2)

        with _Umgebung() as u:
            asyncio.run(szenario(u))

    def test_verwerfe_schliesst(self):
        async def szenario(u: _Umgebung) -> None:
            await rf.list_dir("erp")
            rf.verwerfe("erp")
            self.assertTrue(u.verbindungen[0].is_closed())
            await rf.list_dir("erp")
            self.assertEqual(len(u.verbindungen), 2)

        with _Umgebung() as u:
            asyncio.run(szenario(u))


class TestPfadSicherheit(unittest.TestCase):
    def test_upload_backslash_wird_zum_basename(self):
        async def szenario(u: _Umgebung) -> None:
            ergebnis = await rf.upload_file("erp", "/ziel",
                                            r"..\..\Windows\System32\boese.exe",
                                            _Quelle(b"inhalt"))
            self.assertEqual(ergebnis["path"], "/ziel/boese.exe")
            ergebnis = await rf.upload_file("erp", "/ziel", "../../etc/passwd",
                                            _Quelle(b"x"))
            self.assertEqual(ergebnis["path"], "/ziel/passwd")
            ergebnis = await rf.upload_file("erp", "/ziel", "..", _Quelle(b"x"))
            self.assertEqual(ergebnis["path"], "/ziel/upload")

        with _Umgebung() as u:
            asyncio.run(szenario(u))

    def test_delete_folgt_keinem_symlink(self):
        async def szenario(u: _Umgebung) -> None:
            await rf.list_dir("erp")           # Verbindung aufbauen
            sftp = u.verbindungen[0].sftp
            sftp.typen["/home/agent/link"] = stat.S_IFLNK | 0o777
            sftp.aufrufe.clear()
            await rf.delete("erp", "/home/agent/link")
            self.assertIn(("remove", "/home/agent/link"), sftp.aufrufe)
            self.assertFalse([a for a in sftp.aufrufe if a[0] in ("readdir", "rmdir")],
                             "Symlink wurde als Verzeichnis behandelt")
            self.assertFalse([a for a in sftp.aufrufe if a[0] == "stat"],
                             "stat folgt dem Symlink — lstat verwenden")

        with _Umgebung() as u:
            asyncio.run(szenario(u))

    def test_delete_verzeichnis_rekursiv(self):
        async def szenario(u: _Umgebung) -> None:
            await rf.list_dir("erp")
            sftp = u.verbindungen[0].sftp
            sftp.typen["/home/agent/ordner"] = stat.S_IFDIR | 0o755
            sftp.aufrufe.clear()
            await rf.delete("erp", "/home/agent/ordner")
            self.assertIn(("rmdir", "/home/agent/ordner"), sftp.aufrufe)

        with _Umgebung() as u:
            asyncio.run(szenario(u))


if __name__ == "__main__":
    unittest.main(verbosity=2)
