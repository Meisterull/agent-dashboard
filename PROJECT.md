# agent-dashboard — Projektplan

> Zuletzt aktualisiert: 26.06.2026
> Status: Konzeption + gehärtetes Infra-Grundgerüst + lauffähiger Vertical Slice (Mailbox-Roundtrip)
>
> **Lies zuerst [Revision 2026-06-26 — Härtung & MCP](#revision-2026-06-26--härtung--mcp) am Ende.**
> Diese Revision überschreibt bei Widersprüchen die früheren Abschnitte.

## Ziel

Ein Docker-Container mit Web-Oberfläche, über die ich als zentrale Steuerung mit mehreren entfernten Claude-Code-Instanzen arbeite. Jede Instanz ist über eine eigene SSH-Verbindung erreichbar. Ein zentrales LLM im Container plant Aufgaben, erstellt Projektdateien und delegiert Arbeit über MCP an die einzelnen Claude-Code-Agenten. Jeder Agent hat einen eigenen "Briefkasten" (Mailbox) für Aufgaben und Rückmeldungen.

**Neu:** Das Dashboard muss mehrere LLM-Backends gleichzeitig unterstützen:
- Claude via Anthropic API oder ACP/Claude-Code-Adapter
- Ollama (lokale Modelle im Container oder auf anderem Host)
- OpenRouter (aggregierte API)
- Telegram-Bot als alternativer Eingangskanal (Befehle/Chat vom Handy an den Orchestrator)

SSL/TLS wird vorbereitet, aber das echte Zertifikat kommt später.

---

## Container-Inhalt

| Komponente | Zweck |
|------------|-------|
| `nginx` | Reverse Proxy, SSL-Terminierung |
| Let's Encrypt / eigenes SSL-Zertifikat | HTTPS für die Domäne |
| Web-App (Frontend) | Anmeldung, Dashboard, Chat, Dateibaum, SSH-Terminal, Einstellungen |
| Orchestrator-Agent (LLM) | Plant Aufgaben, schreibt Projektdateien, orchestriert Agents |
| MCP-Server | Vermittelt Aufgaben an externe Claude-Code-Instanzen |
| SSH-Client/Proxy im Container | Verbindungen zu entfernten Hosts |
| Dateisystem-Workspace | Projekte, Agent-Mailboxes, Logs |

---

## UI-Layout (Dashboard)

### Nach Anmeldung

```
+-------------------------------------------------------------+
|  agent-dashboard                       [User] [Einstellungen] |
+-------------------------------------------------------------+
|  Chat mit Orchestrator        |  Dateibaum (Container)     |
|                               |  /workspace/projects/...     |
|  (Planung + Chat)             |                              |
|                               |                              |
|                               |                              |
+-------------------------------+------------------------------+
|  SSH-Verbindungen (Tabs)      |  Live-Preview / MCP-Monitor  |
|  z.B. "webserver", "db",      |  (optional: Host-Kamera /   |
|  "claude-pc-1"                |   Screen des Ziel-PCs)       |
|                               |                              |
|  Pro SSH: Terminal +          |                              |
|  Mailbox-Status               |                              |
+-------------------------------+------------------------------+
```

### Wichtige UI-Bereiche

| Bereich | Funktion |
|---------|----------|
| **Top-Bar** | Domänen-Name, Angemeldeter User, Einstellungen-Button |
| **Linker Bereich oben** | Dateibaum des Containers (Projektordner, erstellte Dateien) |
| **Linker Bereich unten** | SSH-Verbindungs-Liste mit Status + Verbindungsbutton |
| **Mitte oben** | Chatfenster mit dem Orchestrator-Modell |
| **Mitte unten** | SSH-Terminal(s) — Tabs für eine oder mehrere gleichzeitige Verbindungen |
| **Rechts oben (optional)** | Live-View / Status des MCP-/Agent-Servers |
| **Einstellungen** | SSH-Verbindungen, Credentials, Agent-Namen, Mailbox-Pfade, API-Keys, LLM-Provider-Auswahl, Telegram-Bot-Token |

---

## Ablauf: Ich → Orchestrator → Agenten

1. **Ich chatte** mit dem Orchestrator-Modell im Dashboard.
2. Orchestrator **plant** die Aufgabe und **legt Projektdateien** unter `/workspace/projects/<projektname>/` an.
3. Orchestrator formuliert die Aufgabe und **gibt sie an den MCP-Server** weiter.
4. MCP-Server legt die Aufgabe im **Briefkasten (Mailbox)** des passenden Agents ab.
   - Pfad z.B.: `/workspace/mailboxes/<agent-name>/inbox/<task-id>.json`
5. Der Agent auf der entfernten Seite (Claude-Code) liest seinen Briefkasten, führt die Aufgabe aus und schreibt die Rückmeldung zurück.
   - Rückmeldung z.B.: `/workspace/mailboxes/<agent-name>/outbox/<task-id>-response.json`
6. Orchestrator liest die Rückmeldung und zeigt mir im Chat das Ergebnis.
7. Ich kann über den Chat jedem Agenten direkt Nachrichten schicken oder Aufgaben bestätigen.

---

## Agenten & Briefkästen

| Konzept | Bedeutung |
|---------|-----------|
| **Agent-Name** | Frei wählbarer Name, z.B. `webserver`, `db-master`, `claude-office-pc` |
| **Mailbox** | Pro Agent ein Ordnerpaar `inbox/` und `outbox/` |
| **SSH-Verbindung** | Pro Agent eine hinterlegte SSH-Adresse + Credentials |
| **Aufgaben-JSON** | Enthält: `task_id`, `agent`, `instruction`, `files`, `status`, `created_at` |
| **Rückmeldung-JSON** | Enthält: `task_id`, `agent`, `result`, `status` (`done`/`error`/`needs_confirm`), `log` |

### Aufgaben-Status

- `pending` — wartet auf Agent
- `running` — Agent bearbeitet
- `done` — abgeschlossen
- `error` — Fehler
- `needs_confirm` — braucht meine Bestätigung

---

## Finaler Tech-Stack (MVP)

| Schicht | Technologie | Grund |
|---------|-------------|-------|
| **Container-Base** | `python:3.12-slim-bookworm` | Python-Backend + Node.js für Frontend ausreichend |
| **Webserver/SSL** | `nginx` + `certbot` | Bekannt, einfach, SSL später per Let's Encrypt |
| **Frontend** | **React** + **Vite** + **Tailwind CSS** + **xterm.js** | Leicht, schnell, WebSocket-tauglich, Terminal direkt einbindbar |
| **Frontend-State/Realtime** | **WebSocket (native)** + React-Hooks | Kein großes Framework nötig, eigene Steuerung der Sessions |
| **Backend-API** | **FastAPI** | Async, Python, einfache WebSocket-Integration |
| **Datenbank** | **SQLite** (lokal im Container/Volume) | Für MVP ausreichend: Sessions, Chat-History, User, Tasks |
| **LLM-Clients** | `anthropic`, `openai`-Paket für OpenRouter, `httpx` für Ollama | Alle über gemeinsames Adapter-Interface |
| **Telegram-Bot** | `python-telegram-bot` (asynchron) | Standard, Webhook + Long-Polling möglich |
| **SSH** | `asyncssh` + `paramiko` (Reserve) | `asyncssh` passt zu FastAPI, mehrere parallele Sessions |
| **Terminal im Browser** | `xterm.js` + WebSocket-Bridge zu `asyncssh` | Industriestandard |
| **Mailbox/Filesystem** | Plain JSON-Dateien im Volume + `aiofiles` | Einfach, debuggbar, Agent-seitig ohne API nötig |
| **Auth** | Eigenes Login + `bcrypt` + Session-Cookie | Für MVP ausreichend |
| **Config-Loader** | `PyYAML` + `pydantic-settings` | Validierung, Env-Substitution |
| **Task-Queue** | Eigener Scheduler im Backend (Polling) | Kein Redis nötig für erstes MVP |
| **Process-Management** | `supervisord` oder Shell-Skript in Entrypoint | nginx + FastAPI + Telegram-Bot-Polling im selben Container |

### Später austauschbar / optional

- SQLite → PostgreSQL wenn mehrere User / viel Traffic
- Eigenes Task-Queue → Redis + Celery / RQ
- Dateibaum → vollständiger VS Code Web-Integration
- OAuth / SSO (Keycloak, Authelia)
- Separater Ollama-Container statt externer Host

### Grund für diese Auswahl

- Alles in **einem Container** für einfaches Deployment.
- Keine externe Datenbank nötig.
- React/Vite liefert modernes SPA-Erlebnis ohne großen Overhead.
- FastAPI + asyncssh + WebSocket ermöglicht mehrere parallele SSH-Terminals.
- JSON-Mailbox ist die einfachste Brücke zu entfernten Claude-Code-Instanzen — die können mit einfachem Python-Skript oder sogar Shell-Skript abfragen.

---

## System-Architektur (MVP)

```
                                    +---------------------+
                                    |      User          |
                                    |  Browser / Telegram |
                                    +----------+----------+
                                               |
                           +-------------------+-------------------+
                           | HTTPS (später)    | HTTP (lokal)      | Telegram API
                           ↓                   ↓                   ↓
                 +------------------+  +------------------+  +------------------+
                 |   nginx (443)    |  |   nginx (80)     |  |  Telegram Server |
                 |  SSL-Terminierung |  |  Reverse Proxy   |  |                  |
                 +--------+---------+  +--------+---------+  +--------+---------+
                          |                   |                            |
                          +-------------------+-------------------+--------+
                                              |
                                              ↓
                                   +----------------------+
                                   |    nginx intern      |
                                   |  / → Frontend SPA    |
                                   |  /api → FastAPI      |
                                   |  /ws → WebSocket     |
                                   +----------+-----------+
                                              |
                    +-------------------------+-------------------------+
                    |                                                   |
                    ↓                                                   ↓
        +----------------------+                            +----------------------+
        |   React Frontend     |                            |   FastAPI Backend    |
        |   (Vite Build)       |  <── HTTP/REST + WS ───>   |   (Python 3.12)      |
        |                      |                            |                      |
        |  ├ Login             |                            |  ├ Auth (bcrypt)     |
        |  ├ Chat UI           |                            |  ├ Chat API          |
        |  ├ File Tree         |                            |  ├ LLM Router        |
        |  ├ SSH Terminal      |                            |  ├ Task Orchestrator |
        |  └ Settings          |                            |  ├ Mailbox Manager   |
        |                      |                            |  ├ SSH Bridge (WS)   |
        |  xterm.js ────────>  |                            |  ├ Telegram Bot      |
        |  WebSocket-Terminal  |                            |  └ File Manager      |
        +----------------------+                            +----------+-----------+
                                                                         |
            +------------------------------------------------------------+------------+
            |                                                            |            |
            ↓                                                            ↓            ↓
 +----------------------+                            +----------------------+  +-------------------+
 |     SQLite DB        |                            |   /workspace Volume  |  |   LLM Backends    |
 |  users, sessions,    |                            |  ├── projects/       |  |  ├ Claude API     |
 |  chat_history, tasks |                            |  ├── mailboxes/      |  |  ├ OpenRouter     |
 +----------------------+                            |  ├── config/         |  |  └ Ollama (extern)|
                                                     |  ├── logs/           |  +-------------------+
                                                     |  └── ssl/            |
                                                     +----------+-----------+
                                                                |
                     +------------------------------------------+-----------------------------------+
                     |                                          |                                   |
                     ↓                                          ↓                                   ↓
          +----------------------+                    +----------------------+             +----------------------+
          |  Agent-PC / Server     |                    |  Agent-PC / Server   |             |  Agent-PC / Server   |
          |  (SSH + Claude-Code)   |                    |  (Mailbox Watcher)   |             |  (SSH Push Mode)     |
          |                      |                    |                      |             |                      |
          |  ├ SSH-Daemon        |                    |  ├ Watcher-Skript    |             |  ├ SSH-Daemon        |
          |  ├ claude-agent-acp    |                    |  ├ liest inbox/      |             |  ├ claude --print    |
          |  └ workdir           |                    |  ├ startet claude     |             |  └ schreibt outbox/  |
          |                      |                    |  └ schreibt outbox/   |             |                      |
          |  Verbindung: asyncssh|                    |  Verbindung: SFTP/SSH |             |  Verbindung: SSH    |
          +----------------------+                    +----------------------+             +----------------------+
```

### Datenflüsse

| Fluss | Beschreibung |
|-------|--------------|
| **1. User → Dashboard** | Login → React-App lädt → Chat, Dateibaum, SSH-Liste |
| **2. Chat → Orchestrator** | Nachricht per REST `/api/chat` → LLM Router → Antwort zurück |
| **3. Task → Mailbox** | Orchestrator legt Task-JSON in `/workspace/mailboxes/<agent>/inbox/` |
| **4. Agent → Mailbox** | Agent liest Inbox, arbeitet, schreibt Response in Outbox |
| **5. Outbox → Dashboard** | Backend pollt oder nutzt Datei-Events → zeigt Ergebnis im Chat |
| **6. SSH-Terminal** | Frontend öffnet WebSocket `/ws/ssh/<agent>` → Backend baut SSH-Session über `asyncssh` |
| **7. Telegram → Orchestrator** | Bot empfängt Nachricht → Backend fügt Chat-History hinzu → Orchestrator antwortet über Bot |

### Komponenten-Interaktion

```
Frontend (React)
  ├── REST /api/auth/*          → FastAPI Auth
  ├── REST /api/chat/*          → FastAPI Chat + Orchestrator
  ├── REST /api/files/*         → FastAPI File Manager
  ├── REST /api/agents/*        → Agent-Config + Status
  ├── REST /api/tasks/*         → Task-Verwaltung
  ├── WS   /ws/ssh/<agent>      → SSH Terminal Bridge
  └── WS   /ws/events           → Live-Updates für Chat/Tasks/Agent-Status

FastAPI Backend
  ├── Routers
  │   ├── auth.py
  │   ├── chat.py
  │   ├── files.py
  │   ├── agents.py
  │   ├── tasks.py
  │   └── telegram.py
  ├── Services
  │   ├── orchestrator.py       → zentrale LLM + Planungslogik
  │   ├── llm_router.py         → Claude / OpenRouter / Ollama Adapter
  │   ├── mailbox_service.py    → Inbox/Outbox lesen/schreiben
  │   ├── ssh_bridge.py         → WebSocket ↔ SSH-Session
  │   ├── file_service.py       → Dateibaum + CRUD
  │   └── telegram_bot.py       → Telegram-Bot-Handler
  └── Models (SQLAlchemy / SQLite)
      ├── User
      ├── Session
      ├── ChatMessage
      ├── Task
      └── AgentConnection
```

---

## Abläufe im Detail

---

## Dateisystem im Container

```
/workspace/
├── projects/                    # Vom Orchestrator angelegte Projekte
│   └── mein-projekt/
│       ├── plan.md
│       └── src/
├── mailboxes/                   # Briefkästen pro Agent
│   └── webserver/
│       ├── inbox/
│       └── outbox/
├── config/
│   ├── agents.yaml              # SSH-Verbindungen + Credentials-Referenzen
│   ├── settings.yaml            # API-Keys, Domäne, Ports, Telegram-Token
│   ├── llm-providers.yaml       # Claude, Ollama, OpenRouter Konfiguration
│   └── users.json               # Login-Daten (gehasht)
├── logs/
│   ├── orchestrator.log
│   ├── mcp-server.log
│   ├── ssh-connections.log
│   └── telegram-bot.log
└── ssl/                         # Zertifikate (vorbereitet)
    ├── fullchain.pem
    └── privkey.pem
```

---

## Offene Punkte / Nächste Schritte

- [ ] Domäne festlegen, für die SSL-Zertifikat geholt wird
- [ ] Authentifizierungsmethode wählen (lokale User-Datenbank vs. externer Auth)
- [ ] LLM-Anbieter/Orchestrator klären (lokal, OpenRouter, Anthropic)
- [ ] Entscheiden: sollen die entfernten Claudes selbst den Briefkasten abfragen, oder soll der Container aktiv per SSH Befehle auf den Ziel-PCs ausführen? (aktuell: beides vorbereiten)
- Telegram-Bot dient als **zweiter Eingangskanal** neben dem Web-Dashboard.
- User kann dem Bot schreiben:
  - Freitext → wird als Chat-Eintrag an den Orchestrator übergeben.
  - `/task <agent> <Beschreibung>` → Orchestrator legt eine neue Aufgabe an.
  - `/status` → Orchestrator zeigt aktuelle Aufgaben + Agenten-Status.
  - `/agents` → Liste der konfigurierten Agenten + Verbindungsstatus.
- Bot leitet **Rückfragen** der Agenten an den User weiter (z.B. "Aufgabe X braucht Bestätigung" / "Unklar, bitte präzisieren").
- Antworten des Users auf Rückfragen gehen zurück an den Orchestrator und dann an den betreffenden Agenten.
- Bot **sendet keine unaufgeforderten Statusmeldungen** — nur als Antwort auf User-Nachrichten oder bei Rückfragen/Bestätigungen.
## Abläufe im Detail

### A. Web-Dashboard → Orchestrator

```
User schreibt im Chat
        ↓
Backend speichert Nachricht in Chat-History
        ↓
Orchestrator-LLM analysiert Anfrage
        ↓
Entscheidung:
  → nur Chat-Antwort → direkte Antwort im Chat
  → Aufgabe nötig → Projektordner + Plan + Task-JSON anlegen → Agent-Mailbox
        ↓
Orchestrator zeigt Zusammenfassung / Bestätigung im Chat
        ↓
Wenn Agent-Rückmeldung eintrifft (Polling/WebSocket):
  → Orchestrator liest Outbox
  → zeigt Ergebnis im Chat
  → bei Rückfrage: markiert Task als "needs_confirm" und fragt User
```

### B. Telegram → Orchestrator

```
User schreibt @AgentDashboardBot
        ↓
Telegram-Webhook oder Long-Polling → Backend
        ↓
Backend prüft User-ID (Whitelist)
        ↓
Nachricht wird als Chat-Eintrag im Orchestrator übernommen
        ↓
Weiter wie Ablauf A
        ↓
Antwort des Orchestrators zurück an Telegram
        ↓
Rückfragen/Bestätigungen ebenfalls über Telegram
```

### C. Agent → Orchestrator

```
Agent (Claude-Code auf fremdem PC) liest Inbox /workspace/mailboxes/<name>/inbox/
        ↓
Führt Aufgabe aus
        ↓
Schreibt Ergebnis in Outbox /workspace/mailboxes/<name>/outbox/<task-id>-response.json
        ↓
Backend erkennt neue Rückmeldung (Polling oder Datei-Event)
        ↓
Orchestrator fasst Ergebnis zusammen
        ↓
Anzeige im Dashboard + ggf. Telegram + ggf. Bestätigungsanfrage
```

### D. SSH-Verbindung im Browser

```
User klickt im Dashboard auf SSH-Verbindung
        ↓
Backend öffnet SSH-Session über paramiko/asyncssh
        ↓
WebSocket-Tunnel zwischen Frontend (xterm.js) und Backend
        ↓
Terminal erscheint im Tab
        ↓
Mehrere SSH-Sessions gleichzeitig möglich (Tabs)
```
## LLM-Provider-Optionen

Der Orchestrator kann Chat- und Planungs-Anfragen an unterschiedliche Backends senden. Im Dashboard und in der Config ist pro Chat oder pro Task wählbar:

| Provider | Verwendung | Konfiguration |
|----------|-----------|---------------|
| **Claude (Anthropic API)** | Standard-Orchestrator für Planung + Chat | `ANTHROPIC_API_KEY`, Modell z.B. `claude-sonnet-4` |
| **Claude ACP / Claude-Code** | Für entfernte Agenten — nicht für den Orchestrator vorgesehen | SSH-Verbindung + `claude-agent-acp-adapter` auf Ziel-Host |
| **Ollama** | Lokales Modell, Anbindung an externen Ollama-Host (erstmal nicht im Container) | `OLLAMA_BASE_URL`, z.B. `http://192.168.2.x:11434`, Modellname |
| **OpenRouter** | Fallback oder wenn Anthropic nicht verfügbar | `OPENROUTER_API_KEY`, Modellreferenz |

### Orchestrator-Logik

1. Default-Provider aus `llm-providers.yaml` / UI-Auswahl.
2. Wenn Provider ausfällt (Timeout, Rate-Limit, 401/500): automatischer Fallback auf nächsten Provider in Reihenfolge.
3. Für lange Code-Planung kann Claude API bevorzugt werden, für schnelle Antworten Ollama.
4. Der Orchestrator **meldet dem Agenten nur "du hast eine Aufgabe"** — er delegiert, führt aber nicht selbst Code auf dem Ziel-PC aus.

### Agent-Anbindung (Claude-Code auf fremdem PC)

Zwei Varianten, beide vorbereitet:

**Variante A: Push über SSH (bevorzugt für direkte Steuerung)**
- Container verbindet sich per SSH zum Ziel-PC.
- Startet dort Claude-Code als Subprozess mit einer Aufgabe (über ACP-Adapter oder `--print --input-format stream-json`).
- Führt Aufgabe aus, schreibt Ergebnis in die Mailbox-Outbox zurück.
- Gut für: einzelne Befehle, gezielte Dateiänderungen.

**Variante B: Pull über Mailbox (bevorzugt für dauerhafte Agents)**
- Auf dem Ziel-PC läuft ein kleiner Watcher/Agent (Python-Skript), der regelmäßig sein Inbox-Verzeichnis abfragt.
- Wenn Aufgabe vorhanden: startet Claude-Code lokal, führt sie aus, schreibt Ergebnis in Outbox.
- Container liest Outbox und zeigt Ergebnis im Chat.
- Gut für: langlaufende Agents, mehrere Aufgaben hintereinander, weniger SSH-Overhead.

Für beide Varianten braucht jeder Agent einen eindeutigen Namen, der gleichzeitig Mailbox-Ordner und SSH-Verbindung referenziert.

### Beispiel: Aufgaben-Delegation

```
User im Dashboard: "Erstelle eine Login-Seite für Projekt foo"

Orchestrator:
1. Erstellt Projektordner /workspace/projects/foo/
2. Schreibt plan.md mit Aufgabenzerlegung
3. Erkennt: "frontend"-Agent zuständig
4. Legt Aufgabe in /workspace/mailboxes/frontend/inbox/task-001.json ab:
   {
     "task_id": "task-001",
     "agent": "frontend",
     "project": "foo",
     "instruction": "Erstelle login.html mit Formular für User/Passwort...",
     "files": ["/workspace/projects/foo/plan.md"],
     "status": "pending",
     "created_at": "2026-06-26T14:00:00+02:00"
   }
5. Meldet dem User: "Aufgabe task-001 an frontend delegiert."
6. Frontend-Agent (Claude-Code auf Ziel-PC) liest Inbox, erledigt Aufgabe,
   schreibt /workspace/mailboxes/frontend/outbox/task-001-response.json.
7. Orchestrator liest Rückmeldung und zeigt Zusammenfassung im Chat.
```
## Config-Schema (Vorschlag)

### `llm-providers.yaml`

```yaml
default: claude-api

providers:
  claude-api:
    type: anthropic
    model: claude-sonnet-4
    api_key: ${ANTHROPIC_API_KEY}
    max_tokens: 8192

  openrouter:
    type: openrouter
    model: anthropic/claude-sonnet-4
    api_key: ${OPENROUTER_API_KEY}
    fallback: true

  ollama-local:
    type: ollama
    base_url: ${OLLAMA_BASE_URL}
    model: ${OLLAMA_MODEL}
    fallback: false

order:
  - claude-api
  - openrouter
  - ollama-local
```

### `agents.yaml`

```yaml
agents:
  - name: frontend
    description: "Claude-Code auf dem Frontend-Entwicklungsrechner"
    connection:
      type: ssh
      host: 192.168.2.100
      port: 22
      user: dev
      auth: key                        # key | password | agent
      key_file: /workspace/secrets/frontend_rsa   # oder Docker Secret
      workdir: /home/dev/projects
    mode: mailbox                      # mailbox | push
    mailbox_path: /workspace/mailboxes/frontend
    acp:
      enabled: true
      adapter_path: /home/dev/.npm-global/bin/claude-agent-acp

  - name: webserver
    description: "Claude-Code auf dem Produktivserver"
    connection:
      type: ssh
      host: srv.example.com
      port: 22
      user: ansible
      auth: key
      key_file: /workspace/secrets/webserver_rsa
    mode: push
    command_template: |
      cd {{ workdir }} && claude --print --input-format stream-json < {{ task_file }}
```

### `settings.yaml`

```yaml
app:
  domain: agent-dashboard.local        # später echte Domäne
  port_http: 80
  port_https: 443
  api_port: 5000
  timezone: Europe/Berlin
  language: de

auth:
  method: local                        # local | oauth | none
  session_ttl_hours: 24
  password_min_length: 12

telegram:
  enabled: true
  bot_token: ${TELEGRAM_BOT_TOKEN}
  allowed_user_ids:
    - ${TELEGRAM_ALLOWED_USER_ID}
  webhook_url: https://${DOMAIN}/api/v1/telegram/webhook

ssl:
  enabled: true
  certbot: false                       # später true wenn echte Domäne + Port 80/443 extern erreichbar
  cert_path: /app/ssl/fullchain.pem
  key_path: /app/ssl/privkey.pem
  staging: false

logging:
  level: info
  retain_days: 14
```

---

## Notizen / Gedankenstütze

- Jeder SSH-Ziel-PC braucht einen laufenden Claude-Code- oder ACP-Adapter-Prozess, damit Aufgaben ankommen.
- Alternative ohne dauerhaften Agenten-Prozess: Container verbindet sich per SSH, startet Claude-Code für jede Aufgabe, führt sie aus und schreibt Ergebnis zurück.
- Credentials dürfen nicht im Klartext in `config/agents.yaml` stehen — entweder `.env`, Docker Secrets oder Vault.
- Für den Anfang reicht eine lokale User-Datenbank im Container.
- SSL-Zertifikat automatisch erneuern (certbot cron im Container oder auf Host).
## Sicherheit & Credential-Management (Vorschlag)

| Thema | Regel |
|-------|-------|
| **Credentials** | Keine Klartext-Keys in Config. SSH-Keys und API-Keys kommen in `.env` oder Docker Secrets. Im Container werden sie unter `/run/secrets/` oder `/workspace/secrets/` eingelesen. |
| **SSH** | Nur Key-Based-Auth erlaubt, Passwort-Login deaktiviert. Pro Agent separater SSH-Key. |
| **API-Keys** | Nur im Backend-Prozess geladen, nie an Frontend ausliefern. |
| **Befehls-Whitelist** | Push-Modus führt nur Befehle aus, die in `command_template` definiert sind. Keine beliebigen Shell-Befehle. |
| **Dateizugriff** | Backend darf nur innerhalb `/workspace/` und konfigurierter Agent-Workdirs arbeiten. Pfad-Traversal unterbinden. |
| **Auth** | Login mit gehashtem Passwort + Session-Cookie (HttpOnly, Secure). Telegram-Whitelist auf User-ID. |
| **Rate-Limiting** | Für API + Login-Endpunkte, gegen Brute-Force. |
| **SSL** | HTTPS erzwungen, HSTS-Header. Self-signed nur für lokale Tests. |
| **Logs** | Keine Secrets in Logs. Rotiert nach 14 Tagen. |
| **Container** | Nicht-root-User für App-Prozesse, read-only-Filesystem wo möglich. |

### Phase-1-Minimalversion

- Lokale User-Datenbank + Passwort-Hash.
- SSH-Keys und API-Keys in `.env` / Docker Secrets.
- Keine Befehls-Whitelist im Code, aber nur `command_template` aus Config.
- Self-signed Zertifikat bis echte Domäne da ist.

---

## Revision 2026-06-26 — Härtung & MCP

Diese Revision macht aus dem Konzept ein **solides Fundament**. Sie behebt
konkrete Widersprüche im ursprünglichen Plan und ergänzt den MCP-Server.
Bei Widersprüchen gilt diese Revision.

### Behobene Widersprüche (Infra)

| Problem im Erstentwurf | Fix |
|------------------------|-----|
| `/var/run/docker.sock` read-only gemountet, aber nirgends gebraucht = Root auf dem Host | **Mount entfernt** aus `docker-compose.yml`. |
| `config/` read-only gemountet, aber UI soll Settings schreiben | Vorlage bleibt read-only unter `/app/config`; **editierbare Config wandert nach `/workspace/config`** (entrypoint kopiert beim ersten Start). |
| `sshpass` installiert, aber Security sagt „nur Key-Auth" | **`sshpass` entfernt.** Key-only. |
| `build-essential` + `npm` im Runtime-Image (~300 MB Ballast) | **Multi-Stage-Build:** Node baut nur das Frontend, ein Build-Stage kompiliert die Python-Wheels, das Runtime-Image enthält keins davon. |
| Prozesse per `&` im entrypoint (Crash bleibt unbemerkt) | **`supervisord`** beaufsichtigt nginx/api/mcp/telegram und startet bei Absturz neu. |
| Alles als root | App-Prozesse laufen als **non-root `app`**, `no-new-privileges`, `cap_drop: ALL` + nur nötige Caps. |
| FastAPI-Port 5000 nach außen veröffentlicht | **Nicht mehr gemappt** — nur intern hinter nginx. |
| Race-Condition: halb geschriebene Mailbox-JSON | **Atomares Schreiben** (`tmp` + `fsync` + `os.replace`) + `.processing/`-Claim gegen Doppel-Pickup → in `backend/app/mailbox.py`. |

### MCP-Server — Entscheidung

Ja, der MCP-Server gehört in den Container. Wichtig ist die **Rollentrennung**:

- **Rolle 1 (jetzt, MVP): MCP = Werkzeugkasten des Orchestrators.**
  Das Orchestrator-LLM bekommt strukturierte Tools statt provider-spezifischem
  Function-Calling: `create_task`, `read_responses`, `list_agents`,
  `write_project_file`, `read_project_file`. Eine Tool-Schicht für Claude,
  OpenRouter **und** Ollama. Läuft als eigener supervisord-Prozess,
  Streamable-HTTP auf `127.0.0.1:9000`, **nicht nach außen veröffentlicht**.
  → `backend/mcp_server.py`.

- **Rolle 2 (später, optional): MCP als Transport zu den Remote-Agenten.**
  Eleganter als Datei-Polling, aber netzabhängig und fragiler. Fürs MVP
  bleibt die **Datei-Mailbox der Transport** (offline-tolerant, debugbar).
  Remote-Agenten dürfen den MCP-Server später *zusätzlich* erreichen.

Weitere Tools, die der MCP-Server bekommen sollte, sobald die Dienste stehen:
`run_ssh_command(agent, cmd)` (gegen `command_template`-Whitelist),
`ask_user(question)` (löst den `needs_confirm`-Flow nach Dashboard/Telegram aus).

### Neue/geänderte Dateien

```
Dockerfile                         # Multi-Stage, non-root, schlank
docker-compose.yml                 # gehärtet, kein docker.sock, Caps, Healthcheck
entrypoint.sh                      # root-Setup -> exec supervisord
supervisord.conf                   # nginx + api + mcp + telegram
nginx/agent-dashboard.conf.template
.env.example
backend/requirements.txt
backend/app/mailbox.py             # atomare Mailbox (riskanteste Primitive)
backend/app/orchestrator_core.py   # gemeinsamer Kern: MCP-Anbindung + Agentic-Loop
backend/app/files.py               # pfad-sichere Datei-Ops (Dateibaum)
backend/app/config.py              # Settings + SSH-Verbindungen (ohne Credentials)
backend/app/ssh_bridge.py          # WebSocket ↔ asyncssh (Terminal)
backend/mcp_server.py              # MCP-Tools für den Orchestrator
backend/orchestrator.py            # Orchestrator-CLI (dünn, über dem Kern)
backend/main.py                    # FastAPI: health/chat/agents/tasks/files/connections/settings + ws ssh
frontend/                          # React + Vite + Tailwind v4 — vollständiges Dashboard
  ├── package.json · vite.config.js · index.html
  └── src/
      ├── App.jsx · main.jsx · api.js · index.css
      └── components/{TopBar, Chat, FileTree, AgentsPanel,
                      TerminalPanel, Terminal, Settings, FileViewer, Modal}.jsx
scripts/agent_watcher.py           # Remote-Watcher (Variante B) — dependency-frei
```

### Orchestrator-Loop starten (Punkt 2 der Roadmap)

`backend/orchestrator.py` ist die CLI-Version des Orchestrators — **noch ohne
Web/SSL/Telegram**. Sie bezieht ihre Tools über den MCP-Server (Rolle 1) und
delegiert real in die Datei-Mailbox. Modell: `claude-opus-4-8`, adaptives
Thinking, manuelle Agentic-Loop, die Tool-Calls über die MCP-Session ausführt.

```bash
cd backend
pip install -r requirements.txt           # bringt anthropic + mcp mit
export ANTHROPIC_API_KEY=sk-ant-...
export WORKSPACE_DIR=/tmp/mb               # gemeinsam mit dem Watcher

# Terminal 1: MCP-Server (Tools)
python -m mcp_server                       # lauscht auf 127.0.0.1:9000/mcp

# Terminal 2: ein Test-Agent zieht Aufgaben (dry-run = ohne echtes claude)
python ../scripts/agent_watcher.py --agent frontend --root "$WORKSPACE_DIR/mailboxes" --dry-run

# Terminal 3: der Orchestrator-Chat
python orchestrator.py
# > "Lege dem frontend-Agent eine Aufgabe an: erstelle login.html"
#   Claude ruft create_task auf -> inbox -> Watcher -> outbox -> read_responses
```

> Status: Code vorhanden und syntaktisch geprüft; der **LLM-Teil ist noch
> nicht end-to-end gelaufen** (lokal keine `anthropic`/`mcp`-Installation und
> kein API-Key). Der Mailbox-Roundtrip selbst ist getestet (siehe oben).

### FastAPI-Wrapper starten (Punkt 3 der Roadmap)

`backend/main.py` setzt denselben Orchestrator-Kern hinter HTTP. nginx leitet
`/api/` intern an `127.0.0.1:5000` — daher alle Endpunkte unter `/api`.

| Endpunkt | Zweck |
|----------|-------|
| `GET /api/health` | Liveness (Docker-Healthcheck), unabhängig von MCP/Anthropic |
| `GET /api/agents` | Konfigurierte Agenten (= Mailbox-Ordner), kein LLM nötig |
| `GET /api/agents/{name}/tasks` | Inbox + Outbox eines Agenten (MCP-Monitor) |
| `POST /api/chat` | Eine Chat-Runde; Body `{message, session_id?}` → `{session_id, reply, tool_calls}` |
| `GET /api/files?path=` | Dateibaum im Workspace (path-traversal-sicher) |
| `GET /api/files/content?path=` | Dateiinhalt (begrenzt) |
| `GET /api/connections` | SSH-Verbindungen aus agents.yaml (ohne Credentials) |
| `GET/PUT /api/settings` | Editierbare UI-Settings (`/workspace/config/settings.json`) |
| `WS /ws/ssh/{name}` | SSH-Terminal-Bridge (xterm.js ↔ asyncssh) |

Chat-State ist fürs MVP **in-memory** (pro Session serialisiert; verschiedene
Sessions parallel). Die MCP-Session wird pro Runde frisch geöffnet → robust
gegen MCP-Neustarts. Persistenz später via SQLite (siehe Tech-Stack).

```bash
cd backend && pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
export WORKSPACE_DIR=/tmp/mb
python -m mcp_server &                      # Tools
uvicorn main:app --host 127.0.0.1 --port 5000

curl localhost:5000/api/health              # {"status":"ok"}
curl -XPOST localhost:5000/api/chat -H 'content-type: application/json' \
  -d '{"message":"Lege dem frontend-Agent eine Aufgabe an: erstelle login.html"}'
```

> Status: Code vorhanden, alle Module kompilieren (`py_compile` grün). Der
> Chat-Pfad braucht zum echten Lauf `anthropic`/`mcp` + API-Key (lokal nicht
> verfügbar); `/api/health` und `/api/agents` sind LLM-frei.

### React-Chat-View starten (Punkt 4 der Roadmap)

`frontend/` ist die erste sichtbare Oberfläche: ein Chatfenster mit
Agenten-Sidebar, das `/api/chat` und `/api/agents` anspricht. Dieselben
relativen `fetch('/api/...')`-Aufrufe funktionieren in Dev (Vite proxyt an
`127.0.0.1:5000`) und Produktion (nginx liefert `dist/` aus und proxyt `/api`).

```bash
cd frontend
npm install
npm run dev        # Entwicklung: http://localhost:5173 (proxyt /api ans Backend)
npm run build      # Produktion: erzeugt dist/, das nginx im Container ausliefert
```

### Vollständiges Dashboard (alle Panels der Skizze)

Die Oberfläche ist jetzt komplett ausgebaut — Layout wie im UI-Entwurf oben:

```
Top-Bar (Session-Anzeige · Einstellungen)
┌ Dateibaum ┬ Chat mit Orchestrator      ┬ MCP-Monitor ┐
│ (Workspace)│ (Tool-Call-Chips)          │  Inbox /    │
│           ├────────────────────────────┤  Outbox je  │
│           │ SSH-Terminal (Tabs)        │  Agent +    │
│           │ (xterm.js)                 │  Status     │
└───────────┴────────────────────────────┴─────────────┘
```

- **Dateibaum** — lazy-ladender Tree über `/api/files`; Klick öffnet Datei im Viewer.
- **Chat** — Orchestrator-Chat mit Tool-Call-Chips (welche MCP-Tools liefen).
- **SSH-Terminal** — Verbindungen aus `agents.yaml` als Tabs, echtes xterm.js über `/ws/ssh/<name>`.
- **MCP-Monitor** — pro Agent Inbox/Outbox mit farbigen Status-Badges (`pending`/`running`/`done`/`error`/`needs_confirm`).
- **Einstellungen** — Modal für Provider/Sprache/Telegram; persistiert via `/api/settings` (Keys/Tokens bleiben in `.env`).

> Status:
> - **Frontend-Build verifiziert** (`npm run build` grün, 44 Module, xterm gebündelt).
> - **Datei- und Settings-Logik echt getestet** (Standardlib-Module ohne Server):
>   Dateibaum, Dateiinhalt, **Path-Traversal blockiert** (3 Angriffe), Settings-Round-Trip
>   + Whitelist (ungültige Keys verworfen).
> - **Backend-Server nicht live gestartet** (lokal kein `pip`/`venv` → kein
>   `fastapi`/`anthropic`/`mcp`); alle Module kompilieren (`py_compile` grün).
> - **SSH-Bridge real implementiert, aber nicht end-to-end getestet** (kein
>   SSH-Ziel/Credentials). Im Container mit `agents.yaml` + Key-Datei lauffähig.

### Vertical Slice — funktioniert bereits

Der riskanteste Pfad (Orchestrator → Mailbox → Agent → Mailbox → Orchestrator)
ist als Code da und getestet, **ohne UI, ohne SSL, ohne Telegram**:

```bash
ROOT=/tmp/mb/mailboxes
PYTHONPATH=backend python3 -c "from app.mailbox import Mailbox, Task; \
  Mailbox('$ROOT','frontend').put_task(Task('task-0001','frontend','Erstelle login.html'))"
python3 scripts/agent_watcher.py --agent frontend --root "$ROOT" --dry-run --once
# -> outbox/task-0001-response.json mit status=done
```

`--dry-run` weglassen, sobald `claude` auf dem Ziel-PC installiert ist.

### Revidierte Priorisierung (riskantester Pfad zuerst, nicht sichtbarster)

1. ✅ **Mailbox-Roundtrip** (atomar) — erledigt, getestet.
2. ✅ **MCP-Server an Orchestrator-Loop angebunden** (Claude API, CLI ohne Web) — Code da, syntaktisch geprüft; LLM-Teil noch nicht end-to-end gelaufen.
3. ✅ **FastAPI-Wrapper** um Orchestrator + Mailbox: `/api/health`, `/api/chat`, `/api/agents` — Code da, kompiliert; Chat-Pfad noch nicht end-to-end gelaufen.
4. ✅ **Minimale React-Chat-View** (Chat + Agenten-Sidebar) — Produktions-Build verifiziert.
5. Dann *additiv*: SSH-Terminal (xterm.js + asyncssh ✅) · Multi-Provider (anthropic + ollama ✅, Loop gegen Ollama real getestet; OpenRouter + Fallback offen) · Telegram · echtes SSL/Domäne · Settings-UI ✅ · File-Tree ✅.

Begründung: Login/SSL/UI sind sichtbar, aber risikoarm. Der Remote-Agent-Roundtrip
ist der unsichere Kern — der steht jetzt zuerst und ist testbar.

---

## Betriebsmodi

Dasselbe Gerüst (Orchestrator + Mailbox + Agenten + Dashboard) trägt zwei sehr
unterschiedliche Anwendungsfälle. Modus A ist der **primäre** Grund für das
Projekt; Modus B ist möglich, braucht aber andere Leitplanken.

### Modus A — Entwicklungs-Orchestrierung (primär)

**Das Problem, das es löst:** Beim Entwickeln mit mehreren Claude-Instanzen
wechselt man heute ständig **von Fenster zu Fenster** und verkuppelt die Agenten
von Hand — kopiert Rückfragen des einen ins Fenster des anderen, leitet Ergebnisse
weiter. Das Dashboard automatisiert genau diesen Botengang.

**Konkretes Beispiel (ERPNext-Anpassung, reale Vorlage):**

```
                    ┌──────────────────────────────┐
   Du (Chat) ──────►│  Koordinator-Agent           │  läuft im ERPNext-Container
                    │  - prüft & koordiniert        │  - schaut im ERP nach Fehlern
                    │  - kennt die Stücklisten-     │  - sagt jedem Worker GENAU,
                    │    Anforderungen              │    was/wie er es braucht
                    └──────┬─────────────────┬──────┘
                           │ Aufgabe         │ Aufgabe
                           ▼                 ▼
                 ┌──────────────────┐ ┌──────────────────┐
                 │ Worker 1: WSCAD  │ │ Worker 2: Solid  │
                 │ Upload-Skript    │ │ Edge Upload-Skript│
                 │ für Artikel      │ │ für Artikel      │
                 └────────┬─────────┘ └─────────┬────────┘
                          │ Rückfrage/Ergebnis  │
                          └─────────► Koordinator ◄┘   (kein manuelles Verkuppeln)
```

Heute machst du das Weiterreichen zwischen Koordinator und den beiden Worker-
Fenstern selbst. Mit dem Dashboard läuft es über die Mailbox: der Koordinator legt
präzise Aufgaben in die Worker-Inboxes, Worker schreiben Rückfragen/Ergebnisse
zurück, du **supervidierst statt zu verkuppeln** — alles in einer Oberfläche,
sichtbar im MCP-Monitor.

**Was dafür gegenüber dem MVP noch fehlt (kleine, gezielte Erweiterungen):**

| Fehlt | Warum nötig | Umsetzung |
|-------|-------------|-----------|
| **Agent-↔-Agent-Nachrichten** | aktuell nur Orchestrator → Agent. Der Koordinator muss Worker direkt beauftragen **und** Worker müssen dem Koordinator Rückfragen stellen | Mailbox um `from`/`to` erweitern; ein Agent darf in fremde Inboxes schreiben (Koordinator → Worker) und in die Koordinator-Inbox antworten |
| **Koordinator-Rolle** | ein Agent, der zugleich Aufträge entgegennimmt, delegiert und Ergebnisse aggregiert | im `agents.yaml` ein `role: coordinator`; der bekommt die Delegations-Tools (`create_task`, `read_responses`) ebenfalls |
| **Rückfrage-Flow** (`needs_confirm`) | „Worker braucht Klärung" darf nicht im Fenster verschwinden | Status `needs_confirm` ist im Schema schon da — im UI als Banner/Antwortfeld sichtbar machen, Antwort geht zurück an den Fragenden |
| **Domänen-Tool ERP-Lookup** | der Koordinator „schaut im ERP nach Fehlern" | ein MCP-Tool `erp_query(...)` (REST-Call an ERPNext) statt SSH/Shell — sauber gekapselt |
| **Live-Mitlesen je Agent** | du willst jederzeit in einen Worker reinschauen | pro Agent ein Konversations-/Log-Stream im Dashboard (über die Outbox + optional WebSocket-Events) |

Kurz: der Sprung vom „ein Orchestrator delegiert" zum „mehrere Agenten reden
miteinander, ich schaue zu" ist **klein** — vor allem die Mailbox um Absender/
Empfänger erweitern und die `needs_confirm`-Rückfragen im UI sichtbar machen.

**✅ Umgesetzt (Mailbox v2, getestet):**
- Mailbox-Envelopes mit `kind` (task/message/question/answer) + `sender`/`to`;
  `Mailbox.post()` / `read_inbox()`; Watcher führt **nur** `kind=task` aus
  (Nachrichten/Rückfragen bleiben liegen) — real getestet inkl. Frage→Antwort-Roundtrip.
- Generische MCP-Tools: `send_task`, `send_message`, `ask`, `answer`, `inbox`
  (kein ERPNext im Code). `create_task` bleibt als Alias.
- Rückfrage-Flow im Dashboard: Banner über alle Agenten (`GET /api/questions`),
  Beantworten via `POST /api/questions/{agent}/{qid}/answer` → Antwort landet in
  der Inbox des Fragestellers, Frage wird erledigt.
- Config-getriebene Integrationen (`config/integrations.yaml` + `call_integration`-
  Tool) statt hartem ERP-Tool → jeder Workflow hängt eigene HTTP-Endpunkte an.

Offen: Koordinator-Rolle aktiv nutzen (role in agents.yaml ist da), Live-Mitlesen
je Agent als Stream.

### Modus B — Admin-/Fleet-Verwaltung (sekundär, andere Leitplanken)

Dasselbe Gerüst kann eine **Natural-Language-Ops-Konsole** für mehrere Linux-
Maschinen sein (Diagnose, kontrollierte Aktionen). Hier ändert sich die
Ausführungsphilosophie grundlegend, weil ein LLM, der frei Shell auf Prod-Servern
ausführt, das größte denkbare Risiko ist.

**Pivot gegenüber Modus A:**

| Thema | Modus A (Dev) | Modus B (Admin/Fleet) |
|-------|---------------|------------------------|
| LLM-Ort | ggf. Claude-Code je Host | **nur zentral** im Container; Hosts bleiben „dumm" |
| Ausführung | Agent baut/ändert Code frei | **deterministisch** über `command_template` (Variante A) oder generiertes Ansible-Playbook |
| Default | schreibend | **read-only**; Diagnose ohne Rückfrage |
| Mutierende Aktion | direkt | nur über **Approval-Gate** (`needs_confirm`) + **Allowlist** |
| Skalierung | wenige Agenten | **Host-Gruppen** + paralleler Fan-out + Ergebnis-Aggregation |

**Empfohlene Leitplanken für Modus B:**

- **Agentless bleiben** — Orchestrator führt über SSH-`command_template` aus; Variante B (LLM pro Host) entfällt hier.
- **Default read-only** — lesende Diagnose ohne Rückfrage, **jeder** mutierende Befehl durch `needs_confirm`.
- **Command-Allowlist** statt freier Shell — der LLM wählt nur aus erlaubten Templates.
- **Stärkste Variante:** LLM *generiert* ein Ansible-Playbook → Mensch sieht das Diff → Ansible führt idempotent aus (Idempotenz, Audit, Rollback geschenkt).
- **Host-Gruppen + Fan-out + Aggregation**, **RBAC + per-Host-Scoping** sobald mehr als eine Person zugreift.

**Einordnung:** Als ernstzunehmender Ansible-/Salt-Ersatz konkurriert Modus B
schlecht (Reife, Idempotenz, RBAC). Als **LLM-Schicht obendrauf** für Diagnose und
freigegebene Aktionen über eine kleine/mittlere Flotte ist er sinnvoll — aber nur
mit Allowlist, Approval-Gate, Dry-Run und Audit als Pflicht, nicht Fußnote.

> Reihenfolge: **Modus A zuerst** (das ist der reale Bedarf). Modus B ist additiv
> und teilt sich Mailbox, Approval-Flow und Dashboard mit Modus A.
