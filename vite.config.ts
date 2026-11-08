import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import readableType from "./scripts/postcss-readable-type.mjs";
import oneFont from "./scripts/postcss-one-font.mjs";

// Only loopback names are accepted unless VITE_ALLOWED_HOSTS lists the exact
// extra hostnames (comma-separated) a private preview is served under.
const allowedHosts = (process.env.VITE_ALLOWED_HOSTS ?? "")
  .split(",")
  .map((host) => host.trim())
  .filter(Boolean);

export default defineConfig({
  plugins: [react()],
  // Lift the prototype's very small font sizes to a readable size everywhere,
  // and use the ERP's one typeface in every section.
  css: { postcss: { plugins: [readableType(), oneFont()] } },
  server: {
    host: "127.0.0.1",
    port: 5000,
    strictPort: true,
    allowedHosts,
    fs: {
      // Preserve the installed Vite secret-file defaults, then also exclude
      // private operator data. Git/deployment ignores are not HTTP protection.
      deny: [
        ".env", ".env.*", "*.{crt,pem,key,p12,pfx,cer,der}",
        ".npmrc", ".yarnrc.yml", "**/.git/**",
        "**/.runtime/**", "**/backups/**", "**/generated-artifacts/**",
      ],
    },
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        // Keep the browser Host intact; the Origin header is forwarded as sent.
        changeOrigin: false,
      },
    },
  },
});
