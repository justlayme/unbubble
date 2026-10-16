import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig(({ mode }) => {
  // `--mode app` builds the bundle that ships inside the Windows app (served at /, no source maps).
  const app = mode === "app";
  return {
    plugins: [react()],
    // Deployed under thegreymirror.com/export by default; override with --base for another path.
    base: app ? "/" : "/export/",
    build: { target: "es2023", sourcemap: !app },
    server: { port: 5173, strictPort: true },
  };
});
