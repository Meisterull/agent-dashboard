/* Tastatur ohne Größenänderung (08.11.2026): Auf- und Zuklappen der
 * Bildschirmtastatur darf die Zeilenzahl NICHT ändern (sonst zeichnet Claude
 * Code neu und lässt die alte Fassung im Verlauf stehen), die Cursorzeile
 * muss trotzdem sichtbar bleiben. `?roh` = ohne Gegenmaßnahme (Gegenprobe).
 *
 * Aufruf wie test_terminal_browser.cjs, nur mit ?panel=tastatur:
 *   cd frontend && npx vite build --config tests/vite.pruefstand.mjs
 *   python3 -m http.server 8181 --directory /tmp/pruefstand &
 *   docker run --rm --network=host -e NODE_PATH=/usr/src/app/node_modules \
 *     -v "$PWD/tests:/t" zenika/alpine-chrome:with-puppeteer \
 *     node /t/test_tastatur_browser.cjs "http://127.0.0.1:8181/pruefstand.html?panel=tastatur"
 */
const puppeteer = require("puppeteer");
const ZIEL = process.argv[2] || "http://127.0.0.1:8181/pruefstand.html?panel=tastatur";
let fehler = 0;
const pruefe = (was, ok, zusatz = "") => {
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${zusatz ? `  ${zusatz}` : ""}`);
};
const ablauf = async (browser, url) => {
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 780, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await page.goto(url, { waitUntil: "networkidle0" });
  await page.waitForFunction(() => window.__tastatur);
  const vorher = await page.evaluate(() => ({ zeilen: window.__term.rows, groessen: window.__groessen }));
  const auf = await page.evaluate(() => window.__tastatur(true));
  const zu = await page.evaluate(() => window.__tastatur(false));
  await page.close();
  return { vorher, auf, zu };
};
(async () => {
  const browser = await puppeteer.launch({ args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  const mit = await ablauf(browser, ZIEL);
  console.log("mit Gegenmaßnahme:", JSON.stringify(mit));
  pruefe("Tastatur auf: Zeilenzahl bleibt", mit.auf.zeilen === mit.vorher.zeilen, `${mit.vorher.zeilen} → ${mit.auf.zeilen}`);
  pruefe("Tastatur auf: Cursorzeile sichtbar", mit.auf.cursorSichtbar);
  pruefe("Tastatur zu: Zeilenzahl bleibt", mit.zu.zeilen === mit.vorher.zeilen);
  pruefe("keine einzige Größenänderung", mit.zu.groessen === mit.vorher.groessen, `${mit.zu.groessen - mit.vorher.groessen} Änderungen`);
  const roh = await ablauf(browser, ZIEL + (ZIEL.includes("?") ? "&" : "?") + "roh");
  console.log("Gegenprobe ohne:", JSON.stringify(roh));
  pruefe("Gegenprobe: ohne Gegenmaßnahme ändert sich die Zeilenzahl", roh.auf.zeilen < roh.vorher.zeilen, `${roh.vorher.zeilen} → ${roh.auf.zeilen}`);
  await browser.close();
  console.log(fehler ? `\n${fehler} Prüfung(en) fehlgeschlagen` : "\nalle Prüfungen bestanden");
  process.exit(fehler ? 1 : 0);
})();
