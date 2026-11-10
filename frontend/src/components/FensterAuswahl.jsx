import { useEffect, useRef, useState } from "react";
import { darfAusblenden } from "../fensterSichtbar";
import { t } from "../sprache";

// Fenster ein-/ausblenden (Issue #50): dieselbe Hakenliste im Einstellungs-
// Dialog und im Schnellmenü der TopBar. Wirkt sofort und nur auf diesem Gerät
// (localStorage, App.jsx) — mit dem Speichern-Knopf der Settings hat das
// nichts zu tun.
export function FensterListe({ fenster, versteckt, onUmschalten }) {
  const ids = fenster.map((f) => f.id);
  return (
    <div className="space-y-1">
      {fenster.map(({ id, title }) => {
        const an = !versteckt.has(id);
        const gesperrt = an && !darfAusblenden(id, ids, versteckt);
        return (
          <label
            key={id}
            data-fenster={id}
            title={gesperrt ? t("Mindestens ein Fenster bleibt sichtbar") : undefined}
            className={`flex items-center gap-2 rounded px-1 py-1 text-sm ${
              gesperrt ? "opacity-50" : "cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800"
            }`}
          >
            <input
              type="checkbox"
              checked={an}
              disabled={gesperrt}
              onChange={() => onUmschalten(id)}
              className="h-4 w-4"
            />
            <span className="text-slate-700 dark:text-slate-200">{title}</span>
          </label>
        );
      })}
    </div>
  );
}

// Schnellzugriff in der TopBar: ein Knopf, darunter die Liste als Klappmenü.
export default function FensterMenue({ fenster, versteckt, onUmschalten }) {
  const [offen, setOffen] = useState(false);
  const wurzel = useRef(null);
  useEffect(() => {
    if (!offen) return undefined;
    const weg = (e) => {
      if (!wurzel.current?.contains(e.target)) setOffen(false);
    };
    const taste = (e) => e.key === "Escape" && setOffen(false);
    document.addEventListener("pointerdown", weg, true);
    document.addEventListener("keydown", taste);
    return () => {
      document.removeEventListener("pointerdown", weg, true);
      document.removeEventListener("keydown", taste);
    };
  }, [offen]);
  const aus = fenster.filter((f) => versteckt.has(f.id)).length;
  return (
    <div ref={wurzel} className="relative">
      <button
        onClick={() => setOffen((o) => !o)}
        title={t("Fenster ein- und ausblenden (nur auf diesem Gerät)")}
        aria-expanded={offen}
        data-fenster-knopf
        className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-800"
      >
        <span className="sm:hidden">▦</span>
        <span className="hidden sm:inline">{t("Fenster")}</span>
        {aus > 0 && <span className="ml-1 text-slate-400">({t("{0} aus", aus)})</span>}
      </button>
      {offen && (
        <div
          data-fenster-menue
          className="absolute right-0 top-full z-50 mt-1 max-h-[70vh] w-56 overflow-y-auto rounded-lg border border-slate-300 bg-white p-2 shadow-lg dark:border-slate-600 dark:bg-slate-900"
        >
          <p className="mb-1 px-1 text-xs text-slate-400">{t("Sichtbare Fenster")}</p>
          <FensterListe fenster={fenster} versteckt={versteckt} onUmschalten={onUmschalten} />
        </div>
      )}
    </div>
  );
}
