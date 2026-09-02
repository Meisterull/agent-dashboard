// Prüfstand: mountet einzelne Teile der Oberfläche OHNE Backend, Login oder
// Chat, damit sie sich in einem echten Browser prüfen lassen.
//
//   ?panel=workspace  (Default)  Fensteranordnung  -> tests/test_workspace_browser.cjs
//   ?panel=agenten               Agenten-Panel     -> tests/test_agents_browser.cjs
//   ?panel=keybar                Tastenleiste      -> tests/test_keybar_browser.cjs
//   ?panel=terminal              xterm + Leiste    -> tests/test_terminal_browser.cjs
//
// Temporäre Datei — gehört nicht in den Auslieferungs-Build.
import { createRoot } from "react-dom/client";
import AgentsPanel from "./components/AgentsPanel";
import KeyBar from "./components/KeyBar";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { fittenOhneSprung, wischScrollen } from "./termScroll";
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
  if (url === "/api/automatik") return json({ notaus: false, agents: {} });
  if (url.startsWith("/api/agents/PMNB029/tasks"))
    return json({
      agent: "PMNB029",
      inbox: [{ task_id: "task-1", status: "pending", instruction: "bau das" }],
      outbox: [],
      messages: nachrichten,
    });
  if (url.startsWith("/api/agents/erp/tasks"))
    return json({ agent: "erp", inbox: [], outbox: [], messages: [] });
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
    window.__fit = () => fittenOhneSprung(term, () => fit.fit());
    window.__fitRoh = () => fit.fit();
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
