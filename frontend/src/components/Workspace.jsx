import { useEffect, useRef, useState } from "react";

// Fenster-Manager für den Desktop (md+): jedes Panel ist ein frei
// verschiebbares und größenveränderbares Fenster — Titelleiste ziehen zum
// Verschieben, rechte/untere Kante bzw. Ecke zum Vergrößern. Positionen
// liegen in Prozent der Arbeitsfläche (skalieren also mit dem Browser-
// fenster) und werden in localStorage gemerkt; "workspace:reset" (Knopf in
// der TopBar) stellt die Standard-Anordnung wieder her.
//
// Mobil (< md) bleibt die Tab-Ansicht: genau ein Panel vollflächig. Die
// gleiche Darstellung gibt es am Desktop als wählbaren Tab-Modus
// (viewMode="tabs", Umschalter in der TopBar) — z. B. um zwischen Chat und
// einem externen noVNC-Fenster hin- und herzuschalten. In allen Modi
// bleiben alle Panels dauerhaft gemountet (nur versteckt), damit
// Chat-Zustand und SSH-Sessions Wechsel überleben.

const LS_KEY = "workspace-layout-v1";

const DEFAULT_LAYOUT = {
  dateien: { x: 0.4, y: 0.8, w: 18, h: 98.4 },
  chat: { x: 19, y: 0.8, w: 52.4, h: 62 },
  terminal: { x: 19, y: 63.6, w: 52.4, h: 35.6 },
  agenten: { x: 72, y: 0.8, w: 27.6, h: 98.4 },
};

const MIN_W_PX = 240;
const MIN_H_PX = 150;

const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

// Standard-Rechteck eines Panels: feste Plätze für die vier Kern-Panels,
// dynamische (z. B. externe noVNC-Fenster) kaskadieren über der Mitte.
function defaultRect(id, index = 0) {
  if (DEFAULT_LAYOUT[id]) return { ...DEFAULT_LAYOUT[id] };
  return {
    x: clamp(14 + 4 * index, 0, 30),
    y: clamp(4 + 5 * index, 0, 30),
    w: 60,
    h: 70,
  };
}

function loadLayout() {
  try {
    const saved = JSON.parse(localStorage.getItem(LS_KEY));
    const out = {};
    // Defaults + alles Gespeicherte (auch Fenster, die es nur dynamisch gibt)
    for (const id of new Set([...Object.keys(DEFAULT_LAYOUT), ...Object.keys(saved || {})])) {
      const r = saved?.[id];
      if (r && [r.x, r.y, r.w, r.h].every((n) => Number.isFinite(n))) {
        out[id] = { x: clamp(r.x, 0, 98), y: clamp(r.y, 0, 98), w: clamp(r.w, 2, 100), h: clamp(r.h, 2, 100) };
      } else if (DEFAULT_LAYOUT[id]) {
        out[id] = { ...DEFAULT_LAYOUT[id] };
      }
    }
    return out;
  } catch {
    return structuredClone(DEFAULT_LAYOUT);
  }
}

