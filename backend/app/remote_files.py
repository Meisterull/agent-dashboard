"""SFTP-Datei-Operationen auf den Agenten-PCs (Verbindungen aus agents.yaml).

Gegenstück zu app/files.py, nur remote: Auflisten, Lesen/Schreiben (Editor),
Download-Stream und Upload laufen über dieselben SSH-Credentials wie das
Browser-Terminal (ssh_bridge._agent_connection). Pfade sind absolute Pfade
auf dem Zielrechner; Startpunkt ist das Home-Verzeichnis des SSH-Users —
eine Workspace-Beschränkung wie im Container gibt es hier bewusst nicht,
der SSH-Key definiert die Rechte.
"""
from __future__ import annotations

import asyncio
import ntpath
import posixpath
import re
import shlex
import stat
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from app.files import (
    SUCHE_MAX_SEKUNDEN, SUCHE_MAX_TREFFER, FilesError, decode_text, encode_text,
    pruefe_suchbegriff,
)
from app.ssh_bridge import _agent_connection
from app.ssh_connect import connect_ssh

CONNECT_TIMEOUT = 10
MAX_READ_BYTES = 512 * 1024  # Editor-Limit; Größeres nur als Download
CHUNK = 64 * 1024
# Wie lange eine ungenutzte SSH+SFTP-Verbindung offen bleibt (M15). Jeder Klick
# im Datei-Browser baute vorher einen kompletten Handshake auf — spürbar träge.
IDLE_TIMEOUT = 60.0


class RemoteFilesError(Exception):
    """Verbindungs- oder Dateifehler — vom Router als 4xx/502 behandelt."""


class _Verbindung:
    """Gecachte SSH+SFTP-Session eines Agenten."""

    __slots__ = ("conn", "sftp", "aktiv", "zuletzt", "aufraeumer", "waechter", "tot")

    def __init__(self, conn, sftp) -> None:
        self.conn = conn
        self.sftp = sftp
        self.aktiv = 0                 # laufende Operationen
        self.zuletzt = time.monotonic()
        self.aufraeumer: asyncio.Task | None = None
        self.waechter: asyncio.Task | None = None
        self.tot = False


_cache: dict[str, _Verbindung] = {}
_cache_lock = asyncio.Lock()  # nur der Aufbau ist exklusiv, nicht die Nutzung


def _lebt(v: _Verbindung) -> bool:
    if v.tot:
        return False
    pruef = getattr(v.conn, "is_closed", None)  # nicht in jeder asyncssh-Version
    if callable(pruef):
        try:
            return not pruef()
        except Exception:  # noqa: BLE001
            return False
    return True


def _schliesse(v: _Verbindung) -> None:
    v.tot = True
    try:
        v.sftp.exit()
    except Exception:  # noqa: BLE001
        pass
    try:
        v.conn.close()
    except Exception:  # noqa: BLE001
        pass


async def _ueberwache(agent_name: str, v: _Verbindung) -> None:
    """Stirbt die Verbindung (Netzabriss, Server-Neustart), sofort aus dem
    Cache nehmen — sonst hängt der nächste Klick an einer Leiche."""
    try:
        await v.conn.wait_closed()
    except Exception:  # noqa: BLE001
        pass
    v.tot = True
    if _cache.get(agent_name) is v:
        _cache.pop(agent_name, None)


async def _hole(agent_name: str) -> _Verbindung:
    """Gecachte Verbindung liefern oder neu aufbauen."""
    v = _cache.get(agent_name)
    if v is not None and _lebt(v):
        return v

    async with _cache_lock:
        v = _cache.get(agent_name)  # zweite Prüfung: jemand war schneller
        if v is not None and _lebt(v):
            return v
        if v is not None:
            _cache.pop(agent_name, None)
            _schliesse(v)

        conn_cfg = _agent_connection(agent_name) or {}
        if not conn_cfg.get("host"):
            raise RemoteFilesError(f"Keine SSH-Konfiguration für '{agent_name}'.")
        try:
            conn = await connect_ssh(conn_cfg)
        except Exception as exc:  # noqa: BLE001
            raise RemoteFilesError(f"SSH-Verbindung fehlgeschlagen: {exc}") from exc
        try:
            sftp = await conn.start_sftp_client()
        except Exception as exc:  # noqa: BLE001
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            raise RemoteFilesError(f"SFTP nicht verfügbar: {exc}") from exc
        v = _Verbindung(conn, sftp)
        v.waechter = asyncio.create_task(_ueberwache(agent_name, v))
        _cache[agent_name] = v
        return v


