// Wörterbuch-Konsistenz (Review-Testlücke sprache.js):
//   1. kein Schlüssel mit ZWEI verschiedenen Übersetzungen über die
//      woerter_*.js-Teile hinweg (der Objekt-Spread gewinnt still — genau so
//      hatte "Löschen fehlgeschlagen: {0}" zwei Fassungen),
//   2. Platzhalter-Parität: {0},{1},… im Schlüssel == in der Übersetzung
//      (ein fehlender Platzhalter zeigte dem Nutzer rohes "{0}").
// Die t()-Laufzeitlogik selbst hängt an localStorage/window und bleibt dem
// Browser-Prüfstand überlassen; hier zählt der Datenbestand.
//   cd frontend && node tests/test_woerter.mjs
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ordner = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "sprache");
const fehler = [];
const gesehen = new Map(); // key -> {datei, wert}

const PAAR = /"((?:[^"\\]|\\.)+)":\s*\n?\s*"((?:[^"\\]|\\.)+)"/g;
const PLATZHALTER = /\{\d+\}/g;

for (const datei of readdirSync(ordner).filter((f) => f.startsWith("woerter_")).sort()) {
  const text = readFileSync(join(ordner, datei), "utf-8");
  for (const m of text.matchAll(PAAR)) {
    const [_, schluessel, wert] = m;
    const alt = gesehen.get(schluessel);
    if (alt && alt.wert !== wert)
      fehler.push(
        `KONFLIKT ${JSON.stringify(schluessel)}: ${alt.datei}=${JSON.stringify(alt.wert)} vs ${datei}=${JSON.stringify(wert)}`,
      );
    gesehen.set(schluessel, { datei, wert });
    const links = new Set(schluessel.match(PLATZHALTER) || []);
    const rechts = new Set(wert.match(PLATZHALTER) || []);
    if (
      links.size !== rechts.size ||
      [...links].some((p) => !rechts.has(p))
    )
      fehler.push(
        `PLATZHALTER ${datei}: ${JSON.stringify(schluessel)} → ${JSON.stringify(wert)}`,
      );
  }
}

if (!gesehen.size) {
  console.error("keine Wörterbuch-Einträge gefunden — Parser kaputt?");
  process.exit(1);
}
if (fehler.length) {
  for (const f of fehler) console.error(f);
  process.exit(1);
}
console.log(`Wörterbuch konsistent: ${gesehen.size} Schlüssel, 0 Konflikte`);
