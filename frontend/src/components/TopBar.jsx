export default function TopBar({ sessionId, onOpenSettings, theme, onToggleTheme }) {
  return (
    <header className="flex items-center justify-between border-b bg-white px-4 py-2.5 dark:border-slate-700 dark:bg-slate-900">
      <h1 className="font-semibold text-slate-800 dark:text-slate-100">
        agent-dashboard
      </h1>
      <div className="flex items-center gap-3 text-xs text-slate-500 dark:text-slate-400">
        <span>{sessionId ? `Session ${sessionId.slice(0, 8)}…` : "neue Session"}</span>
        <button
          onClick={() => window.dispatchEvent(new Event("workspace:reset"))}
          title="Fensteranordnung auf Standard zurücksetzen"
          className="hidden rounded border border-slate-300 px-2 py-1 hover:bg-slate-50 md:block dark:border-slate-600 dark:hover:bg-slate-800"
        >
          Fenster anordnen
        </button>
        <button
          onClick={onToggleTheme}
          title={theme === "dark" ? "Hell-Modus" : "Dunkel-Modus"}
          className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-800"
        >
          {theme === "dark" ? "☀️" : "🌙"}
        </button>
        <button
          onClick={onOpenSettings}
          className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-800"
        >
          Einstellungen
        </button>
      </div>
    </header>
  );
}
