# TODO — Review 16.08.2026

Vollreview mit 4 parallelen Agenten (Backend-Kern, SSH/Remote, Frontend, Infra/Security),
Duplikate zusammengeführt. H1+H2 wurden zusätzlich von Hand am Code verifiziert.

## Stand 16.08.2026, abends: ALLE Befunde umgesetzt — noch NICHT deployt

Alle 6 HOCH-, 29 MITTEL- und 28 NIEDRIG-Befunde sind im Arbeitsbaum gefixt, dazu
die Test-Lücken T1–T10. Kein Befund erwies sich als widerlegt. Verifikation:
`cd backend && python -m tests.run_alle` → **11 Module grün** (Standardlib, läuft
auf dem Host wie im Container), `npm run build` grün, alle Quellen kompilieren.

**Beim Nachprüfen gefunden und behoben (16.08., nach dem ersten Fix):** Die erste
Fassung von M16 prüfte `X-Original-URI` — das ist umgehbar. nginx normalisiert
`..`-Segmente VOR dem Location-Matching, also proxyt
`/ext/<erlaubt>/6080/a/../../../../ext/<fremd>/80/x` zu `<fremd>`, während die
rohe URI noch mit `<erlaubt>` beginnt; umgekehrt hätte ein doppelter Slash ein
legitimes Fenster fälschlich blockiert. Jetzt liefert nginx das echte Ziel als
`X-Ext-Ziel` (Captures des Requests), das Backend zerlegt keine URI mehr.
Verifiziert mit der ECHTEN Konfiguration in einem isolierten Wegwerf-Container
(nginx 1.22.1 aus dem Projekt-Image): erlaubt 200 · fremd 403 · Bypass 403 ·
doppelte Slashes 200 · Query-String 200.

**Bewusst abgewichen:** HSTS bleibt AUS (N19) — es gilt pro Hostname, nicht pro
Port, und würde beim Zugriff über die Host-IP auch die anderen HTTP-Dienste
auf demselben Rechner auf https zwingen; Rücknahme nur über max-age=0.

**Noch offen (Betrieb, nicht Code):**
- **Deploy** — enthält auch die Automatik-Issues #12–#19 (seit 10.08. ungedeployt):
  `docker compose up -d --build`. Der laufende Container hat nichts davon.
- `chmod 750 workspace/` (N19) — braucht eine Entscheidung, siehe dort.
- Public-Sync + Issues #12–#19 schließen.
- Die manuellen Nach-dem-Deploy-Prüfungen unten.

Status-Legende: `[x]` erledigt · `[ ]` offen · `[~]` bewusst anders entschieden

---

## HOCH — Korrektheit, zuerst fixen

- [x] **H1: `project` geht über den MCP-Transport verloren — Task läuft im falschen Verzeichnis** (2× unabhängig gefunden, von Hand verifiziert)
  `backend/mcp_server.py:195` + `backend/app/mailbox.py:117-126` + `scripts/agent_watcher.py:540`
  `claim_task` gibt nur `{claimed, instruction, status}` zurück, `normalize_envelope` (Basis von `inbox()`) kennt `project` nicht → `claimed.get("project") or env.get("project")` ist über MCP (= Automatik-Standardpfad!) immer `None`. Issue #19 wirkt nur auf dem Datei-Transport; über MCP arbeitet Claude still im Basis-`workdir` statt im Projekt.
  **Fix:** `project` (und `files`) in die `claim_task`-Antwort aufnehmen UND in `normalize_envelope` durchreichen. Regressionstest: MCP-Roundtrip mit gesetztem `project` gegen `build_server()`.

- [x] **H2: Doppel-Ausführung desselben Tasks — MCP-`claim_task` ist idempotent, Watcher merkt es nicht** (2× unabhängig gefunden, von Hand verifiziert)
  `backend/app/mailbox.py:257-261` + `scripts/agent_watcher.py:537`
  Liegt der Task schon in `.processing`, liefert `claim_task` ihn erneut als normales dict; der Watcher-Kommentar „schon von jemand anderem beansprucht" greift nie, weil nur `error` geprüft wird. Zwei Watcher (Netz-Flap ohne ClientAliveInterval → Container startet neuen, alter lebt noch; oder manuell gestarteter neben der Automatik) führen denselben Task doppelt aus. Datei-Transport ist via `os.replace` korrekt.
  **Fix:** `claimed_by`/`claim_token`-Stempel im Envelope; Claim aus `.processing` nur bei passendem Owner als Erfolg, sonst `{"error": "already claimed"}`. Zusätzlich Instanz-Lock auf dem Agenten-PC (`~/.agent-dashboard/<agent>.lock`).

