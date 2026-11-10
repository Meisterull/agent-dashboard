// Fenster ein-/ausblenden (Issue #50): reine Logik, kein Browser.
//   cd frontend && node tests/test_fenstersichtbar.mjs
import assert from "node:assert/strict";
import {
  darfAusblenden,
  ersatzTab,
  sichtbareIds,
  umschalten,
  versteckteLesen,
  versteckteText,
} from "../src/fensterSichtbar.js";
import { standardLayout } from "../src/workspaceLayout.js";

const ALLE = ["dateien", "chat", "terminal", "agenten", "ext:VNC"];
let n = 0;
const fall = (name, f) => { f(); n++; console.log("ok ", name); };

fall("Lesen: kaputt/fremd → leer", () => {
  assert.equal(versteckteLesen(null).size, 0);
  assert.equal(versteckteLesen("{kaputt").size, 0);
  assert.equal(versteckteLesen('{"a":1}').size, 0);
  assert.deepEqual([...versteckteLesen('["chat", 3, "ext:VNC"]')], ["chat", "ext:VNC"]);
});
fall("Schreiben/Lesen hin und zurück", () => {
  const s = new Set(["terminal", "dateien"]);
  assert.deepEqual(versteckteLesen(versteckteText(s)), s);
});
fall("sichtbare Ids in Panel-Reihenfolge", () =>
  assert.deepEqual(sichtbareIds(ALLE, new Set(["chat", "ext:VNC"])), ["dateien", "terminal", "agenten"]));
fall("alles aus → alles sichtbar (nie leerer Workspace)", () =>
  assert.deepEqual(sichtbareIds(ALLE, new Set(ALLE)), ALLE));
fall("unbekannte Id (ext-Fenster noch nicht geladen) stört nicht", () =>
  assert.deepEqual(sichtbareIds(["chat"], new Set(["ext:Später"])), ["chat"]));
fall("umschalten blendet aus und wieder ein", () => {
  const a = umschalten(new Set(), "dateien", ALLE);
  assert.ok(a.has("dateien"));
  assert.ok(!umschalten(a, "dateien", ALLE).has("dateien"));
});
fall("letztes sichtbares Fenster bleibt", () => {
  const fast = new Set(["dateien", "terminal", "agenten", "ext:VNC"]);
  assert.equal(darfAusblenden("chat", ALLE, fast), false);
  assert.equal(umschalten(fast, "chat", ALLE), fast);
  assert.equal(darfAusblenden("dateien", ALLE, fast), true); // Einblenden geht immer
});
fall("Ersatz-Tab: bleibt, sonst Chat, sonst erstes", () => {
  assert.equal(ersatzTab("terminal", ["chat", "terminal"]), "terminal");
  assert.equal(ersatzTab("dateien", ["chat", "terminal"]), "chat");
  assert.equal(ersatzTab("chat", ["terminal", "agenten"]), "terminal");
});
fall("Layout der sichtbaren Fenster füllt die Breite (Zusage #24)", () => {
  const l = standardLayout(sichtbareIds(ALLE, new Set(["dateien", "agenten", "ext:VNC"])));
  assert.deepEqual(Object.keys(l).sort(), ["chat", "terminal"]);
  // Nur die Mitte belegt — die freigewordenen Spalten sind wirklich frei.
  assert.equal(l.chat.x, l.terminal.x);
});

console.log(`\n${n} Fälle bestanden`);
