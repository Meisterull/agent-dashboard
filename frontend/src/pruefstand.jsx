// Prüfstand: mountet einzelne Teile der Oberfläche OHNE Backend, Login oder
// Chat, damit sie sich in einem echten Browser prüfen lassen.
//
//   ?panel=workspace  (Default)  Fensteranordnung  -> tests/test_workspace_browser.cjs
//   ?panel=agenten               Agenten-Panel     -> tests/test_agents_browser.cjs
//   ?panel=keybar                Tastenleiste      -> tests/test_keybar_browser.cjs
//   ?panel=terminal              xterm + Leiste    -> tests/test_terminal_browser.cjs
//   ?panel=dateien               Datei-Panel       -> tests/test_dateien_browser.cjs
//   ?panel=terminalfenster       Terminal verdoppeln -> tests/test_terminalfenster_browser.cjs
//   ?panel=schrift               Terminal-Schrift, Unicode 11, Mindestspalten -> tests/test_schrift_browser.cjs
//   ?panel=tastatur              Tastatur ohne Größenänderung -> tests/test_tastatur_browser.cjs
//   ?panel=langdruck             Langer Druck → Zeile/Absatz  -> tests/test_langdruck_browser.cjs
//   ?panel=fenster               Fenster ein-/ausblenden (#50) -> tests/test_fenster_browser.cjs
//   ?panel=container             Container-Fenster (#49)       -> tests/test_container_browser.cjs
//
// Temporäre Datei — gehört nicht in den Auslieferungs-Build.
import { createRoot } from "react-dom/client";
import AgentsPanel from "./components/AgentsPanel";
import FilesPanel from "./components/FilesPanel";
import KeyBar from "./components/KeyBar";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import "@xterm/xterm/css/xterm.css";
import { SCHRIFT_FAMILIE, schriftenLaden, schriftFuerSpalten } from "./termSchrift";
import { tastaturRahmen } from "./termTastatur";
import { langDruck, pufferZeileBei } from "./termLangdruck";
import { logischeZeilen, textBei } from "./termAbsatz";
import { fittenOhneSprung, wischScrollen } from "./termScroll";
import { imeEingabe } from "./termEingabe";
import { istSichtbar } from "./termVerbindung";
import Workspace from "./components/Workspace";
import TerminalPanel, { TerminalVorrat, useTerminalFenster } from "./components/TerminalPanel";
import { panelId } from "./terminalFenster";
import FensterMenue from "./components/FensterAuswahl";
import ContainerPanel from "./components/ContainerPanel";
import { LS_VERSTECKT, umschalten, versteckteLesen, versteckteText } from "./fensterSichtbar";
import { useEffect as useEffectP, useState as useStateP } from "react";
import "./index.css";

const platzhalter = (name) => (
  <div className="p-2 text-xs text-slate-500">{name}</div>
);

// Ein echtes iframe, damit der Fokus-Test etwas zum Hineinklicken hat.
// KEIN autofocus: Sonst zieht der Rahmen den Fokus schon beim Laden zu sich,
// und der spätere Klick hinein ist gar kein Fokuswechsel mehr — der Test
// prüfte dann nichts.
const rahmen = (
  <iframe
    title="ext"
    className="h-full w-full flex-1 border-0 bg-white"
    srcDoc="<html><body style='margin:0;height:100vh;background:#123'><button id='drin' style='width:100%;height:100%'>VNC</button></body></html>"
  />
);

const panels = [
  { id: "dateien", title: "Dateien", body: platzhalter("Dateien") },
  { id: "chat", title: "Chat", body: platzhalter("Chat") },
  { id: "terminal", title: "Terminal", body: platzhalter("Terminal") },
  { id: "agenten", title: "Agenten", body: platzhalter("Agenten") },
  { id: "ext:vnc", title: "VNC", body: rahmen },
];

