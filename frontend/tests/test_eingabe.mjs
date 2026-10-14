// Prüft den Textvergleich für die Bildschirmtastatur (src/termEingabe.js):
// was muss ans Terminal, wenn die Tastatur ihr Textfeld von `alt` auf `neu`
// ändert? Reines Node, keine Abhängigkeiten:  node tests/test_eingabe.mjs
//
// Das Abfangen der Ereignisse selbst prüft tests/test_terminal_browser.cjs.

import { textUnterschied } from "../src/termEingabe.js";

let fehler = 0;
const R = "\x7f";
const pruefe = (was, alt, neu, soll) => {
  const ist = textUnterschied(alt, neu);
  const ok = ist === soll;
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${ok ? "" : `  ${JSON.stringify(ist)} statt ${JSON.stringify(soll)}`}`);
};

console.log("Textvergleich:");
pruefe("Anhängen schickt nur das Neue", "hallo", "hallo w", " w");
pruefe("erstes Zeichen", "", "h", "h");
pruefe("Löschen schickt Rücktasten", "hallo", "hal", R + R);
pruefe("nichts geändert, nichts geschickt", "hallo", "hallo", "");
pruefe(
  "Vorschlag ersetzt das letzte Wort — der Anfang bleibt stehen",
  "das ist ein tse", "das ist ein Test ", R.repeat(3) + "Test ",
);
pruefe(
  "Autokorrektur gleicher Länge schickt NICHT die ganze Zeile",
  "das ist ein tset", "das ist ein Test", R.repeat(4) + "Test",
);
pruefe(
  "Großschreibung am Zeilenanfang nachgezogen",
  "hallo welt", "Hallo welt", R.repeat(10) + "Hallo welt",
);
pruefe("Emoji ist eine Rücktaste", "ok 👍", "ok ", R);
pruefe("Zeilenumbruch wird Enter", "ls", "ls\n", "\r");

console.log(fehler ? `\n${fehler} Prüfung(en) fehlgeschlagen` : "\nalles grün");
process.exit(fehler ? 1 : 0);
