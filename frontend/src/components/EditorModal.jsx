import { useEffect, useRef, useState } from "react";
import { basicSetup } from "codemirror";
import { EditorView, keymap } from "@codemirror/view";
import { Compartment, EditorState } from "@codemirror/state";
import { indentWithTab } from "@codemirror/commands";
import { LanguageDescription } from "@codemirror/language";
import { languages } from "@codemirror/language-data";
import { oneDark } from "@codemirror/theme-one-dark";
import { getFileContent, getRemoteFile, saveFile, downloadUrl } from "../api";
import { bestaetigen } from "./Dialog";
import { t } from "../sprache";

// Vollbild-Editor (CodeMirror 6) für Workspace- und Remote-Dateien.
// Sprache wird am Dateinamen erkannt und lazy nachgeladen (language-data).
// Speichern: Button oder Strg/Cmd+S. Angeschnittene (truncated) Dateien
// öffnen read-only — Speichern würde den Rest der Datei abschneiden.
export default function EditorModal({ source, path, onClose }) {
  const hostRef = useRef(null);
  const viewRef = useRef(null);
  const saveRef = useRef(() => {});
  const [status, setStatus] = useState(t("lädt…"));
  const [error, setError] = useState(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [readonly, setReadonly] = useState(false);
  // Kodierung aus dem Lese-Ergebnis — beim Speichern zurückgeben, damit
  // z. B. eine cp1252-Datei vom Windows-PC nicht still UTF-8 wird.
  const [encoding, setEncoding] = useState("utf-8");

  const filename = path.split("/").pop();

  async function doSave() {
    const view = viewRef.current;
    if (!view || readonly) return;
    setStatus(t("speichert…"));
    setError(null);
    try {
      await saveFile(source, path, view.state.doc.toString(), encoding);
      setDirty(false);
      setStatus(t("gespeichert"));
    } catch (e) {
      setError(String(e.message || e));
      setStatus("");
    }
  }
  saveRef.current = doSave;

  // Ungespeichertes nicht kommentarlos wegwerfen (der ●-Punkt allein wird
  // beim Zuklappen leicht übersehen).
  async function requestClose() {
    if (
      dirty &&
      !(await bestaetigen({
        title: t("Ungespeicherte Änderungen"),
        text: t("„{0}“ hat ungespeicherte Änderungen. Trotzdem schließen?", filename),
        ok: t("Schließen"),
        danger: true,
      }))
    )
      return;
    onClose();
  }

  useEffect(() => {
    let view;
    let cancelled = false;

    (async () => {
      let data;
      try {
        data =
          source === "ws"
            ? await getFileContent(path)
            : await getRemoteFile(source, path);
      } catch (e) {
        if (!cancelled) {
          setError(String(e.message || e));
          setLoadFailed(true);
          setStatus("");
        }
        return;
      }
      if (cancelled) return;

      const ro = !!data.truncated;
      setReadonly(ro);
      setEncoding(data.encoding || "utf-8");
      setStatus(ro ? t("read-only (Datei gekürzt geladen)") : "");

      const dark = document.documentElement.classList.contains("dark");
      const langCompartment = new Compartment();
      view = new EditorView({
        state: EditorState.create({
          doc: data.content,
          extensions: [
            basicSetup,
            keymap.of([
              {
                key: "Mod-s",
                run: () => {
                  saveRef.current();
                  return true;
                },
              },
              indentWithTab,
            ]),
            langCompartment.of([]),
            ...(dark ? [oneDark] : []),
            EditorState.readOnly.of(ro),
            EditorView.updateListener.of((u) => {
              if (u.docChanged) setDirty(true);
            }),
            EditorView.theme({
              "&": { height: "100%", fontSize: "13px" },
              ".cm-scroller": { overflow: "auto" },
            }),
          ],
        }),
        parent: hostRef.current,
      });
      viewRef.current = view;

      // Sprache anhand des Dateinamens lazy laden
      const desc = LanguageDescription.matchFilename(languages, filename);
      if (desc) {
        desc.load().then((lang) => {
          if (viewRef.current === view)
            view.dispatch({ effects: langCompartment.reconfigure(lang) });
        });
      }
    })();

    return () => {
      cancelled = true;
      viewRef.current = null;
      view?.destroy();
    };
  }, [source, path]);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-white dark:bg-slate-900">
      <div className="flex shrink-0 items-center gap-2 border-b px-3 py-2 dark:border-slate-700">
        <span className="min-w-0 flex-1 truncate font-mono text-sm" title={path}>
          {source !== "ws" && (
            <span className="mr-1 rounded bg-slate-200 px-1.5 py-0.5 text-xs text-slate-600 dark:bg-slate-700 dark:text-slate-300">
              {source}
            </span>
          )}
          {filename}
          {dirty && <span className="ml-1 text-amber-500">●</span>}
        </span>
        {encoding !== "utf-8" && (
          <span
            title={t("Datei-Kodierung — wird beim Speichern beibehalten")}
            className="shrink-0 rounded bg-slate-200 px-1.5 py-0.5 text-xs text-slate-600 dark:bg-slate-700 dark:text-slate-300"
          >
            {encoding}
          </span>
        )}
        <span className="shrink-0 text-xs text-slate-400 dark:text-slate-500">
          {status}
        </span>
        <a
          href={downloadUrl(source, path)}
          download={filename}
          className="shrink-0 rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          ⤓
        </a>
        <button
          onClick={doSave}
          disabled={readonly || !dirty}
          className="shrink-0 rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-40"
        >
          {t("Speichern")}
        </button>
        <button
          onClick={requestClose}
          className="shrink-0 rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          ✕
        </button>
      </div>
      {error && (
        <div className="shrink-0 border-b border-red-200 bg-red-50 px-3 py-1.5 text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
          {loadFailed && (
            <>
              {" — "}
              <a
                href={downloadUrl(source, path)}
                download={filename}
                className="underline"
              >
                {t("stattdessen herunterladen")}
              </a>
            </>
          )}
        </div>
      )}
      <div ref={hostRef} className="min-h-0 flex-1 overflow-hidden" />
    </div>
  );
}
