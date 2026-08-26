import { useEffect, useRef, useState } from "react";
import WorkspaceViews from "./WorkspaceViews";
import {
  clamp,
  defaultRect,
  gueltigesRect,
  normRect,
  standardLayout,
} from "../workspaceLayout";

// Fenster-Manager für den Desktop (md+): jedes Panel ist ein frei
// verschiebbares und größenveränderbares Fenster — Titelleiste ziehen zum
// Verschieben, rechte/untere Kante bzw. Ecke zum Vergrößern. Positionen
// liegen in Prozent der Arbeitsfläche (skalieren also mit dem Browser-
// fenster) und werden in localStorage gemerkt; "workspace:reset" (Knopf in
// der TopBar) stellt die Standard-Anordnung wieder her — die rechnet
// `workspaceLayout.standardLayout` über ALLE vorhandenen Panels aus und ist
// darum immer überschneidungsfrei, auch mit externen Fenstern (Issue #24).
// "workspace:views" öffnet den Dialog für eigene, benannte Anordnungen.
//
// Mobil (< md) bleibt die Tab-Ansicht: genau ein Panel vollflächig. Die
// gleiche Darstellung gibt es am Desktop als wählbaren Tab-Modus
// (viewMode="tabs", Umschalter in der TopBar) — z. B. um zwischen Chat und
// einem externen noVNC-Fenster hin- und herzuschalten. In allen Modi
// bleiben alle Panels dauerhaft gemountet (nur versteckt), damit
// Chat-Zustand und SSH-Sessions Wechsel überleben.

const LS_KEY = "workspace-layout-v1";
const VIEWS_KEY = "workspace-views-v1";

const MIN_W_PX = 240;
const MIN_H_PX = 150;

// Nur Gespeichertes; Lücken füllt der Materialisierungs-Effekt aus der
// Standardanordnung, sobald die Panel-Liste feststeht.
function loadLayout() {
  try {
    const saved = JSON.parse(localStorage.getItem(LS_KEY)) || {};
    const out = {};
    for (const [id, r] of Object.entries(saved)) if (gueltigesRect(r)) out[id] = normRect(r);
    return out;
  } catch {
    return {};
  }
}

