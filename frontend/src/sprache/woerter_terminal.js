// Deutsch → Englisch. Teil des Wörterbuchs, zusammengelegt in ../sprache.js.
export default {
  // KeyBar.jsx — Tasten-Labels (nur die, die sich vom Englischen unterscheiden;
  // Esc/Tab/Alt/Shift/Pfeile/⏎/^C bleiben identisch und brauchen keinen Eintrag)
  "Strg": "Ctrl",
  "Entf": "Del",
  "Pos1": "Home",
  "Ende": "End",
  "Bild↑": "PgUp",
  "Bild↓": "PgDn",

  // KeyBar.jsx — Knopf-Titel
  "Kopier-Modus ein/aus: Terminal-Inhalt als frei markierbarer Text — funktioniert auch, wenn eine TUI (z.B. Claude Code) die Maus abfängt":
    "Copy mode on/off: terminal content as freely selectable text — also works when a TUI (e.g. Claude Code) captures the mouse",
  "Textzeile ein/aus: mit der Handy-Tastatur samt Wortvorschlägen schreiben und am Stück senden — Vorschläge direkt ins Terminal verdoppeln den Text":
    "Text line on/off: type with the phone keyboard including word suggestions and send it all at once — suggestions typed directly into the terminal duplicate the text",
  "Bildschirmtastatur ein- oder ausblenden": "Show or hide the on-screen keyboard",
  "Schrift kleiner": "Smaller font",
  "Schrift größer": "Larger font",

  // Terminal.jsx — Systemmeldungen im Terminal-Puffer
  "[Sitzung in anderem Fenster übernommen]": "[session taken over in another window]",
  "[getrennt — neuer Versuch in {0}s]": "[disconnected — retrying in {0}s]",

  // Terminal.jsx — Übernahme-Badge
  "Sitzung in einem anderen Fenster übernommen": "Session taken over in another window",
  "Wieder verbinden": "Reconnect",

  // Terminal.jsx — Maus-Erfassungs-Hinweis
  "App steuert die Maus — Markieren: Shift+Ziehen (Mac: ⌥) · Touch: ⎘":
    "App is controlling the mouse — select: Shift+drag (Mac: ⌥) · touch: ⎘",

  // Terminal.jsx — Kopier-Modus-Overlay
  "Kopier-Modus — Text frei markierbar": "Copy mode — text freely selectable",
  "voller Sitzungsverlauf (Server)": "full session history (server)",
  "TUI aktiv — nur Bildschirm + Verlauf davor; alles: „Voller Verlauf“":
    "TUI active — only screen + history before it; for everything: \"Full history\"",
  "Voller Verlauf": "Full history",
  "Aktualisieren": "Refresh",
  "✓ kopiert": "✓ copied",
  "Alles kopieren": "Copy all",
  "Schließen": "Close",

  // Terminal.jsx — Textzeile
  "Text hier tippen — Wortvorschläge funktionieren": "Type text here — word suggestions work",
  "Text nur einfügen (ohne Enter)": "Insert text only (no Enter)",
  "Text senden (mit Enter)": "Send text (with Enter)",

  // TerminalPanel.jsx
  "keine Verbindungen": "no connections",
  "Session beenden": "End session",
  "Session „{0}“ wirklich beenden?\nDie Shell auf dem Agenten-PC wird gekillt — Laufendes geht verloren.":
    "Really end session \"{0}\"?\nThe shell on the agent PC will be killed — unsaved work is lost.",
  "Beenden": "End",
  "Fehler": "Error",
  "Beenden fehlgeschlagen: {0}": "Ending failed: {0}",
  "Session läuft": "Session running",
  "Fenster schließen (Session läuft weiter)": "Close window (session keeps running)",
  "Weiteres Terminal auf dieser Verbindung öffnen": "Open another terminal on this connection",
  "Verbindungen verwalten / neue anlegen": "Manage connections / create new",
  "Verbindung wählen, um ein Terminal zu öffnen": "Select a connection to open a terminal",
};
