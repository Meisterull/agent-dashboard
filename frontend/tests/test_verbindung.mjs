// Prüft die Verbindungs-Hygiene des Terminals (src/termVerbindung.js):
// Herzschlag gegen eingeschlafene Sockets, Warteschlange für Eingaben
// während der Trennung, Wächter gegen Zwergen-Maße. Reines Node, keine
// Abhängigkeiten:  node tests/test_verbindung.mjs
//
// Die Uhr wird von Hand gedreht — so lässt sich „8 s ohne Pong" in
// Millisekunden nachstellen, ohne dass der Test wirklich wartet.

import {
  Herzschlag,
  MIN_COLS,
  MIN_ROWS,
  Warteschlange,
  plausibleGroesse,
} from "../src/termVerbindung.js";

let fehler = 0;
const pruefe = (was, ok, zusatz = "") => {
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${zusatz ? `  ${zusatz}` : ""}`);
};

// Herzschlag mit gestellter Uhr: pings/tote zählen, was passiert ist.
function aufbau(opts = {}) {
  let uhr = 1_000_000;
  const protokoll = { pings: 0, tot: 0 };
  const herz = new Herzschlag({
    jetzt: () => uhr,
    sendePing: () => protokoll.pings++,
    tot: () => protokoll.tot++,
    ...opts,
  });
  const vor = (ms) => (uhr += ms);
  return { herz, protokoll, vor };
}

console.log("Herzschlag — Funkstille:");
{
  const { herz, protokoll, vor } = aufbau();
  vor(10_000);
  herz.pruefen();
  pruefe("10 s Ruhe: noch kein Ping (Schwelle 15 s)", protokoll.pings === 0);
  vor(6_000);
  herz.pruefen();
  pruefe("16 s Ruhe: Ping geht raus", protokoll.pings === 1);
  vor(5_000);
  pruefe("5 s nach dem Ping: noch kein Urteil", herz.pruefen() === false && protokoll.tot === 0);
  vor(4_000);
  pruefe("9 s ohne Pong: Verbindung gilt als tot", herz.pruefen() === true && protokoll.tot === 1);
  pruefe("kein zweiter Ping währenddessen", protokoll.pings === 1);
  pruefe("direkt danach kein erneutes Urteil", herz.pruefen() === false && protokoll.tot === 1);
}

console.log("Herzschlag — Pong rettet:");
{
  const { herz, protokoll, vor } = aufbau();
  vor(16_000);
  herz.pruefen();
  vor(3_000);
  herz.empfangen(); // Pong (oder irgendeine Ausgabe) kommt an
  vor(20_000);
  pruefe("nach Lebenszeichen kein Todesurteil", herz.pruefen() === false && protokoll.tot === 0);
  pruefe("…sondern nach neuer Funkstille der nächste Ping", protokoll.pings === 2);
}

console.log("Herzschlag — Tippen ohne Echo:");
{
  const { herz, protokoll, vor } = aufbau();
  herz.gesendet();
  pruefe("Tastendruck kurz nach Ausgabe: kein Ping", protokoll.pings === 0);
  vor(3_000);
  herz.gesendet();
  pruefe("Tastendruck nach 3 s ohne Echo: Ping sofort", protokoll.pings === 1);
  herz.gesendet();
  pruefe("weiteres Tippen: kein Ping-Gewitter, einer reicht", protokoll.pings === 1);
  vor(9_000);
  pruefe("ohne Pong: tot nach der Frist (statt erst nach 15 s + Frist)", herz.pruefen() === true);
}

console.log("Herzschlag — App kommt zurück:");
{
  const { herz, protokoll, vor } = aufbau();
  vor(1_000);
  herz.sofort();
  pruefe("sofort(): Ping ohne Rücksicht auf Ruhezeit", protokoll.pings === 1);
  herz.sofort();
  pruefe("sofort() während eines offenen Pings: kein zweiter", protokoll.pings === 1);
  herz.neueVerbindung();
  vor(9_000);
  pruefe("neueVerbindung() vergisst den offenen Ping", herz.pruefen() === false && protokoll.tot === 0);
}

console.log("Warteschlange:");
{
  const w = new Warteschlange(10);
  pruefe("leer: nichts nachzuliefern", w.leeren() === "" && w.laenge === 0);
  pruefe("Leeres wird angenommen und ignoriert", w.push("") === true && w.laenge === 0);
  pruefe("nimmt Eingaben an", w.push("ls -") && w.push("la\r") && w.laenge === 7);
  pruefe("Deckel: darüber wird abgelehnt, nichts Altes fliegt raus", w.push("xxxx") === false && w.laenge === 7);
  pruefe("Reihenfolge bleibt", w.leeren() === "ls -la\r");
  pruefe("nach dem Leeren wieder frei", w.laenge === 0 && w.push("neu") === true);
}

console.log("Zwergen-Maße:");
{
  pruefe(`unter ${MIN_COLS}×${MIN_ROWS} unplausibel (7×4 aus dem Screenshot)`, !plausibleGroesse(7, 4));
  pruefe("45×2 unplausibel", !plausibleGroesse(45, 2));
  pruefe("45×30 plausibel", plausibleGroesse(45, 30));
  pruefe("Grenze zählt als plausibel", plausibleGroesse(MIN_COLS, MIN_ROWS));
}

console.log(fehler ? `\n${fehler} Fehler` : "\nalles grün");
process.exit(fehler ? 1 : 0);
