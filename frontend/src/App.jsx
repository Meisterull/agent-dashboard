import { useEffect, useState } from "react";
import TopBar from "./components/TopBar";
import QuestionsBanner from "./components/QuestionsBanner";
import Chat from "./components/Chat";
import FilesPanel from "./components/FilesPanel";
import AgentsPanel from "./components/AgentsPanel";
import TerminalPanel from "./components/TerminalPanel";
import Workspace from "./components/Workspace";
import ExternalFrame from "./components/ExternalFrame";
import Settings from "./components/Settings";
import EditorModal from "./components/EditorModal";
import Login from "./components/Login";
import { authCheck, getSettings } from "./api";

// Dashboard-Layout: auf dem Desktop (md+) sind die vier Panels frei
// verschieb- und größenveränderbare Fenster (Workspace.jsx); Standard-
// Anordnung entspricht dem alten festen Raster aus PROJECT.md.
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
  // Externe Fenster (z. B. noVNC) aus den Settings; Settings-Dialog feuert
  // nach dem Speichern "settings:changed".
  const [extWindows, setExtWindows] = useState([]);
  useEffect(() => {
    if (!authed) return;
    const load = () =>
      getSettings()
        .then((s) => setExtWindows((s.external_windows || []).filter((w) => w?.name && w?.url)))
        .catch(() => {});
    load();
    window.addEventListener("settings:changed", load);
    return () => window.removeEventListener("settings:changed", load);
  }, [authed]);

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

      <Workspace
        tab={tab}
        panels={[
          {
            id: "dateien",
            title: "Dateien",
            body: <FilesPanel refreshKey={refreshKey} onOpenFile={setOpenFile} />,
          },
          {
            id: "chat",
            title: "Chat",
            body: (
              <Chat
                sessionId={sessionId}
                setSessionId={setSessionId}
                onActivity={bump}
              />
            ),
            bodyClass: "bg-slate-100 dark:bg-slate-950",
          },
          { id: "terminal", title: "Terminal", body: <TerminalPanel /> },
          {
            id: "agenten",
            title: "Agenten",
            body: <AgentsPanel refreshKey={refreshKey} />,
          },
          ...extWindows.map((w) => ({
            id: `ext:${w.name}`,
            title: w.name,
            body: <ExternalFrame url={w.url} />,
          })),
        ]}
      />

      {/* Mobil: Tab-Leiste unten */}
      <nav className="flex shrink-0 overflow-x-auto border-t bg-white md:hidden dark:border-slate-700 dark:bg-slate-900">
        {[...TABS, ...extWindows.map((w) => [`ext:${w.name}`, w.name])].map(([id, label]) => (
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
