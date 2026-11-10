// Langer Druck ins Terminal (08.11.2026). Android kann im Terminal nicht
// selbst markieren: touch-action: none (für das Wischen, termScroll.js) und
// die Maus-Übernahme durch Claude Code verhindern jede native Auswahl. Ein
// ruhig gehaltener Finger öffnet stattdessen ein Kopier-Menü an der Stelle.
//
// Bewegt sich der Finger mehr als `toleranz` px, ist es ein Wisch — dann
// passiert hier nichts und termScroll.js führt die Geste.

export function langDruck(wurzel, ausloesen, { dauer = 500, toleranz = 10 } = {}) {
  let zeiger = null;
  let startX = 0;
  let startY = 0;
  let uhr = null;

  const abbrechen = () => {
    clearTimeout(uhr);
    uhr = null;
    zeiger = null;
  };

  const runter = (e) => {
    if (e.pointerType === "mouse") return;
    abbrechen();
    zeiger = e.pointerId;
    startX = e.clientX;
    startY = e.clientY;
    uhr = setTimeout(() => {
      uhr = null;
      if (zeiger === null) return;
      ausloesen({ x: startX, y: startY });
      zeiger = null;
    }, dauer);
  };

  const bewegen = (e) => {
    if (zeiger !== e.pointerId) return;
    if (Math.hypot(e.clientX - startX, e.clientY - startY) > toleranz) abbrechen();
  };

  const hoch = (e) => {
    if (zeiger === e.pointerId) abbrechen();
  };

  // Das Kontextmenü des Browsers (lange gedrückt) gehört im Terminal uns.
  const kontext = (e) => {
    if (e.pointerType === "mouse") return;
    e.preventDefault();
  };

  const fangen = { capture: true };
  wurzel.addEventListener("pointerdown", runter, fangen);
  wurzel.addEventListener("pointermove", bewegen, fangen);
  wurzel.addEventListener("pointerup", hoch, fangen);
  wurzel.addEventListener("pointercancel", hoch, fangen);
  wurzel.addEventListener("contextmenu", kontext, fangen);
  return () => {
    abbrechen();
    wurzel.removeEventListener("pointerdown", runter, fangen);
    wurzel.removeEventListener("pointermove", bewegen, fangen);
    wurzel.removeEventListener("pointerup", hoch, fangen);
    wurzel.removeEventListener("pointercancel", hoch, fangen);
    wurzel.removeEventListener("contextmenu", kontext, fangen);
  };
}

/** Bildschirmpunkt → Pufferzeile (inkl. Verlauf) im xterm. */
export function pufferZeileBei(term, y) {
  const screen = term.element?.querySelector(".xterm-screen");
  if (!screen || !term.rows) return -1;
  const r = screen.getBoundingClientRect();
  const zeile = Math.floor(((y - r.top) / r.height) * term.rows);
  if (zeile < 0 || zeile >= term.rows) return -1;
  return term.buffer.active.viewportY + zeile;
}
