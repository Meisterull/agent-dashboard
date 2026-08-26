// Prüft die Standardanordnung des Fenster-Managers (Issue #24).
// Reines Node, keine Abhängigkeiten:  node tests/test_layout.mjs
//
// Die zentrale Zusage ist die aus der Issue: „Wichtig wäre vor allem, dass
// workspace:reset einen überschneidungsfreien Zustand liefert — dafür ist der
// Knopf da." Genau das wird hier für jede Panel-Menge nachgerechnet, nicht nur
// für die vier bekannten.

import {
  MAX_ZEILEN_MITTE,
  SPALTEN,
  defaultRect,
  standardLayout,
} from "../src/workspaceLayout.js";

let fehler = 0;
const pruefe = (was, ok, zusatz = "") => {
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${zusatz ? `  ${zusatz}` : ""}`);
};

const KERN = ["dateien", "chat", "terminal", "agenten"];
const ext = (n) => Array.from({ length: n }, (_, i) => `ext:fenster${i + 1}`);

// Zwei Rechtecke überlappen, wenn sie sich in beiden Achsen überschneiden.
// Eine Winzigkeit Toleranz, weil die Höhen auf eine Nachkommastelle gerundet
// werden — Kanten dürfen sich berühren, aber nicht übereinanderliegen.
const EPS = 0.15;
function ueberlappt(a, b) {
  return (
    a.x + a.w > b.x + EPS &&
    b.x + b.w > a.x + EPS &&
    a.y + a.h > b.y + EPS &&
    b.y + b.h > a.y + EPS
  );
}

function paare(layout) {
  const eintraege = Object.entries(layout);
  const raus = [];
  for (let i = 0; i < eintraege.length; i++)
    for (let j = i + 1; j < eintraege.length; j++)
      if (ueberlappt(eintraege[i][1], eintraege[j][1]))
        raus.push(`${eintraege[i][0]}×${eintraege[j][0]}`);
  return raus;
}

console.log("Die vier Kern-Panels behalten ihre gewohnten Plätze:");
{
  const l = standardLayout(KERN);
  const soll = {
    dateien: { x: 0.4, y: 0.8, w: 18, h: 98.4 },
    chat: { x: 19, y: 0.8, w: 52.4, h: 62 },
    terminal: { x: 19, y: 63.6, w: 52.4, h: 35.6 },
    agenten: { x: 72, y: 0.8, w: 27.6, h: 98.4 },
  };
  for (const id of KERN)
    pruefe(id, JSON.stringify(l[id]) === JSON.stringify(soll[id]), JSON.stringify(l[id]));
}

console.log("\nÜberschneidungsfrei, egal wie viele externe Fenster:");
for (let n = 0; n <= 8; n++) {
  const ids = [...KERN, ...ext(n)];
  const l = standardLayout(ids);
  const kollisionen = paare(l);
  pruefe(
    `${n} externe Fenster`,
    kollisionen.length === 0 && Object.keys(l).length === ids.length,
    kollisionen.length ? kollisionen.join(", ") : "",
  );
}

console.log("\nAlles bleibt auf der Arbeitsfläche:");
for (let n = 0; n <= 8; n++) {
  const l = standardLayout([...KERN, ...ext(n)]);
  const daneben = Object.entries(l).filter(
    ([, r]) => r.x < 0 || r.y < 0 || r.x + r.w > 100.01 || r.y + r.h > 100.01,
  );
  pruefe(`${n} externe Fenster`, daneben.length === 0, daneben.map(([i]) => i).join(", "));
}

console.log("\nKein Fenster wird zum Schlitz:");
for (let n = 1; n <= 8; n++) {
  const l = standardLayout([...KERN, ...ext(n)]);
  const flach = Object.entries(l).filter(([, r]) => r.h < 12);
  pruefe(`${n} externe Fenster, mindestens 12 % hoch`, flach.length === 0,
    flach.map(([i, r]) => `${i}=${r.h}%`).join(", "));
}

console.log("\nEinsortierung dynamischer Fenster:");
{
  const l = standardLayout([...KERN, "ext:vnc"]);
  pruefe("erstes externes Fenster in die breite Mitte", l["ext:vnc"].x === SPALTEN[1].x,
    `x=${l["ext:vnc"].x}, w=${l["ext:vnc"].w}`);
  pruefe("es bekommt echte Fläche (mind. 30 % hoch)", l["ext:vnc"].h >= 30,
    `h=${l["ext:vnc"].h}%`);

  const viele = standardLayout([...KERN, ...ext(4)]);
  const inMitte = ext(4).filter((id) => viele[id].x === SPALTEN[1].x).length;
  pruefe(
    `Mitte nimmt höchstens ${MAX_ZEILEN_MITTE} Zeilen, der Rest geht nach rechts`,
    inMitte === MAX_ZEILEN_MITTE - 2 && ext(4).some((id) => viele[id].x === SPALTEN[2].x),
    `${inMitte} in der Mitte`,
  );
}

console.log("\nSonderfälle:");
{
  pruefe("leere Panel-Liste", Object.keys(standardLayout([])).length === 0);
  const nurExt = standardLayout(["ext:a"]);
  pruefe("nur ein externes Fenster", !!nurExt["ext:a"] && paare(nurExt).length === 0,
    JSON.stringify(nurExt["ext:a"]));
  const ohneChat = standardLayout(["dateien", "agenten"]);
  pruefe("fehlende Kern-Panels lassen ihre Spalte leer",
    paare(ohneChat).length === 0 && Object.keys(ohneChat).length === 2);
  const unbekannt = defaultRect("ext:neu", KERN);
  pruefe("defaultRect kennt auch eine noch nicht gelistete Id", !!unbekannt,
    JSON.stringify(unbekannt));
}

console.log(fehler ? `\n${fehler} Fehler` : "\nAlles grün");
process.exit(fehler ? 1 : 0);
