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
import posixpath
import stat
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from app.files import FilesError, decode_text, encode_text
from app.ssh_bridge import _agent_connection
from app.ssh_connect import connect_ssh

CONNECT_TIMEOUT = 10
MAX_READ_BYTES = 512 * 1024  # Editor-Limit; Größeres nur als Download
CHUNK = 64 * 1024


class RemoteFilesError(Exception):
    """Verbindungs- oder Dateifehler — vom Router als 4xx/502 behandelt."""


@asynccontextmanager
async def sftp_client(agent_name: str):
    """SSH+SFTP-Session für eine Verbindung aus agents.yaml öffnen."""
    conn_cfg = _agent_connection(agent_name) or {}
    if not conn_cfg.get("host"):
        raise RemoteFilesError(f"Keine SSH-Konfiguration für '{agent_name}'.")

    try:
        conn = await connect_ssh(conn_cfg)
    except Exception as exc:  # noqa: BLE001
        raise RemoteFilesError(f"SSH-Verbindung fehlgeschlagen: {exc}") from exc

    async with conn:
        try:
            sftp = await conn.start_sftp_client()
        except Exception as exc:  # noqa: BLE001
            raise RemoteFilesError(f"SFTP nicht verfügbar: {exc}") from exc
        async with sftp:
            yield sftp


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
            attrs = await sftp.stat(path)
            if stat.S_ISDIR(attrs.permissions or 0):
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
    # Nur Basename verwenden — kein Pfad-Schmuggel über den Dateinamen.
    safe_name = posixpath.basename(filename or "upload")
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
