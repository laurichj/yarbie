import { defineConfig } from "vite";

// The panel is built into the integration, which serves it to Home Assistant.
export default defineConfig({
  base: "./",
  build: {
    outDir: "../custom_components/yarbie/www",
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000,
    lib: {
      entry: "src/panel.ts",
      formats: ["es"],
      fileName: () => "yarbie-panel.js",
    },
  },
});