// Benannte Ansichten: {name: {layout, order, gespeichert}}. Bewusst im
// localStorage wie die laufende Anordnung auch — eine Ansicht gehört zum
// Bildschirm, vor dem man sitzt.
export function loadViews() {
  try {
    const v = JSON.parse(localStorage.getItem(VIEWS_KEY));
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch {
    return {};
  }
}

function saveViews(views) {
  try {
    localStorage.setItem(VIEWS_KEY, JSON.stringify(views));
  } catch {
    /* voller/gesperrter Storage — Ansichten gelten dann nur für die Sitzung */
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
  const onFocusRef = useRef(onFocusPanel);
  onFocusRef.current = onFocusPanel;

  // Ids, die in DIESER Sitzung schon einmal als Panel da waren. Nur solche
  // dürfen beim Verschwinden aus dem Layout fliegen — beim Start sind die
  // externen Fenster noch nicht geladen (kommen asynchron aus den Settings),
  // ihre gespeicherten Positionen sollen das überleben.
  const gesehenRef = useRef(new Set());

  // Gespeicherte Ansichten (Dialog über den Knopf "Ansichten" in der TopBar)
  const [viewsOffen, setViewsOffen] = useState(false);
  const [views, setViews] = useState(loadViews);
  const [aktiveView, setAktiveView] = useState(null);

  // Panels können sich zur Laufzeit ändern (externe Fenster aus den
  // Settings): neuen Fenstern ein Rechteck geben, gelöschte aus Layout und
  // localStorage entfernen, order nachziehen.
  const idsKey = panels.map((p) => p.id).join("|");
  useEffect(() => {
    setLayout((l) => {
      const out = { ...l };
      let changed = false;
      const alleIds = panelsRef.current.map((p) => p.id);
      const aktuell = new Set(alleIds);
      // Neue Fenster bekommen ihren Platz aus der Standardanordnung ALLER
      // Panels — die vorhandenen bleiben aber, wo der Nutzer sie hingezogen
      // hat. Wer eine saubere Aufteilung will, drückt "Fenster anordnen".
      const std = standardLayout(alleIds);
      for (const id of alleIds) {
        if (!out[id]) {
          out[id] = std[id];
          changed = true;
        }
      }
      // Karteileichen: Fenster, die es gab und die der Nutzer in den
      // Einstellungen gelöscht hat — sonst wächst der localStorage-Eintrag
      // mit jedem je angelegten externen Fenster.
      for (const id of Object.keys(out))
        if (!aktuell.has(id) && gesehenRef.current.has(id)) {
          delete out[id];
          changed = true;
        }
      aktuell.forEach((id) => gesehenRef.current.add(id));
      if (changed) {
        try {
          localStorage.setItem(LS_KEY, JSON.stringify(out));
        } catch {
          /* voller/gesperrter Storage — Layout gilt trotzdem für die Sitzung */
        }
      }
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

  const nachLayoutwechsel = () =>
    // xterm neu fitten, sobald die neuen Maße stehen
    setTimeout(() => window.dispatchEvent(new Event("resize")), 50);

  useEffect(() => {
    const onReset = () => {
      setLayout(standardLayout(panelsRef.current.map((p) => p.id)));
      localStorage.removeItem(LS_KEY);
      nachLayoutwechsel();
    };
    const onViews = () => setViewsOffen(true);
    window.addEventListener("workspace:reset", onReset);
    window.addEventListener("workspace:views", onViews);
    return () => {
      window.removeEventListener("workspace:reset", onReset);
      window.removeEventListener("workspace:views", onViews);
    };
  }, []);

  const raise = (id) =>
    setOrder((o) => (o[o.length - 1] === id ? o : [...o.filter((x) => x !== id), id]));

  // Klick IN ein externes Fenster nach vorn holen (Issue #24, zweiter Befund).
  // `raise` hängt an onPointerDownCapture der <section> — ein iframe stellt im
  // Elterndokument aber keine Pointer-Events zu, also reagierte nur die
  // Titelleiste. Bei noVNC ist der Bildbereich praktisch die ganze Fläche.
  // Der übliche Ausweg: Fokus wandert in den iframe → das Fenster verliert
  // ihn, und document.activeElement zeigt danach auf genau diesen iframe.
  useEffect(() => {
    if (!windowed) return undefined;
    const onBlur = () => {
      // activeElement steht erst nach dem Blur — deshalb ein Tick später.
      setTimeout(() => {
        const el = document.activeElement;
        if (el?.tagName !== "IFRAME") return;
        const id = el.closest("section[data-panel-id]")?.dataset.panelId;
        if (!id) return;
        onFocusRef.current?.(id);
        raise(id);
      }, 0);
    };
    window.addEventListener("blur", onBlur);
    return () => window.removeEventListener("blur", onBlur);
    // `raise` arbeitet nur über setOrder und ist damit stabil; der Callback
    // hängt an einem Ref, damit der Listener nicht bei jedem Render neu bindet.
  }, [windowed]);

  // --- Ansichten ---------------------------------------------------------
  // Gespeichert wird die ganze Anordnung inklusive z-Reihenfolge. Beim Laden
  // zählt nur, was es JETZT an Panels gibt: fehlende Fenster werden
  // übergangen, seither hinzugekommene bekommen ihren Standardplatz — sonst
  // wäre jede Ansicht wertlos, sobald man ein externes Fenster anlegt.
  const ansichtSpeichern = (name) => {
    const sauber = name.trim().slice(0, 40);
    if (!sauber) return;
    const naechste = {
      ...views,
      [sauber]: {
        layout: structuredClone(layoutRef.current),
        order: [...order],
        gespeichert: new Date().toISOString(),
      },
    };
    setViews(naechste);
    saveViews(naechste);
    setAktiveView(sauber);
  };

  const ansichtLaden = (name) => {
    const view = views[name];
    if (!view) return;
    const alleIds = panelsRef.current.map((p) => p.id);
    const std = standardLayout(alleIds);
    const neu = {};
    for (const id of alleIds) {
      const r = view.layout?.[id];
      neu[id] = gueltigesRect(r) ? normRect(r) : std[id];
    }
    setLayout(neu);
    setOrder(() => {
      const bekannt = (view.order || []).filter((id) => alleIds.includes(id));
      return [...bekannt, ...alleIds.filter((id) => !bekannt.includes(id))];
    });
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(neu));
    } catch {
      /* voller/gesperrter Storage — Layout gilt trotzdem für die Sitzung */
    }
    setAktiveView(name);
    setViewsOffen(false);
    nachLayoutwechsel();
  };

  const ansichtLoeschen = (name) => {
    const naechste = { ...views };
    delete naechste[name];
    setViews(naechste);
    saveViews(naechste);
    setAktiveView((a) => (a === name ? null : a));
  };

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
          defaultRect(id, panelsRef.current.map((p) => p.id));
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
    // Pointer festnageln: ohne Capture schluckt ein iframe unter dem Zeiger
    // (noVNC & Co.) die pointermove-Events, sobald die Geste darüber läuft —
    // das Fenster „friert" dann mitten im Ziehen ein. Mit Capture werden die
    // Events weiter am Griff zugestellt (und blubbern bis zum window-Listener).
    const griff = e.currentTarget;
    const pid = e.pointerId;
    try {
      griff?.setPointerCapture?.(pid);
    } catch {
      /* alter Browser / kein Pointer-Event — Fallback bleibt das window-Listening */
    }
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
        griff?.releasePointerCapture?.(pid);
      } catch {
        /* schon freigegeben */
      }
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

  // Fallback für den ersten Render nach einer Panel-Änderung — der
  // Materialisierungs-Effekt läuft erst danach.
  const standard = standardLayout(panels.map((p) => p.id));

  return (
    <div
      ref={containerRef}
      className="relative min-h-0 flex-1 overflow-hidden bg-slate-200/60 dark:bg-slate-950"
    >
      {viewsOffen && (
        <WorkspaceViews
          views={views}
          aktiv={aktiveView}
          onSpeichern={ansichtSpeichern}
          onLaden={ansichtLaden}
          onLoeschen={ansichtLoeschen}
          onClose={() => setViewsOffen(false)}
        />
      )}
      {panels.map(({ id, title, body, bodyClass = "bg-white dark:bg-slate-900" }) => {
        const r = layout[id] || standard[id];
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
            data-panel-id={id}
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
