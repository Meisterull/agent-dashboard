import { useEffect, useRef, useState } from "react";
import {
  getErinnerungen,
  legeErinnerungAn,
  loescheErinnerung,
  verschiebeErinnerung,
} from "../api";
import { bestaetigen, melden, nachfragen } from "./Dialog";
import { t } from "../sprache";

// Erinnerungs-Postfach eines Agenten (Issue #52): Wiedervorlagen, die sich
// der Agent selbst setzt (MCP `remind`) — der Mensch sieht, verschiebt,
// löscht sie und kann selbst eine anlegen.
export default function Erinnerungen({ agent, refreshKey }) {
  const [liste, setListe] = useState([]);
  const [offen, setOffen] = useState(false);
  const wannRef = useRef(null);
  const textRef = useRef(null);
  const agentRef = useRef(agent);
  agentRef.current = agent;

  const laden = () => {
    const wer = agent;
    getErinnerungen(wer)
      .then((d) => agentRef.current === wer && setListe(d.erinnerungen || []))
      .catch(() => {});
  };
  useEffect(() => {
    if (!agent) return undefined;
    setListe([]);
    laden();
    const takt = setInterval(() => !document.hidden && laden(), 60000);
    return () => clearInterval(takt);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent, refreshKey]);

  const fehler = (e) => melden({ title: t("Fehler"), text: String(e.message || e) });
  const anlegen = () => {
    const when = wannRef.current?.value;
    const text = (textRef.current?.value || "").trim();
    if (!when || !text) return;
    legeErinnerungAn(agent, when, text)
      .then(() => {
        textRef.current.value = "";
        laden();
      })
      .catch(fehler);
  };
  const verschieben = async (e) => {
    const neu = await nachfragen({
      title: t("Erinnerung verschieben"),
      label: t("Neuer Termin (JJJJ-MM-TT oder JJJJ-MM-TTThh:mm)"),
      initial: e.faellig.slice(0, 16),
    });
    if (neu) verschiebeErinnerung(agent, e.id, neu).then(laden).catch(fehler);
  };
  const loeschen = async (e) => {
    if (await bestaetigen({ title: t("Erinnerung löschen"), text: e.text, ok: t("Löschen"), danger: true }))
      loescheErinnerung(agent, e.id).then(laden).catch(fehler);
  };

  if (!agent) return null;
  const feld =
    "rounded border border-slate-300 px-1.5 py-1 text-xs dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100";
  return (
    <div className="rounded bg-slate-50 p-1.5 text-xs dark:bg-slate-800">
      <button
        onClick={() => setOffen((v) => !v)}
        className="flex w-full items-center gap-1 text-left text-slate-600 dark:text-slate-300"
      >
        <span>{offen ? "▾" : "▸"}</span>
        <span>⏰ {t("Erinnerungen")}</span>
        {liste.length > 0 && (
          <span className="rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium text-sky-800 dark:bg-sky-900/50 dark:text-sky-200">
            {liste.length}
          </span>
        )}
        {!offen && liste[0] && (
          <span className="truncate text-[11px] text-slate-400">
            · {t("nächste {0}", liste[0].faellig.slice(0, 16).replace("T", " "))}
          </span>
        )}
      </button>
      {offen && (
        <div className="mt-1 space-y-1">
          {liste.length === 0 && <p className="text-slate-400">{t("keine offenen Erinnerungen")}</p>}
          {liste.map((e) => (
            <div key={e.id} className="flex items-start gap-2 rounded bg-white p-1.5 dark:bg-slate-900">
              <span className="shrink-0 font-mono text-slate-500">
                {e.faellig.slice(0, 16).replace("T", " ")}
              </span>
              <span className="min-w-0 flex-1 whitespace-pre-wrap break-words text-slate-700 dark:text-slate-200">
                {e.text}
                <span className="text-slate-400">
                  {" "}
                  · {e.als ? e.als : t("Task bei Automatik, sonst Nachricht")}
                  {e.von && e.von !== agent ? ` · ${t("von {0}", e.von)}` : ""}
                </span>
              </span>
              <button onClick={() => verschieben(e)} title={t("verschieben")} className="shrink-0 text-slate-400 hover:text-sky-600">
                ↷
              </button>
              <button onClick={() => loeschen(e)} title={t("Löschen")} className="shrink-0 text-slate-400 hover:text-red-600">
                ✕
              </button>
            </div>
          ))}
          <div className="flex flex-wrap items-center gap-1 pt-1">
            <input ref={wannRef} type="datetime-local" className={feld} />
            <input
              ref={textRef}
              placeholder={t("Woran soll {0} erinnert werden?", agent)}
              onKeyDown={(ev) => ev.key === "Enter" && anlegen()}
              className={`${feld} min-w-0 flex-1`}
            />
            <button onClick={anlegen} className="rounded bg-slate-700 px-2 py-1 text-xs text-white hover:bg-slate-800">
              {t("Anlegen")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
