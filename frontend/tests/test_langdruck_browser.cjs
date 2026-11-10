/* Langer Druck ins Terminal (08.11.2026): ein ruhig gehaltener Finger liefert
 * Zeile + Absatz unter dem Finger, ein Wisch löst nichts aus und scrollt
 * weiter den Verlauf. Echte Touch-Ereignisse über CDP.
 *
 *   cd frontend && npx vite build --config tests/vite.pruefstand.mjs
 *   python3 -m http.server 8181 --directory /tmp/pruefstand &
 *   docker run --rm --network=host -e NODE_PATH=/usr/src/app/node_modules \
 *     -v "$PWD/tests:/t" zenika/alpine-chrome:with-puppeteer \
 *     node /t/test_langdruck_browser.cjs "http://127.0.0.1:8181/pruefstand.html?panel=langdruck"
 */
const puppeteer = require("puppeteer");
const ZIEL = process.argv[2] || "http://127.0.0.1:8181/pruefstand.html?panel=langdruck";
let fehler = 0;
const pruefe = (was, ok, zusatz = "") => {
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${zusatz ? `  ${zusatz}` : ""}`);
};
const warte = (ms) => new Promise((ok) => setTimeout(ok, ms));
(async () => {
  const browser = await puppeteer.launch({ args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 780, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await page.goto(ZIEL, { waitUntil: "networkidle0" });
  await page.waitForFunction(() => window.__yVon);
  const cdp = await page.target().createCDPSession();
  const touch = (type, x, y) => cdp.send("Input.dispatchTouchEvent", { type, touchPoints: type === "touchEnd" ? [] : [{ x, y }] });

  // 1. langer Druck auf die Befehlszeile
  const y = await page.evaluate(() => window.__yVon("kapsel/bauen.sh"));
  await touch("touchStart", 60, y);
  await warte(700);
  await touch("touchEnd");
  await warte(100);
  const t1 = await page.evaluate(() => window.__treffer);
  pruefe("langer Druck löst genau einmal aus", t1.length === 1, `${t1.length}×`);
  pruefe("Zeile = der Befehl", t1[0]?.zeile === "./kapsel/bauen.sh install", JSON.stringify(t1[0]?.zeile));
  pruefe("Absatz = Antwort ohne ●-Marke", t1[0]?.absatz?.startsWith("Hier ist der Befehl") && t1[0]?.absatz?.includes("bauen.sh install"), JSON.stringify(t1[0]?.absatz));

  // 2. kurzer Tipp löst nicht aus
  await touch("touchStart", 60, y);
  await warte(150);
  await touch("touchEnd");
  await warte(700);
  pruefe("kurzer Tipp löst nicht aus", (await page.evaluate(() => window.__treffer.length)) === 1);

  // 3. Wisch: kein Auslösen, Verlauf bewegt sich
  const vorher = await page.evaluate(() => window.__term.buffer.active.viewportY);
  await touch("touchStart", 100, 300);
  for (let i = 1; i <= 10; i++) { await touch("touchMove", 100, 300 + i * 30); await warte(30); }
  await warte(600);
  await touch("touchEnd");
  await warte(100);
  const nachher = await page.evaluate(() => window.__term.buffer.active.viewportY);
  pruefe("Wisch löst nicht aus", (await page.evaluate(() => window.__treffer.length)) === 1);
  pruefe("Wisch scrollt weiter den Verlauf", nachher < vorher, `${vorher} → ${nachher}`);

  // 4. Leerzeile: nichts
  const yLeer = await page.evaluate(() => window.__yVon("zeig mir den Befehl"));
  await page.evaluate(() => window.__term.scrollToBottom());
  const yLeerNeu = await page.evaluate(() => window.__yVon("zeig mir den Befehl"));
  await touch("touchStart", 60, yLeerNeu + 20); // eine Zeile tiefer = Leerzeile
  await warte(700);
  await touch("touchEnd");
  await warte(100);
  const t4 = await page.evaluate(() => window.__treffer);
  pruefe("auf einer Leerzeile kommt nichts", t4.length === 2 && t4[1] === null, JSON.stringify(t4[1]));

  await browser.close();
  console.log(fehler ? `\n${fehler} Prüfung(en) fehlgeschlagen` : "\nalle Prüfungen bestanden");
  process.exit(fehler ? 1 : 0);
})();
