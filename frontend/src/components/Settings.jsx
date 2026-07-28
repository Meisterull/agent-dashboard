import { useEffect, useState } from "react";
import Modal from "./Modal";
import { getSettings, putSettings } from "../api";

export default function Settings({ onClose }) {
  const [settings, setSettings] = useState(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getSettings()
      .then(setSettings)
      .catch(() => setSettings({ llm_provider: "claude-api", language: "de", telegram_enabled: false }));
  }, []);

  function update(key, value) {
    setSettings((s) => ({ ...s, [key]: value }));
    setSaved(false);
  }

  async function save() {
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
    } catch {
      setSaved(false);
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
          <label className="block">
            <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">LLM-Provider</span>
            <select
              value={settings.llm_provider}
              onChange={(e) => update("llm_provider", e.target.value)}
              className="w-full rounded border border-slate-300 px-2 py-1.5 dark:border-slate-600 dark:bg-slate-800"
            >
              <option value="claude-api">Claude (Anthropic API)</option>
              <option value="openrouter">OpenRouter</option>
              <option value="ollama-local">Ollama (lokal)</option>
            </select>
          </label>

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

          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={!!settings.telegram_enabled}
              onChange={(e) => update("telegram_enabled", e.target.checked)}
            />
            <span className="text-slate-600 dark:text-slate-300">Telegram-Bot aktiviert</span>
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
