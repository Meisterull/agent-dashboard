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
  mcp_server.py            MCP-Server (Tools fürs Orchestrator-LLM), Streamable-HTTP :9000;
                           jedes Tool wird über `werkzeug` als async registriert und
                           läuft im Thread (#34) — das SDK führt sync-Tools sonst im
                           Event-Loop aus, und ALLE Kanäle teilen sich einen
  orchestrator.py          CLI-Variante des Orchestrators
  app/
    orchestrator_core.py   gemeinsamer Kern: MCP-Anbindung + run_turn (provider-neutral)
    llm.py                 Provider-Schicht: ollama (Standardlib-HTTP) + anthropic (SDK lazy)
    mailbox.py             atomare Mailbox v2: Envelopes (task/message/question/
                           answer/response), post/read_inbox/claim_tasks+
                           claim_task(nur task)/write_response(rettet sender als
                           `to`, legt Ergebnis als response in die Inbox des
                           Auftraggebers, räumt inbox UND .processing; error →
                           inbox/.failed/ + instruction in der Response, #15)/
                           mark_read(→ inbox/.archive)/normalize_envelope;
                           Rückfragen-Parken (#17): link_question (ask heftet
                           Frage an laufende Tasks), park_wenn_offene_fragen
                           (done mit offener Frage → needs_confirm in
                           .processing), resolve_question (answer → Nachtrag +
                           zurück in die Inbox), merged_instruction (Prompt
                           inkl. Antworten/Zwischenstand für den Folgelauf);
                           beantworte_frage = die EINE Antwort-Primitive
                           (Dashboard + MCP-`answer`: zustellen, Frage
                           archivieren, geparkte Tasks anstoßen; `answered_by`
                           vermerkt den Menschen, der für einen Agenten
                           antwortet, #22); schliesse_frage/verwerfe_frage =
                           der Ausgang OHNE Antwort (#23, s.u.);
                           requeue_stale/aufraeumen/pflege (Wartung, s.u.)
    files.py               pfad-sichere Datei-Ops (Dateibaum, Editor, Up-/Download)
    remote_files.py        SFTP-Datei-Ops auf den Agenten-PCs (/api/remote/…)
    chat_store.py          SQLite-Persistenz der Chat-Sessions (/workspace/chat.db)
    events.py              Mailbox-Wächter (F4/F10): watchfiles (mtime-Fallback) →
                           SSE-Broadcaster für GET /api/events + Push-Auslöser
                           (Schnappschuss-Diff: NEUE Mensch-Rückfragen,
                           Orchestrator-Responses und Nachrichten an den
                           Menschen (#33), Bestand nie)
    push.py                Web-Push (F10): VAPID-Keys (vapid.json, auto-erzeugt) +
                           Subscriptions (push_subscriptions.json) in DATA_CONFIG_DIR,
                           Versand via pywebpush (lazy — fehlt es, wird still
                           übersprungen); sw.js in frontend/public zeigt die Meldung
    ssh_connect.py         zentraler SSH-Connect mit Host-Key-Pinning (TOFU,
                           /workspace/config/known_hosts) — von bridge/SFTP/tunnel genutzt
    config.py              Settings (settings.json) + Verbindungen (agents.yaml)
    integrations.py        config-getriebene HTTP-Tools (integrations.yaml), generisch;
                           Timeout Default 60 s (INTEGRATION_TIMEOUT, je Integration
                           `timeout:`) — lange Vorgänge asynchron anstoßen, nicht
                           das Timeout hochdrehen (#34)
    rollen.py              Rollen für Task-Läufe (St.1): config/rollen/<name>.md
                           (Frontmatter beschreibung/permission_mode/allowed_tools
                           + Prompt), SERVERSEITIG beim send_task(rolle=…) in den
                           Envelope eingefroren (rolle/rollen_prompt/…); der
                           Watcher rechnet die SCHNITTMENGE mit seinen
                           Agenten-Rechten (wirksame_rechte — Rolle kann nur
                           einschränken, nie erweitern) und hängt den Prompt per
                           --append-system-prompt an. UI: Agenten-Panel → Rollen
    verbrauch.py           Verbrauchszähler (St.3): usage/total_cost_usd aus dem
                           result-Event (Watcher → complete_task(verbrauch)/
                           Outbox-Response), Aggregation ON-READ aus der Outbox
                           (keine eigene Persistenz — Datei-Watcher schreibt
                           remote am Server vorbei), hängt an
                           /api/agents/{name}/tasks; Schwelle
                           verbrauch_schwelle_5h (Settings) färbt Panel rot +
                           Planer pausiert GEPLANTE Tasks (▶/Chat laufen weiter)
    zeitplaene.py          Geplante Tasks (St.2): Planer-Loop im API-Prozess
                           postet fällige Pläne (config/zeitplaene.yaml, Dialog
                           im Agenten-Panel ⏰) als normale Tasks (sender=
                           orchestrator → Ergebnis+Push an den Menschen);
                           verpasst = verfallen (PLANER_KULANZ 600 s),
                           nachholen: true = höchstens EIN Nachzügler. Dazu
                           nicht_vor am Task-Envelope: claim_tasks überspringt,
                           claim_task wirft ZuFrueh, Datei-Watcher lässt liegen.
                           TZ=Europe/Berlin steht in docker-compose (UTC-Falle!)
    mcp_scope.py           Kanal-Identität + Tool-Allowlists je Agent (Issue #13):
                           Port-Vergabe (frei :9000, gebunden ab :9100), Port-Map
                           mcp_ports.json, resolve_ident; reine Stdlib, Tests in
                           backend/tests/test_mcp_scope.py
    ssh_bridge.py          WebSocket ↔ asyncssh (Terminal); Sessions überleben
                           das Fenster-Schließen (stabile sids "main", "2", … —
                           mehrere Terminals pro Verbindung möglich;
                           SSH_GRACE_SECONDS Default 24 h, 0 = ∞;
                           GET /api/ssh/sessions fürs UI-Badge/Auto-Reopen)
    mcp_tunnel.py          Reverse-SSH-Tunnel: MCP-Server auf die Agenten-PCs
                           (supervisord `mcp-tunnel`, Gate MCP_TUNNEL_ENABLED)
    auto_watcher.py        Automatikmodus (Issue #12): hält pro Agent einen
                           Remote-Watcher per SSH (Muster mcp_tunnel, aber im
                           API-Prozess); gewünschter Zustand in settings.json
                           (automatik/automatik_notaus), /api/automatik*
  requirements.txt
frontend/                  React 18 + Vite 6 + Tailwind v4 (komplettes Dashboard)
  src/App.jsx              Layout; src/api.js fetch-Helfer; src/components/*.jsx
  src/sprache.js           Oberfläche zweisprachig (de/en), ohne i18n-Paket:
                           t("Deutscher Text") — Deutsch IST der Schlüssel,
                           Englisch aus sprache/woerter_*.js (fehlender Eintrag
                           → Deutsch). Platzhalter {0},{1} via t(text, werte…).
                           Sprache = Setting `language` (global) + localStorage-
                           Spiegel `ui.sprache` (synchron beim ersten Render);
                           Umschalten in den Settings lädt die Seite neu.
                           NEUE UI-STRINGS immer in t() + Wörterbuch-Eintrag.
  src/termScroll.js        Wischen + Größenwechsel im Terminal (#35): xterm
                           verliert bei einer Wischgeste das Berührungsziel
                           (DOM-Renderer ersetzt die Zeilen) — Zeiger festhalten
                           statt nativ scrollen; fittenOhneSprung hält die
                           Stelle im Verlauf beim Tastatur-Wechsel
scripts/agent_watcher.py   Remote-Watcher, nur Standardlib. Transporte: --root
                           (Datei-Mailbox) ODER --mcp-url (über den gebundenen
                           MCP-Kanal, kein Mount); auf stdin: "stop"/EOF = sanft
                           beenden, "kill" = laufenden claude-Lauf sofort
                           abschießen (--mcp-hint: Identitäts-Kontext
                           voranstellen); Instanz-Lock je Agent (rc=2 bei
                           Doppelstart, rc=1 bei Preflight/Fehlerserie);
                           claude läuft mit stream-json (#18): Tool-Calls/Text
                           als Live-Fortschritt ins Panel, Timeout killt die
                           ganze Prozessgruppe, stdin=DEVNULL (#16); fertige
                           Ergebnisse werden mit eigener Retry-Schleife
                           abgeliefert — auch nach "stop" (30 min Arbeit dürfen
                           nicht an einem Tunnel-Reconnect verloren gehen)
scripts/setup_agent_pc.sh  auf dem Agenten-PC: Dashboard-MCP in Claude-Code
                           registrieren (http://127.0.0.1:<mcp_port>/mcp)
Dockerfile · docker-compose.yml · entrypoint.sh · supervisord.conf · nginx/
```

## Befehle

```bash
# Frontend
cd frontend && npm install && npm run dev      # http://localhost:5173 (proxyt /api,/ws)
cd frontend && npm run build                   # erzeugt dist/ (nginx liefert es aus)
cd frontend && node tests/test_layout.mjs      # Fensteranordnung, rein rechnerisch (kein Browser)
# Im echten Browser (Prüfstand ohne Backend/Login, ?panel=… wählt den Teil) —
# Aufruf steht im Kopf der Testdatei; Handy-Format + Touch-Emulation:
#   tests/test_workspace_browser.cjs  Fensteranordnung (#24)
#   tests/test_agents_browser.cjs     Agenten-Panel: Nachrichten (#33)
#   tests/test_terminal_browser.cjs   Terminal wischen/Größenwechsel (#35)
#   tests/test_keybar_browser.cjs     Tastenleiste bleibt wischbar

# Backend (braucht: pip install -r backend/requirements.txt + ANTHROPIC_API_KEY)
cd backend && python -m mcp_server             # Tools, :9000
cd backend && uvicorn main:app --host 127.0.0.1 --port 5000
cd backend && python orchestrator.py           # CLI-Chat statt Web

# Vertical Slice ohne LLM (nur Standardlib, läuft überall)
python scripts/agent_watcher.py --agent frontend --root /tmp/mb/mailboxes --dry-run --once

# Tests: alles Standardlib, läuft auf dem Host wie im Container
cd backend && python -m tests.run_alle             # alle Module (je eigener Prozess)
cd backend && python -m tests.test_mcp_tools       # einzelnes Modul

# Ganzer Stack
docker compose up --build                      # nginx+api+mcp(+tunnel) via supervisord
```

## Architektur-Kernpunkte

- **Tools laufen über MCP, nicht hartcodiert.** `orchestrator_core.run_turn` öffnet pro
  Runde eine MCP-Session (`mcp_session()`), übersetzt die Tools ins Anthropic-Format
  und führt Tool-Calls über `session.call_tool` aus. CLI und FastAPI teilen diesen Kern.
- **Mailbox ist die riskanteste Primitive** → `app/mailbox.py` schreibt atomar
  (`tmp` + `fsync` + `os.replace`) und beansprucht Tasks exklusiv über `.processing/`.
  Nie naiv `open(...).write()` für Mailbox-JSON. Dazu drei Regeln aus dem
  Review vom 16.08.2026:
  - **Read-Modify-Write nur unter `self._lock()`** (flock auf `<agent>/.lock`).
    Atomares Schreiben verhindert halbe Dateien, nicht verlorene Updates —
    API-Prozess und MCP-Server sind getrennte Prozesse und fassen dieselben
    Envelopes an. Der Lock ist pro Thread re-entrant, aber NIEMALS über zwei
    Mailboxen verschachteln (Deadlock-Gefahr).
  - **`claim_task` ist exklusiv, nicht idempotent**: ein schon beanspruchter
    Task wirft `AlreadyClaimed` (der zweite Watcher überspringt ihn); nur
    `erneut=True` liefert ihn dem eigenen Bearbeiter noch einmal aus.
    `complete_task` ist dagegen absichtlich wiederholbar (`already: true`) —
    der Watcher liefert bei Verbindungsabriss erneut ab.
  - **Wartung läuft im API-Prozess** (`mailbox.pflege`, alle
    `MAILBOX_PFLEGE_INTERVALL` s): Tasks, die länger als
    `MAILBOX_STALE_ALTER` (Default 3 h) in `.processing` liegen, gelten als
    verwaist und gehen zurück in die Inbox; `MAILBOX_ARCHIV_TAGE` rotiert
    Archiv/Fehlschläge/Outbox. Der Wert MUSS über dem `CLAUDE_TIMEOUT` des
    Watchers (1800 s) bleiben, sonst wird ein laufender Task doppelt gestartet.
    `MAILBOX_INBOX_TAGE` (Default 14, 0 = aus) nimmt zusätzlich alte
    `response`/`answer` aus der **Inbox** ins Archiv (Issue #21) — sonst wächst
    sie bei jedem, der nie `mark_read` ruft, unbegrenzt. Tasks und Fragen sind
    dabei tabu (Arbeitsvorrat, kein Protokoll); von Hand räumt der Knopf
    „✓ alles gelesen" im Agenten-Panel (`POST /api/agents/{name}/inbox/read-all`
    → `Mailbox.alle_gelesen`, lässt offene Tasks und Rückfragen liegen).
- **Multi-Provider (`app/llm.py`):** `ORCH_PROVIDER=anthropic|ollama`. Neutrale
  History (`user`/`assistant`+`tool_calls`/`tool`), erst beim Aufruf ins Provider-
  Format übersetzt. Ollama über Standardlib-HTTP (kein pip), Anthropic lazy via SDK
  (`claude-opus-4-8`, adaptives Thinking, `effort: high`, Prompt-Caching auf
  System+Tools). Agentic-Loop gegen Ollama real getestet. **Beim Erweitern
  provider-neutral bleiben** — nichts Anthropic-Spezifisches in
  orchestrator_core/main. Zwei History-Regeln:
  Assistant-Nachrichten führen auf dem Anthropic-Pfad die rohen Content-Blöcke
  in `_anthropic` mit und geben sie unverändert zurück — **Thinking-Blöcke
  tragen eine Signatur**, ein Neubau aus Text+tool_calls verliert sie und die
  API weist die Fortsetzung ab. Und jeder `tool_call` braucht ein `tool_result`:
  bricht ein Turn ab, trägt `llm.repariere_history` die fehlenden nach, statt
  die Runde (und das Wissen um bereits ausgeführte Tools) wegzuwerfen.
- **MCP-Rollen:** (1) Werkzeugkasten des Orchestrators. (2) Transport zu Agenten =
  Reverse-SSH-Tunnel (`app/mcp_tunnel.py`): pro SSH-Agent aus agents.yaml lauscht auf
  dem Agenten-PC 127.0.0.1:<mcp_port> (Default 9000) auf den Container-MCP. Claude-Code
  dort einmalig via `scripts/setup_agent_pc.sh` registrieren — dann können Agenten
  selbst inbox/ask/answer/send_message nutzen. Alle MCP-Ports bleiben
  unveröffentlicht (nur Loopback + Key-Auth-Tunnel). Einträge ohne existierende
  key_file werden übersprungen; agents.yaml wird alle 60 s neu eingelesen.
  Aufgaben-Transport an die Watcher bleibt die Datei-Mailbox.
  `list_agents` liefert Mailbox-Ordner **plus** konfigurierte Agenten — und genau
  diese Menge akzeptieren `send_task`/`send_message`/`ask` als Empfänger. Ein
  Tippfehler legt damit keine Geister-Mailbox mehr an (die sich sonst über
  `list_agents` selbst bestätigt hätte).
- **Kanal-Identität + Tool-Scoping (Issue #13, `app/mcp_scope.py`):** Pro SSH-Agent
  lauscht im Container ein EIGENER, an den Agentennamen gebundener MCP-Port (auto ab
  :9100, explizit `mcp_local_port`); der Tunnel forwardet dorthin statt auf :9000.
  Auf gebundenen Kanälen leitet der Server agent/sender aus der Bindung ab und lehnt
  fremde Werte ab; `tools:` am Agenten (agents.yaml) blendet nicht erlaubte Tools
  komplett aus. Server schreibt die aktive Port-Map nach mcp_ports.json, der Tunnel
  liest sie. **Kein Fallback auf :9000**: fehlt der gebundene Kanal, bleibt der
  Tunnel für den Agenten ausgesetzt (Logzeile) — auf dem freien Kanal wären
  agent/sender frei wählbar. Auto-Ports sind stabil (bestehende Zuordnungen
  aus mcp_ports.json werden wiederverwendet), sonst rutschte beim Einfügen
  eines Agenten die Bindung eines anderen weiter und ein Tunnel zeigte
  kurzzeitig auf eine FREMDE Identität. Der freie Kanal :9000
  (Orchestrator) verhält sich unverändert. Tool-Registrierung läuft über
  `register_tools(mcp, identity, allowed)` in mcp_server.py — beim Tool-Ergänzen dort
  registrieren UND den Namen in mcp_scope.KNOWN_TOOLS aufnehmen. Nach Änderungen an
  tools/mcp_local_port: `supervisorctl restart mcp`.
- **Automatikmodus (Issue #12, `app/auto_watcher.py` + Toggle im Agenten-Panel):**
  Pro Agent schaltbar; der Manager (API-Prozess, Start via FastAPI-startup) hält per
  SSH einen `agent_watcher.py --mcp-url http://127.0.0.1:<mcp_port>/mcp` auf dem
  Agenten-PC — Script wird bei jedem Start per SFTP nach ~/.agent-dashboard/
  gelegt (immer aktuell, kein Install-Schritt), MCP läuft über den gebundenen
  Kanal (#13). GEWÜNSCHT lebt in settings.json (`automatik`, `automatik_notaus`),
  IST = echter Prozess (stirbt er → "fehler" + Reconnect, nie falsches "an").
  Aus = sanft ("stop" auf stdin, laufender claude-Lauf darf fertig werden, Deckel
  AUTO_STOP_GRACE 1860 s, läuft im Hintergrund — der HTTP-Request wartet NICHT
  darauf); Not-Aus = hart: **"kill" auf stdin** beendet die claude-Prozessgruppe
  (POSIX killpg, Windows `taskkill /F /T`), erst danach wird die Verbindung
  geschlossen. Verbindung-schließen allein reicht NICHT — ohne PTY schickt sshd
  kein SIGHUP, der Lauf liefe verwaist weiter und änderte Dateien, während der
  Task schon als Fehler gemeldet wäre. Ein Instanz-Lock
  (`~/.agent-dashboard/<agent>.lock`) verhindert zwei Watcher für denselben
  Agenten; ein Watcher, der wegen Preflight/Fehlerserie mit rc=1 endet, wird
  NICHT automatisch neu gestartet (Status "gesperrt", erst der Toggle löst ihn). Optional je Agent in agents.yaml: `workdir`,
  `python` (Default python3), `claude_bin` (Pfad/Name des Claude-Binaries).
  Robustheit (Issue #14): Preflight VOR dem ersten Claim (workdir + Binary via
  `finde_claude` — sucht nach `which` auch ~/.local/bin & Co., weil die
  nicht-interaktive SSH-Shell den Login-PATH nicht hat); bei kaputter Umgebung
  Exit statt Warteschlange fressen, ebenso nach 3 sofortigen Fehlschlägen in
  Folge (`fehlerserie`); leeres result bei status=error wird mit der
  Fehlerursache gefüllt. Rückfragen (Issue #17): stellt der Agent während
  eines Tasks ein ask(), parkt complete_task(done) den Task (needs_confirm in
  .processing) statt Erfolg zu melden; die Antwort (Dashboard oder answer-Tool)
  stößt ihn mit Kontext neu an. Projekt + Berechtigungen (Issue #19): das
  `project` eines Tasks wählt ein Unterverzeichnis unter `workdir`
  (`projekt_workdir` — Ausbruch/fehlendes Verzeichnis → Task scheitert mit
  Klartext); `permission_mode`/`allowed_tools` je Agent in agents.yaml werden
  an `claude --permission-mode`/`--allowed-tools` durchgereicht (allowed_tools
  als EIN Komma-Argument; **die instruction geht über STDIN des claude-Kinds**
  — seit Review 02.09. (P1-3): auf Windows parst cmd.exe die Argumentzeile des
  claude.cmd-Shims erneut, ein `&` im Task-Text konnte Kommandos ausführen;
  stdin löst zugleich Issue #20 (variadisches --allowed-tools) und ARG_MAX.
  Der Watcher-stdin bleibt der Stopp-Kanal (Issue #16). Kommandozeile baut
  `baue_claude_cmd`, dafür gibt es Tests);
  verweigerte Werkzeuge landen als „Berechtigung verweigert: …"
  im log der Antwort (permission_denials aus dem result-Event + tool_result-
  Heuristik). Headless beantwortet niemand Freigabe-Fragen — was die Automatik
  dürfen soll, MUSS als Flag mitkommen.
- **Agent-↔-Agent (Mailbox v2):** Envelopes haben `kind` (task/message/question/
  answer/response) + `sender`/`to`. MCP-Tools: `send_task`/`send_message`/`ask`/
  `answer`/`inbox`, dazu der Task-Lebenszyklus für MCP-getriebene Agenten:
  `claim_task` (→ .processing, im Panel "running") und `complete_task` (Response
  in die Outbox + Task abräumen — ohne ihn bliebe jeder Task ewig pending,
  Issue #7) sowie `mark_read` (Envelope → inbox/.archive, sonst liefert `inbox()`
  denselben Stapel immer wieder). Das Ergebnis eines Tasks erreicht den
  Auftraggeber aktiv: `write_response` (MCP **und** Watcher) rettet `sender` vor
  dem Abräumen als `to` in die Outbox-Response und legt es als `kind=response`
  (`reply_to`=task_id) in dessen Inbox — normaler `inbox()`/`mark_read`-Zyklus
  statt Outbox-Polling; `read_responses(worker, for_sender?)` bleibt als Archiv
  (Issue #11). Der Watcher führt **nur** `kind=task` aus. `needs_confirm`-Rückfragen erscheinen im
  Dashboard (`/api/questions`, Banner) und werden dort beantwortet; hängengebliebene
  Tasks schließt `POST /api/tasks/{agent}/{task_id}/close` (✕ im Agenten-Panel).
  **Alles Nicht-Task aus der Inbox** (message/answer/response) liefert
  `/api/agents/{name}/tasks` als `messages` mit; das Panel zeigt es als
  Abschnitt „Nachrichten" samt Zähler am Agenten-Kopf, ✓ archiviert einzeln
  (`POST /api/agents/{name}/inbox/{id}/read`). Bis Issue #33 war das für
  Menschen unsichtbar — nur die MCP-Tools kamen an diese Envelopes.
- **Rückfragen im Banner: wer ist gemeint, und wie kommt man wieder raus.**
  `/api/questions` sammelt über ALLE Mailboxen — darunter Fragen, die zwei
  Agenten einander stellen. Am Dashboard sitzt aber ein Mensch, und der ist
  der `orchestrator` (`mailbox.ORCHESTRATOR`): nur Fragen an ihn sind seine
  Entscheidung. Jede Frage trägt darum `fuer_mensch`, `?to=<agent>` filtert
  hart, und das Banner zeigt fremde Fragen nur eingeklappt als Zähler
  (Issue #22); antwortet der Mensch doch für einen Agenten, steht
  `answered_by: "dashboard"` im Antwort-Envelope. Und seit #17 hängt an einer
  offenen Frage ein geparkter Task — ohne Ausgang aus der Frage gäbe es also
  auch keinen aus dem Task (`requeue_stale` fasst `needs_confirm` bewusst
  nicht an). `POST /api/questions/{agent}/{qid}/close` (✕ im Banner) ist er:
  Frage ins Archiv (`closed_at`/`closed_reason`), und der nur auf sie
  wartende Task **scheitert mit Klartext** statt still weiterzulaufen — über
  #15 landet er samt instruction in `inbox/.failed/` (Issue #23). Hängen noch
  weitere Fragen an ihm, bleibt er geparkt.
- **Generisch, nicht workflow-spezifisch:** Integrationen kommen aus `integrations.yaml`
  (`call_integration`-Tool); jedes Zielsystem ist nur eine Konfig-Zeile. Beim Erweitern
  nichts Workflow-Spezifisches im Code hartverdrahten.

## Konventionen / Sicherheit

- **Path-Traversal:** jeder Workspace-Zugriff über `app/files._safe` bzw. `_safe` im
  MCP-Server (resolve + Prüfung gegen WORKSPACE). Nie roh joinen — das gilt auch
  für Pfadsegmente aus der URL (`main._agent_base`/`_geprüfte_id`).
- **Secrets:** API-Keys/Tokens nur in `.env`/Docker-Secrets, nie ins Frontend, nie in
  `agents.yaml` im Klartext. `/api/connections` gibt nur Name/Host/User zurück.
  `SESSION_SECRET` setzen — sonst wird das Cookie-Secret aus dem Admin-Passwort
  abgeleitet (per PBKDF2 gebremst, aber vermeidbar). `files.GESPERRT` hält
  `keys/`, `ssl/` und `chat.db` aus dem Datei-Panel heraus.
- **`/ext/`-Proxy ist eine Allowlist:** nginx lässt jede private IPv4 durch,
  die zweite Hälfte der Prüfung macht `/api/auth/verify` gegen
  `settings.external_windows` (`config.ist_erlaubtes_ext_ziel`). Ohne sie liefe
  eine beliebige LAN-Seite unter der Dashboard-Origin und könnte mit dem
  Session-Cookie die ganze API bedienen. **Geprüft wird der Header `X-Ext-Ziel`
  (nginx-Captures = echtes proxy_pass-Ziel), NIEMALS die URI:** nginx
  normalisiert `..`-Segmente vor dem Location-Matching, `$request_uri` kann
  also ein erlaubtes Ziel vortäuschen, während wirklich ein anderes
  angesprochen wird (nachgestellt und behoben 16.08.2026).
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

| Backend-Tests (`python -m tests.run_alle`) | ✅ 11 Module grün 16.08.2026 (Standardlib, Host wie Container) |
| Review 16.08.2026 (H/M/N-Befunde) | ✅ Code gefixt + Tests + deployt 16.08.2026 (mit Issues #12–#19) |

Wenn du etwas änderst: bei reinen Standardlib-Modulen (mailbox, files, config, watcher)
gibt es echte Tests — `cd backend && python -m tests.run_alle` läuft ohne pip und ohne
Container. Beim LLM-Pfad ehrlich kennzeichnen, was verifiziert ist und was nicht:
der Anthropic-Pfad ist weiterhin nur unit-getestet (Prompt-Caching, Thinking-Blöcke,
`stop_reason` — nie gegen die echte API gelaufen, hier läuft Ollama).

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

- **Datei-Editor & Kodierung:** `files.decode_text` liest utf-8 (auch BOM), utf-16 (BOM)
  und cp1252 tolerant — nur echte Binärdaten (NUL-Bytes) werden abgelehnt. Das
  `encoding` aus dem Lese-Ergebnis geht beim Speichern mit zurück, die Datei bleibt
  also in ihrer Kodierung (Windows-Agenten-PCs!). Gilt für Workspace UND SFTP.
- **Fensteranordnung** (`frontend/src/workspaceLayout.js`): `standardLayout(ids)` rechnet
  die Standardanordnung über ALLE vorhandenen Panels — nie wieder feste Plätze für
  eine bekannte Handvoll Ids, sonst hat das nächste dynamische Fenster keinen Platz
  und „Fenster anordnen" liefert eine Überlappung (Issue #24). Dass das Ergebnis
  überschneidungsfrei ist, prüft `frontend/tests/test_layout.mjs` für 0–8 externe
  Fenster. Eigene Anordnungen speichert der Dialog „Ansichten"
  (`WorkspaceViews.jsx`, localStorage `workspace-views-v1`).
  **Ein iframe stellt im Elterndokument keine Pointer-Events zu** — `raise()` am
  `onPointerDownCapture` der `<section>` greift bei externen Fenstern deshalb nur
  auf der Titelleiste; das Nach-vorn-Holen beim Klick INS Fenster hängt an
  `window.blur` + `document.activeElement` (Workspace.jsx).
- **Externe Fenster** (`settings.external_windows`, Settings-Dialog): `IP:Port[/pfad]`
  wird im Frontend zu `/ext/<ip>/<port>/…` — nginx proxyt das per `auth_request`
  (Session-Cookie) NUR auf private IPv4-Ziele, WebSocket-fähig (noVNC/websockify,
  Mixed-Content-Problem gelöst). Volle `https://`-URLs landen direkt im iframe.
  Location-Regex mit `{}`-Quantifiern muss in nginx gequotet sein.
- `app/` ist ein Namespace-Package; Backend mit cwd `backend/` oder `PYTHONPATH=backend` starten.
- FastAPI-Port 5000 und MCP-Port 9000 sind **intern** — nicht in docker-compose gemappt.
- `config/` ist read-only gemountet; editierbare Settings liegen in `/workspace/config/settings.json`
  (entrypoint kopiert Vorlagen beim ersten Start dorthin).
- Frontend nutzt relative `fetch('/api/...')` — funktioniert in Dev (Vite-Proxy) und Prod (nginx).
- supervisord `user=app` wechselt nur die UID, **nicht** HOME — Prozesse, die asyncssh
  nutzen (api, mcp-tunnel), brauchen `environment=HOME="/home/app"`, sonst
  PermissionError auf /root/.ssh.
- SSH-Keys in `secrets/` müssen uid 10001 (`app`) gehören/lesbar sein.
