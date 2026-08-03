// Tastenleiste unter dem Terminal, primär für Mobilgeräte ohne Sondertasten.
// Strg/Alt/Shift sind "sticky": antippen → gilt für die nächste Taste (auch
// eine von der Bildschirmtastatur getippte), danach automatisch wieder aus.
// So sind Kombis wie Shift+Tab oder Strg+C möglich.
//
// Wichtig: pointerdown wird abgefangen (preventDefault), damit die Buttons
// dem Terminal nicht den Fokus klauen — sonst klappt auf dem Handy die
// Bildschirmtastatur bei jedem Tastendruck zu.

const MODS = [
  ["ctrl", "Strg"],
  ["alt", "Alt"],
  ["shift", "Shift"],
];

const KEYS = [
  ["esc", "Esc"],
  ["tab", "Tab"],
  ["shifttab", "⇧Tab"],
  ["up", "↑"],
  ["down", "↓"],
  ["left", "←"],
  ["right", "→"],
  ["enter", "⏎"],
  ["ctrlc", "^C"],
  ["home", "Pos1"],
  ["end", "Ende"],
  ["pageup", "Bild↑"],
  ["pagedown", "Bild↓"],
  ["delete", "Entf"],
];

// Buttons mit fest eingebautem Modifikator (Direkt-Kombis)
const FIXED = {
  shifttab: { key: "tab", mods: { shift: true } },
  ctrlc: { key: "c", mods: { ctrl: true } },
};

export default function KeyBar({ mods, onToggleMod, onKey, onCopyMode }) {
  const noFocusSteal = (e) => e.preventDefault();

  return (
    <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-t border-slate-700 bg-slate-900 px-1 py-1">
      {onCopyMode && (
        <>
          <button
            onPointerDown={noFocusSteal}
            onClick={onCopyMode}
            title="Kopier-Modus: Terminal-Inhalt als frei markierbarer Text — funktioniert auch, wenn eine TUI (z.B. Claude Code) die Maus abfängt"
            className="shrink-0 rounded bg-slate-700 px-2.5 py-1.5 text-xs font-semibold text-sky-300"
          >
            ⎘
          </button>
          <span className="mx-1 h-5 w-px shrink-0 bg-slate-700" />
        </>
      )}
      {MODS.map(([name, label]) => (
        <button
          key={name}
          onPointerDown={noFocusSteal}
          onClick={() => onToggleMod(name)}
          className={`shrink-0 rounded px-2.5 py-1.5 text-xs font-semibold ${
            mods[name]
              ? "bg-sky-500 text-white"
              : "bg-slate-700 text-slate-200"
          }`}
        >
          {label}
        </button>
      ))}
      <span className="mx-1 h-5 w-px shrink-0 bg-slate-700" />
      {KEYS.map(([name, label]) => {
        const fixed = FIXED[name];
        return (
          <button
            key={name}
            onPointerDown={noFocusSteal}
            onClick={() =>
              fixed ? onKey(fixed.key, fixed.mods) : onKey(name)
            }
            className="shrink-0 rounded bg-slate-700 px-2.5 py-1.5 text-xs text-slate-200"
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
