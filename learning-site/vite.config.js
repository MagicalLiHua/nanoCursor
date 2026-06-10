import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          markdown: ["react-markdown", "remark-gfm", "rehype-highlight", "highlight.js"],
          icons: ["lucide-react"],
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5174,
    fs: {
      allow: [fileURLToPath(new URL("..", import.meta.url))],
    },
  },
});
