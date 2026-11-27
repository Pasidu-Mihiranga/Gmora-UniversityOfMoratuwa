import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The API is proxied so the app and API share one origin: the strict CSP (connect-src 'self')
// and the SameSite=Strict refresh cookie both work without exceptions (SG-17, SEC-4).
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
  build: {
    target: "es2022",
    sourcemap: false,
    // FE-16: routes are lazy; keep the initial bundle small.
    chunkSizeWarningLimit: 250,
  },
});
