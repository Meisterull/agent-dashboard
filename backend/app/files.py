"""Pfad-sichere Datei-Operationen innerhalb des Workspace.

Jeder Zugriff wird gegen WORKSPACE_DIR gehärtet (kein Path-Traversal). Dient
dem Dateibaum-Panel (/api/files) und dem Lesen erzeugter Projektdateien.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace")).resolve()
MAX_READ_BYTES = 256 * 1024  # erzeugte Dateien sind klein; großes nicht ins UI


class FilesError(Exception):
    """Ungültiger Pfad oder nicht lesbar — vom Router als 400/404 behandelt."""


def _safe(relpath: str) -> Path:
    rel = (relpath or "").lstrip("/")
    p = (WORKSPACE / rel).resolve()
    if not (p == WORKSPACE or WORKSPACE in p.parents):
        raise FilesError(f"Pfad verlässt den Workspace: {relpath}")
    return p


def list_dir(relpath: str = "") -> dict[str, Any]:
    """Ein Verzeichnis auflisten (Ordner zuerst, alphabetisch)."""
    base = _safe(relpath)
    if not base.exists():
        raise FilesError(f"nicht gefunden: {relpath}")
    if not base.is_dir():
        raise FilesError(f"kein Verzeichnis: {relpath}")
    entries = []
    for child in sorted(base.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
        if child.name.startswith("."):
            continue
        is_dir = child.is_dir()
        entries.append(
            {
                "name": child.name,
                "path": str(child.relative_to(WORKSPACE)),
                "type": "dir" if is_dir else "file",
                "size": None if is_dir else child.stat().st_size,
            }
        )
    rel = str(base.relative_to(WORKSPACE)) if base != WORKSPACE else ""
    return {"path": rel, "entries": entries}


def read_file(relpath: str) -> dict[str, Any]:
    """Textinhalt einer Datei (begrenzt) zurückgeben."""
    p = _safe(relpath)
    if not p.is_file():
        raise FilesError(f"keine Datei: {relpath}")
    size = p.stat().st_size
    truncated = size > MAX_READ_BYTES
    data = p.read_bytes()[:MAX_READ_BYTES]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise FilesError("keine Textdatei — nutze Download")
    return {"path": relpath, "content": text, "truncated": truncated, "size": size}


def write_file(relpath: str, content: str) -> dict[str, Any]:
    """Editor-Speichern: Datei komplett überschreiben (UTF-8)."""
    p = _safe(relpath)
    if p.is_dir():
        raise FilesError(f"ist ein Verzeichnis: {relpath}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": relpath, "size": p.stat().st_size}


def file_path(relpath: str) -> Path:
    """Geprüfter absoluter Pfad einer existierenden Datei (für Downloads)."""
    p = _safe(relpath)
    if not p.is_file():
        raise FilesError(f"keine Datei: {relpath}")
    return p


def make_dir(relpath: str) -> dict[str, Any]:
    """Neues Verzeichnis (inkl. Zwischenebenen) anlegen."""
    p = _safe(relpath)
    if p.exists():
        raise FilesError(f"existiert schon: {relpath}")
    p.mkdir(parents=True)
    return {"path": str(p.relative_to(WORKSPACE))}


def rename(relpath: str, new_relpath: str) -> dict[str, Any]:
    """Datei/Ordner umbenennen bzw. verschieben (beides workspace-intern)."""
    src = _safe(relpath)
    dst = _safe(new_relpath)
    if not src.exists():
        raise FilesError(f"nicht gefunden: {relpath}")
    if dst.exists():
        raise FilesError(f"Ziel existiert schon: {new_relpath}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return {"path": str(dst.relative_to(WORKSPACE))}


def delete(relpath: str) -> dict[str, Any]:
    """Datei oder Ordner (rekursiv) löschen."""
    p = _safe(relpath)
    if p == WORKSPACE:
        raise FilesError("Workspace-Wurzel kann nicht gelöscht werden")
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()
    else:
        raise FilesError(f"nicht gefunden: {relpath}")
    return {"deleted": relpath}


def save_upload(rel_dir: str, filename: str, data: bytes) -> dict[str, Any]:
    """Upload in ein Workspace-Verzeichnis; Dateiname wird auf Basename gekürzt."""
    base = _safe(rel_dir)
    if not base.is_dir():
        raise FilesError(f"kein Verzeichnis: {rel_dir}")
    safe_name = Path(filename or "upload").name
    dest = base / safe_name
    dest.write_bytes(data)
    return {"path": str(dest.relative_to(WORKSPACE)), "size": len(data)}
