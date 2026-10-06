import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    // Development only: the Vite server hands API calls to the local `web`
    // service, so the browser still sees one origin (AD-19).
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    outDir: "dist",
    // Every file is referenced by URL: the content security policy allows no
    // inline scripts or data: URLs.
    assetsInlineLimit: 0,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    restoreMocks: true,
    coverage: {
      enabled: true,
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/api/contracts.gen.ts", "src/main.tsx", "src/test/**"],
      reporter: ["text-summary"],
      // coding-style.md rule 25: never lower it; add tests.
      thresholds: { lines: 60, functions: 60, branches: 60, statements: 60 },
    },
  },
});
