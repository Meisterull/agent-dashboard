import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./index.css";

// Service Worker: nur für Web-Push (F10) — kein Offline-Caching. Ohne ihn
// erreichen Rückfragen/fertige Tasks das Handy nicht, sobald die PWA im
// Hintergrund schläft (das Polling schläft dann mit).
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
