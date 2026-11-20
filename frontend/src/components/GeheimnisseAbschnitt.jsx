import { useEffect, useRef, useState } from "react";
import {
  getGeheimnisse,
  loescheGeheimnis,
  setzeGeheimnis,
  setzeGeheimnisEnvDatei,
} from "../api";
import { bestaetigen } from "./Dialog";
import { t } from "../sprache";

// Geheimnisse eines Agenten (Issue #64): nur setzbar und ersetzbar, nie
// wieder lesbar. Automatik-Läufe bekommen sie als Umgebungsvariablen —
// im Befehl steht dann $NAME statt des Tokens.
export default function GeheimnisseAbschnitt({ agent }) {
  const [stand, setStand] = useState(null);
  const [fehler, setFehler] = useState(null);
  const nameRef = useRef(null);
  const wertRef = useRef(null);

  useEffect(() => {
    getGeheimnisse(agent).then(setStand).catch((e) => setFehler(String(e.message || e)));
  }, [agent]);

  const lauf = async (aktion) => {
    setFehler(null);
    try {
      setStand(await aktion());
    } catch (e) {
      setFehler(String(e.message || e));
    }
  };
  const setzen = () => {
    const name = (nameRef.current?.value || "").trim().toUpperCase();
    const wert = wertRef.current?.value || "";
    if (!name || !wert) return;
    lauf(async () => {
      const neu = await setzeGeheimnis(agent, name, wert);
      nameRef.current.value = "";
      wertRef.current.value = "";
      return neu;
    });
  };
  const loeschen = async (name) => {
    if (await bestaetigen({ title: t("Geheimnis löschen"), text: t("{0} für {1} löschen?", name, agent), ok: t("Löschen"), danger: true }))
      lauf(() => loescheGeheimnis(agent, name));
  };

  const feld =
    "min-w-0 flex-1 rounded border border-slate-300 px-2 py-1 font-mono text-xs dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100";
  return (
    <div className="border-t border-slate-200 pt-3 dark:border-slate-700">
      <div className="mb-1 font-semibold">{t("Geheimnisse")}</div>
      <p className="mb-2 text-xs text-slate-400">
        {t("Tokens und Passwörter für diesen Agenten. Automatik-Läufe bekommen sie als Umgebungsvariablen — im Befehl $NAME schreiben statt des Werts. Werte sind hier nie wieder lesbar; in Antworten und Logs werden sie durch ***NAME*** ersetzt.")}
      </p>
      {stand && !stand.verfuegbar && (
        <p className="text-xs text-red-600">{t("Nicht verfügbar: Verschlüsselungs-Paket fehlt im Image.")}</p>
      )}
      {stand?.namen?.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1">
          {stand.namen.map((n) => (
            <span key={n} className="inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs dark:bg-slate-800">
              ${n}
              <button onClick={() => loeschen(n)} title={t("Löschen")} className="text-slate-400 hover:text-red-600">
                ✕
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <input ref={nameRef} placeholder="ERP_DEV_TOKEN" autoCapitalize="characters" autoComplete="off" className={feld} />
        <input ref={wertRef} type="password" placeholder={t("Wert (wird ersetzt, falls der Name existiert)")} autoComplete="new-password" className={feld} />
        <button onClick={setzen} className="rounded bg-slate-700 px-3 py-1 text-xs font-medium text-white hover:bg-slate-800">
          {t("Setzen")}
        </button>
      </div>
      <label className="mt-2 flex items-start gap-2 text-xs">
        <input
          type="checkbox"
          checked={!!stand?.env_datei}
          onChange={(e) => lauf(() => setzeGeheimnisEnvDatei(agent, e.target.checked))}
          className="mt-0.5"
        />
        <span>
          {t("Auch für interaktive Sitzungen: als ~/.agent-dashboard/env (nur für den Benutzer lesbar) auf der Maschine ablegen. In der Shell-Startdatei einbinden mit: . ~/.agent-dashboard/env")}
        </span>
      </label>
      {fehler && <p className="mt-1 text-xs text-red-600">{fehler}</p>}
    </div>
  );
}
