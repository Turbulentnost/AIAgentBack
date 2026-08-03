import { fileURLToPath, URL } from "node:url";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiProxy = env.VITE_API_PROXY || "http://127.0.0.1:8080";

  return {
    plugins: [react()],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) }
    },
    server: {
      host: "0.0.0.0",
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": {
          target: apiProxy,
          changeOrigin: true,
          timeout: 600000,
          proxyTimeout: 600000
        },
        "/eskd-api": {
          target: apiProxy,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/eskd-api/, ""),
          timeout: 600_000,
          proxyTimeout: 600_000
        },
        "/health": {
          target: apiProxy,
          changeOrigin: true
        }
      }
    },
    build: { outDir: "dist", sourcemap: true }
  };
});
