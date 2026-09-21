/* Terminal am Handy: Verlauf wischen, Größenwechsel (Tastatur auf/zu) und
 * der Sichtbarkeits-Wächter (kein Fit bei display:none, termVerbindung.js).
 *
 * Beides ist xterm-eigenes Verhalten, das mit dem Finger nicht funktioniert:
 * Wischgesten rasten auf Zeilenkanten zurück (ein Wisch über 300 px bewegte
 * 30 px), und ein fit() nach Größenwechsel wirft den Blick ans Ende. Geprüft
 * wird der echte Code aus src/termScroll.js, den auch Terminal.jsx benutzt;
 * `?roh` mountet dasselbe Terminal OHNE die Gegenmaßnahme (Gegenprobe).
 *
 * Aufruf (Host hat keine GUI-Libs, deshalb Chrome aus dem Container):
 *
 *   cd frontend && npx vite build --config tests/vite.pruefstand.mjs
 *   python3 -m http.server 8181 --directory /tmp/pruefstand &
 *   docker run --rm --network=host -e NODE_PATH=/usr/src/app/node_modules \
 *     -v "$PWD/tests:/t" zenika/alpine-chrome:with-puppeteer \
 *     node /t/test_terminal_browser.cjs "http://127.0.0.1:8181/pruefstand.html?panel=terminal"
 */
const puppeteer = require("puppeteer");
const ZIEL = process.argv[2] || "http://127.0.0.1:8181/pruefstand.html?panel=terminal";

