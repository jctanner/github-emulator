import react from "@vitejs/plugin-react";
import {defineConfig} from "vitest/config";

export default defineConfig({
  base: "/ui/",
  plugins: [react()],
  server: {
    proxy: {
      "/admin/api": "http://127.0.0.1:8000",
      "/api": "http://127.0.0.1:8000",
      "/avatars": "http://127.0.0.1:8000",
      "/graphql": "http://127.0.0.1:8000",
      "/ui-legacy": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
  },
});
