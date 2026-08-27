import { memo, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  uploadFiles,
  streamChat,
  cancelChatStream,
  getChatSessions,
  getChatHistory,
  deleteChatSession,
} from "../api";
import { bestaetigen } from "./Dialog";

// Orchestrator-Antworten rendern Markdown (Sessions liegen serverseitig in
// SQLite); die zuletzt aktive Session wird nach Reload/App-Neustart über
// localStorage wiedergefunden, ältere über das Auswahlmenü im Kopf.
// memo: ReactMarkdown parst sonst bei JEDEM Re-Render (z. B. jedem
// Tool-Event während des Streamings) den kompletten Verlauf neu — auf dem
// Handy ruckelt davon das Scrollen. Die msg-Objekte sind unveränderlich
// (Verlauf wird nur angehängt), der Identitätsvergleich trägt also.
const Message = memo(function Message({ msg }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
          isUser
            ? "whitespace-pre-wrap bg-blue-600 text-white"
            : "md bg-white text-slate-800 shadow dark:bg-slate-800 dark:text-slate-100"
        }`}
      >
        {isUser ? (
          msg.text
        ) : msg.text ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
        ) : (
          <span className="italic opacity-60">(keine Textantwort)</span>
        )}
        {msg.toolCalls?.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {msg.toolCalls.map((c, i) => (
              <span
                key={i}
                className="rounded bg-slate-200 px-2 py-0.5 font-mono text-xs text-slate-700 dark:bg-slate-700 dark:text-slate-300"
              >
                {c.name}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
});

// Grobzeiger = Finger statt Maus. Trifft Handys und Tablets, nicht den
// Desktop — dort bleibt Enter zum Senden.
const grobZeiger =
  typeof window !== "undefined" &&
  window.matchMedia("(pointer: coarse)").matches;

export default function Chat({ sessionId, setSessionId, onActivity, onDone }) {
  const [messages, setMessages] = useState([]);
  const [sessions, setSessions] = useState([]);
  // Eingabe bewusst UNcontrolled (ref statt value-Prop): kontrollierte
  // Inputs bringen die Wortvorschläge der Handy-Tastatur (GBoard/Samsung)
  // aus dem Takt — ein angetippter Vorschlag fügt dann den ganzen Text
  // doppelt ein. canSend spiegelt nur den Leer-Zustand für den Button.
  const inputRef = useRef(null);
  const [canSend, setCanSend] = useState(false);
  const [zeilen, setZeilen] = useState(2);
  // Anhänge, die mit der nächsten Nachricht hochgeladen werden (Issue #29).
  const [anhaenge, setAnhaenge] = useState([]);
  const [loading, setLoading] = useState(false);
  // Streaming-Fortschritt (F3): stream_id fürs Abbrechen + bisherige Tool-Calls
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);
  // Zähler des angezeigten Verlaufs: jeder Session-Wechsel (auch "Neu")
  // erhöht ihn. Eine noch laufende Antwort wird beim Eintreffen dagegen
  // geprüft und verworfen, statt sie in den inzwischen offenen Verlauf zu
  // mischen (der bleibt sonst dauerhaft falsch, weil er so gespeichert wird).
  const viewRef = useRef(0);

  // Nur ans Ende springen, wenn der Leser eh (fast) unten steht — wer im
  // Verlauf hochgescrollt liest, würde sonst von jeder neuen Nachricht
  // heruntergerissen. Eigene Aktionen (Senden, Session-Wechsel) setzen das
  // Flag explizit, damit sie immer unten landen.
  const amBodenRef = useRef(true);
  // Hochgescrollt? Dann schwebt unten rechts ein ↓-Knopf zum Ende des
  // Verlaufs (auf dem Handy ist die Strecke sonst viel Gewische). Gleiche
  // Werte lösen in React kein Re-Render aus — der Scroll-Handler ist billig.
  const [zeigeSprung, setZeigeSprung] = useState(false);
  const merkeScrollLage = () => {
    const el = scrollRef.current;
    if (!el) return;
    const unten = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    amBodenRef.current = unten;
    setZeigeSprung(!unten);
  };
  const springeAnsEnde = () => {
    amBodenRef.current = true;
    setZeigeSprung(false);
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  };
  useEffect(() => {
    if (amBodenRef.current)
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, loading]);

  const refreshSessions = () =>
    getChatSessions()
      .then((d) => setSessions(d.sessions))
      .catch(() => setSessions([]));

  // Beim Start: Session-Liste laden + letzte Session wiederherstellen
  useEffect(() => {
    refreshSessions();
    const last = localStorage.getItem("chat-session");
    if (last) openSession(last);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openSession(id) {
    getChatHistory(id)
      .then((d) => {
        viewRef.current += 1;
        amBodenRef.current = true; // frisch geöffneter Verlauf: unten anfangen
        setMessages(d.messages);
        setError(null);
        setSessionId(id);
        localStorage.setItem("chat-session", id);
      })
      .catch(() => {
        localStorage.removeItem("chat-session");
      });
  }

  function newSession() {
    viewRef.current += 1;
    setSessionId(null);
    setMessages([]);
    setError(null);
    localStorage.removeItem("chat-session");
  }

  // Verlauf löschen (Endpunkt gibt es seit jeher, nur den Knopf nicht).
  async function removeSession() {
    if (!sessionId) return;
    const s = sessions.find((x) => x.id === sessionId);
    if (
      !(await bestaetigen({
        title: "Verlauf löschen",
        text: `Verlauf „${s?.title || sessionId.slice(0, 8)}“ endgültig löschen?`,
        ok: "Löschen",
        danger: true,
      }))
    )
      return;
    try {
      await deleteChatSession(sessionId);
      newSession();
      refreshSessions();
    } catch (e) {
      setError(`Löschen fehlgeschlagen: ${e.message || e}`);
    }
  }

  // Vom Teilen-Menü des Handys übergebener Entwurf (Issue #29): Der Service
  // Worker hat die Dateien bereits abgelegt und schickt Text plus Pfade als
  // Parameter mit. Er landet im Eingabefeld, nicht direkt im Versand — der
  // Nutzer will ja noch schreiben, worum es geht.
  useEffect(() => {
    const parameter = new URLSearchParams(window.location.search);
    const entwurf = parameter.get("entwurf");
    if (!entwurf) return;
    if (inputRef.current) {
      inputRef.current.value = entwurf;
      setCanSend(true);
      setZeilen(Math.min(6, Math.max(2, entwurf.split("\n").length + 1)));
      inputRef.current.focus();
    }
    // Parameter wieder aus der Adresse nehmen, sonst steht der Entwurf beim
    // nächsten Neuladen erneut da.
    parameter.delete("entwurf");
    const rest = parameter.toString();
    window.history.replaceState(
      {},
      "",
      window.location.pathname + (rest ? `?${rest}` : ""),
    );
  }, []);

  function anhaengen(liste) {
    const dateien = Array.from(liste || []).filter(Boolean);
    if (dateien.length) setAnhaenge((a) => [...a, ...dateien]);
  }

  // Strg+V mit einem Screenshot in der Zwischenablage: am Desktop der
  // schnellste Weg, am Handy der einzige neben dem Teilen-Menü.
  function onPaste(e) {
    const dateien = Array.from(e.clipboardData?.files || []);
    if (dateien.length) {
      e.preventDefault();
      anhaengen(dateien);
    }
  }

  /** Lädt die Anhänge hoch und liefert ihre Pfade im Workspace. */
  async function anhaengeHochladen() {
    if (!anhaenge.length) return [];
    // Nach Tag sortiert ablegen, sonst wird der Ordner mit der Zeit unlesbar.
    const tag = new Date().toISOString().slice(0, 10);
    const ziel = `uploads/chat/${tag}`;
    const antwort = await uploadFiles("ws", ziel, anhaenge);
    const gespeichert = antwort?.saved || [];
    return gespeichert.map((s) => (typeof s === "string" ? s : s.path));
  }

  async function send() {
    const text = (inputRef.current?.value || "").trim();
    if ((!text && !anhaenge.length) || loading) return;
    setError(null);
    setLoading(true);

    // Erst hochladen, dann senden: Der Orchestrator bekommt die Pfade als
    // Textzeile mit — Claude-Code-Agenten können Bilder unter diesem Pfad
    // selbst ansehen, ohne dass der Chat ein neues Datenformat braucht.
    let pfade = [];
    try {
      pfade = await anhaengeHochladen();
    } catch (e) {
      setError(`Anhang fehlgeschlagen: ${e.message || e}`);
      setLoading(false);
      return;
    }
    const volltext = pfade.length
      ? `${text}${text ? "\n\n" : ""}Anhänge: ${pfade.join(", ")}`
      : text;

    if (inputRef.current) inputRef.current.value = "";
    setZeilen(2);
    setCanSend(false);
    setAnhaenge([]);
    amBodenRef.current = true; // eigene Nachricht: immer zu ihr springen
    setMessages((m) => [...m, { role: "user", text: volltext }]);
    const view = viewRef.current;
    try {
      // Streaming (F3): Tool-Calls kommen live als Events — sichtbar, was der
      // Orchestrator gerade tut, plus Abbrechen-Knopf statt Blackbox-POST.
      const data = await streamChat(volltext, sessionId, {
        onStart: (d) => {
          if (viewRef.current === view)
            setProgress({ streamId: d.stream_id, tools: [] });
        },
        onTool: (d) => {
          if (viewRef.current === view)
            setProgress((p) => (p ? { ...p, tools: [...p.tools, d.name] } : p));
        },
      });
      refreshSessions();
      onActivity?.(); // Mailboxes / Dateibaum können sich geändert haben
      // Der Verlauf ist serverseitig gespeichert — beim Zurückwechseln ist die
      // Antwort da; nur hineinmischen dürfen wir sie nicht.
      if (viewRef.current !== view) return;
      setSessionId(data.sessionId);
      localStorage.setItem("chat-session", data.sessionId);
      if (data.aborted) {
        // Abgebrochen: die reparierte Wahrheit (inkl. bereits ausgeführter
        // Tools) liegt serverseitig — neu laden statt raten.
        openSession(data.sessionId);
        return;
      }
      setMessages((m) => [
        ...m,
        { role: "assistant", text: data.reply, toolCalls: data.toolCalls },
      ]);
      onDone?.(); // Antwort fertig → Chat-Reiter blinkt, bis reingeklickt wird
    } catch (e) {
      if (viewRef.current !== view) return;
      setError(String(e.message || e));
      // Die eigene Zeile wieder zurück in die Eingabe, statt sie mit dem
      // Fehler verschwinden zu lassen — aber nur, wenn der Nutzer nicht
      // inzwischen schon etwas Neues getippt hat.
      setMessages((m) =>
        m.length && m[m.length - 1].role === "user" ? m.slice(0, -1) : m,
      );
      if (inputRef.current && !inputRef.current.value) {
        inputRef.current.value = text;
        setCanSend(true);
      }
    } finally {
      setLoading(false);
      setProgress(null);
    }
  }

  // Enter sendet — außer auf Touch-Geräten (Issue #28). Bildschirmtastaturen
  // haben kein Shift+Enter, der Umbruch wäre dort also unerreichbar und jeder
  // Absatz schickte den halben Text ab. Gesendet wird mobil über den Knopf,
  // den es ohnehin gibt.
  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey && !grobZeiger) {
      e.preventDefault();
      send();
    }
  }

  // Das Feld wächst mit dem Text, sonst sieht man mobil nur die letzten zwei
  // Zeilen dessen, was man geschrieben oder diktiert hat.
  function onEingabe(e) {
    const wert = e.target.value;
    setCanSend(wert.trim().length > 0);
    setZeilen(Math.min(6, Math.max(2, wert.split("\n").length)));
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b bg-slate-50 px-3 py-1.5 text-xs dark:border-slate-700 dark:bg-slate-900">
        <span className="shrink-0 font-semibold text-slate-500 dark:text-slate-400">
          Chat mit Orchestrator
        </span>
        <select
          value={sessionId || ""}
          onChange={(e) => e.target.value && openSession(e.target.value)}
          className="min-w-0 flex-1 truncate rounded border border-slate-300 bg-white px-1 py-0.5 text-xs dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200"
        >
          <option value="">— Verlauf —</option>
          {sessions.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title || s.id.slice(0, 8)}
            </option>
          ))}
        </select>
        <button
          onClick={newSession}
          title="neue Session beginnen"
          className="shrink-0 rounded border border-slate-300 px-2 py-0.5 text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          Neu
        </button>
        <button
          onClick={removeSession}
          disabled={!sessionId || loading}
          title="diesen Verlauf löschen"
          className="shrink-0 rounded border border-slate-300 px-2 py-0.5 text-slate-500 hover:bg-red-50 hover:text-red-600 disabled:opacity-30 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-red-950 dark:hover:text-red-400"
        >
          🗑
        </button>
      </div>
      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollRef}
          onScroll={merkeScrollLage}
          className="h-full space-y-3 overflow-y-auto p-3"
        >
        {messages.length === 0 && (
          <p className="text-sm text-slate-400 dark:text-slate-500">
            Chatte mit dem Orchestrator — z.B. „Lege dem frontend-Agent eine
            Aufgabe an: erstelle login.html“.
          </p>
        )}
        {messages.map((m, i) => (
          <Message key={i} msg={m} />
        ))}
        {loading && (
          <div className="flex flex-wrap items-center gap-2 text-sm text-slate-400">
            <span className="italic">Orchestrator denkt…</span>
            {progress?.tools?.length > 0 &&
              progress.tools.map((n, i) => (
                <span
                  key={i}
                  className="rounded bg-slate-200 px-1.5 py-0.5 font-mono text-[10px] text-slate-600 dark:bg-slate-700 dark:text-slate-300"
                >
                  {n}
                </span>
              ))}
            {progress && (
              <button
                onClick={() => cancelChatStream(progress.streamId).catch(() => {})}
                title="Nach dem laufenden Schritt anhalten — bereits ausgeführte Tool-Aufrufe bleiben wirksam"
                className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-500 hover:bg-red-50 hover:text-red-600 dark:border-slate-600 dark:hover:bg-red-950 dark:hover:text-red-400"
              >
                Abbrechen
              </button>
            )}
          </div>
        )}
        {error && (
          <div className="rounded bg-red-100 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
            {error}
          </div>
        )}
        </div>
        {/* bewusst ohne backdrop-blur: der kostet auf Handy-GPUs beim
            Scrollen genau die Frames, um die es hier geht */}
        {zeigeSprung && (
          <button
            onClick={springeAnsEnde}
            title="zum Ende des Verlaufs springen"
            className="absolute bottom-3 right-4 z-10 flex h-9 w-9 items-center justify-center rounded-full bg-slate-700/90 text-lg text-white shadow-lg hover:bg-slate-600 dark:bg-slate-600/90 dark:hover:bg-slate-500"
          >
            ↓
          </button>
        )}
      </div>
      <div className="border-t bg-white p-2 dark:border-slate-700 dark:bg-slate-900">
        {anhaenge.length > 0 && (
          <div className="mb-1.5 flex flex-wrap gap-1">
            {anhaenge.map((f, i) => (
              <span
                key={`${f.name}-${i}`}
                className="flex max-w-[14rem] items-center gap-1 rounded bg-slate-100 px-2 py-0.5 text-xs dark:bg-slate-800"
              >
                <span className="truncate" title={f.name}>
                  {f.type.startsWith("image/") ? "🖼️" : "📄"} {f.name}
                </span>
                <button
                  onClick={() => setAnhaenge((a) => a.filter((_, k) => k !== i))}
                  title="Anhang entfernen"
                  className="shrink-0 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex gap-2">
          <textarea
            ref={inputRef}
            onPaste={onPaste}
            onChange={onEingabe}
            onKeyDown={onKeyDown}
            rows={zeilen}
            // Die Handy-Tastatur zeigt sonst "Senden" und verspricht damit
            // ein Verhalten, das es hier nicht mehr gibt.
            enterKeyHint={grobZeiger ? "enter" : "send"}
            placeholder="Nachricht an den Orchestrator…"
            className="flex-1 resize-none rounded border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:placeholder-slate-500"
          />
          {/* Büroklammer: am Handy bietet der Browser darüber direkt Kamera,
              Fotos und Dateien an — der Weg über Datei-App und Dateibaum
              entfällt (Issue #29). */}
          <label
            title="Datei anhängen"
            className="self-end cursor-pointer rounded border border-slate-300 px-3 py-2 text-sm text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            📎
            <input
              type="file"
              multiple
              accept="image/*,.pdf,.txt,.md,.log,.json,.csv"
              onChange={(e) => {
                anhaengen(e.target.files);
                e.target.value = ""; // dieselbe Datei soll erneut wählbar sein
              }}
              className="hidden"
            />
          </label>
          <button
            onClick={send}
            disabled={loading || (!canSend && anhaenge.length === 0)}
            className="self-end rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
          >
            Senden
          </button>
        </div>
      </div>
    </div>
  );
}
