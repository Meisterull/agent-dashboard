/* Fenster ein-/ausblenden (Issue #50) im echten Browser.
 *
 * Prüft, was nur ein Browser zeigt: Haken im Menü blendet ein Fenster ganz aus
 * (display:none, kein Platz), die übrigen füllen danach die Fläche
 * überschneidungsfrei, das Panel wird dabei NICHT neu gemountet (Sitzungen
 * bleiben), das letzte sichtbare Fenster lässt sich nicht abwählen, die Wahl
 * überlebt ein Neuladen, und eine gespeicherte Ansicht bringt die
 * Sichtbarkeit mit.
 *
 *   cd frontend && npx vite build --config tests/vite.pruefstand.mjs
 *   python3 -m http.server 8177 --directory /tmp/pruefstand &
 *   docker run --rm --network=host -e NODE_PATH=/usr/src/app/node_modules \
 *     -v "$PWD/tests:/t" zenika/alpine-chrome:with-puppeteer \
 *     node /t/test_fenster_browser.cjs "http://127.0.0.1:8177/pruefstand.html?panel=fenster"
 */
const puppeteer = require("puppeteer");

const ZIEL = process.argv[2] || "http://127.0.0.1:8177/pruefstand.html?panel=fenster";
const EPS = 1;
let fehler = 0;
const pruefe = (was, ok, zusatz = "") => {
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${zusatz ? `  ${zusatz}` : ""}`);
};
const warte = (ms) => new Promise((r) => setTimeout(r, ms));

const sichtbare = (page) =>
  page.$$eval("section[data-panel-id]", (secs) =>
    secs
      .filter((s) => getComputedStyle(s).display !== "none")
      .map((s) => {
        const r = s.getBoundingClientRect();
        return { id: s.dataset.panelId, x: r.x, y: r.y, w: r.width, h: r.height };
      }),
  );
const flaeche = (page) =>
  page.$eval("section[data-panel-id]", (s) => s.parentElement.getBoundingClientRect().toJSON());

function kollisionen(k) {
  const raus = [];
  for (let i = 0; i < k.length; i++)
    for (let j = i + 1; j < k.length; j++) {
      const a = k[i], b = k[j];
      if (a.x + a.w > b.x + EPS && b.x + b.w > a.x + EPS && a.y + a.h > b.y + EPS && b.y + b.h > a.y + EPS)
        raus.push(`${a.id}×${b.id}`);
    }
  return raus;
}

async function haken(page, id) {
  if (!(await page.$("[data-fenster-menue]"))) await page.click("[data-fenster-knopf]");
  await page.waitForSelector(`[data-fenster-menue] [data-fenster="${id}"] input`);
  await page.click(`[data-fenster-menue] [data-fenster="${id}"] input`);
  await warte(150);
}

(async () => {
  const browser = await puppeteer.launch({ args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 900 });
  const log = [];
  page.on("pageerror", (e) => log.push(String(e)));
  page.on("console", (m) => m.type() === "error" && !/favicon/i.test(m.text()) && log.push(m.text()));

  await page.goto(ZIEL, { waitUntil: "networkidle0" });
  await page.evaluate(() => localStorage.clear());
  await page.reload({ waitUntil: "networkidle0" });
  await page.waitForSelector("section[data-panel-id]");

  console.log("Erstanzeige:");
  let k = await sichtbare(page);
  pruefe("alle 5 Fenster sichtbar", k.length === 5, k.map((x) => x.id).join(","));
  const mountsVorher = await page.evaluate(() => ({ ...window.__mounts }));

  console.log("\nDateien + Agenten ausblenden:");
  await haken(page, "dateien");
  await haken(page, "agenten");
  k = await sichtbare(page);
  const f = await flaeche(page);
  pruefe("nur noch chat, terminal, ext:vnc", k.map((x) => x.id).sort().join(",") === "chat,ext:vnc,terminal",
    k.map((x) => x.id).join(","));
  pruefe("überschneidungsfrei", kollisionen(k).length === 0, kollisionen(k).join(","));
  const links = Math.min(...k.map((x) => x.x)) - f.x;
  const rechts = f.x + f.width - Math.max(...k.map((x) => x.x + x.w));
  pruefe("frei gewordener Platz wird genutzt (Ränder < 2 %)",
    links < f.width * 0.02 && rechts < f.width * 0.02, `links ${links.toFixed(0)} px, rechts ${rechts.toFixed(0)} px`);
  const mountsNach = await page.evaluate(() => ({ ...window.__mounts }));
  pruefe("Ausblenden mountet nichts neu", JSON.stringify(mountsNach) === JSON.stringify(mountsVorher),
    JSON.stringify(mountsNach));
  pruefe("Knopf zeigt Zahl der ausgeblendeten", (await page.$eval("[data-fenster-knopf]", (b) => b.textContent)).includes("2"));

  console.log("\nLetztes Fenster bleibt:");
  await haken(page, "terminal");
  await haken(page, "ext:vnc");
  const gesperrt = await page.$eval('[data-fenster-menue] [data-fenster="chat"] input', (i) => i.disabled && i.checked);
  pruefe("Haken beim letzten sichtbaren (chat) ist gesperrt", gesperrt);
  k = await sichtbare(page);
  pruefe("chat allein füllt die Fläche", k.length === 1 && k[0].w > f.width * 0.95 && k[0].h > f.height * 0.95,
    `${k[0]?.w.toFixed(0)}×${k[0]?.h.toFixed(0)}`);

  console.log("\nNach dem Neuladen:");
  await page.reload({ waitUntil: "networkidle0" });
  await page.waitForSelector("section[data-panel-id]");
  k = await sichtbare(page);
  pruefe("Wahl gilt weiter (nur chat)", k.map((x) => x.id).join(",") === "chat", k.map((x) => x.id).join(","));

  console.log("\nAnsicht bringt Sichtbarkeit mit:");
  await page.click("[data-ansichten]");
  await page.waitForSelector("input[maxlength='40']");
  await page.type("input[maxlength='40']", "nur chat");
  await page.keyboard.press("Enter");
  await warte(100);
  await page.keyboard.press("Escape");
  await warte(100);
  for (const id of ["dateien", "terminal", "agenten", "ext:vnc"]) await haken(page, id);
  pruefe("wieder alle 5 sichtbar", (await sichtbare(page)).length === 5);
  await page.keyboard.press("Escape");
  await page.click("[data-ansichten]");
  await page.waitForSelector("li button");
  await page.click("li button"); // „Laden" der einzigen Ansicht
  await warte(200);
  k = await sichtbare(page);
  pruefe("Ansicht „nur chat“ blendet die anderen wieder aus", k.map((x) => x.id).join(",") === "chat",
    k.map((x) => x.id).join(","));
  pruefe("und bringt ihre Anordnung mit (chat vollflächig)", k[0] && k[0].w > f.width * 0.95,
    `${k[0]?.w.toFixed(0)} px`);

  pruefe("keine Fehler in der Konsole", log.length === 0, log.join(" | "));
  await browser.close();
  console.log(fehler ? `\n${fehler} Fehler` : "\nAlles grün");
  process.exit(fehler ? 1 : 0);
})();
