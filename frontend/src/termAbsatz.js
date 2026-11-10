// Langer Druck ins Terminal (08.11.2026): welche Zeile, welcher Absatz liegt
// unter dem Finger? Rein rechnerisch auf dem xterm-Puffer, damit testbar.
//
// „Logische Zeile" = eine Pufferzeile samt der Folgezeilen, die xterm nur
// wegen der Breite umbrochen hat (isWrapped). „Absatz" = zusammenhängender
// Block logischer Zeilen bis zur nächsten Leerzeile — in Claude Code ist das
// eine Antwort, ein Aufzählungspunkt oder ein Befehl. Trennlinien aus
// Rahmenzeichen (─────) zählen als Leerzeile.

/** Puffer → logische Zeilen mit dem Bereich der Pufferzeilen, die sie belegen. */
export function logischeZeilen(buf) {
  const zeilen = [];
  for (let i = 0; i < buf.length; i++) {
    const line = buf.getLine(i);
    if (!line) continue;
    const weiter = i + 1 < buf.length && buf.getLine(i + 1)?.isWrapped;
    const s = line.translateToString(!weiter);
    const letzte = zeilen[zeilen.length - 1];
    if (line.isWrapped && letzte) {
      letzte.text += s;
      letzte.bis = i;
    } else {
      zeilen.push({ text: s, von: i, bis: i });
    }
  }
  return zeilen;
}

const LEER = /^[\s─━═│┃╭╮╰╯┌┐└┘├┤┬┴┼]*$/;
export const istLeer = (text) => LEER.test(text);

// Claude-Code-Markierungen am Anfang einer Antwort/eines Werkzeugergebnisses
// (● ⏺ ⎿ ❯ >) — gehören nicht zum kopierten Inhalt.
const MARKE = /^\s*(?:[●⏺⎿❯>]\s+)+/;
const RAHMEN_LINKS = /^\s*│\s?/;
const RAHMEN_RECHTS = /\s?│\s*$/;

function saeubern(zeilen) {
  const rahmen = zeilen.length > 0 && zeilen.every((z) => RAHMEN_LINKS.test(z));
  return zeilen
    .map((z) => (rahmen ? z.replace(RAHMEN_LINKS, "").replace(RAHMEN_RECHTS, "").replace(/\s+$/, "") : z))
    .map((z, i) => (i === 0 ? z.replace(MARKE, "") : z));
}

/** Index der logischen Zeile, die Pufferzeile `zeile` enthält (oder -1). */
export function indexBei(zeilen, zeile) {
  return zeilen.findIndex((z) => zeile >= z.von && zeile <= z.bis);
}

/**
 * Zeile und Absatz an Pufferzeile `zeile`.
 * Liefert { index, zeile, absatz, von, bis } — `von`/`bis` sind Indizes der
 * logischen Zeilen des Absatzes; null, wenn dort nichts steht.
 */
export function textBei(zeilen, zeile) {
  const index = indexBei(zeilen, zeile);
  if (index < 0 || istLeer(zeilen[index].text)) return null;
  let von = index;
  let bis = index;
  while (von > 0 && !istLeer(zeilen[von - 1].text)) von--;
  while (bis + 1 < zeilen.length && !istLeer(zeilen[bis + 1].text)) bis++;
  const absatz = saeubern(zeilen.slice(von, bis + 1).map((z) => z.text.replace(/\s+$/, "")));
  const eine = saeubern([zeilen[index].text.replace(/\s+$/, "")])[0];
  return { index, zeile: eine.trim(), absatz: absatz.join("\n").replace(/\s+$/, ""), von, bis };
}
