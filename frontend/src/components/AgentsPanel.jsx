import { useEffect, useRef, useState } from "react";
import {
  closeTask,
  getAgents,
  getAutomatik,
  getTasks,
  setAutomatik,
  setNotaus,
} from "../api";

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

// Automatikmodus (Issue #12): Punkt-Farbe = ECHTER Watcher-Zustand des Agenten.
const AUTO_DOT = {
  an: "bg-green-500",
  startet: "bg-amber-400 animate-pulse",
  stoppt: "bg-amber-400 animate-pulse",
  fehler: "bg-red-500",
};

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
  const [auto, setAuto] = useState({ notaus: false, agents: {} });
  const [localKey, setLocalKey] = useState(0); // ↻-Button
  const prevRef = useRef(null); // "agent/box/task_id" -> status
  const onAttentionRef = useRef(onAttention);
  onAttentionRef.current = onAttention;

  useEffect(() => {
    let stale = false;
    const load = async () => {
      try {
        getAutomatik()
          .then((a) => !stale && setAuto(a))
          .catch(() => {});
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
  const autoInfo = selected ? auto.agents?.[selected] : null;
  const autoAktiv = Object.values(auto.agents || {}).some((a) =>
    ["an", "startet", "stoppt"].includes(a.status),
  );

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

  // Automatikmodus: Watcher auf dem Agenten-PC per Klick an/aus (Issue #12).
  const toggleAutomatik = async () => {
    const ziel = !autoInfo.gewuenscht;
    if (
      ziel &&
      !window.confirm(
        `Automatik für '${selected}' einschalten?\nClaude Code arbeitet dann UNBEAUFSICHTIGT Tasks aus der Inbox ab.`,
      )
    )
      return;
    try {
      setAuto(await setAutomatik(selected, ziel));
    } catch (e) {
      alert(`Umschalten fehlgeschlagen: ${e.message}`);
    }
  };

  const toggleNotaus = async () => {
    if (
      !auto.notaus &&
      !window.confirm("Not-Aus: ALLE Automatiken sofort hart stoppen?")
    )
      return;
    try {
      setAuto(await setNotaus(!auto.notaus));
    } catch (e) {
      alert(`Not-Aus fehlgeschlagen: ${e.message}`);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-1 border-b bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
        <span className="flex-1">MCP-Monitor · Aufgaben</span>
        {(autoAktiv || auto.notaus) && (
          <button
            onClick={toggleNotaus}
            title={
              auto.notaus
                ? "Not-Aus lösen — eingeschaltete Automatiken starten wieder"
                : "Not-Aus: alle Automatiken sofort hart stoppen"
            }
            className={`rounded px-1.5 py-0.5 font-semibold ${
              auto.notaus
                ? "bg-red-600 text-white"
                : "text-red-600 hover:bg-red-100 dark:text-red-400 dark:hover:bg-red-950"
            }`}
          >
            ⏻ {auto.notaus ? "Not-Aus aktiv" : "Not-Aus"}
          </button>
        )}
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
        {agents.map((a) => {
          const st = auto.agents?.[a];
          return (
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
              {st && (st.status !== "aus" || st.gewuenscht) && (
                <span
                  title={`Automatik: ${st.status}`}
                  className={`ml-1 inline-block h-1.5 w-1.5 rounded-full align-middle ${
                    AUTO_DOT[st.status] || "bg-slate-400"
                  }`}
                />
              )}
            </button>
          );
        })}
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto p-3 text-xs">
        {autoInfo && (
          <div className="flex items-center gap-2 rounded bg-slate-50 p-1.5 dark:bg-slate-800">
            <button
              onClick={toggleAutomatik}
              disabled={auto.notaus || (!autoInfo.gewuenscht && !autoInfo.startbar)}
              title={
                auto.notaus
                  ? "Not-Aus aktiv — erst lösen"
                  : autoInfo.gewuenscht
                    ? "Automatik ausschalten (laufender Task darf fertig werden)"
                    : autoInfo.startbar
                      ? "Automatik einschalten: Inbox selbständig abarbeiten"
                      : "keine nutzbare SSH-Verbindung (key_file fehlt?)"
              }
              className={`rounded px-2 py-0.5 font-semibold disabled:opacity-40 ${
                autoInfo.gewuenscht
                  ? "bg-sky-500 text-white"
                  : "bg-slate-200 text-slate-700 hover:bg-slate-300 dark:bg-slate-700 dark:text-slate-200 dark:hover:bg-slate-600"
              }`}
            >
              ▶ Automatik
            </button>
            <StatusBadge
              status={
                { an: "running", fehler: "error" }[autoInfo.status] || autoInfo.status
              }
            />
            <span
              className="flex-1 truncate text-slate-500 dark:text-slate-400"
              title={(autoInfo.log || []).join("\n")}
            >
              {auto.notaus ? "Not-Aus aktiv" : autoInfo.detail}
            </span>
          </div>
        )}
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
