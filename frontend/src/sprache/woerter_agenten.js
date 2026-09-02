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
  "Log ({0})": "Log ({0})",

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
  "Löschen fehlgeschlagen: {0}": "Deleting failed: {0}",

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
};
