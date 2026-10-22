import { defineConfig } from "vite";

export default defineConfig({
  build: { chunkSizeWarningLimit: 2000 },
  server: {
    port: 5173,
    // YARBIE_API points the dev page at another yarbie, e.g. a second one under test.
    proxy: { "/api": process.env.YARBIE_API ?? "http://127.0.0.1:8765" },
  },
});
