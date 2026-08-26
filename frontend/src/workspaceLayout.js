// Reine Layout-Mathematik des Fenster-Managers — ohne React, damit sie sich
// ohne Browser prüfen lässt (frontend/tests/test_layout.mjs).
//
// Kernzusage (Issue #24): `standardLayout` liefert für JEDE Menge von Panels
// eine überschneidungsfreie Anordnung. Vorher gab es feste Plätze nur für die
// vier Kern-Panels, und die belegten zusammen die gesamte Fläche — jedes
// weitere Fenster (etwa ein noVNC-Desktop aus den Einstellungen) landete
// zwangsläufig quer über der Mitte, und "Fenster anordnen" stellte genau das
// wieder her.

// Spaltenraster. Die Zahlen sind so gewählt, dass sich die gewohnten vier
// Kern-Panels exakt wie früher anordnen (links 18 %, Mitte 52,4 %, rechts
// 27,6 %, Ränder 0,4/0,8, Lücken 0,6/0,8).
export const SPALTEN = [
  { x: 0.4, w: 18 },
  { x: 19, w: 52.4 },
  { x: 72, w: 27.6 },
];
export const RAND_Y = 0.8;
export const LUECKE_Y = 0.8;

// Welches Kern-Panel in welche Spalte gehört. Alles, was hier nicht steht
// (externe Fenster), ist dynamisch und wird unten einsortiert.
export const KERN_SPALTE = { dateien: 0, chat: 1, terminal: 1, agenten: 2 };

// Höhenanteile innerhalb einer Spalte. Chat und Terminal tragen ihre alten
// Prozentwerte, damit die Anordnung ohne externe Fenster unverändert bleibt.
export const GEWICHT = { chat: 62, terminal: 35.6 };
export const GEWICHT_STANDARD = 62;

// Ab wann die mittlere Spalte zu einer Leiste aus Schlitzen würde: Weitere
// dynamische Fenster wandern dann unter das Agenten-Panel.
export const MAX_ZEILEN_MITTE = 4;

export const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
const rund = (v) => Math.round(v * 10) / 10;

export function standardLayout(ids) {
  const spalten = [[], [], []];
  const dynamisch = [];
  for (const id of ids) {
    const s = KERN_SPALTE[id];
    if (s === undefined) dynamisch.push(id);
    else spalten[s].push(id);
  }
  // Dynamische Fenster in die breite Mitte — dort will man einen noVNC-
  // Desktop haben. Wird sie zu voll, in die rechte Spalte.
  for (const id of dynamisch) {
    spalten[spalten[1].length < MAX_ZEILEN_MITTE ? 1 : 2].push(id);
  }

  const out = {};
  spalten.forEach((spalte, si) => {
    if (!spalte.length) return;
    const { x, w } = SPALTEN[si];
    const nutzbar = 100 - 2 * RAND_Y - LUECKE_Y * (spalte.length - 1);
    const summe = spalte.reduce((a, id) => a + (GEWICHT[id] ?? GEWICHT_STANDARD), 0);
    let y = RAND_Y;
    spalte.forEach((id) => {
      const h = (nutzbar * (GEWICHT[id] ?? GEWICHT_STANDARD)) / summe;
      out[id] = { x, y: rund(y), w, h: rund(h) };
      y += h + LUECKE_Y;
    });
  });
  return out;
}

// Platz eines einzelnen Panels in der Standardanordnung aller `ids`.
export function defaultRect(id, ids) {
  return standardLayout(ids.includes(id) ? ids : [...ids, id])[id];
}

export const gueltigesRect = (r) =>
  !!r && [r.x, r.y, r.w, r.h].every((n) => Number.isFinite(n));

export const normRect = (r) => ({
  x: clamp(r.x, 0, 98),
  y: clamp(r.y, 0, 98),
  w: clamp(r.w, 2, 100),
  h: clamp(r.h, 2, 100),
});
