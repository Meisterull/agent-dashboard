"""Zentraler SSH-Verbindungsaufbau mit Host-Key-Pinning.

Trust-on-first-use (TOFU): Beim ersten Kontakt wird der Host-Key des Ziels
in /workspace/config/known_hosts eingetragen; ab dann muss er passen. Ändert
sich der Key (Server neu aufgesetzt — oder ein Man-in-the-Middle), schlägt
die Verbindung mit einer klaren Meldung fehl, statt still zu vertrauen.
Genutzt von ssh_bridge (Terminal), remote_files (SFTP) und mcp_tunnel.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))
KNOWN_HOSTS = WORKSPACE / "config" / "known_hosts"
CONNECT_TIMEOUT = 10


class HostKeyChanged(Exception):
    """Gepinnter Host-Key stimmt nicht mehr — bewusst NICHT automatisch heilen."""


def _host_pattern(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


def _has_entry(pattern: str) -> bool:
    if not KNOWN_HOSTS.exists():
        return False
    for line in KNOWN_HOSTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if pattern in line.split()[0].split(","):
            return True
    return False


async def connect_ssh(conn_cfg: dict[str, Any], **extra):
    """SSH-Verbindung nach agents.yaml-Eintrag, Host-Key gepinnt via TOFU.

    `extra` wird an asyncssh.connect durchgereicht (z.B. keepalive_interval).
    """
    import asyncssh

    host = conn_cfg["host"]
    port = int(conn_cfg.get("port", 22))

    if not KNOWN_HOSTS.exists():
        KNOWN_HOSTS.parent.mkdir(parents=True, exist_ok=True)
        KNOWN_HOSTS.touch()

    kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "username": conn_cfg.get("user"),
        "known_hosts": str(KNOWN_HOSTS),
        **extra,
    }
    key_file = conn_cfg.get("key_file")
    if key_file and Path(key_file).exists():
        kwargs["client_keys"] = [key_file]

    try:
        return await asyncio.wait_for(asyncssh.connect(**kwargs), CONNECT_TIMEOUT)
    except asyncssh.HostKeyNotVerifiable as exc:
        pattern = _host_pattern(host, port)
        if _has_entry(pattern):
            raise HostKeyChanged(
                f"Host-Key von {pattern} hat sich geändert! Wenn der Rechner wirklich "
                f"neu aufgesetzt wurde: Eintrag in {KNOWN_HOSTS} löschen. ({exc})"
            ) from exc
        # erster Kontakt: Key holen, pinnen, erneut verbinden
        key = await asyncio.wait_for(
            asyncssh.get_server_host_key(host, port), CONNECT_TIMEOUT
        )
        entry = f"{pattern} {key.export_public_key().decode().strip()}\n"
        with open(KNOWN_HOSTS, "a", encoding="utf-8") as f:
            f.write(entry)
        return await asyncio.wait_for(asyncssh.connect(**kwargs), CONNECT_TIMEOUT)
