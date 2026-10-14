// Läuft im Browser noch die Fassung, die der Server gerade ausliefert?
//
// DAS PROBLEM (10.10.2026): Nach einem Deploy verbindet sich das Terminal von
// selbst neu — die SEITE aber bleibt die alte. Ein frisch ausgerollter Fix
// (Wortvorschläge am Handy) wirkte deshalb scheinbar nicht: das Handy lief
// weiter mit dem alten Skript, bis jemand von Hand neu lud. In der
// installierten App gibt es dafür nicht einmal einen Knopf.
//
// Erkennung ohne Zutun des Servers: Vite benennt das Hauptskript nach seinem
// Inhalt (/assets/index-<hash>.js), und index.html wird nie zwischengespeichert.
// Nennt die frische index.html ein anderes Skript als das laufende, gibt es
// eine neue Fassung. Geprüft wird, wenn die App wieder nach vorn kommt, das
// Netz zurück ist, und in großen Abständen.

const HAUPTSKRIPT = /<script[^>]+src="(\/assets\/index-[^"]+\.js)"/;

/** Pfad des Hauptskripts aus dem Text einer index.html (null = keins). */
export function skriptAus(html) {
  return HAUPTSKRIPT.exec(html || "")?.[1] ?? null;
}

/** true nur, wenn BEIDE bekannt sind und sich unterscheiden. */
export function istNeuer(laufend, frisch) {
  return !!laufend && !!frisch && laufend !== frisch;
}

const laufendesSkript = () => {
  const el = document.querySelector('script[type="module"][src^="/assets/index-"]');
  return el ? new URL(el.src).pathname : null; // im Dev-Betrieb null → nie Alarm
};

const TAKT_MS = 10 * 60 * 1000;

/** Ruft `melden()` einmal, sobald der Server eine andere Fassung ausliefert. */
export function fassungBeobachten(melden) {
  const laufend = laufendesSkript();
  if (!laufend) return () => {};
  let fertig = false;
  const pruefen = async () => {
    if (fertig || document.hidden) return;
    try {
      const antwort = await fetch("/index.html", { cache: "no-store" });
      if (!antwort.ok) return; // Server startet gerade neu — später wieder
      if (istNeuer(laufend, skriptAus(await antwort.text())) && !fertig) {
        fertig = true;
        melden();
      }
    } catch {
      /* kein Netz — die nächste Gelegenheit kommt */
    }
  };
  const takt = setInterval(pruefen, TAKT_MS);
  document.addEventListener("visibilitychange", pruefen);
  window.addEventListener("online", pruefen);
  // Das Terminal meldet jeden Wiederaufbau seiner Verbindung — nach einem
  // Deploy ist das der früheste Moment, in dem die neue Fassung da ist.
  window.addEventListener("fassung:pruefen", pruefen);
  return () => {
    fertig = true;
    clearInterval(takt);
    document.removeEventListener("visibilitychange", pruefen);
    window.removeEventListener("online", pruefen);
    window.removeEventListener("fassung:pruefen", pruefen);
  };
}
