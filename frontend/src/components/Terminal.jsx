import { useEffect, useRef, useState } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import KeyBar from "./KeyBar";
import { encodeKey, encodeChar } from "../keys";
import { getSshBuffer } from "../api";

// Ein SSH-Terminal. Spricht /ws/ssh/<name>?sid=… — die sid identifiziert
// eine serverseitig persistente Session: bricht der WebSocket ab (Handy
// gesperrt, Netzwechsel) oder wird das Fenster geschlossen, läuft die Shell
// serverseitig weiter; beim nächsten Öffnen wird der gepufferte Output
// nachgespielt. Pro Verbindung sind mehrere Terminals möglich; die sids sind
// bewusst stabil ("main", "2", "3", … — nicht pro Browser vergeben): so hängt
// sich auch ein anderer PC/Browser an dieselbe Session — der bisherige
// Client wird per Close-Code 4000 übernommen.
// Explizit beendet wird die Session vom TerminalPanel (DELETE-Endpoint);
// endet die Shell (exit/kill), meldet das Terminal das über onEnded.
//
// Für Mobilgeräte gibt es eine KeyBar mit Sondertasten und Sticky-Modifikatoren
// (Strg/Alt/Shift): ein aktiver Modifikator wirkt auf die nächste Taste — egal
// ob aus der Leiste oder von der Bildschirmtastatur — und schaltet sich danach
// selbst ab.
//
// Kopieren (Issue #4 + #6 + #8 + #10): Auswahl landet beim Loslassen sofort in
// der Zwischenablage (mouseup auf document, gegated über mousedown im Terminal);
// Strg+C mit Auswahl kopiert statt SIGINT. Fängt eine TUI die Maus ab
// (Claude Code, ?1000h), zeigt ein Badge den Ausweg (Shift+Ziehen, Mac: ⌥)
// und der ⎘-Kopier-Modus in der KeyBar (Umschalter mit Aktiv-Zustand) bietet
// den Puffer als frei markierbaren Text an — der einzige Weg auf Touch-Geräten.
// Im Alt-Screen kommt buffer.normal mit dazu; den vollen Sitzungsverlauf
// liefert der serverseitige Replay-Puffer (GET /api/ssh/{name}/buffer).

export const DEFAULT_SID = "main";

// Kopieren in die Zwischenablage, mit Fallback ohne Clipboard-API (Zugriff
// per IP/HTTP oder https mit ungültigem Zertifikat — Chrome sperrt "powerful
// features" dann). Das ta.focus() ist zwingend: ohne Fokus kopiert execCommand
// die Auswahl des Dokuments, nicht die des Textfelds — und die xterm-Auswahl
// ist gemalt, keine DOM-Selection, also landet sonst nichts in der
// Zwischenablage. `term` optional: wenn gesetzt, geht die Tastatur danach
// zurück ans Terminal.
function copyFallback(text, term) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand("copy");
  } catch {
    /* mehr als Fallback haben wir nicht */
  }
  ta.remove();
  term?.focus();
}

// writeText kann trotz vorhandener API scheitern (Fokus, Berechtigung,
// unsicherer Kontext) — die Ablehnung muss in den Fallback führen,
// sonst schluckt der catch den Fehler und es wird gar nichts kopiert.
function copyText(text, term) {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).catch(() => copyFallback(text, term));
  } else {
    copyFallback(text, term);
  }
}