let fehler = 0;
const pruefe = (was, ok, zusatz = "") => {
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${zusatz ? `  ${zusatz}` : ""}`);
};

(async () => {
  const browser = await puppeteer.launch({ args: ["--no-sandbox", "--disable-dev-shm-usage"] });

  const messen = async (url) => {
    const page = await browser.newPage();
    await page.setViewport({ width: 390, height: 780, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
    await page.goto(url, { waitUntil: "networkidle0" });
    await page.waitForSelector(".xterm-viewport");
    await new Promise((r) => setTimeout(r, 500));

    const zeile = () => page.evaluate(() => window.__term.buffer.active.viewportY);
    const wisch = async (dy) => {
      const x = 195, y = dy > 0 ? 200 : 500;
      await page.touchscreen.touchStart(x, y);
      for (let i = 1; i <= 20; i++) {
        await page.touchscreen.touchMove(x, y + (dy * i) / 20);
        await new Promise((r) => setTimeout(r, 16)); // ~60 Hz wie ein echter Finger
      }
      await page.touchscreen.touchEnd();
      await new Promise((r) => setTimeout(r, 400));
    };
    return { page, zeile, wisch };
  };

  const zeilenHoehe = 300 / 17; // ~17 px pro Zeile → ein 300-px-Wisch ≈ 17 Zeilen

  console.log("Ausgeblendetes Panel (display:none) darf die Spalten nicht ändern:");
  {
    // Befund vom 10.09.2026: bei display:none liest das FitAddon die Breite
    // „100%" als 100 px und schlägt eine Handvoll Spalten vor — die gingen
    // bis dahin als resize an die Shell (Siebener-Häppchen im Verlauf).
    const { page } = await messen(ZIEL);
    const vorher = await page.evaluate(() => window.__term.cols);
    const versteckt = await page.evaluate(() => {
      window.__verstecken(true);
      const gefittet = window.__fit();
      return { gefittet, cols: window.__term.cols, roh: window.__fitVorschlag() };
    });
    pruefe(`Terminal hat vorher eine echte Breite (${vorher} Spalten)`, vorher >= 20);
    pruefe(
      "Gegenprobe: das FitAddon selbst würde ausgeblendet Zwergen-Maße vorschlagen",
      versteckt.roh && versteckt.roh.cols < 20,
      JSON.stringify(versteckt.roh),
    );
    pruefe("Wächter überspringt das Fit", versteckt.gefittet === false);
    pruefe(`Spalten bleiben bei ${vorher}`, versteckt.cols === vorher);
    const zurueck = await page.evaluate(() => {
      window.__verstecken(false);
      return { gefittet: window.__fit(), cols: window.__term.cols };
    });
    pruefe("sichtbar: Fit läuft wieder", zurueck.gefittet === true && zurueck.cols === vorher);
    await page.close();
  }

  console.log("Ohne Gegenmaßnahme (xterm pur):");
  {
    const { page, zeile, wisch } = await messen(`${ZIEL}&roh`);
    const vor = await zeile();
    await wisch(300);
    const nach = await zeile();
    console.log(`  Wisch über 300 px bewegt ${vor - nach} Zeilen (erwartbar wären ~${Math.round(zeilenHoehe)})`);
    pruefe("Befund bestätigt: xterm allein scrollt kaum", vor - nach < 4);
    await page.close();
  }

  console.log("\nMit termScroll.js:");
  {
    const { page, zeile, wisch } = await messen(ZIEL);
    const vor = await zeile();
    await wisch(300); // Finger nach unten = zurück im Verlauf
    const zurueck = await zeile();
    pruefe(
      "Wisch blättert proportional zurück",
      vor - zurueck > 10,
      `${vor - zurueck} Zeilen`,
    );

    await wisch(-300); // Finger nach oben = wieder ans Ende
    const vorwaerts = await zeile();
    pruefe("Wisch in die Gegenrichtung führt zurück", vorwaerts > zurueck, `${vorwaerts - zurueck} Zeilen`);

    // Tastatur auf: Größenwechsel darf die Stelle im Verlauf nicht wegwerfen
    await wisch(300);
    const vorFit = await zeile();
    await page.evaluate(() => {
      document.documentElement.style.setProperty("--app-h", "400px");
      window.__fit();
    });
    await new Promise((r) => setTimeout(r, 300));
    const nachFit = await zeile();
    const endeJetzt = await page.evaluate(() => window.__term.buffer.active.baseY);
    pruefe(
      "Tastatur auf: Blick bleibt im Verlauf",
      nachFit < endeJetzt,
      `Zeile ${nachFit} von ${endeJetzt}`,
    );

    // Gegenprobe: am Ende stehend soll es auch am Ende bleiben
    await page.evaluate(() => window.__term.scrollToBottom());
    await page.evaluate(() => {
      document.documentElement.style.setProperty("--app-h", "780px");
      window.__fit();
    });
    await new Promise((r) => setTimeout(r, 300));
    const amEnde = await page.evaluate(
      () => window.__term.buffer.active.viewportY === window.__term.buffer.active.baseY,
    );
    pruefe("am Ende stehend bleibt es am Ende", amEnde);
    await page.close();
  }

  // Befund vom 21.09.2026 („am Tablet kann ich nicht scrollen, am PC geht es"):
  // Läuft eine TUI, blättert die ANWENDUNG — xterm übersetzt dafür das Mausrad
  // (Maus-Reports bzw. Pfeiltasten), für den Finger gab es das nicht.
  const TUI_LAGEN = [
    ["Alternativpuffer + Maus-Reporting (Claude Code)", "\x1b[?1049h\x1b[?1000h\x1b[?1006h", "\x1b[<64;", "\x1b[<65;"],
    ["nur Alternativpuffer (less, vim)", "\x1b[?1049h", "\x1b[A", "\x1b[B"],
    ["Maus-Reporting im Normalpuffer", "\x1b[?1000h\x1b[?1006h", "\x1b[<64;", "\x1b[<65;"],
  ];
  const tuiSeite = async (url, modus) => {
    const page = await browser.newPage();
    // Tablet quer: breit genug für die Desktop-Ansicht, aber nur ein Finger
    await page.setViewport({ width: 1024, height: 768, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
    await page.goto(url, { waitUntil: "networkidle0" });
    await page.waitForSelector(".xterm-viewport");
    await new Promise((r) => setTimeout(r, 500));
    await page.evaluate((s) => new Promise((ok) => window.__term.write(s + "TUI\r\n", ok)), modus);
    await page.evaluate(() => { window.__daten.length = 0; });
    const wisch = async (dy) => {
      const x = 500, y = dy > 0 ? 200 : 500;
      await page.touchscreen.touchStart(x, y);
      for (let i = 1; i <= 20; i++) {
        await page.touchscreen.touchMove(x, y + (dy * i) / 20);
        await new Promise((r) => setTimeout(r, 16));
      }
      await page.touchscreen.touchEnd();
      await new Promise((r) => setTimeout(r, 400));
    };
    const gesendet = async () => {
      const alles = await page.evaluate(() => window.__daten.join(""));
      await page.evaluate(() => { window.__daten.length = 0; });
      return alles;
    };
    return { page, wisch, gesendet };
  };
  const zaehle = (text, stueck) => text.split(stueck).length - 1;

  for (const [name, modus, hoch, runter] of TUI_LAGEN) {
    console.log(`\nTUI — ${name}:`);
    {
      const { page, wisch, gesendet } = await tuiSeite(`${ZIEL}&roh`, modus);
      await wisch(300);
      pruefe("Gegenprobe (xterm pur): der Wisch erreicht die Anwendung nicht", (await gesendet()) === "");
      await page.close();
    }
    const { page, wisch, gesendet } = await tuiSeite(ZIEL, modus);
    await wisch(300); // Finger nach unten = zurückblättern = Rad nach oben
    let text = await gesendet();
    pruefe(
      "Wisch nach unten kommt als Rad-nach-oben an, etwa eines je Zeile",
      zaehle(text, hoch) > 10 && zaehle(text, runter) === 0,
      `${zaehle(text, hoch)}×`,
    );
    await wisch(-300);
    text = await gesendet();
    pruefe(
      "Gegenrichtung kommt als Rad-nach-unten an",
      zaehle(text, runter) > 10 && zaehle(text, hoch) === 0,
      `${zaehle(text, runter)}×`,
    );
    if (modus.includes("?1000h")) {
      // Das Festhalten des Zeigers darf den Tipp nicht schlucken: die TUI
      // bekommt weiter Drücken + Loslassen (Knöpfe, Cursor setzen).
      await page.touchscreen.tap(500, 300);
      await new Promise((r) => setTimeout(r, 500));
      text = await gesendet();
      pruefe(
        "Tipp bleibt ein Klick (Drücken + Loslassen), kein Rad",
        zaehle(text, "\x1b[<0;") === 2 && zaehle(text, hoch) + zaehle(text, runter) === 0,
        JSON.stringify(text),
      );
    }
    await page.close();
  }

  await browser.close();
  console.log(fehler ? `\n${fehler} Prüfung(en) fehlgeschlagen` : "\nalles grün");
  process.exit(fehler ? 1 : 0);
})();
