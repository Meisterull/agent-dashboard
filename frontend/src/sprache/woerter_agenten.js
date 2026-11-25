// Deutsch → Englisch. Teil des Wörterbuchs, zusammengelegt in ../sprache.js.
export default {
  // AgentsPanel.jsx — Kopfzeile, Notaus, Aktualisieren
  "MCP-Monitor · Aufgaben": "MCP monitor · tasks",
  "Letzte Aktualisierung fehlgeschlagen — angezeigt wird der letzte bekannte Stand.":
    "Last update failed — showing the last known state.",
  "Verbindung gestört": "Connection disrupted",
  "Not-Aus lösen — eingeschaltete Automatiken starten wieder":
    "Release emergency stop — enabled automations start again",
  "Not-Aus: alle Automatiken sofort hart stoppen":
    "Emergency stop: hard-stop all automations immediately",
  "Not-Aus aktiv": "Emergency stop active",
  "Not-Aus": "Emergency stop",
  "jetzt aktualisieren": "refresh now",
  "keine Agenten": "no agents",
  "{0} ungelesene Nachricht(en)": "{0} unread message(s)",
  "Automatik: {0}": "Automation: {0}",

  // AgentsPanel.jsx — Automatik-Leiste
  "Not-Aus aktiv — erst lösen": "Emergency stop active — release it first",
  "Automatik ausschalten (laufender Task darf fertig werden)":
    "Turn off automation (a running task is allowed to finish)",
  "Automatik einschalten: Inbox selbständig abarbeiten":
    "Turn on automation: work through the inbox unattended",
  "keine nutzbare SSH-Verbindung (key_file fehlt?)":
    "no usable SSH connection (key_file missing?)",
  "Automatik": "Automation",
  "Log einklappen": "Collapse log",
  "Fortschritts-Log anzeigen": "Show progress log",
  "Roh-Log ({0})": "Raw log ({0})",
  // Ereignisse.jsx — Zeitleiste je Agent
  "Ereignisse": "Events",
  "Ereignisse anzeigen": "Show events",
  "Ereignisse einklappen": "Collapse events",
  "nur Probleme": "problems only",
  "nur Warnungen und Fehler zeigen": "show warnings and errors only",
  "keine Probleme": "no problems",
  "noch keine Ereignisse": "no events yet",
  "Task {0} aufklappen": "expand task {0}",
  "mehr laden": "load more",

  // AgentsPanel.jsx — Inbox / Nachrichten / Outbox
  "Inbox ({0})": "Inbox ({0})",
  "Alle erledigten Eingänge (Antworten, Nachrichten) ins Archiv legen. Offene Tasks und Rückfragen bleiben liegen.":
    "Archive all finished entries (replies, messages). Open tasks and questions are left alone.",
  "✓ alles gelesen": "✓ mark all read",
  "leer": "empty",
  "Task manuell schließen (ohne Ergebnis)": "Close task manually (without a result)",
  "Nachrichten ({0})": "Messages ({0})",
  "gelesen — ins Archiv legen": "read — move to archive",
  "Outbox ({0})": "Outbox ({0})",

  // AgentsPanel.jsx — Nicht-Task-Eingänge (KIND_LABEL)
  "Nachricht": "Message",
  "Antwort": "Reply",
  "Ergebnis": "Result",
  "Rückfrage": "Question",

  // AgentsPanel.jsx — StatusBadge (Task-Status + Automatik-Status als Anzeige-Label)
  "pending": "pending",
  "running": "running",
  "done": "done",
  "error": "error",
  "needs_confirm": "needs confirmation",
  "an": "on",
  "aus": "off",
  "startet": "starting",
  "stoppt": "stopping",
  "fehler": "error",
  "gesperrt": "locked",

  // AgentsPanel.jsx — Dialoge (bestaetigen/melden)
  "Task schließen": "Close task",
  "Task {0} ohne Ergebnis schließen?": "Close task {0} without a result?",
  "Schließen": "Close",
  "Fehler": "Error",
  "Schließen fehlgeschlagen: {0}": "Closing failed: {0}",
  "Inbox aufräumen": "Clean up inbox",
  "Alle erledigten Eingänge von '{0}' ins Archiv legen?\nOffene Tasks und Rückfragen bleiben liegen.":
    "Archive all finished entries of '{0}'?\nOpen tasks and questions are left alone.",
  "Archivieren": "Archive",
  "Inbox": "Inbox",
  "Nichts zu archivieren — die Inbox ist schon sauber.":
    "Nothing to archive — the inbox is already clean.",
  "Aufräumen fehlgeschlagen: {0}": "Cleanup failed: {0}",
  "Archivieren fehlgeschlagen: {0}": "Archiving failed: {0}",
  "Automatik einschalten": "Turn on automation",
  "Automatik für '{0}' einschalten?\nClaude Code arbeitet dann UNBEAUFSICHTIGT Tasks aus der Inbox ab.":
    "Turn on automation for '{0}'?\nClaude Code will then work through inbox tasks UNATTENDED.",
  "Einschalten": "Turn on",
  "Umschalten fehlgeschlagen: {0}": "Switching failed: {0}",
  "Not-Aus: ALLE Automatiken sofort hart stoppen?":
    "Emergency stop: hard-stop ALL automations immediately?",
  "Stoppen": "Stop",
  "Not-Aus fehlgeschlagen: {0}": "Emergency stop failed: {0}",

  // Dialog.jsx — Standard-Knöpfe
  "OK": "OK",
  "Abbrechen": "Cancel",

  // ConnectionsModal.jsx
  "„{0}“ angelegt. Einmalig auf dem Zielrechner ausführen (als der SSH-Benutzer):":
    "“{0}” created. Run this once on the target machine (as the SSH user):",
  "kopiert ✓": "copied ✓",
  "Befehl kopieren": "Copy command",
  "SSH-Verbindungen": "SSH connections",
  "Vorhanden": "Existing",
  "keine Verbindungen": "no connections",
  "Public Key / Einrichtungsbefehl anzeigen": "Show public key / setup command",
  "Key": "Key",
  "Verbindung löschen": "Delete connection",
  "in agents.yaml gepflegt — dort von Hand ändern": "managed in agents.yaml — edit it there by hand",
  "Verbindung „{0}“ löschen (samt Schlüssel)?": "Delete connection “{0}” (including its key)?",
  "Löschen": "Delete",
  "Neue Verbindung": "New connection",
  "Name (z.B. buero-pc)": "Name (e.g. office-pc)",
  "SSH-Benutzer": "SSH user",
  "Host / IP": "Host / IP",
  "Port": "Port",
  "vorhandenen privaten Schlüssel verwenden (sonst wird ein neues Schlüsselpaar erzeugt)":
    "use an existing private key (otherwise a new key pair is generated)",
  "legt an…": "creating…",
  "Anlegen": "Create",

  // RollenDialog.jsx + Rollen-Knopf/Badge im AgentsPanel (St.1)
  "Rollen": "Roles",
  "Rollen verwalten — Prompt und Rechte je Task-Lauf":
    "Manage roles — prompt and permissions per task run",
  "Rolle dieses Laufs": "Role of this run",
  "Eine Rolle gibt einem Task-Lauf einen Prompt und kann seine Rechte einschränken — als Schnittmenge mit agents.yaml, nie erweiternd.":
    "A role gives a task run a prompt and can restrict its permissions — as an intersection with agents.yaml, never widening them.",
  "neue-rolle (kleinbuchstaben, - und _)": "new-role (lowercase, - and _)",
  "Ungültiger Name — erlaubt: kleinbuchstaben, ziffern, - und _":
    "Invalid name — allowed: lowercase letters, digits, - and _",
  "Noch keine Rolle angelegt — oben einen Namen vergeben, die Vorlage ist vorausgefüllt.":
    "No role yet — enter a name above, the template is prefilled.",
  "Datei fehlerhaft: {0}": "File is broken: {0}",
  "ohne Beschreibung": "no description",
  "schränkt Rechte ein": "restricts permissions",
  "Rolle löschen": "Delete role",
  "Rolle „{0}“ endgültig löschen?": "Delete role “{0}” permanently?",
  "noch nicht gespeichert": "not saved yet",
  "Laden fehlgeschlagen: {0}": "Loading failed: {0}",

  // ZeitplaeneDialog.jsx + ⏰-Knopf/Badge im AgentsPanel (St.2)
  "Zeitpläne": "Schedules",
  "Zeitpläne — Tasks zur Uhrzeit, einmalig oder wiederkehrend":
    "Schedules — tasks at a set time, one-off or recurring",
  "Fällige Pläne werden als normale Tasks gepostet (Absender: du) — Ergebnis kommt wie gewohnt als Nachricht/Push. Verpasste Termine verfallen; „nachholen“ holt höchstens einen nach.":
    "Due plans are posted as regular tasks (sender: you) — results arrive as messages/push like any other task. Missed slots expire; “catch up” runs at most one late.",
  "neuer-plan (kleinbuchstaben, - und _)": "new-plan (lowercase, - and _)",
  "„{0}“ gibt es schon.": "“{0}” already exists.",
  "Noch kein Zeitplan — oben einen Namen vergeben.":
    "No schedule yet — enter a name above.",
  "täglich": "daily",
  "zuletzt: {0}": "last run: {0}",
  "noch nie gelaufen": "never ran yet",
  "Plan aktiv/inaktiv schalten (speichert sofort)":
    "Toggle plan on/off (saves immediately)",
  "an": "on",
  "aus": "off",
  "sofort ausführen (Test)": "run now (test)",
  "Sofort ausgeführt — Task {0} an {1}.": "Ran now — task {0} sent to {1}.",
  "Ausführen fehlgeschlagen: {0}": "Running failed: {0}",
  "Plan löschen": "Delete plan",
  "Plan „{0}“ endgültig löschen?": "Delete plan “{0}” permanently?",
  "Agent": "Agent",
  "Rolle": "Role",
  "(keine Rolle)": "(no role)",
  "Uhrzeit": "Time",
  "Projekt (optional)": "Project (optional)",
  "Unterverzeichnis im workdir": "subdirectory inside workdir",
  "Tage (keiner gewählt = täglich)": "Days (none selected = daily)",
  "Mo": "Mon",
  "Di": "Tue",
  "Mi": "Wed",
  "Do": "Thu",
  "Fr": "Fri",
  "Sa": "Sat",
  "So": "Sun",
  "nachholen — ein verpasster Termin läuft nach, sobald alles wieder lebt":
    "catch up — one missed slot runs late once everything is back",
  "Auftrag": "Instruction",
  "geplant — läuft nicht vor {0}": "scheduled — will not run before {0}",
  "{0}× von der Pflege zurückgereiht (Watcher-Lauf verstummt) — nach {1} wird der Task aufgegeben":
    "requeued {0}× by maintenance (watcher run went silent) — the task is given up after {1}",

  // Verbrauchszähler im AgentsPanel (St.3)
  "heute: {0} Tasks · {1} Tok · {2} $": "today: {0} tasks · {1} tok · ${2}",
  "5 h: {0} Tok": "5 h: {0} tok",
  "Schwelle erreicht — geplante Tasks pausieren":
    "threshold reached — scheduled tasks pause",
  // Lauf-Daten an der Antwort (Issues #36–#39)
  "abgebrochen · fortsetzbar": "aborted · resumable",
  "abgebrochen": "aborted",
  "mit Einschränkungen": "with restrictions",
  "einzelne Werkzeug-Aufrufe wurden verweigert": "some tool calls were denied",
  "Lauf hat die vorherige Claude-Sitzung fortgesetzt":
    "run resumed the previous Claude session",
  "Sitzung fortgesetzt": "session continued",
  "neue Sitzung": "new session",
  "verweigert": "denied",
  "Sitzung übernehmen": "Take over session",
  "Kontext {0}": "context {0}",
  "Vorgang {0}": "thread {0}",
  "{0} ungelesene Einträge — die Automatik reagiert darauf nicht":
    "{0} unread entries — automation does not react to them",
  "reagiert auf: {0}": "reacts to: {0}",
  "Auch Nachrichten, Ergebnisse und Rückfragen wecken die Automatik (kostet je Weckruf einen Lauf). Ohne Haken bleibt solche Post liegen.":
    "Messages, results and questions also wake the automation (each wake-up costs one run). Without the tick such mail stays untouched.",
  "Weckruf": "Wake-up",
  "Schleifenschutz: von {0} kamen in der letzten Stunde schon zu viele Weckrufe.":
    "Loop guard: too many wake-ups from {0} within the last hour.",
  "Nichts zu wecken — die Einträge wurden schon einmal als Weckruf eingestellt.":
    "Nothing to wake — these entries were already queued as a wake-up once.",
  "Weckruf fehlgeschlagen: {0}": "Wake-up failed: {0}",
  "Jetzt wecken": "Wake now",
  "Tagesbericht": "Daily report",
  "gesamt {0} $": "total {0} $",
  "auch ruhige Agenten": "include quiet agents",
  "Push täglich um {0} — abschaltbar unter Einstellungen.": "Push daily at {0} — can be switched off in settings.",
  "Kein Push eingeschaltet (Einstellungen → Tagesbericht) — diese Seite gibt es trotzdem.":
    "No push enabled (Settings → Daily report) — this page exists anyway.",
  "An diesem Tag ist nichts passiert.": "Nothing happened on this day.",
  "{0} erledigt": "{0} done",
  "{0} Fehler": "{0} errors",
  "{0} mit Einschränkungen": "{0} with restrictions",
  "Rückfrage {0}": "Question {0}",
  "{0} Freigabe(n) warten": "{0} approval(s) waiting",
  "{0} Task(s) liegen, längster seit {1} min": "{0} task(s) waiting, longest for {1} min",
  "{0} beanspruchte(r) Task(s) ohne Lebenszeichen": "{0} claimed task(s) without a sign of life",
  "gescheitert": "failed",
  "größter Lauf {0} $": "largest run {0} $",
  "Auffälligkeiten": "Anomalies",
  "erledigte Tasks": "finished tasks",
  "Tagesbericht — erledigt, Kosten, Offenes je Agent": "Daily report — done, cost, open items per agent",
  "ausgestellt": "issued", "übernommen": "accepted", "eingespielt": "deployed", "live": "live",
  "zurückgezogen": "withdrawn",
  "Paket {0}": "Package {0}",
  "Phase von Hand auf „{0}“ setzen?": "Set the phase to “{0}” by hand?",
  "Deploy-Pakete": "Deploy packages",
  "{0} offen — das älteste seit {1}": "{0} open — the oldest since {1}",
  "kein offenes Paket": "no open package",
  "Noch nichts gemeldet. Agenten melden ein Paket mit dem MCP-Tool deploy_status (Token, Phase, Repos mit erwartetem SHA, Flags wie MIGRATE, enthaltene Tasks).":
    "Nothing reported yet. Agents report a package with the MCP tool deploy_status (token, phase, repos with expected SHA, flags such as MIGRATE, contained tasks).",
  "{0}. Ausstellung": "issue no. {0}",
  "{0} Repos": "{0} repos",
  "als live markieren": "mark as live",
  "zurückziehen": "withdraw",
  "Deploy-Pakete — was steht an, was ist live": "Deploy packages — what is pending, what is live",
  "läuft": "running", "wartet auf Antwort": "waiting for answer",
  "wartet auf Freigabe": "waiting for approval", "fertig": "done",
  "Notiz zum Vorgang": "Note on the thread",
  "z. B. „wartet auf Abend-Deploy“ — leer entfernt die Notiz": "e.g. “waiting for evening deploy” — empty removes the note",
  "Vorgänge": "Threads",
  "Zeitraum": "Period",
  "{0} Tage": "{0} days",
  "nur benannte Vorgänge": "named threads only",
  "keine Vorgänge im Zeitraum": "no threads in this period",
  "{0} Task": "{0} task", "{0} Tasks": "{0} tasks",
  "Notiz ändern": "Edit note", "Notiz": "Note",
  "aufgegeben": "given up", "fragt": "asks", "offen": "open",
  "Vorgänge — einen Auftrag über alle Agenten verfolgen": "Threads — follow one job across all agents",
  "Vorgang {0} öffnen": "Open thread {0}",
  "wartet seit {0}": "waiting for {0}",
  "Tasks warten — die Automatik ist aus.": "Tasks are waiting — automation is off.",
  "Automatik einschalten, die liegenden Tasks abarbeiten lassen und danach wieder ausschalten":
    "Switch automation on, let it work through the waiting tasks, then switch it off again",
  "Per Automatik abarbeiten": "Work through with automation",
  "Später erinnern": "Remind me later",
  "Einmal-Lauf: die Automatik schaltet sich ab, sobald nichts mehr zu tun ist.":
    "One-off run: automation switches itself off once nothing is left to do.",
  "Erinnerung verschieben": "Reschedule reminder",
  "Neuer Termin (JJJJ-MM-TT oder JJJJ-MM-TTThh:mm)": "New date (YYYY-MM-DD or YYYY-MM-DDThh:mm)",
  "Erinnerung löschen": "Delete reminder",
  "Erinnerungen": "Reminders",
  "nächste {0}": "next {0}",
  "keine offenen Erinnerungen": "no open reminders",
  "Task bei Automatik, sonst Nachricht": "task if automation is on, otherwise message",
  "verschieben": "reschedule",
  "Woran soll {0} erinnert werden?": "What should {0} be reminded of?",
  "Anlegen": "Create",
  "Geheimnis löschen": "Delete secret",
  "{0} für {1} löschen?": "Delete {0} for {1}?",
  "Geheimnisse": "Secrets",
  "Tokens und Passwörter für diesen Agenten. Automatik-Läufe bekommen sie als Umgebungsvariablen — im Befehl $NAME schreiben statt des Werts. Werte sind hier nie wieder lesbar; in Antworten und Logs werden sie durch ***NAME*** ersetzt.":
    "Tokens and passwords for this agent. Automation runs receive them as environment variables — write $NAME in the command instead of the value. Values can never be read here again; in answers and logs they are replaced by ***NAME***.",
  "Nicht verfügbar: Verschlüsselungs-Paket fehlt im Image.": "Not available: encryption package missing in the image.",
  "Wert (wird ersetzt, falls der Name existiert)": "Value (replaces an existing one of the same name)",
  "Setzen": "Set",
  "Auch für interaktive Sitzungen: als ~/.agent-dashboard/env (nur für den Benutzer lesbar) auf der Maschine ablegen. In der Shell-Startdatei einbinden mit: . ~/.agent-dashboard/env":
    "Also for interactive sessions: store as ~/.agent-dashboard/env (readable by the user only) on the machine. Source it in the shell startup file with: . ~/.agent-dashboard/env",
  "Immer erlauben": "Always allow",
  "Nie erlauben": "Never allow",
  "Dieses Muster gilt künftig für {0} ohne Rückfrage als erlaubt. Widerrufbar unter Agenten → Freigaben.":
    "From now on this pattern is allowed for {0} without asking. Revocable under Agents → Approvals.",
  "Dieses Muster wird für {0} künftig sofort abgelehnt — ohne Anfrage. Widerrufbar unter Agenten → Freigaben.":
    "From now on this pattern is denied for {0} immediately — without a request. Revocable under Agents → Approvals.",
  "Muster": "Pattern",
  "Zugangsdaten im Muster?": "Credentials in the pattern?",
  "Das Muster enthält etwas, das wie ein Token aussieht. Es stünde dann im Klartext in der Freigabeliste — besser unter Agenten → Freigaben ein Geheimnis anlegen und im Befehl $NAME verwenden.":
    "The pattern contains something that looks like a token. It would sit in the approval list in plain text — better create a secret under Agents → Approvals and use $NAME in the command.",
  "Freigabe-Anfragen der Automatik ({0}) — der Lauf wartet": "Approval requests from automation ({0}) — the run is waiting",
  "noch {0} min": "{0} min left",
  "Der Befehl enthält etwas, das wie Zugangsdaten aussieht — nicht „immer erlauben“, sonst stehen sie in der Liste.":
    "The command contains something that looks like credentials — do not “always allow”, or they end up in the list.",
  "Erlauben": "Allow",
  "Ablehnen": "Deny",
  "Immer erlauben …": "Always allow …",
  "Nie erlauben …": "Never allow …",
  "Hinweis": "Note",
  "Freigaben — {0}": "Approvals — {0}",
  "lädt …": "loading …",
  "Freigaben über das Dashboard: braucht ein Automatik-Lauf eine Berechtigung, fragt er hier nach (Banner + Push), statt still abzulehnen.":
    "Approvals via the dashboard: when an automation run needs a permission it asks here (banner + push) instead of silently denying.",
  // FreigabenDialog.jsx — Zustellung in die Terminal-Sitzung (Issue #67)
  "Tasks in die laufende Terminal-Sitzung tippen: ist im Projekt-Terminal Claude Code offen, bekommt es den Task dort (du siehst live mit und kannst dazwischenschreiben). Ist die Sitzung belegt, wartet der Task; ohne offene Sitzung läuft er wie bisher im Hintergrund.":
    "Type tasks into the running terminal session: if Claude Code is open in the project terminal, it gets the task there (you watch live and can interject). If the session is busy, the task waits; without an open session it runs in the background as before.",
  "Wirkt beim nächsten Start der Automatik. Nur mit tmux (Linux/Mac); der Watcher trägt dafür Hooks in .claude/settings.local.json des Projekts ein.":
    "Takes effect on the next automation start. Only with tmux (Linux/Mac); the watcher adds hooks to the project's .claude/settings.local.json for this.",
  "Push, wenn ein Task in die Sitzung getippt wurde": "Push when a task was typed into the session",
  "Wirkt beim nächsten Start der Automatik. Voraussetzung: der Dashboard-MCP ist auf der Maschine registriert ({0}).":
    "Takes effect at the next automation start. Requires the dashboard MCP to be registered on the machine ({0}).",
  "Wartezeit auf deine Antwort (Minuten)": "Wait for your answer (minutes)",
  "Immer erlauben (ein Muster je Zeile, z. B. Bash(git status:*) oder Edit)":
    "Always allow (one pattern per line, e.g. Bash(git status:*) or Edit)",
  "Nie erlauben — wird sofort abgelehnt, ohne Anfrage (z. B. Bash(./deploy-prod.sh:*))":
    "Never allow — denied immediately, without a request (e.g. Bash(./deploy-prod.sh:*))",
  "Freigaben und Geheimnisse dieses Agenten": "Approvals and secrets of this agent",
  "{0} Datei(en) warten auf Abholung": "{0} file(s) waiting for pickup",
  "von {0}": "from {0}",
  "⛔ Tagesgrenze erreicht ({0} $ von {1} $) — Automatik wartet auf deine Antwort im Rückfragen-Banner":
    "⛔ Daily limit reached ({0} $ of {1} $) — automation waits for your answer in the questions banner",
  "Kosten heute {0} $ von {1} $": "Cost today {0} $ of {1} $",
  "Archiv nicht lesbar: {0}": "Archive not readable: {0}",
  "Archiv ausblenden": "Hide archive",
  "auch Archiv": "include archive",
  "Mailbox {0} · Archiv {1}": "Mailbox {0} · archive {1}",
  "Archiv ist leer": "Archive is empty",
  "Künftig immer wecken": "Always wake from now on",
  "Leerer Text wird nicht gespeichert — zum Entfernen die Rolle löschen.":
    "Empty text is not saved — delete the role to remove it.",
};
