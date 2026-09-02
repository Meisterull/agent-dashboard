#!/usr/bin/env bash
# Deploy, das einen Verbindungsabriss überlebt.
#
#   scripts/deploy.sh          baut das Image neu und startet den Container
#
# Der Witz: Wer das Dashboard ÜBER das Dashboard (oder über SSH) deployt,
# sägt sich mit dem Container-Neustart die eigene Sitzung ab — und mit ihr
# stürbe normalerweise auch das laufende `docker compose up`. Deshalb löst
# sich das Script sofort von der Sitzung (setsid + nohup), schreibt alles
# nach deploy.log und läuft dort zu Ende, egal was mit der Verbindung ist.
#
#   tail -f deploy.log         zum Zuschauen (nach dem Reconnect)
#
# Reihenfolge bewusst: erst `docker compose build` (kann minutenlang laufen,
# der alte Container bedient währenddessen weiter), erst dann das kurze
# `up -d`. Ein Build-Fehler lässt den laufenden Container unangetastet.
set -euo pipefail

SCRIPT="$(readlink -f "${BASH_SOURCE[0]}")"
REPO="$(cd "$(dirname "$SCRIPT")/.." && pwd)"
LOG="${DEPLOY_LOG:-$REPO/deploy.log}"
CONTAINER="agent-dashboard"

if [[ "${1:-}" != "--lauf" ]]; then
    # Stufe 1: sich selbst abgekoppelt neu starten und sofort zurückkehren.
    setsid nohup bash "$SCRIPT" --lauf >>"$LOG" 2>&1 < /dev/null &
    echo "Deploy läuft abgekoppelt weiter (PID $!) — Verbindungsabriss ist egal."
    echo "Zuschauen:  tail -f $LOG"
    exit 0
fi

# --- Stufe 2: der eigentliche Lauf, nur noch mit dem Log verbunden ----------
zeit() { date '+%F %T'; }
echo "==== $(zeit) Deploy startet (Commit $(git -C "$REPO" log -1 --format=%h 2>/dev/null || echo '?')) ===="

fehlschlag() {
    echo "==== $(zeit) DEPLOY FEHLGESCHLAGEN (Schritt: $1) ===="
    docker logs --tail 30 "$CONTAINER" 2>&1 | sed 's/^/[container] /' || true
    exit 1
}

cd "$REPO"
echo "---- $(zeit) docker compose build ----"
docker compose build || fehlschlag "build"

echo "---- $(zeit) docker compose up -d ----"
docker compose up -d || fehlschlag "up"

echo "---- $(zeit) warte auf Healthcheck ----"
for _ in $(seq 1 60); do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo wartet)"
    if [[ "$status" == "healthy" ]]; then
        echo "==== $(zeit) DEPLOY OK — Container ist healthy ===="
        docker compose ps
        exit 0
    fi
    sleep 3
done
echo "Healthcheck nach 180 s: $status"
fehlschlag "healthcheck"
