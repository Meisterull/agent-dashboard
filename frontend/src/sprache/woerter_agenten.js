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
};
