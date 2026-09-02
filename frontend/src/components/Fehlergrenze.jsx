import { Component } from "react";
import { t } from "../sprache";

// ErrorBoundary je Panel (Review 02.09., P1-4/P2): Ein Render-Fehler in
// EINEM Panel (z.B. unerwartete Serverdaten) riss vorher das ganze Dashboard
// in eine weiße Seite — Chat, Terminals, alles weg, nur ein Reload half.
// Jetzt fällt nur das betroffene Panel aus und lässt sich neu laden;
// SSH-Sessions und der restliche Zustand bleiben unberührt.
export default class Fehlergrenze extends Component {
  constructor(props) {
    super(props);
    this.state = { fehler: null, anlauf: 0 };
  }

  static getDerivedStateFromError(fehler) {
    return { fehler };
  }

  componentDidCatch(fehler, info) {
    // Nur loggen — der Fehler ist im Panel sichtbar, nichts geht verloren.
    console.error("[panel]", this.props.titel || "?", fehler, info?.componentStack);
  }

  render() {
    if (!this.state.fehler) return this.props.children;
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center text-sm text-slate-500 dark:text-slate-400">
        <div className="font-medium text-red-600 dark:text-red-400">
          {t("Dieses Panel ist abgestürzt.")}
        </div>
        <div className="max-w-full overflow-hidden text-ellipsis whitespace-nowrap font-mono text-xs">
          {String(this.state.fehler?.message || this.state.fehler)}
        </div>
        <button
          onClick={() =>
            this.setState((s) => ({ fehler: null, anlauf: s.anlauf + 1 }))
          }
          className="rounded border border-slate-300 px-3 py-1 hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-800"
        >
          {t("Panel neu laden")}
        </button>
      </div>
    );
  }
}
