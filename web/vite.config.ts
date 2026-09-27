import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// The built SPA is written into sql_harness/_web_assets/ inside the Python
// package so importlib.resources can pick it up at runtime (webapp._serve_asset).
//
// On-disk layout after `npm run build`:
//   ../src/sql_harness/_web_assets/
//     index.html
//     assets/index-<hash>.js
//     assets/style-<hash>.css
//
// Runtime (webapp._serve_asset):
//   GET /                       -> _web_assets/index.html
//   GET /static/assets/<x>      -> _web_assets/assets/<x>
//
// base:'/static/assets/' makes the asset URLs in the emitted HTML match the
// served path. The /api proxy is dev-only; production serves through Python.
//
// npm ci / npm run build / npm run dev are all run from this web/ directory.
//
// Source layout:
//   web/
//     vite.config.ts
//     index.html             ← entry HTML (sibling of vite.config)
//     src/
//       main.tsx
//       components/...
//       lib/...
//       styles/...

import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB_ASSETS_DIR = path.resolve(HERE, "../src/sql_harness/_web_assets");
const SRC_DIR = path.resolve(HERE, "src");

export default defineConfig({
  plugins: [react()],
  base: "/static/",
  build: {
    outDir: WEB_ASSETS_DIR,
    emptyOutDir: true,
    assetsDir: ".",
    sourcemap: false,
    target: "es2022",
  },
  resolve: {
    alias: {
      "@": SRC_DIR,
    },
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
  resolve: {
    preserveSymlinks: false,
  },
});