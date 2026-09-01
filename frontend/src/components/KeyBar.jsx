// Tastenleiste unter dem Terminal, primär für Mobilgeräte ohne Sondertasten.
// Strg/Alt/Shift sind "sticky": antippen → gilt für die nächste Taste (auch
// eine von der Bildschirmtastatur getippte), danach automatisch wieder aus.
// So sind Kombis wie Shift+Tab oder Strg+C möglich.
//
// Wichtig: pointerdown wird abgefangen (preventDefault), damit die Buttons
// dem Terminal nicht den Fokus klauen — sonst klappt auf dem Handy die
// Bildschirmtastatur bei jedem Tastendruck zu.

import { t } from "../sprache";

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

export default function KeyBar({
  mods,
  onToggleMod,
  onKey,
  onCopyMode,
  copyActive = false,
  onTextMode,
  textActive = false,
  onSchrift,
  onTastatur,
}) {
  const noFocusSteal = (e) => e.preventDefault();

  return (
    <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-t border-slate-700 bg-slate-900 px-1 py-1">
      {onCopyMode && (
        <>
          <button
            onPointerDown={noFocusSteal}
            onClick={onCopyMode}
            title={t(
              "Kopier-Modus ein/aus: Terminal-Inhalt als frei markierbarer Text — funktioniert auch, wenn eine TUI (z.B. Claude Code) die Maus abfängt",
            )}
            // Aktiv-Zustand wie die Sticky-Modifikatoren: sichtbar, dass ein
            // Modus läuft — und derselbe Knopf schaltet ihn wieder aus.
            className={`shrink-0 rounded px-2.5 py-1.5 text-xs font-semibold ${
              copyActive ? "bg-sky-500 text-white" : "bg-slate-700 text-sky-300"
            }`}
          >
            ⎘
          </button>
          <span className="mx-1 h-5 w-px shrink-0 bg-slate-700" />
        </>
      )}
      {/* Aa: Textzeile überm Terminal (Terminal.jsx) — Androids Wort-
          vorschläge verdoppeln direkt im xterm getippten Text (IME-
          Komposition), in einem echten Input funktionieren sie sauber. */}
      {onTextMode && (
        <>
          <button
            onPointerDown={noFocusSteal}
            onClick={onTextMode}
            title={t(
              "Textzeile ein/aus: mit der Handy-Tastatur samt Wortvorschlägen schreiben und am Stück senden — Vorschläge direkt ins Terminal verdoppeln den Text",
            )}
            className={`shrink-0 rounded px-2.5 py-1.5 text-xs font-semibold ${
              textActive ? "bg-sky-500 text-white" : "bg-slate-700 text-sky-300"
            }`}
          >
            Aa
          </button>
          <span className="mx-1 h-5 w-px shrink-0 bg-slate-700" />
        </>
      )}
      {/* ⌨ holt die Bildschirmtastatur gezielt oder schickt sie weg. Ohne
          diesen Knopf bleibt nur die Systemgeste — und danach weiß niemand,
          ob das Terminal noch fokussiert ist (Issue #27). */}
      {onTastatur && (
        <>
          <button
            onPointerDown={noFocusSteal}
            onClick={onTastatur}
            title={t("Bildschirmtastatur ein- oder ausblenden")}
            className="shrink-0 rounded bg-slate-700 px-2.5 py-1.5 text-xs text-sky-300"
          >
            ⌨
          </button>
          <span className="mx-1 h-5 w-px shrink-0 bg-slate-700" />
        </>
      )}
      {/* A− / A+ : Schriftgröße des Terminals, gemerkt pro Gerät (Issue #31).
          Hochkant am Handy sind 13 px zu groß, am 4K-Desktop zu klein. */}
      {onSchrift && (
        <>
          <button
            onPointerDown={noFocusSteal}
            onClick={() => onSchrift(-1)}
            title={t("Schrift kleiner")}
            className="shrink-0 rounded bg-slate-700 px-2.5 py-1.5 text-xs text-slate-200"
          >
            A−
          </button>
          <button
            onPointerDown={noFocusSteal}
            onClick={() => onSchrift(1)}
            title={t("Schrift größer")}
            className="shrink-0 rounded bg-slate-700 px-2.5 py-1.5 text-xs font-semibold text-slate-200"
          >
            A+
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
          {t(label)}
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
            {t(label)}
          </button>
        );
      })}
    </div>
  );
}
