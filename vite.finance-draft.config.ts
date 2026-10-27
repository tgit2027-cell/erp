import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// No backend proxy, credentials, environment loading, or shared application entry.
export default defineConfig({
  root: fileURLToPath(new URL("./prototypes/finance-drafts", import.meta.url)),
  publicDir: false,
  plugins: [react()],
  build: {
    outDir: fileURLToPath(new URL("./generated-artifacts/finance-draft-prototype", import.meta.url)),
    emptyOutDir: true,
  },
  preview: { host: "127.0.0.1", port: 18068, strictPort: true },
});
