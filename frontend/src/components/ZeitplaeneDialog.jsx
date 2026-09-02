import { useEffect, useState } from "react";
import Modal from "./Modal";
import { bestaetigen, melden } from "./Dialog";
import { getRollen, getZeitplaene, runZeitplanJetzt, saveZeitplaene } from "../api";
import { t } from "../sprache";

// Zeitpläne (Dashboard-Paket St.2): wiederkehrende Tasks zur Uhrzeit. Der
// Planer im Backend postet fällige Pläne als ganz normale Tasks — Automatik,
// Rückfragen und Push greifen von selbst. Verpasste Termine verfallen
// (je Plan: „nachholen" = höchstens EIN Nachzügler). Ablage:
// workspace/config/zeitplaene.yaml; PUT ersetzt die ganze Liste.

const TAGE = ["mo", "di", "mi", "do", "fr", "sa", "so"];
const TAG_LABEL = { mo: "Mo", di: "Di", mi: "Mi", do: "Do", fr: "Fr", sa: "Sa", so: "So" };
const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

const zeitpunkt = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString([], { dateStyle: "short", timeStyle: "short" });
};

export default function ZeitplaeneDialog({ agents = [], onClose }) {
  const [plaene, setPlaene] = useState(null); // null = lädt
  const [dateiFehler, setDateiFehler] = useState(null);
  const [rollen, setRollen] = useState([]);
  const [entwurf, setEntwurf] = useState(null); // Plan im Formular (Kopie)
  const [entwurfV, setEntwurfV] = useState(0); // remountet Formular-Felder
  const [neuName, setNeuName] = useState("");
  const [speichert, setSpeichert] = useState(false);

  // Review P1-4: handgepflegte YAML kann `tage: null` (oder Felder gar nicht)
  // enthalten — GET liefert die rohen Dicts. Ohne Normalisierung crashte
  // `entwurf.tage.includes(...)` das ganze Dashboard (weiße Seite).
  const normalisiert = (p) => ({
    ...p,
    tage: Array.isArray(p.tage) ? p.tage : [],
    zeit: p.zeit || "07:00",
    an: p.an !== false,
    nachholen: !!p.nachholen,
  });

  const laden = async () => {
    try {
      const d = await getZeitplaene();
      setPlaene(d.plaene);
      setDateiFehler(d.fehler || null);
    } catch (e) {
      setPlaene([]);
      melden({ title: t("Fehler"), text: t("Laden fehlgeschlagen: {0}", e.message) });
    }
  };
  useEffect(() => {
    laden();
    getRollen()
      .then((d) => setRollen(d.rollen.filter((r) => !r.fehler)))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const speichereListe = async (liste) => {
    setSpeichert(true);
    try {
      const d = await saveZeitplaene(liste);
      setPlaene(d.plaene);
      setDateiFehler(null);
      return true;
    } catch (e) {
      melden({ title: t("Fehler"), text: t("Speichern fehlgeschlagen: {0}", e.message) });
      return false;
    } finally {
      setSpeichert(false);
    }
  };

  const anlegen = () => {
    const n = neuName.trim().toLowerCase();
    if (!NAME_RE.test(n)) {
      melden({ title: t("Zeitpläne"), text: t("Ungültiger Name — erlaubt: kleinbuchstaben, ziffern, - und _") });
      return;
    }
    if ((plaene || []).some((p) => p.name === n)) {
      melden({ title: t("Zeitpläne"), text: t("„{0}“ gibt es schon.", n) });
      return;
    }
    setNeuName("");
    setEntwurf({
      name: n,
      agent: agents[0] || "",
      instruction: "",
      zeit: "07:00",
      tage: [],
      an: true,
      nachholen: false,
      _neu: true,
    });
    setEntwurfV((v) => v + 1);
  };

  const entwurfSpeichern = async () => {
    if (!entwurf) return;
    const { _neu, ...plan } = entwurf;
    const liste = _neu
      ? [...(plaene || []), plan]
      : (plaene || []).map((p) => (p.name === plan.name ? { ...p, ...plan } : p));
    if (await speichereListe(liste)) setEntwurf(null);
  };

  const schalte = async (name, an) =>
    speichereListe((plaene || []).map((p) => (p.name === name ? { ...p, an } : p)));

  const loeschen = async (name) => {
    if (
      !(await bestaetigen({
        title: t("Plan löschen"),
        text: t("Plan „{0}“ endgültig löschen?", name),
        ok: t("Löschen"),
        danger: true,
      }))
    )
      return;
    if (entwurf?.name === name) setEntwurf(null);
    await speichereListe((plaene || []).filter((p) => p.name !== name));
  };

  const sofort = async (name) => {
    try {
      const d = await runZeitplanJetzt(name);
      melden({ title: t("Zeitpläne"), text: t("Sofort ausgeführt — Task {0} an {1}.", d.task_id, d.agent) });
      laden();
    } catch (e) {
      melden({ title: t("Fehler"), text: t("Ausführen fehlgeschlagen: {0}", e.message) });
    }
  };

  const feld = (patch) => setEntwurf((e) => ({ ...e, ...patch }));
  const tagToggle = (tag) =>
    setEntwurf((e) => {
      const tage = Array.isArray(e.tage) ? e.tage : []; // P1-4: nie crashen
      return {
        ...e,
        tage: tage.includes(tag) ? tage.filter((x) => x !== tag) : [...tage, tag],
      };
    });
  const agentOptionen = [...new Set([...(agents || []), entwurf?.agent].filter(Boolean))];

  return (
    <Modal title={t("Zeitpläne")} onClose={onClose}>
      <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">
        {t(
          "Fällige Pläne werden als normale Tasks gepostet (Absender: du) — Ergebnis kommt wie gewohnt als Nachricht/Push. Verpasste Termine verfallen; „nachholen“ holt höchstens einen nach.",
        )}
      </p>
      {dateiFehler && (
        <p className="mb-2 rounded bg-red-100 px-2 py-1.5 text-xs text-red-700 dark:bg-red-950 dark:text-red-300">
          {t("Datei fehlerhaft: {0}", dateiFehler)}
        </p>
      )}
      <div className="flex gap-2">
        <input
          key={`neu:${entwurfV}`}
          defaultValue=""
          maxLength={64}
          onChange={(e) => setNeuName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && anlegen()}
          placeholder={t("neuer-plan (kleinbuchstaben, - und _)")}
          className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1.5 font-mono text-sm dark:border-slate-600 dark:bg-slate-800"
        />
        <button
          onClick={anlegen}
          disabled={!neuName.trim()}
          className="shrink-0 rounded bg-slate-800 px-3 py-1.5 text-sm text-white disabled:opacity-40 dark:bg-slate-700"
        >
          {t("Anlegen")}
        </button>
      </div>

      {plaene === null ? (
        <p className="mt-4 text-sm text-slate-400">{t("lädt…")}</p>
      ) : plaene.length === 0 && !entwurf ? (
        <p className="mt-4 text-sm text-slate-500 dark:text-slate-400">
          {t("Noch kein Zeitplan — oben einen Namen vergeben.")}
        </p>
      ) : (
        <ul className="mt-3 flex flex-col gap-1.5">
          {plaene.map((p) => (
            <li
              key={p.name}
              className={`flex items-center gap-2 rounded border px-2.5 py-1.5 ${
                entwurf?.name === p.name && !entwurf?._neu
                  ? "border-blue-400 dark:border-blue-600"
                  : "border-slate-200 dark:border-slate-700"
              } ${p.an ? "" : "opacity-60"}`}
            >
              <button
                onClick={() => {
                  // Remount erzwingen (Review P2): erneutes Antippen des schon
                  // offenen Plans setzte nur den State zurück — die
                  // defaultValue-Felder zeigten weiter die Bearbeitung, und
                  // „Speichern" schrieb still den alten Stand.
                  setEntwurf(normalisiert(p));
                  setEntwurfV((v) => v + 1);
                }}
                className="min-w-0 flex-1 text-left"
              >
                <div className="truncate text-sm">
                  <span className="font-mono">{p.name}</span>
                  <span className="ml-2 text-xs text-slate-500 dark:text-slate-400">
                    {p.zeit} · {(p.tage || []).length ? p.tage.map((x) => t(TAG_LABEL[x] || x)).join(" ") : t("täglich")} → {p.agent}
                    {p.rolle ? ` · ${p.rolle}` : ""}
                  </span>
                </div>
                <div className="truncate text-xs text-slate-500 dark:text-slate-400">
                  {p.letzter_lauf ? t("zuletzt: {0}", zeitpunkt(p.letzter_lauf)) : t("noch nie gelaufen")}
                </div>
              </button>
              <button
                onClick={() => schalte(p.name, !p.an)}
                disabled={speichert}
                title={t("Plan aktiv/inaktiv schalten (speichert sofort)")}
                className={`shrink-0 rounded px-2 py-0.5 text-xs font-semibold ${
                  p.an
                    ? "bg-sky-500 text-white"
                    : "bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300"
                }`}
              >
                {p.an ? t("an") : t("aus")}
              </button>
              <button
                onClick={() => sofort(p.name)}
                title={t("sofort ausführen (Test)")}
                className="shrink-0 rounded px-1.5 py-1 text-xs text-slate-400 hover:text-sky-600"
              >
                ▶
              </button>
              <button
                onClick={() => loeschen(p.name)}
                title={t("Plan „{0}“ endgültig löschen?", p.name)}
                className="shrink-0 rounded px-1.5 py-1 text-xs text-slate-400 hover:text-red-600"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}

      {entwurf && (
        <div className="mt-3 rounded border border-slate-200 p-2.5 dark:border-slate-700" key={`${entwurf.name}:${entwurfV}`}>
          <div className="mb-2 flex items-center gap-2">
            <span className="font-mono text-sm">{entwurf.name}</span>
            {entwurf._neu && (
              <span className="text-xs text-amber-600 dark:text-amber-400">{t("noch nicht gespeichert")}</span>
            )}
            <span className="flex-1" />
            <button
              onClick={entwurfSpeichern}
              disabled={speichert}
              className="rounded bg-blue-600 px-3 py-1 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
            >
              {speichert ? "…" : t("Speichern")}
            </button>
          </div>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <label className="block">
              <span className="mb-0.5 block text-xs text-slate-500 dark:text-slate-400">{t("Agent")}</span>
              <select
                value={entwurf.agent}
                onChange={(e) => feld({ agent: e.target.value })}
                className="w-full rounded border border-slate-300 px-2 py-1.5 dark:border-slate-600 dark:bg-slate-800"
              >
                {agentOptionen.map((a) => (
                  <option key={a} value={a}>{a}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-0.5 block text-xs text-slate-500 dark:text-slate-400">{t("Rolle")}</span>
              <select
                value={entwurf.rolle || ""}
                onChange={(e) => feld({ rolle: e.target.value || undefined })}
                className="w-full rounded border border-slate-300 px-2 py-1.5 dark:border-slate-600 dark:bg-slate-800"
              >
                <option value="">{t("(keine Rolle)")}</option>
                {rollen.map((r) => (
                  <option key={r.name} value={r.name}>{r.name}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-0.5 block text-xs text-slate-500 dark:text-slate-400">{t("Uhrzeit")}</span>
              <input
                type="time"
                value={entwurf.zeit}
                onChange={(e) => feld({ zeit: e.target.value })}
                className="w-full rounded border border-slate-300 px-2 py-1.5 dark:border-slate-600 dark:bg-slate-800"
              />
            </label>
            <label className="block">
              <span className="mb-0.5 block text-xs text-slate-500 dark:text-slate-400">{t("Projekt (optional)")}</span>
              <input
                defaultValue={entwurf.project || ""}
                onChange={(e) => feld({ project: e.target.value.trim() || undefined })}
                placeholder={t("Unterverzeichnis im workdir")}
                className="w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-xs dark:border-slate-600 dark:bg-slate-800"
              />
            </label>
          </div>
          <div className="mt-2">
            <span className="mb-0.5 block text-xs text-slate-500 dark:text-slate-400">
              {t("Tage (keiner gewählt = täglich)")}
            </span>
            <div className="flex flex-wrap gap-1">
              {TAGE.map((tag) => (
                <button
                  key={tag}
                  onClick={() => tagToggle(tag)}
                  className={`rounded px-2 py-0.5 text-xs font-medium ${
                    entwurf.tage.includes(tag)
                      ? "bg-sky-500 text-white"
                      : "bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300"
                  }`}
                >
                  {t(TAG_LABEL[tag])}
                </button>
              ))}
            </div>
          </div>
          <label className="mt-2 flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
            <input
              type="checkbox"
              checked={!!entwurf.nachholen}
              onChange={(e) => feld({ nachholen: e.target.checked })}
            />
            {t("nachholen — ein verpasster Termin läuft nach, sobald alles wieder lebt")}
          </label>
          <label className="mt-2 block">
            <span className="mb-0.5 block text-xs text-slate-500 dark:text-slate-400">{t("Auftrag")}</span>
            <textarea
              defaultValue={entwurf.instruction}
              onChange={(e) => feld({ instruction: e.target.value })}
              rows={4}
              className="w-full rounded border border-slate-300 p-2 font-mono text-xs leading-snug dark:border-slate-600 dark:bg-slate-900"
            />
          </label>
        </div>
      )}
    </Modal>
  );
}
