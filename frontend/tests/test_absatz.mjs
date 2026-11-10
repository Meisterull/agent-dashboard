// Langer Druck: Zeile/Absatz unter dem Finger (rein rechnerisch, kein Browser).
//   cd frontend && node tests/test_absatz.mjs
import assert from "node:assert/strict";
import { logischeZeilen, textBei, indexBei } from "../src/termAbsatz.js";

// Nachbau des xterm-Puffers: [text, isWrapped]
const puffer = (zeilen) => ({
  length: zeilen.length,
  getLine: (i) => zeilen[i] && {
    isWrapped: zeilen[i][1],
    translateToString: (trim) => (trim ? zeilen[i][0].replace(/\s+$/, "") : zeilen[i][0]),
  },
});

const buf = puffer([
  ["> wie spät ist es", false],          // 0
  ["", false],                           // 1
  ["● Es ist 14 Uhr. Das hier ist eine ", false], // 2
  ["lange Zeile, die umbricht.", true],  // 3 (Fortsetzung von 2)
  ["  Zweite Zeile der Antwort.", false], // 4
  ["", false],                           // 5
  ["──────────────────", false],         // 6
  ["│ Rahmen links    │", false],        // 7
  ["│ zweite Zeile    │", false],        // 8
  ["──────────────────", false],         // 9
]);

let n = 0;
const fall = (name, f) => { f(); n++; console.log("ok ", name); };
const zeilen = logischeZeilen(buf);

fall("umbrochene Pufferzeilen werden eine logische Zeile", () => {
  assert.equal(zeilen[2].text, "● Es ist 14 Uhr. Das hier ist eine lange Zeile, die umbricht.");
  assert.deepEqual([zeilen[2].von, zeilen[2].bis], [2, 3]);
});
fall("Fortsetzungszeile findet ihre logische Zeile", () => assert.equal(indexBei(zeilen, 3), 2));
fall("Absatz = Block bis zur Leerzeile, ohne ●-Marke", () =>
  assert.equal(textBei(zeilen, 4).absatz, "Es ist 14 Uhr. Das hier ist eine lange Zeile, die umbricht.\n  Zweite Zeile der Antwort."));
fall("Zeile ohne Marke und ohne Einrückung", () =>
  assert.equal(textBei(zeilen, 3).zeile, "Es ist 14 Uhr. Das hier ist eine lange Zeile, die umbricht."));
fall("Eingabezeile ohne >-Marke", () => assert.equal(textBei(zeilen, 0).zeile, "wie spät ist es"));
fall("Leerzeile → nichts", () => assert.equal(textBei(zeilen, 1), null));
fall("Trennlinie zählt als leer", () => assert.equal(textBei(zeilen, 6), null));
fall("Rahmen │…│ wird abgezogen", () => assert.equal(textBei(zeilen, 7).absatz, "Rahmen links\nzweite Zeile"));
fall("außerhalb des Puffers → nichts", () => assert.equal(textBei(zeilen, 99), null));

console.log(`\n${n} Fälle bestanden`);
