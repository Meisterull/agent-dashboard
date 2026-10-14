// Prüft die Logik der verdoppelten Terminal-Fenster (terminalFenster.js) und
// ihre Standardanordnung. Reines Node:  node tests/test_terminalfenster.mjs
//
// Die Zusage, um die es geht: Ein zweites Terminal-Fenster nimmt dem ersten
// nie eine Session weg (serverseitig hängt jede an genau einem Client), und
// "Fenster anordnen" legt die Terminal-Fenster nebeneinander, ohne dass sich
// irgendetwas überschneidet.

import {
  MAX_FENSTER, fensterListe, freieSid, heimholen, istProjektSid, istTerminalPanel, nebeneinander,
  neueNummer, nummerVon, panelId, platzZurueck, projektSid, projektVon, tabLabel, tabsFuerKlick,
  tabsFuerProjekt,
} from "../src/terminalFenster.js";
import { standardLayout } from "../src/workspaceLayout.js";

let fehler = 0;
const pruefe = (was, ok, zusatz = "") => {
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${zusatz ? `  ${zusatz}` : ""}`);
};
const gleich = (a, b) => JSON.stringify(a) === JSON.stringify(b);

console.log("Namen und Nummern:");
pruefe("Fenster 1 heißt weiter 'terminal'", panelId(1) === "terminal" && panelId(3) === "terminal:3");
pruefe("nummerVon kehrt panelId um", nummerVon("terminal") === 1 && nummerVon("terminal:4") === 4);
pruefe("istTerminalPanel erkennt nur Terminal-Fenster",
  istTerminalPanel("terminal") && istTerminalPanel("terminal:2") && !istTerminalPanel("ext:terminal:2") && !istTerminalPanel("chat"));
pruefe("gespeicherter Unsinn ergibt [1]", gleich(fensterListe("kaputt"), [1]) && gleich(fensterListe(null), [1]));
pruefe("Liste wird gesäubert und gedeckelt",
  gleich(fensterListe([3, 2, 2, 1, 0, "x", 2.5, 7, 9]), [1, 2, 3, 7]) && fensterListe([2, 3, 4, 5, 6]).length === MAX_FENSTER);
pruefe("neue Nummer füllt Lücken", neueNummer([1]) === 2 && neueNummer([1, 3]) === 2 && neueNummer([1, 2, 3]) === 4);

console.log("Klick auf eine Verbindung:");
{
  const laeuft = [{ name: "server", sid: "main" }, { name: "server", sid: "2" }];
  let t = tabsFuerKlick("server", 1, [], laeuft);
  pruefe("laufende, nirgends offene Sessions werden angehängt", gleich(t.map((x) => x.sid), ["main", "2"]) && t.every((x) => x.fenster === 1));
  const offen = t;
  t = tabsFuerKlick("server", 2, offen, laeuft);
  pruefe("ist alles schon im Nachbarfenster offen, gibt es ein NEUES Terminal", gleich(t, [{ name: "server", sid: "3", fenster: 2 }]), JSON.stringify(t));
  t = tabsFuerKlick("server", 2, [offen[0]], laeuft);
  pruefe("eine laufende, noch nicht offene Session kommt ins klickende Fenster", gleich(t, [{ name: "server", sid: "2", fenster: 2 }]), JSON.stringify(t));
  t = tabsFuerKlick("werkstatt", 2, offen, laeuft);
  pruefe("unberührte Verbindung startet mit 'main'", gleich(t, [{ name: "werkstatt", sid: "main", fenster: 2 }]));
  /* Tab offen, aber serverseitig noch nicht gemeldet (frisch geklickt): */
  t = tabsFuerKlick("werkstatt", 1, [{ name: "werkstatt", sid: "main", fenster: 2 }], []);
  pruefe("auch ein eben erst geöffneter Tab wird nicht doppelt belegt", t[0].sid === "2", JSON.stringify(t));
  pruefe("freieSid zählt offene UND laufende", freieSid("server", [{ name: "server", sid: "3", fenster: 2 }], laeuft) === "4");
}

console.log("Projekt-Terminals:");
{
  pruefe("projektSid/istProjektSid/projektVon", projektSid("zeitalter") === "p:zeitalter" && istProjektSid("p:zeitalter") && !istProjektSid("p:") && !istProjektSid("2") && !istProjektSid("main") && projektVon("p:bot") === "bot" && projektVon("2") === null);
  pruefe("tabLabel", tabLabel("w", "main") === "w" && tabLabel("w", "2") === "w ·2" && tabLabel("w", "p:zeitalter") === "w · zeitalter");
  const laeuft = [{ name: "w", sid: "p:zeitalter" }];
  pruefe("Projekt-Session zählt nicht für Nummern", freieSid("w", [], laeuft) === "2");
  let o = tabsFuerProjekt("w", "zeitalter", 1, []);
  pruefe("neuer Projekt-Tab", gleich(o, [{ name: "w", sid: "p:zeitalter", fenster: 1 }]));
  pruefe("zweiter Klick im selben Fenster ändert nichts", tabsFuerProjekt("w", "zeitalter", 1, o) === o);
  const o2 = tabsFuerProjekt("w", "zeitalter", 2, o);
  pruefe("Klick in anderem Fenster holt den Tab herüber statt zu doppeln", o2.length === 1 && o2[0].fenster === 2);
  const t = tabsFuerKlick("w", 1, [], laeuft);
  pruefe("laufende Projekt-Session kommt beim Klick auf die Verbindung zurück", gleich(t, [{ name: "w", sid: "p:zeitalter", fenster: 1 }]));
}

console.log("Fenster schließen:");
{
  const offen = [{ name: "a", sid: "main", fenster: 1 }, { name: "b", sid: "main", fenster: 3 }];
  pruefe("Tabs eines geschlossenen Fensters wandern ins erste", gleich(heimholen(offen, [1, 2]).map((t) => t.fenster), [1, 1]));
  pruefe("ohne Änderung bleibt es dieselbe Liste", heimholen(offen, [1, 3]) === offen);
}

console.log("Nebeneinander legen:");
{
  const eins = { x: 19, y: 63.6, w: 52.4, h: 35.6 };
  const zwei = nebeneinander([{ id: "terminal", rect: eins }], "terminal:2");
  const l = zwei.terminal, r = zwei["terminal:2"];
  pruefe("gleiche Höhe und Zeile", l.y === r.y && l.h === r.h && l.y === 63.6);
  pruefe("zusammen so breit wie vorher, mit Lücke", Math.abs(l.w + r.w + 0.6 - 52.4) < 0.15 && r.x > l.x + l.w, JSON.stringify(zwei));
  const drei = nebeneinander([{ id: "terminal", rect: l }, { id: "terminal:2", rect: r }], "terminal:3");
  const breiten = Object.values(drei).map((x) => x.w);
  pruefe("das dritte macht Drittel, nicht Hälfte und Viertel", Math.max(...breiten) - Math.min(...breiten) < 0.15 && Object.keys(drei).length === 3, JSON.stringify(drei));
  pruefe("die Zeile bleibt so breit wie am Anfang", Math.abs(drei["terminal:3"].x + drei["terminal:3"].w - (19 + 52.4)) < 0.3);
  pruefe("zu schmal zum Teilen → null", nebeneinander([{ id: "terminal", rect: { x: 0, y: 0, w: 20, h: 30 } }], "terminal:2") === null);
  const verstreut = nebeneinander(
    [{ id: "terminal", rect: { x: 0, y: 0, w: 40, h: 30 } }, { id: "terminal:2", rect: { x: 50, y: 50, w: 40, h: 40 } }], "terminal:3");
  pruefe("liegen sie nicht in einer Zeile, wird nur das letzte halbiert",
    !verstreut.terminal && verstreut["terminal:2"].y === 50 && verstreut["terminal:3"].y === 50 && verstreut["terminal:3"].x > 69, JSON.stringify(verstreut));

  console.log("Platz zurückgeben:");
  let erbe = platzZurueck(r, [{ id: "terminal", rect: l }]);
  pruefe("das rechte schließt → das linke ist wieder so breit wie vorher", erbe.id === "terminal" && Math.abs(erbe.rect.w - eins.w) < 0.15 && erbe.rect.x === eins.x, JSON.stringify(erbe));
  erbe = platzZurueck(l, [{ id: "terminal:2", rect: r }]);
  pruefe("das linke schließt → das rechte rückt nach links auf", erbe.id === "terminal:2" && erbe.rect.x === eins.x && Math.abs(erbe.rect.w - eins.w) < 0.15, JSON.stringify(erbe));
  pruefe("kein anliegender Nachbar → niemand erbt", platzZurueck(r, [{ id: "terminal", rect: { x: 0, y: 0, w: 10, h: 10 } }]) === null);
}

console.log("Standardanordnung mit mehreren Terminal-Fenstern:");
{
  const EPS = 0.15;
  const ueberlappt = (a, b) =>
    a.x + a.w > b.x + EPS && b.x + b.w > a.x + EPS && a.y + a.h > b.y + EPS && b.y + b.h > a.y + EPS;
  for (const extra of [1, 2, 3]) {
    const ids = ["dateien", "chat", "terminal", "agenten", "ext:vnc", ...Array.from({ length: extra }, (_, i) => `terminal:${i + 2}`)];
    const l = standardLayout(ids);
    const e = Object.entries(l);
    const krach = [];
    for (let i = 0; i < e.length; i++) for (let j = i + 1; j < e.length; j++)
      if (ueberlappt(e[i][1], e[j][1])) krach.push(`${e[i][0]}×${e[j][0]}`);
    const reihe = ids.filter(istTerminalPanel).map((id) => l[id]);
    pruefe(`${extra + 1} Terminal-Fenster: jedes Panel hat Platz, nichts überschneidet sich`,
      ids.every((id) => l[id]) && krach.length === 0, krach.join(" "));
    pruefe(`${extra + 1} Terminal-Fenster liegen in einer Zeile nebeneinander`,
      reihe.every((r) => r.y === reihe[0].y && r.h === reihe[0].h) && reihe.every((r, i) => i === 0 || r.x > reihe[i - 1].x));
    pruefe(`${extra + 1} Terminal-Fenster bleiben in der mittleren Spalte`,
      reihe[0].x === 19 && reihe.at(-1).x + reihe.at(-1).w <= 19 + 52.4 + EPS);
  }
  const ohne = standardLayout(["dateien", "chat", "terminal", "agenten"]);
  const mit = standardLayout(["dateien", "chat", "terminal", "terminal:2", "agenten"]);
  pruefe("Chat, Dateien und Agenten bleiben, wo sie waren", ["dateien", "chat", "agenten"].every((id) => gleich(ohne[id], mit[id])));
}

console.log(fehler ? `\n${fehler} Fehler` : "\nAlles grün");
process.exit(fehler ? 1 : 0);
