import { useState } from "react";
import Modal from "./Modal";
import { bestaetigen } from "./Dialog";

// Gespeicherte Fensteranordnungen. "Fenster anordnen" stellt den Standard her,
// hier legt man sich eigene Anordnungen für wiederkehrende Arbeitsweisen ab —
// etwa "Chat groß" zum Planen und "VNC groß" zum Zuschauen.
//
// Die Ansichten liegen im localStorage, wie die laufende Anordnung auch: Sie
// gehören zu dem Bildschirm, vor dem man sitzt. Weil alle Maße in Prozent der
// Arbeitsfläche stehen, überlebt eine Ansicht aber jede Fenstergröße.

const zeitpunkt = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
};

export default function WorkspaceViews({
  views,
  aktiv,
  onSpeichern,
  onLaden,
  onLoeschen,
  onClose,
}) {
  const [name, setName] = useState("");
  const namen = Object.keys(views).sort((a, b) => a.localeCompare(b, "de"));
  const sauber = name.trim();
  const ueberschreibt = sauber && Object.hasOwn(views, sauber);

  const speichern = () => {
    if (!sauber) return;
    onSpeichern(sauber);
    setName("");
  };

  // Rückfrage, weil das ✕ direkt neben „Laden“ sitzt: ein Fehlgriff wäre die
  // Ansicht sonst unwiederbringlich los (localStorage, kein Backup).
  const loeschen = async (n) => {
    if (
      !(await bestaetigen({
        title: "Ansicht löschen",
        text: `Ansicht „${n}“ endgültig löschen?`,
        ok: "Löschen",
        danger: true,
      }))
    )
      return;
    onLoeschen(n);
  };

  return (
    <Modal title="Ansichten" onClose={onClose}>
      <div className="flex gap-2">
        <input
          value={name}
          autoFocus
          maxLength={40}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && speichern()}
          placeholder="Name der Ansicht, z. B. „VNC groß“"
          className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-800"
        />
        <button
          onClick={speichern}
          disabled={!sauber}
          className="shrink-0 rounded bg-slate-800 px-3 py-1.5 text-sm text-white disabled:opacity-40 dark:bg-slate-700"
        >
          {ueberschreibt ? "Überschreiben" : "Speichern"}
        </button>
      </div>
      <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
        {ueberschreibt
          ? `„${sauber}“ gibt es schon und wird ersetzt.`
          : "Sichert die aktuelle Anordnung samt Reihenfolge der Fenster."}
      </p>

      {namen.length === 0 ? (
        <p className="mt-5 text-sm text-slate-500 dark:text-slate-400">
          Noch keine Ansicht gespeichert. Schieb die Fenster zurecht und gib der
          Anordnung oben einen Namen.
        </p>
      ) : (
        <ul className="mt-5 flex flex-col gap-1.5">
          {namen.map((n) => (
            <li
              key={n}
              className="flex items-center gap-2 rounded border border-slate-200 px-2.5 py-1.5 dark:border-slate-700"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm">
                  {n}
                  {n === aktiv && (
                    <span className="ml-2 text-xs text-slate-400">· zuletzt geladen</span>
                  )}
                </div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  {Object.keys(views[n]?.layout || {}).length} Fenster
                  {zeitpunkt(views[n]?.gespeichert) && ` · ${zeitpunkt(views[n].gespeichert)}`}
                </div>
              </div>
              <button
                onClick={() => onLaden(n)}
                className="shrink-0 rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-800"
              >
                Laden
              </button>
              <button
                onClick={() => loeschen(n)}
                title={`„${n}“ löschen`}
                className="shrink-0 rounded px-1.5 py-1 text-xs text-slate-400 hover:text-red-600"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