def verwerfe(agent_name: str) -> None:
    """Verbindung eines Agenten sofort schließen (Config-Wechsel, Fehler)."""
    _betriebssystem.pop(agent_name, None)
    v = _cache.pop(agent_name, None)
    if v is not None:
        if v.aufraeumer:
            v.aufraeumer.cancel()
        _schliesse(v)


async def _leerlauf(agent_name: str, v: _Verbindung) -> None:
    """Verbindung nach IDLE_TIMEOUT ohne Nutzung schließen."""
    try:
        while True:
            rest = IDLE_TIMEOUT - (time.monotonic() - v.zuletzt)
            if rest <= 0:
                break
            await asyncio.sleep(rest)
        if v.aktiv > 0:
            return
    except asyncio.CancelledError:
        return
    if _cache.get(agent_name) is v:
        _cache.pop(agent_name, None)
    _schliesse(v)


@asynccontextmanager
async def sftp_client(agent_name: str):
    """SFTP-Session für eine Verbindung aus agents.yaml (gecacht, M15)."""
    async with _nutze(agent_name) as v:
        yield v.sftp


@asynccontextmanager
async def _nutze(agent_name: str):
    """Gecachte Verbindung (SSH + SFTP) in Gebrauch nehmen — die Suche braucht
    neben SFTP auch die SSH-Verbindung selbst (find/grep auf der Maschine)."""
    v = await _hole(agent_name)
    v.aktiv += 1
    if v.aufraeumer:
        v.aufraeumer.cancel()
        v.aufraeumer = None
    try:
        yield v
    except BaseException:
        # Im Fehlerfall die Verbindung nicht weiterverwenden, wenn sie tot ist —
        # sonst hängt jeder weitere Klick an derselben Leiche.
        if not _lebt(v):
            verwerfe(agent_name)
        raise
    finally:
        v.aktiv = max(0, v.aktiv - 1)
        v.zuletzt = time.monotonic()
        if v.aktiv == 0 and not v.tot and _cache.get(agent_name) is v:
            try:
                v.aufraeumer = asyncio.create_task(_leerlauf(agent_name, v))
            except RuntimeError:  # Loop fährt gerade herunter
                pass


async def list_dir(agent_name: str, path: str = "") -> dict[str, Any]:
    """Verzeichnis auflisten (Ordner zuerst); leerer Pfad = Home-Verzeichnis."""
    async with sftp_client(agent_name) as sftp:
        base = str(await sftp.realpath(path or "."))
        try:
            names = await sftp.readdir(base)
        except Exception as exc:  # noqa: BLE001
            raise RemoteFilesError(f"nicht lesbar: {base} ({exc})") from exc
        entries = []
        for e in names:
            if e.filename in (".", ".."):
                continue
            perms = e.attrs.permissions or 0
            is_dir = stat.S_ISDIR(perms)
            entries.append(
                {
                    "name": e.filename,
                    "path": posixpath.join(base, e.filename),
                    "type": "dir" if is_dir else "file",
                    "size": None if is_dir else e.attrs.size,
                }
            )
        entries.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
        parent = posixpath.dirname(base) if base != "/" else None
        return {"path": base, "parent": parent, "entries": entries}


async def read_file(agent_name: str, path: str) -> dict[str, Any]:
    """Textinhalt (begrenzt) für den Editor."""
    async with sftp_client(agent_name) as sftp:
        try:
            attrs = await sftp.stat(path)
            async with sftp.open(path, "rb") as f:
                data = await f.read(MAX_READ_BYTES)
        except Exception as exc:  # noqa: BLE001
            raise RemoteFilesError(f"nicht lesbar: {path} ({exc})") from exc
        size = attrs.size or 0
        truncated = size > MAX_READ_BYTES
        try:
            text, encoding = decode_text(data, truncated)
        except FilesError as exc:
            raise RemoteFilesError(str(exc)) from exc
        return {
            "path": path,
            "content": text,
            "truncated": truncated,
            "size": size,
            "encoding": encoding,
        }


