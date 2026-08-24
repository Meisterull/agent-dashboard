import { useEffect, useState } from "react";
import { getConnections, getSshSessions, deleteSshSession } from "../api";
import { bestaetigen, melden } from "./Dialog";
import Terminal, { DEFAULT_SID } from "./Terminal";
import ConnectionsModal from "./ConnectionsModal";

// SSH-Verbindungen als Tabs; pro Verbindung sind MEHRERE Terminals möglich
// (Issue #5): jedes offene Terminal ist ein Tab {name, sid} — das erste hat
// die sid "main", weitere bekommen "2", "3", … (⧉-Knopf). So kann z. B. in
// einem Terminal Claude Code laufen und im zweiten tippt man selbst.
// Einmal geöffnete Terminals bleiben gemountet (nur versteckt), damit die
// SSH-Session den Tab-Wechsel überlebt. Fenster schließen (×) detacht nur:
// die Shell läuft serverseitig weiter (SSH_GRACE_SECONDS) und wird beim
// nächsten Öffnen — auch von einem anderen PC — samt Output-Replay wieder
// angehängt. Beendet wird nur über den eigenen ⏻-Knopf (DELETE-Endpoint)
// oder Shell-Exit. Ein Punkt am Tab markiert laufende Sessions; beim Mount
// und bei Fokus-Rückkehr werden laufende Sessions automatisch wieder geöffnet.
const keyOf = (t) => `${t.name}:${t.sid}`;

