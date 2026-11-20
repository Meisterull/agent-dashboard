import { useEffect, useState } from "react";
import Modal from "./Modal";
import { getVorgaenge, getVorgang, setVorgangNotiz } from "../api";
import { melden, nachfragen } from "./Dialog";
import { t } from "../sprache";

// Vorgangs-Ansicht (Issue #58): ein Auftrag über alle Agenten — Liste der
// Vorgänge und je Vorgang eine Zeitleiste. Gelesen wird nur beim Öffnen
// bzw. auf Knopfdruck (der Server geht dafür durch alle Mailboxen).
const STATUS_FARBE = {
  "läuft": "bg-sky-100 text-sky-800 dark:bg-sky-900/50 dark:text-sky-200",
  "wartet auf Antwort": "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200",
  "wartet auf Freigabe": "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200",
  Fehler: "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-200",
  fertig: "bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-200",
};
const ART_ZEICHEN = { task: "📨", uebernommen: "▶", rueckfrage: "❓", ergebnis: "✅", ereignis: "•" };
const zeit = (iso) => (iso ? String(iso).slice(5, 16).replace("T", " ") : "");
const dauer = (s) => (!s ? "" : s >= 5400 ? `${(s / 3600).toFixed(1)} h` : `${Math.max(1, Math.round(s / 60))} min`);

