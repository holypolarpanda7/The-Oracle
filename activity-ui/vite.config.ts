import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base './' so FastAPI can mount the built bundle under any path (/activity).
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist", sourcemap: false },
  server: {
    proxy: {
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
      "/activity-api": "http://127.0.0.1:8000",
    },
  },
});
