// Actual authentication/APIs; this module is only run by the guarded disposable harness.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { browserHarness } from "./browser-harness.mjs";
import { openFunction } from "./workspace-browser-navigation.mjs";

assert.ok(process.env.ACCEPTANCE_ROOT?.startsWith("/tmp/tivoli-retail-acceptance."));
assert.equal(process.env.REAL_BACKEND_ORIGIN, "http://127.0.0.1:18064");
const output = "generated-artifacts/capacity-real-browser";
await mkdir(output, { recursive: true });
function control(phase) {
  execFileSync("python", ["manage.py", "shell", "--no-imports", "-c",
    `from scripts.retail_acceptance_fixture import control; control(${JSON.stringify(phase)})`], { stdio: "pipe" });
}
function snapshot() {
  return JSON.parse(execFileSync("python", ["manage.py", "shell", "--no-imports", "-c",
    "import json; from scripts.retail_acceptance_fixture import probe; print(json.dumps(probe(), default=str))"], { encoding: "utf8" }));
}
async function fill(b, selector, text) {
  await b.click(selector);
  await b.evaluate(`document.querySelector(${JSON.stringify(selector)}).select()`);
  await b.text(String(text));
}
async function read(b, path) {
  return b.evaluate(`(async()=>{const r=await fetch(${JSON.stringify(path)});return {status:r.status,body:await r.json()};})()`);
}
async function captureCapacity(b, name) {
  // A coordinate click on content can hit the narrow sidebar flyout instead.
  // Collapse it using its real keyboard-operated disclosure, without navigating.
  await b.evaluate(`document.querySelector('[data-workspace-link="inventory/capacity"]')
    .closest(".section-navigation-group").querySelector(".nav-section-toggle").focus()`);
  const expanded = await b.evaluate(`document.querySelector('[data-workspace-link="inventory/capacity"]')
    .closest(".section-navigation-group").querySelector(".nav-section-toggle").getAttribute("aria-expanded")`);
  if (expanded === "true") await b.key("Enter");
  assert.equal(await b.evaluate("location.hash"), "#/inventory/capacity");
  b.assertSafe();
  await b.screenshot(name, "capacity-real-browser");
}
async function setup(width, loseNextRead = () => false) {
  control("capacity");
  const fixture = JSON.parse(await readFile(`${process.env.ACCEPTANCE_ROOT}/browser-private.json`, "utf8"));
  const [a, other] = fixture.capacity_locations;
  const b = await browserHarness({
    width, height: 1000, realBackendOrigin: process.env.REAL_BACKEND_ORIGIN,
    allowedRealWrites: ["POST /api/auth/login/", `POST /api/units/${fixture.roche}/select/`,
      `PUT /api/warehouse-capacity/${a}/`],
    beforeRealResponse: async call => {
      if (call.method === "GET" && call.path === "/api/warehouse-capacity/" && call.status === 200 && loseNextRead()) {
        throw new Error("TEST ONLY lost permitted read acknowledgement");
      }
    },
  });
  await b.wait('!!document.querySelector("#username")');
  await fill(b, "#username", "demo_multi");
  await fill(b, "#password", fixture.credentials.demo_multi);
  await b.click('button[type=submit]'); await b.wait('!!document.querySelector(".sidebar")');
  await openFunction(b, "inventory", "capacity");
  await b.wait('document.querySelectorAll(".capacity-location-card").length===2');
  const cards = await b.evaluate('[...document.querySelectorAll(".capacity-location-card")].map(n=>n.getAttribute("aria-label"))');
  return { b, fixture, a, other, cards, form: ".capacity-form" };
}
for (const width of [1280, 320]) test(`real capacity view revocation removes protected measurements at ${width}px`, async () => {
  const { b, a } = await setup(width);
  try {
    await fill(b, ".capacity-form input", "150.123456");
    control("capacity-view-revoke");
    const baseline = snapshot();
    assert.equal((await read(b, `/api/warehouse-capacity/${a}/`)).status, 403);
    await b.click(".capacity-form button");
    await b.wait('document.querySelectorAll(".capacity-location-card").length===1');
    assert.equal(await b.evaluate('document.body.innerText.includes("125.250000")'), false);
    assert.equal(await b.evaluate('!!document.querySelector(".capacity-form")'), false);
    assert.equal(await b.evaluate('document.body.innerText.includes("222.500000 m²")'), true);
    assert.deepEqual(snapshot(), baseline, "Denied edit and authorized read change no foundation facts");
    assert.equal(b.calls.filter(c => c.method === "PUT").length, 1);
    assert.ok(b.calls.some(c => c.method === "PUT" && c.status === 403));
    b.assertSafe();
    await captureCapacity(b, `view-revoked-${width}`);
    await writeFile(`${output}/view-revoked-${width}.json`, JSON.stringify(b.calls, null, 2));
  } finally { await b.close(); }
});

