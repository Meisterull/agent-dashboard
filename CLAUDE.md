# CLAUDE.md

Arbeitsanleitung für Claude Code in diesem Repo. Kurz und aktuell halten.
Ausführliche Doku: `README.md`. Ursprünglicher Plan + Statushistorie: `PROJECT.md`.

## Was das ist

Single-Container-Dashboard, über das ein zentraler **Orchestrator** (LLM) Aufgaben
plant und an mehrere entfernte **Claude-Code-Agenten** delegiert. Transport zwischen
Container und Agenten ist eine **Datei-Mailbox** (inbox/outbox als JSON). Der
Orchestrator bekommt seine Tools über einen **MCP-Server** im Container.

```
Browser ─HTTP/WS─ nginx ─ FastAPI (orchestrator) ─MCP─ mcp_server (Tools)
                                   │
                                   └─ /workspace/mailboxes/<agent>/{inbox,outbox}
                                          ▲
                          Agent-PC: agent_watcher.py ─ startet claude-code
```

## Verzeichnisstruktur

```
backend/
  main.py                  FastAPI-App (alle /api-Endpunkte + /ws/ssh)
  mcp_server.py            MCP-Server (Tools fürs Orchestrator-LLM), Streamable-HTTP :9000
  orchestrator.py          CLI-Variante des Orchestrators
  app/
    orchestrator_core.py   gemeinsamer Kern: MCP-Anbindung + run_turn (provider-neutral)
    llm.py                 Provider-Schicht: ollama (Standardlib-HTTP) + anthropic (SDK lazy)
    mailbox.py             atomare Mailbox v2: Envelopes (task/message/question/answer),
                           post/read_inbox/claim_tasks(nur task)/normalize_envelope
    files.py               pfad-sichere Datei-Ops (Dateibaum, Editor, Up-/Download)
    remote_files.py        SFTP-Datei-Ops auf den Agenten-PCs (/api/remote/…)
    chat_store.py          SQLite-Persistenz der Chat-Sessions (/workspace/chat.db)
    ssh_connect.py         zentraler SSH-Connect mit Host-Key-Pinning (TOFU,
                           /workspace/config/known_hosts) — von bridge/SFTP/tunnel genutzt
    config.py              Settings (settings.json) + Verbindungen (agents.yaml)
    integrations.py        config-getriebene HTTP-Tools (integrations.yaml), generisch
    ssh_bridge.py          WebSocket ↔ asyncssh (Terminal)
    mcp_tunnel.py          Reverse-SSH-Tunnel: MCP-Server auf die Agenten-PCs
                           (supervisord `mcp-tunnel`, Gate MCP_TUNNEL_ENABLED)
  requirements.txt
frontend/                  React 18 + Vite 6 + Tailwind v4 (komplettes Dashboard)
  src/App.jsx              Layout; src/api.js fetch-Helfer; src/components/*.jsx
scripts/agent_watcher.py   Remote-Watcher (Variante B), nur Standardlib
                           (--mcp-hint: Identitäts-/Tool-Kontext voranstellen)
scripts/setup_agent_pc.sh  auf dem Agenten-PC: Dashboard-MCP in Claude-Code
                           registrieren (http://127.0.0.1:<mcp_port>/mcp)
Dockerfile · docker-compose.yml · entrypoint.sh · supervisord.conf · nginx/
```

## Befehle

```bash
# Frontend
cd frontend && npm install && npm run dev      # http://localhost:5173 (proxyt /api,/ws)
cd frontend && npm run build                   # erzeugt dist/ (nginx liefert es aus)

# Backend (braucht: pip install -r backend/requirements.txt + ANTHROPIC_API_KEY)
cd backend && python -m mcp_server             # Tools, :9000
cd backend && uvicorn main:app --host 127.0.0.1 --port 5000
cd backend && python orchestrator.py           # CLI-Chat statt Web

# Vertical Slice ohne LLM (nur Standardlib, läuft überall)
python scripts/agent_watcher.py --agent frontend --root /tmp/mb/mailboxes --dry-run --once

# Ganzer Stack
docker compose up --build                      # nginx+api+mcp+telegram via supervisord
```