export default function Terminal({ name, sid = DEFAULT_SID, visible = true, onEnded }) {
  const ref = useRef(null);
  const fitRef = useRef(null);
  const wsRef = useRef(null);
  const termRef = useRef(null); // xterm-Instanz, für Kopier-Modus außerhalb des Effects
  // Zeigt eine TUI-App (z.B. Claude Code) Maus-Interesse an (?1000h & Co.),
  // deaktiviert xterm.js die eigene Auswahl komplett — dann hilft nur
  // Shift+Ziehen oder der Kopier-Modus. Der Zustand steuert den Hinweis-Badge.
  const [mouseCaptured, setMouseCaptured] = useState(false);
  const [copyView, setCopyView] = useState(null); // {text} | null = Kopier-Modus
  const [copied, setCopied] = useState(false);
  const [mods, setMods] = useState({ ctrl: false, alt: false, shift: false });
  const modsRef = useRef(mods);
  modsRef.current = mods;
  // Callback in einer Ref, damit ein neuer onEnded pro Re-Render nicht den
  // Verbindungs-Effect (und damit den WebSocket) neu aufbaut.
  const onEndedRef = useRef(onEnded);
  onEndedRef.current = onEnded;

  const clearMods = () => setMods({ ctrl: false, alt: false, shift: false });
  const toggleMod = (name) => setMods((m) => ({ ...m, [name]: !m[name] }));

  const sendRaw = (data) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN)
      ws.send(JSON.stringify({ type: "data", data }));
  };

  const handleKey = (key, fixed = {}) => {
    sendRaw(encodeKey(key, { ...modsRef.current, ...fixed }));
    clearMods();
  };

  useEffect(() => {
    if (visible && fitRef.current) fitRef.current();
  }, [visible]);

  useEffect(() => {
    const term = new XTerm({
      fontSize: 13,
      theme: { background: "#1e293b" },
      cursorBlink: true,
      // Auf dem Mac erzwingt ⌥+Ziehen die Auswahl, wenn eine TUI die Maus
      // beansprucht (Pendant zu Shift+Ziehen anderswo) — ohne diese Option
      // gäbe es dort während Claude Code KEINEN Weg zu markieren (Issue #6).
      macOptionClickForcesSelection: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(ref.current);
    fit.fit();
    termRef.current = term;

    // Hinweis-Badge: schaltet eine TUI Maus-Reporting ein (?1000h/?1006h),
    // deaktiviert xterm.js seinen Selection-Service und setzt die Klasse
    // enable-mouse-events aufs Element — das ist unser (einziges öffentliches)
    // Signal. MutationObserver statt Polling.
    const readMouseCapture = () =>
      setMouseCaptured(term.element?.classList.contains("enable-mouse-events") ?? false);
    const mouseObs = new MutationObserver(readMouseCapture);
    if (term.element)
      mouseObs.observe(term.element, { attributes: true, attributeFilter: ["class"] });
    readMouseCapture();

    // Kopieren: Strg+C mit aktiver Auswahl kopiert in die Zwischenablage
    // (statt SIGINT an die Shell); ohne Auswahl bleibt Strg+C das gewohnte
    // Abbrechen. Deckt auch Strg+Shift+C (sonst DevTools) und Strg+Einfg ab.
    const copySelection = () => {
      const text = term.getSelection();
      if (!text) return false;
      copyText(text, term);
      term.clearSelection();
      return true;
    };

    // Kopieren-bei-Auswahl (PuTTY-Stil): beim Loslassen der Maus landet die
    // Auswahl sofort in der Zwischenablage. Nötig für TUI-Apps wie Claude
    // Code: deren Alt-Screen-Wechsel löscht die xterm-Auswahl, und Ink-
    // Redraws schieben den Inhalt unter den Koordinaten weg — bis zum Strg+C
    // überlebt die Auswahl dann oft nicht. Der Schnappschuss beim Loslassen
    // umgeht dieses Zeitfenster komplett. (Erzwingt die App Mouse-Tracking,
    // entsteht die Auswahl per Shift+Ziehen — auch die landet hier.)
    //
    // mouseup hängt an DOCUMENT, nicht am Terminal-Container: xterm macht das
    // genauso — beim Markieren großer Blöcke endet das Ziehen regelmäßig
    // außerhalb des Terminals, und ein Container-Listener feuert dann nicht
    // (Issue #6, Nebenbefund). Gegated über mousedown IM Terminal, damit
    // fremde Klicks anderswo nie eine liegengebliebene Auswahl erneut
    // kopieren (und bei mehreren gemounteten Terminals nur das richtige
    // reagiert).
    let dragFromTerm = false;
    const onTermMouseDown = (e) => {
      if (e.button === 0) dragFromTerm = true;
    };
    const handleMouseUp = () => {
      if (!dragFromTerm) return;
      dragFromTerm = false;
      const text = term.getSelection();
      if (text) copyText(text, term);
    };
    const termEl = ref.current;
    termEl.addEventListener("mousedown", onTermMouseDown);
    document.addEventListener("mouseup", handleMouseUp);
    term.attachCustomKeyEventHandler((e) => {
      if (
        e.type === "keydown" &&
        e.ctrlKey &&
        !e.altKey &&
        (e.key?.toLowerCase() === "c" || e.key === "Insert") &&
        copySelection()
      ) {
        e.preventDefault();
        return false;
      }
      return true;
    });

    let gone = false; // Komponente weg oder Session endgültig zu
    let retry = 0;
    let retryTimer = null;

    const sendResize = (ws) => {
      if (ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    };

    const connect = () => {
      if (gone) return;
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(
        `${proto}://${window.location.host}/ws/ssh/${encodeURIComponent(name)}?sid=${sid}`,
      );
      wsRef.current = ws;

      ws.onopen = () => {
        retry = 0;
        sendResize(ws);
      };
      ws.onmessage = (ev) => term.write(ev.data);
      ws.onclose = (ev) => {
        if (gone || wsRef.current !== ws) return;
        if (ev.code === 4401) {
          // nicht angemeldet → Login-Screen statt Reconnect-Schleife
          window.dispatchEvent(new CustomEvent("auth:required"));
          return;
        }
        if (ev.code === 4404) {
          // Shell beendet / Session gekillt — Panel räumt den Tab auf
          gone = true;
          onEndedRef.current?.();
          return;
        }
        if (ev.code === 4000) {
          term.write("\r\n[Sitzung in anderem Fenster übernommen]\r\n");
          return; // kein Reconnect — sonst klauen sich zwei Tabs die Session
        }
        // Netz weg / Handy gesperrt → automatisch neu verbinden
        const delay = Math.min(1000 * 2 ** retry, 10000);
        retry += 1;
        term.write(`\r\n[getrennt — neuer Versuch in ${Math.round(delay / 1000)}s]\r\n`);
        retryTimer = setTimeout(connect, delay);
      };
    };
    connect();

    term.onData((data) => {
      let out = data;
      const m = modsRef.current;
      if ((m.ctrl || m.alt || m.shift) && data.length === 1) {
        out = encodeChar(data, m);
        setMods({ ctrl: false, alt: false, shift: false });
      }
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: "data", data: out }));
    });

    // Zurück in die App (Handy entsperrt): sofort neu verbinden statt warten
    const onVisible = () => {
      if (
        !document.hidden &&
        !gone &&
        (!wsRef.current || wsRef.current.readyState === WebSocket.CLOSED)
      ) {
        clearTimeout(retryTimer);
        retry = 0;
        connect();
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    const onResize = () => {
      fit.fit();
      if (wsRef.current) sendResize(wsRef.current);
    };
    window.addEventListener("resize", onResize);
    fitRef.current = onResize;

    return () => {
      gone = true;
      clearTimeout(retryTimer);
      fitRef.current = null;
      termRef.current = null;
      setMouseCaptured(false);
      mouseObs.disconnect();
      termEl.removeEventListener("mousedown", onTermMouseDown);
      document.removeEventListener("mouseup", handleMouseUp);
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisible);
      try {
        wsRef.current?.close();
      } catch {
        /* egal */
      }
      wsRef.current = null;
      term.dispose();
    };
  }, [name, sid]);

  // Kopier-Modus (Issue #6): der Puffer (inkl. Scrollback) als ganz normal
  // markierbarer Text in einem Overlay — umgeht die xterm-Auswahl komplett
  // und funktioniert damit unabhängig davon, was die laufende TUI mit der
  // Maus macht. Auf dem Handy (kein Shift+Ziehen möglich) der einzige Weg.
  // Schnappschuss beim Öffnen; umbrochene Zeilen werden zu logischen Zeilen
  // zusammengesetzt (nur echte Zeilenenden werden getrimmt, sonst gingen
  // Leerzeichen an Umbruchgrenzen verloren).
  const bufferToText = (buf) => {
    const parts = [];
    for (let i = 0; i < buf.length; i++) {
      const line = buf.getLine(i);
      if (!line) continue;
      const continued = i + 1 < buf.length && buf.getLine(i + 1)?.isWrapped;
      const s = line.translateToString(!continued);
      if (line.isWrapped && parts.length) parts[parts.length - 1] += s;
      else parts.push(s);
    }
    return parts.join("\n").replace(/\s+$/, "");
  };

  const openCopyMode = () => {
    const term = termRef.current;
    if (!term) return;
    // Im Alt-Screen (TUI wie Claude Code) hat der aktive Puffer KEINEN
    // Scrollback — was vor dem TUI-Start im Terminal stand, liegt aber noch
    // in buffer.normal und wird vorangestellt (Issue #8). Den Verlauf
    // innerhalb der TUI-Sitzung liefert nur der Server-Puffer (Knopf
    // "Voller Verlauf"), das Overlay sagt das ehrlich dazu.
    const alt = term.buffer.active.type === "alternate";
    const text = alt
      ? [bufferToText(term.buffer.normal), bufferToText(term.buffer.active)]
          .filter(Boolean)
          .join("\n")
      : bufferToText(term.buffer.active);
    setCopied(false);
    setCopyView({ text, alt, full: false });
  };

  // Serverseitigen Replay-Puffer laden — der echte Sitzungsverlauf, vom
  // Alt-Screen unberührt (ANSI-bereinigt vom Backend).
  const loadFullBuffer = async () => {
    try {
      const data = await getSshBuffer(name, sid);
      setCopied(false);
      setCopyView((v) => v && { ...v, text: data.text, full: true });
    } catch {
      /* Session weg oder Netzfehler — der Schnappschuss bleibt stehen */
    }
  };

  const refreshCopyMode = () => {
    if (copyView?.full) loadFullBuffer();
    else openCopyMode();
  };

  const closeCopyMode = () => {
    setCopyView(null);
    termRef.current?.focus();
  };

  const copyAll = () => {
    copyText(copyView.text, null); // kein Refokus — das Overlay bleibt offen
    setCopied(true);
  };

  const overlayBtn =
    "rounded border border-slate-600 px-2 py-0.5 text-xs text-slate-200 hover:bg-slate-700";

  return (
    <div className="flex h-full w-full flex-col">
      <div className="relative min-h-0 w-full flex-1">
        <div ref={ref} className="h-full w-full" />
        {mouseCaptured && !copyView && (
          <div className="pointer-events-none absolute right-2 top-1 z-10 max-w-[90%] rounded border border-slate-600 bg-slate-900/85 px-2 py-0.5 text-[10px] text-slate-300">
            App steuert die Maus — Markieren: Shift+Ziehen (Mac: ⌥) · Touch: ⎘
          </div>
        )}
        {copyView && (
          <div className="absolute inset-0 z-20 flex flex-col bg-slate-900/95">
            <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-700 px-2 py-1">
              <span className="text-xs font-semibold text-slate-300">
                Kopier-Modus — Text frei markierbar
              </span>
              {copyView.full ? (
                <span className="text-[10px] text-slate-400">
                  voller Sitzungsverlauf (Server)
                </span>
              ) : copyView.alt ? (
                <span className="text-[10px] text-amber-400">
                  TUI aktiv — nur Bildschirm + Verlauf davor; alles: „Voller
                  Verlauf“
                </span>
              ) : null}
              <span className="flex-1" />
              {!copyView.full && (
                <button onClick={loadFullBuffer} className={overlayBtn}>
                  Voller Verlauf
                </button>
              )}
              <button onClick={refreshCopyMode} className={overlayBtn}>
                Aktualisieren
              </button>
              <button onClick={copyAll} className={overlayBtn}>
                {copied ? "✓ kopiert" : "Alles kopieren"}
              </button>
              <button onClick={closeCopyMode} className={overlayBtn}>
                Schließen
              </button>
            </div>
            <pre
              // beim Öffnen ans Ende scrollen — da steht das Aktuelle
              ref={(el) => {
                if (el) el.scrollTop = el.scrollHeight;
              }}
              className="min-h-0 flex-1 select-text overflow-auto whitespace-pre px-2 py-1 font-mono text-[13px] leading-snug text-slate-200"
            >
              {copyView.text}
            </pre>
          </div>
        )}
      </div>
      <KeyBar
        mods={mods}
        onToggleMod={toggleMod}
        onKey={handleKey}
        // ⎘ ist ein Umschalter (Issue #10): im Kopier-Modus schließt er ihn —
        // gerade auf dem Handy ist er derselbe Knopf an derselben Stelle,
        // statt des weit entfernten "Schließen" am oberen Overlay-Rand.
        copyActive={!!copyView}
        onCopyMode={() => (copyView ? closeCopyMode() : openCopyMode())}
      />
    </div>
  );
}
