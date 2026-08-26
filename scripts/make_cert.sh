#!/bin/bash
# Lokale CA + Server-Zertifikat für das Dashboard erzeugen (LAN-Betrieb ohne
# öffentliche Domain). Die CA wird nur beim ersten Lauf angelegt; das
# Server-Zertifikat (825 Tage — mehr mögen Mobilgeräte nicht) kann jederzeit
# neu ausgestellt werden, ohne dass Handys die CA neu importieren müssen.
#
# Aufruf:  scripts/make_cert.sh [domain] [ip-oder-domain ...]
# Ergebnis in ssl/: ca.crt (aufs Handy!), fullchain.pem, privkey.pem
# Danach: docker compose restart
set -euo pipefail

# ssl/ ist gitignored, existiert auf einem frischen Clone also nicht — ohne
# mkdir -p scheiterte das Script hier mit "No such file or directory".
SSL_DIR="$(cd "$(dirname "$0")/.." && pwd)/ssl"
mkdir -p "$SSL_DIR"
cd "$SSL_DIR"

# docker-compose.yml mountet fullchain.pem/privkey.pem/ca.crt einzeln (damit
# ca.key NICHT in den Container wandert). Startet jemand den Stack vor diesem
# Script, legt Docker an ihrer Stelle leere Verzeichnisse an — die hier
# wegräumen, sonst scheitert openssl gleich mit "Is a directory".
for f in fullchain.pem privkey.pem ca.crt; do
    [ -d "$f" ] && rmdir "$f" || true
done

DOMAIN=${1:-agent-dashboard.local}
shift || true

if [ ! -f ca.key ]; then
    echo "== erzeuge CA (10 Jahre) =="
    openssl req -x509 -newkey rsa:4096 -nodes -days 3650 \
        -keyout ca.key -out ca.crt \
        -subj "/CN=agent-dashboard CA" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign"
    chmod 600 ca.key
fi

SAN="DNS:$DOMAIN,DNS:localhost,IP:127.0.0.1"
# Weitere Argumente: IPv4-Adressen werden IP-, alles andere DNS-Einträge
# (z.B. steuerung.fritz.box — die FritzBox löst den Hostnamen auch für
# VPN-Clients auf, Pi-hole-Namen wie agent-dashboard.local dagegen nur im LAN).
for arg in "$@"; do
    if [[ "$arg" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        SAN="$SAN,IP:$arg"
    else
        SAN="$SAN,DNS:$arg"
    fi
done
echo "== stelle Server-Zertifikat aus: $SAN =="

openssl req -newkey rsa:2048 -nodes \
    -keyout privkey.pem -out server.csr -subj "/CN=$DOMAIN"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 \
    -extfile <(printf 'subjectAltName=%s\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature,keyEncipherment\nbasicConstraints=CA:FALSE\n' "$SAN") \
    -out server.crt
cat server.crt ca.crt > fullchain.pem
rm -f server.csr
chmod 600 privkey.pem

echo "== fertig: $(pwd)/{fullchain.pem,privkey.pem,ca.crt} =="
openssl x509 -in server.crt -noout -subject -enddate -ext subjectAltName
