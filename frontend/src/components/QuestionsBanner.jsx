import { useEffect, useRef, useState } from "react";
import { getQuestions, answerQuestion } from "../api";

// Offene Rückfragen (needs_confirm) über alle Agenten. Hier beantwortet der
// Mensch eine Worker-Rückfrage, ohne ins jeweilige Fenster zu wechseln.
// Antwort-Inputs sind UNcontrolled (refs statt value-Prop): kontrollierte
// Inputs lassen angetippte Wortvorschläge der Handy-Tastatur den Text
// doppelt einfügen; außerdem übersteht der Entwurf so das 5-s-Polling.
export default function QuestionsBanner({ refreshKey, onAnswered, onNew }) {
  const [questions, setQuestions] = useState([]);
  const inputsRef = useRef({}); // q.id -> <input>-Element
  const prevIdsRef = useRef(null); // erste Antwort = Basis, kein Blinken beim Laden
  const onNewRef = useRef(onNew);
  onNewRef.current = onNew;

  function load() {
    getQuestions()
      .then((d) => {
        setQuestions(d.questions);
        const ids = d.questions.map((q) => `${q.agent}/${q.id}`);
        // neue Rückfrage aufgetaucht → Agenten-Reiter blinken lassen
        if (prevIdsRef.current && ids.some((id) => !prevIdsRef.current.includes(id)))
          onNewRef.current?.();
        prevIdsRef.current = ids;
      })
      .catch(() => setQuestions([]));
  }

  useEffect(() => {
    load();
    const t = setInterval(load, 5000); // Rückfragen tauchen auch ohne Aktion auf
    return () => clearInterval(t);
  }, [refreshKey]);

  async function submit(q) {
    const el = inputsRef.current[q.id];
    const text = (el?.value || "").trim();
    if (!text) return;
    try {
      await answerQuestion(q.agent, q.id, text);
      if (el) el.value = "";
      load();
      onAnswered?.();
    } catch {
      /* still im Banner, nächster Versuch */
    }
  }

  if (questions.length === 0) return null;

  return (
    <div className="border-b border-amber-300 bg-amber-50 px-4 py-2 dark:border-amber-800 dark:bg-amber-950">
      <div className="mb-1 text-xs font-semibold text-amber-700 dark:text-amber-400">
        Offene Rückfragen ({questions.length})
      </div>
      <div className="space-y-2">
        {questions.map((q) => (
          <div key={`${q.agent}/${q.id}`} className="text-sm">
            <div className="text-slate-700 dark:text-slate-200">
              <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{q.sender}</span>
              {" fragt "}
              <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{q.agent}</span>: {q.text}
            </div>
            <div className="mt-1 flex gap-2">
              <input
                ref={(el) => {
                  if (el) inputsRef.current[q.id] = el;
                  else delete inputsRef.current[q.id];
                }}
                onKeyDown={(e) => e.key === "Enter" && submit(q)}
                placeholder="Antwort…"
                className="flex-1 rounded border border-amber-300 px-2 py-1 text-sm focus:outline-none dark:border-amber-800 dark:bg-slate-900 dark:text-slate-100"
              />
              <button
                onClick={() => submit(q)}
                className="rounded bg-amber-600 px-3 py-1 text-sm font-medium text-white hover:bg-amber-700"
              >
                Antworten
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
