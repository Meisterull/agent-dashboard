/* Prüft die Fensteranordnung in einem echten Browser (Issue #24).
 *
 * Die Layout-Mathematik hat eigene Tests (test_layout.mjs). Hier geht es um
 * das, was nur ein Browser zeigt: dass die Fenster wirklich überschneidungsfrei
 * gerendert werden, dass "Fenster anordnen" das auch wiederherstellt, dass ein
 * Klick IN ein iframe das Fenster nach vorn holt und dass gespeicherte
 * Ansichten eine veränderte Anordnung zurückbringen.
 *
 * Aufruf (Host hat keine GUI-Libs, deshalb Chrome aus dem Container — der Weg
 * steht so in CLAUDE.md):
 *
 *   cd frontend && npx vite build --config tests/vite.pruefstand.mjs
 *   python3 -m http.server 8177 --directory /tmp/pruefstand &
 *   docker run --rm --network=host -e NODE_PATH=/usr/src/app/node_modules \
 *     -v "$PWD/tests:/t" zenika/alpine-chrome:with-puppeteer \
 *     node /t/test_workspace_browser.cjs http://127.0.0.1:8177/pruefstand.html
 *
 * Der Port muss frei sein — `--network=host` heißt, der Browser im Container
 * greift auf denselben Netzwerk-Stack zu; ein schon belegter Port liefert ihm
 * klaglos den falschen Dienst aus. Anderen Port wählen: in beiden Zeilen.
 * Den Ablageort überschreibt PRUEFSTAND_OUT (siehe vite.pruefstand.mjs).
 */
const puppeteer = require("puppeteer");

const ZIEL = process.argv[2] || "http://127.0.0.1:8177/pruefstand.html";
const EPS = 1; // px — gerundete Prozentwerte dürfen sich um ein Haar berühren

