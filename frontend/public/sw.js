// Service Worker — NUR für Web-Push (F10), kein Offline-Caching: das
// Dashboard lebt von Live-Daten, ein Cache zeigte nur veralteten Zustand.
// push zeigt die Notification, notificationclick holt das Fenster nach vorn.
self.addEventListener("push", (event) => {
  let d = {};
  try {
    d = event.data ? event.data.json() : {};
  } catch {
    d = { body: event.data ? event.data.text() : "" };
  }
  // Knöpfe direkt in der Meldung (Issue #30): Eine Rückfrage mit vorgegebenen
  // Antworten lässt sich vom Sperrbildschirm aus erledigen. Ohne `optionen`
  // bleibt es beim Öffnen — wer eine Freitextfrage stellt oder eine mit
  // schweren Folgen, bekommt bewusst keinen Ein-Tipp-Weg.
  const optionen = Array.isArray(d.optionen) ? d.optionen.slice(0, 2) : [];
  const actions = optionen.map((o) => ({
    action: `antwort:${o}`,
    title: o.charAt(0).toUpperCase() + o.slice(1),
  }));
  if (actions.length) actions.push({ action: "oeffnen", title: "Öffnen" });

  event.waitUntil(
    (async () => {
      // Zahl offener Rückfragen aufs App-Symbol — auch wenn die App
      // geschlossen ist.
      if (typeof d.offen === "number" && self.navigator?.setAppBadge) {
        try {
          if (d.offen > 0) await self.navigator.setAppBadge(d.offen);
          else await self.navigator.clearAppBadge();
        } catch {
          /* Badging nicht erlaubt — kein Grund, die Meldung zu verschlucken */
        }
      }
      await self.registration.showNotification(d.title || "agent-dashboard", {
        body: d.body || "",
        // gleiche Envelope-ID ersetzt sich selbst statt sich zu stapeln
        tag: d.tag || undefined,
        icon: "/icon-192.png",
        badge: "/icon-192.png",
        actions,
        data: d,
      });
    })(),
  );
});

async function fensterOeffnen(url) {
  const liste = await clients.matchAll({
    type: "window",
    includeUncontrolled: true,
  });
  for (const c of liste) {
    if ("focus" in c) {
      // Die offene App soll zur richtigen Stelle springen, nicht bloß nach
      // vorn kommen — sonst landet man auf dem zuletzt benutzten Tab.
      c.postMessage({ typ: "navigiere", url });
      return c.focus();
    }
  }
  return clients.openWindow(url);
}

self.addEventListener("notificationclick", (event) => {
  const d = event.notification.data || {};
  const ziel = d.url || "/";
  const aktion = event.action || "";
  event.notification.close();

  // Antwort direkt aus der Meldung: Der fetch läuft same-origin und bringt
  // damit das Session-Cookie mit. Schlägt er fehl (Sitzung abgelaufen,
  // Frage schon beantwortet), öffnen wir stattdessen die App — verschluckt
  // wird eine Rückfrage nie.
  if (aktion.startsWith("antwort:") && d.agent && d.qid) {
    const text = aktion.slice("antwort:".length);
    event.waitUntil(
      fetch(
        `/api/questions/${encodeURIComponent(d.agent)}/${encodeURIComponent(d.qid)}/answer`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
          credentials: "same-origin",
        },
      )
        .then((r) => {
          if (!r.ok) throw new Error(String(r.status));
          if (self.navigator?.clearAppBadge) return self.navigator.clearAppBadge();
        })
        .catch(() => fensterOeffnen(ziel)),
    );
    return;
  }

  event.waitUntil(fensterOeffnen(ziel));
});


// Teilen-Ziel (Issue #29): Android schickt geteilte Inhalte als POST an
// /share. Diesen POST fängt der Service Worker ab, statt ihn ans Backend zu
// lassen — und zwar aus einem handfesten Grund: Das Session-Cookie ist
// `samesite=lax` und begleitet einen POST aus einer fremden App NICHT. Der
// Upload würde also an der Anmeldung scheitern. Der Service Worker liest die
// Daten stattdessen selbst aus und lädt sie mit einem eigenen fetch hoch, das
// als same-origin gilt und das Cookie mitbringt.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(clients.claim()));

async function geteiltesAblegen(request) {
  const daten = await request.formData();
  const dateien = daten.getAll("files").filter((f) => f && f.size);
  const text = [daten.get("title"), daten.get("text"), daten.get("url")]
    .filter(Boolean)
    .join(" ");

  let pfade = [];
  if (dateien.length) {
    const tag = new Date().toISOString().slice(0, 10);
    const form = new FormData();
    for (const f of dateien) form.append("files", f, f.name || "geteilt");
    const antwort = await fetch(
      `/api/files/upload?path=${encodeURIComponent("uploads/chat/" + tag)}`,
      { method: "POST", body: form, credentials: "same-origin" },
    );
    if (antwort.ok) {
      const d = await antwort.json();
      pfade = (d.saved || []).map((s) => (typeof s === "string" ? s : s.path));
    }
  }

  // Als fertigen Entwurf in den Chat geben: Der Nutzer ergänzt seine Frage
  // und schickt ab. Die Anhänge sind zu diesem Zeitpunkt bereits abgelegt.
  const entwurf = [text, pfade.length ? `Anhänge: ${pfade.join(", ")}` : ""]
    .filter(Boolean)
    .join("\n\n");
  return Response.redirect(
    `/?tab=chat&entwurf=${encodeURIComponent(entwurf)}`,
    303,
  );
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === "POST" && url.pathname === "/share") {
    event.respondWith(
      geteiltesAblegen(event.request).catch(() =>
        Response.redirect("/?tab=chat", 303),
      ),
    );
  }
});
