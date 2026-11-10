import { useEffect, useMemo, useRef, useState } from "react";
import {
  containerDownloadUrl,
  dateiGroesse,
  getContainer,
  getContainerDatei,
  getContainerDateien,
  getContainerLogs,
  getContainerSchalter,
} from "../api";
import { containerSid } from "../terminalFenster";
import { lsGet, lsSet } from "../speicher";
import { t } from "../sprache";

// Container-Fenster (Issue #49): Docker- und Proxmox-Container eines Rechners,
// NUR lesend — Liste, Shell (Tab im Terminal-Fenster), Logs, Dateien. Welche
// Rechner hier auftauchen, entscheiden die Haken im Verbindungen-Dialog
// (backend/app/container.py). Aktualisiert wird nur auf Knopfdruck: eine
// Dauerabfrage von `docker ps` je offenem Tab wäre teuer und selten nötig.

const LS_RECHNER = "container-rechner-v1";
const ZEILEN = [200, 500, 1000];
const MAX_ANSEHEN = 512 * 1024;
const ABZEICHEN = {
  docker: ["Docker", "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300"],
  lxc: ["LXC", "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300"],
  vm: ["VM", "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300"],
};
const knopf =
  "rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-600 dark:hover:bg-slate-800";

export const shellOeffnen = (verbindung, c) =>
  window.dispatchEvent(
    new CustomEvent("terminal:oeffnen", {
      detail: { name: verbindung, sid: containerSid(c.quelle, c.id) },
    }),
  );

function Fehler({ text }) {
  if (!text) return null;
  return (
    <p className="rounded bg-red-50 px-2 py-1.5 text-xs text-red-700 dark:bg-red-950 dark:text-red-300" data-fehler>
      {text}
    </p>
  );
}

