import { useEffect, useState } from "react";
import Modal from "./Modal";
import { bestaetigen } from "./Dialog";
import { t } from "../sprache";
import {
  getConnections,
  createConnection,
  deleteConnection,
  getConnectionPubkey,
  getProjekte,
  putProjekte,
} from "../api";

// SSH-Verbindungen verwalten: anlegen (Server erzeugt das Schlüsselpaar,
// der Public Key muss einmalig auf den Zielrechner), löschen, Key erneut
// anzeigen. Handgepflegte Einträge aus agents.yaml sind hier nur sichtbar.
// Nach Änderungen wird "connections:changed" gefeuert — Terminal- und
// Datei-Panel laden ihre Verbindungsliste dann neu.

function notifyChanged() {
  window.dispatchEvent(new CustomEvent("connections:changed"));
}

function SetupHint({ result }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard
      ?.writeText(result.setup_command)
      .then(() => setCopied(true))
      .catch(() => {});
  };
  return (
    <div className="rounded border border-emerald-300 bg-emerald-50 p-2 text-xs dark:border-emerald-800 dark:bg-emerald-950">
      <p className="mb-1 font-medium text-emerald-800 dark:text-emerald-300">
        {t(
          "„{0}“ angelegt. Einmalig auf dem Zielrechner ausführen (als der SSH-Benutzer):",
          result.name,
        )}
      </p>
      <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded bg-white p-1.5 font-mono text-[10px] dark:bg-slate-900">
        {result.setup_command}
      </pre>
      <button
        onClick={copy}
        className="mt-1 rounded border border-emerald-400 px-2 py-0.5 text-emerald-700 hover:bg-emerald-100 dark:text-emerald-300 dark:hover:bg-emerald-900"
      >
        {copied ? t("kopiert ✓") : t("Befehl kopieren")}
      </button>
    </div>
  );
}

// Projekt-Terminals (11.10.2026): je Verbindung eine Liste {name, pfad,
// befehl}, gespeichert über PUT /api/projekte/<verbindung> (projekte.yaml).
// Nach dem Speichern "projekte:changed" — das Terminal-Panel lädt dann neu.
// Zeilen sind uncontrolled (defaultValue, GBoard-Regel wie unten) mit
// stabilen Schlüsseln, damit Entfernen in der Mitte keine Werte verrutscht.
let zeilenZaehler = 0;
const neueZeile = (p = {}) => ({
  id: ++zeilenZaehler,
  name: p.name || "",
  pfad: p.pfad || "",
  befehl: p.befehl || "claude --continue",
});

