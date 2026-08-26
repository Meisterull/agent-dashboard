// Live-Events (F4): EIN EventSource auf /api/events für die ganze App.
// Mailbox-Änderungen kommen als {type:"mailbox", agents:[…]} und werden als
// window-Event "live:mailbox" weitergereicht — Agenten-Panel und Rückfragen-
// Banner laden dann sofort nach, statt auf ihren nächsten Poll zu warten.
// Das Polling bleibt bewusst bestehen: reißt der Strom ab (Standby, Proxy),
// stimmt die Anzeige spätestens einen Poll später wieder. Reconnect macht
// der Browser selbst (EventSource + retry-Vorgabe vom Server).
//
// Entprellung: der Server bündelt Datei-Änderungen nur in 300-ms-Fenstern
// (EVENTS_SAMMEL_MS) — ein arbeitender Agent schreibt seinen Fortschritt
// aber im Sekundentakt in die Mailbox. Jedes Event löst im Panel eine
// komplette Lade-Salve aus (getAgents + getTasks je Agent); ungebremst
// ruckelt davon das Scrollen auf dem Handy. Erstes Event feuert sofort
// (Reaktionsgefühl), weitere frühestens nach WARTE_MS — das letzte geht
// nie verloren (trailing dispatch, agents-Listen werden vereinigt).
const WARTE_MS = 2500;

export function starteLiveEvents() {
  const es = new EventSource("/api/events");
  let zuletzt = 0;
  let timer = null;
  let gesammelt = null;
  const feuern = () => {
    zuletzt = Date.now();
    window.dispatchEvent(new CustomEvent("live:mailbox", { detail: gesammelt }));
    gesammelt = null;
  };
  es.onmessage = (ev) => {
    let d;
    try {
      d = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (d.type !== "mailbox") return;
    gesammelt = {
      type: "mailbox",
      agents: [...new Set([...(gesammelt?.agents || []), ...(d.agents || [])])],
    };
    const rest = WARTE_MS - (Date.now() - zuletzt);
    if (rest <= 0) feuern();
    else if (!timer)
      timer = setTimeout(() => {
        timer = null;
        feuern();
      }, rest);
  };
  return () => {
    es.close();
    if (timer) clearTimeout(timer);
  };
}
