export default function TopBar({
  sessionId,
  onOpenSettings,
  theme,
  onToggleTheme,
  viewMode,
  onToggleViewMode,
  onLogout,
}) {
  // Mobil ist die Leiste eng: Session-Kennung ausblenden, Einstellungen/
  // Abmelden als Icon — sonst schiebt sich die Knopfreihe aus dem Bild.
  // pt-[max(…)] hält sie unter Notch/Statusleiste (viewport-fit=cover).
  return (
    <header className="flex items-center justify-between border-b bg-white px-3 pb-2.5 pt-[max(0.625rem,env(safe-area-inset-top))] sm:px-4 dark:border-slate-700 dark:bg-slate-900">
      <h1 className="font-semibold text-slate-800 dark:text-slate-100">
        agent-dashboard
      </h1>
      <div className="flex items-center gap-2 text-xs text-slate-500 sm:gap-3 dark:text-slate-400">
        <span className="hidden sm:inline">{sessionId ? `Session ${sessionId.slice(0, 8)}…` : "neue Session"}</span>
        <button
          onClick={onToggleViewMode}
          title={
            viewMode === "windows"
              ? "Ein Panel vollflächig, Wechsel über Tabs unten"
              : "Frei verschiebbare Fenster"
          }
          className="hidden rounded border border-slate-300 px-2 py-1 hover:bg-slate-50 md:block dark:border-slate-600 dark:hover:bg-slate-800"
        >
          {viewMode === "windows" ? "Tab-Modus" : "Fenster-Modus"}
        </button>
        {viewMode === "windows" && (
          <button
            onClick={() => window.dispatchEvent(new Event("workspace:reset"))}
            title="Fensteranordnung auf Standard zurücksetzen"
            className="hidden rounded border border-slate-300 px-2 py-1 hover:bg-slate-50 md:block dark:border-slate-600 dark:hover:bg-slate-800"
          >
            Fenster anordnen
          </button>
        )}
        <button
          onClick={onToggleTheme}
          title={theme === "dark" ? "Hell-Modus" : "Dunkel-Modus"}
          className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-800"
        >
          {theme === "dark" ? "☀️" : "🌙"}
        </button>
        <button
          onClick={onOpenSettings}
          title="Einstellungen"
          className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-800"
        >
          <span className="sm:hidden">⚙️</span>
          <span className="hidden sm:inline">Einstellungen</span>
        </button>
        <button
          onClick={onLogout}
          title="Session-Cookie löschen und zum Login zurück"
          className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-800"
        >
          <span className="sm:hidden">🚪</span>
          <span className="hidden sm:inline">Abmelden</span>
        </button>
      </div>
    </header>
  );
}
