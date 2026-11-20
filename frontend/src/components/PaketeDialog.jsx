import { useEffect, useState } from "react";
import Modal from "./Modal";
import { getPakete, setPaketPhase } from "../api";
import { bestaetigen, melden } from "./Dialog";
import { t } from "../sprache";

// Deploy-Panel (Issue #61): was die Agenten per MCP `deploy_status` gemeldet
// haben — offene Pakete zuerst (die blockieren), darunter der Verlauf.
// Das Dashboard zeigt nur an; eingespielt wird wie gehabt von den Agenten.
const WEG = ["ausgestellt", "uebernommen", "gelaufen", "live"];
const NAME = {
  ausgestellt: "ausgestellt", uebernommen: "übernommen", gelaufen: "eingespielt",
  live: "live", fehler: "Fehler", zurueckgezogen: "zurückgezogen",
};
const zeit = (iso) => (iso ? String(iso).slice(5, 16).replace("T", " ") : "");

function Schritte({ phase }) {
  if (phase === "fehler" || phase === "zurueckgezogen")
    return (
      <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${phase === "fehler" ? "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-200" : "bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300"}`}>
        {t(NAME[phase])}
      </span>
    );
  const stand = WEG.indexOf(phase);
  return (
    <span className="flex flex-wrap items-center gap-1 text-[10px]">
      {WEG.map((p, i) => (
        <span
          key={p}
          className={`rounded px-1.5 py-0.5 ${
            i <= stand
              ? i === stand ? "bg-sky-600 font-medium text-white" : "bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-200"
              : "bg-slate-100 text-slate-400 dark:bg-slate-800"
          }`}
        >
          {t(NAME[p])}
        </span>
      ))}
    </span>
  );
}

export default function PaketeDialog({ onClose, onVorgang }) {
  const [pakete, setPakete] = useState(null);
  const [auf, setAuf] = useState(() => new Set());
  const laden = () =>
    getPakete().then((d) => setPakete(d.pakete)).catch((e) => melden({ title: t("Fehler"), text: String(e.message || e) }));
  useEffect(() => {
    laden();
  }, []);

  const setze = async (p, phase) => {
    if (!(await bestaetigen({ title: t("Paket {0}", p.token), text: t("Phase von Hand auf „{0}“ setzen?", t(NAME[phase])), ok: t(NAME[phase]) })))
      return;
    setPaketPhase(p.token, phase).then(laden).catch((e) => melden({ title: t("Fehler"), text: String(e.message || e) }));
  };

  const offene = (pakete || []).filter((p) => WEG.slice(0, 3).includes(p.phase));
  const knopf = "rounded border border-slate-300 px-2 py-0.5 text-xs dark:border-slate-600";
  return (
    <Modal title={t("Deploy-Pakete")} onClose={onClose} wide>
      <div className="space-y-2 text-sm">
        <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
          <span className="flex-1">
            {pakete === null
              ? t("lädt …")
              : offene.length
                ? t("{0} offen — das älteste seit {1}", offene.length, zeit(offene[0].ausgestellt || offene[0].erstellt))
                : t("kein offenes Paket")}
          </span>
          <button onClick={laden} title={t("jetzt aktualisieren")} className="rounded px-1.5 py-0.5 hover:bg-slate-200 dark:hover:bg-slate-700">↻</button>
        </div>
        {pakete?.length === 0 && (
          <p className="text-xs text-slate-400">
            {t("Noch nichts gemeldet. Agenten melden ein Paket mit dem MCP-Tool deploy_status (Token, Phase, Repos mit erwartetem SHA, Flags wie MIGRATE, enthaltene Tasks).")}
          </p>
        )}
        {(pakete || []).map((p) => {
          const istAuf = auf.has(p.token);
          const offen = WEG.slice(0, 3).includes(p.phase);
          return (
            <div key={p.token} className={`rounded p-2 ${offen ? "bg-sky-50 dark:bg-sky-950/40" : "bg-slate-50 dark:bg-slate-800"}`}>
              <div
                className="flex cursor-pointer flex-wrap items-center gap-2"
                onClick={() => setAuf((s) => { const n = new Set(s); if (n.has(p.token)) n.delete(p.token); else n.add(p.token); return n; })}
              >
                <span className="font-mono font-semibold">{p.token}</span>
                {p.titel && <span className="min-w-0 flex-1 truncate">{p.titel}</span>}
                {(p.flags || []).map((f) => (
                  <span key={f} className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:bg-amber-900/50 dark:text-amber-200">{f}</span>
                ))}
                <Schritte phase={p.phase} />
              </div>
              <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11px] text-slate-400">
                <span>{t("von {0}", p.ausgestellt_von || "?")} · {zeit(p.ausgestellt || p.erstellt)}</span>
                {p.ausstellungen > 1 && <span className="text-amber-600 dark:text-amber-400">{t("{0}. Ausstellung", p.ausstellungen)}</span>}
                {p.ziel && <span>→ {p.ziel}</span>}
                <span>{t("{0} Repos", (p.repos || []).length)}</span>
                {(p.tasks || []).length > 0 && <span>{t((p.tasks || []).length === 1 ? "{0} Task" : "{0} Tasks", p.tasks.length)}</span>}
              </div>
              {istAuf && (
                <div className="mt-2 space-y-2 text-xs">
                  {(p.repos || []).length > 0 && (
                    <div className="overflow-x-auto">
                      {(p.repos || []).map((r) => (
                        <div key={r.name} className="flex gap-2 whitespace-nowrap font-mono">
                          <span className="w-40 shrink-0 truncate">{r.name}</span>
                          <span className="text-slate-500">{(r.sha || "").slice(0, 10)}</span>
                          <span>{r.vorhanden === true ? "✓" : r.vorhanden === false ? "✗" : ""}</span>
                          <span className="truncate font-sans text-slate-500">{r.message}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {(p.vorgaenge || []).length > 0 && (
                    <div className="flex flex-wrap items-center gap-1">
                      {t("Vorgänge")}:
                      {p.vorgaenge.map((v) => (
                        <button key={v} onClick={() => onVorgang?.(v)} className="rounded bg-slate-200 px-1.5 py-0.5 hover:bg-slate-300 dark:bg-slate-700">⛓ {v}</button>
                      ))}
                    </div>
                  )}
                  {(p.tasks || []).length > 0 && <div className="break-all font-mono text-slate-500">{p.tasks.join(" · ")}</div>}
                  <div className="space-y-0.5 border-t border-slate-200 pt-1 dark:border-slate-700">
                    {(p.verlauf || []).map((v, i) => (
                      <div key={i} className="text-slate-500 dark:text-slate-400">
                        <span className="font-mono">{zeit(v.zeit)}</span> · {t(NAME[v.phase] || v.phase)} · {v.von}
                        {v.notiz ? ` — ${v.notiz}` : ""}
                      </div>
                    ))}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {offen && <button onClick={() => setze(p, "live")} className={knopf}>{t("als live markieren")}</button>}
                    {offen && <button onClick={() => setze(p, "zurueckgezogen")} className={knopf}>{t("zurückziehen")}</button>}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Modal>
  );
}