function ProjekteEditor({ verbindung, projekte, onGespeichert }) {
  const [auf, setAuf] = useState(false);
  const [zeilen, setZeilen] = useState(() => projekte.map(neueZeile));
  const [busy, setBusy] = useState(false);
  const [ok, setOk] = useState(false);
  const [fehler, setFehler] = useState(null);

  const setze = (id, feld) => (e) =>
    setZeilen((z) => z.map((r) => (r.id === id ? { ...r, [feld]: e.target.value } : r)));
  const entferne = (id) => setZeilen((z) => z.filter((r) => r.id !== id));

  async function speichern() {
    setBusy(true);
    setFehler(null);
    setOk(false);
    try {
      const res = await putProjekte(
        verbindung,
        zeilen.map(({ name, pfad, befehl }) => ({
          name: name.trim(),
          pfad: pfad.trim(),
          befehl: befehl.trim(),
        })),
      );
      setZeilen(res.projekte.map(neueZeile));
      setOk(true);
      onGespeichert();
      window.dispatchEvent(new CustomEvent("projekte:changed"));
    } catch (err) {
      setFehler(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  const feld =
    "min-w-0 rounded border border-slate-300 px-1.5 py-1 font-mono text-[11px] dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100";
  return (
    <div className="ml-2 border-l border-slate-200 pl-2 dark:border-slate-700">
      <button
        onClick={() => setAuf((a) => !a)}
        data-test={`projekte-editor-${verbindung}`}
        className="text-[11px] text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
      >
        {auf ? "▾" : "▸"} 📁 {t("Projekte")} ({zeilen.length})
      </button>
      {auf && (
        <div className="mb-2 mt-1 space-y-1">
          {zeilen.map((z) => (
            <div key={z.id} className="grid grid-cols-[6rem_1fr_1fr_auto] items-center gap-1">
              <input defaultValue={z.name} onChange={setze(z.id, "name")} placeholder={t("Name")} className={feld} />
              <input defaultValue={z.pfad} onChange={setze(z.id, "pfad")} placeholder={t("Pfad auf dem Zielrechner")} className={feld} />
              <input defaultValue={z.befehl} onChange={setze(z.id, "befehl")} placeholder={t("Startbefehl")} className={feld} />
              <button
                onClick={() => entferne(z.id)}
                title={t("Projekt entfernen")}
                className="rounded px-1 text-slate-400 hover:bg-red-100 hover:text-red-600 dark:hover:bg-red-950 dark:hover:text-red-400"
              >
                🗑
              </button>
            </div>
          ))}
          <div className="flex items-center gap-2">
            <button
              onClick={() => setZeilen((z) => [...z, neueZeile()])}
              className="rounded border border-slate-300 px-1.5 py-0.5 text-[11px] text-slate-500 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-800"
            >
              {t("+ Projekt")}
            </button>
            <button
              onClick={speichern}
              disabled={busy}
              className="rounded bg-blue-600 px-2 py-0.5 text-[11px] font-medium text-white hover:bg-blue-700 disabled:opacity-40"
            >
              {busy ? t("speichert…") : ok ? t("gespeichert ✓") : t("Speichern")}
            </button>
            {fehler && <span className="text-[11px] text-red-600 dark:text-red-400">{fehler}</span>}
          </div>
        </div>
      )}
    </div>
  );
}

export default function ConnectionsModal({ onClose }) {
  const [connections, setConnections] = useState([]);
  const [form, setForm] = useState({ name: "", host: "", port: "22", user: "" });
  const [privateKey, setPrivateKey] = useState("");
  const [showKeyInput, setShowKeyInput] = useState(false);
  const [result, setResult] = useState(null); // {name, public_key, setup_command}
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [projekte, setProjekte] = useState(null); // null = noch nicht geladen

  const load = () =>
    getConnections()
      .then((d) => setConnections(d.connections))
      .catch(() => setConnections([]));
  const loadProjekte = () =>
    getProjekte()
      .then((d) => setProjekte(d.projekte || {}))
      .catch(() => setProjekte({}));
  useEffect(() => {
    load();
    loadProjekte();
  }, []);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setBusy(true);
    try {
      const res = await createConnection({
        name: form.name.trim(),
        host: form.host.trim(),
        port: parseInt(form.port, 10) || 22,
        user: form.user.trim(),
        private_key: privateKey.trim() || null,
      });
      setResult(res);
      setForm({ name: "", host: "", port: "22", user: "" });
      setPrivateKey("");
      // Inputs sind uncontrolled (defaultValue, GBoard-Regel s. u.) —
      // der State-Reset leert das DOM nicht mehr, das macht reset():
      e.target.reset();
      load();
      notifyChanged();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(c) {
    if (
      !(await bestaetigen({
        title: t("Verbindung löschen"),
        text: t("Verbindung „{0}“ löschen (samt Schlüssel)?", c.name),
        ok: t("Löschen"),
        danger: true,
      }))
    )
      return;
    setError(null);
    try {
      await deleteConnection(c.name);
      load();
      notifyChanged();
    } catch (err) {
      setError(String(err.message || err));
    }
  }

  async function showKey(c) {
    setError(null);
    try {
      setResult(await getConnectionPubkey(c.name));
    } catch (err) {
      setError(String(err.message || err));
    }
  }

  const inputCls =
    "w-full rounded border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100";

  return (
    <Modal title={t("SSH-Verbindungen")} onClose={onClose}>
      <div className="space-y-4 text-sm">
        <div>
          <div className="mb-1 text-xs font-semibold text-slate-500 dark:text-slate-400">
            {t("Vorhanden")}
          </div>
          {connections.length === 0 && (
            <p className="text-xs text-slate-400">{t("keine Verbindungen")}</p>
          )}
          {connections.length > 0 && (
            <p className="mb-1 text-[11px] text-slate-400">
              {t("Projekt-Terminals: je Projekt eine dauerhafte Claude-Code-Sitzung (tmux) im Projektordner — Knopf 📁 in der Terminal-Leiste.")}
            </p>
          )}
          {connections.map((c) => (
            <div key={c.name} className="border-b border-slate-100 py-1 text-xs dark:border-slate-800">
            <div className="flex items-center gap-2">
              <span className="font-mono font-medium">{c.name}</span>
              <span className="min-w-0 flex-1 truncate text-slate-400">
                {c.user}@{c.host}:{c.port}
              </span>
              {c.source === "ui" ? (
                <>
                  <button
                    onClick={() => showKey(c)}
                    title={t("Public Key / Einrichtungsbefehl anzeigen")}
                    className="shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-slate-500 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-800"
                  >
                    {t("Key")}
                  </button>
                  <button
                    onClick={() => onDelete(c)}
                    title={t("Verbindung löschen")}
                    className="shrink-0 rounded px-1 py-0.5 text-slate-400 hover:bg-red-100 hover:text-red-600 dark:hover:bg-red-950 dark:hover:text-red-400"
                  >
                    🗑
                  </button>
                </>
              ) : (
                <span
                  title={t("in agents.yaml gepflegt — dort von Hand ändern")}
                  className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500 dark:bg-slate-800 dark:text-slate-400"
                >
                  agents.yaml
                </span>
              )}
            </div>
            {projekte && (
              <ProjekteEditor
                verbindung={c.name}
                projekte={projekte[c.name] || []}
                onGespeichert={loadProjekte}
              />
            )}
            </div>
          ))}
        </div>

        {result && <SetupHint result={result} />}
        {error && (
          <p className="rounded bg-red-100 px-2 py-1.5 text-xs text-red-700 dark:bg-red-950 dark:text-red-300">
            {error}
          </p>
        )}

        <form onSubmit={submit} className="space-y-2">
          <div className="text-xs font-semibold text-slate-500 dark:text-slate-400">
            {t("Neue Verbindung")}
          </div>
          {/* defaultValue statt value: kontrollierte Inputs lassen angetippte
              Wortvorschläge der Handy-Tastatur den Text doppelt einfügen
              (bekanntes GBoard-Muster, siehe Chat.jsx) — der State bleibt
              über onChange nur als Spiegel für submit gepflegt. */}
          <div className="grid grid-cols-2 gap-2">
            <input defaultValue={form.name} onChange={set("name")} placeholder={t("Name (z.B. buero-pc)")} className={inputCls} required />
            <input defaultValue={form.user} onChange={set("user")} placeholder={t("SSH-Benutzer")} className={inputCls} required />
            <input defaultValue={form.host} onChange={set("host")} placeholder={t("Host / IP")} className={inputCls} required />
            <input defaultValue={form.port} onChange={set("port")} placeholder={t("Port")} inputMode="numeric" className={inputCls} />
          </div>
          <label className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
            <input
              type="checkbox"
              checked={showKeyInput}
              onChange={(e) => setShowKeyInput(e.target.checked)}
            />
            {t("vorhandenen privaten Schlüssel verwenden (sonst wird ein neues Schlüsselpaar erzeugt)")}
          </label>
          {showKeyInput && (
            <textarea
              defaultValue={privateKey}
              onChange={(e) => setPrivateKey(e.target.value)}
              rows={4}
              placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
              className={`${inputCls} font-mono text-xs`}
            />
          )}
          <button
            type="submit"
            disabled={busy}
            className="rounded bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
          >
            {busy ? t("legt an…") : t("Anlegen")}
          </button>
        </form>
      </div>
    </Modal>
  );
}
