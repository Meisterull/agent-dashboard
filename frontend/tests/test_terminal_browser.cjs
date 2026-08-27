/* Terminal am Handy: Verlauf wischen und Größenwechsel (Tastatur auf/zu).
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

  await browser.close();
  console.log(fehler ? `\n${fehler} Prüfung(en) fehlgeschlagen` : "\nalles grün");
  process.exit(fehler ? 1 : 0);
})();
