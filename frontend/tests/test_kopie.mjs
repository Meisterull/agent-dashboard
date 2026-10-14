// Prüft das Aufräumen kopierter Terminal-Auswahlen (src/termKopie.js):
// Leerzeichen am Zeilenende, Einrückung, von der TUI umbrochene Links und
// Befehle. Reines Node, keine Abhängigkeiten:  node tests/test_kopie.mjs

import { kopierSauber, zeilenEnden } from "../src/termKopie.js";

let fehler = 0;
const pruefe = (was, ist, soll) => {
  const ok = ist === soll;
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${ok ? "" : `  ${JSON.stringify(ist)} statt ${JSON.stringify(soll)}`}`);
};
const B = 40; // Terminalbreite der Beispiele

console.log("Leerzeichen:");
pruefe("aufgefüllte Zeile verliert ihr Ende", kopierSauber("ls -la" + " ".repeat(30), { spalten: B }), "ls -la");
pruefe("Einrückung vorn fällt weg", kopierSauber("  docker ps", { spalten: B }), "docker ps");
pruefe(
  "gemeinsame Einrückung weg, die innere bleibt",
  kopierSauber("  if x:\n      y()\n  z()", { spalten: B }),
  "if x:\n    y()\nz()",
);
pruefe("zeilenEnden lässt Einrückung und Umbrüche stehen", zeilenEnden("  a   \n  b  "), "  a\n  b");

console.log("Umbrochene Adressen:");
const teil1 = "  https://example.org/ein/sehr/langer/pf"; // genau 40 breit
pruefe("Testannahme: die Zeile reicht bis zum Rand", teil1.length, B);
pruefe(
  "Link über zwei Zeilen wird wieder EIN Link",
  kopierSauber(`${teil1}\n  ad/zur/datei.html`, { spalten: B }),
  "https://example.org/ein/sehr/langer/pfad/zur/datei.html",
);
pruefe(
  "Auswahl beginnt mitten in der Zeile (versatz)",
  kopierSauber(`${teil1.slice(2)}\n  ad/zur/datei.html`, { spalten: B, versatz: 2 }),
  "https://example.org/ein/sehr/langer/pfad/zur/datei.html",
);
pruefe(
  "Link über drei Zeilen",
  kopierSauber(`${teil1}\n  ${"a".repeat(38)}\n  ende`, { spalten: B }),
  `https://example.org/ein/sehr/langer/pf${"a".repeat(38)}ende`,
);

console.log("Umbrochene Befehle und Fließtext:");
pruefe(
  "am Wort umbrochener Befehl bekommt EIN Leerzeichen",
  kopierSauber("  docker compose -f compose.yml up\n  --build --detach", { spalten: B }),
  "docker compose -f compose.yml up --build --detach",
);
pruefe(
  "kurze Zeilen bleiben getrennt (echter Zeilenwechsel)",
  kopierSauber("  cd frontend\n  npm test", { spalten: B }),
  "cd frontend\nnpm test",
);
pruefe(
  "Aufzählungspunkte werden nie angehängt",
  kopierSauber("  - ein Punkt, der bis fast an den Rand\n  - noch ein Punkt", { spalten: B }),
  "- ein Punkt, der bis fast an den Rand\n- noch ein Punkt",
);
pruefe(
  "Leerzeile trennt immer",
  kopierSauber(`${teil1}\n\n  ad/zur/datei.html`, { spalten: B }),
  `${teil1.trim()}\n\nad/zur/datei.html`,
);
pruefe(
  "ohne bekannte Breite wird nichts zusammengesetzt",
  kopierSauber(`${teil1}\n  ad/zur/datei.html`),
  `${teil1.trim()}\nad/zur/datei.html`,
);
pruefe(
  "von xterm umbrochene Langzeile: nur ihr Rest zählt",
  kopierSauber(`${"x".repeat(45)}\nkurz`, { spalten: B }),
  `${"x".repeat(45)}\nkurz`,
);

console.log(fehler ? `\n${fehler} Prüfung(en) fehlgeschlagen` : "\nalles grün");
process.exit(fehler ? 1 : 0);
