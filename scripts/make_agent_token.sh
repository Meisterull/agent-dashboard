#!/usr/bin/env bash
# Erzeugt den Zugangs-Token für einen Agenten, der sich über HTTPS meldet
# (Issue #32) — für Geräte ohne SSH-Tunnel: Windows-Notebook mit Claude
# Desktop, Rechner hinter NAT, alles was das Dashboard nicht erreichen kann.
#
#   scripts/make_agent_token.sh PMNB029
#
# Der Token landet in config/tokens/<agent>.token (nur für den Eigentümer
# lesbar) und NICHT in agents.yaml — dort steht nur der Pfad, genau wie bei den
# SSH-Schlüsseln. So bleibt die Konfiguration teilbar.
set -euo pipefail

NAME="${1:-}"
if [[ -z "$NAME" ]]; then
    echo "Aufruf: $0 <agentname>" >&2
    exit 1
fi
if [[ ! "$NAME" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "Ungültiger Agentname: $NAME" >&2
    exit 1
fi

WURZEL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ZIEL_DIR="$WURZEL/config/tokens"
ZIEL="$ZIEL_DIR/$NAME.token"

mkdir -p "$ZIEL_DIR"
chmod 700 "$ZIEL_DIR"

if [[ -e "$ZIEL" ]]; then
    read -rp "$ZIEL existiert. Ersetzen? Der alte Token gilt danach nicht mehr. [j/N] " antwort
    [[ "$antwort" == "j" || "$antwort" == "J" ]] || { echo "abgebrochen"; exit 0; }
fi

# 32 Byte Zufall, base64url — deutlich über der Mindestlänge, die das Backend
# verlangt, und ohne Zeichen, die in Kommandozeilen Ärger machen.
python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$ZIEL"
chmod 600 "$ZIEL"

TOKEN="$(cat "$ZIEL")"

# Der API-Prozess im Container läuft als uid 10001 (app) und muss die
# Datei LESEN können — mit Eigentümer $USER und chmod 600 war der
# dokumentierte Weg sonst fail-closed tot (Review P2).
if ! chown 10001:10001 "$ZIEL" 2>/dev/null; then
    if ! sudo chown 10001:10001 "$ZIEL"; then
        echo "WARNUNG: chown fehlgeschlagen — bitte manuell:" >&2
        echo "  sudo chown 10001:10001 $ZIEL" >&2
    fi
fi
cat <<HINWEIS

Token für '$NAME' liegt in:
  $ZIEL

In config/agents.yaml eintragen (Pfad IM CONTAINER, nicht auf dem Host):

  - name: $NAME
    description: "meldet sich selbst über HTTPS"
    role: worker
    connection:
      type: token
      token_file: /app/config/tokens/$NAME.token

Danach den MCP-Dienst neu starten, damit der Kanal entsteht:
  docker compose exec app supervisorctl restart mcp

Auf dem Gerät des Agenten einrichten — Claude Code:
  claude mcp add --scope user --transport http dashboard \\
    https://<dashboard>/mcp/$NAME --header "Authorization: Bearer $TOKEN"

Claude Desktop (über die stdio-Brücke):
  npx mcp-remote https://<dashboard>/mcp/$NAME \\
    --header "Authorization: Bearer $TOKEN"

Beide brauchen die lokale CA (ssl/ca.crt) im Zertifikatsspeicher — dieselbe,
die auch der Browser importiert hat.

HINWEIS
