import { createContext, useContext, useEffect, useRef, useState } from "react";
import { getConnections, getProjekte, getSshSessions, deleteSshSession } from "../api";
import { bestaetigen, melden } from "./Dialog";
import Terminal from "./Terminal";
import ConnectionsModal from "./ConnectionsModal";
import { lsGet, lsSet } from "../speicher";
import {
  MAX_FENSTER,
  fensterListe,
  freieSid,
  heimholen,
  istProjektSid,
  keyOf,
  neueNummer,
  tabLabel,
  tabsFuerKlick,
  tabsFuerProjekt,
} from "../terminalFenster";
import { t } from "../sprache";

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
//
// MEHRERE TERMINAL-FENSTER (07.10.2026): Das ganze Panel lässt sich
// verdoppeln (◫ rechts in der Leiste) und liegt dann als eigenes Fenster
// daneben. Die Tabs gehören allen Fenstern gemeinsam und tragen die Nummer
// ihres Fensters — warum, steht in terminalFenster.js. Deshalb lebt der
// Zustand hier im <TerminalVorrat> (einmal, über der Arbeitsfläche) und nicht
// mehr im Panel; jedes <TerminalPanel n={…}> zeigt nur seine Tabs.
//
// PROJEKT-TERMINALS (11.10.2026): Je Verbindung eine Liste von Projekten
// (Dialog „+" → Projekte, /api/projekte). 📁 an der Verbindung klappt sie auf;
// ein Tipp öffnet den Tab „werkstatt · zeitalter" (sid "p:zeitalter"), hinter
// dem der Server eine tmux-Sitzung im Projektordner mit Claude Code startet
// (app/projekte.py). Für die Tab-Logik ist das ein normaler Tab mit eigener
// sid — ⇄, ◫, Auto-Reopen funktionieren unverändert. ⏻ killt die tmux-Sitzung.

const ZUORDNUNG_KEY = "terminal-zuordnung-v1"; // {"name:sid": fensternummer}

function ladeZuordnung() {
  try {
    const z = JSON.parse(lsGet(ZUORDNUNG_KEY));
    return z && typeof z === "object" ? z : {};
  } catch {
    return {};
  }
}

// Welche Terminal-Fenster es gibt: 1 immer, weitere legt ◫ an. Die Liste
// gehört wie die Anordnung zum Bildschirm, vor dem man sitzt (localStorage).
// Die Panels melden ihren Wunsch per Ereignis "terminal:fenster" — so wie
// "workspace:reset" —, weil die Panel-Liste der App gehört.
const FENSTER_KEY = "terminal-fenster-v1";

export function useTerminalFenster() {
  const [liste, setListe] = useState(() => {
    try {
      return fensterListe(JSON.parse(lsGet(FENSTER_KEY)));
    } catch {
      return [1];
    }
  });
  useEffect(() => {
    lsSet(FENSTER_KEY, JSON.stringify(liste));
  }, [liste]);
  useEffect(() => {
    const on = (e) => {
      const { aktion, n } = e.detail || {};
      setListe((l) => {
        if (aktion === "neu" && l.length < MAX_FENSTER)
          return [...l, neueNummer(l)].sort((a, b) => a - b);
        if (aktion === "zu" && n !== 1) return l.filter((x) => x !== n);
        return l;
      });
    };
    window.addEventListener("terminal:fenster", on);
    return () => window.removeEventListener("terminal:fenster", on);
  }, []);
  return liste;
}

const Kontext = createContext(null);

