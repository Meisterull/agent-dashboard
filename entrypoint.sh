#!/bin/bash
# Root-Setup vor dem Start. Läuft als root, droppt danach via supervisord
# die einzelnen App-Prozesse auf den 'app'-User.
set -euo pipefail

SSL_DIR=${SSL_DIR:-/app/ssl}
WORKSPACE_DIR=${WORKSPACE_DIR:-/workspace}
DATA_CONFIG_DIR=${DATA_CONFIG_DIR:-/workspace/config}
# Extern veröffentlichter HTTPS-Port (Host-Mapping in docker-compose). Muss in
# den HTTP->HTTPS-Redirect, sonst landet der Browser auf 443 statt 8443.
EXTERNAL_HTTPS_PORT=${EXTERNAL_HTTPS_PORT:-8443}

log() { echo "[entrypoint] $*"; }

# 1. SSL-Zertifikat: self-signed Platzhalter, falls keins vorhanden.
#    /app/ssl ist read-only gemountet -> Platzhalter kommen nach /workspace/ssl,
#    damit der Container auch ohne vorab gelegtes Zertifikat startet.
RUNTIME_SSL_DIR="$WORKSPACE_DIR/ssl"
if [ -f "$SSL_DIR/fullchain.pem" ] && [ -f "$SSL_DIR/privkey.pem" ]; then
    # Kopie statt Direktnutzung: $SSL_DIR ist read-only gemountet, das
    # chown in 3a würde dort scheitern.
    log "SSL-Zertifikat aus $SSL_DIR wird verwendet (Kopie nach $RUNTIME_SSL_DIR)."
    mkdir -p "$RUNTIME_SSL_DIR"
    cp -f "$SSL_DIR/fullchain.pem" "$SSL_DIR/privkey.pem" "$RUNTIME_SSL_DIR/"
    if [ -f "$SSL_DIR/ca.crt" ]; then
        cp -f "$SSL_DIR/ca.crt" "$RUNTIME_SSL_DIR/"  # für den /ca.crt-Download
    fi
    EFFECTIVE_SSL_DIR="$RUNTIME_SSL_DIR"
else
    log "Kein Zertifikat in $SSL_DIR — erstelle self-signed Platzhalter in $RUNTIME_SSL_DIR."
    mkdir -p "$RUNTIME_SSL_DIR"
    if [ ! -f "$RUNTIME_SSL_DIR/fullchain.pem" ]; then
        # subjectAltName, sonst wirft Chrome ERR_CERT_COMMON_NAME_INVALID und
        # sperrt auf der Seite "powerful features" wie die Clipboard-API.
        # Bleibt self-signed mit Warnung — der saubere Weg ist make_cert.sh.
        openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout "$RUNTIME_SSL_DIR/privkey.pem" \
            -out "$RUNTIME_SSL_DIR/fullchain.pem" \
            -subj "/CN=${DOMAIN:-agent-dashboard.local}" \
            -addext "subjectAltName=DNS:${DOMAIN:-agent-dashboard.local},IP:127.0.0.1" 2>/dev/null
    fi
    EFFECTIVE_SSL_DIR="$RUNTIME_SSL_DIR"
fi

# 2. Workspace-Struktur anlegen.
mkdir -p \
    "$WORKSPACE_DIR/projects" \
    "$WORKSPACE_DIR/mailboxes" \
    "$WORKSPACE_DIR/logs" \
    "$WORKSPACE_DIR/uploads" \
    "$DATA_CONFIG_DIR"

# 2a. Vorlagen-Config beim ersten Start nach /workspace/config kopieren,
#     damit das Dashboard sie editieren kann (read-only /app/config bleibt Vorlage).
if [ -d /app/config ]; then
    for f in /app/config/*; do
        [ -e "$f" ] || continue
        base=$(basename "$f")
        if [ ! -e "$DATA_CONFIG_DIR/$base" ]; then
            # -r + tolerant (Review P0-8): ein Unterverzeichnis (z.B. das von
            # make_agent_token.sh angelegte config/tokens/) ließ das nackte cp
            # scheitern, und set -e schickte den Container in eine
            # Neustart-Schleife, bevor supervisord überhaupt startete.
            cp -r "$f" "$DATA_CONFIG_DIR/$base" \
                && log "Config-Vorlage kopiert: $base" \
                || log "WARNUNG: Config-Vorlage $base nicht kopierbar — übersprungen."
        fi
    done
fi

# 3. Besitzrechte: App-Prozesse laufen als 'app' und müssen schreiben können.
chown -R app:app "$WORKSPACE_DIR"

# 3a. SSL-Key muss vom nginx-Master lesbar sein. Der Master läuft als root,
#     hat aber bei cap_drop:ALL kein DAC_OVERRIDE — eine 600-Datei, die 'app'
#     gehört, kann er dann NICHT lesen. Also Zertifikat root-eigen lassen
#     (nginx-Worker droppen auf www-data und brauchen den Key nicht).
chown -R root:root "$EFFECTIVE_SSL_DIR"

# 4. nginx-Config rendern (SSL-Pfad einsetzen).
export EFFECTIVE_SSL_DIR EXTERNAL_HTTPS_PORT
envsubst '${EFFECTIVE_SSL_DIR} ${EXTERNAL_HTTPS_PORT}' \
    < /app/nginx/agent-dashboard.conf.template \
    > /etc/nginx/sites-enabled/agent-dashboard
rm -f /etc/nginx/sites-enabled/default

log "Setup fertig. Starte supervisord."
# 5. Prozessaufsicht übernehmen (nginx, api, mcp, mcp-tunnel).
#    exec => supervisord wird PID 1. Darauf baut der fatal_exit-Eventlistener
#    in supervisord.conf: Er signalisiert seinem Elternprozess, damit ein
#    endgültig gescheiterter Dienst den Container beendet, statt ihn "gesund
#    tot" weiterlaufen zu lassen.
exec "$@"
