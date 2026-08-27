// Bildschirmtastatur und sichtbare Fensterhöhe (Issue #27).
//
// DAS PROBLEM: Seit Chrome 108 verkleinert Android bei geöffneter Tastatur nur
// noch den *visuellen* Viewport, nicht mehr den Layout-Viewport. `100dvh`
// bleibt damit die volle Bildschirmhöhe, die Tastatur legt sich über die
// Seite, und alles, was per Flex-Layout unten sitzt — KeyBar, Textzeile,
// Tab-Leiste — verschwindet dahinter. iOS Safari verkleinert den
// Layout-Viewport ohnehin nie.
//
// Die Gegenmaßnahmen sind zweigeteilt:
//   1. `interactive-widget=resizes-content` im Meta-Tag (index.html) bringt
//      Android zurück zum alten Verhalten. Deckt Android ab, iOS nicht.
//   2. Diese Datei schreibt die tatsächlich sichtbare Höhe fortlaufend in die
//      CSS-Variable `--app-h`. Das Layout hängt daran statt an `h-dvh` und
//      stimmt damit auf beiden Systemen.
//
// Zusätzlich löst sie den Ping-Pong-Effekt: Drückt der Nutzer die Tastatur
// weg, bleibt das Eingabefeld fokussiert — der nächste Tap auf eine
// Sondertaste holt sie deshalb sofort wieder hoch. Sobald die Tastatur
// verschwindet, nehmen wir dem Feld daher den Fokus. Die KeyBar-Tasten
// brauchen ihn nicht: Sie schicken direkt über den WebSocket.

const SCHWELLE = 120; // px Höhenverlust, ab dem wir von "Tastatur" ausgehen

let hoechste = 0;
let offen = false;

export function tastaturOffen() {
  return offen;
}

/** Nimmt dem fokussierten Eingabefeld den Fokus (schließt die Tastatur). */
export function tastaturSchliessen() {
  const el = document.activeElement;
  if (el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT")) el.blur();
}

export function initViewport() {
  const vv = window.visualViewport;
  const wurzel = document.documentElement;

  const messen = () => {
    const hoehe = Math.round(vv ? vv.height : window.innerHeight);
    if (!hoehe) return;
    wurzel.style.setProperty("--app-h", `${hoehe}px`);

    // Referenz ist die größte je gesehene Höhe. `window.innerHeight` taugt
    // dafür nicht: Mit `resizes-content` schrumpft der Layout-Viewport mit,
    // die Tastatur wäre dann nie erkennbar.
    hoechste = Math.max(hoechste, hoehe);
    const jetztOffen = hoehe < hoechste - SCHWELLE;

    if (offen && !jetztOffen) {
      // Tastatur ist gerade verschwunden — Fokus abgeben, sonst klappt sie
      // beim nächsten Tap von selbst wieder auf.
      tastaturSchliessen();
    }
    if (jetztOffen !== offen) {
      offen = jetztOffen;
      window.dispatchEvent(
        new CustomEvent("tastatur", { detail: { offen } }),
      );
    }
  };

  messen();
  if (vv) {
    vv.addEventListener("resize", messen);
    // Scrollen des visuellen Viewports (iOS schiebt die Seite hoch, statt sie
    // zu verkleinern) ändert ebenfalls, was sichtbar ist.
    vv.addEventListener("scroll", messen);
  }
  window.addEventListener("resize", messen);
  window.addEventListener("orientationchange", () => {
    hoechste = 0; // im Querformat gilt eine andere Bildschirmhöhe
    setTimeout(messen, 250);
  });
}