async def write_file(
    agent_name: str, path: str, content: str, encoding: str = "utf-8"
) -> dict[str, Any]:
    """Editor-Speichern: Datei komplett überschreiben (Kodierung der Datei erhalten)."""
    try:
        data = encode_text(content, encoding)
    except FilesError as exc:
        raise RemoteFilesError(str(exc)) from exc
    async with sftp_client(agent_name) as sftp:
        try:
            async with sftp.open(path, "wb") as f:
                await f.write(data)
        except Exception as exc:  # noqa: BLE001
            raise RemoteFilesError(f"nicht schreibbar: {path} ({exc})") from exc
        return {"path": path, "size": len(data)}


async def stream_file(agent_name: str, path: str) -> AsyncIterator[bytes]:
    """Datei-Inhalt chunk-weise für den Download streamen."""
    async with sftp_client(agent_name) as sftp:
        try:
            async with sftp.open(path, "rb") as f:
                while True:
                    chunk = await f.read(CHUNK)
                    if not chunk:
                        break
                    yield chunk
        except Exception as exc:  # noqa: BLE001
            raise RemoteFilesError(f"Download fehlgeschlagen: {path} ({exc})") from exc


async def make_dir(agent_name: str, path: str) -> dict[str, Any]:
    """Neues Verzeichnis (inkl. Zwischenebenen) anlegen."""
    async with sftp_client(agent_name) as sftp:
        try:
            await sftp.makedirs(path)
        except Exception as exc:  # noqa: BLE001
            raise RemoteFilesError(f"mkdir fehlgeschlagen: {path} ({exc})") from exc
        return {"path": path}


async def rename(agent_name: str, path: str, new_path: str) -> dict[str, Any]:
    """Datei/Ordner umbenennen bzw. verschieben (schlägt fehl, wenn Ziel existiert)."""
    async with sftp_client(agent_name) as sftp:
        try:
            await sftp.rename(path, new_path)
        except Exception as exc:  # noqa: BLE001
            raise RemoteFilesError(f"umbenennen fehlgeschlagen: {path} ({exc})") from exc
        return {"path": new_path}


async def _rmtree(sftp, path: str) -> None:
    for e in await sftp.readdir(path):
        if e.filename in (".", ".."):
            continue
        sub = posixpath.join(path, e.filename)
        if stat.S_ISDIR(e.attrs.permissions or 0):
            await _rmtree(sftp, sub)
        else:
            await sftp.remove(sub)
    await sftp.rmdir(path)


async def delete(agent_name: str, path: str) -> dict[str, Any]:
    """Datei oder Ordner (rekursiv) löschen."""
    async with sftp_client(agent_name) as sftp:
        try:
            # lstat statt stat (N13): einem Symlink aufs Verzeichnis würde
            # _rmtree sonst folgen und dessen Inhalt löschen — der Link selbst
            # bliebe stehen. Symlinks werden nur entfernt, nie verfolgt.
            attrs = await sftp.lstat(path)
            perms = attrs.permissions or 0
            if stat.S_ISDIR(perms):
                await _rmtree(sftp, path)
            else:
                await sftp.remove(path)
        except RemoteFilesError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RemoteFilesError(f"löschen fehlgeschlagen: {path} ({exc})") from exc
        return {"deleted": path}


async def upload_file(agent_name: str, directory: str, filename: str, src) -> dict[str, Any]:
    """Upload in ein Zielverzeichnis; `src` ist ein async Reader (UploadFile)."""
    # Nur Basename verwenden — kein Pfad-Schmuggel über den Dateinamen. Auf
    # Windows-Zielen trennt auch "\" (N12), deshalb beide Konventionen.
    safe_name = ntpath.basename(posixpath.basename(filename or ""))
    if safe_name in ("", ".", ".."):
        safe_name = "upload"
    dest = posixpath.join(directory, safe_name)
    written = 0
    async with sftp_client(agent_name) as sftp:
        try:
            async with sftp.open(dest, "wb") as f:
                while True:
                    chunk = await src.read(CHUNK)
                    if not chunk:
                        break
                    await f.write(chunk)
                    written += len(chunk)
        except Exception as exc:  # noqa: BLE001
            raise RemoteFilesError(f"Upload fehlgeschlagen: {dest} ({exc})") from exc
    return {"path": dest, "size": written}


