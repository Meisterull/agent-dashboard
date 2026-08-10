"""Reverse-SSH-Tunnel: bringt den MCP-Server auf die Agenten-PCs.

Agent-↔-Agent-Transport, Variante 1: Der Container öffnet zu jedem in
agents.yaml konfigurierten SSH-Agenten einen Reverse-Port-Forward
(127.0.0.1:<mcp_port> auf dem Agenten-PC -> GEBUNDENER Kanal des Agenten im
Container, Issue #13). Claude-Code auf dem Agenten-PC registriert den Server
dann einmalig als `http://127.0.0.1:<mcp_port>/mcp` (scripts/setup_agent_pc.sh)
und kann selbst inbox/ask/answer/send_message nutzen — niemand muss mehr von
Hand zwischen den Instanzen vermitteln.

Kanal-Identität: Das Forward-Ziel ist nicht mehr pauschal :9000, sondern der
Port des Agenten aus der Port-Map (mcp_ports.json), die der MCP-Server beim
Start schreibt — die Identität kommt damit fälschungssicher aus dem Kanal.
Fehlt ein Agent in der Map (Server älter als der Eintrag): Fallback auf den
freien Kanal :9000, AUSSER der Agent hat eine Tool-Allowlist konfiguriert —
dann wird der Tunnel ausgesetzt statt die Allowlist zu umgehen.

Sicherheitsmodell: Alle MCP-Ports bleiben im Container auf 127.0.0.1 und werden
nirgends veröffentlicht. Erreichbar sind sie ausschließlich über die
SSH-Tunnel (Key-Auth), auf den Agenten-PCs wiederum nur auf deren Loopback.

Betrieb: supervisord-Programm `mcp-tunnel`, Gate MCP_TUNNEL_ENABLED (.env).
agents.yaml wird periodisch neu gelesen — neue/geänderte/entfernte Agenten
brauchen keinen Container-Neustart. Einträge ohne existierende key_file
(Platzhalter) werden übersprungen. Pro Agent optional `mcp_port` in der
connection, falls der Standardport auf dem Ziel-PC belegt ist.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from app.config import AGENTS_YAML

MCP_PORT = int(os.environ.get("MCP_PORT", "9000"))
REMOTE_PORT_DEFAULT = int(os.environ.get("MCP_TUNNEL_REMOTE_PORT", str(MCP_PORT)))
RECONNECT_DELAY = 15  # Sekunden zwischen Verbindungsversuchen
RECONCILE_INTERVAL = 60  # Sekunden, bis agents.yaml-Änderungen greifen


def _load_ssh_agents() -> dict[str, dict[str, Any]]:
    """name -> Tunnel-Config aller SSH-Agenten, deren Key-Datei existiert.

    `ziel_port` = gebundener Kanal des Agenten aus der Port-Map des MCP-Servers;
    None solange die Map den Agenten (noch) nicht kennt. `nur_gebunden` = Agent
    hat eine Tool-Allowlist — für ihn ist der freie Kanal kein Fallback."""
    from app import mcp_scope
    from app.config import load_agents_full

    try:
        agents = load_agents_full()  # agents.yaml + agents_ui.yaml
    except Exception:  # noqa: BLE001 — kaputte Config darf den Dienst nicht killen
        return {}
    port_map = mcp_scope.read_port_map()
    out: dict[str, dict[str, Any]] = {}
    for agent in agents:
        conn = agent.get("connection") or {}
        name = agent.get("name")
        if not name or conn.get("type") != "ssh" or not conn.get("host"):
            continue
        key_file = conn.get("key_file")
        if not key_file or not Path(key_file).exists():
            continue  # Platzhalter ohne Secret -> kein Tunnel, kein Fehlerspam
        out[name] = {
            "host": conn["host"],
            "port": int(conn.get("port", 22)),
            "user": conn.get("user"),
            "key_file": key_file,
            "remote_port": int(conn.get("mcp_port", REMOTE_PORT_DEFAULT)),
            "ziel_port": port_map.get(name),
            "nur_gebunden": mcp_scope._tools_of(agent) is not None,
        }
    return out


async def _tunnel_loop(name: str, cfg: dict[str, Any]) -> None:
    """Einen Reverse-Forward offen halten; bei Abriss mit Abstand neu verbinden."""
    from app.ssh_connect import connect_ssh

    last_error: str | None = None
    while True:
        try:
            conn = await connect_ssh(
                cfg,  # host/port/user/key_file wie agents.yaml
                keepalive_interval=30,  # tote Verbindungen erkennen statt ewig hängen
            )
            async with conn:
                await conn.forward_remote_port(
                    "127.0.0.1", cfg["remote_port"], "127.0.0.1", cfg["ziel_port"]
                )
                kanal = "frei" if cfg["ziel_port"] == MCP_PORT else "gebunden"
                print(
                    f"[mcp-tunnel] {name}: aktiv — auf {cfg['host']} lauscht "
                    f"127.0.0.1:{cfg['remote_port']} -> Container-MCP :{cfg['ziel_port']} ({kanal})",
                    flush=True,
                )
                last_error = None
                await conn.wait_closed()
            print(
                f"[mcp-tunnel] {name}: Verbindung geschlossen — neuer Versuch in {RECONNECT_DELAY}s",
                flush=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — Reconnect-Loop, nie crashen
            msg = f"{type(exc).__name__}: {exc}"
            if msg != last_error:  # nur Zustandswechsel loggen, kein Retry-Spam
                print(
                    f"[mcp-tunnel] {name}: {msg} — weitere Versuche alle {RECONNECT_DELAY}s",
                    flush=True,
                )
                last_error = msg
        await asyncio.sleep(RECONNECT_DELAY)


async def main() -> None:
    print(f"[mcp-tunnel] gestartet — Agenten aus {AGENTS_YAML}", flush=True)
    running: dict[str, tuple[tuple, asyncio.Task]] = {}
    gewarnt: set[str] = set()
    while True:
        agents = _load_ssh_agents()
        for name, cfg in list(agents.items()):
            if cfg["ziel_port"] is None:
                if cfg["nur_gebunden"]:
                    # Allowlist konfiguriert, aber kein gebundener Kanal in der
                    # Port-Map -> Tunnel aussetzen statt die Allowlist über den
                    # freien Kanal zu umgehen (Server-Neustart nötig).
                    if name not in gewarnt:
                        print(
                            f"[mcp-tunnel] {name}: hat Tool-Allowlist, aber keinen Kanal in "
                            f"der Port-Map — Tunnel ausgesetzt (MCP-Server neu starten).",
                            flush=True,
                        )
                        gewarnt.add(name)
                    agents.pop(name)
                    continue
                if name not in gewarnt:
                    print(
                        f"[mcp-tunnel] {name}: kein Kanal in der Port-Map — Fallback auf "
                        f"freien Kanal :{MCP_PORT} (MCP-Server neu starten für Bindung).",
                        flush=True,
                    )
                    gewarnt.add(name)
                cfg["ziel_port"] = MCP_PORT
            else:
                gewarnt.discard(name)
        for name, cfg in agents.items():
            sig = tuple(sorted(cfg.items()))
            if name in running and running[name][0] == sig:
                continue
            if name in running:  # Config geändert -> Tunnel neu aufbauen
                running[name][1].cancel()
            running[name] = (sig, asyncio.create_task(_tunnel_loop(name, cfg)))
        for name in list(running):
            if name not in agents:
                running.pop(name)[1].cancel()
                print(f"[mcp-tunnel] {name}: aus agents.yaml entfernt — Tunnel beendet", flush=True)
        await asyncio.sleep(RECONCILE_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
