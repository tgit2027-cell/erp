import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { createServer, resolveConfig } from "vite";

const configFile = resolve("vite.config.ts");
const privateDirectories = [".runtime", "backups", "generated-artifacts"];

test("development file guard preserves Vite secret defaults and API proxy", async () => {
  const defaults = await resolveConfig({ configFile: false }, "serve");
  const actual = await resolveConfig({ configFile }, "serve");
  for (const pattern of defaults.server.fs.deny) {
    assert.ok(actual.server.fs.deny.includes(pattern), `Default secret protection missing: ${pattern}`);
  }
  for (const directory of privateDirectories) {
    assert.ok(actual.server.fs.deny.includes(`**/${directory}/**`), `${directory} must not be web-served`);
  }
  assert.equal(actual.server.proxy["/api"].target, "http://127.0.0.1:8000");
  assert.equal(actual.server.proxy["/api"].changeOrigin, false);
});

test("real Vite GET/HEAD and @fs paths cannot download private synthetic sentinels", async () => {
  const root = await mkdtemp(join(tmpdir(), "tivoli-private-files-"));
  let server;
  try {
    await writeFile(join(root, "public.txt"), "TEST-ONLY public fixture");
    for (const directory of privateDirectories) {
      await mkdir(join(root, directory));
      await writeFile(join(root, directory, "private.txt"), "TEST-ONLY private sentinel, not a credential");
    }
    server = await createServer({
      configFile, root, logLevel: "silent",
      server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false,
        watch: null, proxy: {}, fs: { allow: [root] } },
    });
    await server.listen();
    const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
    assert.equal((await fetch(`${origin}/public.txt`)).status, 200);
    for (const directory of privateDirectories) {
      for (const path of [`/${directory}/private.txt`, `/@fs/${join(root, directory, "private.txt")}`]) {
        for (const method of ["GET", "HEAD"]) {
          const response = await fetch(origin + path, { method });
          const body = await response.text();
          assert.equal(response.status, 403, `${method} ${directory} private path must be denied`);
          assert.ok(!body.includes("TEST-ONLY private sentinel"));
        }
      }
    }
  } finally {
    if (server) await server.close();
    await rm(root, { recursive: true, force: true });
  }
});
