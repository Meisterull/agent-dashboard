# Start-Anleitung — Docker Compose

Schritt für Schritt vom geklonten Ordner zum laufenden Dashboard im Browser.
Bezieht sich auf `docker-compose.yml`, `entrypoint.sh` und `supervisord.conf`
in diesem Repo. Überblick/Architektur: `README.md`.

---

## 0. Voraussetzungen

- **Docker** + **Docker Compose v2** (`docker compose version` muss klappen).
- Ein **Anthropic-API-Key** (`sk-ant-…`) für den Chat. Health/Dateien/Agenten
  funktionieren auch ohne; nur der Orchestrator-Chat braucht ihn.
- Linux/macOS-Host. Alle Befehle aus dem Projektordner (wo `docker-compose.yml` liegt).

```bash
cd ~/agent-dashboard        # oder dein Projektpfad
```

---

## 1. `.env` anlegen

```bash
cp .env.example .env
```

In `.env` mindestens setzen — **Provider wählen**:

```dotenv
# Variante A: Claude (braucht Key)
ORCH_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Variante B: Ollama lokal (KEIN Key nötig)
# ORCH_PROVIDER=ollama
# OLLAMA_BASE_URL=http://host.docker.internal:11434   # aus dem Container den Host adressieren
# OLLAMA_MODEL=gpt-oss:120b-cloud                      # Tool-fähiges Modell

SESSION_SECRET=$(openssl rand -hex 32)   # oder irgendeinen langen Zufallswert eintragen
DOMAIN=agent-dashboard.local
```

> **Tipp:** Mit `ORCH_PROVIDER=ollama` läuft der Chat ohne Anthropic-Key. Aus dem
> Container ist `localhost` der Container selbst — für eine Ollama-Instanz auf dem
> Host `host.docker.internal` (oder die Host-IP) verwenden.

Optional pro Integration (passend zu `config/integrations.yaml`):

```dotenv
ERP_API_KEY=...                     # nur wenn du eine HTTP-Integration mit auth_env nutzt
```

> Secrets stehen **nur** in `.env`/Docker-Secrets — nie im Frontend, nie im
> Klartext in `config/agents.yaml`.

---

## 2. Konfiguration prüfen (`config/`)

Beim ersten Start kopiert der Container die Vorlagen aus `config/` nach
`/workspace/config/` (dort editierbar). Die mitgelieferten Vorlagen reichen zum
Hochfahren — anpassen kannst du später.

- `config/agents.yaml` — deine Agenten (Koordinator/Worker), SSH-Hosts, Rolle.
- `config/integrations.yaml` — benannte HTTP-Endpunkte je Workflow (Beispiele in der Datei).

**SSH-Keys (nur nötig, wenn du das Browser-Terminal/echte Hosts nutzt):**
Lege private Keys in `./secrets/` ab, passend zu `key_file:` in `agents.yaml`
(dort referenziert als `/run/secrets/<name>`):

```bash
mkdir -p secrets
cp ~/.ssh/id_ed25519 secrets/wscad_ed25519
chmod 600 secrets/*
```

`./secrets` wird read-only nach `/run/secrets` gemountet. Ohne SSH-Ziele kannst
du diesen Schritt überspringen — alles andere läuft trotzdem.

---

## 3. Bauen und starten

```bash
docker compose up --build
```

Der erste Build dauert ein paar Minuten (mehrstufig: Node baut das Frontend,
ein Build-Stage kompiliert die Python-Wheels). Was beim Start passiert:

1. **entrypoint.sh** (als root): erzeugt bei Bedarf ein **self-signed SSL-Zertifikat**
   in `/workspace/ssl`, legt `/workspace/{projects,mailboxes,logs,uploads,config}` an,
   kopiert die Config-Vorlagen, `chown` auf den `app`-User, rendert die nginx-Config.
2. **supervisord** startet die Dienste und überwacht sie:
   `nginx` (80/443) · `uvicorn` (FastAPI, intern :5000) · `mcp_server` (intern :9000) ·
   `mcp-tunnel` (nur mit `MCP_TUNNEL_ENABLED=true`). Geht ein Dienst endgültig
   nicht hoch (FATAL), beendet sich der Container und Docker startet ihn neu.

Im Hintergrund laufen lassen: `docker compose up --build -d`.

---

## 4. Dashboard öffnen

```
https://localhost:8443
```

Der Browser warnt wegen des **self-signed Zertifikats** — das ist im MVP normal,
einmal akzeptieren. (`8443` → Container-443; `8080` → Container-80 leitet auf HTTPS um.
Beide Ports sind per `.env` übersteuerbar: `EXTERNAL_HTTPS_PORT`, `EXTERNAL_HTTP_PORT`.)

Schnelltest vom Host aus:

```bash
curl -k https://localhost:8443/api/health      # {"status":"ok"}
curl -k https://localhost:8443/api/agents       # {"agents":[]} bis Mailboxes existieren
```

