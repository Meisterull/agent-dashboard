import { useEffect, useState } from "react";
import { getConnections } from "../api";
import Terminal, { sidFor, clearSid } from "./Terminal";
import ConnectionsModal from "./ConnectionsModal";

// SSH-Verbindungen als Tabs. Einmal geöffnete Terminals bleiben gemountet
// (nur versteckt), damit die SSH-Session — z.B. ein laufendes Claude-Code —
// den Tab-Wechsel überlebt. Schließen beendet die Session explizit
// (serverseitig via DELETE, sonst lebt sie die Grace-Zeit weiter).
export default function TerminalPanel() {
  const [connections, setConnections] = useState([]);
  const [open, setOpen] = useState([]); // Namen mit laufender Session
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

  const activate = (name) => {
    setActive(name);
    setOpen((o) => (o.includes(name) ? o : [...o, name]));
  };

  const close = (name) => {
    const sid = sidFor(name);
    fetch(`/api/ssh/${encodeURIComponent(name)}/session?sid=${sid}`, {
      method: "DELETE",
    }).catch(() => {});
    clearSid(name); // nächstes Öffnen = frische Session
    setOpen((o) => o.filter((n) => n !== name));
    setActive((a) => (a === name ? null : a));
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
              {c.name}
            </button>
            {open.includes(c.name) && (
              <button
                onClick={() => close(c.name)}
                title="Session beenden"
                className="ml-0.5 rounded px-1 text-xs text-slate-400 hover:bg-red-100 hover:text-red-600 dark:hover:bg-red-950 dark:hover:text-red-400"
              >
                ×
              </button>
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
            <Terminal name={name} visible={active === name} />
          </div>
        ))}
      </div>
    </div>
  );
}
