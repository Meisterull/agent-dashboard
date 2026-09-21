import { useRef, useState } from "react";
import Modal from "./Modal";
import { setAustausch, deleteAustausch, sendeDateien } from "../api";
import { t } from "../sprache";

// Austausch-Ordner je Maschine (backend/app/austausch.py): zwei kleine Dialoge
// fürs Datei-Panel. Der erste schaltet den Ordner einer Maschine ein/aus, der
// zweite legt eine Datei in den Ordner einer ANDEREN Maschine. Kopiert wird
// serverseitig per SFTP — durch den Browser läuft kein Byte der Datei.
//
// Eingabefelder sind uncontrolled (defaultValue/ref): GBoard verdoppelt in
// kontrollierten Inputs die Wortvorschläge.

const knopf =
  "rounded border border-slate-300 px-3 py-1 text-slate-600 hover:bg-slate-50 disabled:opacity-40 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800";
const knopfHaupt =
  "rounded bg-blue-600 px-3 py-1 font-medium text-white hover:bg-blue-700 disabled:opacity-40";
const feld =
  "w-full rounded border border-slate-300 px-2 py-1.5 text-xs dark:border-slate-600 dark:bg-slate-800";

function groesse(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// `/C:/Users/x` → `C:/Users/x` — so steht es auch in der Nachricht an den Agenten.
const anzeige = (pfad) => (/^\/[A-Za-z]:\//.test(pfad || "") ? pfad.slice(1) : pfad);

export function AustauschDialog({ maschine, eintrag, standardOrdner, onClose, onGeaendert, onOeffnen }) {
  const ordnerRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [fehler, setFehler] = useState(null);
  const aktiv = !!eintrag?.aktiv;

  async function lauf(fn) {
    setBusy(true);
    setFehler(null);
    try {
      await fn();
      onGeaendert();
      onClose();
    } catch (err) {
      setFehler(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  const einschalten = (e) => {
    e.preventDefault();
    lauf(() => setAustausch(maschine, (ordnerRef.current?.value || "").trim()));
  };

  return (
    <Modal title={t("Austausch-Ordner · {0}", maschine)} onClose={onClose}>
      {eintrag && !eintrag.moeglich ? (
        <div className="space-y-3 text-sm">
          <p className="text-slate-600 dark:text-slate-300">
            {t(
              "Diese Maschine hat keine SSH-Verbindung — der Dateiaustausch geht nur zwischen SSH-Maschinen.",
            )}
          </p>
          <div className="flex justify-end">
            <button type="button" onClick={onClose} className={knopf}>
              {t("Schließen")}
            </button>
          </div>
        </div>
      ) : (
        <form onSubmit={einschalten} className="space-y-3 text-sm">
          <p className="text-slate-600 dark:text-slate-300">
            {t(
              "Andere Maschinen können Dateien in diesen Ordner legen — je Absender in einen Unterordner „von-<Absender>“. Vorhandenes wird nie überschrieben.",
            )}
          </p>
          {aktiv && (
            <p className="rounded bg-emerald-50 px-2 py-1.5 text-xs text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
              {t("Eingeschaltet:")}{" "}
              <span className="break-all font-mono">{anzeige(eintrag.pfad) || eintrag.ordner}</span>
            </p>
          )}
          <label className="block">
            <span className="mb-1 block text-slate-600 dark:text-slate-300">
              {t("Ordner (relativ zum Home oder absolut)")}
            </span>
            <input
              ref={ordnerRef}
              defaultValue={eintrag?.ordner || standardOrdner || "austausch"}
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              className={`${feld} font-mono`}
            />
          </label>
          {fehler && <p className="text-xs text-red-600 dark:text-red-400">{fehler}</p>}
          <div className="flex flex-wrap items-center justify-end gap-2">
            {aktiv && (
              <button
                type="button"
                disabled={busy}
                onClick={() => lauf(() => deleteAustausch(maschine))}
                title={t("Ausschalten löscht nichts auf der Maschine.")}
                className="mr-auto rounded border border-red-300 px-3 py-1 text-red-600 hover:bg-red-50 disabled:opacity-40 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950"
              >
                {t("Ausschalten")}
              </button>
            )}
            {aktiv && eintrag.pfad && (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  onOeffnen(eintrag.pfad);
                  onClose();
                }}
                className={knopf}
              >
                {t("Ordner öffnen")}
              </button>
            )}
            <button type="submit" disabled={busy} className={knopfHaupt}>
              {busy ? "…" : aktiv ? t("Pfad ändern") : t("Einschalten")}
            </button>
          </div>
          {aktiv && (
            <p className="text-[11px] text-slate-400 dark:text-slate-500">
              {t("Ausschalten löscht nichts auf der Maschine.")}
            </p>
          )}
        </form>
      )}
    </Modal>
  );
}

export function SendenDialog({ von, datei, ziele, maxMb, onClose }) {
  const notizRef = useRef(null);
  const [an, setAn] = useState(ziele[0] || "");
  const [busy, setBusy] = useState(false);
  const [fehler, setFehler] = useState(null);
  const [fertig, setFertig] = useState(null);
  const zuGross = maxMb && datei.size != null && datei.size > maxMb * 1024 * 1024;

  async function senden(e) {
    e.preventDefault();
    if (!an || busy) return;
    setBusy(true);
    setFehler(null);
    try {
      const r = await sendeDateien(von, an, [datei.path], (notizRef.current?.value || "").trim());
      setFertig(r.zugestellt[0]);
    } catch (err) {
      setFehler(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  let inhalt;
  if (fertig) {
    inhalt = (
      <div className="space-y-3 text-sm">
        <p className="text-emerald-700 dark:text-emerald-400">
          ✓ {t("Zugestellt an {0}:", an)}
        </p>
        <p className="break-all font-mono text-xs text-slate-600 dark:text-slate-300">
          {anzeige(fertig.pfad)} ({groesse(fertig.bytes)})
        </p>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {t("Die Maschine hat eine Nachricht mit dem Pfad bekommen.")}
        </p>
        <div className="flex justify-end">
          <button type="button" onClick={onClose} className={knopfHaupt}>
            {t("Fertig")}
          </button>
        </div>
      </div>
    );
  } else if (!ziele.length) {
    inhalt = (
      <div className="space-y-3 text-sm">
        <p className="text-slate-600 dark:text-slate-300">
          {t(
            "Noch keine andere Maschine hat einen Austausch-Ordner. Einschalten: auf dem Tab der Zielmaschine 📥 antippen.",
          )}
        </p>
        <div className="flex justify-end">
          <button type="button" onClick={onClose} className={knopf}>
            {t("Schließen")}
          </button>
        </div>
      </div>
    );
  } else {
    inhalt = (
      <form onSubmit={senden} className="space-y-3 text-sm">
        <p className="text-slate-600 dark:text-slate-300">
          {t("„{0}“ von {1} in den Austausch-Ordner einer anderen Maschine legen.", datei.name, von)}
        </p>
        <label className="block">
          <span className="mb-1 block text-slate-600 dark:text-slate-300">{t("Empfänger")}</span>
          <select value={an} onChange={(e) => setAn(e.target.value)} disabled={busy} className={feld}>
            {ziele.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-slate-600 dark:text-slate-300">
            {t("Begleittext (optional)")}
          </span>
          <input ref={notizRef} defaultValue="" disabled={busy} className={feld} />
        </label>
        {zuGross ? (
          <p className="text-xs text-red-600 dark:text-red-400">
            {t("Die Datei ist zu groß ({0}) — erlaubt sind {1} MB.", groesse(datei.size), maxMb)}
          </p>
        ) : (
          <p className="text-[11px] text-slate-400 dark:text-slate-500">
            {t(
              "Kopiert wird direkt von Maschine zu Maschine. Der Empfänger bekommt eine Nachricht mit dem Pfad.",
            )}
          </p>
        )}
        {fehler && <p className="text-xs text-red-600 dark:text-red-400">{fehler}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={knopf}>
            {t("Abbrechen")}
          </button>
          <button type="submit" disabled={busy || zuGross} className={knopfHaupt}>
            {busy ? t("überträgt…") : t("Senden")}
          </button>
        </div>
      </form>
    );
  }

  return (
    <Modal title={t("An Maschine senden")} onClose={onClose}>
      {inhalt}
    </Modal>
  );
}
