/* Prüft die Austausch-Ordner im Datei-Panel in einem echten Browser.
 *
 * Nachgestellt wird, was ein Mensch am Handy tut: auf dem Tab einer Maschine
 * über 📥 den Austausch-Ordner einschalten (samt abgelehntem Pfad), in den
 * Ordner springen, eine Datei über 📤 an eine ANDERE Maschine schicken, und
 * wieder ausschalten. Dazu die Grenzen: der Workspace kennt beides nicht,
 * Ordner haben kein 📤, die eigene Maschine und Token-Maschinen sind nie
 * Empfänger, eine zu große Datei lässt sich gar nicht erst abschicken.
 *
 * Das Backend ersetzt ein fetch-Doppel im Prüfstand (src/pruefstand.jsx,
 * ?panel=dateien); es schneidet alle schreibenden Aufrufe in window.__aufrufe
 * mit — daran wird geprüft, was wirklich an die API ginge.
 *
 * Aufruf (Host hat keine GUI-Libs, deshalb Chrome aus dem Container):
 *
 *   cd frontend && npx vite build --config tests/vite.pruefstand.mjs
 *   python3 -m http.server 8177 --directory /tmp/pruefstand &
 *   docker run --rm --network=host -e NODE_PATH=/usr/src/app/node_modules \
 *     -v "$PWD/tests:/t" zenika/alpine-chrome:with-puppeteer \
 *     node /t/test_dateien_browser.cjs "http://127.0.0.1:8177/pruefstand.html?panel=dateien"
 *
 * Der Port muss frei sein (--network=host, siehe test_workspace_browser.cjs).
 */
const puppeteer = require("puppeteer");

const ZIEL =
  process.argv[2] || "http://127.0.0.1:8177/pruefstand.html?panel=dateien";

