import { defineConfig } from "vite";

// The panel is built into the integration, which serves it to Home Assistant.
export default defineConfig({
  base: "./",
  build: {
    outDir: "../custom_components/yarbie/www",
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000,
    lib: {
      // The panel, and the dashboard strategy HA loads on every page (kept tiny).
      entry: { "yarbie-panel": "src/panel.ts", "yarbie-dashboard": "src/dashboard.ts" },
      formats: ["es"],
      fileName: (_format, name) => `${name}.js`,
    },
  },
});
