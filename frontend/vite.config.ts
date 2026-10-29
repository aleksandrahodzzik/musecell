import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Spec §7: Vite dev on 5174, /api proxied to the FastAPI backend on 8787. Never 8000/8001/3000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:8787", changeOrigin: false } },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
