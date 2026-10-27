// Isolated real sessions/APIs only. No mocked report data or browser transactions.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { browserHarness } from "./browser-harness.mjs";
import { openFunction } from "./workspace-browser-navigation.mjs";

assert.ok(process.env.ACCEPTANCE_ROOT?.startsWith("/tmp/tivoli-retail-acceptance."));
assert.equal(process.env.REAL_BACKEND_ORIGIN, "http://127.0.0.1:18064");
const output = "generated-artifacts/inventory-register-real-browser";
await mkdir(output, { recursive: true });
function control(phase) {
  execFileSync("python", ["manage.py", "shell", "--no-imports", "-c",
    `from scripts.retail_acceptance_fixture import control; control(${JSON.stringify(phase)})`], { stdio: "pipe" });
}
function snapshot() {
  return JSON.parse(execFileSync("python", ["manage.py", "shell", "--no-imports", "-c",
    "import json; from scripts.retail_acceptance_fixture import probe; print(json.dumps(probe(), default=str))"],
  { encoding: "utf8" }));
}
async function select(b, selector, value) {
  await b.wait(`Array.from(document.querySelector(${JSON.stringify(selector)})?.options||[])
    .some(o=>o.value===${JSON.stringify(String(value))})`);
  await b.wait(`!document.querySelector(${JSON.stringify(selector)}).disabled`);
  // Browser DOM select/change interaction (not an API or mocked response).
  // Select the exact option without intermediate BU choices or OS popup keys.
  await b.evaluate(`(() => {const n=document.querySelector(${JSON.stringify(selector)});
    n.focus(); n.value=${JSON.stringify(String(value))};
    n.dispatchEvent(new Event("change",{bubbles:true})); n.blur();})()`);
  await b.wait(`document.querySelector(${JSON.stringify(selector)}).value===${JSON.stringify(String(value))}`);
}
async function activate(b, label) {
  await b.evaluate(`document.querySelector('[aria-label="${label}"]').focus()`);
  await b.key("Enter");
}
async function read(b, path) {
  return b.evaluate(`(async()=>{const r=await fetch(${JSON.stringify(path)});return {status:r.status,body:await r.json()};})()`);
}
for (const [panel, width] of [["inventory", 1280], ["transfer_history", 320]]) {
  test(`real ${panel} removed-location recovery, reselect, masking and panel revocation at ${width}px`, async () => {
    control("registers");
    const f = JSON.parse(await readFile(`${process.env.ACCEPTANCE_ROOT}/browser-private.json`, "utf8"));
    const [a, other] = f.register_locations;
    const b = await browserHarness({
      width, height: 1000, realBackendOrigin: process.env.REAL_BACKEND_ORIGIN,
      allowedRealWrites: ["POST /api/auth/login/", `POST /api/units/${f.unit}/select/`],
    });
    try {
      await b.wait('!!document.querySelector("#username")');
      for (const [id, value] of [["username", "demo_multi"], ["password", f.credentials.demo_multi]]) {
        await b.click(`#${id}`); await b.text(value);
      }
      await b.click('button[type="submit"]'); await b.wait('!!document.querySelector(".sidebar")');
      await select(b, '[aria-label="Business unit"]', f.unit);
      await b.wait('!document.body.innerText.includes("Updating business-unit context")');
      await openFunction(b, "inventory", "registers");
      if (panel !== "inventory") await select(b, '[aria-label="Inventory report panel"]', panel);
      await select(b, '[aria-label="Report location filter"]', a);
      const route = `/api/reports/${panel}/`;
      await b.wait('!!document.querySelector(".inventory-registers tbody")');
      const rowsBefore = await read(b, `${route}?unit=${f.unit}&location=${a}`);
      assert.equal(rowsBefore.status, 200);
      assert.ok(rowsBefore.body.rows.length > 0);
      const dates = await b.evaluate('[...document.querySelectorAll(".inventory-register-controls input")].map(n=>n.value)');
      control("registers-remove-location");
      const baseline = snapshot();
      const denial = await read(b, `${route}?unit=${f.unit}&location=${a}`);
      assert.equal(denial.status, 403);
      assert.equal("rows" in denial.body, false);
      const requestCount = b.calls.filter(c => c.path === route).length;
      await activate(b, "Refresh current inventory register");
      await b.wait('!!document.querySelector(\'[aria-label="Clear unavailable location filter"]\')');
      assert.equal(await b.evaluate('!!document.querySelector(".inventory-registers tbody")'), false);
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Report location filter"]\').selectedOptions[0].textContent.includes("unavailable")'), true);
      assert.equal(b.calls.filter(c => c.path === route).length, requestCount, "No implicit all-location/old-location load");
      assert.deepEqual(snapshot(), baseline, "Refresh and denial must not mutate business facts");
      const controlsFit = await b.evaluate(`(() => {
        const s=document.querySelector(".inventory-registers"), bounds=s.getBoundingClientRect();
        return [...s.querySelectorAll("input,select,button")].every(n=>{
          const r=n.getBoundingClientRect();return r.left>=bounds.left && r.right<=bounds.right;
        });
      })()`);
      assert.equal(controlsFit, true, "Filter/recovery controls must fit the card, not be hidden by overflow clipping");
      // Keyboard clear explicitly authorizes a new, current BU-scoped request.
      await activate(b, "Clear unavailable location filter");
      await b.wait('!!document.querySelector(".inventory-registers tbody")');
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Report location filter"]\').value'), "");
      assert.ok(b.calls.some(c => c.path === route && c.status === 200
        && new URLSearchParams(c.query).get("unit") === String(f.unit)
        && !new URLSearchParams(c.query).has("location")));
      await select(b, '[aria-label="Report location filter"]', other);
      await b.wait('!!document.querySelector(".inventory-registers tbody")');
      const allowed = await read(b, `${route}?unit=${f.unit}&location=${other}`);
      assert.equal(allowed.status, 200);
      assert.equal(allowed.body.filters.location_id, other);
      assert.equal(allowed.body.scope.business_unit.id, f.unit);
      assert.ok(allowed.body.rows.length);
      assert.equal(JSON.stringify(allowed.body).includes("inventory_value"), false);
      assert.equal(JSON.stringify(allowed.body).includes("transferred_value"), false);
      assert.equal((await read(b, `${route}?unit=${f.roche}&location=${other}`)).status, 403);
      assert.deepEqual(await b.evaluate('[...document.querySelectorAll(".inventory-register-controls input")].map(n=>n.value)'), dates);
      assert.deepEqual(snapshot(), baseline);
      // Take the verified page screenshot without clicking through a flyout.
      await b.evaluate(`document.querySelector('[data-workspace-link="inventory/registers"]')
        .closest(".section-navigation-group").querySelector(".nav-section-toggle").focus()`);
      await b.key("Enter");
      assert.equal(await b.evaluate("location.hash"), "#/inventory/registers");
      assert.equal(await b.evaluate('document.documentElement.scrollWidth <= innerWidth'), true);
      await b.screenshot(`${panel}-${width}`, "inventory-register-real-browser");
      control("registers-revoke-panel");
      const revoked = snapshot();
      assert.equal((await read(b, `${route}?unit=${f.unit}&location=${other}`)).status, 403);
      await activate(b, "Refresh current inventory register");
      await b.wait('document.body.innerText.includes("panel is not assigned")');
      assert.equal(await b.evaluate('!!document.querySelector(".inventory-registers tbody")'), false);
      assert.deepEqual(snapshot(), revoked);
      b.assertSafe();
      assert.equal(b.calls.some(c => ["PUT", "PATCH", "DELETE"].includes(c.method)), false);
      await writeFile(`${output}/${panel}.json`, JSON.stringify(b.calls, null, 2));
    } catch (error) {
      console.error(JSON.stringify({ calls: b.calls, state: await b.evaluate(`({
        route:location.hash, unit:document.querySelector('[aria-label="Business unit"]')?.value,
        unitDisabled:document.querySelector('[aria-label="Business unit"]')?.disabled,
        messages:[...document.querySelectorAll('[role="alert"],[role="status"]')].map(n=>n.textContent)
      })`) }));
      throw error;
    } finally { await b.close(); }
  });
}
