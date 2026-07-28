import { useEffect, useState } from "react";
import TopBar from "./components/TopBar";
import QuestionsBanner from "./components/QuestionsBanner";
import Chat from "./components/Chat";
import FilesPanel from "./components/FilesPanel";
import AgentsPanel from "./components/AgentsPanel";
import TerminalPanel from "./components/TerminalPanel";
import Settings from "./components/Settings";
import EditorModal from "./components/EditorModal";
import Login from "./components/Login";
import { authCheck } from "./api";

// Vollständiges Dashboard-Layout nach PROJECT.md:
//   Top-Bar
//   ┌ links: Dateibaum ┬ Mitte oben: Chat        ┬ rechts: MCP-Monitor ┐
//   │                  ├ Mitte unten: SSH-Terminal┤                     │
//   └──────────────────┴───────────────────────────┴─────────────────────┘
//
// Mobil (< md) gibt es stattdessen eine Tab-Leiste unten, die genau ein
// Panel vollflächig zeigt. Alle Panels bleiben dabei gemountet (nur per
// CSS versteckt), damit Chat-Zustand und SSH-Sessions Tab-Wechsel überleben.

const TABS = [
  ["chat", "Chat"],
  ["terminal", "Terminal"],
  ["agenten", "Agenten"],
  ["dateien", "Dateien"],
];

export default function App() {
  const [sessionId, setSessionId] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [showSettings, setShowSettings] = useState(false);
  const [openFile, setOpenFile] = useState(null);
  const [tab, setTab] = useState("chat"); // mobil aktives Panel
  const [authed, setAuthed] = useState(null); // null = prüft noch

  // Login-Gate: initiale Prüfung + globales 401-Event aus api.js
  useEffect(() => {
    authCheck()
      .then((d) => setAuthed(d.authed))
      .catch(() => setAuthed(true)); // API nicht erreichbar → Panels zeigen Fehler
    const onRequired = () => setAuthed(false);
    window.addEventListener("auth:required", onRequired);
    return () => window.removeEventListener("auth:required", onRequired);
  }, []);

  // Hell/Dunkel: gespeicherte Wahl, sonst System-Einstellung
  const [theme, setTheme] = useState(
    () =>
      localStorage.getItem("theme") ||
      (window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"),
  );
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem("theme", theme);
  }, [theme]);

  const bump = () => setRefreshKey((k) => k + 1);

  // Nach dem Umschalten einmal "resize" feuern, damit xterm das Terminal
  // neu fittet, sobald es wieder sichtbar ist (Listener in Terminal.jsx).
  const switchTab = (t) => {
    setTab(t);
    setTimeout(() => window.dispatchEvent(new Event("resize")), 50);
  };

  if (authed === false) return <Login onSuccess={() => setAuthed(true)} />;
  if (authed === null)
    return (
      <div className="flex h-dvh items-center justify-center bg-slate-100 text-sm text-slate-400 dark:bg-slate-950 dark:text-slate-500">
        lädt…
      </div>
    );

  return (
    <div className="flex h-dvh flex-col bg-slate-100 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <TopBar
        sessionId={sessionId}
        onOpenSettings={() => setShowSettings(true)}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
      />
      <QuestionsBanner refreshKey={refreshKey} onAnswered={bump} />

      <div className="flex min-h-0 flex-1">
        {/* Links: Dateibaum (mobil: Tab "Dateien") */}
        <aside
          className={`${
            tab === "dateien" ? "flex" : "hidden"
          } w-full flex-col bg-white md:flex md:w-60 md:shrink-0 md:border-r dark:bg-slate-900 dark:md:border-slate-700`}
        >
          <FilesPanel refreshKey={refreshKey} onOpenFile={setOpenFile} />
        </aside>

        {/* Mitte: Chat (oben) + SSH-Terminal (unten); mobil je ein Tab */}
        <main
          className={`${
            tab === "chat" || tab === "terminal" ? "flex" : "hidden"
          } min-w-0 flex-1 flex-col md:flex`}
        >
          <div
            className={`${
              tab === "chat" ? "flex" : "hidden"
            } min-h-0 flex-1 flex-col md:flex md:border-b dark:md:border-slate-700`}
          >
            <Chat
              sessionId={sessionId}
              setSessionId={setSessionId}
              onActivity={bump}
            />
          </div>
          <div
            className={`${
              tab === "terminal" ? "flex" : "hidden"
            } min-h-0 flex-1 flex-col md:flex md:h-64 md:flex-none`}
          >
            <TerminalPanel />
          </div>
        </main>

        {/* Rechts: MCP-Monitor / Aufgaben (mobil: Tab "Agenten") */}
        <aside
          className={`${
            tab === "agenten" ? "flex" : "hidden"
          } w-full flex-col bg-white md:flex md:w-72 md:shrink-0 md:border-l dark:bg-slate-900 dark:md:border-slate-700`}
        >
          <AgentsPanel refreshKey={refreshKey} />
        </aside>
      </div>

      {/* Mobil: Tab-Leiste unten */}
      <nav className="flex shrink-0 border-t bg-white md:hidden dark:border-slate-700 dark:bg-slate-900">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            onClick={() => switchTab(id)}
            className={`-mt-px flex-1 border-t-2 py-2.5 text-sm ${
              tab === id
                ? "border-sky-500 font-semibold text-sky-600 dark:text-sky-400"
                : "border-transparent text-slate-500 dark:text-slate-400"
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      {showSettings && <Settings onClose={() => setShowSettings(false)} />}
      {openFile && (
        <EditorModal
          source={openFile.source}
          path={openFile.path}
          onClose={() => setOpenFile(null)}
        />
      )}
    </div>
  );
}