export function TerminalVorrat({ fenster, children }) {
  const [connections, setConnections] = useState([]);
  const [projekte, setProjekte] = useState({}); // {verbindung: [{name, pfad, befehl}]}
  const [open, setOpen] = useState([]); // offene Terminal-Tabs: {name, sid, fenster}
  const [running, setRunning] = useState([]); // serverseitige Sessions: {name, sid}
  const [pendingOpen, setPendingOpen] = useState([]); // beim Mount gefundene Sessions
  const [active, setActive] = useState({}); // je Fenster: "name:sid"

  const aktiviere = (n, key) => setActive((a) => ({ ...a, [n]: key }));

  useEffect(() => {
    const load = () =>
      getConnections()
        .then((d) => setConnections(d.connections))
        .catch(() => setConnections([]));
    load();
    window.addEventListener("connections:changed", load);
    return () => window.removeEventListener("connections:changed", load);
  }, []);

  useEffect(() => {
    const load = () =>
      getProjekte()
        .then((d) => setProjekte(d.projekte || {}))
        .catch(() => setProjekte({}));
    load();
    window.addEventListener("projekte:changed", load);
    window.addEventListener("connections:changed", load);
    return () => {
      window.removeEventListener("projekte:changed", load);
      window.removeEventListener("connections:changed", load);
    };
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
  // die es (noch) gibt — sonst gäbe es Terminals ohne Tab. Jeder Tab kommt in
  // das Fenster zurück, in dem er zuletzt lag (gemerkt im localStorage); gibt
  // es das nicht mehr, ins erste.
  useEffect(() => {
    const tabs = pendingOpen.filter((t) => connections.some((c) => c.name === t.name));
    if (!tabs.length) return;
    const gemerkt = ladeZuordnung();
    const mitFenster = tabs.map((t) => ({
      ...t,
      fenster: fenster.includes(gemerkt[keyOf(t)]) ? gemerkt[keyOf(t)] : 1,
    }));
    setOpen((o) => [...o, ...mitFenster.filter((t) => !o.some((x) => keyOf(x) === keyOf(t)))]);
    setPendingOpen((p) => p.filter((t) => !tabs.includes(t)));
  }, [pendingOpen, connections]);

  // Ein Fenster wurde geschlossen: Seine Terminals wandern ins erste — die
  // Sessions laufen weiter, niemand verliert etwas.
  const fensterKey = fenster.join("|");
  useEffect(() => {
    setOpen((o) => heimholen(o, fenster));
  }, [fensterKey]);

  // Wer liegt wo — für das nächste Laden merken. ERGÄNZEN, nicht ersetzen:
  // Direkt nach dem Laden ist `open` noch leer, und ein Ersetzen löschte die
  // Zuordnung, bevor das Auto-Reopen sie lesen kann. Einträge zu längst
  // beendeten Sessions stören nicht — sie greifen nur, wenn es Name und
  // Nummer wieder gibt.
  useEffect(() => {
    if (!open.length) return;
    lsSet(
      ZUORDNUNG_KEY,
      JSON.stringify({ ...ladeZuordnung(), ...Object.fromEntries(open.map((t) => [keyOf(t), t.fenster])) }),
    );
  }, [open]);

  // Klick auf eine Verbindung ohne Tab in DIESEM Fenster — was dann geöffnet
  // wird, entscheidet tabsFuerKlick (nie dem Nachbarfenster etwas wegnehmen).
  const activate = (name, n) => {
    const tabs = tabsFuerKlick(name, n, open, running);
    setOpen((o) => [...o, ...tabs.filter((t) => !o.some((x) => keyOf(x) === keyOf(t)))]);
    aktiviere(n, keyOf(tabs[0]));
  };

  // ⧉ — weiteres Terminal auf derselben Verbindung, in diesem Fenster.
  const openExtra = (name, n) => {
    const tab = { name, sid: freieSid(name, open, running), fenster: n };
    setOpen((o) => [...o, tab]);
    aktiviere(n, keyOf(tab));
  };

  // 📁 — Projekt-Terminal in diesem Fenster öffnen (oder aus einem anderen
  // Fenster herüberholen — die Session hat nur einen Client).
  const vorratOpenRef = useRef(open);
  vorratOpenRef.current = open;
  const openProjekt = (name, projekt, n) => {
    const neu = tabsFuerProjekt(name, projekt, n, vorratOpenRef.current);
    const tab = neu.find((t) => t.name === name && t.sid === `p:${projekt}`);
    setOpen(neu);
    setActive((a) => ({
      ...Object.fromEntries(
        Object.entries(a).map(([f, k]) => [f, k === keyOf(tab) && Number(f) !== n ? null : k]),
      ),
      [n]: keyOf(tab),
    }));
    if (neu !== vorratOpenRef.current)
      setTimeout(() => window.dispatchEvent(new Event("resize")), 50);
  };

  // × — nur das Fenster schließen; die Session läuft serverseitig weiter.
  const close = (tab) => {
    setOpen((o) => o.filter((t) => keyOf(t) !== keyOf(tab)));
    setActive((a) =>
      Object.fromEntries(Object.entries(a).map(([n, k]) => [n, k === keyOf(tab) ? null : k])),
    );
  };

  // ⇄ — Tab ins nächste Terminal-Fenster legen. Das Terminal wird dort neu
  // eingehängt und holt sich die Session samt bisheriger Ausgabe zurück.
  const verschieben = (tab) => {
    const ziel = fenster[(fenster.indexOf(tab.fenster) + 1) % fenster.length];
    if (ziel === tab.fenster) return;
    setOpen((o) => o.map((t) => (keyOf(t) === keyOf(tab) ? { ...t, fenster: ziel } : t)));
    setActive((a) => ({
      ...a,
      [tab.fenster]: a[tab.fenster] === keyOf(tab) ? null : a[tab.fenster],
      [ziel]: keyOf(tab),
    }));
    setTimeout(() => window.dispatchEvent(new Event("resize")), 50);
  };

  // ⏻ — Session serverseitig beenden (killt die Shell auf dem Agenten-PC).
  // Unumkehrbar (ein laufender claude-Lauf stirbt mit) → Rückfrage. Der echte
  // Zustand kommt aus loadSessions(), nicht aus lokalem Wegfiltern: sonst
  // verschwindet der Punkt auch dann, wenn das Beenden gescheitert ist.
  const endSession = async (tab) => {
    const label = tabLabel(tab.name, tab.sid);
    const projekt = istProjektSid(tab.sid);
    if (
      !(await bestaetigen({
        title: projekt ? t("Projekt-Terminal beenden") : t("Session beenden"),
        text: projekt
          ? t(
              "Projekt-Terminal „{0}“ wirklich beenden?\nDie tmux-Sitzung auf dem Zielrechner wird samt Claude Code gekillt. Die Unterhaltung bleibt — `claude --continue` holt sie zurück.",
              label,
            )
          : t(
              "Session „{0}“ wirklich beenden?\nDie Shell auf dem Agenten-PC wird gekillt — Laufendes geht verloren.",
              label,
            ),
        ok: t("Beenden"),
        danger: true,
      }))
    )
      return;
    try {
      await deleteSshSession(tab.name, tab.sid);
      close(tab);
    } catch (e) {
      melden({ title: t("Fehler"), text: t("Beenden fehlgeschlagen: {0}", e.message) });
    } finally {
      loadSessions();
    }
  };

  // Shell hat sich selbst beendet (exit) oder wurde anderweitig gekillt.
  const ended = (tab) => {
    close(tab);
    loadSessions();
  };

  return (
    <Kontext.Provider
      value={{
        fenster, connections, projekte, open, running, active,
        aktiviere, activate, openExtra, openProjekt, close, verschieben, endSession, ended,
      }}
    >
      {children}
    </Kontext.Provider>
  );
}

// Fenster öffnen/schließen entscheidet die App (sie hält die Panel-Liste) —
// das Panel meldet den Wunsch nur, wie "workspace:reset" es auch tut.
const fensterWunsch = (aktion, n) =>
  window.dispatchEvent(new CustomEvent("terminal:fenster", { detail: { aktion, n } }));

// 📁 mit aufklappbarer Projektliste einer Verbindung. Schließt bei Klick
// daneben oder Escape; ein Tipp auf ein Projekt öffnet dessen Terminal.
function ProjektMenue({ name, projekte, onWahl }) {
  const [auf, setAuf] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!auf) return;
    const zu = (e) => {
      if (e.type === "keydown" ? e.key === "Escape" : !ref.current?.contains(e.target)) setAuf(false);
    };
    document.addEventListener("pointerdown", zu);
    document.addEventListener("keydown", zu);
    return () => {
      document.removeEventListener("pointerdown", zu);
      document.removeEventListener("keydown", zu);
    };
  }, [auf]);
  return (
    <span ref={ref} className="relative inline-flex">
      <button
        onClick={() => setAuf((a) => !a)}
        title={t("Projekt-Terminal öffnen (Claude Code in tmux)")}
        data-test={`projekte-${name}`}
        className="rounded px-1 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-300"
      >
        📁
      </button>
      {auf && (
        <div className="absolute left-0 top-full z-20 mt-1 min-w-[10rem] rounded border border-slate-300 bg-white py-1 shadow-lg dark:border-slate-600 dark:bg-slate-800">
          {projekte.map((p) => (
            <button
              key={p.name}
              onClick={() => {
                setAuf(false);
                onWahl(p.name);
              }}
              title={`${p.pfad} — ${p.befehl}`}
              className="block w-full px-3 py-1 text-left text-xs text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-700"
            >
              {p.name}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

export default function TerminalPanel({ n = 1 }) {
  const {
    fenster, connections, projekte, running, aktiviere, activate, openExtra, openProjekt,
    close, verschieben, endSession, ended, ...vorrat
  } = useContext(Kontext);
  const open = vorrat.open.filter((tab) => tab.fenster === n);
  const active = vorrat.active[n] ?? null;
  const setActive = (key) => aktiviere(n, key);
  const [showManage, setShowManage] = useState(false);

  const runningDot = (
    <span
      title={t("Session läuft")}
      className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-emerald-500 align-middle"
    />
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-1 border-b bg-slate-50 px-2 py-1 dark:border-slate-700 dark:bg-slate-900">
        <span className="mr-2 text-xs font-semibold text-slate-500 dark:text-slate-400">SSH</span>
        {connections.length === 0 && (
          <span className="text-xs text-slate-400">{t("keine Verbindungen")}</span>
        )}
        {connections.map((c) => {
          const tabs = open.filter((t) => t.name === c.name);
          const projektListe = projekte[c.name] || [];
          const menue = projektListe.length > 0 && (
            <ProjektMenue
              name={c.name}
              projekte={projektListe}
              onWahl={(p) => openProjekt(c.name, p, n)}
            />
          );
          if (tabs.length === 0)
            return (
              <span key={c.name} className="inline-flex items-center">
                <button
                  onClick={() => activate(c.name, n)}
                  title={`${c.user}@${c.host}`}
                  className="rounded bg-slate-200 px-2 py-0.5 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300"
                >
                  {running.some((r) => r.name === c.name) && runningDot}
                  {c.name}
                </button>
                {menue}
              </span>
            );
          return (
            <span key={c.name} className="inline-flex items-center">
              {tabs.map((tab) => (
                <span key={keyOf(tab)} className="inline-flex items-center">
                  <button
                    onClick={() => setActive(keyOf(tab))}
                    title={`${c.user}@${c.host}`}
                    className={`rounded px-2 py-0.5 text-xs ${
                      active === keyOf(tab)
                        ? "bg-slate-700 text-white dark:bg-slate-600"
                        : "bg-emerald-100 text-slate-700 dark:bg-emerald-900 dark:text-emerald-200"
                    }`}
                  >
                    {running.some((r) => keyOf(r) === keyOf(tab)) && runningDot}
                    {tabLabel(c.name, tab.sid)}
                  </button>
                  {fenster.length > 1 && (
                    <button
                      onClick={() => verschieben(tab)}
                      title={t("In das nächste Terminal-Fenster legen")}
                      className="ml-0.5 rounded px-1 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-300"
                    >
                      ⇄
                    </button>
                  )}
                  <button
                    onClick={() => close(tab)}
                    title={t("Fenster schließen (Session läuft weiter)")}
                    className="ml-0.5 rounded px-1 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-300"
                  >
                    ×
                  </button>
                  <button
                    onClick={() => endSession(tab)}
                    title={istProjektSid(tab.sid) ? t("Projekt-Terminal beenden") : t("Session beenden")}
                    className="rounded px-1 text-xs text-slate-400 hover:bg-red-100 hover:text-red-600 dark:hover:bg-red-950 dark:hover:text-red-400"
                  >
                    ⏻
                  </button>
                </span>
              ))}
              <button
                onClick={() => openExtra(c.name, n)}
                title={t("Weiteres Terminal auf dieser Verbindung öffnen")}
                className="rounded px-1 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-300"
              >
                ⧉
              </button>
              {menue}
            </span>
          );
        })}
        <button
          onClick={() => setShowManage(true)}
          title={t("Verbindungen verwalten / neue anlegen")}
          className="ml-1 shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-xs text-slate-500 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-800"
        >
          +
        </button>
        {/* Rechts außen: das ganze Fenster verdoppeln bzw. ein Zweitfenster
            wieder schließen (seine Terminals wandern ins erste). */}
        <span className="ml-auto inline-flex shrink-0 items-center gap-1">
          {fenster.length < MAX_FENSTER && (
            <button
              onClick={() => fensterWunsch("neu", n)}
              title={t("Terminal-Fenster verdoppeln (legt ein zweites daneben)")}
              data-test="terminal-verdoppeln"
              className="rounded border border-slate-300 px-1.5 py-0.5 text-xs text-slate-500 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-800"
            >
              ◫
            </button>
          )}
          {n !== 1 && (
            <button
              onClick={() => fensterWunsch("zu", n)}
              title={t("Dieses Terminal-Fenster schließen (Sessions laufen weiter)")}
              data-test="terminal-fenster-zu"
              className="rounded border border-slate-300 px-1.5 py-0.5 text-xs text-slate-500 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-800"
            >
              ✕
            </button>
          )}
        </span>
      </div>
      {showManage && <ConnectionsModal onClose={() => setShowManage(false)} />}
      <div className="relative min-h-0 flex-1 bg-slate-800">
        {open.length === 0 && (
          <div className="flex h-full items-center justify-center text-xs text-slate-400">
            {t("Verbindung wählen, um ein Terminal zu öffnen")}
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
