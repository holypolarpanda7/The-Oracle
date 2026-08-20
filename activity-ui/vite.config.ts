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
      // Board art — object/wreckage sprites and the surface swatches the
      // isometric board is built from. In production FastAPI serves the bundle
      // and these are same-origin; without this line they 404 in `vite dev`,
      // which is why a locally-run board has always come up untextured.
      "/imagery": "http://127.0.0.1:8000",
      // Landmark meshes this installation MADE. The committed pack meshes live
      // under public/ and vite serves them itself; a mesh generated for a
      // landmark the DM invented is gitignored, outside public/, and only the
      // backend knows about it. See vtt/setpieces.GENERATED_ROOT.
      "/vtt": "http://127.0.0.1:8000",
    },
  },
});