---

## 5. Erster Durchlauf (alles auf einer Maschine)

`./workspace/mailboxes` ist ein Bind-Mount auf dem Host. Damit kann ein
**lokaler Watcher** auf dem Host dieselben Mailboxes bedienen wie der Container —
ideal zum Ausprobieren ohne echte Remote-Hosts.

1. **Im Dashboard chatten** (Mitte oben):
   > „Lege dem Agent `wscad` eine Aufgabe an: schreibe eine Datei hello.txt mit Inhalt 'hi'."

   Der Orchestrator ruft `send_task("wscad", …)` → es entsteht
   `./workspace/mailboxes/wscad/inbox/task-….json`. Im **MCP-Monitor** (rechts)
   erscheint `wscad` mit der Aufgabe (Status `pending`).

2. **Watcher auf dem Host starten** (zieht die Aufgabe, `--dry-run` = ohne echtes Claude):

   ```bash
   python3 scripts/agent_watcher.py --agent wscad \
     --root ./workspace/mailboxes --dry-run
   ```

   Der Watcher verschiebt die Aufgabe nach `.processing/` und schreibt eine
   Antwort in `outbox/` → im MCP-Monitor wird der Status `done`.

3. **Echtes Claude-Code** statt `--dry-run`: `--dry-run` weglassen, sobald auf dem
   Ziel-Rechner `claude` installiert und eingeloggt ist.

**Rückfragen testen:** Bittet ein Agent um Klärung (`ask(...)`), erscheint oben im
Dashboard das **Rückfrage-Banner** — dort direkt beantworten, die Antwort landet
in der Inbox des Fragestellers.

---

## 6. Echte Remote-Agenten anbinden

Jeder Agenten-PC braucht Zugriff auf seinen Mailbox-Ordner. Zwei Wege:

- **Pull (empfohlen):** Auf dem Agenten-PC `scripts/agent_watcher.py` laufen lassen,
  der Mailbox-Ordner ist per **SSHFS/SFTP** vom Container-Host gemountet:
  ```bash
  sshfs user@dashboard-host:/pfad/zu/workspace/mailboxes /mnt/mailboxes
  python3 agent_watcher.py --agent wscad --root /mnt/mailboxes --workdir ~/projekt
  ```
- **Push:** Variante A (Container führt per SSH `command_template` aus) — siehe
  `PROJECT.md`, eher für Admin-/Fleet-Szenarien.

Das **Browser-Terminal** (unten Mitte) nutzt die SSH-Daten aus `agents.yaml` +
die Keys aus `./secrets`.

---

## 7. Logs, Stoppen, Aufräumen

```bash
docker compose logs -f                 # alle Dienste live (nginx/api/mcp)
docker compose logs -f agent-dashboard # nur der Container
docker compose ps                      # Status + Healthcheck (healthy/unhealthy)
docker compose down                    # stoppen (Volumes/Workspace bleiben)
docker compose down -v                 # auch anonyme Volumes weg (./workspace bleibt, ist Bind-Mount)
```

`./workspace` (Mailboxes, Projekte, Logs, settings.json) liegt als Ordner auf dem
Host und überlebt Neustarts.

---

## 8. Troubleshooting

| Symptom | Ursache / Fix |
|---------|---------------|
| Container `unhealthy` | Healthcheck `https://localhost/api/health` schlägt fehl → `docker compose logs` ansehen; meist Startfehler im FastAPI- oder MCP-Prozess |
| Chat antwortet `503` | `ANTHROPIC_API_KEY` fehlt in `.env` |
| Chat antwortet `502 Orchestrator-Fehler` | MCP-Server nicht erreichbar → Logs des `mcp`-Prozesses prüfen |
| `/api/agents` bleibt leer | Noch keine Mailbox angelegt — erst eine Aufgabe per Chat senden (oder `mkdir ./workspace/mailboxes/<name>/inbox`) |
| Browser meckert über Zertifikat | self-signed im MVP — akzeptieren oder echtes Zertifikat in `./ssl/{fullchain,privkey}.pem` legen |
| Terminal verbindet nicht | SSH-Key in `./secrets` + korrekter `key_file:`/`host:` in `agents.yaml`. Host-Keys werden per TOFU gepinnt (`workspace/config/known_hosts`) — nach einer Neuinstallation des Agenten-PCs den alten Eintrag dort löschen |
| Integration-Tool gibt `error` | Name/Methode nicht in `integrations.yaml` erlaubt, oder `auth_env`-Variable fehlt in `.env` |

---

## Kurzreferenz

```bash
cp .env.example .env          # ANTHROPIC_API_KEY eintragen
docker compose up --build -d  # bauen + starten
# https://localhost:8443  (self-signed akzeptieren)
python3 scripts/agent_watcher.py --agent wscad --root ./workspace/mailboxes --dry-run
docker compose logs -f        # mitlesen
docker compose down           # stoppen
```
