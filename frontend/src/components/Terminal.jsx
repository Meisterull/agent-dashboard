import { useEffect, useRef, useState } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import KeyBar from "./KeyBar";
import { encodeKey, encodeChar } from "../keys";

// Ein SSH-Terminal pro Verbindung. Spricht /ws/ssh/<name>?sid=… — die sid
// identifiziert eine serverseitig persistente Session: bricht der WebSocket
// ab (Handy gesperrt, Netzwechsel) oder wird das Fenster geschlossen, läuft
// die Shell serverseitig weiter; beim nächsten Öffnen wird der gepufferte
// Output nachgespielt. Die sid ist bewusst eine Konstante pro Verbindung
// (nicht pro Browser): so hängt sich auch ein anderer PC/Browser an dieselbe
// Session — der bisherige Client wird per Close-Code 4000 übernommen.
// Explizit beendet wird die Session vom TerminalPanel (DELETE-Endpoint);
// endet die Shell (exit/kill), meldet das Terminal das über onEnded.
//
// Für Mobilgeräte gibt es eine KeyBar mit Sondertasten und Sticky-Modifikatoren
// (Strg/Alt/Shift): ein aktiver Modifikator wirkt auf die nächste Taste — egal
// ob aus der Leiste oder von der Bildschirmtastatur — und schaltet sich danach
// selbst ab.

export const DEFAULT_SID = "main";

export default function Terminal({ name, sid = DEFAULT_SID, visible = true, onEnded }) {
  const ref = useRef(null);
  const fitRef = useRef(null);
  const wsRef = useRef(null);
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
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(ref.current);
    fit.fit();

    // Kopieren: Strg+C mit aktiver Auswahl kopiert in die Zwischenablage
    // (statt SIGINT an die Shell); ohne Auswahl bleibt Strg+C das gewohnte
    // Abbrechen. Deckt auch Strg+Shift+C (sonst DevTools) und Strg+Einfg ab.
    // Fallback ohne Clipboard-API (Zugriff per IP/HTTP oder per https mit
    // ungültigem Zertifikat — Chrome sperrt "powerful features" dann). Das
    // ta.focus() ist zwingend: ohne Fokus kopiert execCommand die Auswahl des
    // Dokuments, nicht die des Textfelds — und die xterm-Auswahl ist gemalt,
    // keine DOM-Selection, also landet nichts in der Zwischenablage.
    const copyFallback = (text) => {
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
      term.focus(); // Tastatur zurück ans Terminal
    };

    // writeText kann trotz vorhandener API scheitern (Fokus, Berechtigung,
    // unsicherer Kontext) — die Ablehnung muss in den Fallback führen,
    // sonst schluckt der catch den Fehler und es wird gar nichts kopiert.
    const copyText = (text) => {
      if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text).catch(() => copyFallback(text));
      } else {
        copyFallback(text);
      }
    };

    const copySelection = () => {
      const text = term.getSelection();
      if (!text) return false;
      copyText(text);
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
    const handleMouseUp = () => {
      const text = term.getSelection();
      if (text) copyText(text);
    };
    const termEl = ref.current;
    termEl.addEventListener("mouseup", handleMouseUp);
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
      termEl.removeEventListener("mouseup", handleMouseUp);
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

  return (
    <div className="flex h-full w-full flex-col">
      <div ref={ref} className="min-h-0 w-full flex-1" />
      <KeyBar mods={mods} onToggleMod={toggleMod} onKey={handleKey} />
    </div>
  );
}
