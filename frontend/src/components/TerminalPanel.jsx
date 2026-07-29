import { useEffect, useState } from "react";
import { getConnections } from "../api";
import Terminal, { DEFAULT_SID } from "./Terminal";
import ConnectionsModal from "./ConnectionsModal";

// SSH-Verbindungen als Tabs. Einmal geöffnete Terminals bleiben gemountet
// (nur versteckt), damit die SSH-Session — z.B. ein laufendes Claude-Code —
// den Tab-Wechsel überlebt. Fenster schließen (×) detacht nur: die Shell
// läuft serverseitig weiter (SSH_GRACE_SECONDS) und wird beim nächsten
// Öffnen — auch von einem anderen PC — samt Output-Replay wieder angehängt.
// Beendet wird nur über den eigenen ⏻-Knopf (DELETE-Endpoint) oder Shell-Exit.
// Ein Punkt am Tab markiert Verbindungen mit laufender Session; beim Mount
// und bei Fokus-Rückkehr werden laufende Sessions automatisch wieder geöffnet.
export default function TerminalPanel() {
  const [connections, setConnections] = useState([]);
  const [open, setOpen] = useState([]); // Namen mit geöffnetem Terminal-Fenster
  const [running, setRunning] = useState([]); // Namen mit serverseitiger Session
  const [pendingOpen, setPendingOpen] = useState([]); // beim Mount gefundene Sessions
  const [active, setActive] = useState(null);
  const [showManage, setShowManage] = useState(false);

  useEffect(() => {
    const load = () =>
      getConnections()
        .then((d) => setConnections(d.connections))
        .catch(() => setConnections([]));
    load();
    window.addEventListener("connections:changed", load);
    return () => window.removeEventListener("connections:changed", load);
  }, []);

  // autoOpen NUR beim Mount (Browser-Neustart/Login): dann kommen laufende
  // Terminals von selbst zurück — auch von einem anderen PC (Übernahme via
  // Close-Code 4000). Bei Fokus-Rückkehr wird nur das Badge aktualisiert:
  // sonst würde ein bloß im Hintergrund offenes Dashboard die Session eines
  // anderen PCs klauen, sobald es Fokus bekommt. Reattach dann per Tab-Klick.
  const loadSessions = (autoOpen = false) =>
    fetch("/api/ssh/sessions")
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => {
        const names = [...new Set(d.sessions.map((s) => s.name))];
        setRunning(names);
        if (autoOpen) setPendingOpen(names);
      })
      .catch(() => {});

  useEffect(() => {
    loadSessions(true);
    const onFocus = () => loadSessions();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  // Auto-Reopen, sobald auch die Verbindungsliste da ist; nur für Verbindungen,
  // die es (noch) gibt — sonst gäbe es Terminals ohne Tab.
  useEffect(() => {
    const names = pendingOpen.filter((n) => connections.some((c) => c.name === n));
    if (!names.length) return;
    setOpen((o) => [...o, ...names.filter((n) => !o.includes(n))]);
    setPendingOpen((p) => p.filter((n) => !names.includes(n)));
  }, [pendingOpen, connections]);

  const activate = (name) => {
    setActive(name);
    setOpen((o) => (o.includes(name) ? o : [...o, name]));
  };

  // × — nur das Fenster schließen; die Session läuft serverseitig weiter.
  const close = (name) => {
    setOpen((o) => o.filter((n) => n !== name));
    setActive((a) => (a === name ? null : a));
  };

  // ⏻ — Session serverseitig beenden (killt die Shell auf dem Agenten-PC).
  const endSession = (name) => {
    fetch(`/api/ssh/${encodeURIComponent(name)}/session?sid=${DEFAULT_SID}`, {
      method: "DELETE",
    })
      .catch(() => {})
      .finally(() => {
        setRunning((r) => r.filter((n) => n !== name));
        close(name);
      });
  };

  // Shell hat sich selbst beendet (exit) oder wurde anderweitig gekillt.
  const ended = (name) => {
    close(name);
    loadSessions();
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b bg-slate-50 px-2 py-1 dark:border-slate-700 dark:bg-slate-900">
        <span className="mr-2 text-xs font-semibold text-slate-500 dark:text-slate-400">SSH</span>
        {connections.length === 0 && (
          <span className="text-xs text-slate-400">keine Verbindungen</span>
        )}
        {connections.map((c) => (
          <span key={c.name} className="inline-flex items-center">
            <button
              onClick={() => activate(c.name)}
              title={`${c.user}@${c.host}`}
              className={`rounded px-2 py-0.5 text-xs ${
                active === c.name
                  ? "bg-slate-700 text-white dark:bg-slate-600"
                  : open.includes(c.name)
                    ? "bg-emerald-100 text-slate-700 dark:bg-emerald-900 dark:text-emerald-200"
                    : "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
              }`}
            >
              {running.includes(c.name) && (
                <span
                  title="Session läuft"
                  className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-emerald-500 align-middle"
                />
              )}
              {c.name}
            </button>
            {open.includes(c.name) && (
              <>
                <button
                  onClick={() => close(c.name)}
                  title="Fenster schließen (Session läuft weiter)"
                  className="ml-0.5 rounded px-1 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-300"
                >
                  ×
                </button>
                <button
                  onClick={() => endSession(c.name)}
                  title="Session beenden"
                  className="rounded px-1 text-xs text-slate-400 hover:bg-red-100 hover:text-red-600 dark:hover:bg-red-950 dark:hover:text-red-400"
                >
                  ⏻
                </button>
              </>
            )}
          </span>
        ))}
        <button
          onClick={() => setShowManage(true)}
          title="Verbindungen verwalten / neue anlegen"
          className="ml-1 shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-xs text-slate-500 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-800"
        >
          +
        </button>
      </div>
      {showManage && <ConnectionsModal onClose={() => setShowManage(false)} />}
      <div className="relative min-h-0 flex-1 bg-slate-800">
        {open.length === 0 && (
          <div className="flex h-full items-center justify-center text-xs text-slate-400">
            Verbindung wählen, um ein Terminal zu öffnen
          </div>
        )}
        {open.map((name) => (
          <div
            key={name}
            className={`absolute inset-0 ${active === name ? "" : "invisible"}`}
          >
            <Terminal
              name={name}
              visible={active === name}
              onEnded={() => ended(name)}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
