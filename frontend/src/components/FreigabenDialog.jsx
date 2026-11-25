import { useEffect, useRef, useState } from "react";
import Modal from "./Modal";
import { getFreigaben, getZustellung, setFreigabeEinstellung, setZustellung } from "../api";
import { melden } from "./Dialog";
import { t } from "../sprache";

// Freigaben je Agent (Issue #56): Schalter, Wartefrist und die beiden
// Muster-Listen — einsehbar und widerrufbar. Text-Inputs uncontrolled
// (Repo-Regel: Android-Wortvorschläge verdoppeln sonst den Text).
export default function FreigabenDialog({ agent, onClose, children }) {
  const [einst, setEinst] = useState(null);
  const [tool, setTool] = useState("");
  const [fehler, setFehler] = useState(null);
  const anRef = useRef(null);
  const fristRef = useRef(null);
  const immerRef = useRef(null);
  const nieRef = useRef(null);
  const [zustellung, setZustellungStand] = useState(null); // Issue #67
  const sitzungRef = useRef(null);
  const pushRef = useRef(null);

  useEffect(() => {
    getFreigaben()
      .then((d) => {
        setTool(d.tool);
        setEinst(d.agents?.[agent] || { an: false, timeout: 900, immer: [], nie: [] });
      })
      .catch((e) => setFehler(String(e.message || e)));
    getZustellung(agent)
      .then(setZustellungStand)
      .catch(() => setZustellungStand({ sitzung: false, push: false }));
  }, [agent]);

  const speichern = async () => {
    const zeilen = (el) => (el?.value || "").split("\n").map((z) => z.trim()).filter(Boolean);
    try {
      const antwort = await setFreigabeEinstellung(agent, {
        an: !!anRef.current?.checked,
        timeout: Math.max(1, parseInt(fristRef.current?.value, 10) || 15) * 60,
        immer: zeilen(immerRef.current),
        nie: zeilen(nieRef.current),
      });
      if (zustellung)
        await setZustellung(agent, {
          sitzung: !!sitzungRef.current?.checked,
          push: !!pushRef.current?.checked,
        });
      if (antwort.warnung) await melden({ title: t("Hinweis"), text: antwort.warnung });
      onClose();
    } catch (e) {
      setFehler(String(e.message || e));
    }
  };

  const feld =
    "w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-xs dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100";
  return (
    <Modal title={t("Freigaben — {0}", agent)} onClose={onClose}>
      {!einst ? (
        <p className="text-sm text-slate-500">{fehler || t("lädt …")}</p>
      ) : (
        <div className="space-y-3 text-sm text-slate-700 dark:text-slate-200">
          <label className="flex items-start gap-2">
            <input ref={anRef} type="checkbox" defaultChecked={einst.an} className="mt-0.5" />
            <span>
              {t("Freigaben über das Dashboard: braucht ein Automatik-Lauf eine Berechtigung, fragt er hier nach (Banner + Push), statt still abzulehnen.")}
              <span className="block text-xs text-slate-400">
                {t("Wirkt beim nächsten Start der Automatik. Voraussetzung: der Dashboard-MCP ist auf der Maschine registriert ({0}).", tool)}
              </span>
            </span>
          </label>
          <label className="flex items-center gap-2">
            <span>{t("Wartezeit auf deine Antwort (Minuten)")}</span>
            <input
              ref={fristRef}
              type="number"
              min="1"
              max="360"
              defaultValue={Math.round(einst.timeout / 60)}
              className="w-20 rounded border border-slate-300 px-2 py-1 dark:border-slate-600 dark:bg-slate-800"
            />
          </label>
          <label className="block">
            <span className="mb-1 block">
              {t("Immer erlauben (ein Muster je Zeile, z. B. Bash(git status:*) oder Edit)")}
            </span>
            <textarea ref={immerRef} rows={5} defaultValue={einst.immer.join("\n")} className={feld} />
          </label>
          <label className="block">
            <span className="mb-1 block">
              {t("Nie erlauben — wird sofort abgelehnt, ohne Anfrage (z. B. Bash(./deploy-prod.sh:*))")}
            </span>
            <textarea ref={nieRef} rows={4} defaultValue={einst.nie.join("\n")} className={feld} />
          </label>
          {zustellung && (
            <div className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-700">
              <label className="flex items-start gap-2">
                <input ref={sitzungRef} type="checkbox" defaultChecked={zustellung.sitzung} className="mt-0.5" />
                <span>
                  {t("Tasks in die laufende Terminal-Sitzung tippen: ist im Projekt-Terminal Claude Code offen, bekommt es den Task dort (du siehst live mit und kannst dazwischenschreiben). Ist die Sitzung belegt, wartet der Task; ohne offene Sitzung läuft er wie bisher im Hintergrund.")}
                  <span className="block text-xs text-slate-400">
                    {t("Wirkt beim nächsten Start der Automatik. Nur mit tmux (Linux/Mac); der Watcher trägt dafür Hooks in .claude/settings.local.json des Projekts ein.")}
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-2">
                <input ref={pushRef} type="checkbox" defaultChecked={zustellung.push} className="mt-0.5" />
                <span>{t("Push, wenn ein Task in die Sitzung getippt wurde")}</span>
              </label>
            </div>
          )}
          {children}
          {fehler && <p className="text-xs text-red-600">{fehler}</p>}
          <div className="flex justify-end gap-2">
            <button onClick={onClose} className="rounded border border-slate-300 px-3 py-1 text-slate-600 dark:border-slate-600 dark:text-slate-300">
              {t("Abbrechen")}
            </button>
            <button onClick={speichern} className="rounded bg-blue-600 px-3 py-1 font-medium text-white hover:bg-blue-700">
              {t("Speichern")}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
