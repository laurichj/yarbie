import { defineConfig } from "vite";

// The panel is built into the integration, which serves it to Home Assistant.
export default defineConfig({
  base: "./",
  build: {
    outDir: "../custom_components/yarbie/www",
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000,
    lib: {
      // The panel, and the dashboard strategy and its cards that HA loads on every page
      // (kept small).
      entry: {
        "yarbie-panel": "src/panel.ts",
        "yarbie-dashboard": "src/dashboard.ts",
        "yarbie-cards": "src/cards.ts",
      },
      formats: ["es"],
      fileName: (_format, name) => `${name}.js`,
    },
  },
});
