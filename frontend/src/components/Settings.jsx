import { useEffect, useState } from "react";
import Modal from "./Modal";
import {
  getSettings,
  putSettings,
  getModels,
  getPushKey,
  subscribePush,
  unsubscribePush,
  pushTest,
} from "../api";

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
  // Web-Push (F10): Zustand DIESES Geräts — die Subscription lebt im Browser
  // (pushManager), der Server kennt nur ihre Zustelladresse.
  const [pushInfo, setPushInfo] = useState({ status: "prueft" });
  const [pushBusy, setPushBusy] = useState(false);

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

  async function pruefePush() {
    if (
      !("serviceWorker" in navigator) ||
      !("PushManager" in window) ||
      !("Notification" in window)
    )
      return setPushInfo({ status: "unsupported" });
    try {
      const reg = await navigator.serviceWorker.getRegistration();
      if (!reg) return setPushInfo({ status: "unsupported" }); // SW fehlt (noch)
      const [info, sub] = await Promise.all([
        getPushKey(),
        reg.pushManager.getSubscription(),
      ]);
      if (!info.enabled) return setPushInfo({ status: "server-aus", info });
      setPushInfo({
        status:
          Notification.permission === "denied" ? "denied" : sub ? "an" : "aus",
        info,
      });
    } catch (e) {
      setPushInfo({ status: "fehler", detail: String(e.message || e) });
    }
  }
  useEffect(() => {
    pruefePush();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // base64url → Uint8Array (applicationServerKey erwartet rohe Bytes)
  function b64ZuBytes(s) {
    const b64 = (s + "=".repeat((4 - (s.length % 4)) % 4))
      .replace(/-/g, "+")
      .replace(/_/g, "/");
    return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  }

  async function schaltePush(an) {
    setPushBusy(true);
    try {
      const reg = await navigator.serviceWorker.getRegistration();
      if (an) {
        if ((await Notification.requestPermission()) !== "granted") {
          setPushInfo((p) => ({ ...p, status: "denied" }));
          return;
        }
        const { key } = await getPushKey();
        const sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: b64ZuBytes(key),
        });
        await subscribePush(sub.toJSON());
      } else {
        const sub = await reg.pushManager.getSubscription();
        if (sub) {
          await unsubscribePush(sub.endpoint).catch(() => {});
          await sub.unsubscribe();
        }
      }
      await pruefePush();
    } catch (e) {
      setPushInfo({ status: "fehler", detail: String(e.message || e) });
    } finally {
      setPushBusy(false);
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
                // defaultValue statt value: GBoard-Wortvorschläge verdoppeln
                // Text in kontrollierten Inputs (siehe Chat.jsx); der State
                // wird über onChange nur fürs Speichern gespiegelt.
                defaultValue={settings.orch_model || ""}
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
              Benachrichtigungen
            </span>
            <p className="mb-2 text-xs text-slate-400">
              Web-Push auf diesem Gerät: Rückfragen an dich und fertige Tasks
              melden sich auch, wenn die App im Hintergrund schläft.
            </p>
            {pushInfo.status === "prueft" && (
              <p className="text-xs text-slate-400">prüft…</p>
            )}
            {pushInfo.status === "unsupported" && (
              <p className="text-xs text-slate-400">
                Dieser Browser unterstützt kein Web-Push (oder der Service
                Worker ist noch nicht registriert — Seite neu laden).
              </p>
            )}
            {pushInfo.status === "server-aus" && (
              <p className="text-xs text-amber-600 dark:text-amber-400">
                Server kann keine VAPID-Schlüssel erzeugen — Backend-Log prüfen.
              </p>
            )}
            {pushInfo.status === "denied" && (
              <p className="text-xs text-amber-600 dark:text-amber-400">
                Benachrichtigungen sind im Browser blockiert — in den
                Website-Einstellungen wieder erlauben.
              </p>
            )}
            {pushInfo.status === "fehler" && (
              <p className="text-xs text-red-600 dark:text-red-400">
                {pushInfo.detail}
              </p>
            )}
            {(pushInfo.status === "an" || pushInfo.status === "aus") && (
              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => schaltePush(pushInfo.status !== "an")}
                  disabled={pushBusy}
                  className={`rounded px-3 py-1 text-sm font-medium disabled:opacity-40 ${
                    pushInfo.status === "an"
                      ? "border border-slate-300 text-slate-600 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
                      : "bg-blue-600 text-white hover:bg-blue-700"
                  }`}
                >
                  {pushInfo.status === "an"
                    ? "Deaktivieren"
                    : "Auf diesem Gerät aktivieren"}
                </button>
                {pushInfo.status === "an" && (
                  <button
                    onClick={() => pushTest().catch(() => {})}
                    disabled={pushBusy || pushInfo.info?.sender === false}
                    title="Testbenachrichtigung an alle registrierten Geräte"
                    className="rounded border border-slate-300 px-3 py-1 text-sm text-slate-600 hover:bg-slate-50 disabled:opacity-40 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
                  >
                    Test senden
                  </button>
                )}
                <span className="text-xs text-slate-400">
                  {pushInfo.info?.subscriptions ?? 0} Gerät(e) registriert
                </span>
                {pushInfo.info?.sender === false && (
                  <span className="text-xs text-amber-600 dark:text-amber-400">
                    Versand erst nach dem nächsten Image-Rebuild (pywebpush
                    fehlt noch im Container).
                  </span>
                )}
              </div>
            )}
          </div>

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
              {/* defaultValue statt value (GBoard-Regel, s. o.). Die Länge
                  steckt mit im key: nach Löschen/Hinzufügen remounten alle
                  Zeilen mit den State-Werten (sonst zeigten die uncontrolled
                  Inputs die Werte der verrutschten Nachbarzeile); beim
                  Tippen selbst bleibt der key stabil. */}
              {extWindows.map((w, i) => (
                <div key={`${i}von${extWindows.length}`} className="flex gap-2">
                  <input
                    defaultValue={w.name || ""}
                    onChange={(e) => updateWindow(i, { name: e.target.value })}
                    placeholder="Name"
                    className="w-28 rounded border border-slate-300 px-2 py-1.5 dark:border-slate-600 dark:bg-slate-800"
                  />
                  <input
                    defaultValue={w.url || ""}
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
