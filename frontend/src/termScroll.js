// Wischen und Größenwechsel im Terminal (xterm) — die zwei Stellen, an denen
// xterm.js am Handy nicht das tut, was ein Finger erwartet.
//
// Beides steckt hier statt in Terminal.jsx, damit der Prüfstand genau diesen
// Code im Browser prüfen kann (frontend/tests/test_terminal_browser.cjs).

/** Zeilenhöhe in Pixeln, aus dem Scrollbereich gerechnet (keine interne API). */
function zeilenHoehe(term, el) {
  const zeilen = Math.max(1, term.buffer.active.length);
  const hoehe = el ? el.scrollHeight / zeilen : 0;
  return hoehe > 0 ? hoehe : 17;
}

/**
 * Wischen im Verlauf. Gibt eine Abräumfunktion zurück.
 *
 * Am Handy kam man mit dem Finger nicht in den Verlauf: Ein Wisch über 300 px
 * bewegte das Terminal um zwei Zeilen. Der Grund liegt tiefer, als er aussieht
 * — im Browser nachgemessen kommt bei einer Wischgeste genau EIN `touchmove`
 * am Terminal an. xterm zeichnet mit dem DOM-Renderer, beim Scrollen werden
 * die Zeilen-Elemente ersetzt; das berührte Element ist danach aus dem
 * Dokument entfernt. Weitere Berührungsereignisse gehen weiter an dieses (nun
 * lose) Ziel und erreichen das Terminal nie mehr. Die Geste bricht also nach
 * dem ersten Schritt ab — auch die von xterm selbst mitgebrachte.
 *
 * Gegenmittel ist `setPointerCapture` auf der Terminal-Wurzel: der Zeiger wird
 * festgehalten, alle weiteren Ereignisse landen dort, egal was unter dem
 * Finger gerade neu gezeichnet wurde. Der Fingerweg wird aufsummiert, in
 * Zeilen umgerechnet und über die öffentliche API gescrollt.
 *
 * `touch-action: none` auf dem Terminal ist dafür nötig: sonst deutet der
 * Browser die Geste selbst und bricht den Zeiger-Strom mit `pointercancel` ab.
 * Nativ zu scrollen gibt es hier ohnehin nichts — `.xterm-screen` liegt NEBEN
 * `.xterm-viewport`, nicht darin. Verloren geht damit nur Pinch-Zoom über dem
 * Terminal, das dort noch nie etwas taugte (xterm rechnet die Spalten nicht
 * neu; dafür gibt es A− / A+ in der Tastenleiste).
 */
export function wischScrollen(term) {
  const wurzel = term.element;
  if (!wurzel) return () => {};
  const vorherigeTouchAction = wurzel.style.touchAction;
  wurzel.style.touchAction = "none";
  const scrollflaeche = () => wurzel.querySelector(".xterm-viewport");

  let zeiger = null; // pointerId der laufenden Geste
  let letzteY = 0;
  let rest = 0; // angefangene Zeile, damit langsames Wischen nicht verhungert

  // Nur eingreifen, wo es echten Verlauf zu sehen gibt. Läuft eine TUI im
  // Alternativpuffer (Claude Code), gibt es keinen — und beansprucht sie die
  // Maus, gehören die Ereignisse ihr. Dann bleibt die Geste unangetastet
  // (für den Verlauf gibt es dort den Kopier-Modus ⎘).
  const zustaendig = () => {
    const puffer = term.buffer.active;
    return (
      puffer.type === "normal" &&
      puffer.baseY > 0 &&
      !wurzel.classList.contains("enable-mouse-events")
    );
  };

  const runter = (e) => {
    if (e.pointerType === "mouse" || !zustaendig()) return;
    zeiger = e.pointerId;
    letzteY = e.clientY;
    rest = 0;
    try {
      wurzel.setPointerCapture(e.pointerId);
    } catch {
      zeiger = null; // ohne Festhalten stirbt die Geste ohnehin — dann lieber gar nicht
    }
  };

  const bewegen = (e) => {
    if (zeiger !== e.pointerId) return;
    const weg = letzteY - e.clientY; // Finger nach oben = weiter nach unten im Verlauf
    letzteY = e.clientY;
    rest += weg / zeilenHoehe(term, scrollflaeche());
    const ganze = Math.trunc(rest);
    if (ganze) {
      rest -= ganze;
      term.scrollLines(ganze);
    }
  };

  const ende = (e) => {
    if (zeiger !== e.pointerId) return;
    zeiger = null;
    try {
      wurzel.releasePointerCapture(e.pointerId);
    } catch {
      /* schon losgelassen */
    }
  };

  // Während WIR die Geste führen, darf xterms eigene Touch-Behandlung nicht
  // zusätzlich am Scrollbereich ziehen (sie hängt an derselben Wurzel).
  const touchAbfangen = (e) => {
    if (zeiger === null) return;
    e.preventDefault();
    e.stopPropagation();
  };

  const fangen = { capture: true };
  wurzel.addEventListener("pointerdown", runter, fangen);
  wurzel.addEventListener("pointermove", bewegen, fangen);
  wurzel.addEventListener("pointerup", ende, fangen);
  wurzel.addEventListener("pointercancel", ende, fangen);
  wurzel.addEventListener("touchmove", touchAbfangen, { ...fangen, passive: false });
  return () => {
    wurzel.style.touchAction = vorherigeTouchAction;
    wurzel.removeEventListener("pointerdown", runter, fangen);
    wurzel.removeEventListener("pointermove", bewegen, fangen);
    wurzel.removeEventListener("pointerup", ende, fangen);
    wurzel.removeEventListener("pointercancel", ende, fangen);
    wurzel.removeEventListener("touchmove", touchAbfangen, fangen);
  };
}

/**
 * Neu einpassen, ohne die Stelle im Verlauf zu verlieren.
 *
 * Die Bildschirmtastatur auf- oder zuzuklappen ändert die Zeilenzahl, und
 * xterm setzt den Blick dabei ans Ende. Wer gerade zurückgeblättert hat,
 * verliert seine Stelle also bei jedem Tippen. Der Abstand zum Ende ist das,
 * was der Leser behalten will — der wird gemerkt und wiederhergestellt.
 */
export function fittenOhneSprung(term, fit) {
  const puffer = term.buffer.active;
  const abstand = puffer.baseY - puffer.viewportY;
  fit();
  if (abstand > 0) term.scrollLines(-abstand);
}