function Abzeichen({ status }) {
  return (
    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${STATUS_FARBE[status] || ""}`}>
      {t(status)}
    </span>
  );
}

export default function VorgaengeDialog({ onClose, start = null }) {
  const [liste, setListe] = useState(null);
  const [tage, setTage] = useState(14);
  const [nurBenannte, setNurBenannte] = useState(false);
  const [offen, setOffen] = useState(start); // Vorgangs-Schlüssel
  const [detail, setDetail] = useState(null);
  const [auf, setAuf] = useState(() => new Set()); // aufgeklappte Einträge
  const [fehler, setFehler] = useState(null);

  const ladeListe = () => {
    setFehler(null);
    getVorgaenge(tage)
      .then((d) => setListe(d.vorgaenge))
      .catch((e) => setFehler(String(e.message || e)));
  };
  useEffect(ladeListe, [tage]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!offen) return;
    setDetail(null);
    setAuf(new Set());
    getVorgang(offen)
      .then(setDetail)
      .catch((e) => {
        setFehler(String(e.message || e));
        setOffen(null);
      });
  }, [offen]);

  const notiz = async () => {
    const text = await nachfragen({
      title: t("Notiz zum Vorgang"),
      label: t("z. B. „wartet auf Abend-Deploy“ — leer entfernt die Notiz"),
      initial: detail?.notiz || "",
      allowEmpty: true,
    });
    if (text === null) return;
    setVorgangNotiz(offen, text)
      .then(() => {
        setDetail((d) => ({ ...d, notiz: text }));
        ladeListe();
      })
      .catch((e) => melden({ title: t("Fehler"), text: String(e.message || e) }));
  };

  const sichtbar = (liste || []).filter((v) => !nurBenannte || v.benannt);
  return (
    <Modal title={offen ? t("Vorgang {0}", offen) : t("Vorgänge")} onClose={onClose} wide>
      {fehler && <p className="mb-2 text-xs text-red-600">{fehler}</p>}
      {!offen ? (
        <div className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
            <span>{t("Zeitraum")}</span>
            {[7, 14, 30, 90].map((n) => (
              <button
                key={n}
                onClick={() => setTage(n)}
                className={`rounded px-1.5 py-0.5 ${tage === n ? "bg-sky-600 text-white" : "bg-slate-200 dark:bg-slate-700"}`}
              >
                {t("{0} Tage", n)}
              </button>
            ))}
            <label className="ml-auto flex items-center gap-1">
              <input type="checkbox" checked={nurBenannte} onChange={(e) => setNurBenannte(e.target.checked)} />
              {t("nur benannte Vorgänge")}
            </label>
            <button onClick={ladeListe} title={t("jetzt aktualisieren")} className="rounded px-1.5 py-0.5 hover:bg-slate-200 dark:hover:bg-slate-700">
              ↻
            </button>
          </div>
          {liste === null && <p className="text-slate-400">{t("lädt …")}</p>}
          {liste && sichtbar.length === 0 && <p className="text-slate-400">{t("keine Vorgänge im Zeitraum")}</p>}
          {sichtbar.map((v) => (
            <button
              key={v.vorgang}
              onClick={() => setOffen(v.vorgang)}
              className="block w-full rounded bg-slate-50 p-2 text-left hover:bg-slate-100 dark:bg-slate-800 dark:hover:bg-slate-700"
            >
              <div className="flex items-center gap-2">
                <span className={`min-w-0 flex-1 truncate ${v.benannt ? "font-semibold" : "font-mono text-xs text-slate-500"}`}>
                  {v.benannt ? v.vorgang : v.titel || v.vorgang}
                </span>
                <Abzeichen status={v.status} />
              </div>
              {v.benannt && v.titel && <div className="truncate text-xs text-slate-500 dark:text-slate-400">{v.titel}</div>}
              <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11px] text-slate-400">
                <span>{zeit(v.letzte_aktivitaet)}</span>
                <span>{v.agenten.join(" · ")}</span>
                <span>{t(v.tasks === 1 ? "{0} Task" : "{0} Tasks", v.tasks)}</span>
                {v.kosten > 0 && <span>{v.kosten.toFixed(2)} $</span>}
                {v.dauer > 0 && <span>{dauer(v.dauer)}</span>}
                {v.notiz && <span className="text-amber-600 dark:text-amber-400">📝 {v.notiz}</span>}
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={() => setOffen(null)} className="rounded border border-slate-300 px-2 py-0.5 text-xs dark:border-slate-600">
              ← {t("Vorgänge")}
            </button>
            {detail && <Abzeichen status={detail.status} />}
            {detail?.kosten > 0 && <span className="text-xs text-slate-500">{detail.kosten.toFixed(2)} $</span>}
            {detail?.dauer > 0 && <span className="text-xs text-slate-500">{dauer(detail.dauer)}</span>}
            <span className="text-xs text-slate-400">{(detail?.agenten || []).join(" · ")}</span>
            <button onClick={notiz} className="ml-auto rounded border border-slate-300 px-2 py-0.5 text-xs dark:border-slate-600">
              📝 {detail?.notiz ? t("Notiz ändern") : t("Notiz")}
            </button>
          </div>
          {detail?.notiz && <p className="rounded bg-amber-50 p-1.5 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">📝 {detail.notiz}</p>}
          {!detail && <p className="text-slate-400">{t("lädt …")}</p>}
          {(detail?.eintraege || []).map((e, i) => {
            const istAuf = auf.has(i);
            return (
              <div
                key={i}
                onClick={() =>
                  setAuf((s) => {
                    const n = new Set(s);
                    if (n.has(i)) n.delete(i);
                    else n.add(i);
                    return n;
                  })
                }
                className={`cursor-pointer rounded border-l-2 bg-slate-50 p-1.5 text-xs dark:bg-slate-800 ${
                  e.art === "ergebnis"
                    ? e.status === "fehler" ? "border-red-500" : "border-green-500"
                    : e.schwere === "fehler" ? "border-red-500"
                    : e.schwere === "warnung" || e.offen ? "border-amber-500" : "border-slate-300 dark:border-slate-600"
                }`}
              >
                <div className="flex flex-wrap items-baseline gap-x-2 text-slate-500 dark:text-slate-400">
                  <span className="font-mono">{zeit(e.zeit)}</span>
                  <span>{ART_ZEICHEN[e.art] || "•"}</span>
                  {e.art === "task" && <span className="font-medium text-slate-700 dark:text-slate-200">{e.von} → {e.an}{e.rolle ? ` (${e.rolle})` : ""}</span>}
                  {e.art === "ergebnis" && (
                    <span className="font-medium text-slate-700 dark:text-slate-200">
                      {e.an}: {e.aufgegeben ? t("aufgegeben") : e.status === "fehler" ? t("Fehler") : t("fertig")}
                      {e.eingeschraenkt ? ` · ⚠ ${t("mit Einschränkungen")}` : ""}
                    </span>
                  )}
                  {e.art === "rueckfrage" && <span className="font-medium text-slate-700 dark:text-slate-200">{e.an} {t("fragt")}{e.offen ? ` — ${t("offen")}` : ""}</span>}
                  {e.art === "ereignis" && <span>{e.an}</span>}
                  {e.kosten != null && <span>{e.kosten.toFixed(2)} $</span>}
                  {e.dauer ? <span>{dauer(e.dauer)}</span> : null}
                  {e.modell && <span className="font-mono">{e.modell}</span>}
                  <span className="ml-auto truncate font-mono text-[10px]">{e.task_id}</span>
                </div>
                <div className={`${istAuf ? "whitespace-pre-wrap break-words" : "truncate"} text-slate-700 dark:text-slate-200`}>
                  {e.text}
                </div>
                {e.antwort && (istAuf || true) && (
                  <div className={`${istAuf ? "whitespace-pre-wrap break-words" : "truncate"} text-slate-500 dark:text-slate-400`}>
                    ↳ {e.antwort}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Modal>
  );
}
