// Mehrere Terminal-Fenster nebeneinander — die reine Logik, ohne React, damit
// sie sich ohne Browser prüfen lässt (frontend/tests/test_terminalfenster.mjs).
//
// Das Terminal-Panel lässt sich „verdoppeln": Jedes weitere Fenster ist ein
// eigenes Panel im Fenster-Manager mit derselben Verbindungsleiste. Die offenen
// Terminals (Tabs) gehören ALLEN Fenstern gemeinsam — jeder Tab trägt die
// Nummer des Fensters, in dem er liegt. Getrennte Listen je Fenster wären die
// einfachere Bauweise gewesen, aber eine SSH-Session hängt serverseitig an
// genau EINEM Client (ssh_bridge: der zweite übernimmt mit Close-Code 4000):
// Zwei Fenster, die beim Laden beide „alle laufenden Sessions" öffnen,
// nähmen sie sich gegenseitig weg, und ⧉ vergäbe in beiden dieselbe Nummer.

// Fenster 1 heißt wie bisher "terminal" — gespeicherte Anordnungen und
// Ansichten bleiben gültig. Weitere: "terminal:2", "terminal:3", …
export const MAX_FENSTER = 4;
export const panelId = (n) => (n === 1 ? "terminal" : `terminal:${n}`);
export const istTerminalPanel = (id) => id === "terminal" || /^terminal:\d+$/.test(id);
export const nummerVon = (id) => (id === "terminal" ? 1 : Number(id.split(":")[1]));

export const DEFAULT_SID = "main";
export const keyOf = (t) => `${t.name}:${t.sid}`;

// Gespeicherte Fensterliste säubern: 1 ist immer dabei, keine Dopplungen,
// nur ganze Zahlen ≥ 2, höchstens MAX_FENSTER.
export function fensterListe(roh) {
  const extra = Array.isArray(roh)
    ? [...new Set(roh.filter((n) => Number.isInteger(n) && n >= 2))].sort((a, b) => a - b)
    : [];
  return [1, ...extra].slice(0, MAX_FENSTER);
}

// Kleinste freie Nummer für ein neues Fenster.
export function neueNummer(liste) {
  let n = 2;
  while (liste.includes(n)) n += 1;
  return n;
}

// Kleinste freie sid ("2", "3", …) für ein weiteres Terminal auf `name`.
// Auch serverseitig laufende zählen — sonst würde eine abgehängte Session
// ungewollt übernommen statt eine neue Shell zu starten.
export function freieSid(name, open, running) {
  const belegt = new Set([...open, ...running].filter((t) => t.name === name).map((t) => t.sid));
  let n = 2;
  while (belegt.has(String(n))) n += 1;
  return String(n);
}

// Klick auf eine Verbindung in Fenster `n`, in dem sie noch keinen Tab hat.
// Reihenfolge der Antworten:
//   1. laufende Sessions, die in KEINEM Fenster offen sind → hier anhängen
//   2. ist die Verbindung nur anderswo offen → hier ein WEITERES Terminal
//      (neue sid) — nie dem Nachbarfenster die Session wegnehmen
//   3. sonst ein frisches "main"
export function tabsFuerKlick(name, n, open, running) {
  const frei = running.filter(
    (r) => r.name === name && !open.some((o) => keyOf(o) === keyOf(r)),
  );
  if (frei.length) return frei.map((r) => ({ name, sid: r.sid, fenster: n }));
  if (open.some((o) => o.name === name))
    return [{ name, sid: freieSid(name, open, running), fenster: n }];
  return [{ name, sid: DEFAULT_SID, fenster: n }];
}

// Tabs, deren Fenster es nicht (mehr) gibt, wandern ins erste. Gibt dieselbe
// Liste zurück, wenn nichts zu tun war (React-Zustand bleibt dann unberührt).
export function heimholen(open, liste) {
  if (open.every((t) => liste.includes(t.fenster))) return open;
  return open.map((t) => (liste.includes(t.fenster) ? t : { ...t, fenster: 1 }));
}

// --- Platz im Fenster-Manager ------------------------------------------------
// Rechtecke in Prozent der Arbeitsfläche, wie in workspaceLayout.js.
export const TEIL_LUECKE = 0.6;
const rund = (v) => Math.round(v * 10) / 10;
const nah = (a, b) => Math.abs(a - b) < 0.35;

// Ein neues Terminal-Fenster `neuId` legt sich NEBEN die vorhandenen.
// `vorhandene` = [{id, rect}] der Terminal-Fenster, die schon einen Platz haben.
//   1. Liegen sie in einer Zeile (gleiche Oberkante und Höhe), wird die ganze
//      Zeile gleichmäßig neu aufgeteilt — drei Fenster sind dann Drittel und
//      nicht eine Hälfte und zwei Viertel.
//   2. Sonst wird das letzte in der Breite halbiert.
// Würde ein Fenster dabei schmaler als `minBreite` Prozent, gibt es null —
// dann nimmt der Fenster-Manager den Standardplatz.
export function nebeneinander(vorhandene, neuId, minBreite = 12) {
  if (!vorhandene.length) return null;
  const erster = vorhandene[0].rect;
  const inZeile = vorhandene.every(({ rect }) => nah(rect.y, erster.y) && nah(rect.h, erster.h));
  const reihe = inZeile
    ? [...vorhandene].sort((a, b) => a.rect.x - b.rect.x)
    : [vorhandene[vorhandene.length - 1]];
  const links = Math.min(...reihe.map(({ rect }) => rect.x));
  const rechts = Math.max(...reihe.map(({ rect }) => rect.x + rect.w));
  const anzahl = reihe.length + 1;
  const breite = (rechts - links - TEIL_LUECKE * (anzahl - 1)) / anzahl;
  if (breite < minBreite) return null;
  const { y, h } = reihe[0].rect;
  const out = {};
  [...reihe.map(({ id }) => id), neuId].forEach((id, i) => {
    out[id] = { x: rund(links + i * (breite + TEIL_LUECKE)), y, w: rund(breite), h };
  });
  return out;
}

// Ein Terminal-Fenster wird geschlossen: Der Nachbar in derselben Zeile, der
// direkt anliegt, bekommt dessen Breite dazu — sonst bliebe ein Loch, und das
// erste Fenster wäre nach „verdoppeln, wieder schließen" nur noch halb so
// breit. `andere` = [{id, rect}] der übrigen Terminal-Fenster. Gibt
// {id, rect} des verbreiterten Nachbarn zurück oder null.
export function platzZurueck(rect, andere) {
  const inZeile = andere.filter((a) => nah(a.rect.y, rect.y) && nah(a.rect.h, rect.h));
  const beruehrt = (a, b) => Math.abs(a - b) < TEIL_LUECKE + 0.5;
  const links = inZeile.find((a) => beruehrt(a.rect.x + a.rect.w, rect.x));
  if (links)
    return { id: links.id, rect: { ...links.rect, w: rund(rect.x + rect.w - links.rect.x) } };
  const rechts = inZeile.find((a) => beruehrt(rect.x + rect.w, a.rect.x));
  if (rechts)
    return {
      id: rechts.id,
      rect: { ...rechts.rect, x: rect.x, w: rund(rechts.rect.x + rechts.rect.w - rect.x) },
    };
  return null;
}
