// Deutsch → Englisch. Teil des Wörterbuchs, zusammengelegt in ../sprache.js.
// Dateien: FilesPanel.jsx, AustauschDialog.jsx, EditorModal.jsx, MediaModal.jsx, ExternalFrame.jsx.
export default {
  // gemeinsam mit anderen Teildateien (hier zur Sicherheit mitgeführt)
  "lädt…": "loading…",
  "gespeichert": "saved",
  "Speichern": "Save",
  "Löschen": "Delete",

  // FilesPanel.jsx
  "Abbrechen": "Cancel",
  "Anlegen": "Create",
  "Datei": "File",
  "Ordner": "Folder",
  "Ordner (samt Inhalt)": "Folder (including contents)",
  "Workspace": "Workspace",
  "eine Ebene hoch": "up one level",
  "Liste aktualisieren": "Refresh list",
  "neue Datei anlegen": "create new file",
  "neuen Ordner anlegen": "create new folder",
  "Dateien in dieses Verzeichnis hochladen": "Upload files into this directory",
  "leer": "empty",
  "herunterladen": "download",
  "umbenennen": "rename",
  "löschen": "delete",
  "Neue Datei": "New file",
  "Name der neuen Datei": "Name of the new file",
  "Neuer Ordner": "New folder",
  "Name des neuen Ordners": "Name of the new folder",
  "Umbenennen": "Rename",
  "Neuer Name für „{0}“": "New name for “{0}”",
  "{0} „{1}“ wirklich löschen?": "Really delete {0} “{1}”?",

  // EditorModal.jsx
  "speichert…": "saving…",
  "Ungespeicherte Änderungen": "Unsaved changes",
  "„{0}“ hat ungespeicherte Änderungen. Trotzdem schließen?":
    "“{0}” has unsaved changes. Close anyway?",
  "Schließen": "Close",
  "read-only (Datei gekürzt geladen)": "read-only (file loaded truncated)",
  "Datei-Kodierung — wird beim Speichern beibehalten":
    "File encoding — kept as-is when saving",
  "stattdessen herunterladen": "download instead",

  // MediaModal.jsx
  "in eigenem Tab öffnen": "open in its own tab",
  "schließen": "close",
  "Dieses Gerät zeigt PDFs nicht eingebettet.": "This device does not display PDFs inline.",
  "In eigenem Tab öffnen": "Open in its own tab",
  "Von einem entfernten Rechner: spielt von vorn, Spulen ist nicht möglich.":
    "From a remote machine: plays from the start, seeking isn't possible.",

  // ExternalFrame.jsx
  "Ungültige Adresse „{0}“ — erwartet „IP:Port[/pfad]“ (LAN) oder eine volle https://-URL.":
    "Invalid address “{0}” — expected “IP:Port[/path]” (LAN) or a full https:// URL.",

  // AustauschDialog.jsx + die zwei Knöpfe im FilesPanel
  "Austausch-Ordner · {0}": "Exchange folder · {0}",
  "Austausch-Ordner (eingeschaltet)": "Exchange folder (enabled)",
  "Austausch-Ordner einrichten": "Set up exchange folder",
  "an eine andere Maschine senden": "send to another machine",
  "Diese Maschine hat keine SSH-Verbindung — der Dateiaustausch geht nur zwischen SSH-Maschinen.":
    "This machine has no SSH connection — file exchange only works between SSH machines.",
  "Andere Maschinen können Dateien in diesen Ordner legen — je Absender in einen Unterordner „von-<Absender>“. Vorhandenes wird nie überschrieben.":
    "Other machines can drop files into this folder — each sender gets its own subfolder “von-<sender>”. Existing files are never overwritten.",
  "Eingeschaltet:": "Enabled:",
  "Ordner (relativ zum Home oder absolut)": "Folder (relative to home, or absolute)",
  "Ausschalten löscht nichts auf der Maschine.": "Turning it off deletes nothing on the machine.",
  "Ausschalten": "Turn off",
  "Einschalten": "Turn on",
  "Pfad ändern": "Change path",
  "Ordner öffnen": "Open folder",
  "Schließen": "Close",
  "An Maschine senden": "Send to machine",
  "Zugestellt an {0}:": "Delivered to {0}:",
  "Die Maschine hat eine Nachricht mit dem Pfad bekommen.": "The machine received a message with the path.",
  "Fertig": "Done",
  "Noch keine andere Maschine hat einen Austausch-Ordner. Einschalten: auf dem Tab der Zielmaschine 📥 antippen.":
    "No other machine has an exchange folder yet. To enable one, tap 📥 on the target machine's tab.",
  "„{0}“ von {1} in den Austausch-Ordner einer anderen Maschine legen.":
    "Drop “{0}” from {1} into another machine's exchange folder.",
  "Empfänger": "Recipient",
  "Begleittext (optional)": "Note (optional)",
  "Die Datei ist zu groß ({0}) — erlaubt sind {1} MB.": "The file is too large ({0}) — the limit is {1} MB.",
  "Kopiert wird direkt von Maschine zu Maschine. Der Empfänger bekommt eine Nachricht mit dem Pfad.":
    "Files are copied directly from machine to machine. The recipient gets a message with the path.",
  "überträgt…": "transferring…",
  "Senden": "Send",
};