let fehler = 0;
const pruefe = (was, ok, zusatz = "") => {
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${zusatz ? `  ${zusatz}` : ""}`);
};

const kasten = (page) =>
  page.$$eval("section[data-panel-id]", (secs) =>
    secs.map((s) => {
      const r = s.getBoundingClientRect();
      return {
        id: s.dataset.panelId,
        x: r.x, y: r.y, w: r.width, h: r.height,
        z: Number(getComputedStyle(s).zIndex) || 0,
      };
    }),
  );

function kollisionen(kaesten) {
  const raus = [];
  for (let i = 0; i < kaesten.length; i++)
    for (let j = i + 1; j < kaesten.length; j++) {
      const a = kaesten[i], b = kaesten[j];
      if (
        a.x + a.w > b.x + EPS && b.x + b.w > a.x + EPS &&
        a.y + a.h > b.y + EPS && b.y + b.h > a.y + EPS
      )
        raus.push(`${a.id}×${b.id}`);
    }
  return raus;
}

(async () => {
  const browser = await puppeteer.launch({
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 900 });
  const fehlerImLog = [];
  // Das Favicon fehlt im Prüfstand — dieses 404 ist kein Befund.
  const belanglos = (t) => /favicon/i.test(t);
  page.on("pageerror", (e) => fehlerImLog.push(String(e)));
  page.on("console", (m) => {
    if (m.type() === "error" && !belanglos(m.text())) fehlerImLog.push(m.text());
  });
  page.on("requestfailed", (r) => {
    if (!belanglos(r.url())) fehlerImLog.push(`Ladefehler ${r.url()}`);
  });

  await page.goto(ZIEL, { waitUntil: "networkidle0" });
  await page.waitForSelector("section[data-panel-id]");

  console.log("Erstanzeige:");
  let k = await kasten(page);
  pruefe("alle fünf Panels sind da", k.length === 5, k.map((p) => p.id).join(", "));
  pruefe("überschneidungsfrei", kollisionen(k).length === 0, kollisionen(k).join(", "));
  const vnc = k.find((p) => p.id === "ext:vnc");
  pruefe("das externe Fenster hat echte Fläche", vnc.w > 600 && vnc.h > 250,
    `${Math.round(vnc.w)}×${Math.round(vnc.h)} px`);
  pruefe("keine JS-Fehler beim Aufbau", fehlerImLog.length === 0, fehlerImLog.join(" | "));

  console.log("\n„Fenster anordnen“ nach dem Verschieben:");
  // Ein Fenster quer über die anderen ziehen, dann zurücksetzen.
  await page.evaluate(() => {
    const l = JSON.parse(localStorage.getItem("workspace-layout-v1") || "{}");
    l["ext:vnc"] = { x: 10, y: 10, w: 70, h: 70 };
    localStorage.setItem("workspace-layout-v1", JSON.stringify(l));
  });
  await page.reload({ waitUntil: "networkidle0" });
  await page.waitForSelector("section[data-panel-id]");
  k = await kasten(page);
  pruefe("Überlappung ist reproduziert (Ausgangslage der Issue)",
    kollisionen(k).length > 0, kollisionen(k).join(", "));

  await page.evaluate(() => window.dispatchEvent(new Event("workspace:reset")));
  await new Promise((r) => setTimeout(r, 150));
  k = await kasten(page);
  pruefe("danach überschneidungsfrei", kollisionen(k).length === 0, kollisionen(k).join(", "));

  console.log("\nKlick in das iframe holt das Fenster nach vorn:");
  // Erst ein anderes Fenster nach vorn holen — VNC ist das zuletzt angelegte
  // und läge sonst ohnehin oben, der Test wäre dann wertlos.
  await page.click("section[data-panel-id='chat'] header");
  await new Promise((r) => setTimeout(r, 100));
  k = await kasten(page);
  const zVorher = k.find((p) => p.id === "ext:vnc").z;
  const hoechsteVorher = Math.max(...k.map((p) => p.z));
  pruefe("VNC liegt zunächst nicht oben", zVorher < hoechsteVorher,
    `z=${zVorher} von ${hoechsteVorher}`);
  const rahmen = await page.$("section[data-panel-id='ext:vnc'] iframe");
  const inhalt = await rahmen.contentFrame();
  await inhalt.click("#drin");
  await new Promise((r) => setTimeout(r, 150));
  k = await kasten(page);
  const zNachher = k.find((p) => p.id === "ext:vnc").z;
  pruefe("nach dem Klick liegt es oben", zNachher === Math.max(...k.map((p) => p.z)),
    `z=${zNachher}`);

  console.log("\nAnsicht speichern und wieder laden:");
  await page.evaluate(() => window.dispatchEvent(new Event("workspace:views")));
  await page.waitForSelector("input[placeholder^='Name der Ansicht']");
  await page.type("input[placeholder^='Name der Ansicht']", "VNC groß");
  await page.evaluate(() => {
    const k = [...document.querySelectorAll("button")].find((b) => b.textContent === "Speichern");
    k.click();
  });
  await new Promise((r) => setTimeout(r, 100));
  const gespeichert = await page.evaluate(() =>
    Object.keys(JSON.parse(localStorage.getItem("workspace-views-v1") || "{}")),
  );
  pruefe("Ansicht liegt im Speicher", gespeichert.includes("VNC groß"), gespeichert.join(", "));

  const vorLaden = (await kasten(page)).find((p) => p.id === "ext:vnc");
  // Anordnung verändern, dann die Ansicht zurückholen
  await page.evaluate(() => {
    document.querySelector("button[title='Ansichten' i]")?.click();
    const l = JSON.parse(localStorage.getItem("workspace-layout-v1") || "{}");
    l["ext:vnc"] = { x: 2, y: 2, w: 30, h: 30 };
    localStorage.setItem("workspace-layout-v1", JSON.stringify(l));
  });
  await page.reload({ waitUntil: "networkidle0" });
  await page.waitForSelector("section[data-panel-id]");
  const verstellt = (await kasten(page)).find((p) => p.id === "ext:vnc");
  pruefe("Anordnung ist wirklich verstellt", Math.abs(verstellt.w - vorLaden.w) > 50,
    `${Math.round(verstellt.w)} statt ${Math.round(vorLaden.w)} px`);

  await page.evaluate(() => window.dispatchEvent(new Event("workspace:views")));
  await page.waitForSelector("input[placeholder^='Name der Ansicht']");
  await page.evaluate(() => {
    const k = [...document.querySelectorAll("button")].find((b) => b.textContent === "Laden");
    k.click();
  });
  await new Promise((r) => setTimeout(r, 200));
  const geladen = (await kasten(page)).find((p) => p.id === "ext:vnc");
  pruefe("nach dem Laden ist die Ansicht zurück",
    Math.abs(geladen.w - vorLaden.w) < 3 && Math.abs(geladen.h - vorLaden.h) < 3,
    `${Math.round(geladen.w)}×${Math.round(geladen.h)} vs. ${Math.round(vorLaden.w)}×${Math.round(vorLaden.h)}`);
  pruefe("der Dialog ist danach zu",
    (await page.$("input[placeholder^='Name der Ansicht']")) === null);

  pruefe("keine JS-Fehler im ganzen Durchlauf", fehlerImLog.length === 0,
    fehlerImLog.slice(0, 3).join(" | "));

  await browser.close();
  console.log(fehler ? `\n${fehler} Fehler` : "\nAlles grün");
  process.exit(fehler ? 1 : 0);
})().catch((e) => {
  console.error("Abbruch:", e);
  process.exit(1);
});
