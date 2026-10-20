import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Two pages: index.html (Solana desk, port 8765) and rh.html (Robinhood desk, port 8766).
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../src/desk/web/static",
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      input: { main: "index.html", rh: "rh.html" },
    },
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
