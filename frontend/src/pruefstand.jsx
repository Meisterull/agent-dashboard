// Prüfstand: mountet NUR den Workspace mit Platzhalter-Panels, ohne Backend,
// Login oder Chat. Damit lässt sich die Fensteranordnung in einem echten
// Browser prüfen (frontend/tests/test_workspace_browser.cjs).
//
// Temporäre Datei — gehört nicht in den Auslieferungs-Build.
import { createRoot } from "react-dom/client";
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

createRoot(document.getElementById("root")).render(
  <div className="flex h-dvh flex-col">
    <Workspace tab="chat" viewMode="windows" panels={panels} />
  </div>,
);