- [x] **H3: Not-Aus beendet den laufenden claude-Lauf nicht — er läuft verwaist weiter**
  `backend/app/auto_watcher.py:297-309` + `scripts/agent_watcher.py:279-341, 392-407`
  `_stopp_hart` schließt nur die SSH-Verbindung; ohne PTY gibt es kein SIGHUP, stdin-EOF ist als **Sanft**-Stopp definiert. Das nächste `fortschritt`-print auf die tote Pipe wirft `BrokenPipeError` mitten in der stdout-Schleife → Task wird über den (separaten) Tunnel als `error` abgeschlossen, während claude weiterläuft und Dateien ändert — `killpg` wird auf diesem Pfad nie gerufen.
  **Fix:** stdin-Kommando `kill` (sofort STOP + `killpg` auf die aktuelle claude-Prozessgruppe); `_stopp_hart` schickt erst `kill\n`, dann close. `run_claude`-Leseschleife mit try/finally absichern (Abbruch → killpg), `fortschritt` gegen `BrokenPipeError` kapseln.

- [x] **H4: Anthropic-Pfad: Thinking-Blöcke werden aus der History verworfen → 400er bei Tool-Use-Fortsetzung möglich**
  `backend/app/llm.py:183-198` (Call mit `thinking: adaptive`) + `llm.py:148-171` (Replay ohne Thinking)
  Thinking-Blöcke (inkl. Signatur) müssen beim Fortsetzen unverändert zurückgereicht werden; der Agentic-Loop ist bisher nur gegen Ollama real getestet — auf dem Anthropic-Pfad latenter Blocker in der Kern-Schleife.
  **Fix:** Provider-rohe Content-Blöcke in der neutralen History mitführen (z. B. `_raw_anthropic`) und in `_anthropic_blocks` verbatim einsetzen; `stop_reason` (`max_tokens`/`refusal`) auswerten.

- [x] **H5: Terminal dupliziert den gesamten Verlauf nach jedem Reconnect**
  `frontend/src/components/Terminal.jsx:205-209` + `backend/app/ssh_bridge.py:207-210`
  Server spielt beim Reattach den kompletten Puffer nach, das Frontend schreibt in die ungeleerte xterm-Instanz → nach jedem Netz-Blip steht der Verlauf doppelt/dreifach da.
  **Fix:** in `ws.onopen` vor dem Replay `term.reset()`.