test("real capacity edit-only denial and lost verification retain allowed content; refresh and BU change respect location assignments", async () => {
  let lose = false;
  const { b, fixture, a } = await setup(1280, () => {
    if (!lose) return false;
    lose = false; return true;
  });
  try {
    // Actual existing validator denies allocation > total without writes.
    await fill(b, ".capacity-form input:nth-of-type(1)", "90.000000");
    const baseline = snapshot();
    await fill(b, ".capacity-form label:nth-of-type(2) input", "100.000000");
    await b.click(".capacity-form button");
    await b.wait('!!document.querySelector(".capacity-form [role=alert]")');
    assert.ok(b.calls.some(c => c.method === "PUT" && c.status === 400));
    assert.deepEqual(snapshot(), baseline);
    await fill(b, ".capacity-form input", "150.123456");
    control("capacity-edit-revoke");
    const revoked = snapshot();
    assert.equal((await read(b, `/api/warehouse-capacity/${a}/`)).body.capability, "view_capacity");
    lose = true;
    await b.click(".capacity-form button");
    await b.wait('document.body.innerText.includes("Previously loaded")');
    assert.equal(await b.evaluate('document.querySelector(".capacity-form input").value'), "150.123456");
    assert.equal(await b.evaluate('document.querySelector(".capacity-form button").disabled'), true);
    await b.evaluate('[...document.querySelectorAll("button")].find(n=>n.textContent==="Refresh capacity access").focus()');
    await b.key("Enter");
    await b.wait('document.querySelectorAll(".capacity-location-card").length===2 && !document.querySelector(".capacity-form")');
    assert.equal(await b.evaluate('document.body.innerText.includes("125.250000 m²")'), true);
    assert.equal(await b.evaluate('document.body.innerText.includes("Previously loaded")'), false);
    assert.equal(b.calls.filter(c => c.method === "PUT").length, 2, "No automatic write retry");
    assert.deepEqual(snapshot(), revoked);
    // Capacity is independently assigned by physical location, NOT BU-filtered.
    const index = await b.evaluate(`Array.from(document.querySelector('[aria-label="Business unit"]').options).findIndex(o=>o.value===${JSON.stringify(String(fixture.roche))})`);
    assert.ok(index >= 0);
    await b.evaluate('document.querySelector(\'[aria-label="Business unit"]\').focus()');
    await b.key("Home");
    for (let i = 0; i < index; i++) await b.key("ArrowDown");
    await b.key("Enter");
    await b.wait(`document.querySelector('[aria-label="Business unit"]').value===${JSON.stringify(String(fixture.roche))}`);
    await b.wait('!document.body.innerText.includes("Updating business-unit context")');
    assert.ok(b.calls.some(c => c.path === `/api/units/${fixture.roche}/select/` && c.status === 200));
    assert.equal(await b.evaluate('document.querySelectorAll(".capacity-location-card").length'), 2);
    assert.ok(b.calls.filter(c => c.path === "/api/warehouse-capacity/").every(c => !c.query));
    b.assertSafe();
    await captureCapacity(b, "edit-only-and-bu");
    await writeFile(`${output}/edit-only-and-bu.json`, JSON.stringify(b.calls, null, 2));
  } finally { await b.close(); }
});
