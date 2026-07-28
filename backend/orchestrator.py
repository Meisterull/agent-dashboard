"""Orchestrator-Loop (CLI) — Punkt 2 der Roadmap.

Dünne CLI über dem gemeinsamen Kern (app/orchestrator_core.py). Provider wählbar
über ORCH_PROVIDER (anthropic|ollama). Für Ollama ist KEIN API-Key nötig:

  ORCH_PROVIDER=ollama OLLAMA_BASE_URL=http://localhost:11434 \\
  OLLAMA_MODEL=gpt-oss:120b-cloud python orchestrator.py

Voraussetzung: MCP-Server läuft (python -m mcp_server) auf MCP_URL.
Start:  cd backend && python orchestrator.py
"""
from __future__ import annotations

import asyncio
import os
import sys

from app import llm
from app.orchestrator_core import MCP_URL, mcp_session, run_turn


async def main() -> int:
    cfg = llm.provider_from_env()
    if llm.needs_api_key(cfg) and not os.environ.get("ANTHROPIC_API_KEY"):
        print("Fehler: ANTHROPIC_API_KEY fehlt (oder ORCH_PROVIDER=ollama setzen).", file=sys.stderr)
        return 1

    print(f"Provider: {cfg['provider']} | Modell: {cfg['model']}")
    print(f"Verbinde mit MCP-Server: {MCP_URL}")
    async with mcp_session() as (session, tools):
        print(f"MCP-Tools geladen: {', '.join(t['name'] for t in tools)}")
        print("Chat mit dem Orchestrator (leer + Enter beendet).\n")

        messages: list[dict] = []
        while True:
            try:
                user = input("Du: ").strip()
            except EOFError:
                break
            if not user:
                break
            messages.append({"role": "user", "content": user})
            result = await run_turn(session, tools, messages, cfg)
            print(f"\nOrchestrator: {result['text']}\n")
            for call in result["tool_calls"]:
                print(f"  (Tool benutzt: {call['name']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
