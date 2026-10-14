// Kopieren aus dem Terminal: was in der Zwischenablage landet, soll sich
// einfügen lassen — ein Link als Link, ein Befehl als Befehl.
//
// DAS PROBLEM (10.10.2026): Kopierte Links und Befehle kamen mit Leerzeichen
// an. Drei Quellen, alle aus dem Bildschirm einer TUI (Claude Code):
//   1. Zeilen sind bis zum Rand mit ECHTEN Leerzeichen aufgefüllt — xterm
//      schneidet nur leere Zellen ab, geschriebene Leerzeichen bleiben.
//   2. Der Text steht eingerückt; die Einrückung wandert mit.
//   3. Die TUI bricht lange Zeilen selbst um (harter Zeilenwechsel plus
//      Einrückung, für xterm KEINE umbrochene Zeile). Ein Link über zwei
//      Zeilen enthält dann Zeilenwechsel und Leerzeichen mittendrin.
//
// `kopierSauber` räumt eine AUSWAHL auf: Zeilenenden kappen, gemeinsame
// Einrückung weg, und Zeilen wieder zusammensetzen, die nur am Rand
// umbrochen wurden. Ob umbrochen wurde, verrät die Breite: hätte das erste
// Wort der Folgezeile noch in die Zeile gepasst, war der Zeilenwechsel echt.
// Rein rechnerisch, prüfbar ohne Browser (tests/test_kopie.mjs).

// Beginnt so eine Zeile, ist sie ein neuer Punkt — nie anhängen.
const NEUER_PUNKT = /^(?:[-*•●○◦▪⎿>#$❯│|]|\d+[.)])(?:\s|$)/;
// Die Zeile endet mitten in einer Adresse (kein Leerzeichen mehr danach).
const ADRESSE_OFFEN = /(?:[a-z][a-z0-9+.-]*:\/\/|www\.)\S*$/i;
// So viele Spalten lässt eine TUI am rechten Rand gern frei.
const RAND = 2;

const ende = (z) => z.replace(/[ \t\u00a0]+$/, "");
const einzug = (z) => z.length - z.trimStart().length;

/** Leerzeichen an den Zeilenenden weg — sonst nichts (für „Alles kopieren"). */
export function zeilenEnden(text) {
  return text.replace(/\r/g, "").split("\n").map(ende).join("\n");
}

/**
 * Auswahl für die Zwischenablage aufräumen.
 * `spalten`: Breite des Terminals (0 = unbekannt → nichts zusammensetzen),
 * `versatz`: Spalte, in der die Auswahl in ihrer ersten Zeile beginnt.
 */
export function kopierSauber(text, { spalten = 0, versatz = 0 } = {}) {
  const roh = text.replace(/\r/g, "").split("\n").map(ende);

  // Wie voll war die (letzte Bildschirm-)Zeile? Von xterm umbrochene Zeilen
  // kommen als EINE lange Zeile an — dann zählt nur ihr Rest.
  const voll = roh.map((z, i) => {
    const n = z.length + (i === 0 ? versatz : 0);
    return spalten && n > spalten ? ((n - 1) % spalten) + 1 : n;
  });

  // Gemeinsame Einrückung; die erste Zeile zählt nur mit, wenn die Auswahl
  // am Zeilenanfang beginnt (sonst fehlt ihr die Einrückung ohnehin).
  const mass = roh.filter((z, i) => z && (i > 0 || !versatz)).map(einzug);
  const weg = mass.length ? Math.min(...mass) : 0;
  const zeilen = roh.map((z, i) => (i === 0 && versatz ? z.trimStart() : z.slice(weg)));

  const raus = [];
  let vollDavor = 0;
  zeilen.forEach((z, i) => {
    const davor = raus[raus.length - 1];
    const kern = z.trimStart();
    const wort = kern.split(/\s/)[0];
    const umbrochen =
      spalten > 0 &&
      raus.length > 0 &&
      davor !== "" &&
      kern !== "" &&
      !NEUER_PUNKT.test(kern) &&
      vollDavor + 1 + wort.length > spalten - RAND;
    if (umbrochen) {
      // Mitten in einer Adresse am Rand abgeschnitten: ohne Leerzeichen weiter
      const naht = vollDavor >= spalten - RAND && ADRESSE_OFFEN.test(davor) ? "" : " ";
      raus[raus.length - 1] = davor + naht + kern;
    } else {
      raus.push(z);
    }
    vollDavor = voll[i];
  });
  return raus.join("\n").replace(/^\n+|\s+$/g, "");
}
