import { defineConfig } from "vite";
import { resolve } from "path";

export default defineConfig({
  build: {
    outDir: "dist",
    rollupOptions: { input: { main: resolve(__dirname, "index.html"), login: resolve(__dirname, "login.html") } },
  },
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:5000",
        bypass: (req) => {
          if (req.url && req.url.startsWith("/api.js")) return req.url;
        },
      },
      "/crop_samples": "http://127.0.0.1:5000",
    },
  },
});
