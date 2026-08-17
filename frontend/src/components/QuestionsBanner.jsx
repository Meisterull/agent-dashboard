import { useEffect, useRef, useState } from "react";
import { getQuestions, answerQuestion, closeQuestion } from "../api";

// Offene Rückfragen (needs_confirm) über alle Agenten. Hier beantwortet der
// Mensch eine Worker-Rückfrage, ohne ins jeweilige Fenster zu wechseln.
//
// Getrennt nach Adressat (Issue #22): oben die Fragen AN DEN MENSCHEN (an den
// orchestrator, `fuer_mensch`), darunter eingeklappt die, die zwei Agenten
// untereinander stellen. Sonst sieht eine Frage von deverp an erp genauso aus
// wie eine Entscheidung, die er zu treffen hat — und er antwortet an Stelle
// eines Agenten, der selbst hätte antworten können.
//
// Antwort-Inputs sind UNcontrolled (refs statt value-Prop): kontrollierte
// Inputs lassen angetippte Wortvorschläge der Handy-Tastatur den Text
// doppelt einfügen; außerdem übersteht der Entwurf so das 5-s-Polling.
export default function QuestionsBanner({ refreshKey, onAnswered, onNew }) {
  const [questions, setQuestions] = useState([]);
  const [offline, setOffline] = useState(false); // letzter Poll fehlgeschlagen
  const [zeigeFremde, setZeigeFremde] = useState(false); // Agent-an-Agent aufgeklappt?
  const inputsRef = useRef({}); // q.id -> <input>-Element
  const prevIdsRef = useRef(null); // erste Antwort = Basis, kein Blinken beim Laden
  const onNewRef = useRef(onNew);
  onNewRef.current = onNew;

  function load() {
    getQuestions()
      .then((d) => {
        setOffline(false);
        setQuestions(d.questions);
        // Blinken nur für das, was WIRKLICH auf den Menschen wartet — sonst
        // ruft jede Frage zwischen zwei Agenten nach Aufmerksamkeit.
        const ids = d.questions
          .filter((q) => q.fuer_mensch)
          .map((q) => `${q.agent}/${q.id}`);
        if (prevIdsRef.current && ids.some((id) => !prevIdsRef.current.includes(id)))
          onNewRef.current?.();
        prevIdsRef.current = ids;
      })
      // Fragen STEHEN lassen: ein fehlgeschlagener Poll würde das Banner
      // sonst ausblenden — samt der halb getippten Antwort-Entwürfe in den
      // (uncontrolled) Inputs, die mit dem DOM verschwinden.
      .catch(() => setOffline(true));
  }

  useEffect(() => {
    load();
    // Rückfragen tauchen auch ohne Aktion auf — aber nicht im Hintergrund-Tab
    // pollen; beim Zurückkommen dafür sofort einmal laden.
    const t = setInterval(() => {
      if (!document.hidden) load();
    }, 5000);
    const onVisible = () => {
      if (!document.hidden) load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", onVisible);
    };
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

  async function schliessen(q) {
    // Sicherheitsabfrage: das Schließen lässt den wartenden Task scheitern.
    const grund = window.prompt(
      `Rückfrage von ${q.sender} ohne Antwort schließen?\n\n` +
        `Ein Task, der nur auf sie wartet, scheitert dabei mit Klartext und ` +
        `liegt danach wiederanlauffähig in .failed/.\n\nGrund (optional):`,
      "",
    );
    if (grund === null) return; // Abbruch
    try {
      await closeQuestion(q.agent, q.id, grund);
      load();
      onAnswered?.();
    } catch {
      /* still im Banner, nächster Versuch */
    }
  }

  // Bewusst eine Render-FUNKTION, keine verschachtelte Komponente: ein in
  // QuestionsBanner definiertes <Frage/> wäre bei jedem Render ein neuer
  // Komponententyp, React würde den Teilbaum neu aufbauen — und mit dem alten
  // DOM-Knoten verschwände der halb getippte Antwort-Entwurf im uncontrolled
  // Input. Genau alle 5 s, mit jedem Poll.
  function frageZeile(q) {
    return (
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
          <button
            onClick={() => schliessen(q)}
            title="Ohne Antwort schließen — der wartende Task scheitert mit Klartext"
            className="rounded border border-amber-300 px-2 py-1 text-sm text-amber-700 hover:bg-amber-100 dark:border-amber-800 dark:text-amber-400 dark:hover:bg-amber-900"
          >
            ✕
          </button>
        </div>
      </div>
    );
  }

  const meine = questions.filter((q) => q.fuer_mensch);
  const fremde = questions.filter((q) => !q.fuer_mensch);
  if (questions.length === 0) return null;

  return (
    <div className="border-b border-amber-300 bg-amber-50 px-4 py-2 dark:border-amber-800 dark:bg-amber-950">
      <div className="mb-1 text-xs font-semibold text-amber-700 dark:text-amber-400">
        {meine.length > 0 ? `Offene Rückfragen (${meine.length})` : "Keine Rückfrage an dich"}
        {offline && (
          <span
            title="Letzte Aktualisierung fehlgeschlagen — angezeigt wird der letzte bekannte Stand."
            className="ml-2 font-normal text-amber-600 dark:text-amber-500"
          >
            · Verbindung gestört
          </span>
        )}
      </div>
      <div className="space-y-2">{meine.map(frageZeile)}</div>

      {fremde.length > 0 && (
        <div className={meine.length > 0 ? "mt-2 border-t border-amber-200 pt-2 dark:border-amber-900" : ""}>
          <button
            onClick={() => setZeigeFremde((v) => !v)}
            title="Fragen, die Agenten untereinander stellen — beantworten kann sie auch der gefragte Agent selbst"
            className="text-xs text-amber-700/80 hover:underline dark:text-amber-400/80"
          >
            {zeigeFremde ? "▾" : "▸"} {fremde.length} Rückfrage
            {fremde.length === 1 ? "" : "n"} zwischen Agenten
          </button>
          {zeigeFremde && (
            <div className="mt-2 space-y-2 opacity-80">{fremde.map(frageZeile)}</div>
          )}
        </div>
      )}
    </div>
  );
}