export default function Workspace({
  tab,
  viewMode = "windows",
  panels,
  attention = {},
  onFocusPanel,
}) {
  const containerRef = useRef(null);
  const [layout, setLayout] = useState(loadLayout);
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  // z-Reihenfolge: letztes Element liegt oben; DOM-Reihenfolge bleibt
  // stabil (nur zIndex ändert sich), damit nichts remountet.
  const [order, setOrder] = useState(() => panels.map((p) => p.id));
  const panelsRef = useRef(panels);
  panelsRef.current = panels;

  // Panels können sich zur Laufzeit ändern (externe Fenster aus den
  // Settings): neuen Fenstern ein Rechteck geben, order nachziehen.
  const idsKey = panels.map((p) => p.id).join("|");
  useEffect(() => {
    setLayout((l) => {
      const out = { ...l };
      let changed = false;
      panelsRef.current.forEach((p, i) => {
        if (!out[p.id]) {
          out[p.id] = defaultRect(p.id, i);
          changed = true;
        }
      });
      return changed ? out : l;
    });
    setOrder((o) => {
      const cur = panelsRef.current.map((p) => p.id);
      const kept = o.filter((id) => cur.includes(id));
      const added = cur.filter((id) => !kept.includes(id));
      return kept.length === o.length && !added.length ? o : [...kept, ...added];
    });
  }, [idsKey]);

  const [isDesktop, setIsDesktop] = useState(
    () => window.matchMedia("(min-width: 768px)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    const on = (e) => setIsDesktop(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  // Fenster-Manager nur am Desktop im Fenster-Modus; sonst (mobil oder
  // Tab-Modus) genau ein Panel vollflächig über `tab`.
  const windowed = isDesktop && viewMode === "windows";

  useEffect(() => {
    const onReset = () => {
      const fresh = {};
      panelsRef.current.forEach((p, i) => {
        fresh[p.id] = defaultRect(p.id, i);
      });
      setLayout(fresh);
      localStorage.removeItem(LS_KEY);
      // xterm neu fitten, sobald die neuen Maße stehen
      setTimeout(() => window.dispatchEvent(new Event("resize")), 50);
    };
    window.addEventListener("workspace:reset", onReset);
    return () => window.removeEventListener("workspace:reset", onReset);
  }, []);

  const raise = (id) =>
    setOrder((o) => (o[o.length - 1] === id ? o : [...o.filter((x) => x !== id), id]));

  // Doppelklick auf die Titelleiste: maximieren bzw. auf die vorige
  // Größe zurück — zum schnellen Umschalten zwischen den Fenstern.
  const prevRects = useRef({});
  const toggleMax = (id) => {
    setLayout((l) => {
      const r = l[id];
      const isMax = r && r.x === 0 && r.y === 0 && r.w === 100 && r.h === 100;
      let next;
      if (isMax) {
        next =
          prevRects.current[id] ||
          defaultRect(id, panelsRef.current.findIndex((p) => p.id === id));
      } else {
        prevRects.current[id] = r;
        next = { x: 0, y: 0, w: 100, h: 100 };
      }
      const out = { ...l, [id]: next };
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(out));
      } catch {
        /* voller/gesperrter Storage — Layout gilt trotzdem für die Sitzung */
      }
      return out;
    });
    raise(id);
    setTimeout(() => window.dispatchEvent(new Event("resize")), 50);
  };

  // Während des Ziehens gedrosselt "resize" feuern, damit xterm live
  // mitfittet; am Ende einmal ungedrosselt + Layout speichern.
  const lastFitRef = useRef(0);
  const fitThrottled = () => {
    const now = performance.now();
    if (now - lastFitRef.current > 150) {
      lastFitRef.current = now;
      window.dispatchEvent(new Event("resize"));
    }
  };

  function startGesture(e, id, mode) {
    // mode: "move" | "e" | "s" | "se"
    if (e.button !== undefined && e.button !== 0) return;
    e.preventDefault();
    raise(id);
    const cont = containerRef.current.getBoundingClientRect();
    if (!cont.width || !cont.height) return;
    const start = { px: e.clientX, py: e.clientY, ...layoutRef.current[id] };
    const minW = (MIN_W_PX / cont.width) * 100;
    const minH = (MIN_H_PX / cont.height) * 100;

    const onMove = (ev) => {
      const dx = ((ev.clientX - start.px) / cont.width) * 100;
      const dy = ((ev.clientY - start.py) / cont.height) * 100;
      setLayout((l) => {
        const r = { ...l[id] };
        if (mode === "move") {
          r.x = clamp(start.x + dx, 0, Math.max(0, 100 - r.w));
          r.y = clamp(start.y + dy, 0, Math.max(0, 100 - r.h));
        } else {
          if (mode.includes("e")) r.w = clamp(start.w + dx, minW, 100 - r.x);
          if (mode.includes("s")) r.h = clamp(start.h + dy, minH, 100 - r.y);
        }
        return { ...l, [id]: r };
      });
      if (mode !== "move") fitThrottled();
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(layoutRef.current));
      } catch {
        /* voller/gesperrter Storage — Layout gilt trotzdem für die Sitzung */
      }
      window.dispatchEvent(new Event("resize"));
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }

  return (
    <div
      ref={containerRef}
      className="relative min-h-0 flex-1 overflow-hidden bg-slate-200/60 dark:bg-slate-950"
    >
      {panels.map(({ id, title, body, bodyClass = "bg-white dark:bg-slate-900" }, index) => {
        // Fallback für den ersten Render nach einer Panel-Änderung — der
        // Materialisierungs-Effekt läuft erst danach.
        const r = layout[id] || defaultRect(id, index);
        const style = windowed
          ? {
              left: `${r.x}%`,
              top: `${r.y}%`,
              width: `${r.w}%`,
              height: `${r.h}%`,
              zIndex: 10 + order.indexOf(id),
              minWidth: `${MIN_W_PX}px`,
              minHeight: `${MIN_H_PX}px`,
            }
          : undefined;
        const cls = windowed
          ? "absolute flex flex-col overflow-hidden rounded-lg border border-slate-300 shadow-lg dark:border-slate-700"
          : tab === id
            ? "absolute inset-0 flex flex-col"
            : "hidden";
        return (
          <section
            key={id}
            style={style}
            className={cls}
            onPointerDownCapture={() => {
              onFocusPanel?.(id); // Blinken löschen: Nutzer hat es gesehen
              if (windowed) raise(id);
            }}
          >
            {windowed && (
              <header
                onPointerDown={(e) => startGesture(e, id, "move")}
                onDoubleClick={() => toggleMax(id)}
                title="Ziehen: verschieben · Doppelklick: maximieren"
                className="flex shrink-0 cursor-move touch-none select-none items-center border-b bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400"
              >
                <span className={attention[id] ? "attention-blink" : ""}>{title}</span>
              </header>
            )}
            <div className={`flex min-h-0 flex-1 flex-col ${bodyClass}`}>{body}</div>
            {windowed && (
              <>
                <div
                  onPointerDown={(e) => startGesture(e, id, "e")}
                  className="absolute right-0 top-0 z-20 h-full w-1.5 cursor-e-resize touch-none"
                />
                <div
                  onPointerDown={(e) => startGesture(e, id, "s")}
                  className="absolute bottom-0 left-0 z-20 h-1.5 w-full cursor-s-resize touch-none"
                />
                <div
                  onPointerDown={(e) => startGesture(e, id, "se")}
                  className="absolute bottom-0 right-0 z-30 h-4 w-4 cursor-se-resize touch-none"
                >
                  <svg viewBox="0 0 16 16" className="h-full w-full text-slate-400 dark:text-slate-500">
                    <path d="M14 8v6H8m6-11v2M5 14h2" stroke="currentColor" strokeWidth="1.5" fill="none" />
                  </svg>
                </div>
              </>
            )}
          </section>
        );
      })}
    </div>
  );
}
