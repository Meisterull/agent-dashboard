import { useEffect, useState } from "react";
import { fassungBeobachten } from "../fassung";
import { t } from "../sprache";

// Hinweis nach einem Deploy: die Seite läuft noch mit dem alten Skript
// (fassung.js). Kein automatisches Neuladen — ein halb getippter Text im
// Chat ginge verloren; Terminal-Sessions laufen auf dem Server weiter und
// sind nach dem Neuladen wieder da.
export default function NeueFassung() {
  const [da, setDa] = useState(false);
  useEffect(() => fassungBeobachten(() => setDa(true)), []);
  if (!da) return null;
  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-sky-300 bg-sky-100 px-3 py-1.5 text-sm text-sky-900 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-100">
      <span className="min-w-0 flex-1">
        {t("Neue Fassung des Dashboards verfügbar — Terminal-Sessions bleiben erhalten.")}
      </span>
      <button
        onClick={() => window.location.reload()}
        className="shrink-0 rounded bg-sky-600 px-3 py-1 text-sm font-medium text-white hover:bg-sky-700"
      >
        {t("Neu laden")}
      </button>
    </div>
  );
}
