"""SFTP-Doppel für die Austausch-Tests: ein lokales Verzeichnis spielt die
Platte einer Maschine. Bildet genau die Aufrufe nach, die app/austausch.py an
asyncssh richtet (stat/exists/makedirs/realpath/open/rename/remove) — samt der
zwei Eigenheiten, auf die sich der Code verlässt: `open(…, "xb")` scheitert,
wenn die Datei existiert, und `rename` überschreibt nicht.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace


class _Datei:
    def __init__(self, f, stoerung=None):
        self._f, self._stoerung, self._bloecke = f, stoerung, 0

    async def read(self, n):
        self._bloecke += 1
        if self._stoerung and self._bloecke > 1:
            raise OSError("Leitung weg")
        return self._f.read(n)

    async def write(self, daten):
        self._f.write(daten)

    async def close(self):
        self._f.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self._f.close()


class SftpDoppel:
    """Ein Verzeichnis spielt die Platte einer Maschine; relativ = ab /home."""

    def __init__(self, wurzel: Path):
        self.wurzel = wurzel
        (wurzel / "home").mkdir(parents=True, exist_ok=True)
        self.lese_stoerung = False

    def _lokal(self, pfad: str) -> Path:
        p = str(pfad)
        return self.wurzel / (p.lstrip("/") if p.startswith("/") else "home/" + p)

    async def realpath(self, pfad):
        return "/" + str(self._lokal(pfad).relative_to(self.wurzel))

    async def stat(self, pfad):
        st = self._lokal(pfad).stat()
        return SimpleNamespace(permissions=st.st_mode, size=st.st_size)

    async def exists(self, pfad):
        return self._lokal(pfad).exists()

    async def makedirs(self, pfad, exist_ok=False):
        self._lokal(pfad).mkdir(parents=True, exist_ok=exist_ok)

    async def open(self, pfad, modus="rb"):
        await asyncio.sleep(0)  # wie ein echter Netz-Roundtrip: andere dürfen dran
        f = open(self._lokal(pfad), modus)  # 'xb' wirft FileExistsError wie SFTP
        return _Datei(f, self.lese_stoerung and "r" in modus)

    async def rename(self, alt, neu):
        ziel = self._lokal(neu)
        if ziel.exists():
            raise OSError("Ziel existiert")
        self._lokal(alt).rename(ziel)

    async def remove(self, pfad):
        self._lokal(pfad).unlink()


# `sftp.open()` liefert bei asyncssh etwas, das awaitbar UND Kontextmanager
# ist. Das Doppel gibt eine Coroutine zurück — für `async with sftp.open(…)`
# braucht es deshalb diese Hülle.
class _OpenHuelle:
    def __init__(self, coro):
        self._coro, self._datei = coro, None

    def __await__(self):
        return self._coro.__await__()

    async def __aenter__(self):
        self._datei = await self._coro
        return self._datei

    async def __aexit__(self, *exc):
        await self._datei.close()


_roh_open = SftpDoppel.open
SftpDoppel.open = lambda self, pfad, modus="rb": _OpenHuelle(_roh_open(self, pfad, modus))
