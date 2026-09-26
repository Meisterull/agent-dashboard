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

  // Lauf-Daten des Watchers (Issues #36–#39, #41): sichtbar OHNE das
  // log-Feld öffnen zu müssen — am Handy war das praktisch unsichtbar.
  pruefe("liegengebliebene Post wird gemeldet (#36)",
    /2 ungelesene Einträge — die Automatik reagiert darauf nicht/.test(t));
  pruefe("Timeout-Abbruch ist als fortsetzbar gekennzeichnet (#38)",
    t.includes("abgebrochen · fortsetzbar"));
  pruefe("verweigerte Aufrufe kennzeichnen die Antwort (#39)", t.includes("mit Einschränkungen"));
  pruefe("Liste zeigt nur den Anschnitt (#41)", t.includes("angeschnitten…") && !t.includes("VOLLER TEXT"));
  await page.evaluate(() => {
    const el = [...document.querySelectorAll("span")].find((s) => s.innerText === "task-9");
    el.closest("div.cursor-pointer").click();
  });
  await page.waitForFunction(() => document.body.innerText.includes("VOLLER TEXT"), { timeout: 5000 });
  const auf = await text(page);
  pruefe("Aufklappen lädt den vollen Text nach (#41)", auf.includes("VOLLER TEXT der Antwort"));
  pruefe("…nennt den verweigerten Befehl (#39)", auf.includes("cat /etc/shadow"));
  pruefe("…und die Sitzung zum Übernehmen (#37)",
    auf.includes("claude --resume 9a2d20b8-b893-43c0-8867-a80963446b17"));

  // Ereignis-Zeitleiste (Beobachtbarkeit): Kopfzeile mit Problem-Zähler und
  // letztem Eintrag, aufgeklappt alle Einträge farbig, „nur Probleme", und
  // ein Eintrag mit Task klappt den Task auf (nicht zu — idempotent).
  console.log("Ereignisse:");
  const kopf = await text(page);
  pruefe("Kopfzeile: Zähler + Problem-Abzeichen + letzter Eintrag",
    /Ereignisse \(4\)/.test(kopf) && kopf.includes("⚠ 2") && kopf.includes("1 verweigerte Aufrufe: Bash"));
  pruefe("zugeklappt: Watcher-Start noch nicht sichtbar", !kopf.includes("Watcher gestartet"));
  await page.$$eval("button", (bs) => bs.find((b) => (b.title || "") === "Ereignisse anzeigen").click());
  await page.waitForFunction(() => document.body.innerText.includes("Watcher gestartet"), { timeout: 5000 });
  const zl = await text(page);
  pruefe("aufgeklappt: Lauf mit Dauer/Kosten/Kontext, Sitzungsgrund, Watcher-Start",
    zl.includes("6 min · 5k Tok · 0.12 $") && zl.includes("über Grenze 150k") && zl.includes("Watcher gestartet"));
  const gelb = await page.$$eval("div.bg-amber-50", (ds) => ds.length);
  pruefe("Warnungen sind gelb hinterlegt", gelb === 2, `gefunden: ${gelb}`);
  await page.$$eval("button", (bs) => bs.find((b) => b.textContent.trim() === "nur Probleme").click());
  await page.waitForFunction(() => !document.body.innerText.includes("Watcher gestartet"), { timeout: 5000 });
  pruefe("„nur Probleme“ blendet info aus", !(await text(page)).includes("Lauf error"));
  await page.$$eval("button", (bs) => bs.find((b) => b.textContent.trim() === "nur Probleme").click());
  await page.waitForFunction(() => document.body.innerText.includes("Watcher gestartet"), { timeout: 5000 });
  // Task-9 ist oben aufgeklappt → erst zuklappen (die LETZTE „task-9"-Marke
  // ist die Task-Zeile; die Zeitleiste trägt die ID ebenfalls), dann über
  // das Ereignis öffnen
  await page.evaluate(() => {
    const el = [...document.querySelectorAll("span")].filter((s) => s.innerText === "task-9").pop();
    el.closest("div.cursor-pointer").click();
  });
  await page.waitForFunction(() => !document.body.innerText.includes("VOLLER TEXT"), { timeout: 5000 });
  await page.evaluate(() => {
    const z = [...document.querySelectorAll("div[title]")].find((d) => d.title === "Task task-9 aufklappen");
    z.click();
  });
  await page.waitForFunction(() => document.body.innerText.includes("VOLLER TEXT"), { timeout: 5000 });
  pruefe("Ereignis-Klick klappt den Task auf", true);
  await page.evaluate(() => {
    const z = [...document.querySelectorAll("div[title]")].find((d) => d.title === "Task task-9 aufklappen");
    z.click();
  });
  await new Promise((r) => setTimeout(r, 300));
  pruefe("…und ein zweiter Klick klappt ihn NICHT zu", (await text(page)).includes("VOLLER TEXT"));
  pruefe("erp ohne Ereignisse: kein Absturz", true);

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
