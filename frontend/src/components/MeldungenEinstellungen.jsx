import { t } from "../sprache";

// Einstellungen der Hinweis-Meldungen (Issues #59/#63) — Teil des
// Einstellungs-Dialogs; gespeichert wird mit dessen „Speichern".
const TAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const LIEGEZEIT_STANDARD = {
  an: true, schwelle_min: 20, still_min: 120, von: "07:00", bis: "18:00",
  tage: [0, 1, 2, 3, 4], ohne: [],
};

const zahl = "w-20 rounded border border-slate-300 px-2 py-1 dark:border-slate-600 dark:bg-slate-800";

export function TageWahl({ tage, onChange }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      {TAGE.map((name, i) => {
        const an = tage.includes(i);
        return (
          <button
            key={name}
            type="button"
            aria-pressed={an}
            onClick={() => onChange(an ? tage.filter((x) => x !== i) : [...tage, i].sort())}
            className={`rounded px-1.5 py-0.5 text-xs ${
              an ? "bg-sky-600 text-white" : "bg-slate-200 text-slate-500 dark:bg-slate-700 dark:text-slate-400"
            }`}
          >
            {t(name)}
          </button>
        );
      })}
    </span>
  );
}

export function Liegezeit({ settings, update }) {
  const l = { ...LIEGEZEIT_STANDARD, ...(settings.liegezeit || {}) };
  const setze = (feld, wert) => update("liegezeit", { ...l, [feld]: wert });
  return (
    <div>
      <label className="flex items-start gap-2">
        <input type="checkbox" checked={!!l.an} onChange={(e) => setze("an", e.target.checked)} className="mt-0.5" />
        <span>
          <span className="font-medium text-slate-600 dark:text-slate-300">
            {t("Hinweis, wenn Tasks liegen bleiben")}
          </span>
          <span className="block text-xs text-slate-400">
            {t("Automatik aus und ein Task wartet zu lange — oder er ist beansprucht, aber still: ein gebündelter Push je Agent, mit „Per Automatik abarbeiten“ und „24 h Ruhe“.")}
          </span>
        </span>
      </label>
      {l.an && (
        <div className="mt-2 space-y-2 pl-6 text-xs text-slate-600 dark:text-slate-300">
          <label className="flex flex-wrap items-center gap-2">
            {t("unberührt seit (min)")}
            <input type="number" min="1" value={l.schwelle_min} onChange={(e) => setze("schwelle_min", Math.max(1, Number(e.target.value) || 20))} className={zahl} />
            {t("beansprucht, aber still seit (min)")}
            <input type="number" min="5" value={l.still_min} onChange={(e) => setze("still_min", Math.max(5, Number(e.target.value) || 120))} className={zahl} />
          </label>
          <label className="flex flex-wrap items-center gap-2">
            {t("nur zwischen")}
            <input type="time" value={l.von} onChange={(e) => setze("von", e.target.value)} className={zahl + " w-28"} />
            {t("und")}
            <input type="time" value={l.bis} onChange={(e) => setze("bis", e.target.value)} className={zahl + " w-28"} />
            <TageWahl tage={l.tage || []} onChange={(tage) => setze("tage", tage)} />
          </label>
          <label className="block">
            {t("nie für diese Agenten (durch Komma getrennt)")}
            <input
              // uncontrolled (Repo-Regel für Text-Inputs), übernommen beim Verlassen
              defaultValue={(l.ohne || []).join(", ")}
              onBlur={(e) => setze("ohne", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))}
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 dark:border-slate-600 dark:bg-slate-800"
            />
          </label>
        </div>
      )}
    </div>
  );
}

const BERICHT_STANDARD = {
  an: false, push: true, zeit: "17:30", tage: [0, 1, 2, 3, 4],
  woechentlich: false, nur_wenn_etwas: true, ohne: [], pause_bis: null,
};

export function Tagesbericht({ settings, update }) {
  const b = { ...BERICHT_STANDARD, ...(settings.tagesbericht || {}) };
  const setze = (feld, wert) => update("tagesbericht", { ...b, [feld]: wert });
  const pausiert = b.pause_bis && new Date(b.pause_bis) > new Date();
  return (
    <div>
      <label className="flex items-start gap-2">
        <input type="checkbox" checked={!!b.an} onChange={(e) => setze("an", e.target.checked)} className="mt-0.5" />
        <span>
          <span className="font-medium text-slate-600 dark:text-slate-300">{t("Tagesbericht per Push")}</span>
          <span className="block text-xs text-slate-400">
            {t("Einmal am Tag je Agent: erledigt, Kosten, offene Rückfragen und Freigaben, liegende Tasks, anstehende Deploys. Die Seite (📋 im Agenten-Panel) gibt es auch ohne Push.")}
          </span>
        </span>
      </label>
      {b.an && (
        <div className="mt-2 space-y-2 pl-6 text-xs text-slate-600 dark:text-slate-300">
          <label className="flex flex-wrap items-center gap-2">
            {t("um")}
            <input type="time" value={b.zeit} onChange={(e) => setze("zeit", e.target.value)} className={zahl + " w-28"} />
            <TageWahl tage={b.tage || []} onChange={(tage) => setze("tage", tage)} />
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={!!b.woechentlich} onChange={(e) => setze("woechentlich", e.target.checked)} />
            {t("nur wöchentlich (am letzten gewählten Wochentag)")}
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={!!b.nur_wenn_etwas} onChange={(e) => setze("nur_wenn_etwas", e.target.checked)} />
            {t("nur senden, wenn etwas passiert ist")}
          </label>
          <label className="block">
            {t("nie für diese Agenten (durch Komma getrennt)")}
            <input
              defaultValue={(b.ohne || []).join(", ")}
              onBlur={(e) => setze("ohne", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))}
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 dark:border-slate-600 dark:bg-slate-800"
            />
          </label>
          <label className="flex flex-wrap items-center gap-2">
            {t("Pause bis (Urlaub)")}
            <input type="date" value={(b.pause_bis || "").slice(0, 10)} onChange={(e) => setze("pause_bis", e.target.value ? e.target.value + "T23:59" : null)} className={zahl + " w-36"} />
            {pausiert && <span className="text-amber-600 dark:text-amber-400">{t("pausiert")}</span>}
          </label>
        </div>
      )}
    </div>
  );
}

export default function MeldungenEinstellungen({ settings, update, children }) {
  return (
    <div className="space-y-3 text-sm">
      <Liegezeit settings={settings} update={update} />
      <Tagesbericht settings={settings} update={update} />
      {children}
    </div>
  );
}
