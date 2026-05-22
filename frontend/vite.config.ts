import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// VITE_ALLOWED_HOSTS — comma-separated list of hostnames the dev server
// will accept (Vite 5.4+ rejects "host header mismatch" by default when
// served through a reverse proxy on a domain). Set to a single dot-prefix
// like ".example.com" to allow any subdomain, or set to "true" via env to
// disable the host check entirely.
//
// Public default is permissive (true) so tire-kickers can run the dev
// server through any tunnel/proxy without surprise. Production deploys
// don't run the dev server — they serve a static build via nginx (see
// frontend/Dockerfile.prod).
const raw = process.env.VITE_ALLOWED_HOSTS;
const allowedHosts: true | string[] =
  raw === undefined || raw === "" || raw === "true"
    ? true
    : raw.split(",").map((s) => s.trim()).filter(Boolean);

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    host: true,
    allowedHosts,
    watch: {
      // Polling is reliable for bind-mounted volumes inside Docker.
      usePolling: true,
      interval: 300,
    },
    proxy: {
      // Forward API calls to the backend container.
      "/api": {
        target: process.env.VITE_API_TARGET || "http://backend:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
