import { useEffect, useRef, useState } from "react";
import { closeTask, getAgents, getTasks } from "../api";

const STATUS_COLORS = {
  pending: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  running: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  done: "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
  error: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  needs_confirm:
    "bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300",
};

function StatusBadge({ status }) {
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
        STATUS_COLORS[status] ||
        "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
      }`}
    >
      {status}
    </span>
  );
}

// Pollt alle 8 s die Mailboxen ALLER Agenten (nicht nur des angezeigten):
// die Anzeige aktualisiert sich so von selbst, und Statuswechsel auf
// done/error/needs_confirm melden sich über onAttention nach oben (App lässt
// dann den Agenten-Reiter rot blinken). Der erste Durchlauf ist nur Basis —
// alte fertige Tasks sollen beim Laden der Seite nicht blinken.
const ALERT_STATUS = ["done", "error", "needs_confirm"];

export default function AgentsPanel({ refreshKey, onAttention }) {
  const [agents, setAgents] = useState([]);
  const [selected, setSelected] = useState(null);
  const [tasksByAgent, setTasksByAgent] = useState({});
  const [localKey, setLocalKey] = useState(0); // ↻-Button
  const prevRef = useRef(null); // "agent/box/task_id" -> status
  const onAttentionRef = useRef(onAttention);
  onAttentionRef.current = onAttention;

  useEffect(() => {
    let stale = false;
    const load = async () => {
      try {
        const d = await getAgents();
        if (stale) return;
        setAgents(d.agents);
        setSelected((s) => s || d.agents[0] || null);
        const pairs = await Promise.all(
          d.agents.map((a) =>
            getTasks(a)
              .then((t) => [a, t])
              .catch(() => null),
          ),
        );
        if (stale) return;
        const byAgent = Object.fromEntries(pairs.filter(Boolean));
        setTasksByAgent(byAgent);
        const snap = {};
        for (const [a, t] of Object.entries(byAgent))
          for (const box of ["inbox", "outbox"])
            for (const task of t[box] || [])
              snap[`${a}/${box}/${task.task_id}`] = task.status;
        const prev = prevRef.current;
        if (
          prev &&
          Object.entries(snap).some(
            ([k, st]) => ALERT_STATUS.includes(st) && prev[k] !== st,
          )
        )
          onAttentionRef.current?.();
        prevRef.current = snap;
      } catch {
        if (!stale) setAgents([]);
      }
    };
    load();
    const t = setInterval(load, 8000);
    return () => {
      stale = true;
      clearInterval(t);
    };
  }, [refreshKey, localKey]);

  const tasks = selected ? tasksByAgent[selected] : null;

  // Hängengebliebenen Task von Hand abschließen (Agent antwortet nicht mehr).
  const forceClose = async (taskId) => {
    if (!window.confirm(`Task ${taskId} ohne Ergebnis schließen?`)) return;
    try {
      await closeTask(selected, taskId);
      setLocalKey((k) => k + 1);
    } catch (e) {
      alert(`Schließen fehlgeschlagen: ${e.message}`);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center border-b bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
        <span className="flex-1">MCP-Monitor · Aufgaben</span>
        <button
          onClick={() => setLocalKey((k) => k + 1)}
          title="jetzt aktualisieren"
          className="rounded px-1.5 py-0.5 hover:bg-slate-200 dark:hover:bg-slate-800"
        >
          ↻
        </button>
      </div>
      <div className="flex flex-wrap gap-1 border-b p-2 dark:border-slate-700">
        {agents.length === 0 && (
          <span className="text-xs text-slate-400">keine Agenten</span>
        )}
        {agents.map((a) => (
          <button
            key={a}
            onClick={() => setSelected(a)}
            className={`rounded px-2 py-0.5 font-mono text-xs ${
              selected === a
                ? "bg-blue-600 text-white"
                : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
            }`}
          >
            {a}
          </button>
        ))}
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto p-3 text-xs">
        {tasks && (
          <>
            <div>
              <div className="mb-1 font-semibold text-slate-500 dark:text-slate-400">
                Inbox ({tasks.inbox.length})
              </div>
              {tasks.inbox.length === 0 ? (
                <p className="text-slate-400">leer</p>
              ) : (
                tasks.inbox.map((t) => (
                  <div key={t.task_id} className="mb-1 rounded bg-slate-50 p-1.5 dark:bg-slate-800">
                    <div className="flex items-center justify-between gap-1">
                      <span className="flex-1 truncate font-mono">{t.task_id}</span>
                      <StatusBadge status={t.status} />
                      <button
                        onClick={() => forceClose(t.task_id)}
                        title="Task manuell schließen (ohne Ergebnis)"
                        className="rounded px-1 text-slate-400 hover:bg-slate-200 hover:text-slate-600 dark:hover:bg-slate-700 dark:hover:text-slate-300"
                      >
                        ✕
                      </button>
                    </div>
                    <div className="truncate text-slate-500 dark:text-slate-400">{t.instruction}</div>
                  </div>
                ))
              )}
            </div>
            <div>
              <div className="mb-1 font-semibold text-slate-500 dark:text-slate-400">
                Outbox ({tasks.outbox.length})
              </div>
              {tasks.outbox.length === 0 ? (
                <p className="text-slate-400">leer</p>
              ) : (
                tasks.outbox.map((t) => (
                  <div key={t.task_id} className="mb-1 rounded bg-slate-50 p-1.5 dark:bg-slate-800">
                    <div className="flex items-center justify-between">
                      <span className="font-mono">{t.task_id}</span>
                      <StatusBadge status={t.status} />
                    </div>
                    <div className="truncate text-slate-500 dark:text-slate-400">{t.result}</div>
                  </div>
                ))
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
