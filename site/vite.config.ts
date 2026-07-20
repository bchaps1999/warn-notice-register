import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  // GitHub Pages project sites live under /<repo>/; default is root (CF Pages, local)
  base: process.env.SITE_BASE ?? "/",
  plugins: [react(), tailwindcss()],
});
