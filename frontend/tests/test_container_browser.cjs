/* Container-Fenster (Issue #49) im echten Browser, gegen ein fetch-Doppel.
 *
 * Prüft: Karten mit Abzeichen Docker/LXC/VM, gestoppte Container ohne Shell/
 * Dateien, VM nur Ansicht, Rechnerwahl, Filter, Shell-Knopf → Ereignis
 * "terminal:oeffnen" mit der richtigen sid, Logs mit Zeilenwahl, Dateien
 * blättern/ansehen, Fehlermeldung bei 403 — am Handy-Format.
 *
 *   cd frontend && npx vite build --config tests/vite.pruefstand.mjs
 *   python3 -m http.server 8177 --directory /tmp/pruefstand &
 *   docker run --rm --network=host -e NODE_PATH=/usr/src/app/node_modules \
 *     -v "$PWD/tests:/t" zenika/alpine-chrome:with-puppeteer \
 *     node /t/test_container_browser.cjs "http://127.0.0.1:8177/pruefstand.html?panel=container"
 */
const puppeteer = require("puppeteer");

const ZIEL = process.argv[2] || "http://127.0.0.1:8177/pruefstand.html?panel=container";
let fehler = 0;
const pruefe = (was, ok, zusatz = "") => {
  if (!ok) fehler++;
  console.log(`  ${ok ? "✓" : "✗"} ${was}${zusatz ? `  ${zusatz}` : ""}`);
};
const warte = (ms) => new Promise((r) => setTimeout(r, ms));

const karten = (page) =>
  page.$$eval("[data-container]", (els) =>
    els.map((e) => ({
      id: e.dataset.container,
      text: e.textContent,
      knoepfe: Object.fromEntries(
        [...e.querySelectorAll("[data-aktion]")].map((b) => [b.dataset.aktion, !b.disabled]),
      ),
    })),
  );

(async () => {
  const browser = await puppeteer.launch({ args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 800, isMobile: true, hasTouch: true });
  const log = [];
  page.on("pageerror", (e) => log.push(String(e)));
  page.on("console", (m) => m.type() === "error" && !/favicon/i.test(m.text()) && log.push(m.text()));
  await page.goto(ZIEL, { waitUntil: "networkidle0" });
  await page.evaluate(() => localStorage.clear());
  await page.reload({ waitUntil: "networkidle0" });
  await page.waitForSelector("[data-container]");

  console.log("Rechner „pve“ (zuerst alphabetisch):");
  let k = await karten(page);
  pruefe("drei Karten: 2 LXC + 1 VM", k.map((x) => x.id).join(",") === "lxc:101,lxc:102,vm:200", k.map((x) => x.id).join(","));
  pruefe("laufender LXC: Shell/Dateien/Logs an", JSON.stringify(k[0].knoepfe) === '{"shell":true,"dateien":true,"logs":true}', JSON.stringify(k[0].knoepfe));
  pruefe("gestoppter LXC: alles aus (Logs brauchen pct exec)", Object.values(k[1].knoepfe).every((x) => !x), JSON.stringify(k[1].knoepfe));
  pruefe("VM: keine Knöpfe, „nur Ansicht“", Object.keys(k[2].knoepfe).length === 0 && k[2].text.includes("nur Ansicht"));
  pruefe("Abzeichen LXC/VM", k[0].text.includes("LXC") && k[2].text.includes("VM"));

  await page.click('[data-container="lxc:101"] [data-aktion="shell"]');
  let shells = await page.evaluate(() => window.__shells);
  pruefe("Shell → terminal:oeffnen {pve, c:lxc:101}", JSON.stringify(shells) === '[{"name":"pve","sid":"c:lxc:101"}]', JSON.stringify(shells));

  console.log("\nRechner wechseln auf „server“:");
  await page.select("[data-rechner]", "server");
  await page.waitForSelector('[data-container="docker:nginx"]');
  k = await karten(page);
  pruefe("Docker-Karten", k.map((x) => x.id).join(",") === "docker:nginx,docker:alt");
  pruefe("gestoppter Docker: Logs gehen trotzdem", JSON.stringify(k[1].knoepfe) === '{"shell":false,"dateien":false,"logs":true}', JSON.stringify(k[1].knoepfe));
  pruefe("Ports sichtbar", k[0].text.includes("0.0.0.0:80->80/tcp"));
  await page.type("input[placeholder]", "ngi");
  await warte(100);
  k = await karten(page);
  pruefe("Filter „ngi“ → nur nginx", k.length === 1 && k[0].id === "docker:nginx");

  console.log("\nLogs:");
  await page.click('[data-container="docker:nginx"] [data-aktion="logs"]');
  await page.waitForFunction(() => document.querySelector("[data-logtext]")?.textContent.includes("zeile"));
  let text = await page.$eval("[data-logtext]", (e) => e.textContent);
  pruefe("letzte 200 Zeilen geladen", text.includes("nginx zeile 1 von 200"), text.slice(0, 40));
  await page.select("[data-zeilen]", "1000");
  await page.waitForFunction(() => document.querySelector("[data-logtext]")?.textContent.includes("von 1000"));
  pruefe("Wechsel auf 1000 Zeilen lädt neu", true);
  const abrufe = await page.evaluate(() => window.__abrufe.filter((a) => a.includes("/logs")));
  pruefe("Logs-URL trägt Quelle/Ziel/Zeilen", abrufe.at(-1) === "/api/container/server/docker/nginx/logs?zeilen=1000", abrufe.at(-1));
  await page.click("[data-zurueck]");

  console.log("\nDateien:");
  await page.waitForSelector('[data-container="docker:nginx"] [data-aktion="dateien"]');
  await page.click('[data-container="docker:nginx"] [data-aktion="dateien"]');
  await page.waitForSelector('[data-eintrag="etc"]');
  await page.click('[data-eintrag="etc"] button');
  await page.waitForSelector('[data-eintrag="nginx.conf"]');
  pruefe("in /etc geblättert", (await page.$eval("[data-pfad]", (e) => e.textContent)) === "/etc");
  await page.click('[data-eintrag="nginx.conf"] button');
  await page.waitForSelector("[data-dateitext]");
  pruefe("Textdatei angesehen", (await page.$eval("[data-dateitext]", (e) => e.textContent)) === "inhalt von /etc/nginx.conf");
  const dl = await page.$eval("[data-dateitext]", (e) => e.parentElement.querySelector("a").getAttribute("href"));
  pruefe("Download-Link", dl === "/api/container/server/docker/nginx/download?pfad=%2Fetc%2Fnginx.conf", dl);
  await page.click("[data-zurueck]");
  await page.waitForSelector('[data-container="docker:nginx"] [data-aktion="dateien"]');
  await page.click('[data-container="docker:nginx"] [data-aktion="dateien"]');
  await page.waitForSelector('[data-eintrag="geheim"]');
  await page.click('[data-eintrag="geheim"] button');
  await page.waitForSelector("[data-fehler]");
  pruefe("403 → Fehlermeldung statt leerer Liste", (await page.$eval("[data-fehler]", (e) => e.textContent)).includes("Berechtigung"));

  const breit = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  pruefe("kein waagrechtes Scrollen am Handy", breit);
  pruefe("keine Fehler in der Konsole", log.length === 0, log.join(" | "));
  await browser.close();
  console.log(fehler ? `\n${fehler} Fehler` : "\nAlles grün");
  process.exit(fehler ? 1 : 0);
})();
