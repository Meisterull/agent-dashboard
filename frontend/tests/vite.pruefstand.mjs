// Baut NUR den Prüfstand (pruefstand.html) für den Browser-Test.
// Der Auslieferungs-Build bleibt unberührt: `npm run build` nimmt allein
// index.html als Einstieg, pruefstand.html taucht dort nicht auf.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: process.env.PRUEFSTAND_OUT || "/tmp/pruefstand",
    emptyOutDir: true,
    rollupOptions: { input: "pruefstand.html" },
  },
});
