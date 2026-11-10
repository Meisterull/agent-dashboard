/* Terminal-Schrift am Handy (08.11.2026): mitgelieferte Schriften geladen,
 * Unicode 11 aktiv, eine zu große gemerkte Schrift wird auf ≥ 50 Spalten
 * verkleinert — sonst zerfallen die Reiter einer Mehrfach-Rückfrage („Tes…").
 * Legt zusätzlich einen Screenshot nach /t/schrift.png zum Ansehen ab.
 *
 * Aufruf wie test_terminal_browser.cjs, nur mit ?panel=schrift:
 *   cd frontend && npx vite build --config tests/vite.pruefstand.mjs
 *   cp -r public/fonts /tmp/pruefstand/   # Schriften liegen in public/
 *   python3 -m http.server 8181 --directory /tmp/pruefstand &
 *   docker run --rm --network=host -e NODE_PATH=/usr/src/app/node_modules \
 *     -v "$PWD/tests:/t" zenika/alpine-chrome:with-puppeteer \
 *     node /t/test_schrift_browser.cjs "http://127.0.0.1:8181/pruefstand.html?panel=schrift&groesse=16"
 */
const puppeteer = require("puppeteer");
const ZIEL = process.argv[2] || "http://127.0.0.1:8181/pruefstand.html?panel=schrift&groesse=16";
let fehler = 0;
const pruefe = (was, ok, zusatz = "") => {
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${zusatz ? `  ${zusatz}` : ""}`);
};
(async () => {
  const browser = await puppeteer.launch({ args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 780, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await page.goto(ZIEL, { waitUntil: "networkidle0" });
  const r = await page.evaluate(() => window.__bereit);
  console.log("Messwerte:", JSON.stringify(r));
  pruefe("Hauptschrift geladen", r.monoGeladen);
  pruefe("Symbolschrift geladen", r.symboleGeladen);
  pruefe("Unicode 11 aktiv", r.unicode === "11");
  pruefe("Schrift 16 px war am Handy zu groß", r.spaltenVorher < 50, `${r.spaltenVorher} Spalten`);
  pruefe("nach Anpassung mindestens 50 Spalten", r.spalten >= 50, `${r.spalten} Spalten bei ${r.groesse} px`);
  await new Promise((ok) => setTimeout(ok, 300));
  await page.screenshot({ path: "/t/schrift.png" });
  await browser.close();
  console.log(fehler ? `\n${fehler} Prüfung(en) fehlgeschlagen` : "\nalle Prüfungen bestanden");
  process.exit(fehler ? 1 : 0);
})();