## Architektur-Kernpunkte

- **Tools laufen über MCP, nicht hartcodiert.** `orchestrator_core.run_turn` öffnet pro
  Runde eine MCP-Session (`mcp_session()`), übersetzt die Tools ins Anthropic-Format
  und führt Tool-Calls über `session.call_tool` aus. CLI und FastAPI teilen diesen Kern.
- **Mailbox ist die riskanteste Primitive** → `app/mailbox.py` schreibt atomar
  (`tmp` + `fsync` + `os.replace`) und beansprucht Tasks exklusiv über `.processing/`.
  Nie naiv `open(...).write()` für Mailbox-JSON.
- **Multi-Provider (`app/llm.py`):** `ORCH_PROVIDER=anthropic|ollama`. Neutrale
  History (`user`/`assistant`+`tool_calls`/`tool`), erst beim Aufruf ins Provider-
  Format übersetzt. Ollama über Standardlib-HTTP (kein pip), Anthropic lazy via SDK
  (`claude-opus-4-8`, adaptives Thinking, `effort: high`). Agentic-Loop gegen Ollama
  real getestet. **Beim Erweitern provider-neutral bleiben** — nichts Anthropic-
  Spezifisches in orchestrator_core/main.
- **MCP-Rollen:** (1) Werkzeugkasten des Orchestrators. (2) Transport zu Agenten =
  Reverse-SSH-Tunnel (`app/mcp_tunnel.py`): pro SSH-Agent aus agents.yaml lauscht auf
  dem Agenten-PC 127.0.0.1:<mcp_port> (Default 9000) auf den Container-MCP. Claude-Code
  dort einmalig via `scripts/setup_agent_pc.sh` registrieren — dann können Agenten
  selbst inbox/ask/answer/send_message nutzen. Der MCP-Port bleibt trotzdem
  unveröffentlicht (nur Loopback + Key-Auth-Tunnel). Einträge ohne existierende
  key_file werden übersprungen; agents.yaml wird alle 60 s neu eingelesen.
  Aufgaben-Transport an die Watcher bleibt die Datei-Mailbox.
- **Agent-↔-Agent (Mailbox v2):** Envelopes haben `kind` (task/message/question/answer)
  + `sender`/`to`. MCP-Tools: `send_task`/`send_message`/`ask`/`answer`/`inbox`. Der
  Watcher führt **nur** `kind=task` aus. `needs_confirm`-Rückfragen erscheinen im
  Dashboard (`/api/questions`, Banner) und werden dort beantwortet.
- **Generisch, nicht workflow-spezifisch:** Integrationen kommen aus `integrations.yaml`
  (`call_integration`-Tool); jedes Zielsystem ist nur eine Konfig-Zeile. Beim Erweitern
  nichts Workflow-Spezifisches im Code hartverdrahten.

## Konventionen / Sicherheit

- **Path-Traversal:** jeder Workspace-Zugriff über `app/files._safe` bzw. `_safe` im
  MCP-Server (resolve + Prüfung gegen WORKSPACE). Nie roh joinen.
- **Secrets:** API-Keys/Tokens nur in `.env`/Docker-Secrets, nie ins Frontend, nie in
  `agents.yaml` im Klartext. `/api/connections` gibt nur Name/Host/User zurück.
- **Settings-Whitelist:** `config.ALLOWED_KEYS` — `/api/settings` ignoriert unbekannte Keys.
- **Container:** non-root `app`-User, `cap_drop: ALL`, kein `docker.sock`-Mount.
- **`/api/health` darf NICHT von MCP/Anthropic abhängen** (Docker-Healthcheck).

## Status (Stand: 26.06.2026)

