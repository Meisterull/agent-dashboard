import { useEffect, useRef, useState } from "react";
import Modal from "./Modal";
import {
  getConnections,
  getFiles,
  getRemoteFiles,
  downloadUrl,
  uploadFiles,
  mkdir,
  renamePath,
  deletePath,
  saveFile,
} from "../api";

// Datei-Browser mit umschaltbarer Quelle: Container-Workspace ("ws") oder
// eine SSH-Verbindung (SFTP auf dem Agenten-PC). Workspace-Pfade sind relativ,
// Remote-Pfade absolut (leer = Home). Upload lädt in das aktuelle Verzeichnis,
// Download läuft als normaler Browser-Download über die API.
function fmtSize(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} K`;
  return `${(n / 1024 / 1024).toFixed(1)} M`;
}

const dlgBtn =
  "rounded border border-slate-300 px-3 py-1 text-slate-600 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800";

// Eigene Dialoge statt prompt()/confirm(): die blockieren den Tab, sehen auf
// dem Handy fremd aus und werden von manchen Browsern (installierte PWA)
// unterdrückt — dann fiele "+Datei" ersatzlos aus.
function FileDialog({ dlg, onClose }) {
  const inputRef = useRef(null);
  const submit = (e) => {
    e.preventDefault();
    if (dlg.kind === "prompt") {
      const value = (inputRef.current?.value || "").trim();
      if (!value) return;
      onClose();
      dlg.run(value);
    } else {
      onClose();
      dlg.run();
    }
  };
  return (
    <Modal title={dlg.title} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3 text-sm">
        {dlg.kind === "prompt" ? (
          <label className="block">
            <span className="mb-1 block text-slate-600 dark:text-slate-300">
              {dlg.label}
            </span>
            <input
              ref={inputRef}
              autoFocus
              defaultValue={dlg.initial || ""}
              className="w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-xs dark:border-slate-600 dark:bg-slate-800"
            />
          </label>
        ) : (
          <p className="text-slate-600 dark:text-slate-300">{dlg.text}</p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={dlgBtn}>
            Abbrechen
          </button>
          <button
            type="submit"
            className={`rounded px-3 py-1 font-medium text-white ${
              dlg.danger ? "bg-red-600 hover:bg-red-700" : "bg-blue-600 hover:bg-blue-700"
            }`}
          >
            {dlg.ok}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export default function FilesPanel({ refreshKey, onOpenFile }) {
  const [connections, setConnections] = useState([]);
  const [source, setSource] = useState("ws");
  const [path, setPath] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [localKey, setLocalKey] = useState(0); // ↻-Button: Listing neu laden
  const [dlg, setDlg] = useState(null); // Anlegen/Umbenennen/Löschen-Dialog
  const inputRef = useRef(null);

  useEffect(() => {
    const load = () =>
      getConnections()
        .then((d) => setConnections(d.connections.map((c) => c.name)))
        .catch(() => setConnections([]));
    load();
    window.addEventListener("connections:changed", load);
    return () => window.removeEventListener("connections:changed", load);
  }, []);

  useEffect(() => {
    let stale = false;
    setError(null);
    setData(null);
    const req = source === "ws" ? getFiles(path) : getRemoteFiles(source, path);
    req
      .then((d) => !stale && setData(d))
      .catch((e) => !stale && setError(String(e.message || e)));
    return () => {
      stale = true;
    };
  }, [source, path, refreshKey, localKey]);

  const switchSource = (s) => {
    setSource(s);
    setPath("");
  };

  // Eine Ebene hoch: Workspace über den relativen Pfad, remote via API-parent
  const parent =
    source === "ws"
      ? path
        ? path.split("/").slice(0, -1).join("/")
        : null
      : (data && data.parent) ?? null;
  const canUp = source === "ws" ? path !== "" : parent !== null && data;

  // aktuelles Verzeichnis (ws: relativ, remote: absolut aus der Antwort)
  const curDir = source === "ws" ? path : data?.path || "";
  const joinDir = (name) => (curDir ? `${curDir}/${name}` : name);

  async function run(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      // Neu laden über den Lade-Effekt statt über ein eigenes reload(): der
      // Effekt verwirft veraltete Antworten (stale-Flag). Das eigene reload()
      // kannte den Wechsel nicht und hat nach einer Navigation das frische
      // Listing wieder mit dem alten Verzeichnis überschrieben.
      setLocalKey((k) => k + 1);
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  function onUpload(e) {
    const files = e.target.files;
    if (!files?.length) return;
    run(() => uploadFiles(source, curDir, files)).finally(() => {
      e.target.value = "";
    });
  }

  function onNewFile() {
    setDlg({
      kind: "prompt",
      title: "Neue Datei",
      label: "Name der neuen Datei",
      ok: "Anlegen",
      // leere Datei anlegen und direkt im Editor öffnen
      run: (name) =>
        run(async () => {
          const target = joinDir(name);
          await saveFile(source, target, "");
          onOpenFile({ source, path: target });
        }),
    });
  }

  function onNewDir() {
    setDlg({
      kind: "prompt",
      title: "Neuer Ordner",
      label: "Name des neuen Ordners",
      ok: "Anlegen",
      run: (name) => run(() => mkdir(source, joinDir(name))),
    });
  }

  function onRename(entry) {
    setDlg({
      kind: "prompt",
      title: "Umbenennen",
      label: `Neuer Name für „${entry.name}“`,
      initial: entry.name,
      ok: "Umbenennen",
      run: (name) => {
        if (name === entry.name) return;
        run(() => renamePath(source, entry.path, joinDir(name)));
      },
    });
  }

  function onDelete(entry) {
    const what = entry.type === "dir" ? "Ordner (samt Inhalt)" : "Datei";
    setDlg({
      kind: "confirm",
      title: "Löschen",
      text: `${what} „${entry.name}“ wirklich löschen?`,
      ok: "Löschen",
      danger: true,
      run: () => run(() => deletePath(source, entry.path)),
    });
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {dlg && <FileDialog dlg={dlg} onClose={() => setDlg(null)} />}
      <div className="flex items-center gap-1 overflow-x-auto border-b bg-slate-50 px-2 py-1 dark:border-slate-700 dark:bg-slate-900">
        <button
          onClick={() => switchSource("ws")}
          className={`shrink-0 rounded px-2 py-0.5 text-xs ${
            source === "ws"
              ? "bg-blue-600 text-white"
              : "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
          }`}
        >
          Workspace
        </button>
        {connections.map((c) => (
          <button
            key={c}
            onClick={() => switchSource(c)}
            className={`shrink-0 rounded px-2 py-0.5 text-xs ${
              source === c
                ? "bg-blue-600 text-white"
                : "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
            }`}
          >
            {c}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-1 border-b px-2 py-1 text-xs dark:border-slate-700">
        <button
          onClick={() => canUp && setPath(parent)}
          disabled={!canUp}
          title="eine Ebene hoch"
          className="rounded px-1.5 py-0.5 text-slate-500 hover:bg-slate-100 disabled:opacity-30 dark:text-slate-400 dark:hover:bg-slate-800"
        >
          ↑
        </button>
        <span
          className="min-w-0 flex-1 truncate font-mono text-slate-500 dark:text-slate-400"
          title={data?.path || path}
        >
          {source === "ws" ? `/${path}` : data?.path || "…"}
        </span>
        <button
          onClick={() => setLocalKey((k) => k + 1)}
          disabled={busy}
          title="Liste aktualisieren"
          className="shrink-0 rounded px-1.5 py-0.5 text-slate-500 hover:bg-slate-100 disabled:opacity-30 dark:text-slate-400 dark:hover:bg-slate-800"
        >
          ↻
        </button>
        <button
          onClick={onNewFile}
          disabled={busy || !data}
          title="neue Datei anlegen"
          className="shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-slate-600 hover:bg-slate-50 disabled:opacity-40 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          +Datei
        </button>
        <button
          onClick={onNewDir}
          disabled={busy || !data}
          title="neuen Ordner anlegen"
          className="shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-slate-600 hover:bg-slate-50 disabled:opacity-40 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          +Ordner
        </button>
        <button
          onClick={() => inputRef.current?.click()}
          disabled={busy || !data}
          title="Dateien in dieses Verzeichnis hochladen"
          className="shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-slate-600 hover:bg-slate-50 disabled:opacity-40 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          {busy ? "…" : "⇪"}
        </button>
        <input ref={inputRef} type="file" multiple hidden onChange={onUpload} />
      </div>

      <div className="flex-1 overflow-y-auto py-1">
        {error && (
          <p className="px-3 py-1 text-xs text-red-600 dark:text-red-400">{error}</p>
        )}
        {!error && !data && (
          <p className="px-3 py-1 text-xs text-slate-400 dark:text-slate-500">lädt…</p>
        )}
        {data && data.entries.length === 0 && (
          <p className="px-3 py-1 text-xs text-slate-400 dark:text-slate-500">leer</p>
        )}
        {data &&
          data.entries.map((e) => (
            <div
              key={e.path}
              className="group flex items-center gap-1 pr-1 hover:bg-slate-100 dark:hover:bg-slate-800"
            >
              <button
                onClick={() =>
                  e.type === "dir" ? setPath(e.path) : onOpenFile({ source, path: e.path })
                }
                title={e.path}
                className="flex min-w-0 flex-1 items-center gap-1.5 px-2 py-1 text-left text-xs"
              >
                <span className="w-4 shrink-0 text-center">
                  {e.type === "dir" ? "📁" : "📄"}
                </span>
                <span className="truncate">{e.name}</span>
                <span className="ml-auto shrink-0 pl-2 text-[10px] text-slate-400 dark:text-slate-500">
                  {fmtSize(e.size)}
                </span>
              </button>
              {e.type === "file" && (
                <a
                  href={downloadUrl(source, e.path)}
                  download={e.name}
                  title="herunterladen"
                  className="shrink-0 rounded px-1 py-0.5 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-700 dark:text-slate-500 dark:hover:bg-slate-700 dark:hover:text-slate-200"
                >
                  ⤓
                </a>
              )}
              <button
                onClick={() => onRename(e)}
                title="umbenennen"
                className="shrink-0 rounded px-1 py-0.5 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-700 dark:text-slate-500 dark:hover:bg-slate-700 dark:hover:text-slate-200"
              >
                ✎
              </button>
              <button
                onClick={() => onDelete(e)}
                title="löschen"
                className="shrink-0 rounded px-1 py-0.5 text-xs text-slate-400 hover:bg-red-100 hover:text-red-600 dark:text-slate-500 dark:hover:bg-red-950 dark:hover:text-red-400"
              >
                🗑
              </button>
            </div>
          ))}
      </div>
    </div>
  );
}
