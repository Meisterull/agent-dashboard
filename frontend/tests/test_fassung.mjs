// Prüft die Erkennung einer neuen Dashboard-Fassung (src/fassung.js): das
// Hauptskript aus index.html lesen und mit dem laufenden vergleichen.
// Reines Node, keine Abhängigkeiten:  node tests/test_fassung.mjs

import { skriptAus, istNeuer } from "../src/fassung.js";

let fehler = 0;
const pruefe = (was, ist, soll) => {
  const ok = ist === soll;
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${ok ? "" : `  ${JSON.stringify(ist)} statt ${JSON.stringify(soll)}`}`);
};

const seite = (skript) =>
  `<!doctype html><html><head><link rel="stylesheet" crossorigin href="/assets/index-Cd-mPNJN.css">` +
  `<script type="module" crossorigin src="${skript}"></script></head><body></body></html>`;

console.log("Hauptskript lesen:");
pruefe("gebaute index.html", skriptAus(seite("/assets/index-DGbg7Dk3.js")), "/assets/index-DGbg7Dk3.js");
pruefe("Hash mit Binde- und Unterstrich", skriptAus(seite("/assets/index-a_B-9xYz.js")), "/assets/index-a_B-9xYz.js");
pruefe("Dev-Seite (kein gebautes Skript)", skriptAus(seite("/src/main.jsx")), null);
pruefe("Fehlerseite / leer", skriptAus("<html>502 Bad Gateway</html>"), null);
pruefe("nichts", skriptAus(undefined), null);

console.log("Vergleich:");
pruefe("anderes Skript = neue Fassung", istNeuer("/assets/index-A.js", "/assets/index-B.js"), true);
pruefe("gleiches Skript", istNeuer("/assets/index-A.js", "/assets/index-A.js"), false);
pruefe("Server antwortet Unsinn → kein Alarm", istNeuer("/assets/index-A.js", null), false);
pruefe("Dev-Betrieb → kein Alarm", istNeuer(null, "/assets/index-B.js"), false);

console.log(fehler ? `\n${fehler} Prüfung(en) fehlgeschlagen` : "\nalles grün");
process.exit(fehler ? 1 : 0);
