import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The backend mounts every route under /api/v1 and serves the OpenAPI
// browser at /docs. The frontend calls "/api/v1/..." relative to its own
// origin, so the dev server has to forward both prefixes to the API.
const apiProxy = {
  "/api": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
  },
  "/docs": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
  },
  "/openapi.json": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
  },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: apiProxy,
  },
  preview: {
    port: 4173,
    strictPort: true,
    proxy: apiProxy,
  },
});
