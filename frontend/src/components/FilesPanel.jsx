import { useEffect, useRef, useState } from "react";
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

export default function FilesPanel({ refreshKey, onOpenFile }) {
  const [connections, setConnections] = useState([]);
  const [source, setSource] = useState("ws");
  const [path, setPath] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
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
  }, [source, path, refreshKey]);

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

  async function reload() {
    const d = source === "ws" ? await getFiles(path) : await getRemoteFiles(source, path);
    setData(d);
  }

  async function run(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await reload();
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
    const name = prompt("Name der neuen Datei:");
    if (!name) return;
    // leere Datei anlegen und direkt im Editor öffnen
    run(async () => {
      const target = joinDir(name);
      await saveFile(source, target, "");
      onOpenFile({ source, path: target });
    });
  }

  function onNewDir() {
    const name = prompt("Name des neuen Ordners:");
    if (!name) return;
    run(() => mkdir(source, joinDir(name)));
  }

  function onRename(entry) {
    const name = prompt("Neuer Name:", entry.name);
    if (!name || name === entry.name) return;
    run(() => renamePath(source, entry.path, joinDir(name)));
  }

  function onDelete(entry) {
    const what = entry.type === "dir" ? "Ordner (samt Inhalt)" : "Datei";
    if (!confirm(`${what} „${entry.name}“ wirklich löschen?`)) return;
    run(() => deletePath(source, entry.path));
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
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
