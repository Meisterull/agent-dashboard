import { useEffect, useState } from "react";
import { getAgents, getTasks } from "../api";

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

export default function AgentsPanel({ refreshKey }) {
  const [agents, setAgents] = useState([]);
  const [selected, setSelected] = useState(null);
  const [tasks, setTasks] = useState(null);

  useEffect(() => {
    getAgents()
      .then((d) => {
        setAgents(d.agents);
        setSelected((s) => s || d.agents[0] || null);
      })
      .catch(() => setAgents([]));
  }, [refreshKey]);

  useEffect(() => {
    if (!selected) {
      setTasks(null);
      return;
    }
    getTasks(selected)
      .then(setTasks)
      .catch(() => setTasks(null));
  }, [selected, refreshKey]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
        MCP-Monitor · Aufgaben
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
                    <div className="flex items-center justify-between">
                      <span className="font-mono">{t.task_id}</span>
                      <StatusBadge status={t.status} />
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
