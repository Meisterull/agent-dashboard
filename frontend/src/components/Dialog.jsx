import { useEffect, useRef, useState } from "react";
import Modal from "./Modal";

// Zentrale Nachfrage-Dialoge als Ersatz für window.confirm/prompt/alert:
// die blockieren den Tab, sehen auf dem Handy fremd aus und werden von
// manchen Browsern (installierte PWA) komplett unterdrückt — dann fielen
// „Session beenden", das Not-Aus oder das Schließen einer Rückfrage
// ersatzlos aus. Dasselbe Muster wie der FileDialog im Datei-Panel,
// hier einmal für alle Panels.
//
// Nutzung: await bestaetigen({…}) → true/false
//          await nachfragen({…})  → String ("" erlaubt bei allowEmpty) oder null (Abbruch)
//          await melden({…})      → nur zur Kenntnis, Promise fürs Warten
// Der DialogHost ist genau einmal in App.jsx gemountet — als LETZTES Kind,
// damit er über Settings/Editor (gleiches z-50) liegt. Solange er (noch)
// nicht da ist, fallen die Funktionen auf die window-Dialoge zurück:
// besser ein fremd aussehender Dialog als gar keiner.

let zeigeDialog = null; // vom DialogHost registriert

export function bestaetigen({ title, text, ok = "OK", danger = false }) {
  if (!zeigeDialog) return Promise.resolve(window.confirm(text));
  return zeigeDialog({ kind: "confirm", title, text, ok, danger });
}

export function nachfragen({
  title,
  text,
  label,
  initial = "",
  ok = "OK",
  danger = false,
  allowEmpty = false,
}) {
  if (!zeigeDialog)
    return Promise.resolve(window.prompt(text || label, initial));
  return zeigeDialog({ kind: "prompt", title, text, label, initial, ok, danger, allowEmpty });
}

export function melden({ title, text }) {
  if (!zeigeDialog) {
    window.alert(text);
    return Promise.resolve();
  }
  return zeigeDialog({ kind: "alert", title, text });
}

export function DialogHost() {
  // Warteschlange statt „letzter gewinnt": zwei gleichzeitige Anfragen
  // würden sonst ein nie aufgelöstes Promise hinterlassen — der Aufrufer
  // hinge dann für immer im await.
  const [schlange, setSchlange] = useState([]);
  const inputRef = useRef(null);

  useEffect(() => {
    zeigeDialog = (opts) =>
      new Promise((resolve) => setSchlange((q) => [...q, { ...opts, resolve }]));
    return () => {
      zeigeDialog = null;
    };
  }, []);

  const dlg = schlange[0];
  if (!dlg) return null;

  const fertig = (wert) => {
    dlg.resolve(wert);
    setSchlange((q) => q.slice(1));
  };
  // Abbruch (Backdrop, Escape, ✕): confirm → false, prompt → null
  const abbrechen = () =>
    fertig(dlg.kind === "confirm" ? false : dlg.kind === "prompt" ? null : undefined);

  const submit = (e) => {
    e.preventDefault();
    if (dlg.kind === "prompt") {
      const wert = (inputRef.current?.value || "").trim();
      if (!wert && !dlg.allowEmpty) return;
      fertig(wert);
    } else {
      fertig(dlg.kind === "confirm" ? true : undefined);
    }
  };

  return (
    <Modal title={dlg.title} onClose={abbrechen}>
      <form onSubmit={submit} className="space-y-3 text-sm">
        {dlg.text && (
          <p className="whitespace-pre-line text-slate-600 dark:text-slate-300">
            {dlg.text}
          </p>
        )}
        {dlg.kind === "prompt" && (
          <label className="block">
            {dlg.label && (
              <span className="mb-1 block text-slate-600 dark:text-slate-300">
                {dlg.label}
              </span>
            )}
            <input
              ref={inputRef}
              autoFocus
              defaultValue={dlg.initial || ""}
              className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-800"
            />
          </label>
        )}
        <div className="flex justify-end gap-2">
          {dlg.kind !== "alert" && (
            <button
              type="button"
              onClick={abbrechen}
              className="rounded border border-slate-300 px-3 py-1 text-slate-600 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              Abbrechen
            </button>
          )}
          <button
            type="submit"
            className={`rounded px-3 py-1 font-medium text-white ${
              dlg.danger ? "bg-red-600 hover:bg-red-700" : "bg-blue-600 hover:bg-blue-700"
            }`}
          >
            {dlg.ok || "OK"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
