/* Prüft das Agenten-Panel in einem echten Browser (Issue #33).
 *
 * Nachrichten (kind message/answer/response) lagen zwar in der Inbox, waren im
 * Panel aber unsichtbar. Hier wird nachgestellt, was ein Mensch am Handy sehen
 * soll: den Abschnitt „Nachrichten“, den Zähler am Agenten-Kopf, das Aufklappen
 * langer Texte, das Archivieren per ✓ — und dass eine OFFENE Rückfrage bewusst
 * kein Archivieren-Kreuz bekommt (die gehört ins Banner, #22/#23).
 *
 * Das Backend ersetzt ein fetch-Doppel im Prüfstand (src/pruefstand.jsx).
 *
 * Aufruf (Host hat keine GUI-Libs, deshalb Chrome aus dem Container):
 *
 *   cd frontend && npx vite build --config tests/vite.pruefstand.mjs
 *   python3 -m http.server 8177 --directory /tmp/pruefstand &
 *   docker run --rm --network=host -e NODE_PATH=/usr/src/app/node_modules \
 *     -v "$PWD/tests:/t" zenika/alpine-chrome:with-puppeteer \
 *     node /t/test_agents_browser.cjs "http://127.0.0.1:8177/pruefstand.html?panel=agenten"
 *
 * Der Port muss frei sein (--network=host, siehe test_workspace_browser.cjs).
 */
const puppeteer = require("puppeteer");

const ZIEL =
  process.argv[2] || "http://127.0.0.1:8177/pruefstand.html?panel=agenten";

let fehler = 0;
const pruefe = (was, ok, zusatz = "") => {
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${zusatz ? `  ${zusatz}` : ""}`);
};

const text = (page) => page.$eval("body", (b) => b.innerText);

(async () => {
  const browser = await puppeteer.launch({
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  // Handy-Format: der Abschnitt muss auch schmal lesbar bleiben (#31).
  await page.setViewport({ width: 390, height: 780 });
  const fehlerImLog = [];
  const belanglos = (t) => /favicon/i.test(t);
  page.on("pageerror", (e) => fehlerImLog.push(String(e)));
  page.on("console", (m) => {
    if (m.type() === "error" && !belanglos(m.text())) fehlerImLog.push(m.text());
  });

  await page.goto(ZIEL, { waitUntil: "networkidle0" });
  await page.waitForFunction(() => document.body.innerText.includes("Nachrichten"));

  console.log("Anzeige:");
  const t = await text(page);
  pruefe("Abschnitt „Nachrichten“ mit Zähler", /Nachrichten \(2\)/.test(t));
  pruefe("Nachricht ist sichtbar", t.includes("Bericht liegt im Projektordner."));
  pruefe("Absender steht dabei", t.includes("deverp"));
  pruefe("Rückfrage erscheint als eigene Art", t.includes("Rückfrage"));
  pruefe("Tasks bleiben eigener Abschnitt", /Inbox \(1\)/.test(t));

  // Zähler am Agenten-Kopf: ohne Aufklappen sehen, wo etwas liegt (#33).
  const zaehler = await page.$$eval("button", (bs) =>
    bs
      .filter((b) => b.textContent.trim().startsWith("PMNB029"))
      .map((b) => b.textContent.trim()),
  );
  pruefe("Zähler am Agenten-Kopf", zaehler.some((z) => z.includes("2")), zaehler.join("|"));

  // Kein waagerechtes Scrollen am Handy — lange Texte brechen um.
  const ueberbreit = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  pruefe("kein waagerechtes Scrollen (390 px)", !ueberbreit);

  // Offene Rückfrage: kein ✓ — die wird im Banner beantwortet, nicht hier
  // weggeräumt (sonst wartet der daran geparkte Task ewig, #17/#23).
  const haken = await page.$$eval("button", (bs) =>
    bs.filter((b) => b.textContent.trim() === "✓").length,
  );
  pruefe("nur die echte Nachricht hat ein ✓", haken === 1, `gefunden: ${haken}`);

  // Archivieren: ✓ schickt den POST und die Nachricht verschwindet.
  await page.$$eval("button", (bs) => {
    const b = bs.find((x) => x.textContent.trim() === "✓");
    b.click();
  });
  await page.waitForFunction(
    () => !document.body.innerText.includes("Bericht liegt im Projektordner."),
    { timeout: 5000 },
  );
  const posts = await page.evaluate(() => window.__posts);
  pruefe(
    "✓ ruft den Archiv-Endpunkt",
    posts.some((u) => /\/api\/agents\/PMNB029\/inbox\/message-1\/read$/.test(u)),
    posts.join(" "),
  );
  pruefe("Zähler zählt runter", /Nachrichten \(1\)/.test(await text(page)));

  pruefe("keine Fehler in der Konsole", fehlerImLog.length === 0, fehlerImLog.join(" | "));

  await browser.close();
  console.log(fehler ? `\n${fehler} Prüfung(en) fehlgeschlagen` : "\nalles grün");
  process.exit(fehler ? 1 : 0);
})();
