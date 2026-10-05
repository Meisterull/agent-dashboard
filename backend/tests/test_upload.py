"""Tests für den gestreamten Datei-Upload (Issue #48):

    cd backend && python -m tests.test_upload

Abgedeckt: der Leser über einen Byte-Strom (stückweises read, Grenze beim
Mitzählen), die Vorprüfung über Content-Length, das Schreiben in den
Workspace (Inhalt, Basename, gesperrte Bereiche, kein Bruchstück nach
Abbruch), das Aufräumen eines abgebrochenen SFTP-Uploads — und dass die
nginx-Vorlage die Upload-Routen mit eigener Grenze und ohne Zwischenpuffer
führt, während der Rest der API bei 50 MB bleibt.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_WS = Path(tempfile.mkdtemp(prefix="upload-ws-"))
os.environ["WORKSPACE_DIR"] = str(_WS)
os.environ["UPLOAD_MAX_MB"] = "1"

from app import remote_files as rf  # noqa: E402
from app import upload  # noqa: E402
from app.files import FilesError  # noqa: E402
from tests.test_remote_files import _Umgebung  # noqa: E402

VORLAGE = Path(__file__).resolve().parents[2] / "nginx" / "agent-dashboard.conf.template"


async def _strom(stuecke, fehler: BaseException | None = None):
    for s in stuecke:
        yield s
    if fehler:
        raise fehler


class TestLeser(unittest.TestCase):
    def test_read_liefert_gewuenschte_stuecke(self):
        async def szenario():
            leser = upload.KoerperLeser(_strom([b"abc", b"defg", b"h"]), grenze=0)
            teile = []
            while True:
                teil = await leser.read(3)
                if not teil:
                    break
                teile.append(teil)
            self.assertEqual(teile, [b"abc", b"def", b"gh"])
            self.assertEqual(leser.gelesen, 8)
        asyncio.run(szenario())

    def test_grenze_beim_mitzaehlen(self):
        async def szenario():
            leser = upload.KoerperLeser(_strom([b"x" * 600, b"x" * 600]), grenze=1000)
            with self.assertRaises(upload.UploadZuGross):
                while await leser.read(256):
                    pass
            self.assertTrue(leser.zu_gross)
        asyncio.run(szenario())

    def test_vorgabe_kommt_aus_der_umgebung(self):
        self.assertEqual(upload.MAX_MB, 1)
        self.assertEqual(upload.MAX_BYTES, 1024 * 1024)

    def test_vorpruefung_content_length(self):
        upload.pruefe_laenge(None)
        upload.pruefe_laenge("kaputt")
        upload.pruefe_laenge(str(1024 * 1024))
        with self.assertRaises(upload.UploadZuGross) as ctx:
            upload.pruefe_laenge(str(1024 * 1024 + 1))
        self.assertIn("1 MB", str(ctx.exception))
        upload.pruefe_laenge(str(10**12), grenze=0)  # 0 = unbegrenzt


class TestWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        for p in list(_WS.rglob("*"))[::-1]:
            (p.rmdir if p.is_dir() else p.unlink)()
        (_WS / "ziel").mkdir()

    def test_schreibt_gestreamt(self):
        inhalt = os.urandom(700_000)
        stuecke = [inhalt[i:i + 65536] for i in range(0, len(inhalt), 65536)]
        erg = asyncio.run(upload.schreibe_lokal(
            "ziel", "..\\..\\film.mts", upload.KoerperLeser(_strom(stuecke))))
        self.assertEqual(erg["size"], len(inhalt))
        self.assertEqual((_WS / erg["path"]).read_bytes(), inhalt)
        self.assertEqual(Path(erg["path"]).parent.name, "ziel")

    def test_abbruch_laesst_kein_bruchstueck(self):
        leser = upload.KoerperLeser(_strom([b"a" * 300_000], ConnectionError("weg")))
        with self.assertRaises(ConnectionError):
            asyncio.run(upload.schreibe_lokal("ziel", "halb.bin", leser))
        self.assertEqual(list((_WS / "ziel").iterdir()), [])

    def test_zu_gross_laesst_kein_bruchstueck(self):
        leser = upload.KoerperLeser(_strom([b"a" * 900_000, b"a" * 900_000]))
        with self.assertRaises(upload.UploadZuGross):
            asyncio.run(upload.schreibe_lokal("ziel", "gross.bin", leser))
        self.assertEqual(list((_WS / "ziel").iterdir()), [])

    def test_kein_verzeichnis(self):
        with self.assertRaises(FilesError):
            asyncio.run(upload.schreibe_lokal(
                "gibtsnicht", "a.txt", upload.KoerperLeser(_strom([b"x"]))))


class TestRemoteAbbruch(unittest.TestCase):
    def test_abgebrochener_upload_wird_entfernt(self):
        async def szenario(u: _Umgebung) -> None:
            leser = upload.KoerperLeser(_strom([b"a" * 100], ConnectionError("weg")))
            with self.assertRaises(rf.RemoteFilesError):
                await rf.upload_file("erp", "/ziel", "halb.bin", leser)
            self.assertIn(("remove", "/ziel/halb.bin"), u.verbindungen[-1].sftp.aufrufe)

        with _Umgebung() as u:
            asyncio.run(szenario(u))

    def test_zu_gross_ist_am_leser_erkennbar(self):
        async def szenario(u: _Umgebung) -> None:
            leser = upload.KoerperLeser(_strom([b"a" * 600, b"a" * 600]), grenze=1000)
            with self.assertRaises(rf.RemoteFilesError):
                await rf.upload_file("erp", "/ziel", "gross.bin", leser)
            self.assertTrue(leser.zu_gross)

        with _Umgebung() as u:
            asyncio.run(szenario(u))


class TestNginxVorlage(unittest.TestCase):
    def setUp(self) -> None:
        self.text = VORLAGE.read_text(encoding="utf-8")

    def _block(self, kopf: str) -> str:
        start = self.text.index(kopf)
        return self.text[start:self.text.index("\n    }", start)]

    def test_upload_routen_haben_eigene_grenze_ohne_puffer(self):
        for kopf in ("location = /api/files/upload {",
                     "location ~ ^/api/remote/[^/]+/upload$ {"):
            block = self._block(kopf)
            self.assertIn("client_max_body_size ${UPLOAD_MAX_BODY};", block)
            self.assertIn("proxy_request_buffering off;", block)
            self.assertIn("proxy_read_timeout 3600s;", block)

    def test_rest_der_api_bleibt_bei_50_mb(self):
        self.assertEqual(self.text.count("client_max_body_size 50m;"), 1)
        self.assertNotIn("client_max_body_size", self._block("location /api/ {"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
