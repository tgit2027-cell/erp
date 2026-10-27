import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Only loopback names are accepted unless VITE_ALLOWED_HOSTS lists the exact
// extra hostnames (comma-separated) a private preview is served under.
const djangoProxyTarget = process.env.DJANGO_PROXY_TARGET ?? "http://127.0.0.1:8000";

const allowedHosts = (process.env.VITE_ALLOWED_HOSTS ?? "")
  .split(",")
  .map((host) => host.trim())
  .filter(Boolean);

export default defineConfig({
  base: process.env.VITE_BASE_PATH ?? "/",
  plugins: [react()],
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
        target: djangoProxyTarget,
        // Keep the browser Host intact; the Origin header is forwarded as sent.
        changeOrigin: false,
      },
    },
  },
});
