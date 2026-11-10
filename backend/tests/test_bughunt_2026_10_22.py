"""Bug-Hunt 22.10.2026 (Stand 268e28e), die vier schweren Funde + Chat-Anhänge:
  * „Neue Datei" überschreibt keine vorhandene Datei mehr (lokal + SFTP)
  * delete/rename eines Symlinks treffen den Link, nicht sein Ziel
  * Remote-Suche liefert beim Zeitdeckel das bis dahin Gelesene
  * upload_ziel legt den Zielordner an (uploads/chat/<Datum>)
    cd backend && python -m tests.test_bughunt_2026_10_22
"""
from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="bughunt-")
_WS = Path(_TMP) / "ws"
_WS.mkdir()
os.environ["WORKSPACE_DIR"] = str(_WS)
os.environ["DATA_CONFIG_DIR"] = str(_WS / "config")

from app import files  # noqa: E402

files = importlib.reload(files)
from app import remote_files as rf  # noqa: E402

rf = importlib.reload(rf)


class TestNeueDatei(unittest.TestCase):
    def test_neu_laesst_vorhandene_in_ruhe(self):
        (_WS / "notizen.md").write_text("WICHTIG", encoding="utf-8")
        with self.assertRaises(files.FilesError):
            files.write_file("notizen.md", "", neu=True)
        self.assertEqual((_WS / "notizen.md").read_text(encoding="utf-8"), "WICHTIG")
        files.write_file("frisch.md", "", neu=True)
        self.assertEqual((_WS / "frisch.md").read_bytes(), b"")
        files.write_file("notizen.md", "neu")          # Editor-Speichern darf weiter überschreiben
        self.assertEqual((_WS / "notizen.md").read_text(encoding="utf-8"), "neu")

    def test_remote_neu_prueft_per_lstat(self):
        class _Sftp:
            def __init__(self, da: bool) -> None:
                self.da, self.geschrieben = da, []

            async def lstat(self, pfad):
                if not self.da:
                    raise FileNotFoundError(pfad)
                return object()

            def open(self, pfad, modus):
                sftp = self

                class _F:
                    async def __aenter__(self_inner):
                        return self_inner

                    async def __aexit__(self_inner, *a):
                        return False

                    async def write(self_inner, data):
                        sftp.geschrieben.append(data)
                return _F()

        class _Ctx:
            def __init__(self, sftp) -> None:
                self.sftp = sftp

            async def __aenter__(self):
                return self.sftp

            async def __aexit__(self, *a):
                return False

        alt = rf.sftp_client
        try:
            da = _Sftp(True)
            rf.sftp_client = lambda name: _Ctx(da)
            with self.assertRaises(rf.RemoteFilesError):
                asyncio.run(rf.write_file("erp", "/x/notizen.md", "", neu=True))
            self.assertEqual(da.geschrieben, [])
            frisch = _Sftp(False)
            rf.sftp_client = lambda name: _Ctx(frisch)
            asyncio.run(rf.write_file("erp", "/x/frisch.md", "", neu=True))
            self.assertEqual(frisch.geschrieben, [b""])
        finally:
            rf.sftp_client = alt


class TestSymlink(unittest.TestCase):
    def test_delete_haengt_link_aus(self):
        echt = _WS / "echt"
        echt.mkdir(exist_ok=True)
        (echt / "wichtig.txt").write_text("x", encoding="utf-8")
        (_WS / "link").symlink_to(echt, target_is_directory=True)
        files.delete("link")
        self.assertTrue((echt / "wichtig.txt").exists())
        self.assertFalse((_WS / "link").is_symlink())

    def test_rename_verschiebt_den_link(self):
        echt = _WS / "echt2"
        echt.mkdir(exist_ok=True)
        (_WS / "link2").symlink_to(echt, target_is_directory=True)
        files.rename("link2", "neu2")
        self.assertTrue(echt.is_dir())
        self.assertTrue((_WS / "neu2").is_symlink())
        self.assertFalse((_WS / "link2").exists())

    def test_link_nach_draussen_laesst_sich_entfernen(self):
        aussen = Path(_TMP) / "aussen"
        aussen.mkdir(exist_ok=True)
        (aussen / "a").write_text("a", encoding="utf-8")
        (_WS / "raus").symlink_to(aussen, target_is_directory=True)
        files.delete("raus")
        self.assertTrue((aussen / "a").exists())
        self.assertFalse((_WS / "raus").is_symlink())


class TestUploadZiel(unittest.TestCase):
    def test_legt_tagesordner_an(self):
        ziel = files.upload_ziel("uploads/chat/2026-10-22", "bild.png")
        self.assertTrue(ziel.parent.is_dir())
        self.assertEqual(ziel.name, "bild.png")


class TestSucheZeitdeckel(unittest.TestCase):
    def test_bis_dahin_gelesenes_bleibt(self):
        class _Strom:
            def __init__(self, stuecke) -> None:
                self.stuecke = list(stuecke)

            async def read(self, n=-1):
                if not self.stuecke:
                    await asyncio.sleep(10)        # hängt: kein EOF
                    return b""
                return self.stuecke.pop(0)

        class _Proc:
            stdout = _Strom([b"f\t1\t/a\n", b"f\t2\t/b\n"])
            stderr = _Strom([])
            getoetet = 0

            def kill(self):
                _Proc.getoetet += 1

            def close(self):
                pass

        class _Conn:
            async def create_process(self, befehl, encoding=None):
                return _Proc()

        text, abgebrochen, fehler = asyncio.run(rf._fuehre_aus(_Conn(), "find", 0.2))
        self.assertTrue(abgebrochen)
        self.assertEqual(text, "f\t1\t/a\nf\t2\t/b\n")
        self.assertEqual(fehler, "")
        self.assertGreaterEqual(_Proc.getoetet, 1)


if __name__ == "__main__":
    unittest.main()
