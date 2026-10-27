// Optional operator-run loopback review, not a shared preview or deployment.
import assert from "node:assert/strict";
import { browserHarness } from "./browser-harness.mjs";
assert.ok(process.env.ACCEPTANCE_ROOT?.startsWith("/tmp/tivoli-retail-acceptance."));
assert.equal(process.env.REAL_BACKEND_ORIGIN, "http://127.0.0.1:18064");
const b = await browserHarness({ width: 1280, height: 900,
  realBackendOrigin: process.env.REAL_BACKEND_ORIGIN });
try {
  console.log(`SYNTHETIC, DISPOSABLE Retail team review: ${b.origin}`);
  console.log(`Owner-only temporary credentials: ${process.env.ACCEPTANCE_ROOT}/browser-private.json`);
  console.log("Open the loopback URL on the operator's machine. Never copy credentials into feedback.");
  console.log("Ctrl+C discards the session/database; automatic stop after 30 minutes.");
  if (process.env.RETAIL_REVIEW_SMOKE === "1") {
    assert.equal((await fetch(b.origin)).status, 200);
    assert.equal((await fetch(`${b.origin}/api/auth/csrf/`)).status, 200);
    console.log("Review helper smoke: 2 HTTP health checks PASS; no login or transaction submitted.");
  } else {
    await new Promise(resolve => {
      const timer = setTimeout(resolve, 30 * 60 * 1000);
      const stop = () => { clearTimeout(timer); resolve(); };
      process.once("SIGINT", stop); process.once("SIGTERM", stop);
    });
  }
} finally { await b.close(); }
