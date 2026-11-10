// Schrift des Terminals (08.11.2026).
//
// Ohne eigene Schrift nimmt xterm „courier-new, courier, monospace" — am
// Android-Handy landet das bei einer Ersatzschrift, der viele Zeichen von
// Claude Code fehlen (⏵⏵ vor „auto mode", ☐/☒ in den Rückfragen-Reitern,
// ✻ ◐ ⏺): leere Kästchen. Darum liefert das Dashboard zwei Schriften mit
// (frontend/public/fonts, Lizenzen daneben):
//   • DejaVu Sans Mono  — Hauptschrift, deckt Rahmen, Pfeile, ☐☒✔✻◐ ab
//   • Noto Sans Symbols 2 (Teilmenge „symbols") — nur, was DejaVu fehlt (⏵⏺ …),
//     per unicode-range erst geladen, wenn so ein Zeichen vorkommt.
// ⎿ und ⧉ hat keine der beiden; die übernimmt die Systemschrift.

export const SCHRIFT_FAMILIE = '"Terminal Mono", "Terminal Symbole", ui-monospace, monospace';

// Unter rund 50 Spalten kürzt Claude Code die Reiter einer Mehrfach-Rückfrage
// bis zur Unlesbarkeit („Tes…", 08.11.2026) und bricht jede Statuszeile um.
export const MIN_SPALTEN = 50;

/**
 * Lädt beide Schriften, bevor xterm misst: misst es mit der Ersatzschrift,
 * stimmen Zellbreite und Spaltenzahl nicht, sobald die echte Schrift da ist.
 * Liefert nie einen Fehler — ohne Schrift-API oder bei Netzproblemen geht es
 * eben mit der Systemschrift weiter.
 */
export async function schriftenLaden(groesse = 13) {
  const fonts = typeof document !== "undefined" ? document.fonts : null;
  if (!fonts?.load) return false;
  try {
    await Promise.all([
      fonts.load(`${groesse}px "Terminal Mono"`),
      fonts.load(`${groesse}px "Terminal Symbole"`, "⏵"),
    ]);
    return true;
  } catch {
    return false;
  }
}

/**
 * Kleinste nötige Verkleinerung: ausgehend von `start` so lange einen Pixel
 * kleiner, bis `spaltenBei(größe)` mindestens `min` Spalten ergibt oder
 * `untergrenze` erreicht ist. Rein rechnerisch (testbar ohne Browser).
 */
export function schriftFuerSpalten(start, spaltenBei, min = MIN_SPALTEN, untergrenze = 9) {
  let g = start;
  while (g > untergrenze && spaltenBei(g) < min) g -= 1;
  return g;
}

/**
 * Darf die Schrift von `alt` auf `neu` wachsen? Kleiner geht immer; größer
 * nur, solange danach noch `min` Spalten bleiben.
 */
export function vergroessernErlaubt(alt, neu, spaltenBei, min = MIN_SPALTEN) {
  if (neu <= alt) return true;
  return spaltenBei(neu) >= min;
}
