import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // Deployed under thegreymirror.com/export by default; override with --base for another path.
  base: process.env.GM_BASE ?? "/export/",
  // Source maps ship with the site build; the app bundle inside the helper stays small.
  build: { target: "es2023", sourcemap: !process.env.GM_BASE },
  server: { port: 5173, strictPort: true },
});
