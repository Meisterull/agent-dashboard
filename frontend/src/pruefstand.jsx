// Prüfstand: mountet einzelne Teile der Oberfläche OHNE Backend, Login oder
// Chat, damit sie sich in einem echten Browser prüfen lassen.
//
//   ?panel=workspace  (Default)  Fensteranordnung  -> tests/test_workspace_browser.cjs
//   ?panel=agenten               Agenten-Panel     -> tests/test_agents_browser.cjs
//   ?panel=keybar                Tastenleiste      -> tests/test_keybar_browser.cjs
//   ?panel=terminal              xterm + Leiste    -> tests/test_terminal_browser.cjs
//   ?panel=dateien               Datei-Panel       -> tests/test_dateien_browser.cjs
//
// Temporäre Datei — gehört nicht in den Auslieferungs-Build.
import { createRoot } from "react-dom/client";
import AgentsPanel from "./components/AgentsPanel";
import FilesPanel from "./components/FilesPanel";
import KeyBar from "./components/KeyBar";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { fittenOhneSprung, wischScrollen } from "./termScroll";
import { istSichtbar } from "./termVerbindung";
import Workspace from "./components/Workspace";
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
      inbox: [{ task_id: "task-1", status: "pending", instruction: "bau das" }],
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
    if (!new URLSearchParams(location.search).has("roh")) wischScrollen(term);
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
