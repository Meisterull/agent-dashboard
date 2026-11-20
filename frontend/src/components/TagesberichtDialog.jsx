import { useEffect, useState } from "react";
import Modal from "./Modal";
import { getTagesbericht } from "../api";
import { t } from "../sprache";

// Tagesbericht (Issue #63): feste Auswertung je Agent und Tag — erledigt,
// Kosten, Offenes, Liegendes, Auffälligkeiten. Die Seite gibt es immer;
// der Push dazu ist in den Einstellungen abschaltbar (Default aus).
const tagVor = (iso, n) => {
  const d = new Date(iso + "T12:00:00");
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
};
const dauer = (s) => (!s ? "0 min" : s >= 5400 ? `${(s / 3600).toFixed(1)} h` : `${Math.max(1, Math.round(s / 60))} min`);
const ZEICHEN = { done: "✅", error: "⛔", eingeschraenkt: "⚠" };

export default function TagesberichtDialog({ onClose, onVorgang }) {
  const [tag, setTag] = useState(null);
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState(null);
  const [alle, setAlle] = useState(false);

  useEffect(() => {
    setDaten(null);
    getTagesbericht(tag)
      .then((d) => {
        setDaten(d);
        if (!tag) setTag(d.tag);
      })
      .catch((e) => setFehler(String(e.message || e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tag]);

  const berichte = (daten?.berichte || []).filter((b) => alle || b.etwas_passiert);
  const summe = (daten?.berichte || []).reduce((s, b) => s + b.kosten, 0);
  const e = daten?.einstellung;
  return (
    <Modal title={t("Tagesbericht")} onClose={onClose} wide>
      <div className="space-y-2 text-sm">
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
          <button onClick={() => tag && setTag(tagVor(tag, -1))} className="rounded border border-slate-300 px-2 py-0.5 dark:border-slate-600">←</button>
          <span className="font-mono font-semibold text-slate-700 dark:text-slate-200">{tag || "…"}</span>
          <button onClick={() => tag && setTag(tagVor(tag, 1))} className="rounded border border-slate-300 px-2 py-0.5 dark:border-slate-600">→</button>
          {summe > 0 && <span>{t("gesamt {0} $", summe.toFixed(2))}</span>}
          <label className="ml-auto flex items-center gap-1">
            <input type="checkbox" checked={alle} onChange={(ev) => setAlle(ev.target.checked)} />
            {t("auch ruhige Agenten")}
          </label>
        </div>
        {e && (
          <p className="text-[11px] text-slate-400">
            {e.an && e.push
              ? t("Push täglich um {0} — abschaltbar unter Einstellungen.", e.zeit)
              : t("Kein Push eingeschaltet (Einstellungen → Tagesbericht) — diese Seite gibt es trotzdem.")}
          </p>
        )}
        {fehler && <p className="text-xs text-red-600">{fehler}</p>}
        {!daten && !fehler && <p className="text-slate-400">{t("lädt …")}</p>}
        {daten && berichte.length === 0 && <p className="text-slate-400">{t("An diesem Tag ist nichts passiert.")}</p>}
        {berichte.map((b) => (
          <div key={b.agent} className="rounded bg-slate-50 p-2 dark:bg-slate-800">
            <div className="flex flex-wrap items-baseline gap-x-3">
              <span className="font-semibold">{b.agent}</span>
              <span className="text-xs text-slate-500 dark:text-slate-400">
                {t("{0} erledigt", b.erledigt.length)}
                {b.zahl.error ? ` · ${t("{0} Fehler", b.zahl.error)}` : ""}
                {b.zahl.eingeschraenkt ? ` · ${t("{0} mit Einschränkungen", b.zahl.eingeschraenkt)}` : ""}
                {b.kosten ? ` · ${b.kosten.toFixed(2)} $` : ""}
                {b.laufzeit ? ` · ${dauer(b.laufzeit)}` : ""}
                {b.tokens ? ` · ${Math.round(b.tokens / 1000)}k Tok` : ""}
              </span>
            </div>
            {(b.rueckfragen.length > 0 || b.freigaben > 0 || b.liegend.length > 0 || b.still.length > 0 || b.fehlgeschlagen.length > 0 || b.pakete.length > 0) && (
              <div className="mt-1 space-y-0.5 text-xs text-amber-700 dark:text-amber-300">
                {b.rueckfragen.map((q) => (
                  <div key={q.id} className="truncate">❓ {t("Rückfrage {0}", q.richtung)}: {q.text}</div>
                ))}
                {b.freigaben > 0 && <div>🔐 {t("{0} Freigabe(n) warten", b.freigaben)}</div>}
                {b.liegend.length > 0 && <div>⏳ {t("{0} Task(s) liegen, längster seit {1} min", b.liegend.length, Math.max(...b.liegend.map((x) => x.minuten)))}</div>}
                {b.still.length > 0 && <div>⏳ {t("{0} beanspruchte(r) Task(s) ohne Lebenszeichen", b.still.length)}</div>}
                {b.fehlgeschlagen.map((f) => (
                  <div key={f.task_id} className="truncate">⛔ {t("gescheitert")}: {f.titel || f.task_id}</div>
                ))}
                {b.pakete.map((p) => (
                  <div key={p.token}>📦 {p.kurz} — {t(p.phase)}</div>
                ))}
              </div>
            )}
            {b.groesster_lauf && b.erledigt.length > 1 && (
              <div className="mt-1 truncate text-xs text-slate-500 dark:text-slate-400">
                💰 {t("größter Lauf {0} $", b.groesster_lauf.kosten.toFixed(2))}: {b.groesster_lauf.titel}
              </div>
            )}
            {Object.keys(b.auffaellig).length > 0 && (
              <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                {t("Auffälligkeiten")}: {Object.entries(b.auffaellig).map(([art, n]) => `${n}× ${art}`).join(" · ")}
                {b.beispiele.map((x, i) => (
                  <div key={i} className="truncate text-[11px] text-slate-400">· {x}</div>
                ))}
              </div>
            )}
            {b.erledigt.length > 0 && (
              <details className="mt-1 text-xs">
                <summary className="cursor-pointer text-slate-500 dark:text-slate-400">{t("erledigte Tasks")}</summary>
                {b.erledigt.map((x) => (
                  <div key={x.task_id} className="flex gap-2 truncate">
                    <span>{ZEICHEN[x.status]}</span>
                    <span className="min-w-0 flex-1 truncate">{x.titel || x.task_id}</span>
                    {x.vorgang && (
                      <button onClick={() => onVorgang?.(x.vorgang)} className="shrink-0 rounded bg-slate-200 px-1 text-[10px] dark:bg-slate-700">⛓ {x.vorgang}</button>
                    )}
                    {x.kosten != null && <span className="shrink-0 text-slate-400">{x.kosten.toFixed(2)} $</span>}
                  </div>
                ))}
              </details>
            )}
          </div>
        ))}
      </div>
    </Modal>
  );
}