let fehler = 0;
const pruefe = (was, ok, zusatz = "") => {
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${zusatz ? `  ${zusatz}` : ""}`);
};

const text = (page) => page.$eval("body", (b) => b.innerText);
const warte = (page, was) =>
  page.waitForFunction((w) => document.body.innerText.includes(w), { timeout: 5000 }, was);
const warteWeg = (page, was) =>
  page.waitForFunction((w) => !document.body.innerText.includes(w), { timeout: 5000 }, was);

// Knopf über seinen sichtbaren Text bzw. sein title-Attribut antippen.
const tippe = (page, was) =>
  page.evaluate((w) => {
    const b = [...document.querySelectorAll("button")].find(
      (k) => k.innerText.trim() === w || (k.title || "").startsWith(w),
    );
    if (!b) throw new Error(`kein Knopf: ${w}`);
    b.click();
  }, was);
const gibtKnopf = (page, was) =>
  page.evaluate(
    (w) =>
      [...document.querySelectorAll("button")].some(
        (k) => k.innerText.trim() === w || (k.title || "").startsWith(w),
      ),
    was,
  );
// 📤 in der Zeile einer bestimmten Datei
const sendeKnopf = (page, datei) =>
  page.evaluate((d) => {
    const zeile = [...document.querySelectorAll("div.group")].find((z) => z.innerText.includes(d));
    const b = zeile && [...zeile.querySelectorAll("button")].find((k) => k.innerText.trim() === "📤");
    if (b) b.click();
    return !!b;
  }, datei);
const schreibe = async (page, selektor, wert) => {
  await page.click(selektor, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type(selektor, wert);
};
const aufrufe = (page) => page.evaluate(() => window.__aufrufe);

(async () => {
  const browser = await puppeteer.launch({
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 780 }); // Handy-Format
  const fehlerImLog = [];
  const belanglos = (t) => /favicon/i.test(t);
  page.on("pageerror", (e) => fehlerImLog.push(String(e)));
  page.on("console", (m) => {
    if (m.type() === "error" && !belanglos(m.text())) fehlerImLog.push(m.text());
  });

  await page.goto(ZIEL, { waitUntil: "networkidle0" });
  await warte(page, "notebook");

  console.log("Workspace kennt keinen Austausch:");
  pruefe("kein 📥 auf dem Workspace-Tab", !(await gibtKnopf(page, "Austausch-Ordner")));

  console.log("Einschalten auf dem Tab der Maschine:");
  await tippe(page, "erp");
  await warte(page, "export.csv");
  pruefe("📥 ist da und zeigt »noch nicht eingerichtet«", await gibtKnopf(page, "Austausch-Ordner einrichten"));
  pruefe("Ordner-Zeile hat kein 📤", !(await sendeKnopf(page, "projekt")));
  await tippe(page, "Austausch-Ordner einrichten");
  await warte(page, "Austausch-Ordner · erp");
  const vorgabe = await page.$eval("form input", (i) => i.value);
  pruefe("Pfad-Vorgabe ist der Standardordner", vorgabe === "austausch", vorgabe);

  await schreibe(page, "form input", "../woanders");
  await tippe(page, "Einschalten");
  await warte(page, "nicht erlaubt");
  pruefe("abgelehnter Pfad: Fehler sichtbar, Dialog bleibt offen",
    (await text(page)).includes("Austausch-Ordner · erp"));

  await schreibe(page, "form input", "daten/austausch");
  await tippe(page, "Einschalten");
  await warteWeg(page, "Austausch-Ordner · erp");
  const put = (await aufrufe(page)).filter((a) => a.methode === "PUT").pop();
  pruefe("PUT trägt den eingegebenen Ordner",
    put && put.url === "/api/austausch/erp" && put.body.ordner === "daten/austausch",
    JSON.stringify(put));
  await page.waitForFunction(() =>
    [...document.querySelectorAll("button")].some((k) => (k.title || "").includes("(eingeschaltet)")));
  pruefe("📥 zeigt danach »eingeschaltet«", true);

  console.log("Eingeschaltet: in den Ordner springen:");
  await tippe(page, "Austausch-Ordner (eingeschaltet)");
  await warte(page, "Eingeschaltet:");
  const t1 = await text(page);
  pruefe("Pfad auf der Maschine steht da", t1.includes("/home/u/daten/austausch"));
  pruefe("Hinweis: Ausschalten löscht nichts", t1.includes("löscht nichts auf der Maschine"));
  pruefe("Knopf heißt jetzt »Pfad ändern«", await gibtKnopf(page, "Pfad ändern"));
  await tippe(page, "Ordner öffnen");
  await page.waitForFunction(() =>
    [...document.querySelectorAll("span.font-mono")].some((s) => s.innerText === "/home/u/daten/austausch"));
  pruefe("Pfadzeile steht im Austausch-Ordner", true);

  console.log("Datei an eine andere Maschine senden:");
  await tippe(page, "Workspace");
  await tippe(page, "erp");
  await warte(page, "export.csv");
  pruefe("Datei-Zeile hat 📤", await sendeKnopf(page, "export.csv"));
  await warte(page, "An Maschine senden");
  const ziele = await page.$$eval("select option", (o) => o.map((x) => x.value));
  pruefe("Empfänger: nur die ANDERE Maschine mit Ordner (nicht selbst, kein Token)",
    JSON.stringify(ziele) === JSON.stringify(["deverp"]), JSON.stringify(ziele));
  await page.type("form input", "bitte einspielen");
  await tippe(page, "Senden");
  await warte(page, "Zugestellt an deverp:");
  const post = (await aufrufe(page)).filter((a) => a.url === "/api/austausch/senden").pop();
  pruefe("POST: von/an/pfade/nachricht stimmen",
    post && post.body.von === "erp" && post.body.an === "deverp" &&
      JSON.stringify(post.body.pfade) === JSON.stringify(["/home/u/export.csv"]) &&
      post.body.nachricht === "bitte einspielen",
    JSON.stringify(post && post.body));
  pruefe("Zielpfad wird gezeigt",
    (await text(page)).includes("/home/u/austausch/von-erp/export.csv"));
  await tippe(page, "Fertig");
  await warteWeg(page, "An Maschine senden");

  console.log("Zu große Datei:");
  await sendeKnopf(page, "riesig.iso");
  await warte(page, "zu groß");
  const gesperrt = await page.evaluate(() =>
    [...document.querySelectorAll("button")].find((k) => k.innerText.trim() === "Senden").disabled);
  pruefe("Senden ist gesperrt", gesperrt);
  await tippe(page, "Abbrechen");

  console.log("Token-Maschine:");
  await tippe(page, "notebook");
  await page.waitForFunction(() =>
    [...document.querySelectorAll("button")].some((k) => (k.title || "").startsWith("Austausch-Ordner")));
  await tippe(page, "Austausch-Ordner einrichten");
  await warte(page, "keine SSH-Verbindung");
  pruefe("erklärt, warum es nicht geht — ohne Einschalten-Knopf", !(await gibtKnopf(page, "Einschalten")));
  await tippe(page, "Schließen");

  console.log("Ausschalten:");
  await tippe(page, "deverp");
  await warte(page, "export.csv");
  await tippe(page, "Austausch-Ordner (eingeschaltet)");
  await warte(page, "Eingeschaltet:");
  await tippe(page, "Ausschalten");
  await warteWeg(page, "Austausch-Ordner · deverp");
  const del = (await aufrufe(page)).filter((a) => a.methode === "DELETE").pop();
  pruefe("DELETE auf die richtige Maschine", del && del.url === "/api/austausch/deverp", JSON.stringify(del));
  await tippe(page, "erp");
  await warte(page, "export.csv");
  // deverp ist aus, erp selbst zählt nicht → kein Empfänger mehr
  await sendeKnopf(page, "export.csv");
  await warte(page, "Noch keine andere Maschine");
  pruefe("ohne Empfänger: Hinweis, wo man einschaltet", true);
  await tippe(page, "Schließen");

  pruefe("keine Fehler in der Konsole", fehlerImLog.length === 0, fehlerImLog.join(" | "));

  await browser.close();
  console.log(fehler ? `\n${fehler} Prüfung(en) fehlgeschlagen` : "\nalle Prüfungen bestanden");
  process.exit(fehler ? 1 : 0);
})().catch((e) => {
  console.error("Testlauf abgebrochen:", e);
  process.exit(2);
});