- [x] **H6: Terminal-Session-Klau-Ping-Pong zwischen zwei Tabs (Close-Code 4000)**
  `frontend/src/components/Terminal.jsx:223-226` vs. `:249-259`
  Bei 4000 („in anderem Fenster übernommen") wird bewusst nicht reconnectet, aber der `visibilitychange`-Handler verbindet trotzdem neu, sobald der alte Tab sichtbar wird → die Tabs klauen sich die Session abwechselnd.
  **Fix:** `takenOver`-Flag setzen und in `onVisible` prüfen; Reattach nur noch explizit (Button „Wieder verbinden").

---

## MITTEL — Backend / Mailbox / Orchestrator

- [x] **M1: Lost-Update-Races bei Read-Modify-Write auf `.processing`-Envelopes über Prozessgrenzen**
  `backend/app/mailbox.py:265-284, 298-321, 323-359` — FastAPI, MCP-Server (Threadpool!) und Watcher schreiben dieselben Dateien ohne Lock. Konkretes Szenario: `park_wenn_offene_fragen` überschreibt den Nachtrag von `resolve_question` → Task hängt geparkt, obwohl die Antwort da ist, nichts stößt ihn mehr an.
  **Fix:** Cross-Process-Lock pro Agent (`fcntl.flock` auf `<base>/.lock`) um `link_question`/`resolve_question`/`park_wenn_offene_fragen`/`write_response`.

- [x] **M2: Ein Tool-Fehler wirft den ganzen Chat-Turn weg — Seiteneffekte bleiben, Gedächtnis nicht**
  `backend/app/llm.py:242` + `backend/main.py:338-343` — Exception in Runde n → 502, History der Runden 1..n-1 (inkl. bereits ausgeführtem `send_task`!) wird nicht gespeichert; Orchestrator verschickt Tasks ggf. doppelt.
  **Fix:** `call_tool` in try/except, Fehlertext als `tool_result` in die History; History nach jedem vollständigen Runden-Paar speichern.

- [x] **M3: Antwort-Drift Dashboard vs. MCP-`answer`: Frage bleibt offen bzw. ewig in der Inbox**
  `backend/main.py:273-291` vs. `backend/mcp_server.py:287-308` — MCP-`answer` fasst den Frage-Envelope nicht an (Banner zeigt weiter offen); Dashboard setzt `done`, räumt aber nie ab (Inbox + LLM-Kontext wachsen).
  **Fix:** eine `Mailbox.answer(...)`-Primitive (posten + archivieren + `resolve_question`, unter dem M1-Lock), von beiden Pfaden genutzt.

- [x] **M4: Unbegrenztes Wachstum: Chat-History/Tool-Ergebnisse ohne Kappung, Outbox ohne Rotation**
  `backend/app/llm.py:244-246`, `backend/app/orchestrator_core.py:79-85`, `backend/app/mailbox.py:366`, `backend/main.py:238`
  **Fix:** Tool-Ergebnisse vor History-Append kappen (~30k Zeichen + Marker); `read_responses` mit `limit`; Housekeeping für Outbox/`.archive`/`.failed` (N Tage).

- [x] **M5: `send_task`/`send_message`/`ask` an unbekannte Agenten erzeugen still eine Geister-Mailbox**
  `backend/mcp_server.py:130` — Tippfehler des LLM legt neue Mailbox an, Task liegt ewig pending, Geister-Agent erscheint in `list_agents`.
  **Fix:** Existenzprüfung gegen vorhandene Mailboxen/agents.yaml, sonst `{"error": "unbekannter Agent, verfügbare: …"}`.

- [x] **M6: Rohe Pfad-Joins mit URL-Parametern in main.py (eigene `_safe`-Konvention verletzt)**
  `backend/main.py:227, 244, 276, 287, 311-312` — `agent`/`qid` aus der URL landen ungefiltert im Pfad, `answer_question` schreibt sogar dorthin.
  **Fix:** `AGENT_NAME_RE.fullmatch` + ID-Muster `^[A-Za-z0-9_-]+$` erzwingen, sonst 400.

## MITTEL — SSH / Automatik / Watcher

- [x] **M7: Automatik-Ausschalten blockiert den HTTP-Request bis zu 31 Minuten**
  `backend/app/auto_watcher.py:143-151, 278-295` — `schalte(name, False)` awaited den Sanft-Stopp inkl. `STOP_GRACE` 1860 s; nginx/Browser timeouten, UI zeigt Fehler. `_reconcile` macht es richtig (`create_task`).
  **Fix:** Sanft-Stopp als Hintergrund-Task, sofort Status „stoppt" antworten.

- [x] **M8: Ergebnisverlust: fehlgeschlagenes `complete_task` nach Soft-Stopp wird nicht wiederholt**
  `scripts/agent_watcher.py:516, 564-567, 582-593` — Tunnel im Reconnect + STOP gesetzt → bis zu 30 min Claude-Arbeit verworfen, Task hängt als „running".
  **Fix:** Ablieferung vom Poll-Loop entkoppeln: eigene Retry-Schleife (5×10 s) auch bei gesetztem STOP.

- [x] **M9: Kein Recovery verwaister `.processing`-Tasks nach Watcher-Tod**
  `scripts/agent_watcher.py:523, 605` + `backend/app/mailbox.py:225, 247` — Absturz/Stromausfall mitten im Lauf → Task steht ewig auf „running", nur manuelles ✕ hilft.
  **Fix:** beim Watcher-Start (bzw. serverseitig) `.processing`-Einträge ohne `needs_confirm` und älter als CLAUDE_TIMEOUT zurück in die Inbox (`os.replace`). Siehe auch Feature F5 (Stale-Task-Reaper).

- [x] **M10: Cross-Identity-Fenster durch instabile Auto-Port-Vergabe**
  `backend/app/mcp_scope.py:102-109` + `backend/app/mcp_tunnel.py:40, 149-160` — Ports werden in agents.yaml-Reihenfolge ab 9100 vergeben; Eintrag oben einfügen + mcp-Neustart → bestehende Tunnel forwarden bis 60 s auf den Port eines **anderen** Agenten (fremde Identität!).
  **Fix:** `compute_scopes` liest vorhandene `mcp_ports.json` und behält name→port bei; neue Agenten bekommen nur freie Ports.

- [x] **M11: Fallback auf den freien Kanal :9000 streichen (funktional kaputt + Identitäts-Loch)**
  `backend/app/mcp_tunnel.py:139-146` + `backend/mcp_server.py:83-87` — (a) Automatik-Watcher sendet nie `agent`-Parameter → auf :9000 endlose „agent fehlt"-Fehler-Runden, Panel zeigt „an", nichts passiert; (b) Agent ohne Allowlist bekommt den vollen Orchestrator-Kanal mit frei wählbarer Identität.
  **Fix:** Fallback komplett aussetzen (wie bei Allowlist-Agenten) → Fehlerzustand wird sichtbar „Tunnel ausgesetzt" statt kaputter Watcher. Zusätzlich Beispiel-Allowlist in agents.yaml als Default-Muster dokumentieren.

- [x] **M12: Fehlerserien-Schutz (Issue #14) wird vom bedingungslosen Auto-Restart ausgehebelt**
  `scripts/agent_watcher.py:575-579` + `backend/app/auto_watcher.py:248-269` — Watcher-Exit 1 nach Fehlerserie → Manager startet nach 30 s neu → frisst weiter ~3 Tasks pro Zyklus.
  **Fix:** `proc.exit_status` auswerten; bei rc=1 Status „fehler" und kein Auto-Restart (oder exponentieller Backoff mit Deckel) bis zum Neu-Schalten.

- [x] **M13: Windows: Timeout killt nur claude, nicht dessen Kinder — Watcher hängt danach für immer**
  `scripts/agent_watcher.py:259-267` — kein killpg-Ersatz; hängender Tool-Subprozess hält stdout offen, `for zeile in proc.stdout` blockiert unbegrenzt.
  **Fix:** `taskkill /F /T /PID` statt `proc.kill()`.

- [x] **M14: Dateitransport: „stop" wirkt erst nach dem kompletten Inbox-Stapel**
  `scripts/agent_watcher.py:605-696` — keine STOP-Prüfung in der Task-Schleife (MCP-Pfad hat sie: Z. 527-528); 5 Tasks à 30 min = bis 2,5 h Weiterarbeit nach „stop".
  **Fix:** `if STOP.is_set(): break` am Schleifenanfang.

- [x] **M15: remote_files: neue SSH+SFTP-Verbindung pro Operation; Watcher-Script-Upload bei jedem Reconnect**
  `backend/app/remote_files.py:32-50` + `backend/app/auto_watcher.py:217-220` — jeder Datei-Browser-Klick = voller Handshake; spürbar träge.
  **Fix:** Verbindungs-Cache pro Agent (Idle-Timeout ~60 s), Script-Upload nur bei Hash-Abweichung. Siehe Feature F7 (SSH-Pool).

## MITTEL — Infra / Security

- [x] **M16: `/ext/`-Proxy macht fremde LAN-Geräte same-origin mit dem Dashboard — authentisiertes Relay aufs ganze private Netz**
  `nginx/agent-dashboard.conf.template:106-119` + `backend/main.py:123-130` — `auth_verify` ignoriert `X-Original-URI`; jede private IPv4 wird proxied und läuft unter Dashboard-Origin → JS eines kompromittierten LAN-Geräts kann mit dem Session-Cookie die volle API nutzen (inkl. `/api/files/download` → SSH-Keys der Agenten-PCs). `proxy_hide_header X-Frame-Options/CSP` hebelt zudem die Schutzheader des Ziels aus.
  **Fix:** in `auth_verify` `X-Original-URI` parsen und `ip:port` gegen `settings.external_windows` prüfen (~15 Zeilen).

- [x] **M17: Login ohne echte Brute-Force-Bremse**
  `backend/main.py:147` — `asyncio.sleep(0.8)` wirkt pro Request, parallel unbegrenzt; kein `limit_req` in nginx.
  **Fix:** `limit_req_zone` nur für `/api/auth/login` (5 r/m, burst 5) — 3 Zeilen nginx.

- [x] **M18: Session-Secret ohne `SESSION_SECRET` direkt aus dem Passwort ableitbar; keine Revocation**
  `backend/app/auth.py:32-35` (eine SHA-256-Runde, kein Salt) + `backend/main.py:160-163` (Logout nur clientseitig) — geleaktes 30-Tage-Token erlaubt Offline-Wörterbuchangriff aufs Admin-Passwort.
  **Fix:** `SESSION_SECRET` als Zufallswert in `.env` setzen (sofort, kein Code); optional `pbkdf2_hmac` in `_secret()`.

- [x] **M19: Telegram-Feature ist tot verdrahtet — Modul existiert nicht**
  `supervisord.conf:72` (`python -m telegram_bot` → gibt es nicht) + `.env.example:21-23` + Settings-Toggle — `TELEGRAM_ENABLED=true` endet in stillem supervisord-FATAL; Falle für Fremdnutzer des Public-Repos.
  **Fix:** Modul nachliefern ODER Programm-Block, Env-Keys und Settings-Key entfernen.

- [x] **M20: Docker- und nginx-Logs wachsen unbegrenzt** (Server hatte schon Root-Disk-Druck!)
  `docker-compose.yml` ohne `logging:`-Block; nginx loggt zusätzlich nach `/var/log/nginx/` im Container, kein logrotate.
  **Fix:** compose `logging: {max-size: "20m", max-file: "5"}` + im Template `access_log /dev/stdout; error_log /dev/stderr;`.

- [x] **M21: Container bleibt „gesund tot": supervisord-FATAL beendet den Container nicht**
  `supervisord.conf` (kein eventlistener) + `docker-compose.yml:8` — api-FATAL → nginx läuft weiter, Healthcheck unhealthy, aber Docker startet unhealthy nicht neu → dauerhaft 502 bis Handeingriff.
  **Fix:** `[eventlistener:fatal_exit]`, der bei `PROCESS_STATE_FATAL` supervisord killt → `restart: unless-stopped` greift (~10 Zeilen).

## MITTEL — Frontend

- [x] **M22: Settings: Speicherfehler wird verschluckt** — `frontend/src/components/Settings.jsx:33-35`; Fix: `error`-State + rote Meldung (Muster aus ConnectionsModal).
- [x] **M23: LLM-Provider-Auswahl ist wirkungslos; fertiges Modell-API ohne UI** — `Settings.jsx:52-61` schreibt `llm_provider`, das niemand liest (`app/llm.py:58`: Provider bleibt env-bestimmt); `orch_model` + `GET /api/models` (`main.py:626-638`) existieren ohne UI. Fix: Provider-Select raus, Modell-Dropdown aus `/api/models` rein (siehe Feature F9).
- [x] **M24: Editor: Schließen verwirft ungespeicherte Änderungen ohne Rückfrage** — `EditorModal.jsx:159-164`; `dirty` existiert schon. Fix: confirm bei dirty.
- [x] **M25: Terminal-⏻ killt die Remote-Shell ohne Rückfrage und meldet Fehlschläge nicht** — `TerminalPanel.jsx:98-108`; Fix: confirm + im finally `loadSessions()` statt lokal filtern.
- [x] **M26: Chat: Session-Wechsel während laufender Antwort mischt Verläufe** — `Chat.jsx:102-108`; Fix: Ziel-Session beim Absenden merken und vor Append vergleichen (oder Dropdown während `loading` disablen).
- [x] **M27: Chat: Eingabe geht bei Sendefehler verloren** — `Chat.jsx:97` leert das Input vor `postChat`; Fix: erst nach Erfolg leeren bzw. im catch zurückschreiben.
- [x] **M28: Polling läuft im Hintergrund-Tab ungebremst weiter** — `AgentsPanel.jsx:98` (8 s × 7 Requests) + `QuestionsBanner.jsx:31` (5 s); Fix: `document.hidden`-Gate + Sofort-Load bei `visibilitychange`. Langfristig Feature F4 (SSE).
- [x] **M29: Fenster-Drag/Resize friert über iframes (noVNC) ein** — `Workspace.jsx:210-212`; Fix: `setPointerCapture(e.pointerId)` in `startGesture`.

---

## NIEDRIG

### Backend / Mailbox
- [x] **N1: Task-Reihenfolge nicht FIFO** (2× gefunden) — `mailbox.py:176, 225` + `agent_watcher.py:605`: sortiert nach uuid-Dateiname; Fix: nach `created_at`.
- [x] **N2: Envelope-IDs nur 32 Bit — Kollision überschreibt still** — `mailbox.py:54-56`; Fix: 16 Hex-Zeichen + Existenz-Check in `post()`.
- [x] **N3: `/api/models` blockiert den Event-Loop bis 10 s** — `llm.py:75-77` sync `urlopen` in `async def` (`main.py:632-637`); Fix: `asyncio.to_thread`.
- [x] **N4: Leere Assistant-Message vergiftet Anthropic-Sessions dauerhaft** — `llm.py:161`; Fix: leere Messages beim Replay überspringen.
- [x] **N5: System-Prompt lehrt falsche `mark_read`-Argumentreihenfolge** — `orchestrator_core.py:39-40` vs. Signatur `mcp_server.py:332`; Fix: Prompt korrigieren.
- [x] **N6: chat_store: Verbindung pro Aufruf, nie geschlossen** — `chat_store.py:24-50`; Fix: `contextlib.closing` oder eine Verbindung unter `_lock`.
- [x] **N7: integrations: YAML-Fehler still geschluckt; Truncation ohne Marker** — `integrations.py:47-56, 107-108`; Fix: Fehler loggen, `truncated: true` + Marker anhängen.
- [x] **N8: `write_project_file` nicht atomar** — `mcp_server.py:362`; Fix: tmp+`os.replace`-Muster wiederverwenden.
- [x] **N9: Kleinkram Backend** — `main.py:167-171` `_locks` wächst unbegrenzt; `llm.py:130` Ollama-Timeout 180 s hart → `OLLAMA_TIMEOUT`-Env; Tool-Fehler ohne `[TOOL-FEHLER]`-Markierung fürs LLM (`orchestrator_core.py:85` verwirft `is_error`).

### SSH / Watcher / Config
- [x] **N10: ssh_bridge: Session nach fehlgeschlagenem Replay unsterblich; Replay-Index-Race bei vollem Puffer** — `ssh_bridge.py:96-100, 196-214`; Fix: try/except mit Expiry-Neustart, Replay aus Snapshot.
- [x] **N11: `save_settings` nicht atomar — Crash vergisst Automatik-Schalter und Not-Aus** — `config.py:56-65`; Fix: `atomic_write_json`-Muster.
- [x] **N12: Upload: Backslash-Pfad-Schmuggel auf Windows-Zielen** — `remote_files.py:185-187`; Fix: zusätzlich `ntpath.basename`.
- [x] **N13: remote_files `delete` folgt Symlinks aufs Verzeichnis-Ziel** — `remote_files.py:167-180`; Fix: `lstat`, Symlinks nur `remove`.
- [x] **N14: setup_agent_pc.sh nicht idempotent** — `setup_agent_pc.sh:25`; Fix: vorher `claude mcp remove … || true`.
- [x] **N15: `python`-Kommando als einziges ungequotet im Remote-Befehl** — `auto_watcher.py:221-225`; Fix: quoten oder dokumentieren.

### Infra
- [x] **N16: `supervisorctl restart mcp` funktioniert nicht (fehlende Sections), wird aber in CLAUDE.md/agents.yaml dokumentiert** — Fix: `[unix_http_server]`+`[rpcinterface]`+`[supervisorctl]` ergänzen.
- [x] **N17: SSH-Private-Keys und chat.db liegen im Datei-Browser-Root** — `config.py:21` (`/workspace/keys` via `/api/files/…` abrufbar; macht M16 scharf); `ssl/privkey.pem`-Leseversuch = unbehandelter 500. Fix: `keys/`, `ssl/`, `chat.db` in files ausblenden oder KEYS_DIR verlegen.
- [x] **N18: `.env.example` widerspricht dem Code** — „danach in DB gehasht" ist falsch (`auth.py:22-23`); `OPENROUTER_API_KEY` wird nirgends gelesen; fehlend: `MCP_TUNNEL_ENABLED`, `ORCH_MODEL`, `ORCH_MAX_TOOL_ROUNDS`, `AUTO_STOP_GRACE`.
- [x] **N19: Kleinkram Infra** — `make_cert.sh:11` scheitert auf frischem Clone (`mkdir -p` fehlt); compose mountet `ca.key` unnötig in den Container; kein `.dockerignore` (Build-Context schickt `.env`/`secrets/` an den Daemon); `npm ci || npm install` hebelt Lockfile still aus; `certbot` im Image ungenutzt; `workspace/` host-seitig 777 → `chmod 750`; HSTS-Zeile einkommentieren.

### Frontend
- [x] **N20: Ein fehlgeschlagener Poll leert Panel/Banner (inkl. Antwort-Entwürfe)** — `AgentsPanel.jsx:93-95` + `QuestionsBanner.jsx:26`; Fix: letzten Stand behalten + „Verbindung gestört"-Hinweis.
- [x] **N21: Chat-Sessions können nie gelöscht werden; tote api.js-Exporte** — `api.js:148-157` (`deleteChatSession` u. a. nirgends genutzt, Endpoint existiert); Fix: Löschen-Knopf am Verlauf-Dropdown, Rest aufräumen.
- [x] **N22: CodeMirror hängt eager im Hauptbundle** — `App.jsx:11`; Fix: `lazy(() => import("./components/EditorModal"))`.
- [x] **N23: Kein Logout-Knopf trotz vorhandenem Endpoint** — `main.py:160` vs. TopBar; Fix: „Abmelden" in der TopBar.
- [x] **N24: Automatik-Log nur als Hover-Tooltip — mobil unerreichbar** — `AgentsPanel.jsx:238-241`; Fix: aufklappbar per Klick (siehe auch Feature F2).
- [x] **N25: Modal: Backdrop-Fehlklick verwirft Eingaben, kein Escape** — `Modal.jsx:4-6`; Fix: Backdrop-Close nur bei down+up auf Backdrop, Escape-Handler.
- [x] **N26: FilesPanel: spätes `reload()` überschreibt Listing nach Navigation** — `FilesPanel.jsx:76-79`; Fix: `setLocalKey(k => k+1)` statt eigenem reload (Effekt hat Stale-Handling).
- [x] **N27: Attention-Blinken auch im gerade aktiven Tab** — `App.jsx:91-105, 160-161`; Fix: in `flag(id)` aktiven Tab ausnehmen.
- [x] **N28: Konsistenz-Sammelposten** — `TerminalPanel.jsx:43, 99-102` rohes `fetch()` ohne 401-Handling; `FilesPanel.jsx:103-120` `prompt()`/`confirm()` statt Modals; `Workspace.jsx:43-60` Layout-Einträge gelöschter Fenster bleiben in localStorage.

---

## Test-Lücken (riskant + billig testbar, alles Stdlib)

- [x] **T1: MCP-Transport-Roundtrip** `inbox → claim_task → complete_task` gegen echten `build_server()` — hätte H1 gefangen; mit `project`-Assertion als Regressionstest.
- [x] **T2: Nebenläufigkeit Mailbox**: Doppel-Claim (H2) und `resolve_question` vs. `park_wenn_offene_fragen` (M1) als deterministische Interleaving-Tests.
- [x] **T3: `write_response`-Kanten**: Doppel-Abschluss, Abschluss ohne Claim, `mark_read`-Guard.
- [x] **T4: `run_turn`**: MAX_TOOL_ROUNDS-Abbruch, Ollama-Parsing (arguments als String/kaputt), leerer Text + Tool-Calls — mit Fake-`call_tool`.
- [x] **T5: `run_claude`-stream-json-Parsing** gegen Fake-claude-Script (result-Event, `is_error`, `permission_denials`, Nicht-JSON, Timeout/killpg) — komplexester Parser des Watchers, ungetestet.
- [x] **T6: `projekt_workdir` (Ausbruch, fehlendes Verzeichnis) + `fehlerserie`** — reine Funktionen.
- [x] **T7: `McpClient`** gegen Stub-`http.server` (JSON/SSE, Session-Id).
- [x] **T8: `strip_ansi`/`_buffer_append`-Replay-Logik** der ssh_bridge; `auto_watcher`-Zustandsmaschine mit gefaktem asyncssh.
- [x] **T9: chat_store + integrations** (Roundtrip, delete; Methoden-Allowlist, `://`-Guard).
- [x] **T10: main.py-Endpunkte** `answer_question`/`close_task` per TestClient gegen tmp-Mailbox (sichert M6 gleich mit ab).

---

## Nach dem Deploy manuell prüfen

Der Deploy (`docker compose up -d --build`) bringt zusätzlich die Automatik-Issues
#12–#19 erstmals in Betrieb — entsprechend genau hinschauen.

**Zuerst (betrifft alle Agenten):**
- [ ] `docker compose exec -u app agent-dashboard supervisorctl status` muss jetzt
      eine Tabelle liefern (vorher „refused connection"); danach einmal
      `supervisorctl restart mcp` gegenprüfen — genau dieser Weg steht in CLAUDE.md.
- [ ] `mcp_ports.json` muss für JEDEN SSH-Agenten einen Eintrag haben. Fehlt einer,
      steht im Log „Tunnel ausgesetzt" und es gibt bewusst keinen Tunnel mehr
      (der :9000-Rückfall ist weg). Bestehende Ports (`server: 9100`) müssen
      unverändert bleiben.
- [ ] Anmelden: Sessions bleiben gültig (`SESSION_SECRET` ist gesetzt), das
      Passwort-Fallback wurde nur gehärtet.

**Terminal (die zwei HOCH-Frontend-Befunde):**
- [ ] Netz kurz kappen bzw. Handy sperren → nach dem Reconnect darf der Verlauf
      NICHT doppelt dastehen.
- [ ] Dasselbe Terminal in einem zweiten Tab öffnen → im ersten erscheint der
      Übernahme-Hinweis; Tab in den Hintergrund und zurück → er darf die Session
      NICHT von selbst zurückholen, nur über „Wieder verbinden".

**Automatik (nur mit einem echten Agenten testbar):**
- [ ] Automatik ausschalten → die Antwort kommt sofort, Panel zeigt „stoppt";
      der Watcher endet erst nach dem laufenden Task (kein 31-Minuten-Hänger).
- [ ] Not-Aus → binnen ~5 s ist auf dem Agenten-PC kein `claude`-Prozess mehr da
      (`ps -ef | grep claude`), der Task erscheint als error mit
      „[watcher] Not-Aus — Lauf abgebrochen (kill)".
- [ ] Task mit `project` über die Automatik laufen lassen und prüfen, dass er im
      Projekt-Unterverzeichnis arbeitet (das war H1 — vorher still im Basis-workdir).
- [ ] Zweiten Watcher von Hand starten → muss sich mit „Es läuft bereits ein
      Watcher …" beenden (rc=2).
- [ ] Nach ~15 min läuft die Mailbox-Pflege das erste Mal; im Log darf nichts
      wieder eingereiht werden, solange kein Task wirklich verwaist ist.

**Sicherheit:**
- [ ] Ein NICHT eingetragenes LAN-Gerät über `/ext/<ip>/<port>/` aufrufen → 403.
      Ein eingetragenes externes Fenster (noVNC) muss weiter funktionieren,
      inklusive WebSocket.
- [ ] Im Datei-Panel dürfen `keys/`, `ssl/` und `chat.db` nicht mehr auftauchen.
- [ ] 7× mit falschem Passwort anmelden → ab dem 7. Versuch 429 statt 401.

**Betrieb:**
- [ ] `docker inspect -f '{{json .HostConfig.LogConfig}}' agent-dashboard` →
      `max-size 20m`, `max-file 5`.
- [ ] `docker compose exec agent-dashboard ls -l /app/ssl` → kein `ca.key`.
- [ ] Datei-Browser über SFTP: ab dem zweiten Klick spürbar schneller.

## Feature-Ideen (sinnvoll einzubauen, grob nach Nutzen/Aufwand sortiert)

Nicht Teil der Fehlerbehebung — das sind Erweiterungen, die du entscheiden solltest.
Zwei kleine sind beim Fixen mit abgefallen (F8, F9), drei sind durch die Fixes
halb fertig (F2, F5, F6 — dort steht jeweils, was noch fehlt).

- [ ] **F1: Task-Detailansicht + Response-Archiv im Panel** — alles ist heute auf eine `truncate`-Zeile gestutzt; Klick-Modal pro Task (volle Instruction, Ergebnis, Log inkl. „Berechtigung verweigert") + Archiv-Reiter über das existierende `read_responses`. Macht das Panel vom Ampel-Monitor zum Arbeitswerkzeug.
- [~] **F2: Live-Fortschritt des Automatik-Watchers im UI** — TEILWEISE (16.08.: aufklappbares Log je Agent statt Hover-Tooltip, Polling). Offen bleibt der mitlaufende Live-Stream: — der Watcher liefert seit #18 Tool-Calls/Text, im UI kommt nur ein Hover-Tooltip an. Aufklappbares, mitscrollendes Log je Agent = das fehlende Vertrauenssignal für den unbeaufsichtigten Modus. (Behebt N24 gleich mit.)
- [ ] **F3: Chat-Streaming statt Blackbox-POST** — `/api/chat` blockiert bei 25 Tool-Runden minutenlang („Orchestrator denkt…"). SSE-Modus, der pro Runde `{tool, round}` und am Ende den Text liefert; provider-neutral machbar (Ollama `stream:true`, Anthropic `messages.stream`), ermöglicht perspektivisch einen Abbrechen-Knopf. (Entschärft M26/M27 strukturell.)
- [ ] **F4: Events statt Polling** — Verzeichnis-Watcher (watchfiles/inotify) im API-Prozess + SSE `/api/events`; ersetzt das 5–8-s-Polling von Agents/Tasks/Questions (M28), Polling bleibt Fallback.
- [~] **F5: Stale-Task-Reaper + Watcher-Heartbeat + Instanz-Lock** — TEILWEISE (16.08.: `claimed_at`, serverseitiger Requeue via `mailbox.pflege`, Instanz-Lock auf dem Agenten-PC). Offen: Heartbeat + Banner mit Ein-Klick-Requeue: — `claimed_at`-Stempel beim Claim, Banner „Task X läuft seit 45 min ohne Aktivität" mit Ein-Klick-Requeue; Heartbeat lässt den Manager halbtote SSH-Sessions in Sekunden erkennen; Lock-Datei verhindert Doppel-Watcher. Schließt M9/H2 sichtbar und dauerhaft.
- [~] **F6: Task-Abbruch-Button im Panel** — Unterbau steht (stdin-`kill` aus H3), es fehlt nur noch Endpunkt + Knopf: — baut auf dem `kill`-stdin-Kommando aus H3 auf: einen festgefahrenen claude-Lauf gezielt abbrechen, ohne Automatik/Not-Aus für alles; Task landet sauber mit Klartext-error in `.failed`.
- [ ] **F7: Geteilter SSH-Verbindungs-Pool pro Agent** — Terminal, SFTP, Tunnel und Automatik bauen heute jeweils eigene Verbindungen; asyncssh multiplext Channels über eine. Macht den Datei-Browser spürbar schneller (M15) und bündelt Reconnect-Sonderfälle. Dazu Watcher-Versionsmeldung + Hash-basierter Script-Upload.
- [x] **F8: Prompt-Caching auf dem Anthropic-Pfad** (16.08. erledigt: Cache-Punkt auf System+Tools in `_anthropic_call`) — System-Prompt + Tool-Liste sind byte-stabil; `cache_control: ephemeral` spart bei bis zu 25 Runden ~90 % Input-Kosten. Zwei Zeilen in `_anthropic_call`, Ollama unberührt.
- [x] **F9: Modell-Umschalter** (16.08. erledigt — sitzt im Settings-Dialog statt im Chat-Kopf: `orch_model` global, Chat-Kopf ist mobil schon voll) — `orch_model` + `GET /api/models` sind fertig; Dropdown neben der Session-Auswahl, ersetzt das wirkungslose Provider-Select (M23). Fast reine UI-Arbeit.
- [ ] **F10: Push-Benachrichtigungen für Rückfragen/fertige Tasks** — PWA-Manifest ist verdrahtet, mobil schläft das Polling während `needs_confirm` den Task blockiert; Notification-API bzw. Web Push über den bestehenden nginx.
- [ ] **F11: Backup Mailboxen + chat.db** — nächtlich (oder Anschluss an `~/bin/backup-netshare.sh`): `workspace/config`, `keys`, `mailboxes*` als tar.gz + `sqlite3 chat.db ".backup"` (konsistent statt Roh-Kopie), 14-Tage-Rotation.
- [ ] **F12: Deploy-Skript mit Health-Gate** — `scripts/deploy.sh`: build → `up -d` → auf `/api/health` warten, sonst zurück aufs vorherige Image. Passt zum offenen Deploy von #12–#19 und verhindert „gesund tot" (M21) nach Updates.
- [ ] **F13: Monitoring via Home Assistant** — `/api/health` um Prozessstatus + Disk-Free erweitern, HA-REST-Sensor + Telegram-Notify bei unhealthy/Disk > 90 %. Fängt die zwei realistischen Ausfallarten (Prozess tot, Platte voll).

---

## Vom Review geprüft und in Ordnung (nicht erneut untersuchen)

Cookie-Handling (HttpOnly/SameSite=Lax/Secure, WS prüft Cookie) · CSRF-Lage (state-changing nur POST/PUT/DELETE, kein CORS) · Path-Traversal in `files._safe`/MCP-`_safe` (resolve-basiert, symlink-sicher; MCP auf `/workspace/projects` begrenzt) · Git-Hygiene (`.env`, `ssl/`, `secrets/`, `workspace/` nicht getrackt) · Compose-Härtung (keine gemappten 5000/9000, kein docker.sock, cap_drop ALL, no-new-privileges, Limits) · `/api/health` ohne MCP/LLM-Abhängigkeit · `ALLOWED_KEYS` konsistent · `mcp_scope.KNOWN_TOOLS` vollständig (15/15) · Mailbox-Atomik im Kern (tmp+fsync+replace, exklusiver Claim per `os.replace` auf dem Datei-Transport) · Lebenszyklen #15/#17 auf Datei-Ebene getestet.