# --- Suche auf der Maschine (Issue #45) ---------------------------------------
# SFTP kennt kein find — aber das Dashboard hat ohnehin die SSH-Verbindung je
# Maschine. Der Suchbegriff wird NIE in eine Shell-Zeile interpoliert, ohne
# vorher durch pruefe_suchbegriff (keine Steuerzeichen) UND shlex.quote zu
# gehen; auf Windows-Zielen (cmd.exe, kein shlex) zusätzlich eine enge
# Zeichen-Whitelist. Deckel: Treffer (head) + Zeit (Prozess wird beendet).
_betriebssystem: dict[str, str] = {}   # agent -> "posix" | "windows"
_AUSGELASSEN_REMOTE = (".git", "node_modules", "__pycache__")
_WINDOWS_ERLAUBT = re.compile(r"^[\w .,;=+@#~()\[\]{}äöüÄÖÜß-]+$")
_WINDOWS_PFAD_VERBOTEN = re.compile(r'["&|<>^%!\r\n]')
_WIN_SFTP = re.compile(r"^/([A-Za-z]):/")


async def _fuehre_aus(conn, befehl: str, frist: float) -> tuple[str, bool, str]:
    """Befehl auf der Maschine ausführen; (stdout, abgebrochen, stderr). Nach
    `frist` Sekunden wird der Prozess beendet und das bis dahin Gelesene
    geliefert. Bricht der Aufrufer ab (Browser weg), stirbt der Prozess mit."""
    proc = await conn.create_process(befehl, encoding=None)
    roh, fehler, abgebrochen = b"", b"", False

    async def lese() -> None:
        nonlocal roh, fehler
        roh, fehler = await asyncio.gather(proc.stdout.read(), proc.stderr.read())

    try:
        try:
            await asyncio.wait_for(lese(), frist)
        except asyncio.TimeoutError:
            abgebrochen = True
    finally:
        for schritt in (proc.kill, proc.close):
            try:
                schritt()
            except Exception:  # noqa: BLE001
                pass

    def als_text(b) -> str:
        if isinstance(b, str):
            return b
        try:
            return decode_text(b)[0]
        except FilesError:
            return b.decode("utf-8", "replace")
    return als_text(roh), abgebrochen, als_text(fehler)


async def _system(agent_name: str, conn) -> str:
    """Einmal je Verbindung: POSIX (uname antwortet) oder Windows."""
    bekannt = _betriebssystem.get(agent_name)
    if bekannt:
        return bekannt
    try:
        text, _, _ = await _fuehre_aus(conn, "uname -s", 10)
        art = "posix" if text.strip() else "windows"
    except Exception:  # noqa: BLE001
        art = "windows"
    _betriebssystem[agent_name] = art
    return art


def _posix_befehl(base: str, q: str, inhalt: bool, limit: int) -> str:
    prune = " -o ".join(f"-name {shlex.quote(n)}" for n in _AUSGELASSEN_REMOTE)
    if inhalt:
        excl = " ".join(f"--exclude-dir={shlex.quote(n)}" for n in _AUSGELASSEN_REMOTE)
        return (f"grep -rIn -i -m 1 {excl} -e {shlex.quote(q)} {shlex.quote(base)} "
                f"2>/dev/null | head -n {limit + 1}")
    muster = shlex.quote("*" + q + "*")
    return (f"find {shlex.quote(base)} \\( {prune} \\) -prune -o -iname {muster} "
            f"-printf '%y\\t%s\\t%p\\n' 2>/dev/null | head -n {limit + 1}")


def _posix_befehl_ohne_printf(base: str, q: str, limit: int) -> str:
    """BSD-find (macOS) kennt kein -printf: Pfade allein, Typ kommt per SFTP."""
    prune = " -o ".join(f"-name {shlex.quote(n)}" for n in _AUSGELASSEN_REMOTE)
    muster = shlex.quote("*" + q + "*")
    return (f"find {shlex.quote(base)} \\( {prune} \\) -prune -o -iname {muster} "
            f"-print 2>/dev/null | head -n {limit + 1}")


def _windows_pfad(sftp_pfad: str) -> str:
    """`/C:/Users/x` (SFTP-Form von OpenSSH für Windows) → `C:\\Users\\x`."""
    m = _WIN_SFTP.match(sftp_pfad)
    if m:
        return (m.group(1) + ":\\" + sftp_pfad[len(m.group(0)):]).replace("/", "\\")
    return sftp_pfad.replace("/", "\\")


def _sftp_pfad_von_windows(pfad: str) -> str:
    """`C:\\Users\\x` → `/C:/Users/x` — so bleibt das Panel bei EINER Pfadform."""
    p = pfad.strip().replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", p):
        return "/" + p
    return p


