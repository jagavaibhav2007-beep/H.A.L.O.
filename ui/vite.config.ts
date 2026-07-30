import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { browserSessionPlugin } from "./dev/browserSessionPlugin";

// @ts-expect-error process is a nodejs global
const host = process.env.TAURI_DEV_HOST;
const browserDev = process.env.HALO_BROWSER_DEV === "1";

// https://vite.dev/config/
export default defineConfig(async () => ({
  plugins: [react(), browserSessionPlugin()],

  // Vitest — scoped to the two React hooks that can't be reached by the
  // plain *.selfcheck.ts idiom (they need a DOM + StrictMode render). Everything
  // else stays framework-free selfchecks; see ui/src/**/*.test.tsx.
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}", "dev/**/*.test.ts"],
  },

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: 1420,
    strictPort: true,
    host: browserDev ? "127.0.0.1" : host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // 3. tell Vite to ignore watching `src-tauri`
      ignored: ["**/src-tauri/**"],
    },
  },
}));
