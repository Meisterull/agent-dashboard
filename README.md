# agent-dashboard

Ein Single-Container-Dashboard zur zentralen Steuerung mehrerer entfernter
**Claude-Code-Agenten**. Ein **Orchestrator-LLM** im Container plant Aufgaben,
legt Projektdateien an und delegiert Arbeit über einen **MCP-Server** an die
Agenten. Der Transport zu den Agenten läuft über eine **Datei-Mailbox**
(inbox/outbox als JSON) — robust, offline-tolerant, debugbar.

> Weitere Dokumente: **`CLAUDE.md`** (Arbeitsanleitung/Konventionen),
> **`PROJECT.md`** (ursprünglicher Plan + Designentscheidungen + Statushistorie).

---

## Inhalt

1. [Überblick](#überblick)
2. [Architektur](#architektur)
3. [Datenflüsse](#datenflüsse)
4. [Komponenten](#komponenten)
5. [API-Referenz](#api-referenz)
6. [Konfiguration](#konfiguration)
7. [Schnellstart](#schnellstart)
8. [Deployment (Docker)](#deployment-docker)
9. [Sicherheit](#sicherheit)
10. [Status & getestet](#status--getestet)
11. [Roadmap](#roadmap)

---

## Überblick

Das Dashboard besteht aus vier Schichten:

- **Frontend** (React) — komplettes Dashboard: Chat, Dateibaum, SSH-Terminal,
  MCP-Monitor (Aufgaben), Einstellungen.
- **Backend** (FastAPI) — REST + WebSocket; betreibt den Orchestrator-Loop und
  liefert Dashboard-Daten (Dateien, Tasks, Verbindungen, Settings).
- **MCP-Server** — stellt dem Orchestrator-LLM strukturierte Tools bereit
  (`create_task`, `read_responses`, `list_agents`, `write/read_project_file`).
- **Datei-Mailbox** — die Brücke zu den entfernten Agenten. Jeder Agent hat
  `inbox/` (Aufgaben) und `outbox/` (Rückmeldungen) unter `/workspace/mailboxes/`.

Der Orchestrator führt **selbst keinen Code** auf den Ziel-Rechnern aus — er
delegiert nur. Ein kleiner Watcher auf jedem Agenten-PC zieht Aufgaben aus seiner
Inbox, startet dort Claude-Code und schreibt das Ergebnis in die Outbox.

**Multi-Provider:** Der Orchestrator läuft über eine provider-neutrale Schicht
(`app/llm.py`) — umschaltbar per `ORCH_PROVIDER`: **anthropic** (Claude, braucht
`ANTHROPIC_API_KEY`) oder **ollama** (lokal, **kein Key**, z.B. `gpt-oss:120b`).
Derselbe Agentic-Loop und dieselben MCP-Tools für beide.

---

## Architektur

```
                         Browser
                            │ HTTP / WebSocket
                            ▼
                  ┌───────────────────┐
                  │   nginx (80/443)  │   Reverse Proxy, SSL, liefert dist/
                  │   / → Frontend    │
                  │   /api → :5000    │
                  │   /ws  → :5000    │
                  └─────────┬─────────┘
                            ▼
        ┌──────────────────────────────────────────┐
        │   FastAPI (backend/main.py, :5000)        │
        │   ├ /api/chat → orchestrator_core.run_turn│──MCP(:9000)──┐
        │   ├ /api/files, /api/agents/.../tasks     │              │
        │   ├ /api/connections, /api/settings       │              ▼
        │   └ /ws/ssh/<name> → asyncssh             │   ┌────────────────────┐
        └──────────────────┬───────────────────────┘   │  mcp_server.py     │
                           │                            │  (Tools)           │
                           ▼                            │  create_task, …    │
            /workspace/mailboxes/<agent>/               └─────────┬──────────┘
              ├ inbox/<task>.json   ◄───────────────────── create_task
              └ outbox/<task>-response.json ──► read_responses
                           ▲
                           │ SSHFS / SFTP / SSH
              ┌────────────┴───────────────┐
              │  Agenten-PC                │
              │  scripts/agent_watcher.py  │  zieht inbox, startet claude-code,
              │  → claude --print          │  schreibt outbox (atomar)
              └────────────────────────────┘
```

Alle Teile laufen im selben Container, beaufsichtigt von **supervisord**
(nginx · uvicorn · mcp_server · optional telegram-bot).

---

## Datenflüsse

**1. Aufgabe delegieren**

```
User schreibt im Chat
  → POST /api/chat
  → run_turn: Claude plant, ruft via MCP create_task("frontend", "...")
  → mcp_server schreibt /workspace/mailboxes/frontend/inbox/task-0001.json (atomar)
  → Antwort + Tool-Call-Chips zurück ans Frontend
```

**2. Agent arbeitet (Variante B: Pull über Mailbox)**

```
agent_watcher.py auf dem Agenten-PC pollt inbox/
  → verschiebt task-0001.json atomar nach .processing/ (exklusiver Anspruch)
  → startet `claude --print "<instruction>"`
  → schreibt outbox/task-0001-response.json (tmp + fsync + os.replace)
```

**3. Ergebnis sichtbar machen**

```
write_response legt das Ergebnis als kind="response" in die Inbox des
Auftraggebers (sender des Tasks) — der sieht es im normalen inbox()-Zyklus
  → GET /api/agents/frontend/tasks zeigt Inbox/Outbox mit Status-Badges
  → read_responses(worker) liest zusätzlich das Outbox-Archiv
```

Status eines Tasks: `pending` · `running` · `done` · `error` · `needs_confirm`.

---

## Komponenten

### Backend (`backend/`)

| Datei | Zweck |
|-------|-------|
| `main.py` | FastAPI-App: alle `/api`-Endpunkte + `/ws/ssh/<name>` |
| `mcp_server.py` | MCP-Server (Streamable-HTTP, `127.0.0.1:9000/mcp`), Tools für den Orchestrator |
| `orchestrator.py` | CLI-Variante des Orchestrators (Chat im Terminal) |
| `app/orchestrator_core.py` | Gemeinsamer Kern: `mcp_session()` + `run_turn()`. CLI **und** API nutzen ihn |
| `app/llm.py` | Provider-neutrale LLM-Schicht (ein Loop, Backends **ollama** + **anthropic**) |
| `app/mailbox.py` | Atomare Mailbox v2: Envelopes (task/message/question/answer/response), `post`, `read_inbox`, `claim_tasks`/`claim_task` (nur Tasks), `write_response` (rettet `sender` als `to`, legt das Ergebnis als `response` in die Inbox des Auftraggebers, räumt inbox **und** .processing), `mark_read` (Archiv), `normalize_envelope` |
| `app/files.py` | Pfad-sichere Datei-Ops (`list_dir`, `read_file`) für den Dateibaum |
| `app/config.py` | Settings (`settings.json`) + Verbindungen (`agents.yaml`, ohne Credentials) |
| `app/integrations.py` | Config-getriebene HTTP-Integrationen (`integrations.yaml`, generisch) |
| `app/mcp_scope.py` | Kanal-Identität + Tool-Allowlists je Agent (Port-Vergabe, Port-Map, `resolve_ident`) |
| `app/auto_watcher.py` | Automatikmodus: hält pro Agent einen Remote-Watcher per SSH (`/api/automatik*`) |
| `app/ssh_bridge.py` | WebSocket ↔ asyncssh für das Browser-Terminal |

**MCP-Tools** (im `mcp_server.py`, pfad-gehärtet gegen `WORKSPACE_DIR`):
- Delegation: `list_agents()` · `send_task(to, instruction, sender?, project?)` (`create_task` als Alias) · `read_responses(worker, for_sender?)` (Outbox-Archiv des Bearbeiters)
- Task-Lebenszyklus (Agent-Seite): `claim_task(agent, task_id)` (→ "in Arbeit") · `complete_task(agent, task_id, result, status?, log?)` — das Gegenstück zu `send_task`: legt das Ergebnis als `kind="response"` in die Inbox des Auftraggebers (`sender` des Tasks), archiviert es in der Outbox und räumt den Task ab
- Agent-↔-Agent: `send_message(to, text, sender?)` · `ask(to, question, sender?, reply_to?)` · `answer(to, text, sender?, reply_to)` · `inbox(agent, kind?)` · `mark_read(agent, envelope_id)` (Gelesenes archivieren, sonst kommt es bei jedem `inbox()` wieder)
- Projektdateien: `write_project_file(...)` · `read_project_file(...)`
- Integrationen (config-getrieben): `list_integrations()` · `call_integration(name, method, path, body?)`

**Kanal-Identität + Tool-Scoping** (`app/mcp_scope.py`): Neben dem freien Kanal
`:9000` (Orchestrator, intern) lauscht **pro SSH-Agent ein eigener, an dessen
Namen gebundener Port** (automatisch ab `:9100`, explizit via `mcp_local_port`).
Der Reverse-Tunnel des Agenten forwardet auf genau diesen Port — die Identität
kommt fälschungssicher aus dem Kanal, nicht aus einem Parameter: auf gebundenen
Kanälen werden `agent`/`sender` aus der Bindung abgeleitet (Parameter einfach
weglassen), abweichende Werte lehnt der Server ab. Optional begrenzt eine
Allowlist am Agenten (`tools: [inbox, mark_read, …]` in `agents.yaml`), welche
Tools der Kanal überhaupt in der Tool-Liste sieht — so lässt sich z. B. ein
Claude-Desktop-Client auf reine Mailbox-Nutzung beschränken. Die aktive
Port-Zuordnung schreibt der Server nach `/workspace/config/mcp_ports.json`
(liest der Tunnel); jeder Tool-Aufruf wird mit Kanal-Namen geloggt. Agenten ohne
Eintrag in der Map fallen auf `:9000` zurück — **außer** sie haben eine
Allowlist, dann pausiert ihr Tunnel, bis der MCP-Dienst neu gestartet wurde
(die Allowlist wird nie über den freien Kanal umgangen).

**Automatikmodus** (`app/auto_watcher.py`, Toggle im Agenten-Panel): pro Agent
per Klick ein-/ausschaltbar — das Dashboard hält dann per SSH einen
`agent_watcher.py --mcp-url …` auf dem Agenten-PC, der die Inbox selbständig
abarbeitet (Script wird bei jedem Start per SFTP aktuell hingelegt, kein
Installationsschritt; kein SSHFS-Mount nötig). Der gewünschte Zustand steht in
`settings.json` und übersteht Neustarts; angezeigt wird der ECHTE
Prozess-Zustand. „Aus" stoppt sanft (laufender Claude-Lauf darf fertig werden
und sein Ergebnis abliefern), der globale **Not-Aus** im Panel-Kopf stoppt alle
Automatiken sofort hart. Optional je Agent in `agents.yaml`: `workdir`
(Basis-Arbeitsverzeichnis für Claude), `python` (Default `python3`) und
`claude_bin` (Pfad zum Claude-Binary, falls die automatische Suche — PATH plus
`~/.local/bin` & Co. — nicht greift). Das `project`-Feld eines Tasks wählt ein
Unterverzeichnis unter `workdir` — so bedient ein Agent mehrere Repos; ohne
`project` läuft Claude im `workdir` selbst, ein unbekanntes oder ausbrechendes
`project` lässt den Task mit Klartext scheitern statt im falschen Verzeichnis
zu laufen. Vor dem ersten Task prüft der Watcher
die Umgebung (Binary, Arbeitsverzeichnis) und hält bei Serien sofortiger
Fehlschläge an, statt die Warteschlange zu verbrauchen.

Stellt der Agent während eines Tasks eine Rückfrage (`ask`), wird der Task
beim Abschluss **geparkt** statt als erledigt gemeldet: er bleibt als „wartet
auf Antwort" (needs_confirm) sichtbar und läuft nach der Antwort automatisch
erneut — mit der Antwort im Kontext. Fehlgeschlagene Tasks wandern nach
`inbox/.failed/` und behalten ihre Aufgabenbeschreibung in der Antwort.

Achtung: im Automatikmodus arbeitet `claude --print` unbeaufsichtigt —
Berechtigungs-Rückfragen von Claude Code selbst kann headless niemand
beantworten. Was der Lauf dürfen soll, gehört deshalb je Agent in
`agents.yaml`: `permission_mode` (z. B. `acceptEdits`) und `allowed_tools`
(z. B. `[Edit, Write, "Bash(git:*)"]`) werden an `claude --permission-mode` /
`--allowed-tools` durchgereicht — die Betriebsberechtigung steht damit im
Dashboard-Config statt unsichtbar in der Settings-Datei des Agenten-PCs.
Verweigert Claude ein Werkzeug trotzdem, erscheint das ausdrücklich im Log
der Antwort („Berechtigung verweigert: …") statt nur im Fließtext des
Ergebnisses unterzugehen.

### Frontend (`frontend/src/`)

| Datei | Zweck |
|-------|-------|
| `App.jsx` | Dashboard-Layout (Top-Bar · Dateibaum · Chat · Terminal · MCP-Monitor) |
| `api.js` | zentrale fetch-Helfer (relative `/api`-Pfade) |
| `components/TopBar.jsx` | Kopfzeile, Session-Anzeige, Einstellungen-Button |
| `components/Chat.jsx` | Orchestrator-Chat mit Tool-Call-Chips |
| `components/FileTree.jsx` | lazy-ladender Dateibaum über `/api/files` |
| `components/FileViewer.jsx` | Datei-Inhalt im Modal |
| `components/AgentsPanel.jsx` | MCP-Monitor: Inbox/Outbox je Agent mit Status-Badges |
| `components/TerminalPanel.jsx` / `Terminal.jsx` | SSH-Tabs + xterm.js über `/ws/ssh`; pro Verbindung mehrere Terminals (⧉-Knopf, z. B. Claude Code + eigene Shell nebeneinander); Fenster schließen detacht nur — die Session läuft serverseitig weiter (`SSH_GRACE_SECONDS`, Default 24 h, `0` = unbegrenzt) und lässt sich auch von einem anderen PC wieder öffnen; beendet wird per ⏻-Knopf |
| `components/Settings.jsx` | Einstellungen-Modal (Provider/Sprache/Telegram) |
| `components/Modal.jsx` | generischer Modal-Container |

### Agenten-Seite (`scripts/`)

`agent_watcher.py` — läuft auf dem **entfernten** PC, nur Standardlib (kein pip
nötig). Pollt die Inbox, startet Claude-Code, schreibt die Outbox atomar.
`--dry-run` echoed die Aufgabe zurück (Test ohne echtes Claude-Code).

---

## API-Referenz

Alle Endpunkte unter `/api` (nginx proxyt `/api` und `/ws` an `:5000`).

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `GET` | `/api/health` | Liveness (Docker-Healthcheck), unabhängig von MCP/Anthropic |
| `GET` | `/api/agents` | Liste der Agenten (= Mailbox-Ordner) |
| `GET` | `/api/agents/{name}/tasks` | Tasks: `{inbox, outbox}` eines Agenten (beanspruchte als `running`) |
| `GET` | `/api/agents/{name}/inbox?kind=` | Alle Eingänge (Tasks + Nachrichten + Rückfragen), normalisiert |
| `POST` | `/api/tasks/{agent}/{task_id}/close` | Hängengebliebenen Task von Hand abschließen (`{status?, result?}`) |
| `GET` | `/api/questions` | Offene Rückfragen (`needs_confirm`) über alle Agenten |
| `POST` | `/api/questions/{agent}/{qid}/answer` | Rückfrage beantworten → Antwort an Fragesteller |
| `GET` | `/api/integrations` | Konfigurierte Integrationen (Name + Methoden, ohne Secrets) |
| `POST` | `/api/chat` | Body `{message, session_id?}` → `{session_id, reply, tool_calls}` |
| `GET` | `/api/files?path=` | Verzeichnis auflisten (path-traversal-sicher) |
| `GET` | `/api/files/content?path=` | Dateiinhalt (begrenzt auf 256 KB) |
| `GET` | `/api/connections` | SSH-Verbindungen aus `agents.yaml` (ohne Credentials) |
| `GET` | `/api/settings` | editierbare UI-Settings |
| `PUT` | `/api/settings` | Settings speichern (Whitelist) |
| `GET` | `/api/automatik` | Automatikmodus: Not-Aus + gewünschter/echter Status je Agent |
| `POST` | `/api/automatik/{name}` | Body `{an}` — Automatik eines Agenten an/aus (aus = sanft) |
| `POST` | `/api/automatik/notaus` | Body `{an}` — globaler Not-Aus (an = alle sofort hart stoppen) |
| `GET` | `/api/ssh/sessions` | laufende Terminal-Sessions (`name`, `sid`, `attached`, `age`, `idle`) |
| `DELETE` | `/api/ssh/{name}/session?sid=` | Terminal-Session explizit beenden (⏻-Knopf) |
| `WS` | `/ws/ssh/{name}` | SSH-Terminal-Bridge (JSON `{type:"data"/"resize"}` rein, Text raus) |

---

## Konfiguration

```
/workspace/                 (Volume, beschreibbar)
├── projects/               vom Orchestrator angelegte Projekte
├── mailboxes/<agent>/      inbox/ · outbox/ · inbox/.processing/
├── config/
│   ├── settings.json       editierbare UI-Settings (vom Dashboard gepflegt)
│   ├── agents.yaml         Agenten: SSH-Verbindungen, Rolle (coordinator/worker)
│   ├── integrations.yaml   benannte HTTP-Integrationen je Workflow
│   └── llm-providers.yaml  Provider-Reihenfolge (Claude/OpenRouter/Ollama)
├── logs/
└── ssl/                    self-signed Platzhalter, bis echtes Zertifikat da ist
```

**`.env`** (aus `.env.example` kopieren) — Secrets, nie committen:
`ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `OLLAMA_BASE_URL`, `TELEGRAM_*`,
`SESSION_SECRET`, …

**Wichtige Env-Variablen:** `WORKSPACE_DIR` (Default `/workspace`),
`DATA_CONFIG_DIR` (`/workspace/config`), `MCP_HOST`/`MCP_PORT` (`127.0.0.1`/`9000`),
`API_PORT` (`5000`), `ORCH_MODEL` (`claude-opus-4-8`).

---

## Schnellstart

### Nur den Vertical Slice testen (ohne LLM, ohne pip)

`mailbox.py` und `agent_watcher.py` brauchen nur die Python-Standardlib:

```bash
ROOT=/tmp/mb/mailboxes
PYTHONPATH=backend python3 -c "from app.mailbox import Mailbox, Task; \
  Mailbox('$ROOT','frontend').put_task(Task('task-0001','frontend','Erstelle login.html'))"
python3 scripts/agent_watcher.py --agent frontend --root "$ROOT" --dry-run --once
cat "$ROOT"/frontend/outbox/*-response.json   # status: done
```

### Vollständig lokal (Frontend + Backend + LLM)

```bash
# 1. Frontend (Terminal A)
cd frontend && npm install && npm run dev          # http://localhost:5173

# 2. Backend-Deps + Keys
cd backend && pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
export WORKSPACE_DIR=/tmp/mb

# 3. MCP-Server (Terminal B)
cd backend && python -m mcp_server                 # :9000

# 4. FastAPI (Terminal C)
cd backend && uvicorn main:app --host 127.0.0.1 --port 5000

# 5. optional: ein Test-Agent (Terminal D)
python scripts/agent_watcher.py --agent frontend --root "$WORKSPACE_DIR/mailboxes" --dry-run
```

Dann im Browser `http://localhost:5173` öffnen und chatten — die Aufgabe landet
real in der Mailbox, der Watcher bearbeitet sie, das Ergebnis erscheint im
MCP-Monitor.

---

## Deployment (Docker)

> **Schritt-für-Schritt-Anleitung: siehe [`START.md`](START.md)** — `.env`,
> Konfig, erster Durchlauf, Remote-Agenten, Troubleshooting.

```bash
cp .env.example .env        # ausfüllen (mindestens ANTHROPIC_API_KEY)
docker compose up --build
# Dashboard:  https://localhost:8443   (self-signed Zertifikat im MVP)
```

Der Build ist **mehrstufig**: Node baut das Frontend, ein Build-Stage kompiliert
die Python-Wheels, das schlanke Runtime-Image enthält weder `npm` noch
`build-essential`. **supervisord** startet nginx + uvicorn + mcp_server (+ optional
telegram-bot) und startet abgestürzte Dienste neu.

Härtung in `docker-compose.yml`: non-root, `no-new-privileges`, `cap_drop: ALL`
+ nur nötige Caps, Healthcheck auf `/api/health`, **kein** `docker.sock`-Mount,
FastAPI/MCP-Ports nicht nach außen veröffentlicht.

---

## Sicherheit

- **Path-Traversal** unterbunden: jeder Workspace-Zugriff resolved + gegen
  `WORKSPACE_DIR` geprüft (`app/files._safe`, `_safe` im MCP-Server). Getestet.
- **Atomare Mailbox**: `tmp` + `fsync` + `os.replace` → keine halb gelesenen
  JSON-Dateien; `.processing/`-Claim verhindert Doppel-Pickup.
- **Secrets** nur in `.env`/Docker-Secrets — nie ins Frontend, nie im Klartext in
  `agents.yaml`. `/api/connections` liefert nur Name/Host/User/Modus.
- **Settings-Whitelist** (`config.ALLOWED_KEYS`): `/api/settings` ignoriert
  unbekannte Keys.
- **SSH**: Key-Only vorgesehen, `sshpass` bewusst entfernt; Credentials verlassen
  den Server nie Richtung Frontend.
- **Container**: non-root `app`-User, read-only Vorlagen-Config, kein Host-Socket.

---

## Status & getestet

| Teil | Verifikation |
|------|--------------|
| Mailbox-Roundtrip (atomar) | ✅ real getestet (Orchestrator→inbox→Watcher→outbox) |
| `files.py` / `config.py` (Dateibaum, Settings, Path-Traversal) | ✅ real getestet (3 Traversal-Angriffe blockiert, Settings-Whitelist greift) |
| Frontend-Build | ✅ `npm run build` grün (xterm gebündelt) |
| **Agentic-Loop gegen echtes LLM** (Tool-Calls → Mailbox, ein- und mehrstufig) | ✅ **real getestet** via Ollama (`gpt-oss:120b`), provider-neutrale Schicht |
| MCP-Server · FastAPI (Server live) | ⚠️ kompiliert (`py_compile`); Server selbst hier nicht gestartet (kein pip für `fastapi`/`mcp`) |
| SSH-Bridge (`/ws/ssh`) | ⚠️ real implementiert, ohne SSH-Ziel nicht getestet |

Der LLM-Pfad und der Container-Lauf scheitern hier nur an der **Umgebung**
(kein `pip`/`venv`, kein API-Key), nicht am Code. Erster echter End-to-End-Test:
`docker compose up --build`.

---

## Roadmap

1. ✅ Mailbox-Roundtrip (atomar)
2. ✅ MCP-Server an Orchestrator-Loop (Claude API, CLI)
3. ✅ FastAPI-Wrapper (`/api/health`, `/api/chat`, `/api/agents`, …)
4. ✅ Vollständige React-Dashboard-View (Chat · Dateibaum · Terminal · MCP-Monitor · Settings)
5. ✅ **Multi-Provider** (anthropic + ollama) über `app/llm.py` — Agentic-Loop gegen Ollama real getestet
6. **Nächste Schritte (additiv):**
   - erster echter Container-Lauf (`docker compose up`) end-to-end
   - OpenRouter als dritter Provider + automatischer Fallback bei Ausfall
   - Telegram-Bot als zweiter Eingangskanal
   - echtes SSL/Domäne (Let's Encrypt) statt self-signed
   - Persistenz (SQLite) für Chat-History/Tasks statt in-memory
   - `needs_confirm`-Flow (Rückfragen der Agenten an den User)

Details und Designentscheidungen: siehe **`PROJECT.md`**.