// --- Agenten-Panel gegen eine erfundene Mailbox (Issue #33) ----------------
// Statt eines echten Backends antwortet ein fetch-Doppel. Es liefert genau die
// Form, die /api/agents/{name}/tasks liefert — inklusive `messages` — und
// merkt sich POSTs, damit der Test das Archivieren nachweisen kann.
const nachrichten = [
  {
    id: "message-1",
    kind: "message",
    sender: "deverp",
    text: "Bericht liegt im Projektordner.",
    status: "pending",
    created_at: new Date().toISOString(),
  },
  {
    id: "question-1",
    kind: "question",
    sender: "erp",
    text: "Soll ich die alte Tabelle löschen?",
    status: "needs_confirm",
    created_at: new Date().toISOString(),
  },
];
window.__posts = [];
// Antwort mit Lauf-Daten des Watchers (Issues #37–#39): verweigerter Aufruf,
// Timeout, Sitzung zum Übernehmen — und in der Liste nur angeschnitten (#41).
const antwortMitLauf = {
  task_id: "task-9",
  status: "error",
  result: "angeschnitten…",
  gekuerzt: true,
  log: "[watcher] Timeout: 900s ohne Lebenszeichen",
  responded_at: new Date().toISOString(),
  lauf: {
    sitzung: "fortgesetzt",
    session_id: "9a2d20b8-b893-43c0-8867-a80963446b17",
    kontext: 84000,
    timeout: "Timeout: 900s ohne Lebenszeichen",
    fortsetzbar: true,
    verweigert: [{ tool: "Bash", eingabe: "cat /etc/shadow" }],
  },
};

function fetchDoppel(url, opt = {}) {
  const json = (data) =>
    Promise.resolve({ ok: true, status: 200, json: async () => data });
  if (opt.method === "POST") {
    window.__posts.push(url);
    if (url.includes("/read")) {
      const id = url.split("/inbox/")[1].split("/")[0];
      const weg = nachrichten.findIndex((m) => m.id === id);
      if (weg >= 0) nachrichten.splice(weg, 1);
      return json({ archived: id });
    }
    return json({});
  }
  if (url === "/api/rollen") return json({ rollen: [] });
  if (url === "/api/zeitplaene") return json({ plaene: [] });
  if (url === "/api/agents") return json({ agents: ["PMNB029", "erp"] });
  // Automatik an, reagiert aber nur auf Tasks → die zwei Nachrichten oben
  // bleiben liegen, und das Panel muss es sagen (Issue #36).
  if (url === "/api/automatik")
    return json({
      notaus: false,
      agents: {
        PMNB029: {
          gewuenscht: true, startbar: true, status: "an", detail: "", seit: null,
          log: [], gesperrt: false, weckt: ["task"], ungeweckt: 2,
        },
      },
    });
  // Ereignis-Log (Beobachtbarkeit): drei Einträge, einer mit Task
  if (url.startsWith("/api/agents/PMNB029/ereignisse")) {
    const probleme = new URL(url, location.origin).searchParams.get("probleme") === "1";
    const alle = [
      { zeit: new Date().toISOString(), art: "verweigert", schwere: "warnung", task_id: "task-9",
        text: "1 verweigerte Aufrufe: Bash", details: { anzahl: 1, werkzeuge: ["Bash"] } },
      { zeit: new Date(Date.now() - 60000).toISOString(), art: "sitzung", schwere: "warnung", task_id: "task-9",
        text: "neue Sitzung: Kontext 164k über Grenze 150k", details: { kontext_neustart: true } },
      { zeit: new Date(Date.now() - 120000).toISOString(), art: "lauf", schwere: "info", task_id: "task-9",
        text: "Lauf error · 6 min · 5k Tok · 0.12 $ · Kontext 164k", details: { dauer: 379 } },
      { zeit: new Date(Date.now() - 3600000).toISOString(), art: "watcher_start", schwere: "info", task_id: null,
        text: "Watcher gestartet (MCP :9100)", details: {} },
    ];
    const eintraege = probleme ? alle.filter((e) => e.schwere !== "info") : alle;
    return json({ eintraege, mehr: false, gesamt: alle.length });
  }
  if (url.startsWith("/api/agents/erp/ereignisse"))
    return json({ eintraege: [], mehr: false, gesamt: 0 });
  // Einzelabruf der ungekürzten Antwort (Issue #41)
  if (url.startsWith("/api/agents/PMNB029/outbox/task-9"))
    return json({ ...antwortMitLauf, result: "VOLLER TEXT der Antwort", gekuerzt: undefined });
  if (url.startsWith("/api/agents/PMNB029/tasks"))
    return json({
      agent: "PMNB029",
      // requeues: zwei verstummte Watcher-Läufe → Abzeichen „↩ 2/3" (#47)
      inbox: [{ task_id: "task-1", status: "pending", instruction: "bau das", requeues: 2 }],
      outbox: [antwortMitLauf],
      messages: nachrichten,
    });
  if (url.startsWith("/api/agents/erp/tasks"))
    return json({ agent: "erp", inbox: [], outbox: [], messages: [] });
  return json({});
}

