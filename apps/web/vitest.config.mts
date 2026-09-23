import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // Resolves the "@/*" alias from tsconfig.json; Vite 8 does this natively, so
  // vite-tsconfig-paths is not needed.
  resolve: { tsconfigPaths: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
    // tests/live needs a running API and worker; it has its own config.
    exclude: ["tests/live/**"],
    css: false,
  },
});
