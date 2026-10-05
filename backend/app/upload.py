"""Datei-Upload aus dem Browser (Issue #48): streamen statt puffern.

Bis #48 schickte das Frontend alle gewählten Dateien als EIN multipart-Paket,
nginx ließ davon höchstens 50 MB durch, und der Workspace-Upload las jede
Datei komplett in den Speicher (der Container hat 1 GB). Für Videos und
Archive taugt das nicht.

Jetzt kommt je Datei eine Anfrage, deren Rumpf die rohen Bytes sind
(`?path=<ordner>&name=<datei>`, beliebiger Content-Type außer multipart).
nginx reicht den Rumpf ungepuffert durch (`proxy_request_buffering off`), und
hier wandert er stückweise ins Ziel — lokal in den Workspace, sonst per SFTP
auf die Maschine. Der alte multipart-Weg bleibt für fremde Aufrufer bestehen,
wird aber ebenfalls gestreamt.

Die Grenze `UPLOAD_MAX_MB` (Vorgabe 5120, 0 = unbegrenzt) gilt je Anfrage. Sie
steht doppelt: nginx weist zu Großes anhand von Content-Length sofort ab
(entrypoint.sh setzt denselben Wert in die Vorlage), und der Leser hier zählt
mit, falls eine Anfrage ohne Längenangabe kommt.

Alles Standardlib — die Tests laufen ohne FastAPI.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from app import files

CHUNK = 256 * 1024


def _grenze_mb() -> int:
    try:
        return max(0, int(os.environ.get("UPLOAD_MAX_MB", "5120") or 0))
    except ValueError:
        return 5120


MAX_MB = _grenze_mb()
MAX_BYTES = MAX_MB * 1024 * 1024  # 0 = unbegrenzt


class UploadZuGross(Exception):
    """Die Anfrage überschreitet UPLOAD_MAX_MB."""

    def __init__(self, max_mb: int | None = None) -> None:
        self.max_mb = MAX_MB if max_mb is None else max_mb
        super().__init__(f"Datei zu groß — erlaubt sind {self.max_mb} MB")


def pruefe_laenge(content_length: str | None, grenze: int | None = None) -> None:
    """Weist anhand des Content-Length-Kopfs ab, bevor ein Byte gelesen ist."""
    grenze = MAX_BYTES if grenze is None else grenze
    if not grenze or not content_length:
        return
    try:
        laenge = int(content_length)
    except ValueError:
        return
    if laenge > grenze:
        raise UploadZuGross(grenze // (1024 * 1024))


class KoerperLeser:
    """Macht aus einem async Byte-Strom (request.stream()) einen Reader mit
    `await read(n)` — die Schnittstelle, die remote_files.upload_file und
    `schreibe_lokal` erwarten. Zählt mit und bricht über der Grenze ab."""

    def __init__(self, strom, grenze: int | None = None) -> None:
        self._it = strom.__aiter__()
        self._puffer = bytearray()
        self._ende = False
        self._grenze = MAX_BYTES if grenze is None else grenze
        self.gelesen = 0
        self.zu_gross = False

    async def read(self, n: int = -1) -> bytes:
        while not self._ende and (n < 0 or len(self._puffer) < n):
            try:
                stueck = await self._it.__anext__()
            except StopAsyncIteration:
                self._ende = True
                break
            self.gelesen += len(stueck)
            if self._grenze and self.gelesen > self._grenze:
                self.zu_gross = True
                raise UploadZuGross(self._grenze // (1024 * 1024))
            self._puffer += stueck
        if n < 0:
            out = bytes(self._puffer)
            self._puffer.clear()
        else:
            out = bytes(self._puffer[:n])
            del self._puffer[:n]
        return out


async def schreibe_lokal(rel_dir: str, filename: str, leser) -> dict[str, Any]:
    """Streamt `leser` in eine Workspace-Datei. Bricht der Upload ab (Grenze,
    Verbindung weg, Abbrechen im Browser), bleibt kein Bruchstück liegen."""
    ziel = files.upload_ziel(rel_dir, filename)
    geschrieben = 0
    fh = await asyncio.to_thread(open, ziel, "wb")
    try:
        try:
            while True:
                stueck = await leser.read(CHUNK)
                if not stueck:
                    break
                await asyncio.to_thread(fh.write, stueck)
                geschrieben += len(stueck)
        finally:
            await asyncio.to_thread(fh.close)
    except BaseException:
        try:
            ziel.unlink()
        except OSError:
            pass
        raise
    return {"path": str(ziel.relative_to(files.WORKSPACE)), "size": geschrieben}
