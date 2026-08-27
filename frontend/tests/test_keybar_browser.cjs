/* Lässt sich die Tastenleiste unter dem Terminal am Handy wischen?
 *
 * Sie ist mit über zwanzig Knöpfen breiter als jedes Telefon (overflow-x-auto),
 * die rechten Tasten sind also nur per Wisch erreichbar. Jeder Knopf fängt
 * `pointerdown` mit preventDefault ab, damit er dem Terminal nicht den Fokus
 * klaut; dass das den Wisch NICHT abwürgt, hält dieser Test fest (der Verdacht
 * lag nahe, als das Scrollen am Handy nicht mehr ging — er war falsch).
 *
 * Aufruf: siehe test_workspace_browser.cjs (Chrome aus dem Container).
 */
const puppeteer = require("puppeteer");

const ZIEL =
  process.argv[2] || "http://127.0.0.1:8181/pruefstand.html?panel=keybar";

(async () => {
  const browser = await puppeteer.launch({
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  await page.setViewport({
    width: 390, height: 780, isMobile: true, hasTouch: true,
    deviceScaleFactor: 2,
  });
  await page.goto(ZIEL, { waitUntil: "networkidle0" });
  await page.waitForSelector("button");

  const masse = await page.evaluate(() => {
    const leiste = document.querySelector("div.overflow-x-auto");
    const knopf = leiste.querySelectorAll("button")[6].getBoundingClientRect();
    const l = leiste.getBoundingClientRect();
    return {
      breite: leiste.clientWidth, inhalt: leiste.scrollWidth,
      knopfX: knopf.x + knopf.width / 2, knopfY: knopf.y + knopf.height / 2,
      luecke: l.y + 2,
    };
  });
  console.log(`Leiste ${masse.breite} px breit, Inhalt ${masse.inhalt} px ` +
    `-> ${masse.inhalt > masse.breite ? "muss gewischt werden" : "passt"}`);

  const wischen = async (x, y) => {
    await page.evaluate(() => {
      document.querySelector("div.overflow-x-auto").scrollLeft = 0;
    });
    await page.touchscreen.touchStart(x, y);
    for (let i = 1; i <= 10; i++) await page.touchscreen.touchMove(x - i * 12, y);
    await page.touchscreen.touchEnd();
    await new Promise((r) => setTimeout(r, 400));
    return page.evaluate(
      () => document.querySelector("div.overflow-x-auto").scrollLeft,
    );
  };

  const aufKnopf = await wischen(masse.knopfX, masse.knopfY);
  const daneben = await wischen(masse.knopfX, masse.luecke); // Kante über den Knöpfen

  let fehler = 0;
  const pruefe = (was, ok, zusatz = "") => {
    if (!ok) fehler++;
    console.log(`  ${ok ? "✓" : "✗"} ${was}${zusatz ? `  ${zusatz}` : ""}`);
  };
  pruefe("Leiste ist breiter als das Telefon", masse.inhalt > masse.breite,
    `${masse.inhalt} px Inhalt`);
  pruefe("Wisch, der auf einem Knopf beginnt, scrollt", aufKnopf > 100,
    `scrollLeft = ${aufKnopf}`);
  pruefe("Wisch auf freier Fläche scrollt", daneben > 100, `scrollLeft = ${daneben}`);

  await browser.close();
  console.log(fehler ? `\n${fehler} Prüfung(en) fehlgeschlagen` : "\nalles grün");
  process.exit(fehler ? 1 : 0);
})();