def _parse_treffer(text: str, base: str, inhalt: bool, limit: int) -> tuple[list[dict], bool]:
    zeilen = [z for z in text.splitlines() if z.strip()]
    gekuerzt = len(zeilen) > limit
    treffer: list[dict[str, Any]] = []
    for z in zeilen[:limit]:
        if inhalt:
            teile = z.split(":", 2)
            if len(teile) < 3 or not teile[1].isdigit():
                continue
            pfad, nr, rest = teile[0], int(teile[1]), teile[2]
            treffer.append({"name": posixpath.basename(pfad), "path": pfad, "type": "file",
                            "size": None, "zeile": nr, "text": rest.strip()[:200]})
        else:
            teile = z.split("\t", 2)
            if len(teile) == 3:
                typ, groesse, pfad = teile
                ist_dir = typ == "d"
                treffer.append({"name": posixpath.basename(pfad), "path": pfad,
                                "type": "dir" if ist_dir else "file",
                                "size": None if ist_dir else int(groesse or 0)})
            else:
                treffer.append({"name": posixpath.basename(z), "path": z,
                                "type": None, "size": None})
    # den Startpunkt selbst nicht als Treffer führen
    treffer = [t for t in treffer if t["path"] != base]
    return treffer, gekuerzt


async def suche(agent_name: str, path: str, q: str, inhalt: bool = False,
                limit: int = SUCHE_MAX_TREFFER, frist: float = SUCHE_MAX_SEKUNDEN) -> dict[str, Any]:
    """Rekursive Suche ab `path` (leer = Home) auf der Maschine: nach Namen
    (find) oder mit `inhalt` nach Text (grep, erste Trefferzeile je Datei)."""
    q = pruefe_suchbegriff(q)
    start = time.monotonic()
    async with _nutze(agent_name) as v:
        base = str(await v.sftp.realpath(path or "."))
        system = await _system(agent_name, v.conn)
        if system == "windows":
            if inhalt:
                raise RemoteFilesError("Inhaltssuche ist auf Windows-Maschinen nicht verfügbar")
            if not _WINDOWS_ERLAUBT.match(q):
                raise RemoteFilesError("Suchbegriff enthält auf Windows unzulässige Zeichen")
            wurzel = _windows_pfad(base)
            if _WINDOWS_PFAD_VERBOTEN.search(wurzel):
                raise RemoteFilesError("Pfad enthält unzulässige Zeichen")
            treffer: list[dict[str, Any]] = []
            gekuerzt = False
            for schalter, typ in (("/ad", "dir"), ("/a-d", "file")):
                text, abgebrochen, _ = await _fuehre_aus(
                    v.conn, f'dir /s /b {schalter} "{wurzel}\\*{q}*"', frist)
                zeilen = [z for z in text.splitlines() if z.strip()]
                gekuerzt = gekuerzt or abgebrochen or len(zeilen) > limit
                for z in zeilen[:limit]:
                    p = _sftp_pfad_von_windows(z)
                    treffer.append({"name": posixpath.basename(p), "path": p,
                                    "type": typ, "size": None})
            treffer = treffer[:limit]
        else:
            text, abgebrochen, stderr = await _fuehre_aus(
                v.conn, _posix_befehl(base, q, inhalt, limit), frist)
            treffer, gekuerzt = _parse_treffer(text, base, inhalt, limit)
            if not inhalt and not treffer and not abgebrochen and "printf" in stderr:
                # kein GNU-find (macOS/BSD: „unknown primary -printf")
                # → ohne -printf, Typ per SFTP
                text, abgebrochen, _ = await _fuehre_aus(
                    v.conn, _posix_befehl_ohne_printf(base, q, limit), frist)
                treffer, gekuerzt = _parse_treffer(text, base, False, limit)
                for t in treffer:
                    try:
                        attrs = await v.sftp.stat(t["path"])
                        ist_dir = stat.S_ISDIR(attrs.permissions or 0)
                        t["type"] = "dir" if ist_dir else "file"
                        t["size"] = None if ist_dir else attrs.size
                    except Exception:  # noqa: BLE001
                        t["type"] = "file"
            gekuerzt = gekuerzt or abgebrochen
    for t in treffer:
        if t.get("type") is None:
            t["type"] = "file"
    return {"path": base, "q": q, "inhalt": bool(inhalt), "treffer": treffer,
            "gekuerzt": gekuerzt, "dauer": round(time.monotonic() - start, 3)}
