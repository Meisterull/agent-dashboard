/* Terminal-Fenster verdoppeln — im echten Browser (07.10.2026).
 *
 * Die Logik hat eigene Tests (test_terminalfenster.mjs). Hier steht, was nur
 * der Browser zeigt: ◫ legt wirklich ein zweites Fenster NEBEN das erste, das
 * zweite nimmt dem ersten keine Session weg, ⇄ legt einen Tab hinüber, ein
 * Neuladen bringt Fenster und Zuordnung zurück, und ✕ holt die Tabs heim.
 *
 * Aufruf wie test_workspace_browser.cjs (Chrome aus dem Container):
 *
 *   cd frontend && npx vite build --config tests/vite.pruefstand.mjs
 *   python3 -m http.server 8178 --directory /tmp/pruefstand &
 *   docker run --rm --network=host -e NODE_PATH=/usr/src/app/node_modules \
 *     -v "$PWD/tests:/t" zenika/alpine-chrome:with-puppeteer \
 *     node /t/test_terminalfenster_browser.cjs "http://127.0.0.1:8178/pruefstand.html?panel=terminalfenster"
 */
const puppeteer = require("puppeteer");

const ZIEL = process.argv[2] || "http://127.0.0.1:8178/pruefstand.html?panel=terminalfenster";
const BILD = process.argv[3] || null;
const EPS = 1;
const warte = (ms) => new Promise((r) => setTimeout(r, ms));

