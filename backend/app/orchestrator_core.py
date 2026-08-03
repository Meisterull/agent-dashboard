"""Gemeinsamer Orchestrator-Kern für CLI und FastAPI.

Hält die eine Wahrheit: System-Prompt, MCP-Anbindung und die Agentic-Loop,
die Tool-Calls über die MCP-Session ausführt. `orchestrator.py` (CLI) und
`main.py` (HTTP) bauen beide darauf auf, statt die Loop zu duplizieren.
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app import llm

MCP_URL = os.environ.get(
    "MCP_URL",
    f"http://{os.environ.get('MCP_HOST', '127.0.0.1')}:{os.environ.get('MCP_PORT', '9000')}/mcp",
)

SYSTEM = """\
Du bist der Orchestrator/Koordinator eines Agent-Dashboards. Der Nutzer spricht \
mit dir im Dashboard-Chat; du planst Aufgaben und koordinierst mehrere \
Claude-Code-Agenten, die auf eigenen Rechnern laufen (per SSH angebunden). \
Jeder Agent hat dieselben MCP-Tools wie du und prüft selbst seine Inbox — \
der Nutzer soll niemandem hinterherlaufen müssen: Du legst Aufträge, Hinweise \
und Rückfragen in die Mailboxen, die Agenten holen sie sich.

Delegation:
- `list_agents` — welche Agenten (Mailboxes) existieren. Verlasse dich auf \
diese Liste, nicht auf Annahmen.
- `send_task(to, instruction, sender?, project?)` — Arbeitsauftrag an einen \
Agenten. Nur Tasks werden auf dem Agenten-Rechner ausgeführt. Formuliere sie \
selbstständig ausführbar: Ziel, nötiger Kontext, erwartetes Ergebnis.
- Ergebnisse erledigter Aufträge landen als kind="response" in der Inbox des \
Auftraggebers — für dich: `inbox("orchestrator")`; Verarbeitetes danach mit \
`mark_read("orchestrator", id)` archivieren.
- `read_responses(worker, for_sender?)` — Outbox-Archiv eines Bearbeiters \
lesen (worker = der Agent, der gearbeitet hat, nicht du selbst).

Agent-↔-Agent (damit niemand von Hand zwischen Fenstern vermitteln muss):
- `send_message(to, text, sender?)` — informativer Hinweis.
- `ask(to, question, sender?, reply_to?)` — Rückfrage, die eine Antwort braucht \
(erscheint im Dashboard als offene Rückfrage).
- `answer(to, text, sender?, reply_to)` — eine Rückfrage beantworten.
- `inbox(agent, kind?)` — sehen, was einem Agenten geschickt wurde.

Projektdateien: `write_project_file` / `read_project_file` — gemeinsamer \
Austauschordner der Agenten unter /workspace/projects/<projekt>.
Integrationen: `list_integrations`, dann `call_integration(name, method, path, \
body?)` für in integrations.yaml konfigurierte HTTP-Endpunkte.

Arbeitsweise:
- Zerlege größere Vorhaben in klar geschnittene Aufträge pro Agent.
- Hängt Agent B von Agent A ab, kündige das B per send_message an, damit er \
weiß, worauf er wartet.
- Prüfe Rückmeldungen über `inbox("orchestrator")` (bzw. read_responses), \
bevor du dem Nutzer Vollzug meldest — melde nur, was wirklich zurückkam.
- Du führst selbst KEINEN Code auf den Ziel-Rechnern aus — du delegierst und \
koordinierst. Antworte auf Deutsch und fasse knapp zusammen, was du veranlasst \
hast und was noch offen ist."""


def mcp_tools_neutral(mcp_tools) -> list[dict]:
    """MCP-Tool-Definitionen -> neutrales {name, description, input_schema}."""
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.inputSchema,
        }
        for t in mcp_tools
    ]


def mcp_result_to_text(result) -> tuple[str, bool]:
    """MCP-Tool-Ergebnis -> (Text, is_error) fürs tool_result."""
    parts = [getattr(b, "text", None) for b in result.content]
    parts = [p for p in parts if p is not None]
    if not parts and getattr(result, "structuredContent", None) is not None:
        parts.append(json.dumps(result.structuredContent, ensure_ascii=False))
    return ("\n".join(parts) or "(leeres Ergebnis)", bool(getattr(result, "isError", False)))


@asynccontextmanager
async def mcp_session():
    """Öffnet eine MCP-Session (Streamable-HTTP) und liefert (session, tools)."""
    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            yield session, mcp_tools_neutral(listed.tools)


async def run_turn(
    session: ClientSession,
    tools: list[dict],
    messages: list[dict],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Eine Nutzer-Eingabe vollständig abarbeiten (inkl. aller Tool-Calls).

    Provider-neutral (Claude oder Ollama, je nach `cfg`/ORCH_PROVIDER). Tool-Calls
    laufen über die MCP-Session. `messages` wird in-place fortgeschrieben.
    """
    cfg = cfg or llm.provider_from_env()

    async def call_tool(name: str, inp: dict) -> str:
        result = await session.call_tool(name, inp or {})
        return mcp_result_to_text(result)[0]

    return await llm.run_turn(cfg, SYSTEM, messages, tools, call_tool)
