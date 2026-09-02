# Technische Referenz

> Vollständige Architektur- und API-Doku (deutsch). Einstieg und Überblick: [README](../README.de.md) · English: [README](../README.md) · Setup Schritt für Schritt: [START.md](../START.md)

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
(nginx · uvicorn · mcp_server · optional mcp-tunnel).

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
| `app/mailbox.py` | Atomare Mailbox v2: Envelopes (task/message/question/answer/response), `post`, `read_inbox` (FIFO nach `created_at`), `claim_tasks`/`claim_task` (nur Tasks, **exklusiv**: ein laufender Task wirft `AlreadyClaimed`), `write_response` (rettet `sender` als `to`, legt das Ergebnis als `response` in die Inbox des Auftraggebers, räumt inbox **und** .processing), `mark_read` (Archiv), `beantworte_frage` (die eine Antwort-Primitive für Dashboard und MCP), `schliesse_frage`/`verwerfe_frage` (Rückfrage ohne Antwort beenden — der nur auf sie wartende Task scheitert mit Klartext), `requeue_stale`/`aufraeumen`/`pflege` (verwaiste Tasks zurück in die Warteschlange, alte Ablagen rotieren), `normalize_envelope`. Read-Modify-Write läuft unter einem Datei-Lock je Mailbox |
| `app/files.py` | Pfad-sichere Datei-Ops (`list_dir`, `read_file`) für den Dateibaum |
| `app/config.py` | Settings (`settings.json`) + Verbindungen (`agents.yaml`, ohne Credentials) |
| `app/integrations.py` | Config-getriebene HTTP-Integrationen (`integrations.yaml`, generisch) |
| `app/mcp_scope.py` | Kanal-Identität + Tool-Allowlists je Agent (Port-Vergabe, Port-Map, `resolve_ident`) |
| `app/auto_watcher.py` | Automatikmodus: hält pro Agent einen Remote-Watcher per SSH (`/api/automatik*`) |
| `app/ssh_bridge.py` | WebSocket ↔ asyncssh für das Browser-Terminal |

**MCP-Tools** (im `mcp_server.py`, pfad-gehärtet gegen `WORKSPACE_DIR`):
- Delegation: `list_agents()` · `send_task(to, instruction, sender?, project?, rolle?)` (`create_task` als Alias) · `list_rollen()` · `read_responses(worker, for_sender?)` (Outbox-Archiv des Bearbeiters)
- Task-Lebenszyklus (Agent-Seite): `claim_task(task_id, agent?, erneut?)` (→ "in Arbeit"; ein bereits laufender Task wird **nicht** erneut vergeben — `erneut=True` holt dem eigenen Bearbeiter seinen Auftragstext zurück) · `complete_task(task_id, result, status?, log?, agent?)` — das Gegenstück zu `send_task`: legt das Ergebnis als `kind="response"` in die Inbox des Auftraggebers (`sender` des Tasks), archiviert es in der Outbox und räumt den Task ab; ein wiederholter Aufruf ist kein Fehler (`already: true`), damit eine verlorene Antwort erneut abgeliefert werden kann
- Agent-↔-Agent: `send_message(to, text, sender?)` · `ask(to, question, sender?, reply_to?)` · `answer(to, text, sender?, reply_to)` (archiviert die beantwortete Frage gleich mit) · `inbox(agent, kind?)` · `mark_read(envelope_id, agent?)` (Gelesenes archivieren, sonst kommt es bei jedem `inbox()` wieder). Empfänger müssen bekannt sein (Mailbox oder `agents.yaml`) — sonst Fehler statt Geister-Mailbox
- Projektdateien: `write_project_file(...)` · `read_project_file(...)`
- Integrationen (config-getrieben): `list_integrations()` · `call_integration(name, method, path, body?)` — Aufruf-Timeout `INTEGRATION_TIMEOUT` (Default 60 s, je Integration per `timeout:`); lange Vorgänge asynchron anstoßen (Job-ID zurück, Status pollen) statt das Timeout hochzudrehen

