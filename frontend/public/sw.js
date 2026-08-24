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
  event.waitUntil(
    self.registration.showNotification(d.title || "agent-dashboard", {
      body: d.body || "",
      // gleiche Envelope-ID ersetzt sich selbst statt sich zu stapeln
      tag: d.tag || undefined,
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      data: { url: d.url || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((liste) => {
        for (const c of liste) if ("focus" in c) return c.focus();
        return clients.openWindow(
          (event.notification.data && event.notification.data.url) || "/",
        );
      }),
  );
});
