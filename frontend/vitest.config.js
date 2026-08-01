import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Test-only config. Vitest prefers vitest.config.js over vite.config.js, so the
// define() in vite.config.js (which reads the repo-root .env — machine-dependent)
// never applies here. test.env pins VITE_API_BASE on import.meta.env so URL
// assertions are hermetic and deterministic on any machine.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.js"],
    env: {
      VITE_API_BASE: "http://test-api",
    },
  },
});
