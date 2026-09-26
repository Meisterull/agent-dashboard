import { useEffect, useRef, useState } from "react";
import { getEreignisse } from "../api";
import { t } from "../sprache";

// Ereignis-Zeitleiste je Agent (Beobachtbarkeit der Automatik, 26.09.2026).
// Bis dahin war die Automatik eine Blackbox: Dauer, Kosten, verlorene
// Sitzungen (#44), Verweigerungen und Fehlserien standen verstreut in
// Task-Antworten, im 20-Zeilen-Speicher-Log und im Container-Log. Hier steht
// es als Zeitleiste: letzte 50, neueste oben, Probleme farbig, „nur Probleme"
// und „mehr laden". Ein Eintrag mit Task klappt den Task in der Liste auf.
const ICON = {
  lauf: "▶",
  sitzung: "↻",
  verweigert: "⚠",
  timeout: "⏱",
  fehlserie: "⛔",
  watcher_abriss: "⛔",
  watcher_start: "●",
  weckruf: "✉",
  send_file: "📤",
  integration: "🔌",
};

function zeit(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const heute = new Date().toDateString() === d.toDateString();
  const uhr = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return heute ? uhr : `${d.toLocaleDateString([], { day: "2-digit", month: "2-digit" })} ${uhr}`;
}

const FARBE = {
  fehler: "text-red-700 dark:text-red-300",
  warnung: "text-amber-700 dark:text-amber-300",
  info: "text-slate-600 dark:text-slate-300",
};
const HINTERGRUND = {
  fehler: "bg-red-50 dark:bg-red-950/40",
  warnung: "bg-amber-50 dark:bg-amber-950/40",
  info: "",
};

export default function Ereignisse({ agent, onTask }) {
  const [offen, setOffen] = useState(false);
  const [nurProbleme, setNurProbleme] = useState(false);
  const [daten, setDaten] = useState(null); // {eintraege, mehr, gesamt}
  const [fehler, setFehler] = useState(null);
  const [laedt, setLaedt] = useState(false);
  const agentRef = useRef(agent);
  agentRef.current = agent;

  // Kopf-Zähler auch zugeklappt: dafür immer die letzten 50 laden (ETag →
  // 304, wenn nichts Neues da ist).
  const laden = async (vor) => {
    const wer = agent;
    setLaedt(true);
    try {
      const d = await getEreignisse(wer, { vor, probleme: nurProbleme });
      if (agentRef.current !== wer) return;
      setFehler(null);
      setDaten((alt) =>
        vor && alt ? { ...d, eintraege: [...alt.eintraege, ...d.eintraege] } : d,
      );
    } catch (e) {
      if (agentRef.current === wer) setFehler(String(e.message || e));
    } finally {
      if (agentRef.current === wer) setLaedt(false);
    }
  };

  useEffect(() => {
    if (!agent) return undefined;
    setDaten(null);
    laden();
    // Live-Event der Mailbox (auch das Ereignis-Log liegt darunter) →
    // kurz entprellt nachladen; das 8-s-Polling des Panels bleibt Fallback.
    let timer = null;
    const onLive = () => {
      if (document.hidden) return;
      clearTimeout(timer);
      timer = setTimeout(() => laden(), 800);
    };
    window.addEventListener("live:mailbox", onLive);
    const takt = setInterval(() => !document.hidden && laden(), 30000);
    return () => {
      clearTimeout(timer);
      clearInterval(takt);
      window.removeEventListener("live:mailbox", onLive);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent, nurProbleme]);

  const eintraege = daten?.eintraege || [];
  const probleme = eintraege.filter((e) => e.schwere !== "info").length;
  if (!agent) return null;

  return (
    <div className="rounded bg-slate-50 p-1.5 text-xs dark:bg-slate-800">
      <div className="flex items-center gap-2">
        <button
          onClick={() => setOffen((v) => !v)}
          title={offen ? t("Ereignisse einklappen") : t("Ereignisse anzeigen")}
          className="flex min-w-0 flex-1 items-center gap-1 text-left text-slate-600 dark:text-slate-300"
        >
          <span className="shrink-0">{offen ? "▾" : "▸"}</span>
          <span className="truncate">
            {t("Ereignisse")}
            {daten ? ` (${daten.gesamt})` : ""}
          </span>
          {probleme > 0 && (
            <span className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:bg-amber-900/50 dark:text-amber-200">
              ⚠ {probleme}
            </span>
          )}
          {!offen && eintraege[0] && (
            <span className={`truncate text-[11px] ${FARBE[eintraege[0].schwere] || ""}`}>
              · {zeit(eintraege[0].zeit)} {eintraege[0].text}
            </span>
          )}
        </button>
        {offen && (
          <button
            onClick={() => setNurProbleme((v) => !v)}
            aria-pressed={nurProbleme}
            title={t("nur Warnungen und Fehler zeigen")}
            className={`shrink-0 rounded border px-1.5 py-0.5 text-[11px] ${
              nurProbleme
                ? "border-amber-500 bg-amber-500 text-white"
                : "border-slate-300 text-slate-500 dark:border-slate-600 dark:text-slate-400"
            }`}
          >
            {t("nur Probleme")}
          </button>
        )}
      </div>
      {offen && (
        <div className="mt-1 max-h-72 overflow-y-auto">
          {fehler && <p className="px-1 py-0.5 text-red-600 dark:text-red-400">{fehler}</p>}
          {daten && eintraege.length === 0 && (
            <p className="px-1 py-0.5 text-slate-400 dark:text-slate-500">
              {nurProbleme ? t("keine Probleme") : t("noch keine Ereignisse")}
            </p>
          )}
          {eintraege.map((e, i) => (
            <div
              key={`${e.zeit}-${i}`}
              onClick={() => e.task_id && onTask?.(e.task_id)}
              title={e.task_id ? t("Task {0} aufklappen", e.task_id) : e.art}
              className={`flex items-start gap-1.5 rounded px-1 py-0.5 ${HINTERGRUND[e.schwere] || ""} ${
                e.task_id ? "cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700" : ""
              }`}
            >
              <span className="w-10 shrink-0 font-mono text-[10px] text-slate-400 dark:text-slate-500">
                {zeit(e.zeit)}
              </span>
              <span className="w-4 shrink-0 text-center">{ICON[e.art] || "·"}</span>
              <span className={`min-w-0 flex-1 break-words ${FARBE[e.schwere] || ""}`}>
                {e.text}
                {e.task_id && (
                  <span className="ml-1 font-mono text-[10px] text-slate-400 dark:text-slate-500">
                    {e.task_id}
                  </span>
                )}
              </span>
            </div>
          ))}
          {daten?.mehr && (
            <button
              onClick={() => laden(eintraege[eintraege.length - 1]?.zeit)}
              disabled={laedt}
              className="mt-1 w-full rounded border border-slate-300 px-2 py-0.5 text-slate-600 hover:bg-slate-100 disabled:opacity-40 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-700"
            >
              {laedt ? "…" : t("mehr laden")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
