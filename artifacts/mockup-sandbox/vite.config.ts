import { defineConfig, type UserConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import { mockupPreviewPlugin } from "./mockupPreviewPlugin";

export default defineConfig(async ({ command }): Promise<UserConfig> => {
  // PORT / BASE_PATH are only required when serving the dev server. During a
  // production `vite build` (e.g. the deploy build) they are not provided, so
  // fall back to safe defaults instead of throwing and failing the build.
  const isServe = command === "serve";

  const rawPort = process.env.PORT;
  if (isServe && !rawPort) {
    throw new Error(
      "PORT environment variable is required but was not provided.",
    );
  }
  const port = rawPort ? Number(rawPort) : 5173;
  if (isServe && (Number.isNaN(port) || port <= 0)) {
    throw new Error(`Invalid PORT value: "${rawPort}"`);
  }

  const rawBasePath = process.env.BASE_PATH;
  if (isServe && !rawBasePath) {
    throw new Error(
      "BASE_PATH environment variable is required but was not provided.",
    );
  }
  const basePath = rawBasePath ?? "/";

  return {
    base: basePath,
    plugins: [
    mockupPreviewPlugin(),
    react(),
    tailwindcss(),

  ],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
    },
  },
  root: path.resolve(import.meta.dirname),
  build: {
    outDir: path.resolve(import.meta.dirname, "dist"),
    emptyOutDir: true,
  },
  server: {
    port,
    host: "0.0.0.0",
    allowedHosts: true,
    fs: {
      strict: true,
    },
  },
  preview: {
    port,
    host: "0.0.0.0",
    allowedHosts: true,
  },
  };
});