| Teil | Verifikation |
|------|--------------|
| Login/Auth (Cookie, API+WS) | ✅ E2E getestet 12.07.2026 |
| Chat-Persistenz (SQLite) + Markdown | ✅ E2E getestet 12.07.2026 (Neustart + Kontext) |
| Host-Key-Pinning (TOFU, known_hosts) | ✅ E2E getestet 12.07.2026 (inkl. Manipulations-Test) |
| Terminal: persistente Sessions + Reconnect | ✅ E2E gegen Test-sshd 12.07.2026 |
| Datei-Browser: Editor/Up-/Download/mkdir/rename/delete (WS+SFTP) | ✅ E2E getestet 12.07.2026 |
| Mailbox-Roundtrip | ✅ real getestet |
| files.py / config.py (Dateibaum, Settings, Path-Traversal) | ✅ real getestet |
| Frontend-Build | ✅ `npm run build` grün |
| MCP-Server, Orchestrator, FastAPI (LLM-Pfad) | ✅ läuft produktiv gegen Ollama (seit 26.06.2026) |
| SSH-Bridge (`/ws/ssh`) | ✅ E2E getestet 12.07.2026 (persistente Sessions, sid-Reattach) |
| MCP-Tunnel (`app/mcp_tunnel.py`) | ✅ E2E verifiziert 07.07.2026 (Host als Test-Agent `lokal`, Port 9100: Handshake + `claude mcp list` ✔ Connected) |

Wenn du etwas änderst: bei reinen Standardlib-Modulen (mailbox, files, config, watcher)
gibt es echte Tests/Smoke-Checks — nutze sie. Beim LLM-Pfad ehrlich kennzeichnen, was
verifiziert ist und was nicht.

## Gotchas

- **Auth:** alle /api-Routen außer health/auth verlangen das Session-Cookie
  (`app/auth.py`, Passwort = ADMIN_INITIAL_PASSWORD). Für curl-Tests erst
  `POST /api/auth/login`, Cookie mitschicken. Ohne gesetztes Passwort ist die
  API offen (Dev-Modus).
- Frontend/Backend-Tests im Browser: Host hat keine GUI-Libs — Headless-Chrome
  aus Docker nutzen (`zenika/alpine-chrome:with-puppeteer`, --network=host,
  NODE_PATH=/usr/src/app/node_modules).
- **TLS:** echte lokale CA statt self-signed — `scripts/make_cert.sh <domain> <ip…>`
  erzeugt/erneuert ssl/{ca.crt,fullchain.pem,privkey.pem} (CA 10 Jahre, Server-Zert
  825 Tage → ~alle 2 Jahre neu ausstellen + `docker compose restart`; Handys behalten
  die CA). ca.crt wird unter https://…/ca.crt zum Import angeboten.
  Die Domain (Default `agent-dashboard.local`) muss im lokalen DNS auf den
  Docker-Host zeigen, z.B. per Pi-hole (`pihole-FTL --config dns.hosts`).

- `app/` ist ein Namespace-Package; Backend mit cwd `backend/` oder `PYTHONPATH=backend` starten.
- FastAPI-Port 5000 und MCP-Port 9000 sind **intern** — nicht in docker-compose gemappt.
- `config/` ist read-only gemountet; editierbare Settings liegen in `/workspace/config/settings.json`
  (entrypoint kopiert Vorlagen beim ersten Start dorthin).
- Frontend nutzt relative `fetch('/api/...')` — funktioniert in Dev (Vite-Proxy) und Prod (nginx).
- supervisord `user=app` wechselt nur die UID, **nicht** HOME — Prozesse, die asyncssh
  nutzen (api, mcp-tunnel), brauchen `environment=HOME="/home/app"`, sonst
  PermissionError auf /root/.ssh.
- SSH-Keys in `secrets/` müssen uid 10001 (`app`) gehören/lesbar sein.
