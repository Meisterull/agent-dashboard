// Tastatur ohne Größenänderung: Versatz-Rechnung (rein rechnerisch, kein Browser).
//   cd frontend && node tests/test_tastatur.mjs
import assert from "node:assert/strict";
import { versatz } from "../src/termTastatur.js";

let n = 0;
const fall = (name, f) => { f(); n++; console.log("ok ", name); };

// Terminal 700 px hoch, mit Tastatur bleiben 300 px sichtbar.
const basis = { volleHoehe: 700, sichtbar: 300, rand: 0 };

fall("Cursor ganz unten → um den verdeckten Teil nach oben", () =>
  assert.equal(versatz({ ...basis, cursorUnten: 700 }), 400));
fall("Cursor oben (frische Sitzung) → nicht verschieben", () =>
  assert.equal(versatz({ ...basis, cursorUnten: 120 }), 0));
fall("Cursor in der Mitte → gerade so weit, dass er unten sichtbar ist", () =>
  assert.equal(versatz({ ...basis, cursorUnten: 450 }), 150));
fall("nie weiter als der verdeckte Teil", () =>
  assert.equal(versatz({ ...basis, cursorUnten: 900 }), 400));
fall("Rand wird mitgerechnet", () =>
  assert.equal(versatz({ volleHoehe: 700, sichtbar: 300, cursorUnten: 450, rand: 6 }), 156));
fall("sichtbar ≥ volle Höhe → kein Versatz", () =>
  assert.equal(versatz({ volleHoehe: 300, sichtbar: 300, cursorUnten: 300 }), 0));

console.log(`\n${n} Fälle bestanden`);
