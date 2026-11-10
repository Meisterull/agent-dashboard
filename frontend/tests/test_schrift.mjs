// Terminal-Schrift: Mindestspalten-Logik (rein rechnerisch, kein Browser).
//   cd frontend && node tests/test_schrift.mjs
import assert from "node:assert/strict";
import { schriftFuerSpalten, vergroessernErlaubt, MIN_SPALTEN, SCHRIFT_FAMILIE } from "../src/termSchrift.js";

// Handy hochkant: ~390 CSS-px nutzbar, Zellbreite ≈ 0,6 × Schriftgröße
const handy = (g) => Math.floor(390 / (g * 0.6));
// Desktop: viel Platz
const desktop = (g) => Math.floor(1400 / (g * 0.6));

let n = 0;
const fall = (name, f) => { f(); n++; console.log("ok ", name); };

fall("Mindestwert ist 50", () => assert.equal(MIN_SPALTEN, 50));
fall("Schriftliste beginnt mit der mitgelieferten Schrift", () => assert.match(SCHRIFT_FAMILIE, /^"Terminal Mono", "Terminal Symbole"/));

fall("16 px am Handy wird verkleinert, bis 50 Spalten passen", () => {
  const g = schriftFuerSpalten(16, handy);
  assert.ok(handy(g) >= 50, `bei ${g}px nur ${handy(g)} Spalten`);
  assert.ok(handy(g + 1) < 50, "nicht weiter verkleinert als nötig");
});
fall("passt es schon, bleibt die Größe", () => assert.equal(schriftFuerSpalten(12, handy), 12));
fall("Desktop bleibt unberührt", () => assert.equal(schriftFuerSpalten(20, desktop), 20));
fall("Untergrenze wird nie unterschritten", () => assert.equal(schriftFuerSpalten(16, () => 10, 50, 9), 9));

fall("Verkleinern ist immer erlaubt", () => assert.ok(vergroessernErlaubt(16, 15, () => 1)));
fall("Vergrößern über die Grenze ist gesperrt", () => assert.equal(vergroessernErlaubt(13, 14, handy), handy(14) >= 50));
fall("Vergrößern mit genug Platz geht", () => assert.ok(vergroessernErlaubt(13, 14, desktop)));

console.log(`\n${n} Fälle bestanden`);