// --- Datei-Panel: Austausch-Ordner je Maschine ------------------------------
// Drei Maschinen: erp (Ordner aus), deverp (Ordner an), notebook (Token, kein
// SSH). Das Doppel führt den Schalter-Zustand mit und schneidet jeden
// schreibenden Aufruf samt Body mit (window.__aufrufe).
const austauschStand = {
  erp: { moeglich: true, aktiv: false, ordner: null, pfad: null },
  deverp: { moeglich: true, aktiv: true, ordner: "austausch", pfad: "/home/u/austausch" },
  notebook: { moeglich: false, aktiv: false, ordner: null, pfad: null },
};
window.__aufrufe = [];

function fetchDoppelDateien(url, opt = {}) {
  const json = (data, status = 200) =>
    Promise.resolve({ ok: status < 400, status, json: async () => data });
  const methode = opt.method || "GET";
  const body = opt.body ? JSON.parse(opt.body) : null;
  if (methode !== "GET") window.__aufrufe.push({ url, methode, body });

  if (url === "/api/connections")
    return json({ connections: Object.keys(austauschStand).map((name) => ({ name })) });
  if (url === "/api/austausch" && methode === "GET")
    return json({
      maschinen: Object.entries(austauschStand).map(([name, e]) => ({ name, ...e })),
      max_mb: 200,
      standard_ordner: "austausch",
    });
  if (url === "/api/austausch/senden")
    return json({
      von: body.von,
      an: body.an,
      zugestellt: [
        { name: "export.csv", pfad: `/home/u/austausch/von-${body.von}/export.csv`, bytes: 1234 },
      ],
      fehler: [],
      gemeldet: true,
    });
  if (url.startsWith("/api/austausch/")) {
    const name = decodeURIComponent(url.split("/")[3]);
    if (methode === "DELETE") {
      austauschStand[name] = { ...austauschStand[name], aktiv: false, ordner: null, pfad: null };
      return json({ name, aktiv: false });
    }
    if ((body.ordner || "").includes(".."))
      return json({ detail: "»..« ist im Ordner-Pfad nicht erlaubt" }, 400);
    const ordner = body.ordner || "austausch";
    austauschStand[name] = { moeglich: true, aktiv: true, ordner, pfad: `/home/u/${ordner}` };
    return json({ name, aktiv: true, ordner, pfad: `/home/u/${ordner}` });
  }
  // Suche (Issue #45): Namen bzw. Inhalt, beides ab /home/u
  if (url.includes("/suche?")) {
    const sp = new URL(url, location.origin).searchParams;
    const q = sp.get("q") || "";
    window.__aufrufe.push({ url: url.split("?")[0], methode: "GET", body: { q, inhalt: sp.get("inhalt"), path: sp.get("path") } });
    if (sp.get("inhalt") === "1")
      return json({
        path: "/home/u", q, inhalt: true, gekuerzt: false, dauer: 0.1,
        treffer: [{ name: "fehler.log", path: "/home/u/projekt/logs/fehler.log", type: "file",
                    size: null, zeile: 2, text: "Hier steht ein Fehler-String" }],
      });
    return json({
      path: "/home/u", q, inhalt: false, gekuerzt: true, dauer: 0.1,
      treffer: [
        { name: "logs", path: "/home/u/projekt/logs", type: "dir", size: null },
        { name: "fehler.log", path: "/home/u/projekt/logs/fehler.log", type: "file", size: 36 },
      ],
    });
  }
  if (url.startsWith("/api/files")) return json({ path: "", entries: [] });
  if (url.startsWith("/api/remote/")) {
    const pfad = new URL(url, location.origin).searchParams.get("path") || "/home/u";
    if (pfad !== "/home/u") return json({ path: pfad, parent: "/home/u", entries: [] });
    return json({
      path: "/home/u",
      parent: "/home",
      entries: [
        { name: "projekt", path: "/home/u/projekt", type: "dir", size: null },
        { name: "export.csv", path: "/home/u/export.csv", type: "file", size: 1234 },
        { name: "riesig.iso", path: "/home/u/riesig.iso", type: "file", size: 300 * 1024 * 1024 },
      ],
    });
  }
  return json({});
}

