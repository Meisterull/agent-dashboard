import { useEffect, useState } from "react";
import Modal from "./Modal";
import { getSettings, putSettings, getModels } from "../api";

// Das Orchestrator-Modell (`orch_model`) wird bewusst HIER umgeschaltet und
// nicht im Chat-Kopf: der ist mobil schon voll (Titel + Verlauf-Auswahl +
// Neu/Löschen), und die Modellwahl gilt global für alle Sessions — sie gehört
// zu den Einstellungen, nicht zum einzelnen Verlauf. Der Provider bleibt
// env-bestimmt (app/llm.py liest ihn NICHT aus den Settings), deshalb steht er
// hier nur noch als Information.
export default function Settings({ onClose }) {
  const [settings, setSettings] = useState(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState(null);
  const [models, setModels] = useState(null); // {provider, current, models[]}

  useEffect(() => {
    getSettings()
      .then(setSettings)
      .catch((e) => {
        setError(`Einstellungen konnten nicht geladen werden: ${e.message || e}`);
        setSettings({ language: "de" });
      });
    getModels()
      .then(setModels)
      .catch(() => setModels(null));
  }, []);

  function update(key, value) {
    setSettings((s) => ({ ...s, [key]: value }));
    setSaved(false);
    setError(null);
  }

  async function save() {
    setError(null);
    try {
      // leere Fenster-Zeilen (frisch hinzugefügt, nie ausgefüllt) nicht speichern
      const cleaned = {
        ...settings,
        external_windows: (settings.external_windows || []).filter(
          (w) => w.name?.trim() && w.url?.trim(),
        ),
      };
      const result = await putSettings(cleaned);
      setSettings(result);
      setSaved(true);
      window.dispatchEvent(new Event("settings:changed"));
      getModels()
        .then(setModels)
        .catch(() => {});
    } catch (e) {
      setSaved(false);
      setError(`Speichern fehlgeschlagen: ${e.message || e}`);
    }
  }

  const extWindows = settings?.external_windows || [];
  const updateWindow = (i, patch) =>
    update(
      "external_windows",
      extWindows.map((w, j) => (j === i ? { ...w, ...patch } : w)),
    );

  return (
    <Modal title="Einstellungen" onClose={onClose}>
      {!settings ? (
        <p className="text-sm text-slate-400">lädt…</p>
      ) : (
        <div className="space-y-4 text-sm">
          <div>
            <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">
              Orchestrator-Modell
            </span>
            {models?.models?.length ? (
              <select
                value={settings.orch_model || ""}
                onChange={(e) => update("orch_model", e.target.value)}
                className="w-full rounded border border-slate-300 px-2 py-1.5 dark:border-slate-600 dark:bg-slate-800"
              >
                <option value="">Standard aus .env{models.current ? ` (${models.current})` : ""}</option>
                {/* eingestelltes Modell mit aufnehmen, auch wenn es der
                    Provider (nicht mehr) auflistet — sonst zeigt das Feld
                    stillschweigend "Standard" und speichert das auch so */}
                {[...new Set([...models.models, settings.orch_model].filter(Boolean))].map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={settings.orch_model || ""}
                onChange={(e) => update("orch_model", e.target.value)}
                placeholder={models?.current || "leer = Standard aus .env"}
                className="w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-xs dark:border-slate-600 dark:bg-slate-800"
              />
            )}
            <p className="mt-1 text-xs text-slate-400">
              Provider:{" "}
              <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">
                {models?.provider || "unbekannt"}
              </code>{" "}
              — kommt aus <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">.env</code>{" "}
              (<code className="rounded bg-slate-100 px-1 dark:bg-slate-800">ORCH_PROVIDER</code>)
              und ist hier bewusst nicht umschaltbar. Wirkt sofort auf neue
              Chat-Runden.
              {models && !models.models.length
                ? " Der Provider liefert keine Modell-Liste — Name von Hand eintragen."
                : ""}
            </p>
          </div>

          <label className="block">
            <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">Sprache</span>
            <select
              value={settings.language}
              onChange={(e) => update("language", e.target.value)}
              className="w-full rounded border border-slate-300 px-2 py-1.5 dark:border-slate-600 dark:bg-slate-800"
            >
              <option value="de">Deutsch</option>
              <option value="en">English</option>
            </select>
          </label>

          <div>
            <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">
              Externe Fenster
            </span>
            <p className="mb-2 text-xs text-slate-400">
              Zusätzliche Fenster im Workspace, z.&nbsp;B. noVNC. Adresse als
              <code className="mx-1 rounded bg-slate-100 px-1 dark:bg-slate-800">IP:Port/pfad</code>
              (LAN, läuft über das Dashboard — auch WebSocket) oder volle https://-URL.
            </p>
            <div className="space-y-2">
              {extWindows.map((w, i) => (
                <div key={i} className="flex gap-2">
                  <input
                    value={w.name || ""}
                    onChange={(e) => updateWindow(i, { name: e.target.value })}
                    placeholder="Name"
                    className="w-28 rounded border border-slate-300 px-2 py-1.5 dark:border-slate-600 dark:bg-slate-800"
                  />
                  <input
                    value={w.url || ""}
                    onChange={(e) => updateWindow(i, { url: e.target.value })}
                    placeholder="192.168.1.40:6080/vnc.html?autoconnect=1"
                    className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1.5 font-mono text-xs dark:border-slate-600 dark:bg-slate-800"
                  />
                  <button
                    onClick={() =>
                      update("external_windows", extWindows.filter((_, j) => j !== i))
                    }
                    title="Fenster entfernen"
                    className="shrink-0 rounded border border-slate-300 px-2 text-slate-500 hover:bg-red-50 hover:text-red-600 dark:border-slate-600 dark:hover:bg-red-950"
                  >
                    ✕
                  </button>
                </div>
              ))}
              <button
                onClick={() =>
                  update("external_windows", [...extWindows, { name: "", url: "" }])
                }
                className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                + Fenster hinzufügen
              </button>
            </div>
          </div>

          <p className="text-xs text-slate-400">
            API-Keys und Tokens werden hier nicht verwaltet — sie bleiben in
            <code className="mx-1 rounded bg-slate-100 px-1 dark:bg-slate-800">.env</code>/Docker-Secrets.
          </p>

          {error && (
            <p className="rounded bg-red-100 px-2 py-1.5 text-xs text-red-700 dark:bg-red-950 dark:text-red-300">
              {error}
            </p>
          )}

          <div className="flex items-center gap-3 pt-2">
            <button
              onClick={save}
              className="rounded bg-blue-600 px-4 py-1.5 font-medium text-white hover:bg-blue-700"
            >
              Speichern
            </button>
            {saved && <span className="text-xs text-green-600">gespeichert</span>}
          </div>
        </div>
      )}
    </Modal>
  );
}
