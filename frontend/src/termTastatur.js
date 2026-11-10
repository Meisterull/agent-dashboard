// Bildschirmtastatur ohne Größenänderung (08.11.2026).
//
// DAS PROBLEM: Jedes Auf- und Zuklappen der Tastatur änderte die Zeilenzahl
// des Terminals, die Brücke meldete das als SIGWINCH, und Claude Code zeichnet
// darauf seinen ganzen unteren Bereich neu (Eingabe, Dialoge, Rückfragen) —
// per Cursor-hoch, OHNE den Verlauf zu löschen (im Pseudo-Terminal
// nachgemessen: kein \e[2J/\e[3J). Ist der Bereich höher als der Bildschirm
// (bei offener Tastatur fast immer), bleibt die alte Fassung im Verlauf
// stehen und die neue kommt darunter: doppelte Texte.
//
// DIE LÖSUNG: Bei offener Tastatur behält das Terminal seine volle Höhe (die
// zuletzt ohne Tastatur gemessene). Es wird nur nach oben geschoben, so dass
// die Cursorzeile über der Tastatur sichtbar bleibt; der Rest liegt hinter dem
// oberen Rand. Zeilen/Spalten bleiben gleich → kein resize → kein Neuzeichnen.

/**
 * Wie weit (px) das eingefrorene Terminal nach oben geschoben wird, damit die
 * Cursorzeile im sichtbaren Bereich bleibt. Nie weiter, als das Terminal
 * höher ist als der sichtbare Bereich, nie nach unten.
 */
export function versatz({ volleHoehe, sichtbar, cursorUnten, rand = 6 }) {
  const maximal = Math.max(0, volleHoehe - sichtbar);
  return Math.min(maximal, Math.max(0, Math.ceil(cursorUnten + rand - sichtbar)));
}

/**
 * Hält die Terminal-Höhe fest, solange die Tastatur offen ist.
 *   el        — das Element, in dem xterm hängt (ref-div in Terminal.jsx)
 *   term      — die xterm-Instanz (für Cursorzeile/Zeilenhöhe)
 *   istOffen  — () => boolean, ob die Bildschirmtastatur gerade offen ist
 * `anwenden()` VOR jedem fit aufrufen: dann misst das FitAddon die gemerkte
 * Höhe und die Zeilenzahl bleibt. `nachfuehren()` nach Ausgabe/Cursorbewegung.
 */
export function tastaturRahmen(el, term, istOffen) {
  let gemerkt = 0;
  let aktiv = false;
  const huelle = () => el.parentElement;

  const loesen = () => {
    if (!aktiv) return;
    aktiv = false;
    Object.assign(el.style, { position: "", top: "", left: "", height: "", width: "" });
    huelle().style.overflow = "";
  };

  const zellHoehe = () => {
    const screen = term.element?.querySelector(".xterm-screen");
    const h = screen?.clientHeight || el.clientHeight;
    return term.rows ? h / term.rows : 0;
  };

  const nachfuehren = () => {
    if (!aktiv) return;
    const z = zellHoehe();
    const cursorUnten = (term.buffer.active.cursorY + 1) * z;
    const v = versatz({ volleHoehe: gemerkt, sichtbar: huelle().clientHeight, cursorUnten });
    el.style.top = `${-v}px`;
  };

  const anwenden = () => {
    const sichtbar = huelle()?.clientHeight || 0;
    if (!istOffen()) {
      loesen();
      if (sichtbar > 0) gemerkt = sichtbar;
      return;
    }
    // Tastatur offen, aber noch nie ohne gemessen (Seite mit offener Tastatur
    // geladen) oder der Platz ist gar nicht kleiner: normal weiter.
    if (!gemerkt || sichtbar >= gemerkt - 1) {
      loesen();
      return;
    }
    aktiv = true;
    huelle().style.overflow = "hidden";
    Object.assign(el.style, { position: "absolute", left: "0", width: "100%", height: `${gemerkt}px` });
    nachfuehren();
  };

  return { anwenden, nachfuehren, loesen, get aktiv() { return aktiv; } };
}
