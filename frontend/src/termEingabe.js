// Bildschirmtastatur im Terminal (xterm): Wortvorschläge ohne Textverdopplung.
//
// DAS PROBLEM (10.10.2026): Tippt man am Handy direkt ins Terminal und nimmt
// einen Wortvorschlag an, stand danach immer wieder der KOMPLETTE bisher
// getippte Text ein zweites Mal in der Zeile. Der Grund steckt in xterm
// (CompositionHelper._handleAnyTextareaChanges): Android meldet fast jede
// Taste als keyCode 229, xterm vergleicht dann den Inhalt seines versteckten
// Textfelds vorher/nachher mit `neu.replace(alt, "")`. Hängt die Tastatur nur
// etwas an, bleibt genau das Neue übrig. ERSETZT sie aber etwas — Vorschlag
// angenommen, Autokorrektur, Großschreibung nachgezogen —, ist `alt` kein
// Teilstück von `neu` mehr, replace() findet nichts, und xterm schickt das
// ganze Textfeld los. Das Feld wird erst bei Enter geleert, enthält also die
// ganze bisherige Zeile.
//
// GEGENMITTEL: Auf Touch-Geräten nehmen wir xterm die Tastatur-Eingaben der
// Bildschirmtastatur ab (Abfangen an der Terminal-Wurzel, VOR xterms eigenen
// Listenern am Textfeld) und rechnen den Unterschied richtig: gemeinsamer
// Anfang bleibt stehen, der geänderte Rest wird mit Rücktasten gelöscht und
// neu getippt. Echte Tasten (Enter, Hardware-Tastatur) laufen weiter durch
// xterm. Der reine Vergleich (`textUnterschied`) ist ohne Browser prüfbar
// (tests/test_eingabe.mjs), das Abfangen prüft tests/test_terminal_browser.cjs.

const RUECK = "\x7f";

/**
 * Was muss ans Terminal, damit aus `alt` am Zeilenende `neu` wird?
 * Rücktasten für den geänderten Schluss, dann der neue Schluss.
 */
export function textUnterschied(alt, neu) {
  const a = Array.from(alt); // Codepunkte: ein Emoji ist EINE Rücktaste
  const n = Array.from(neu);
  let gleich = 0;
  while (gleich < a.length && gleich < n.length && a[gleich] === n[gleich]) gleich++;
  return RUECK.repeat(a.length - gleich) + n.slice(gleich).join("").replace(/\n/g, "\r");
}

const istTouch = () =>
  typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches;

// keyCode 229 = „die Eingabemethode kümmert sich" — so meldet Android fast
// alles, was von der Bildschirmtastatur kommt.
const vonEingabemethode = (e) =>
  e.keyCode === 229 || e.key === "Unidentified" || e.key === "Process";

/**
 * Eingaben der Bildschirmtastatur selbst übersetzen. Gibt `{ leeren,
 * abraeumen }` zurück: `leeren()` nach jeder Eingabe, die am Textfeld vorbei
 * ans Terminal ging (Tastenleiste) — sonst stimmt der nächste Vergleich nicht.
 */
export function imeEingabe(term, { aktiv = istTouch } = {}) {
  const wurzel = term.element;
  const feld = term.textarea;
  if (!wurzel || !feld) return { leeren() {}, abraeumen() {} };

  let alt = feld.value; // Stand des Textfelds vor der laufenden Eingabe
  let komponiert = false;

  const leeren = () => {
    alt = "";
    if (feld.value) feld.value = "";
  };

  const meins = (e) => e.target === feld && aktiv();

  const taste = (e) => {
    if (!meins(e)) return;
    if (vonEingabemethode(e)) {
      alt = feld.value;
      e.stopPropagation(); // xterm würde sonst selbst (falsch) vergleichen
      return;
    }
    // Echte Taste (Enter, Rücktaste am leeren Feld, Hardware-Tastatur): xterm
    // schickt sie selbst und lässt das Textfeld unberührt — danach passt
    // dessen Inhalt nicht mehr zur Zeile im Terminal. Neu aufsetzen.
    if (e.type === "keydown" && !["Shift", "Control", "Alt", "Meta"].includes(e.key))
      setTimeout(() => {
        if (!komponiert) leeren();
      }, 0);
  };

  const vorEingabe = (e) => {
    if (meins(e)) alt = feld.value;
  };

  const eingabe = (e) => {
    if (!meins(e)) return;
    e.stopPropagation();
    const neu = feld.value;
    // Einfügen aus der Zwischenablage hat xterm über das paste-Ereignis schon
    // losgeschickt — hier nur den Stand nachziehen, sonst käme es doppelt.
    if (e.inputType?.startsWith("insertFromPaste") || e.inputType === "insertFromDrop") {
      alt = neu;
      return;
    }
    const raus = textUnterschied(alt, neu);
    alt = neu;
    if (neu.includes("\n")) leeren(); // Enter als Text: neue Zeile, neuer Stand
    if (raus) term.input(raus, true);
  };

  const komposition = (e) => {
    if (!meins(e)) return;
    komponiert = e.type !== "compositionend";
    e.stopPropagation(); // xterm schickte den Text sonst am Ende noch einmal
  };

  const fangen = { capture: true };
  const arten = [
    ["keydown", taste],
    ["keyup", taste],
    ["beforeinput", vorEingabe],
    ["input", eingabe],
    ["compositionstart", komposition],
    ["compositionupdate", komposition],
    ["compositionend", komposition],
  ];
  for (const [art, fn] of arten) wurzel.addEventListener(art, fn, fangen);
  return {
    leeren,
    abraeumen() {
      for (const [art, fn] of arten) wurzel.removeEventListener(art, fn, fangen);
    },
  };
}
