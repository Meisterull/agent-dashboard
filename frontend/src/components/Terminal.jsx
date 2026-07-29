import { useEffect, useRef, useState } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import KeyBar from "./KeyBar";
import { encodeKey, encodeChar } from "../keys";

// Ein SSH-Terminal pro Verbindung. Spricht /ws/ssh/<name>?sid=… — die sid
// identifiziert eine serverseitig persistente Session: bricht der WebSocket
// ab (Handy gesperrt, Netzwechsel), verbindet sich das Terminal automatisch
// neu und bekommt den gepufferten Output nachgespielt. Die sid liegt in
// localStorage, damit auch ein Seiten-Reload die Session wiederfindet.
// Explizit beendet wird die Session vom TerminalPanel (DELETE-Endpoint).
//
// Für Mobilgeräte gibt es eine KeyBar mit Sondertasten und Sticky-Modifikatoren
// (Strg/Alt/Shift): ein aktiver Modifikator wirkt auf die nächste Taste — egal
// ob aus der Leiste oder von der Bildschirmtastatur — und schaltet sich danach
// selbst ab.

export function sidFor(name) {
  const key = `term-sid-${name}`;
  let sid = localStorage.getItem(key);
  if (!sid) {
    sid = crypto.randomUUID();
    localStorage.setItem(key, sid);
  }
  return sid;
}

export function clearSid(name) {
  localStorage.removeItem(`term-sid-${name}`);
}

export default function Terminal({ name, visible = true }) {
  const ref = useRef(null);
  const fitRef = useRef(null);
  const wsRef = useRef(null);
  const [mods, setMods] = useState({ ctrl: false, alt: false, shift: false });
  const modsRef = useRef(mods);
  modsRef.current = mods;

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
    const copySelection = () => {
      const text = term.getSelection();
      if (!text) return false;
      if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text).catch(() => {});
      } else {
        // Zugriff per HTTP/IP ohne TLS: keine Clipboard-API → execCommand
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      term.clearSelection();
      return true;
    };
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
    const sid = sidFor(name);

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
          // Shell beendet / Session gekillt: neue sid für den nächsten Start
          gone = true;
          clearSid(name);
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
  }, [name]);

  return (
    <div className="flex h-full w-full flex-col">
      <div ref={ref} className="min-h-0 w-full flex-1" />
      <KeyBar mods={mods} onToggleMod={toggleMod} onKey={handleKey} />
    </div>
  );
}
