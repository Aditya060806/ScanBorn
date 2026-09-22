import { defineConfig } from "vite";
import { resolve } from "node:path";

export default defineConfig({
  base: "./",
  build: {
    outDir: "../MOBILE-APP/app/build/generated/viewerAssets/viewer",
    emptyOutDir: true,
    rolldownOptions: { input: resolve(import.meta.dirname, "viewer.html") },
  },
});