**Rollen für Task-Läufe** (Dashboard-Paket St.1): Eine Rolle ist eine
Markdown-Datei `workspace/config/rollen/<name>.md` — YAML-Frontmatter
(`beschreibung`, optional `permission_mode`/`allowed_tools`), darunter der
Rollen-Prompt. `send_task(rolle="review")` löst sie SERVERSEITIG auf und
friert Prompt + Rechte im Task-Envelope ein (`rolle`, `rollen_prompt`,
`rollen_permission_mode`, `rollen_tools`) — beide Watcher-Transporte lesen
dieselben Felder, eine später editierte Rollen-Datei ändert keinen schon
eingereihten Task. Der Watcher hängt den Prompt per `--append-system-prompt`
an und rechnet die Rechte als **Schnittmenge** mit seinen Agenten-Rechten
(`wirksame_rechte`): der `permission_mode` kann nur SINKEN (plan < default <
acceptEdits < bypassPermissions; ohne Agent-Vorgabe ist claudes „default" die
Messlatte), `allowed_tools` ist die exakte String-Schnittmenge — auf einem
Agenten ohne eigene Liste schaltet eine Rolle also NICHTS frei. Eine
Nur-Lese-Review-Rolle setzt `permission_mode: default` und
`allowed_tools: []` (die Auto-Werkzeuge Read/Grep/Glob brauchen keine
Freigabe). Gepflegt werden Rollen im Agenten-Panel („Rollen"-Dialog) oder als
Datei; API: `GET/PUT/DELETE /api/rollen[/{name}]`. Unbekannte Rollen lehnt
`send_task` mit der Liste der verfügbaren ab (kein Tippfehler-Task ohne die
gemeinte Rolle). Das Rollen-Badge am Task zeigt nur den Namen.

**Zeitpläne + geplante Tasks** (Dashboard-Paket St.2): Zwei Bausteine. (1)
`send_task(nicht_vor="2026-09-02T22:00")` plant einen EINZELNEN Task: er
liegt in der Inbox (die Mailbox ist der Wartepuffer), und kein claim liefert
ihn vor der Zeit aus — `claim_tasks` überspringt ihn, `claim_task` antwortet
mit `zu_frueh`, der Datei-Watcher lässt ihn liegen; das Panel zeigt ein
⏰-Badge. (2) WIEDERKEHRENDE Pläne stehen in
`workspace/config/zeitplaene.yaml` (Dialog: Agenten-Panel → ⏰): je Plan
Agent, Instruction, optionale Rolle/Projekt, `zeit` (HH:MM, lokale
Serverzeit — `TZ` steht in docker-compose!), `tage` (mo…so, leer = täglich),
`an`, `nachholen`. Der Planer-Loop läuft im API-Prozess (Muster
Mailbox-Pflege) und postet Fälliges als GANZ NORMALEN Task mit Absender
`orchestrator` — Automatik, Rückfragen und Push greifen von selbst, und der
Not-Aus wirkt wie überall (der Task bleibt dann eben liegen). VERPASSTE
Termine verfallen (Kulanz `PLANER_KULANZ`, Default 600 s); `nachholen: true`
holt höchstens EINEN nach (den jüngsten verpassten Soll-Termin), nie eine
Salve. `letzter_lauf` stempelt der Planer — auch bei Fehlversuchen
(unbekannter Agent, kaputte Rolle): ein Termin = höchstens ein Versuch.
`POST /api/zeitplaene/{name}/jetzt` (▶ im Dialog) führt einen Plan sofort
aus.

**Verbrauchszähler** (Dashboard-Paket St.3): Der Watcher liest `usage` und
`total_cost_usd` aus dem result-Event jedes Claude-Laufs und liefert sie mit
dem Ergebnis ab (`verbrauch` an `complete_task` bzw. direkt in der
Outbox-Response des Datei-Transports). Aggregiert wird ON-READ aus der
Outbox (`app/verbrauch.py`) — bewusst keine eigene Persistenz, denn der
Datei-Transport-Watcher schreibt remote am Server vorbei; die Outbox-Rotation
(30 Tage) deckt die 7-Tage-Anzeige locker. `/api/agents/{name}/tasks` liefert
das Aggregat gleich mit (dieselbe Outbox-Lesung, kein Doppel-I/O); das Panel
zeigt ⚡ heute + rollierendes 5-h-Fenster, Antippen die letzten 7 Tage.
Optionale Schwelle `verbrauch_schwelle_5h` (Settings, Tokens/5 h je Agent,
0 = aus): darüber färbt sich der Zähler rot und der Planer pausiert
GEPLANTE Tasks dieses Agenten (der ▶-Sofort-Knopf und Chat-Delegation laufen
weiter — die Schwelle ist eine Automatik-Bremse, kein Verbot). EHRLICHE
Messung, kein offizielles Limit-%: die Abo-Limits von Claude Code sind
headless nicht abfragbar (Stand 09/2026).

**Nebenläufigkeit:** Jedes Tool wird als `async` registriert und läuft in einem
Thread (Integrationen in einem eigenen, kleinen Pool). Das SDK würde synchrone
Tools sonst direkt im Event-Loop ausführen — und da sich **alle** Kanäle einen
Loop teilen, legte ein einziger langer Aufruf sämtliche Agenten still. Im Log
steht zu jedem Tool-Aufruf eine Start- und eine Endzeile mit Dauer; eine
Startzeile ohne Endzeile ist der Aufruf, der gerade noch läuft.

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

**Agenten ohne SSH** (Issue #32): Ein Gerät, zu dem das Dashboard keinen Tunnel
aufbauen kann — Windows-Notebook mit Claude Desktop, Rechner hinter NAT, mal im
LAN und mal im VPN — meldet sich stattdessen selbst über denselben HTTPS-Zugang,
den auch der Browser nimmt:

```yaml
  - name: PMNB029
    connection:
      type: token
      token_file: /app/config/tokens/PMNB029.token   # nie im Klartext in der YAML
```

Token erzeugen mit `scripts/make_agent_token.sh PMNB029`, dann auf dem Gerät
`claude mcp add --scope user --transport http dashboard https://<dashboard>/mcp/PMNB029
--header "Authorization: Bearer …"`. Das Backend prüft den Token in konstanter
Zeit und reicht die Anfrage an den gebundenen Loopback-Port weiter; die
MCP-Ports selbst bleiben unveröffentlicht wie bisher. **Die Identität kommt
weiter aus dem Kanal**: Ein Token öffnet genau den Port seines Agenten, wer
Token X hat, kann nicht als Y auftreten. Zwei Unterschiede zu SSH-Kanälen, beide
absichtlich: Ohne eigene `tools:`-Liste gibt es hier nur die Mailbox-Grundmenge
(ein Token liegt auf einem Gerät, das das Dashboard nicht kennt, und ist
leichter zu verlieren als ein Schlüssel auf einem bekannten Host), und nach
zehn Fehlversuchen in einer Minute ist der Agent kurzzeitig gesperrt.

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
| `components/AgentsPanel.jsx` | MCP-Monitor: Inbox/Outbox je Agent mit Status-Badges, dazu der Abschnitt **Nachrichten** (alles Nicht-Task aus der Inbox: Hinweise, Antworten, Task-Ergebnisse) mit Zähler am Agenten-Kopf und ✓ zum Archivieren |
| `termScroll.js` | Wischen im Terminal-Verlauf und Größenwechsel: xterm verliert bei einer Wischgeste das Berührungsziel (der DOM-Renderer ersetzt die Zeilen darunter) — die Geste wird per Pointer-Capture selbst geführt; dazu bleibt die Stelle im Verlauf erhalten, wenn die Bildschirmtastatur auf- oder zugeht |
| `components/TerminalPanel.jsx` / `Terminal.jsx` | SSH-Tabs + xterm.js über `/ws/ssh`; pro Verbindung mehrere Terminals (⧉-Knopf, z. B. Claude Code + eigene Shell nebeneinander); Fenster schließen detacht nur — die Session läuft serverseitig weiter (`SSH_GRACE_SECONDS`, Default 24 h, `0` = unbegrenzt) und lässt sich auch von einem anderen PC wieder öffnen; beendet wird per ⏻-Knopf |
| `components/Settings.jsx` | Einstellungen-Modal (Modellwahl/Sprache/externe Fenster) |
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
| `GET` | `/api/agents/{name}/tasks` | `{inbox, outbox, messages}` eines Agenten (beanspruchte Tasks als `running`; `messages` = alles Nicht-Task aus der Inbox, neueste zuerst) |
| `GET` | `/api/agents/{name}/inbox?kind=` | Alle Eingänge (Tasks + Nachrichten + Rückfragen), normalisiert |
| `POST` | `/api/agents/{name}/inbox/read-all` | Alles Erledigte ins Archiv (offene Tasks/Rückfragen bleiben) |
| `POST` | `/api/agents/{name}/inbox/{id}/read` | Eine gelesene Nachricht ins Archiv |
| `POST` | `/api/tasks/{agent}/{task_id}/close` | Hängengebliebenen Task von Hand abschließen (`{status?, result?}`) |
| `GET` | `/api/questions?to=` | Offene Rückfragen (`needs_confirm`) über alle Agenten; je Frage `fuer_mensch` (an den `orchestrator` gerichtet), `?to=` filtert auf eine Mailbox |
| `POST` | `/api/questions/{agent}/{qid}/answer` | Rückfrage beantworten → Antwort an Fragesteller (`answered_by: "dashboard"`) |
| `POST` | `/api/questions/{agent}/{qid}/close` | Rückfrage ohne Antwort schließen (`{grund?}`) → Frage ins Archiv, der wartende Task scheitert mit Klartext nach `.failed/` |
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
│   ├── rollen/             Rollen für Task-Läufe (St.1, *.md mit Frontmatter)
│   └── zeitplaene.yaml     geplante Tasks (St.2, Dialog im Agenten-Panel)
├── logs/
└── ssl/                    self-signed Platzhalter, bis echtes Zertifikat da ist
```

**`.env`** (aus `.env.example` kopieren) — Secrets, nie committen:
`ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`, `ADMIN_INITIAL_PASSWORD`,
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
mcp-tunnel) und startet abgestürzte Dienste neu; bleibt ein Dienst endgültig
unten (FATAL), beendet sich der Container, damit Docker ihn neu startet.

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

## Status

Verifikations-Stand und Testkommandos pflegt **`CLAUDE.md`** (eine Quelle
statt zweier driftender Tabellen — die frühere Kopie hier behauptete noch
„MCP-Server nie gestartet", während er längst produktiv lief; Review
02.09.2026). Kurzfassung: alles Kernige läuft produktiv und ist per
`cd backend && python -m tests.run_alle` plus Browser-Prüfstand abgedeckt.

Die frühere Roadmap an dieser Stelle stammte aus der MVP-Phase und
widersprach dem eigenen Dokument (SQLite-Chat-Persistenz und der
`needs_confirm`-Rückfragen-Flow sind seit Juli 2026 gebaut und oben
beschrieben). Offene Vorhaben stehen in `PROJECT.md` bzw. der Ideenliste
des Betreibers.

Details und Designentscheidungen: siehe **`PROJECT.md`**.
