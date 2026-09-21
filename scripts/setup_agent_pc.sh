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
# send_task, read_responses, send_file, ... — und die Skills aus skills/
# (liegen sie neben diesem Script, s. u.).
set -euo pipefail

PORT="${1:-9000}"
URL="http://127.0.0.1:${PORT}/mcp"

command -v claude >/dev/null 2>&1 || {
  echo "FEHLER: 'claude' nicht im PATH — erst Claude-Code installieren." >&2
  exit 1
}

# Idempotent (N14): ein zweiter Lauf (anderer Port, neue Installation) würde
# sonst an "server already exists" scheitern. Alten Eintrag erst entfernen —
# fehlt er, ist das kein Fehler.
claude mcp remove --scope user dashboard >/dev/null 2>&1 || true

# --scope user: gilt für alle Projekte dieses Users, nicht nur das aktuelle cwd.
claude mcp add --scope user --transport http dashboard "$URL"

# Skills des Dashboards für Claude-Code auf diesem PC (User-Ebene, gilt in
# jedem Projekt): z. B. `dateiaustausch` — wie send_file und die
# Austausch-Ordner funktionieren. Nur wenn der skills/-Ordner des Repos neben
# diesem Script liegt (Checkout); wer das Script einzeln kopiert hat, kopiert
# skills/<name>/ von Hand nach ~/.claude/skills/. Idempotent: überschreibt die
# eigene Kopie, fasst fremde Skills nicht an.
SKILLS_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/skills"
if [[ -d "$SKILLS_SRC" ]]; then
  mkdir -p "$HOME/.claude/skills"
  for skill in "$SKILLS_SRC"/*/; do
    [[ -f "$skill/SKILL.md" ]] || continue
    name="$(basename "$skill")"
    rm -rf "$HOME/.claude/skills/$name"
    cp -r "$skill" "$HOME/.claude/skills/$name"
    echo "Skill installiert: $name"
  done
fi

echo
echo "Registriert. Verbindungstest:"
claude mcp list
