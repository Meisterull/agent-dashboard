import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { postChat, getChatSessions, getChatHistory } from "../api";

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

export default function Chat({ sessionId, setSessionId, onActivity }) {
  const [messages, setMessages] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);

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
        setMessages(d.messages);
        setSessionId(id);
        localStorage.setItem("chat-session", id);
      })
      .catch(() => {
        localStorage.removeItem("chat-session");
      });
  }

  function newSession() {
    setSessionId(null);
    setMessages([]);
    setError(null);
    localStorage.removeItem("chat-session");
  }

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    setError(null);
    setInput("");
    setMessages((m) => [...m, { role: "user", text }]);
    setLoading(true);
    try {
      const data = await postChat(text, sessionId);
      setSessionId(data.session_id);
      localStorage.setItem("chat-session", data.session_id);
      setMessages((m) => [
        ...m,
        { role: "assistant", text: data.reply, toolCalls: data.tool_calls },
      ]);
      refreshSessions();
      onActivity?.(); // Mailboxes / Dateibaum können sich geändert haben
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
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
          <div className="text-sm italic text-slate-400">Orchestrator denkt…</div>
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
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            placeholder="Nachricht an den Orchestrator…"
            className="flex-1 resize-none rounded border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:placeholder-slate-500"
          />
          <button
            onClick={send}
            disabled={loading || !input.trim()}
            className="self-end rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
          >
            Senden
          </button>
        </div>
      </div>
    </div>
  );
}
