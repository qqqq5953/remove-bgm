import { defineConfig } from "vitest/config";

export default defineConfig({
  server: {
    // 開發時把 API 轉給本機後端
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts"],
  },
});