function Karte({ c, onShell, onDateien, onLogs }) {
  const [art, farbe] = ABZEICHEN[c.quelle] || ABZEICHEN.docker;
  const innen = c.laeuft && !c.nur_ansicht; // Shell/Dateien brauchen einen laufenden Container
  const logsGehen = !c.nur_ansicht && (c.quelle === "docker" || c.laeuft);
  return (
    <li
      data-container={`${c.quelle}:${c.id}`}
      className="rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
    >
      <div className="flex items-center gap-2">
        <span
          title={c.status}
          className={`h-2 w-2 shrink-0 rounded-full ${c.laeuft ? "bg-green-500" : "bg-slate-300 dark:bg-slate-600"}`}
        />
        <span className="min-w-0 flex-1 truncate font-mono text-sm font-medium">{c.name}</span>
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ${farbe}`}>{art}</span>
      </div>
      <div className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400" title={c.image}>
        {c.image}
        {c.quelle !== "docker" && c.id !== c.name ? ` · ${c.id}` : ""}
      </div>
      <div className="truncate text-xs text-slate-400" title={c.ports || c.status}>
        {c.status}
        {c.ports ? ` · ${c.ports}` : ""}
      </div>
      {c.nur_ansicht ? (
        <p className="mt-1.5 text-[11px] text-slate-400">{t("nur Ansicht")}</p>
      ) : (
        <div className="mt-1.5 flex gap-1.5">
          <button
            className={knopf}
            disabled={!innen}
            title={innen ? t("Shell im Container (neuer Tab im Terminal)") : t("Container läuft nicht")}
            onClick={onShell}
            data-aktion="shell"
          >
            {t(">_ Shell")}
          </button>
          <button
            className={knopf}
            disabled={!innen}
            title={innen ? undefined : t("Container läuft nicht")}
            onClick={onDateien}
            data-aktion="dateien"
          >
            {t("Dateien")}
          </button>
          <button
            className={knopf}
            disabled={!logsGehen}
            title={logsGehen ? undefined : t("Container läuft nicht")}
            onClick={onLogs}
            data-aktion="logs"
          >
            {t("Logs")}
          </button>
        </div>
      )}
    </li>
  );
}

function Logs({ verbindung, c }) {
  const [zeilen, setZeilen] = useState(ZEILEN[0]);
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState(null);
  const [laedt, setLaedt] = useState(false);
  const [kopiert, setKopiert] = useState(false);
  const unten = useRef(null);
  const laden = () => {
    setLaedt(true);
    setFehler(null);
    getContainerLogs(verbindung, c.quelle, c.id, zeilen)
      .then(setDaten)
      .catch((e) => setFehler(String(e.message || e)))
      .finally(() => setLaedt(false));
  };
  useEffect(laden, [zeilen]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => unten.current?.scrollIntoView({ block: "end" }), [daten]);
  const kopieren = () =>
    navigator.clipboard?.writeText(daten?.text || "").then(() => {
      setKopiert(true);
      setTimeout(() => setKopiert(false), 1500);
    });
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <select
          value={zeilen}
          onChange={(e) => setZeilen(Number(e.target.value))}
          className="rounded border border-slate-300 bg-white px-1.5 py-1 text-xs dark:border-slate-600 dark:bg-slate-800"
          data-zeilen
        >
          {ZEILEN.map((z) => (
            <option key={z} value={z}>
              {t("letzte {0} Zeilen", z)}
            </option>
          ))}
        </select>
        <button className={knopf} onClick={laden} disabled={laedt}>
          {laedt ? t("lädt…") : t("↻ Aktualisieren")}
        </button>
        <button className={knopf} onClick={kopieren} disabled={!daten?.text}>
          {kopiert ? t("✓ kopiert") : t("Kopieren")}
        </button>
        {daten?.herkunft && <span className="text-[11px] text-slate-400">{daten.herkunft}</span>}
      </div>
      <Fehler text={fehler} />
      <pre
        data-logtext
        className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-950 p-2 font-mono text-[11px] leading-snug text-slate-100"
      >
        {daten ? daten.text || t("(keine Ausgabe)") : ""}
        <span ref={unten} />
      </pre>
    </div>
  );
}

function Dateien({ verbindung, c }) {
  const [pfad, setPfad] = useState("/");
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState(null);
  const [ansicht, setAnsicht] = useState(null); // {pfad, text, gekuerzt}
  useEffect(() => {
    setFehler(null);
    setDaten(null);
    getContainerDateien(verbindung, c.quelle, c.id, pfad)
      .then(setDaten)
      .catch((e) => setFehler(String(e.message || e)));
  }, [pfad, verbindung, c.quelle, c.id]);
  const kind = (name) => (pfad === "/" ? `/${name}` : `${pfad}/${name}`);
  const hoch = pfad === "/" ? null : pfad.slice(0, pfad.lastIndexOf("/")) || "/";
  const ansehen = (p) => {
    setFehler(null);
    getContainerDatei(verbindung, c.quelle, c.id, p)
      .then(setAnsicht)
      .catch((e) => setFehler(String(e.message || e)));
  };

  if (ansicht)
    return (
      <div className="flex min-h-0 flex-1 flex-col gap-2">
        <div className="flex items-center gap-1.5">
          <button className={knopf} onClick={() => setAnsicht(null)}>
            {t("← Ordner")}
          </button>
          <span className="min-w-0 flex-1 truncate font-mono text-xs">{ansicht.pfad}</span>
          <a className={knopf} href={containerDownloadUrl(verbindung, c.quelle, c.id, ansicht.pfad)}>
            ⬇
          </a>
        </div>
        {ansicht.gekuerzt && (
          <p className="text-[11px] text-amber-600">{t("Nur der Anfang (512 KB) — ganz: herunterladen.")}</p>
        )}
        <pre data-dateitext className="min-h-0 flex-1 overflow-auto whitespace-pre rounded bg-slate-50 p-2 font-mono text-[11px] dark:bg-slate-950">
          {ansicht.text}
        </pre>
      </div>
    );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-center gap-1.5">
        <button className={knopf} disabled={!hoch} onClick={() => setPfad(hoch)} title={t("Ordner hoch")}>
          ↑
        </button>
        <span className="min-w-0 flex-1 truncate font-mono text-xs" data-pfad>
          {pfad}
        </span>
      </div>
      <Fehler text={fehler} />
      {!daten && !fehler && <p className="text-xs text-slate-400">{t("lädt…")}</p>}
      {daten && (
        <ul className="min-h-0 flex-1 overflow-auto text-sm">
          {daten.eintraege.length === 0 && <li className="text-xs text-slate-400">{t("(leer)")}</li>}
          {daten.eintraege.map((e) => (
            <li
              key={e.name}
              data-eintrag={e.name}
              className="flex items-center gap-2 border-b border-slate-100 py-1 dark:border-slate-800"
            >
              <button
                className="flex min-w-0 flex-1 items-center gap-1.5 text-left hover:text-sky-600"
                title={e.ziel ? `→ ${e.ziel}` : undefined}
                onClick={() =>
                  e.typ === "dir" || (e.typ === "link" && !/\.[a-z0-9]{1,5}$/i.test(e.name))
                    ? setPfad(kind(e.name))
                    : e.groesse <= MAX_ANSEHEN
                      ? ansehen(kind(e.name))
                      : (window.location.href = containerDownloadUrl(verbindung, c.quelle, c.id, kind(e.name)))
                }
              >
                <span>{e.typ === "dir" ? "📁" : e.typ === "link" ? "🔗" : "📄"}</span>
                <span className="truncate font-mono text-xs">{e.name}</span>
              </button>
              {e.typ !== "dir" && (
                <>
                  <span className="shrink-0 text-[11px] text-slate-400">{dateiGroesse(e.groesse)}</span>
                  <a
                    href={containerDownloadUrl(verbindung, c.quelle, c.id, kind(e.name))}
                    title={t("Herunterladen")}
                    className="shrink-0 px-1 text-xs text-slate-400 hover:text-sky-600"
                  >
                    ⬇
                  </a>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function ContainerPanel() {
  const [schalter, setSchalter] = useState({});
  const rechnerListe = useMemo(
    () => Object.keys(schalter).filter((n) => schalter[n].docker || schalter[n].proxmox).sort(),
    [schalter],
  );
  const [rechner, setRechner] = useState(() => lsGet(LS_RECHNER) || "");
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState(null);
  const [laedt, setLaedt] = useState(false);
  const [filter, setFilter] = useState("");
  const [detail, setDetail] = useState(null); // {art: "logs"|"dateien", c}

  useEffect(() => {
    const load = () =>
      getContainerSchalter()
        .then((d) => setSchalter(d.schalter || {}))
        .catch(() => setSchalter({}));
    load();
    window.addEventListener("container:changed", load);
    window.addEventListener("connections:changed", load);
    return () => {
      window.removeEventListener("container:changed", load);
      window.removeEventListener("connections:changed", load);
    };
  }, []);

  const aktiv = rechnerListe.includes(rechner) ? rechner : rechnerListe[0] || "";
  useEffect(() => {
    if (aktiv) lsSet(LS_RECHNER, aktiv);
  }, [aktiv]);

  const laden = () => {
    if (!aktiv) return;
    setLaedt(true);
    setFehler(null);
    getContainer(aktiv)
      .then(setDaten)
      .catch((e) => {
        setDaten(null);
        setFehler(String(e.message || e));
      })
      .finally(() => setLaedt(false));
  };
  const schalterKey = aktiv ? JSON.stringify(schalter[aktiv]) : "";
  useEffect(() => {
    setDetail(null);
    laden();
  }, [aktiv, schalterKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const liste = (daten?.container || []).filter(
    (c) => !filter || `${c.name} ${c.image} ${c.id}`.toLowerCase().includes(filter.toLowerCase()),
  );

  if (!rechnerListe.length)
    return (
      <div className="p-4 text-sm text-slate-500 dark:text-slate-400">
        {t("Noch kein Rechner mit Container-Fenster. Im Terminal unter „Verbindungen“ bei einem Rechner Docker oder Proxmox anhaken.")}
      </div>
    );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 p-2" data-container-panel>
      <div className="flex flex-wrap items-center gap-1.5">
        {detail ? (
          <>
            <button className={knopf} onClick={() => setDetail(null)} data-zurueck>
              {t("← Liste")}
            </button>
            <span className="min-w-0 flex-1 truncate font-mono text-sm font-medium">
              {detail.c.name} · {detail.art === "logs" ? t("Logs") : t("Dateien")}
            </span>
            {detail.c.laeuft && !detail.c.nur_ansicht && (
              <button className={knopf} onClick={() => shellOeffnen(aktiv, detail.c)}>
                {t(">_ Shell")}
              </button>
            )}
          </>
        ) : (
          <>
            {rechnerListe.length > 1 ? (
              <select
                value={aktiv}
                onChange={(e) => setRechner(e.target.value)}
                className="rounded border border-slate-300 bg-white px-1.5 py-1 text-xs dark:border-slate-600 dark:bg-slate-800"
                data-rechner
              >
                {rechnerListe.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            ) : (
              <span className="font-mono text-xs font-medium">{aktiv}</span>
            )}
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={t("Filtern …")}
              className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1 text-xs dark:border-slate-600 dark:bg-slate-800"
            />
            <button className={knopf} onClick={laden} disabled={laedt} data-aktualisieren>
              {laedt ? t("lädt…") : "↻"}
            </button>
          </>
        )}
      </div>

      {detail?.art === "logs" && <Logs verbindung={aktiv} c={detail.c} />}
      {detail?.art === "dateien" && <Dateien verbindung={aktiv} c={detail.c} />}
      {!detail && (
        <>
          <Fehler text={fehler} />
          {Object.entries(daten?.fehler || {}).map(([quelle, text]) => (
            <p key={quelle} className="rounded bg-amber-50 px-2 py-1 text-[11px] text-amber-700 dark:bg-amber-950 dark:text-amber-300" data-quellfehler={quelle}>
              {quelle === "docker" ? "Docker" : quelle === "vm" ? t("VMs") : "Proxmox"}: {text}
            </p>
          ))}
          {daten && (
            <p className="text-[11px] text-slate-400">
              {t("{0} von {1} · {2} laufen", liste.length, daten.container.length, daten.container.filter((c) => c.laeuft).length)}
            </p>
          )}
          <ul className="grid min-h-0 flex-1 auto-rows-min grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-2 overflow-auto">
            {liste.map((c) => (
              <Karte
                key={`${c.quelle}:${c.id}`}
                c={c}
                onShell={() => shellOeffnen(aktiv, c)}
                onDateien={() => setDetail({ art: "dateien", c })}
                onLogs={() => setDetail({ art: "logs", c })}
              />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
