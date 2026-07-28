#!/usr/bin/env bash
# Auf dem AGENTEN-PC ausführen (einmalig, als der User, der Claude-Code nutzt).
#
# Registriert den Dashboard-MCP-Server in Claude-Code. Der Server ist auf dem
# Agenten-PC über den Reverse-SSH-Tunnel des Dashboards erreichbar
# (127.0.0.1:<port>, Standard 9000 — muss zum `mcp_port` des Agenten in
# agents.yaml passen, falls dort gesetzt).
#
#   ./setup_agent_pc.sh [port]
#
# Danach hat jede Claude-Code-Sitzung auf diesem PC (interaktiv UND headless
# über den Watcher) die Dashboard-Tools: inbox, ask, answer, send_message,
# send_task, read_responses, ...
set -euo pipefail

PORT="${1:-9000}"
URL="http://127.0.0.1:${PORT}/mcp"

command -v claude >/dev/null 2>&1 || {
  echo "FEHLER: 'claude' nicht im PATH — erst Claude-Code installieren." >&2
  exit 1
}

# --scope user: gilt für alle Projekte dieses Users, nicht nur das aktuelle cwd.
claude mcp add --scope user --transport http dashboard "$URL"

echo
echo "Registriert. Verbindungstest:"
claude mcp list
