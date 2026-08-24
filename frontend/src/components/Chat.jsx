import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
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
function Message({ msg }) {
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
}

export default function Chat({ sessionId, setSessionId, onActivity, onDone }) {
  const [messages, setMessages] = useState([]);
  const [sessions, setSessions] = useState([]);
  // Eingabe bewusst UNcontrolled (ref statt value-Prop): kontrollierte
  // Inputs bringen die Wortvorschläge der Handy-Tastatur (GBoard/Samsung)
  // aus dem Takt — ein angetippter Vorschlag fügt dann den ganzen Text
  // doppelt ein. canSend spiegelt nur den Leer-Zustand für den Button.
  const inputRef = useRef(null);
  const [canSend, setCanSend] = useState(false);
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

  useEffect(() => {
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

  async function send() {
    const text = (inputRef.current?.value || "").trim();
    if (!text || loading) return;
    setError(null);
    if (inputRef.current) inputRef.current.value = "";
    setCanSend(false);
    setMessages((m) => [...m, { role: "user", text }]);
    setLoading(true);
    const view = viewRef.current;
    try {
      // Streaming (F3): Tool-Calls kommen live als Events — sichtbar, was der
      // Orchestrator gerade tut, plus Abbrechen-Knopf statt Blackbox-POST.
      const data = await streamChat(text, sessionId, {
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

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
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
      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-3">
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
      <div className="border-t bg-white p-2 dark:border-slate-700 dark:bg-slate-900">
        <div className="flex gap-2">
          <textarea
            ref={inputRef}
            onChange={(e) => setCanSend(e.target.value.trim().length > 0)}
            onKeyDown={onKeyDown}
            rows={2}
            placeholder="Nachricht an den Orchestrator…"
            className="flex-1 resize-none rounded border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:placeholder-slate-500"
          />
          <button
            onClick={send}
            disabled={loading || !canSend}
            className="self-end rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
          >
            Senden
          </button>
        </div>
      </div>
    </div>
  );
}
