import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import TopBar from "./components/TopBar";
import QuestionsBanner from "./components/QuestionsBanner";
import Chat from "./components/Chat";
import FilesPanel from "./components/FilesPanel";
import AgentsPanel from "./components/AgentsPanel";
import TerminalPanel from "./components/TerminalPanel";
import Workspace from "./components/Workspace";
import ExternalFrame from "./components/ExternalFrame";
import Settings from "./components/Settings";
import Login from "./components/Login";
import { DialogHost } from "./components/Dialog";
import { starteLiveEvents } from "./live";
import { authCheck, getSettings, logout } from "./api";

// CodeMirror (+ language-data) ist der mit Abstand größte Brocken und wird
// nur beim Öffnen einer Datei gebraucht → aus dem Hauptbundle heraushalten.
const EditorModal = lazy(() => import("./components/EditorModal"));

// Dashboard-Layout: auf dem Desktop (md+) sind die vier Panels frei
// verschieb- und größenveränderbare Fenster (Workspace.jsx); Standard-
// Anordnung entspricht dem alten festen Raster aus PROJECT.md. Alternativ
// gibt es dort einen Tab-Modus (Umschalter in der TopBar, persistiert):
// ein Panel vollflächig, Wechsel über die Tab-Leiste — z. B. Chat ↔ VNC.
//
// Mobil (< md) gibt es immer die Tab-Leiste unten, die genau ein Panel
// vollflächig zeigt. In beiden Modi bleiben alle Panels gemountet (nur per
// CSS versteckt), damit Chat-Zustand und SSH-Sessions Wechsel überleben.

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

  // Live-Events (F4): ein SSE-Strom für die ganze App — erst nach dem Login,
  // vorher antwortet /api/events nur 401 und EventSource retryt ins Leere.
  useEffect(() => {
    if (!authed) return;
    return starteLiveEvents();
  }, [authed]);

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

  // Desktop-Ansicht: freie Fenster oder ein Panel vollflächig mit Tabs
  const [viewMode, setViewMode] = useState(
    () => localStorage.getItem("workspace-view-mode") || "windows",
  );
  useEffect(() => {
    localStorage.setItem("workspace-view-mode", viewMode);
    // xterm neu fitten, sobald der Moduswechsel gerendert ist
    setTimeout(() => window.dispatchEvent(new Event("resize")), 50);
  }, [viewMode]);

  const bump = () => setRefreshKey((k) => k + 1);

  // Abmelden: Cookie serverseitig löschen und zurück zum Login. Scheitert der
  // Request (Netz weg), gehen wir trotzdem zum Login — das Cookie ist dann
  // höchstens noch serverseitig gültig.
  const abmelden = () =>
    logout()
      .catch(() => {})
      .finally(() => setAuthed(false));

  // Aufmerksamkeit pro Panel: Fenster-Titel/Tab blinkt rot, bis der Nutzer
  // hineinklickt bzw. den Tab wählt. Quellen: Chat-Antwort fertig,
  // Task-Statuswechsel (AgentsPanel) und neue Rückfragen (QuestionsBanner).
  // Nur in der Tab-Ansicht (mobil oder Tab-Modus) verdeckt ein Panel die
  // anderen — dort weiß man vom aktiven Tab schon alles, dort darf er also
  // nicht blinken. Im Fenster-Modus am Desktop sind alle Panels sichtbar;
  // "aktiv" gibt es nicht, jedes Fenster darf sich melden.
  const [isDesktop, setIsDesktop] = useState(
    () => window.matchMedia("(min-width: 768px)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    const on = (e) => setIsDesktop(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  const sichtbarRef = useRef(null); // id des allein sichtbaren Panels (sonst null)
  sichtbarRef.current = isDesktop && viewMode === "windows" ? null : tab;
  // Das Agenten-Panel pollt am teuersten (getTasks je Agent) — verdeckt darf
  // es seltener laden (AgentsPanel streckt dann seinen Takt).
  const agentenSichtbar =
    (isDesktop && viewMode === "windows") || tab === "agenten";

  const [attention, setAttention] = useState({});
  const flag = useCallback((id) => {
    if (sichtbarRef.current === id) return; // Panel liegt gerade offen vor dem Nutzer
    setAttention((a) => (a[id] ? a : { ...a, [id]: true }));
  }, []);
  const clearAttention = useCallback(
    (id) =>
      setAttention((a) => {
        if (!a[id]) return a;
        const next = { ...a };
        delete next[id];
        return next;
      }),
    [],
  );

  // Nach dem Umschalten einmal "resize" feuern, damit xterm das Terminal
  // neu fittet, sobald es wieder sichtbar ist (Listener in Terminal.jsx).
  const switchTab = (t) => {
    setTab(t);
    clearAttention(t);
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
        viewMode={viewMode}
        onToggleViewMode={() =>
          setViewMode((m) => (m === "windows" ? "tabs" : "windows"))
        }
        onLogout={abmelden}
      />
      <QuestionsBanner
        refreshKey={refreshKey}
        onAnswered={bump}
        onNew={() => flag("agenten")}
      />

      <Workspace
        tab={tab}
        viewMode={viewMode}
        attention={attention}
        onFocusPanel={clearAttention}
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
                onDone={() => flag("chat")}
              />
            ),
            bodyClass: "bg-slate-100 dark:bg-slate-950",
          },
          { id: "terminal", title: "Terminal", body: <TerminalPanel /> },
          {
            id: "agenten",
            title: "Agenten",
            body: (
              <AgentsPanel
                refreshKey={refreshKey}
                sichtbar={agentenSichtbar}
                onAttention={() => flag("agenten")}
              />
            ),
          },
          ...extWindows.map((w) => ({
            id: `ext:${w.name}`,
            title: w.name,
            body: <ExternalFrame url={w.url} />,
          })),
        ]}
      />

      {/* Tab-Leiste unten: mobil immer, am Desktop nur im Tab-Modus.
          pb-[env(…)] hält die Knöpfe aus der Gesten-Zone am unteren
          Geräterand (viewport-fit=cover zeichnet bis in die Rundungen). */}
      <nav
        className={`flex shrink-0 overflow-x-auto border-t bg-white pb-[env(safe-area-inset-bottom)] dark:border-slate-700 dark:bg-slate-900 ${
          viewMode === "windows" ? "md:hidden" : ""
        }`}
      >
        {[...TABS, ...extWindows.map((w) => [`ext:${w.name}`, w.name])].map(([id, label]) => (
          <button
            key={id}
            onClick={() => switchTab(id)}
            className={`-mt-px flex-1 border-t-2 py-2.5 text-sm ${
              tab === id
                ? "border-sky-500 font-semibold text-sky-600 dark:text-sky-400"
                : "border-transparent text-slate-500 dark:text-slate-400"
            } ${attention[id] ? "attention-blink" : ""}`}
          >
            {label}
          </button>
        ))}
      </nav>

      {showSettings && <Settings onClose={() => setShowSettings(false)} />}
      {openFile && (
        <Suspense
          fallback={
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-white text-sm text-slate-400 dark:bg-slate-900 dark:text-slate-500">
              Editor lädt…
            </div>
          }
        >
          <EditorModal
            source={openFile.source}
            path={openFile.path}
            onClose={() => setOpenFile(null)}
          />
        </Suspense>
      )}
      {/* zentrale confirm/prompt-Dialoge (Dialog.jsx) — als LETZTES Kind,
          damit sie über Settings/Editor (gleiches z-50) liegen */}
      <DialogHost />
    </div>
  );
}
