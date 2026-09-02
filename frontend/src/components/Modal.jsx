import { useEffect, useRef } from "react";

// Schließen per Backdrop nur, wenn Drücken UND Loslassen auf dem Backdrop
// passieren: sonst schließt ein Klick, der in einem Eingabefeld beginnt und
// beim Markieren über den Rand hinaus endet, den Dialog samt Eingaben.
// Escape schließt zusätzlich (erwartet man bei jedem Dialog).

// Escape darf nur den OBERSTEN Dialog schließen (Review P2): jede Instanz
// hängt ihren eigenen keydown-Listener an — ohne diesen Stapel schloss EIN
// Escape den Bestätigungsdialog UND den Dialog darunter, samt Eingaben.
const offeneDialoge = [];

export default function Modal({ title, onClose, children, wide }) {
  const downAufBackdrop = useRef(false);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const eintrag = Symbol("modal");
    offeneDialoge.push(eintrag);
    const onKey = (e) => {
      if (
        e.key === "Escape" &&
        offeneDialoge[offeneDialoge.length - 1] === eintrag
      )
        onCloseRef.current?.();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      const i = offeneDialoge.indexOf(eintrag);
      if (i >= 0) offeneDialoge.splice(i, 1);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onPointerDown={(e) => {
        downAufBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && downAufBackdrop.current) onClose();
        downAufBackdrop.current = false;
      }}
    >
      <div
        className={`flex max-h-[85vh] w-full flex-col rounded-lg bg-white shadow-xl dark:bg-slate-900 dark:text-slate-100 ${
          wide ? "max-w-3xl" : "max-w-md"
        }`}
      >
        <div className="flex items-center justify-between border-b px-4 py-2.5 dark:border-slate-700">
          <h2 className="text-sm font-semibold">{title}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700 dark:hover:text-slate-200">
            ✕
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-4">{children}</div>
      </div>
    </div>
  );
}
