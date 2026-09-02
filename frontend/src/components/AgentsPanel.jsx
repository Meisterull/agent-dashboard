import { useEffect, useRef, useState } from "react";
import {
  closeTask,
  getAgents,
  getAutomatik,
  getTasks,
  markEnvelopeRead,
  markInboxRead,
  setAutomatik,
  setNotaus,
} from "../api";
import { bestaetigen, melden } from "./Dialog";
import RollenDialog from "./RollenDialog";
import ZeitplaeneDialog from "./ZeitplaeneDialog";
import { t } from "../sprache";

const STATUS_COLORS = {
  pending: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  running: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  done: "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
  error: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  needs_confirm:
    "bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300",
  gesperrt: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
};

function StatusBadge({ status }) {
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
        STATUS_COLORS[status] ||
        "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
      }`}
    >
      {t(status)}
    </span>
  );
}

// Automatikmodus (Issue #12): Punkt-Farbe = ECHTER Watcher-Zustand des Agenten.
const AUTO_DOT = {
  an: "bg-green-500",
  startet: "bg-amber-400 animate-pulse",
  stoppt: "bg-amber-400 animate-pulse",
  fehler: "bg-red-500",
  // Review N: der Zustand existierte im Backend (rc=1-Sperre), war im
  // Panel aber unsichtbar — der Toggle wirkte einfach "kaputt".
  gesperrt: "bg-red-700",
};

// Pollt alle 8 s die Mailboxen ALLER Agenten (nicht nur des angezeigten):
// die Anzeige aktualisiert sich so von selbst, und Statuswechsel auf
// done/error/needs_confirm melden sich über onAttention nach oben (App lässt
// dann den Agenten-Reiter rot blinken). Der erste Durchlauf ist nur Basis —
// alte fertige Tasks sollen beim Laden der Seite nicht blinken.
const ALERT_STATUS = ["done", "error", "needs_confirm", "neu"];

// Nicht-Task-Eingänge (Issue #33): die lagen zwar immer in der Inbox, waren am
// Dashboard aber unsichtbar — nur die MCP-Tools kamen dran. Deutsche Etiketten,
// weil die Karte sonst "message"/"response" zeigt und niemand den Unterschied
// zwischen Antwort (auf meine Rückfrage) und Ergebnis (eines Tasks) kennt.
const KIND_LABEL = {
  message: "Nachricht",
  answer: "Antwort",
  response: "Ergebnis",
  question: "Rückfrage",
};

function kurzZeit(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const heute = new Date().toDateString() === d.toDateString();
  return heute
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { day: "2-digit", month: "2-digit" });
}

// Tokenzahlen kompakt (Verbrauchszähler, St.3): 1234 → "1 k", 2500000 → "2.5 M".
const fmtTok = (n) =>
  n >= 1e6
    ? (n / 1e6).toFixed(1) + " M"
    : n >= 1e3
      ? Math.round(n / 1e3) + " k"
      : String(n || 0);

// Verdecktes Panel (Handy-Tab/Tab-Modus): so lange darf der letzte Stand alt
// sein, bevor der 8-s-Takt wieder lädt — das Blinken kommt dann etwas später,
// dafür ruckelt das sichtbare Panel beim Scrollen nicht von fremden Salven.
const VERDECKT_MS = 30000;

export default function AgentsPanel({ refreshKey, sichtbar = true, onAttention }) {
  const [agents, setAgents] = useState([]);
  const [selected, setSelected] = useState(null);
  const [tasksByAgent, setTasksByAgent] = useState({});
  const [auto, setAuto] = useState({ notaus: false, agents: {} });
  const [localKey, setLocalKey] = useState(0); // ↻-Button
  const [offline, setOffline] = useState(false); // letzter Poll fehlgeschlagen
  const [logOffen, setLogOffen] = useState(false); // Automatik-Log aufgeklappt
  const [rollenOffen, setRollenOffen] = useState(false); // Rollen-Dialog (St.1)
  const [plaeneOffen, setPlaeneOffen] = useState(false); // Zeitpläne-Dialog (St.2)
  const [verbrauchOffen, setVerbrauchOffen] = useState(false); // Tages-Aufriss (St.3)
  const [raeumt, setRaeumt] = useState(false); // "alles gelesen" läuft gerade
  // Antippen klappt eine Task-Karte auf (F1 light): Instruction/Ergebnis
  // sind sonst auf eine truncate-Zeile gestutzt und der Volltext war gar
  // nicht erreichbar — auf dem Handy gibt es auch keine Tooltips.
  const [offen, setOffen] = useState(() => new Set()); // "box/task_id"
  const toggleOffen = (key) =>
    setOffen((s) => {
      const n = new Set(s);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });
  const prevRef = useRef(null); // "agent/box/task_id" -> status
  const tasksRef = useRef({}); // letzter bekannter Stand je Agent
  const onAttentionRef = useRef(onAttention);
  onAttentionRef.current = onAttention;
  const sichtbarRef = useRef(sichtbar);
  sichtbarRef.current = sichtbar;
  const letzterLoadRef = useRef(0);
  const loadRef = useRef(null);

  useEffect(() => {
    let stale = false;
    const load = async () => {
      letzterLoadRef.current = Date.now();
      try {
        getAutomatik()
          .then((a) => !stale && setAuto(a))
          .catch(() => {});
        const d = await getAgents();
        if (stale) return;
        setAgents(d.agents);
        // Verschwundener Agent (Verbindung gelöscht) → Auswahl zurücksetzen
        // (Review N: das Panel zeigte sonst dauerhaft einen Geist).
        setSelected((s) =>
          s && d.agents.includes(s) ? s : d.agents[0] || null,
        );
        const pairs = await Promise.all(
          d.agents.map((a) =>
            getTasks(a)
              .then((t) => [a, t])
              .catch(() => null),
          ),
        );
        if (stale) return;
        const byAgent = Object.fromEntries(pairs.filter(Boolean));
        // Agenten, deren Task-Abruf gescheitert ist, behalten ihren letzten
        // Stand — sonst verschwinden ihre Aufgaben kurz aus dem Panel und
        // blinken beim nächsten erfolgreichen Poll als „neu" wieder auf.
        const merged = Object.fromEntries(
          d.agents.map((a) => [a, byAgent[a] || tasksRef.current[a]]),
        );
        tasksRef.current = merged;
        setOffline(pairs.some((p) => !p));
        setTasksByAgent(merged);
        const snap = {};
        for (const [a, t] of Object.entries(merged)) {
          for (const box of ["inbox", "outbox"])
            for (const task of t?.[box] || [])
              snap[`${a}/${box}/${task.task_id}`] = task.status;
          // Eine neue Nachricht soll genauso auffallen wie ein fertiger Task
          // (Issue #33): unbekannter Schlüssel + Status "neu" -> Reiter blinkt.
          for (const m of t?.messages || []) snap[`${a}/msg/${m.id}`] = "neu";
        }
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
        // Letzten bekannten Stand STEHEN lassen — ein einzelner
        // fehlgeschlagener Poll (Reload des Containers, kurzer Netzhänger)
        // darf das Panel nicht leerräumen; nur der Hinweis geht an.
        if (!stale) setOffline(true);
      }
    };
    loadRef.current = load;
    load();
    // Im Hintergrund-Tab nicht pollen (7 Requests alle 8 s); beim
    // Zurückkommen sofort einmal laden statt bis zum nächsten Takt zu warten.
    // Verdeckt (anderer Panel-Tab offen) reicht der gestreckte Takt.
    const t = setInterval(() => {
      if (document.hidden) return;
      if (!sichtbarRef.current && Date.now() - letzterLoadRef.current < VERDECKT_MS)
        return;
      load();
    }, 8000);
    const onVisible = () => {
      if (!document.hidden) load();
    };
    document.addEventListener("visibilitychange", onVisible);
    // Live-Events (F4): Mailbox-Änderung → sofort nachladen statt bis zum
    // nächsten 8-s-Poll zu warten; das Polling bleibt Fallback. Verdeckt
    // NICHT sofort laden — während eines Agenten-Laufs käme sonst alle paar
    // Sekunden eine Salve, die das sichtbare Panel beim Scrollen ruckeln
    // lässt; der gestreckte Takt holt den Stand (und das Blinken) nach.
    const onLive = () => {
      if (!document.hidden && sichtbarRef.current) load();
    };
    window.addEventListener("live:mailbox", onLive);
    return () => {
      stale = true;
      clearInterval(t);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("live:mailbox", onLive);
    };
  }, [refreshKey, localKey]);

  // Beim Aufdecken sofort aktualisieren — der gestreckte Verdeckt-Takt darf
  // bis zu 30 s alten Stand zeigen, der Blick aufs Panel aber nicht.
  const warSichtbarRef = useRef(sichtbar);
  useEffect(() => {
    if (sichtbar && !warSichtbarRef.current) loadRef.current?.();
    warSichtbarRef.current = sichtbar;
  }, [sichtbar]);

  const tasks = selected ? tasksByAgent[selected] : null;
  const autoInfo = selected ? auto.agents?.[selected] : null;
  // Aufklappbares Fortschritts-Log (Watcher-Ausgabe, Issue #18): nur beim
  // Dazukommen neuer Zeilen ans Ende scrollen — sonst würde der 8-s-Poll den
  // Leser alle paar Sekunden nach unten reißen.
  const logText = (autoInfo?.log || []).join("\n");
  const logRef = useRef(null);
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logText, logOffen]);
  const autoAktiv = Object.values(auto.agents || {}).some((a) =>
    ["an", "startet", "stoppt"].includes(a.status),
  );

  // Hängengebliebenen Task von Hand abschließen (Agent antwortet nicht mehr).
  const forceClose = async (taskId) => {
    if (
      !(await bestaetigen({
        title: t("Task schließen"),
        text: t("Task {0} ohne Ergebnis schließen?", taskId),
        ok: t("Schließen"),
        danger: true,
      }))
    )
      return;
    try {
      await closeTask(selected, taskId);
      setLocalKey((k) => k + 1);
    } catch (e) {
      melden({ title: t("Fehler"), text: t("Schließen fehlgeschlagen: {0}", e.message) });
    }
  };

  // Inbox aufräumen (Issue #21): Antworten stapeln sich, wenn hier jemand nur
  // beauftragt und mitliest — mark_read ruft dann nie einer. Ein Klick statt
  // 70. Offene Tasks und Rückfragen fasst der Server dabei nicht an.
  const inboxLeeren = async () => {
    if (
      !(await bestaetigen({
        title: t("Inbox aufräumen"),
        text: t(
          "Alle erledigten Eingänge von '{0}' ins Archiv legen?\nOffene Tasks und Rückfragen bleiben liegen.",
          selected,
        ),
        ok: t("Archivieren"),
      }))
    )
      return;
    setRaeumt(true);
    try {
      const { archiviert } = await markInboxRead(selected);
      setLocalKey((k) => k + 1);
      if (!archiviert)
        melden({ title: t("Inbox"), text: t("Nichts zu archivieren — die Inbox ist schon sauber.") });
    } catch (e) {
      melden({ title: t("Fehler"), text: t("Aufräumen fehlgeschlagen: {0}", e.message) });
    } finally {
      setRaeumt(false);
    }
  };

  // Eine gelesene Nachricht wegräumen (Issue #33). Offene Rückfragen bleiben
  // davon ausgenommen: sie hier zu archivieren nähme sie aus dem Banner,
  // ohne dass jemand geantwortet hätte — dafür gibt es das ✕ dort (#23).
  const nachrichtArchivieren = async (id) => {
    try {
      await markEnvelopeRead(selected, id);
      setLocalKey((k) => k + 1);
    } catch (e) {
      melden({ title: t("Fehler"), text: t("Archivieren fehlgeschlagen: {0}", e.message) });
    }
  };

  // Automatikmodus: Watcher auf dem Agenten-PC per Klick an/aus (Issue #12).
  const toggleAutomatik = async () => {
    const ziel = !autoInfo.gewuenscht;
    if (
      ziel &&
      !(await bestaetigen({
        title: t("Automatik einschalten"),
        text: t(
          "Automatik für '{0}' einschalten?\nClaude Code arbeitet dann UNBEAUFSICHTIGT Tasks aus der Inbox ab.",
          selected,
        ),
        ok: t("Einschalten"),
      }))
    )
      return;
    try {
      setAuto(await setAutomatik(selected, ziel));
    } catch (e) {
      melden({ title: t("Fehler"), text: t("Umschalten fehlgeschlagen: {0}", e.message) });
    }
  };

  const toggleNotaus = async () => {
    if (
      !auto.notaus &&
      !(await bestaetigen({
        title: t("Not-Aus"),
        text: t("Not-Aus: ALLE Automatiken sofort hart stoppen?"),
        ok: t("Stoppen"),
        danger: true,
      }))
    )
      return;
    try {
      setAuto(await setNotaus(!auto.notaus));
    } catch (e) {
      melden({ title: t("Fehler"), text: t("Not-Aus fehlgeschlagen: {0}", e.message) });
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-1 border-b bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
        <span className="flex-1">{t("MCP-Monitor · Aufgaben")}</span>
        {offline && (
          <span
            title={t("Letzte Aktualisierung fehlgeschlagen — angezeigt wird der letzte bekannte Stand.")}
            className="rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-700 dark:bg-amber-950 dark:text-amber-300"
          >
            {t("Verbindung gestört")}
          </span>
        )}
        {(autoAktiv || auto.notaus) && (
          <button
            onClick={toggleNotaus}
            title={
              auto.notaus
                ? t("Not-Aus lösen — eingeschaltete Automatiken starten wieder")
                : t("Not-Aus: alle Automatiken sofort hart stoppen")
            }
            className={`rounded px-1.5 py-0.5 font-semibold ${
              auto.notaus
                ? "bg-red-600 text-white"
                : "text-red-600 hover:bg-red-100 dark:text-red-400 dark:hover:bg-red-950"
            }`}
          >
            ⏻ {auto.notaus ? t("Not-Aus aktiv") : t("Not-Aus")}
          </button>
        )}
        <button
          onClick={() => setPlaeneOffen(true)}
          title={t("Zeitpläne — Tasks zur Uhrzeit, einmalig oder wiederkehrend")}
          className="rounded px-1.5 py-0.5 hover:bg-slate-200 dark:hover:bg-slate-800"
        >
          ⏰
        </button>
        <button
          onClick={() => setRollenOffen(true)}
          title={t("Rollen verwalten — Prompt und Rechte je Task-Lauf")}
          className="rounded px-1.5 py-0.5 hover:bg-slate-200 dark:hover:bg-slate-800"
        >
          {t("Rollen")}
        </button>
        <button
          onClick={() => setLocalKey((k) => k + 1)}
          title={t("jetzt aktualisieren")}
          className="rounded px-1.5 py-0.5 hover:bg-slate-200 dark:hover:bg-slate-800"
        >
          ↻
        </button>
      </div>
      <div className="flex flex-wrap gap-1 border-b p-2 dark:border-slate-700">
        {agents.length === 0 && (
          <span className="text-xs text-slate-400">{t("keine Agenten")}</span>
        )}
        {agents.map((a) => {
          const st = auto.agents?.[a];
          // Ungelesene Nachrichten am Kopf (Issue #33): am Handy sieht man so
          // ohne Aufklappen, bei WEM etwas liegt.
          const post = (tasksByAgent[a]?.messages || []).length;
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
              {post > 0 && (
                <span
                  title={t("{0} ungelesene Nachricht(en)", post)}
                  className={`ml-1 rounded-full px-1 text-[10px] font-semibold ${
                    selected === a
                      ? "bg-white/25 text-white"
                      : "bg-sky-600 text-white"
                  }`}
                >
                  {post}
                </span>
              )}
              {st && (st.status !== "aus" || st.gewuenscht) && (
                <span
                  title={t("Automatik: {0}", t(st.status))}
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
          <div className="rounded bg-slate-50 p-1.5 dark:bg-slate-800">
            <div className="flex items-center gap-2">
              <button
                onClick={toggleAutomatik}
                disabled={auto.notaus || (!autoInfo.gewuenscht && !autoInfo.startbar)}
                title={
                  auto.notaus
                    ? t("Not-Aus aktiv — erst lösen")
                    : autoInfo.gewuenscht
                      ? t("Automatik ausschalten (laufender Task darf fertig werden)")
                      : autoInfo.startbar
                        ? t("Automatik einschalten: Inbox selbständig abarbeiten")
                        : t("keine nutzbare SSH-Verbindung (key_file fehlt?)")
                }
                className={`rounded px-2 py-0.5 font-semibold disabled:opacity-40 ${
                  autoInfo.gewuenscht
                    ? "bg-sky-500 text-white"
                    : "bg-slate-200 text-slate-700 hover:bg-slate-300 dark:bg-slate-700 dark:text-slate-200 dark:hover:bg-slate-600"
                }`}
              >
                ▶ {t("Automatik")}
              </button>
              <StatusBadge
                status={
                  { an: "running", fehler: "error" }[autoInfo.status] || autoInfo.status
                }
              />
              <span className="flex-1 truncate text-slate-500 dark:text-slate-400">
                {auto.notaus ? t("Not-Aus aktiv") : autoInfo.detail}
              </span>
              {/* Fortschritts-Log aufklappbar statt nur als Hover-Tooltip —
                  auf dem Handy war der Verlauf sonst gar nicht erreichbar. */}
              {logText && (
                <button
                  onClick={() => setLogOffen((v) => !v)}
                  title={logOffen ? t("Log einklappen") : t("Fortschritts-Log anzeigen")}
                  className="shrink-0 rounded px-1 text-slate-400 hover:bg-slate-200 hover:text-slate-600 dark:hover:bg-slate-700 dark:hover:text-slate-300"
                >
                  {logOffen ? "▾" : "▸"} {t("Log ({0})", autoInfo.log.length)}
                </button>
              )}
            </div>
            {logOffen && logText && (
              <pre
                ref={logRef}
                className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-white p-1.5 font-mono text-[10px] leading-snug text-slate-600 dark:bg-slate-900 dark:text-slate-300"
              >
                {logText}
              </pre>
            )}
          </div>
        )}
        {/* Verbrauchszähler (St.3): aus den result-Events der Läufe, vom
            Server aus der Outbox aggregiert. Antippen zeigt die letzten
            7 Tage. Rot = selbst gesetzte 5-h-Schwelle erreicht (Settings) —
            der Planer pausiert dann geplante Tasks dieses Agenten. */}
        {tasks?.verbrauch &&
          (tasks.verbrauch.heute.tasks > 0 ||
            tasks.verbrauch.fenster5h.tasks > 0 ||
            tasks.verbrauch.ueber_schwelle) && (
            <div
              onClick={() => setVerbrauchOffen((v) => !v)}
              className="cursor-pointer rounded bg-slate-50 p-1.5 dark:bg-slate-800"
            >
              <div
                className={`flex flex-wrap items-center gap-x-3 gap-y-0.5 ${
                  tasks.verbrauch.ueber_schwelle
                    ? "font-semibold text-red-600 dark:text-red-400"
                    : "text-slate-500 dark:text-slate-400"
                }`}
              >
                <span>
                  ⚡{" "}
                  {t(
                    "heute: {0} Tasks · {1} Tok · {2} $",
                    tasks.verbrauch.heute.tasks,
                    fmtTok(tasks.verbrauch.heute.tokens),
                    tasks.verbrauch.heute.kosten.toFixed(2),
                  )}
                </span>
                <span>
                  {t("5 h: {0} Tok", fmtTok(tasks.verbrauch.fenster5h.tokens))}
                  {tasks.verbrauch.schwelle > 0
                    ? ` / ${fmtTok(tasks.verbrauch.schwelle)}`
                    : ""}
                </span>
                {tasks.verbrauch.ueber_schwelle && (
                  <span>{t("Schwelle erreicht — geplante Tasks pausieren")}</span>
                )}
              </div>
              {verbrauchOffen && (
                <div className="mt-1 text-[10px] text-slate-500 dark:text-slate-400">
                  {tasks.verbrauch.tage.map((tag) => (
                    <div key={tag.datum}>
                      {tag.datum}: {tag.tasks} · {fmtTok(tag.tokens)} Tok ·{" "}
                      {tag.kosten.toFixed(2)} $
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        {tasks && (
          <>
            <div>
              <div className="mb-1 flex items-center gap-1 font-semibold text-slate-500 dark:text-slate-400">
                <span className="flex-1">{t("Inbox ({0})", tasks.inbox.length)}</span>
                <button
                  onClick={inboxLeeren}
                  disabled={raeumt}
                  title={t("Alle erledigten Eingänge (Antworten, Nachrichten) ins Archiv legen. Offene Tasks und Rückfragen bleiben liegen.")}
                  className="rounded px-1.5 py-0.5 text-[10px] font-medium text-slate-500 hover:bg-slate-200 hover:text-slate-700 disabled:opacity-50 dark:text-slate-400 dark:hover:bg-slate-700 dark:hover:text-slate-200"
                >
                  {raeumt ? "…" : t("✓ alles gelesen")}
                </button>
              </div>
              {tasks.inbox.length === 0 ? (
                <p className="text-slate-400">{t("leer")}</p>
              ) : (
                tasks.inbox.map((tk) => (
                  <div
                    key={tk.task_id}
                    onClick={() => toggleOffen(`inbox/${tk.task_id}`)}
                    className="mb-1 cursor-pointer rounded bg-slate-50 p-1.5 dark:bg-slate-800"
                  >
                    <div className="flex items-center justify-between gap-1">
                      <span className="flex-1 truncate font-mono">{tk.task_id}</span>
                      {tk.rolle && (
                        <span
                          title={t("Rolle dieses Laufs")}
                          className="rounded bg-indigo-100 px-1 py-0.5 text-[10px] font-medium text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
                        >
                          {tk.rolle}
                        </span>
                      )}
                      {tk.nicht_vor && new Date(tk.nicht_vor) > new Date() && (
                        <span
                          title={t("geplant — läuft nicht vor {0}", new Date(tk.nicht_vor).toLocaleString())}
                          className="rounded bg-amber-100 px-1 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-950 dark:text-amber-300"
                        >
                          ⏰ {kurzZeit(tk.nicht_vor)}
                        </span>
                      )}
                      <StatusBadge status={tk.status} />
                      <button
                        onClick={(e) => {
                          e.stopPropagation(); // Karte nicht zusätzlich auf-/zuklappen
                          forceClose(tk.task_id);
                        }}
                        title={t("Task manuell schließen (ohne Ergebnis)")}
                        className="rounded px-1 text-slate-400 hover:bg-slate-200 hover:text-slate-600 dark:hover:bg-slate-700 dark:hover:text-slate-300"
                      >
                        ✕
                      </button>
                    </div>
                    <div
                      className={`${
                        offen.has(`inbox/${tk.task_id}`)
                          ? "whitespace-pre-wrap break-words"
                          : "truncate"
                      } text-slate-500 dark:text-slate-400`}
                    >
                      {tk.instruction}
                    </div>
                  </div>
                ))
              )}
            </div>
            {/* Nachrichten (Issue #33): alles, was KEIN Task ist — Hinweise
                anderer Agenten, Antworten auf Rückfragen, Ergebnisse
                delegierter Tasks. Lag bisher unsichtbar in der Inbox. */}
            <div>
              <div className="mb-1 font-semibold text-slate-500 dark:text-slate-400">
                {t("Nachrichten ({0})", (tasks.messages || []).length)}
              </div>
              {(tasks.messages || []).length === 0 ? (
                <p className="text-slate-400">{t("leer")}</p>
              ) : (
                tasks.messages.map((m) => {
                  // Eine offene Rückfrage bekommt KEIN Archivieren-Kreuz: sie
                  // gehört ins Banner (#22/#23), und wegräumen ohne Antwort
                  // ließe den daran geparkten Task für immer warten (#17).
                  const offeneFrage =
                    m.kind === "question" && m.status === "needs_confirm";
                  const key = `msg/${m.id}`;
                  return (
                    <div
                      key={m.id}
                      onClick={() => toggleOffen(key)}
                      className="mb-1 cursor-pointer rounded bg-slate-50 p-1.5 dark:bg-slate-800"
                    >
                      <div className="flex flex-wrap items-center gap-1">
                        <span className="rounded bg-slate-200 px-1 py-0.5 text-[10px] font-medium text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                          {t(KIND_LABEL[m.kind] || m.kind)}
                        </span>
                        <span className="min-w-0 flex-1 truncate font-mono">
                          {m.sender}
                        </span>
                        <span className="shrink-0 text-[10px] text-slate-400">
                          {kurzZeit(m.created_at)}
                        </span>
                        {offeneFrage ? (
                          <StatusBadge status={m.status} />
                        ) : (
                          <button
                            onClick={(e) => {
                              e.stopPropagation(); // Karte nicht auf-/zuklappen
                              nachrichtArchivieren(m.id);
                            }}
                            title={t("gelesen — ins Archiv legen")}
                            className="rounded px-1 text-slate-400 hover:bg-slate-200 hover:text-slate-600 dark:hover:bg-slate-700 dark:hover:text-slate-300"
                          >
                            ✓
                          </button>
                        )}
                      </div>
                      <div
                        className={`${
                          offen.has(key)
                            ? "whitespace-pre-wrap break-words"
                            : "truncate"
                        } text-slate-500 dark:text-slate-400`}
                      >
                        {m.text}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
            <div>
              <div className="mb-1 font-semibold text-slate-500 dark:text-slate-400">
                {t("Outbox ({0})", tasks.outbox.length)}
              </div>
              {tasks.outbox.length === 0 ? (
                <p className="text-slate-400">{t("leer")}</p>
              ) : (
                tasks.outbox.map((tk) => (
                  <div
                    key={tk.task_id}
                    onClick={() => toggleOffen(`outbox/${tk.task_id}`)}
                    className="mb-1 cursor-pointer rounded bg-slate-50 p-1.5 dark:bg-slate-800"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono">{tk.task_id}</span>
                      <StatusBadge status={tk.status} />
                    </div>
                    <div
                      className={`${
                        offen.has(`outbox/${tk.task_id}`)
                          ? "whitespace-pre-wrap break-words"
                          : "truncate"
                      } text-slate-500 dark:text-slate-400`}
                    >
                      {tk.result}
                    </div>
                  </div>
                ))
              )}
            </div>
          </>
        )}
      </div>
      {rollenOffen && <RollenDialog onClose={() => setRollenOffen(false)} />}
      {plaeneOffen && (
        <ZeitplaeneDialog agents={agents} onClose={() => setPlaeneOffen(false)} />
      )}
    </div>
  );
}