export default function TerminalPanel() {
  const [connections, setConnections] = useState([]);
  const [open, setOpen] = useState([]); // offene Terminal-Tabs: {name, sid}
  const [running, setRunning] = useState([]); // serverseitige Sessions: {name, sid}
  const [pendingOpen, setPendingOpen] = useState([]); // beim Mount gefundene Sessions
  const [active, setActive] = useState(null); // "name:sid"
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
    getSshSessions()
      .then((d) => {
        const sess = d.sessions.map((s) => ({ name: s.name, sid: s.sid }));
        setRunning(sess);
        if (autoOpen) setPendingOpen(sess);
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
    const tabs = pendingOpen.filter((t) => connections.some((c) => c.name === t.name));
    if (!tabs.length) return;
    setOpen((o) => [...o, ...tabs.filter((t) => !o.some((x) => keyOf(x) === keyOf(t)))]);
    setPendingOpen((p) => p.filter((t) => !tabs.includes(t)));
  }, [pendingOpen, connections]);

  // Klick auf eine Verbindung ohne offene Tabs: laufende Sessions wieder
  // anhängen (alle — auch die mit sid ≠ main), sonst frisches "main" öffnen.
  const activate = (name) => {
    const runningTabs = running.filter((r) => r.name === name);
    const tabs = runningTabs.length ? runningTabs : [{ name, sid: DEFAULT_SID }];
    setOpen((o) => [...o, ...tabs.filter((t) => !o.some((x) => keyOf(x) === keyOf(t)))]);
    setActive(keyOf(tabs[0]));
  };

  // ⧉ — weiteres Terminal auf derselben Verbindung: kleinste freie Nummer als
  // sid (auch serverseitig laufende zählen, sonst würde eine detachte Session
  // ungewollt übernommen statt eine neue Shell zu starten).
  const openExtra = (name) => {
    const used = new Set(
      [...open, ...running].filter((t) => t.name === name).map((t) => t.sid),
    );
    let n = 2;
    while (used.has(String(n))) n += 1;
    const tab = { name, sid: String(n) };
    setOpen((o) => [...o, tab]);
    setActive(keyOf(tab));
  };

  // × — nur das Fenster schließen; die Session läuft serverseitig weiter.
  const close = (tab) => {
    setOpen((o) => o.filter((t) => keyOf(t) !== keyOf(tab)));
    setActive((a) => (a === keyOf(tab) ? null : a));
  };

  // ⏻ — Session serverseitig beenden (killt die Shell auf dem Agenten-PC).
  // Unumkehrbar (ein laufender claude-Lauf stirbt mit) → Rückfrage. Der echte
  // Zustand kommt aus loadSessions(), nicht aus lokalem Wegfiltern: sonst
  // verschwindet der Punkt auch dann, wenn das Beenden gescheitert ist.
  const endSession = async (tab) => {
    const label = tab.sid === DEFAULT_SID ? tab.name : `${tab.name} ·${tab.sid}`;
    if (
      !(await bestaetigen({
        title: "Session beenden",
        text: `Session „${label}“ wirklich beenden?\nDie Shell auf dem Agenten-PC wird gekillt — Laufendes geht verloren.`,
        ok: "Beenden",
        danger: true,
      }))
    )
      return;
    try {
      await deleteSshSession(tab.name, tab.sid);
      close(tab);
    } catch (e) {
      melden({ title: "Fehler", text: `Beenden fehlgeschlagen: ${e.message}` });
    } finally {
      loadSessions();
    }
  };

  // Shell hat sich selbst beendet (exit) oder wurde anderweitig gekillt.
  const ended = (tab) => {
    close(tab);
    loadSessions();
  };

  const runningDot = (
    <span
      title="Session läuft"
      className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-emerald-500 align-middle"
    />
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-1 border-b bg-slate-50 px-2 py-1 dark:border-slate-700 dark:bg-slate-900">
        <span className="mr-2 text-xs font-semibold text-slate-500 dark:text-slate-400">SSH</span>
        {connections.length === 0 && (
          <span className="text-xs text-slate-400">keine Verbindungen</span>
        )}
        {connections.map((c) => {
          const tabs = open.filter((t) => t.name === c.name);
          if (tabs.length === 0)
            return (
              <button
                key={c.name}
                onClick={() => activate(c.name)}
                title={`${c.user}@${c.host}`}
                className="rounded bg-slate-200 px-2 py-0.5 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300"
              >
                {running.some((r) => r.name === c.name) && runningDot}
                {c.name}
              </button>
            );
          return (
            <span key={c.name} className="inline-flex items-center">
              {tabs.map((t) => (
                <span key={keyOf(t)} className="inline-flex items-center">
                  <button
                    onClick={() => setActive(keyOf(t))}
                    title={`${c.user}@${c.host}`}
                    className={`rounded px-2 py-0.5 text-xs ${
                      active === keyOf(t)
                        ? "bg-slate-700 text-white dark:bg-slate-600"
                        : "bg-emerald-100 text-slate-700 dark:bg-emerald-900 dark:text-emerald-200"
                    }`}
                  >
                    {running.some((r) => keyOf(r) === keyOf(t)) && runningDot}
                    {t.sid === DEFAULT_SID ? c.name : `${c.name} ·${t.sid}`}
                  </button>
                  <button
                    onClick={() => close(t)}
                    title="Fenster schließen (Session läuft weiter)"
                    className="ml-0.5 rounded px-1 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-300"
                  >
                    ×
                  </button>
                  <button
                    onClick={() => endSession(t)}
                    title="Session beenden"
                    className="rounded px-1 text-xs text-slate-400 hover:bg-red-100 hover:text-red-600 dark:hover:bg-red-950 dark:hover:text-red-400"
                  >
                    ⏻
                  </button>
                </span>
              ))}
              <button
                onClick={() => openExtra(c.name)}
                title="Weiteres Terminal auf dieser Verbindung öffnen"
                className="rounded px-1 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-300"
              >
                ⧉
              </button>
            </span>
          );
        })}
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
        {open.map((t) => (
          <div
            key={keyOf(t)}
            className={`absolute inset-0 ${active === keyOf(t) ? "" : "invisible"}`}
          >
            <Terminal
              name={t.name}
              sid={t.sid}
              visible={active === keyOf(t)}
              onEnded={() => ended(t)}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