let fehler = 0;
const pruefe = (was, ok, zusatz = "") => {
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${zusatz ? `  ${typeof zusatz === "string" ? zusatz : JSON.stringify(zusatz)}` : ""}`);
};

const kasten = (page) =>
  page.$$eval("section[data-panel-id]", (secs) =>
    secs.map((s) => {
      const r = s.getBoundingClientRect();
      return { id: s.dataset.panelId, x: r.x, y: r.y, w: r.width, h: r.height };
    }));
const ueberlappungen = (k) => {
  const raus = [];
  for (let i = 0; i < k.length; i++) for (let j = i + 1; j < k.length; j++) {
    const a = k[i], b = k[j];
    if (a.x + a.w > b.x + EPS && b.x + b.w > a.x + EPS && a.y + a.h > b.y + EPS && b.y + b.h > a.y + EPS)
      raus.push(`${a.id}×${b.id}`);
  }
  return raus;
};
/* Die Tab-Beschriftungen eines Terminal-Fensters (aktive und inaktive Tabs
   tragen die Titelzeile user@host, die Verbindungsknöpfe ohne Tab auch —
   Tabs erkennt man am × daneben). */
const tabs = (page, id) =>
  page.$eval(`section[data-panel-id="${id}"]`, (s) =>
    [...s.querySelectorAll("button")].filter((b) => b.textContent.trim() === "×")
      .map((b) => b.previousElementSibling.textContent.trim() === "⇄"
        ? b.previousElementSibling.previousElementSibling.textContent.trim()
        : b.previousElementSibling.textContent.trim()));
const klick = (page, id, text) =>
  page.$eval(`section[data-panel-id="${id}"]`, (s, text) => {
    const b = [...s.querySelectorAll("button")].find((x) => x.textContent.trim() === text);
    if (!b) throw new Error(`kein Knopf „${text}“`);
    b.click();
  }, text);

(async () => {
  const browser = await puppeteer.launch({
    executablePath: "/usr/bin/chromium-browser",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 900 });
  const skriptfehler = [];
  page.on("pageerror", (e) => skriptfehler.push(String(e)));
  await page.goto(ZIEL, { waitUntil: "networkidle0" });
  await page.evaluate(() => localStorage.clear());
  await page.reload({ waitUntil: "networkidle0" });
  await page.waitForSelector('section[data-panel-id="terminal"]');
  await warte(300);

  console.log("Ausgangslage:");
  pruefe("ein Terminal-Fenster", (await kasten(page)).filter((k) => k.id.startsWith("terminal")).length === 1);
  pruefe("die laufenden Sessions kommen von selbst zurück", JSON.stringify(await tabs(page, "terminal")) === '["server","server ·2"]', await tabs(page, "terminal"));
  const vorher = (await kasten(page)).find((k) => k.id === "terminal");

  console.log("Verdoppeln:");
  await page.click('section[data-panel-id="terminal"] [data-test="terminal-verdoppeln"]');
  await page.waitForSelector('section[data-panel-id="terminal:2"]');
  await warte(300);
  let k = await kasten(page);
  const a = k.find((x) => x.id === "terminal"), b = k.find((x) => x.id === "terminal:2");
  pruefe("das zweite Fenster liegt rechts neben dem ersten, gleiche Höhe", Math.abs(a.y - b.y) < EPS && Math.abs(a.h - b.h) < EPS && b.x >= a.x + a.w - EPS, { a, b });
  pruefe("beide teilen sich den Platz des alten", Math.abs(a.x - vorher.x) < EPS && Math.abs(b.x + b.w - (vorher.x + vorher.w)) < 3 && Math.abs(a.w - b.w) < 3, { vorher, a, b });
  pruefe("nichts überschneidet sich", ueberlappungen(k).length === 0, ueberlappungen(k).join(" "));
  pruefe("das neue Fenster ist leer, das alte behält seine Tabs",
    (await tabs(page, "terminal:2")).length === 0 && (await tabs(page, "terminal")).length === 2);
  if (BILD) await page.screenshot({ path: BILD });

  console.log("Kein Wegnehmen:");
  await klick(page, "terminal:2", "server");
  await warte(150);
  pruefe("„server“ im zweiten Fenster öffnet ein NEUES Terminal (·3)", JSON.stringify(await tabs(page, "terminal:2")) === '["server ·3"]', await tabs(page, "terminal:2"));
  pruefe("das erste Fenster behält main und ·2", JSON.stringify(await tabs(page, "terminal")) === '["server","server ·2"]', await tabs(page, "terminal"));
  await klick(page, "terminal:2", "werkstatt");
  await warte(150);
  await klick(page, "terminal", "werkstatt");
  await warte(150);
  pruefe("zweite Verbindung: main rechts, ·2 links",
    (await tabs(page, "terminal:2")).includes("werkstatt") && (await tabs(page, "terminal")).includes("werkstatt ·2"),
    { links: await tabs(page, "terminal"), rechts: await tabs(page, "terminal:2") });
  const alle = [...await tabs(page, "terminal"), ...await tabs(page, "terminal:2")];
  pruefe("kein Terminal ist in zwei Fenstern zugleich", new Set(alle).size === alle.length, alle);

  console.log("Hinüberlegen (⇄):");
  await page.$eval('section[data-panel-id="terminal"]', (s) => {
    const tab = [...s.querySelectorAll("button")].find((x) => x.textContent.trim() === "server ·2");
    tab.nextElementSibling.click();          // der ⇄-Knopf direkt daneben
  });
  await warte(200);
  pruefe("„server ·2“ liegt jetzt im zweiten Fenster",
    !(await tabs(page, "terminal")).includes("server ·2") && (await tabs(page, "terminal:2")).includes("server ·2"),
    { links: await tabs(page, "terminal"), rechts: await tabs(page, "terminal:2") });

  console.log("Neuladen:");
  await page.reload({ waitUntil: "networkidle0" });
  await page.waitForSelector('section[data-panel-id="terminal:2"]');
  await warte(300);
  pruefe("das zweite Fenster ist wieder da, an seinem Platz", ueberlappungen(await kasten(page)).length === 0
    && Math.abs((await kasten(page)).find((x) => x.id === "terminal:2").x - b.x) < EPS);
  pruefe("die laufenden Sessions liegen, wo sie zuletzt lagen",
    JSON.stringify(await tabs(page, "terminal")) === '["server"]' && JSON.stringify(await tabs(page, "terminal:2")) === '["server ·2"]',
    { links: await tabs(page, "terminal"), rechts: await tabs(page, "terminal:2") });

  console.log("Fenster anordnen:");
  await page.evaluate(() => window.dispatchEvent(new Event("workspace:reset")));
  await warte(300);
  k = await kasten(page);
  const a2 = k.find((x) => x.id === "terminal"), b2 = k.find((x) => x.id === "terminal:2");
  pruefe("ordnet die Terminal-Fenster nebeneinander an, ohne Überschneidung",
    ueberlappungen(k).length === 0 && Math.abs(a2.y - b2.y) < EPS && b2.x >= a2.x + a2.w - EPS, ueberlappungen(k).join(" "));

  console.log("Schließen (✕):");
  await page.click('section[data-panel-id="terminal:2"] [data-test="terminal-fenster-zu"]');
  await warte(300);
  pruefe("das zweite Fenster ist weg", !(await kasten(page)).some((x) => x.id === "terminal:2"));
  {
    const allein = (await kasten(page)).find((x) => x.id === "terminal");
    pruefe("das erste Fenster ist wieder so breit wie zwei", Math.abs(allein.w - (b2.x + b2.w - a2.x)) < 3, { allein, a2, b2 });
  }
  pruefe("seine Terminals sind im ersten gelandet", JSON.stringify((await tabs(page, "terminal")).sort()) === '["server","server ·2"]', await tabs(page, "terminal"));
  pruefe("das erste Fenster hat kein ✕", !(await page.$('section[data-panel-id="terminal"] [data-test="terminal-fenster-zu"]')));

  console.log("Obergrenze:");
  /* Vier Fenster brauchen in der mittleren Spalte je 240 px (Mindestbreite) —
     auf einem breiten Bildschirm, wie ihn hat, wer vier Terminals will. */
  await page.setViewport({ width: 2200, height: 1000 });
  await warte(200);
  for (let i = 0; i < 3; i++) {
    await page.click('section[data-panel-id="terminal"] [data-test="terminal-verdoppeln"]');
    await warte(250);
  }
  k = await kasten(page);
  {
    const reihe = k.filter((x) => x.id.startsWith("terminal"));
    pruefe("alle vier gleich breit in einer Zeile", reihe.every((x) => Math.abs(x.w - reihe[0].w) < 3 && Math.abs(x.y - reihe[0].y) < EPS), reihe);
  }
  pruefe("vier Terminal-Fenster, überschneidungsfrei", k.filter((x) => x.id.startsWith("terminal")).length === 4 && ueberlappungen(k).length === 0, ueberlappungen(k).join(" "));
  pruefe("mehr geht nicht: ◫ ist verschwunden", !(await page.$('[data-test="terminal-verdoppeln"]')));

  pruefe("keine Skriptfehler", skriptfehler.length === 0, skriptfehler.join(" | "));
  await browser.close();
  console.log(fehler ? `\n${fehler} Fehler` : "\nAlles grün");
  process.exit(fehler ? 1 : 0);
})();
