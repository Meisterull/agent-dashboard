import { useEffect, useRef, useState } from "react";
import Modal from "./Modal";
import MediaModal, { medienArt } from "./MediaModal";
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
import { t } from "../sprache";

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
            {t("Abbrechen")}
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

// Am Symbol sieht man schon in der Liste, was beim Antippen passiert —
// auf dem kleinen Bildschirm hilft das mehr als jede Erklärung.
const DATEI_ICON = { bild: "🖼️", pdf: "📕", ton: "🎵" };

export default function FilesPanel({ refreshKey, onOpenFile }) {
  const [connections, setConnections] = useState([]);
  const [source, setSource] = useState("ws");
  const [path, setPath] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [localKey, setLocalKey] = useState(0); // ↻-Button: Listing neu laden
  const [dlg, setDlg] = useState(null); // Anlegen/Umbenennen/Löschen-Dialog
  const [medien, setMedien] = useState(null); // Bild-/PDF-/Audio-Vorschau
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
      title: t("Neue Datei"),
      label: t("Name der neuen Datei"),
      ok: t("Anlegen"),
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
      title: t("Neuer Ordner"),
      label: t("Name des neuen Ordners"),
      ok: t("Anlegen"),
      run: (name) => run(() => mkdir(source, joinDir(name))),
    });
  }

  function onRename(entry) {
    setDlg({
      kind: "prompt",
      title: t("Umbenennen"),
      label: t("Neuer Name für „{0}“", entry.name),
      initial: entry.name,
      ok: t("Umbenennen"),
      run: (name) => {
        if (name === entry.name) return;
        run(() => renamePath(source, entry.path, joinDir(name)));
      },
    });
  }

  function onDelete(entry) {
    const what = t(entry.type === "dir" ? "Ordner (samt Inhalt)" : "Datei");
    setDlg({
      kind: "confirm",
      title: t("Löschen"),
      text: t("{0} „{1}“ wirklich löschen?", what, entry.name),
      ok: t("Löschen"),
      danger: true,
      run: () => run(() => deletePath(source, entry.path)),
    });
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {dlg && <FileDialog dlg={dlg} onClose={() => setDlg(null)} />}
      {medien && (
        <MediaModal
          art={medien.art}
          source={source}
          path={medien.path}
          name={medien.name}
          size={medien.size}
          onClose={() => setMedien(null)}
        />
      )}
      <div className="flex items-center gap-1 overflow-x-auto border-b bg-slate-50 px-2 py-1 dark:border-slate-700 dark:bg-slate-900">
        <button
          onClick={() => switchSource("ws")}
          className={`shrink-0 rounded px-2 py-0.5 text-xs ${
            source === "ws"
              ? "bg-blue-600 text-white"
              : "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
          }`}
        >
          {t("Workspace")}
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
          title={t("eine Ebene hoch")}
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
          title={t("Liste aktualisieren")}
          className="shrink-0 rounded px-1.5 py-0.5 text-slate-500 hover:bg-slate-100 disabled:opacity-30 dark:text-slate-400 dark:hover:bg-slate-800"
        >
          ↻
        </button>
        <button
          onClick={onNewFile}
          disabled={busy || !data}
          title={t("neue Datei anlegen")}
          className="shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-slate-600 hover:bg-slate-50 disabled:opacity-40 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          +{t("Datei")}
        </button>
        <button
          onClick={onNewDir}
          disabled={busy || !data}
          title={t("neuen Ordner anlegen")}
          className="shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-slate-600 hover:bg-slate-50 disabled:opacity-40 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          +{t("Ordner")}
        </button>
        <button
          onClick={() => inputRef.current?.click()}
          disabled={busy || !data}
          title={t("Dateien in dieses Verzeichnis hochladen")}
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
          <p className="px-3 py-1 text-xs text-slate-400 dark:text-slate-500">{t("lädt…")}</p>
        )}
        {data && data.entries.length === 0 && (
          <p className="px-3 py-1 text-xs text-slate-400 dark:text-slate-500">{t("leer")}</p>
        )}
        {data &&
          data.entries.map((e) => (
            <div
              key={e.path}
              className="group flex items-center gap-1 pr-1 hover:bg-slate-100 dark:hover:bg-slate-800"
            >
              <button
                onClick={() => {
                  if (e.type === "dir") return setPath(e.path);
                  // Bilder, PDFs und Audio öffnen sich in der Vorschau statt
                  // im Texteditor, der bei Binärdaten ohnehin nur
                  // "Binärdatei — nutze Download" meldet (Issues #25/#26).
                  const art = medienArt(e.name);
                  if (art)
                    return setMedien({ art, path: e.path, name: e.name, size: e.size });
                  onOpenFile({ source, path: e.path });
                }}
                title={e.path}
                className="flex min-w-0 flex-1 items-center gap-1.5 px-2 py-1 text-left text-xs"
              >
                <span className="w-4 shrink-0 text-center">
                  {e.type === "dir" ? "📁" : DATEI_ICON[medienArt(e.name)] || "📄"}
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
                  title={t("herunterladen")}
                  className="shrink-0 rounded px-1 py-0.5 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-700 dark:text-slate-500 dark:hover:bg-slate-700 dark:hover:text-slate-200"
                >
                  ⤓
                </a>
              )}
              <button
                onClick={() => onRename(e)}
                title={t("umbenennen")}
                className="shrink-0 rounded px-1 py-0.5 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-700 dark:text-slate-500 dark:hover:bg-slate-700 dark:hover:text-slate-200"
              >
                ✎
              </button>
              <button
                onClick={() => onDelete(e)}
                title={t("löschen")}
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
