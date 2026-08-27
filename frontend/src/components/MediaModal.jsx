import { useEffect, useRef } from "react";
import { downloadUrl, rawUrl } from "../api";

// Vorschau für Dateien, die kein Text sind (Issues #25/#26): Bilder, PDFs und
// Audio direkt im Dashboard, statt sie erst in den Download-Ordner und von
// dort in eine fremde App zu bringen. Auf dem Handy war das bisher so
// umständlich, dass man es gelassen hat.
//
// Bewusst vollflächig statt als kleiner Dialog: Ein Screenshot in einem
// Kästchen von 400 px hilft niemandem, und der Schließen-Knopf soll groß genug
// sein, um ihn ohne Zielen zu treffen.

const BILD = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;
const PDF = /\.pdf$/i;
const TON = /\.(mp3|wav|ogg|oga|m4a|aac|flac|opus|weba)$/i;

/** Womit eine Datei geöffnet werden will — `null` heißt: ab in den Editor. */
export function medienArt(name = "") {
  if (BILD.test(name)) return "bild";
  if (PDF.test(name)) return "pdf";
  if (TON.test(name)) return "ton";
  return null;
}

export default function MediaModal({ art, source, path, name, size, onClose }) {
  const audioRef = useRef(null);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      // Ohne das läuft die Aufnahme nach dem Schließen unsichtbar weiter.
      audioRef.current?.pause();
    };
  }, [onClose]);

  // Sperrbildschirm und Benachrichtigung sollen wissen, was läuft — sonst
  // steht dort in der PWA nur der Seitentitel.
  useEffect(() => {
    if (art !== "ton" || !("mediaSession" in navigator)) return;
    navigator.mediaSession.metadata = new window.MediaMetadata({
      title: name,
      artist: source === "ws" ? "Workspace" : source,
    });
  }, [art, name, source]);

  const quelle = rawUrl(source, path);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black/90">
      <div className="flex items-center gap-2 px-3 py-2 text-slate-200">
        <span className="min-w-0 flex-1 truncate text-sm" title={path}>
          {name}
        </span>
        {size != null && (
          <span className="shrink-0 text-xs text-slate-400">
            {Math.round(size / 1024)} kB
          </span>
        )}
        <a
          href={downloadUrl(source, path)}
          download={name}
          title="herunterladen"
          className="shrink-0 rounded px-2 py-1 text-sm text-slate-300 hover:bg-white/10"
        >
          ⤓
        </a>
        <a
          href={quelle}
          target="_blank"
          rel="noreferrer"
          title="in eigenem Tab öffnen"
          className="shrink-0 rounded px-2 py-1 text-sm text-slate-300 hover:bg-white/10"
        >
          ↗
        </a>
        <button
          onClick={onClose}
          title="schließen"
          className="shrink-0 rounded px-3 py-1 text-lg text-slate-200 hover:bg-white/10"
        >
          ✕
        </button>
      </div>

      <div className="flex min-h-0 flex-1 items-center justify-center p-2">
        {art === "bild" && (
          <img
            src={quelle}
            alt={name}
            className="max-h-full max-w-full object-contain"
          />
        )}
        {art === "pdf" && (
          // iOS-Safari zeigt PDFs im iframe nur seitenweise — deshalb steht
          // oben zusätzlich der ↗-Knopf, der es in einem eigenen Tab öffnet.
          <object data={quelle} type="application/pdf" className="h-full w-full">
            <p className="p-4 text-center text-sm text-slate-300">
              Dieses Gerät zeigt PDFs nicht eingebettet.{" "}
              <a href={quelle} target="_blank" rel="noreferrer" className="underline">
                In eigenem Tab öffnen
              </a>
            </p>
          </object>
        )}
        {art === "ton" && (
          <div className="w-full max-w-xl rounded-lg bg-slate-900 p-4">
            <audio
              ref={audioRef}
              src={quelle}
              controls
              autoPlay
              className="w-full"
            />
            {source !== "ws" && (
              <p className="mt-2 text-xs text-slate-500">
                Von einem entfernten Rechner: spielt von vorn, Spulen ist nicht
                möglich.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
