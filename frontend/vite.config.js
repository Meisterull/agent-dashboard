import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Im Build: statisches dist/, das nginx ausliefert (/api + /ws proxyt nginx).
// Im Dev: Vite proxyt /api und /ws (WebSocket) direkt ans Backend, damit
// dieselben relativen Aufrufe lokal und in Produktion funktionieren.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:5000",
      "/ws": { target: "ws://127.0.0.1:5000", ws: true },
    },
  },
});