const welches = new URLSearchParams(location.search).get("panel");
if (welches === "terminal") {
  // Echtes xterm in DERSELBEN Schachtelung wie Terminal.jsx (h-full flex-col →
  // relative min-h-0 flex-1 → ref-div h-full), darunter die Tastenleiste.
  // Gefüllt mit Verlauf, damit es etwas zu scrollen gibt.
  const wurzel = document.getElementById("root");
  createRoot(wurzel).render(
    <div className="flex h-[var(--app-h,100dvh)] w-full flex-col">
      <div className="relative min-h-0 w-full flex-1">
        <div id="termhost" className="h-full w-full" />
      </div>
      <KeyBar mods={{}} onToggleMod={() => {}} onKey={() => {}} onCopyMode={() => {}}
        onTextMode={() => {}} onSchrift={() => {}} onTastatur={() => {}} />
    </div>,
  );
  setTimeout(() => {
    const term = new XTerm({ fontSize: 13, theme: { background: "#1e293b" }, cursorBlink: true });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(document.getElementById("termhost"));
    fit.fit();
    for (let i = 1; i <= 300; i++) term.writeln(`Zeile ${i} — Ausgabe im Verlauf`);
    // Dieselben zwei Handgriffe wie in Terminal.jsx — der Test prüft damit den
    // Code, der auch produktiv läuft, nicht eine Nachbildung.
    if (!new URLSearchParams(location.search).has("roh")) {
      wischScrollen(term);
      window.__ime = imeEingabe(term);
    }
    window.__term = term;
    // Was xterm an die ANWENDUNG schickt (Maus-Reports, Pfeiltasten): der
    // TUI-Test liest hier mit, statt eine echte PTY zu brauchen.
    window.__daten = [];
    term.onData((d) => window.__daten.push(d));
    // Sichtbarkeits-Wächter wie in Terminal.jsx (termVerbindung.js, Befund 1):
    // ausgeblendet wird nicht gefittet — der Test versteckt den Host per
    // display:none und prüft, dass die Spalten stehen bleiben.
    const host = document.getElementById("termhost");
    window.__fit = () => {
      if (!istSichtbar(host)) return false;
      fittenOhneSprung(term, () => fit.fit());
      return true;
    };
    window.__fitRoh = () => fit.fit();
    window.__fitVorschlag = () => fit.proposeDimensions(); // was das FitAddon roh messen würde
    window.__verstecken = (ja) => {
      host.parentElement.style.display = ja ? "none" : "";
    };
  }, 0);
} else if (welches === "schrift") {
  // Dieselbe Terminal-Konfiguration wie Terminal.jsx (Schrift, Unicode 11,
  // Mindestspalten am Handy), dazu die Zeilen einer Claude-Code-Mehrfachfrage.
  const wurzel = document.getElementById("root");
  wurzel.innerHTML = '<div id="termhost" style="height:100dvh;width:100%"></div>';
  const start = Number(new URLSearchParams(location.search).get("groesse") || 16);
  const term = new XTerm({ fontSize: start, fontFamily: SCHRIFT_FAMILIE, allowProposedApi: true, theme: { background: "#1e293b" } });
  const fit = new FitAddon();
  term.loadAddon(fit);
  term.loadAddon(new Unicode11Addon());
  term.unicode.activeVersion = "11";
  term.open(document.getElementById("termhost"));
  const spaltenBei = (g) => {
    const vorher = term.options.fontSize;
    term.options.fontSize = g;
    const cols = fit.proposeDimensions()?.cols;
    term.options.fontSize = vorher;
    return cols ?? Infinity;
  };
  window.__term = term;
  window.__bereit = schriftenLaden(start).then((geladen) => {
    term.options.fontFamily = SCHRIFT_FAMILIE;
    const vorher = spaltenBei(start);
    term.options.fontSize = schriftFuerSpalten(start, spaltenBei, undefined, 9);
    fit.fit();
    term.write("\x1b[2J\x1b[H");
    term.writeln("← \x1b[7m☐ Test 1\x1b[0m  ☒ Test 2  ☐ Test 3  ✔ Submit →");
    term.writeln("");
    term.writeln("❯ 1. Erste Option mit längerem Text");
    term.writeln("  2. [✔] Alpha   3. [ ] Beta");
    term.writeln("⏵⏵ auto mode on · ✻ ◐ ⏺ ⎿ ⧉");
    term.writeln("╭──────────╮");
    term.writeln("│ Rahmen   │");
    term.writeln("╰──────────╯");
    return {
      geladen,
      monoGeladen: document.fonts.check(`${start}px "Terminal Mono"`),
      symboleGeladen: document.fonts.check(`${start}px "Terminal Symbole"`, "⏵"),
      unicode: term.unicode.activeVersion,
      spaltenVorher: vorher,
      groesse: term.options.fontSize,
      spalten: term.cols,
      breiteHaken: term.unicode.getStringCellWidth ? term.unicode.getStringCellWidth("✔☒⏺") : null,
    };
  });
} else if (welches === "tastatur") {
  // Tastatur auf/zu ohne echtes Handy: die Hülle wird kleiner gestellt, ein
  // Schalter ersetzt viewport.js. Gezählt wird jede Größenänderung von xterm
  // (das wäre ein resize an die Gegenseite = Neuzeichnen von Claude Code).
  const wurzel = document.getElementById("root");
  wurzel.innerHTML = '<div id="huelle" style="position:relative;height:700px;width:100%"><div id="termhost" style="height:100%;width:100%"></div></div>';
  const huelle = document.getElementById("huelle");
  const host = document.getElementById("termhost");
  const term = new XTerm({ fontSize: 13, theme: { background: "#1e293b" } });
  const fit = new FitAddon();
  term.loadAddon(fit);
  term.open(host);
  let offen = false;
  const roh = new URLSearchParams(location.search).has("roh");
  const rahmen = roh ? null : tastaturRahmen(host, term, () => offen);
  if (rahmen) term.onCursorMove(() => rahmen.nachfuehren());
  window.__groessen = 0;
  term.onResize(() => window.__groessen++);
  const neuMessen = () => { rahmen?.anwenden(); fit.fit(); };
  neuMessen();
  for (let i = 1; i <= 120; i++) term.writeln(`Zeile ${i}`);
  term.write("❯ Eingabe");
  window.__term = term;
  window.__tastatur = (auf) => new Promise((fertig) => {
    offen = auf;
    huelle.style.height = auf ? "300px" : "700px";
    neuMessen();
    setTimeout(() => {
      const zelle = host.querySelector(".xterm-screen").clientHeight / term.rows;
      const cursorOben = host.getBoundingClientRect().top + term.buffer.active.cursorY * zelle;
      const h = huelle.getBoundingClientRect();
      fertig({ zeilen: term.rows, groessen: window.__groessen, cursorSichtbar: cursorOben >= h.top && cursorOben + zelle <= h.bottom + 1 });
    }, 100);
  });
} else if (welches === "langdruck") {
  // Echtes xterm mit Claude-Code-artigem Inhalt, Wischen (termScroll.js) UND
  // langer Druck (termLangdruck.js) an derselben Wurzel wie in Terminal.jsx.
  const wurzel = document.getElementById("root");
  wurzel.innerHTML = '<div id="termhost" style="height:100dvh;width:100%"></div>';
  const host = document.getElementById("termhost");
  const term = new XTerm({ fontSize: 13, theme: { background: "#1e293b" } });
  const fit = new FitAddon();
  term.loadAddon(fit);
  term.open(host);
  fit.fit();
  for (let i = 1; i <= 80; i++) term.writeln(`Verlauf ${i}`);
  term.writeln("");
  term.writeln("> zeig mir den Befehl");
  term.writeln("");
  term.writeln("● Hier ist der Befehl, mit dem du die App baust und installierst:");
  term.writeln("  ./kapsel/bauen.sh install");
  term.writeln("");
  term.write("> ");
  wischScrollen(term);
  window.__treffer = [];
  langDruck(host, ({ y }) => {
    const zeile = pufferZeileBei(term, y);
    window.__treffer.push(zeile >= 0 ? textBei(logischeZeilen(term.buffer.active), zeile) : null);
  });
  // Bildschirm-Y der Mitte einer Zeile, die `text` enthält
  window.__yVon = (text) => {
    const buf = term.buffer.active;
    for (let i = 0; i < buf.length; i++) {
      if (buf.getLine(i)?.translateToString(true).includes(text)) {
        const r = host.querySelector(".xterm-screen").getBoundingClientRect();
        return r.top + (i - buf.viewportY + 0.5) * (r.height / term.rows);
      }
    }
    return null;
  };
  window.__term = term;
} else if (welches === "keybar") {
  // Die Leiste allein, mit allen Knöpfen wie im Terminal — geprüft wird, ob
  // sie sich am Handy waagerecht wischen lässt (sie ist breiter als jedes
  // Telefon).
  createRoot(document.getElementById("root")).render(
    <div className="flex h-dvh flex-col justify-end bg-slate-800">
      <KeyBar
        mods={{}}
        onToggleMod={() => {}}
        onKey={() => {}}
        onCopyMode={() => {}}
        onTextMode={() => {}}
        onSchrift={() => {}}
        onTastatur={() => {}}
      />
    </div>,
  );
} else if (welches === "dateien") {
  window.fetch = fetchDoppelDateien;
  createRoot(document.getElementById("root")).render(
    <div className="flex h-dvh flex-col">
      <FilesPanel refreshKey={0} onOpenFile={(f) => window.__aufrufe.push({ url: "editor", methode: "OPEN", body: f })} />
    </div>,
  );
} else if (welches === "terminalfenster") {
  // Terminal-Panel verdoppeln: echte Panels, echter Fenster-Manager, aber ein
  // fetch-Doppel statt Backend. Zwei Verbindungen; auf "server" laufen schon
  // zwei Sessions (main und 2). Die Terminals selbst bekommen keine
  // WebSocket-Gegenstelle — geprüft werden Fenster und Tabs, nicht xterm.
  window.fetch = (url) => {
    const json = (data) => Promise.resolve({ ok: true, status: 200, json: async () => data });
    if (String(url).startsWith("/api/connections"))
      return json({ connections: [
        { name: "server", user: "u", host: "h1" },
        { name: "werkstatt", user: "u", host: "h2" },
      ] });
    if (String(url).startsWith("/api/ssh/sessions"))
      return json({ sessions: [{ name: "server", sid: "main" }, { name: "server", sid: "2" }] });
    // Projekt-Terminals (11.10.2026): nur werkstatt hat welche.
    if (String(url).startsWith("/api/projekte"))
      return json({ projekte: { werkstatt: [
        { name: "zeitalter", pfad: "/home/u/zeitalter", befehl: "claude --continue" },
        { name: "bot", pfad: "/home/u/bot", befehl: "claude" },
      ] } });
    return json({});
  };
  function Stand() {
    const fenster = useTerminalFenster();
    return (
      <TerminalVorrat fenster={fenster}>
        <div className="flex h-dvh flex-col">
          <Workspace
            tab="terminal"
            viewMode="windows"
            panels={[
              { id: "dateien", title: "Dateien", body: platzhalter("Dateien") },
              { id: "chat", title: "Chat", body: platzhalter("Chat") },
              ...fenster.map((n) => ({
                id: panelId(n),
                title: n === 1 ? "Terminal" : `Terminal ${n}`,
                body: <TerminalPanel n={n} />,
              })),
              { id: "agenten", title: "Agenten", body: platzhalter("Agenten") },
            ]}
          />
        </div>
      </TerminalVorrat>
    );
  }
  createRoot(document.getElementById("root")).render(<Stand />);
} else if (welches === "fenster") {
  // Fenster ein-/ausblenden (Issue #50): Hakenmenü + Workspace wie in App.jsx,
  // Sichtbarkeit im localStorage. Jedes Panel zählt seine Mounts mit — der
  // Test weist damit nach, dass Ausblenden NICHT unmountet.
  window.__mounts = {};
  function Zaehler({ name }) {
    useEffectP(() => {
      window.__mounts[name] = (window.__mounts[name] || 0) + 1;
    }, []);
    return platzhalter(name);
  }
  function Stand() {
    const [versteckt, setVersteckt] = useStateP(() =>
      versteckteLesen(localStorage.getItem(LS_VERSTECKT)),
    );
    useEffectP(() => localStorage.setItem(LS_VERSTECKT, versteckteText(versteckt)), [versteckt]);
    const liste = panels.map((p) => ({ ...p, body: <Zaehler name={p.id} /> }));
    const ids = liste.map((p) => p.id);
    return (
      <div className="flex h-dvh flex-col">
        <div className="flex justify-end gap-2 border-b p-1">
          <button data-ansichten onClick={() => window.dispatchEvent(new Event("workspace:views"))}>
            Ansichten
          </button>
          <FensterMenue
            fenster={liste.map(({ id, title }) => ({ id, title }))}
            versteckt={versteckt}
            onUmschalten={(id) => setVersteckt((v) => umschalten(v, id, ids))}
          />
        </div>
        <Workspace
          tab="chat"
          viewMode="windows"
          panels={liste}
          versteckt={versteckt}
          onVerstecktSetzen={setVersteckt}
        />
      </div>
    );
  }
  createRoot(document.getElementById("root")).render(<Stand />);
} else if (welches === "container") {
  // Container-Fenster (Issue #49) gegen ein fetch-Doppel: zwei Rechner, auf
  // "server" Docker, auf "pve" Proxmox mit LXC + VM. Aufgerufene URLs landen
  // in window.__abrufe, "terminal:oeffnen" in window.__shells.
  window.__abrufe = [];
  window.__shells = [];
  window.addEventListener("terminal:oeffnen", (e) => window.__shells.push(e.detail));
  const antw = (d, status = 200) =>
    Promise.resolve(new Response(JSON.stringify(d), { status, headers: { "Content-Type": "application/json" } }));
  const LISTEN = {
    server: {
      container: [
        { quelle: "docker", id: "nginx", name: "nginx", image: "nginx:1.27", status: "Up 3 days", laeuft: true, ports: "0.0.0.0:80->80/tcp" },
        { quelle: "docker", id: "alt", name: "alt", image: "busybox", status: "Exited (0)", laeuft: false, ports: "" },
      ],
      fehler: {},
    },
    pve: {
      container: [
        { quelle: "lxc", id: "101", name: "pihole", image: "LXC 101", status: "running", laeuft: true },
        { quelle: "lxc", id: "102", name: "cloud", image: "LXC 102", status: "stopped", laeuft: false },
        { quelle: "vm", id: "200", name: "win11", image: "VM 8192 MB RAM", status: "running", laeuft: true, nur_ansicht: true },
      ],
      fehler: {},
    },
  };
  window.fetch = (url) => {
    const u = new URL(url, location.origin);
    window.__abrufe.push(u.pathname + u.search);
    const p = u.pathname;
    if (p === "/api/container")
      return antw({ schalter: { server: { docker: true, proxmox: false }, pve: { docker: false, proxmox: true } } });
    const m = p.match(/^\/api\/container\/([^/]+)(?:\/([^/]+)\/([^/]+)\/(\w+))?$/);
    if (!m) return antw({ detail: "unbekannt" }, 404);
    const [, rechner, , ziel, was] = m;
    if (!was) return antw({ verbindung: rechner, ...LISTEN[rechner] });
    if (was === "logs") {
      const z = Number(u.searchParams.get("zeilen"));
      return antw({ text: Array.from({ length: 3 }, (_, i) => `${ziel} zeile ${i + 1} von ${z}`).join("\n"), zeilen: z, herkunft: "docker logs" });
    }
    if (was === "dateien") {
      const pfad = u.searchParams.get("pfad");
      if (pfad === "/geheim") return antw({ detail: "Keine Berechtigung" }, 403);
      return antw({
        pfad,
        eintraege: pfad === "/"
          ? [{ name: "etc", typ: "dir", groesse: 4096 }, { name: "geheim", typ: "dir", groesse: 4096 }, { name: "readme.txt", typ: "file", groesse: 12 }]
          : [{ name: "nginx.conf", typ: "file", groesse: 30 }],
      });
    }
    if (was === "lesen") return antw({ pfad: u.searchParams.get("pfad"), text: "inhalt von " + u.searchParams.get("pfad"), gekuerzt: false });
    return antw({ detail: "?" }, 404);
  };
  createRoot(document.getElementById("root")).render(
    <div className="flex h-dvh flex-col">
      <ContainerPanel />
    </div>,
  );
} else if (welches === "agenten") {
  window.fetch = fetchDoppel;
  createRoot(document.getElementById("root")).render(
    <div className="flex h-dvh flex-col">
      <AgentsPanel refreshKey={0} sichtbar={true} />
    </div>,
  );
} else {
  createRoot(document.getElementById("root")).render(
    <div className="flex h-dvh flex-col">
      <Workspace tab="chat" viewMode="windows" panels={panels} />
    </div>,
  );
}
