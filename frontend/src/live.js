// Live-Events (F4): EIN EventSource auf /api/events für die ganze App.
// Mailbox-Änderungen kommen als {type:"mailbox", agents:[…]} und werden als
// window-Event "live:mailbox" weitergereicht — Agenten-Panel und Rückfragen-
// Banner laden dann sofort nach, statt auf ihren nächsten Poll zu warten.
// Das Polling bleibt bewusst bestehen: reißt der Strom ab (Standby, Proxy),
// stimmt die Anzeige spätestens einen Poll später wieder. Reconnect macht
// der Browser selbst (EventSource + retry-Vorgabe vom Server).
export function starteLiveEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = (ev) => {
    let d;
    try {
      d = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (d.type === "mailbox")
      window.dispatchEvent(new CustomEvent("live:mailbox", { detail: d }));
  };
  return () => es.close();
}
