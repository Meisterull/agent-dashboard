"""Tool-Scoping + Kanal-Identität für den MCP-Server (Issue #13).

Jeder SSH-Agent bekommt einen EIGENEN Container-Port; der Reverse-Tunnel des
Agenten forwardet auf diesen Port statt auf den freien :9000. Damit kommt die
Identität fälschungssicher aus dem Kanal — der Client kann sie weder mitschicken
noch verfälschen. Auf einem gebundenen Kanal leitet der Server `agent`/`sender`
aus der Verbindung ab und lehnt abweichende Werte ab; optional begrenzt eine
Tool-Allowlist (`tools:` am Agenten in agents.yaml), welche Tools der Kanal
überhaupt sieht.

Quelle der Wahrheit für die Port→Agent-Zuordnung ist der LAUFENDE Server: er
schreibt beim Start die aktive Zuordnung nach MCP_PORT_MAP (JSON), der Tunnel
liest sie und forwardet dorthin. So können Server und Tunnel nie auseinander
laufen, wenn sich agents.yaml zwischen Server-Start und Tunnel-Reconcile ändert.
Beim Neuberechnen übernimmt compute_scopes die Ports aus der vorhandenen Map,
damit sie beim Umsortieren von agents.yaml NICHT wandern — sonst zeigt ein noch
offener Tunnel für bis zu 60 s auf den Kanal eines anderen Agenten.

Reine Logik (stdlib) — testbar ohne mcp/fastapi (backend/tests/test_mcp_scope.py).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.config import DATA_CONFIG_DIR

MCP_PORT = int(os.environ.get("MCP_PORT", "9000"))
# Gebundene Kanäle liegen standardmäßig oberhalb des freien Ports (9100, 9101, …).
SCOPED_PORT_BASE = int(os.environ.get("MCP_SCOPED_PORT_BASE", str(MCP_PORT + 100)))
PORT_MAP_PATH = Path(os.environ.get("MCP_PORT_MAP", str(DATA_CONFIG_DIR / "mcp_ports.json")))

# Kanonische Tool-Namen — Allowlist-Einträge außerhalb dieser Menge sind Tippfehler
# und werden (mit Warnung) ignoriert statt still nichts zu erlauben.
KNOWN_TOOLS = frozenset({
    "list_agents", "send_task", "create_task", "read_responses",
    "claim_task", "complete_task",
    "send_message", "ask", "answer", "inbox", "mark_read",
    "write_project_file", "read_project_file",
    "list_integrations", "call_integration",
    "list_rollen",
})


# Verbindungsarten, die einen eigenen gebundenen Kanal bekommen.
#   ssh   — das Dashboard baut den Tunnel auf, der Agent ist erreichbar.
#   token — der Agent meldet sich SELBST über HTTPS an (Issue #32): ein
#           Notebook hinter NAT, ein Gerät mal im LAN und mal im VPN. Es gibt
#           keinen Tunnel, die Identität kommt aus dem Token statt aus dem
#           Kanalaufbau — der Port bleibt derselbe Mechanismus.
KANAL_TYPEN = frozenset({"ssh", "token"})

# Grundmenge für Token-Agenten ohne eigene `tools:`-Liste. Anders als bei SSH
# gibt es hier NICHT alles: Ein Token liegt in einer Datei auf einem Gerät, das
# das Dashboard nicht kennt, und ist leichter zu verlieren als ein SSH-Schlüssel
# auf einem bekannten Host. Wer mehr braucht, schreibt es ausdrücklich hin.
TOKEN_GRUNDTOOLS = [
    "inbox", "mark_read", "claim_task", "complete_task",
    "send_message", "ask", "answer", "list_agents",
]


class ScopeError(ValueError):
    """Aufruf verletzt die Kanal-Bindung (falscher agent/sender)."""


def resolve_ident(bound: str, given: str | None, feld: str) -> str:
    """Identitäts-Parameter gegen die Kanal-Bindung auflösen.

    None/leer -> gebundener Name; abweichender Wert -> ScopeError (Issue #13:
    ablehnen statt still akzeptieren)."""
    if given is None or given == "" or given == bound:
        return bound
    raise ScopeError(
        f"Dieser Kanal ist an '{bound}' gebunden — {feld}='{given}' ist nicht erlaubt."
    )


def _tools_of(agent: dict[str, Any]) -> list[str] | None:
    """Allowlist eines Eintrags: `tools:` am Agenten (wie im Issue) oder in der
    connection. None = nicht konfiguriert = alle Tools."""
    conn = agent.get("connection") or {}
    for quelle in (agent.get("tools"), conn.get("tools")):
        if quelle is not None:
            return [str(t) for t in quelle]
    return None


def compute_scopes(
    agents: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Port- und Tool-Zuordnung für alle tunnel-fähigen (SSH-)Agenten.

    Liefert (scopes, warnungen); scopes: name -> {"port": int, "tools": list|None}.
    Explizite Ports via `mcp_local_port` in der connection; sonst der Port aus
    der bestehenden Port-Map (Stabilität, M10) und erst dann fortlaufend ab
    SCOPED_PORT_BASE. Kollisionen werden automatisch aufgelöst (Warnung).

    Warum die alte Map gewinnt: die Auto-Vergabe folgte bisher der Reihenfolge
    in agents.yaml — ein neuer Eintrag OBEN verschob alle Ports darunter. Bis
    der Tunnel das (nach bis zu 60 s) merkt, forwardet er auf den Kanal eines
    ANDEREN Agenten: fremde Identität, fremde Tool-Allowlist."""
    scopes: dict[str, dict[str, Any]] = {}
    warnungen: list[str] = []
    belegt = {MCP_PORT}

    kandidaten = [
        a for a in agents
        if a.get("name") and (a.get("connection") or {}).get("type") in KANAL_TYPEN
    ]

    # Erst explizite Ports binden, damit Auto-Vergabe ihnen nicht in die Quere kommt.
    for agent in kandidaten:
        name = agent["name"]
        conn = agent.get("connection") or {}
        port = conn.get("mcp_local_port")
        if port is None:
            continue
        port = int(port)
        if port in belegt:
            warnungen.append(
                f"{name}: mcp_local_port {port} ist schon vergeben — Port wird automatisch zugewiesen."
            )
            continue
        belegt.add(port)
        scopes[name] = {"port": port}

    # Dann die bereits vergebenen Ports der letzten Runde übernehmen (nur für
    # Agenten, die es noch gibt — Ports verschwundener Agenten bleiben frei).
    bekannt = read_port_map()
    for agent in kandidaten:
        name = agent["name"]
        if name in scopes:
            continue
        alt = bekannt.get(name)
        if alt is None or alt in belegt or alt == MCP_PORT:
            continue
        belegt.add(alt)
        scopes[name] = {"port": alt}

    naechster = SCOPED_PORT_BASE
    for agent in kandidaten:
        name = agent["name"]
        if name not in scopes:
            while naechster in belegt:
                naechster += 1
            belegt.add(naechster)
            scopes[name] = {"port": naechster}

        tools = _tools_of(agent)
        if tools is None and (agent.get("connection") or {}).get("type") == "token":
            tools = list(TOKEN_GRUNDTOOLS)
        if tools is not None:
            unbekannt = [t for t in tools if t not in KNOWN_TOOLS]
            if unbekannt:
                warnungen.append(
                    f"{name}: unbekannte Tools in Allowlist ignoriert: {', '.join(unbekannt)}"
                )
            tools = [t for t in tools if t in KNOWN_TOOLS]
        scopes[name]["tools"] = tools

    return scopes, warnungen


def write_port_map(scopes: dict[str, dict[str, Any]]) -> None:
    """Aktive Zuordnung persistieren (atomar) — der Tunnel liest sie."""
    PORT_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PORT_MAP_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "legacy_port": MCP_PORT,
                "agents": {name: sc["port"] for name, sc in scopes.items()},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(tmp, PORT_MAP_PATH)


def read_port_map() -> dict[str, int]:
    """name -> Container-Port des gebundenen Kanals; {} wenn (noch) keine Map da."""
    try:
        data = json.loads(PORT_MAP_PATH.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in (data.get("agents") or {}).items()}
    except (OSError, ValueError):
        return {}
