import test from "node:test";
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { setTimeout as delay } from "node:timers/promises";
import { browserHarness } from "./browser-harness.mjs";
import { openFunction, isVisible } from "./workspace-browser-navigation.mjs";
import { navigationContent } from "./navigation-content-contract.mjs";

if (!process.env.ACCEPTANCE_ROOT || !process.env.REAL_BACKEND_ORIGIN) {
  throw new Error("Use scripts/verify-real-navigation.sh; a verified disposable backend is required");
}
const fixture = JSON.parse(await readFile(`${process.env.ACCEPTANCE_ROOT}/browser-private.json`, "utf8"));
const realBackendOrigin = process.env.REAL_BACKEND_ORIGIN;
const paymentInput = 'form:has(option[value="simulated_cash"]) input[type=number]';
async function recordTrace(b, name) {
  await mkdir("generated-artifacts/real-navigation-browser", { recursive: true });
  await writeFile(`generated-artifacts/real-navigation-browser/${name}-http.json`, JSON.stringify({
    scope: "Disposable synthetic database; actual Django responses, no API mocks",
    // Intentionally omit headers, cookies, tokens, credentials and payload bodies.
    requests: b.calls,
  }, null, 2));
}
async function login(b, username) {
  await b.wait('!!document.querySelector("#username")');
  await b.click("#username"); await b.text(username);
  await b.click("#password"); await b.text(fixture.credentials[username]);
  await b.click('form button[type=submit]');
  await b.wait('!!document.querySelector(".sidebar") && !!document.querySelector(\'[aria-label="Business unit"]\')?.value');
  assert.equal(b.calls.find(call => call.path === "/api/auth/login/")?.status, 200,
    "Actual Django CSRF/session login succeeded");
}
async function api(b, path) {
  return b.evaluate(`fetch(${JSON.stringify(path)}).then(async r=>({status:r.status,body:await r.json()}))`);
}
async function chooseQuote(b) {
  await selectUnit(b, fixture.unit);
  await openFunction(b, "retail", "actions");
  await b.click("#pw-quote-id"); await b.text(String(fixture.quote_id)); await b.key("Enter");
  await b.wait(`document.querySelector(".pw-quote")?.textContent.includes("#${fixture.quote_id}")`);
}
async function selectUnit(b, id) {
  const selector = '[aria-label="Business unit"]';
  const index = await b.evaluate(`[...document.querySelector(${JSON.stringify(selector)}).options].findIndex(n=>n.value===${JSON.stringify(String(id))})`);
  assert.ok(index >= 0);
  if (await b.evaluate(`document.querySelector(${JSON.stringify(selector)}).value`) === String(id)) return;
  await b.click(selector); await b.key("Home");
  for (let i = 0; i < index; i++) await b.key("ArrowDown");
  await b.key("Enter");
  await b.wait(`document.querySelector(${JSON.stringify(selector)}).value===${JSON.stringify(String(id))} && !document.body.textContent.includes("Updating business-unit context")`);
}
async function dialog(b, accept, action) {
  const before = b.dialogs.length;
  const operation = action();
  for (let i = 0; i < 100 && b.dialogs.length === before; i++) await delay(20);
  assert.equal(b.dialogs.length, before + 1, "Unsaved draft requires an explicit navigation decision");
  assert.match(b.dialogs.at(-1).message, /unsaved|draft/i);
  await b.handleDialog(accept);
  await operation;
}
async function attemptSection(b, section, child) {
  const parent = `.section-navigation-group:has([data-workspace-link="${section}/${child}"]) .nav-section-toggle`;
  if (!await b.evaluate(`document.querySelector(${JSON.stringify(parent)})?.getAttribute("aria-expanded")==="true"`)) {
    await b.click(parent);
  }
  await b.click(`[data-workspace-link="${section}/${child}"]`);
}

test("real session: every privileged child, scoped BU/theme, section/function Favorite persistence and direct denial", async () => {
  const b = await browserHarness({ realBackendOrigin, allowedRealWrites: [
    "POST /api/auth/login/", "POST /api/favorites/", "DELETE /api/favorites/procurement.progress/",
    `POST /api/units/${fixture.unit}/select/`, `POST /api/units/${fixture.roche}/select/`,
  ] });
  try {
    await login(b, "synthetic_browser_operator");
    await selectUnit(b, fixture.roche);
    await b.wait('document.documentElement.dataset.unitTheme==="roche"');
    await selectUnit(b, fixture.unit);
    await b.wait('document.documentElement.dataset.unitTheme==="tivoli"');
    await openFunction(b, "inventory", "main");
    // All rendered sidebar children come from actual authenticated server context.
    const destinations = await b.evaluate(`Array.from(document.querySelectorAll("[data-workspace-link]"))
      .map(n=>n.dataset.workspaceLink)`);
    assert.deepEqual([...new Set(destinations)].sort(), Object.keys(navigationContent).sort(),
      "All 49 current child destinations require actual content witnesses");
    const contentEvidence = [];
    for (const route of destinations) {
      const [section, child] = route.split("/");
      const label = await b.evaluate(`document.querySelector('[data-workspace-link="${route}"]').textContent.trim()`);
      await openFunction(b, section, child);
      assert.equal(await b.evaluate("location.hash"), `#/${route}`);
      assert.equal(await b.evaluate('!!document.querySelector("[data-workspace-unavailable]")'), false, route);
      assert.equal(await b.evaluate('document.querySelector(".page-heading h1").textContent.trim()'), label, route);
      if (child !== "main" && await isVisible(b, "#purchasing-workbench-title")) {
        assert.equal(await b.evaluate('[...document.querySelectorAll("#purchasing-workbench-title")].find(n=>n.getClientRects().length).textContent.trim()'), label,
          `${route}: shared function heading matches the selected sidebar child`);
      }
      const witness = navigationContent[route];
      try {
        await b.wait(`[...document.querySelectorAll(${JSON.stringify(witness)})].some(n=>n.getClientRects().length)`);
      } catch (error) {
        throw new Error(`${route}: visible content witness ${witness} failed`, { cause: error });
      }
      assert.equal(await isVisible(b, witness), true, `${route}: actual focused function content`);
      if (route === "hr/main") {
        assert.match(await b.evaluate('document.querySelector(".planned-panel").innerText'), /PLANNED.*NOT IMPLEMENTED/);
        assert.equal(await isVisible(b, ".pw-transactions"), false);
      }
      if (["delivery/quotes", "delivery/storage", "delivery/reservations", "delivery/deliveries"].includes(route)) {
        assert.equal(await b.evaluate('[...document.querySelectorAll(".pr-tabs [aria-current=page]")].filter(n=>n.getClientRects().length).length'), 1);
      }
      if (["retail", "procurement", "delivery", "purchasing"].includes(section)) {
        assert.equal(await isVisible(b, ".pw-overview-launcher"), child === "main", route);
      }
      if (route !== "purchasing/transactions") {
        for (const exclusive of ["#pw-receipt-title", "#pw-commitment-create-title",
          "#pw-commitment-progress-title", "#pw-create-quote-title", "#pw-quote-actions-title", ".packing-preview"]) {
          assert.equal(await isVisible(b, exclusive), exclusive === witness,
            `${route}: unrelated action forms must not be visible (${exclusive})`);
        }
      }
      contentEvidence.push({ route, label, witness, result: "PASS", backend: "real authenticated synthetic" });
    }
    await mkdir("generated-artifacts/real-navigation-browser", { recursive: true });
    await writeFile("generated-artifacts/real-navigation-browser/content-matrix.json", JSON.stringify(contentEvidence, null, 2));
    // The contextual control uses the same guarded router and actual manager GET.
    await openFunction(b, "governance", "reporting");
    assert.doesNotMatch(await b.evaluate('document.querySelector(".governance-report-boundary").innerText'),
      /Panel management is not part of this account/);
    await b.click(".governance-report-boundary button");
    await b.wait('location.hash==="#/governance/report-panels" && !!document.querySelector(".report-panel-manager")');
    await b.history(-1);
    await b.wait('location.hash==="#/governance/reporting"');
    await b.history(1);
    await b.wait('location.hash==="#/governance/report-panels"');
    await openFunction(b, "procurement", "progress");
    await openFunction(b, "procurement", "commitments");
    await b.history(-1);
    await b.wait('location.hash==="#/procurement/progress" && document.querySelector(\'[data-workspace-link="procurement/progress"]\')?.getAttribute("aria-current")==="page"');
    await b.history(1);
    await b.wait('location.hash==="#/procurement/commitments"');
    await openFunction(b, "procurement", "progress");
    assert.equal(await isVisible(b, "#pw-create-quote-title"), false);
    const star = '[data-workspace-link="procurement/progress"] + .favorite-toggle';
    await b.click(star);
    await b.wait(`document.querySelector(${JSON.stringify(star)})?.getAttribute("aria-pressed") === "true"`);
    await b.reload();
    await b.wait(`document.querySelector(${JSON.stringify(star)})?.getAttribute("aria-pressed") === "true"`);
    await b.wait('!!document.querySelector(".my-page-link")');
    await b.click(".my-page-link");
    await b.wait('!!document.querySelector(".my-favorite-open")');
    await b.click(".my-favorite-open");
    await b.wait('location.hash === "#/procurement/progress"');
    await b.click(star);
    await b.wait(`document.querySelector(${JSON.stringify(star)})?.getAttribute("aria-pressed") === "false"`);
    const forbidden = await api(b, `/api/stock-balances/?unit=${fixture.unit}&location=999999`);
    assert.equal(forbidden.status, 403);
    b.assertSafe();
    await recordTrace(b, "destinations");
  } finally { await b.close(); }
});

test("real manager session: contextual management navigation is keyboard usable on a 340px screen", async () => {
  const b = await browserHarness({ realBackendOrigin, width: 340, height: 900,
    allowedRealWrites: ["POST /api/auth/login/"] });
  try {
    await login(b, "synthetic_browser_operator");
    await openFunction(b, "governance", "reporting");
    await b.evaluate('document.querySelector(".governance-report-boundary button").focus()');
    assert.equal(await b.evaluate(`(() => {
      const n = document.activeElement;
      const s = getComputedStyle(n);
      return n.textContent.trim()==="Manage report panels" && (s.outlineStyle!=="none" || s.boxShadow!=="none");
    })()`), true, "Contextual management control has visible keyboard focus");
    await b.key("Enter");
    await b.wait('location.hash==="#/governance/report-panels" && !!document.querySelector(".report-panel-manager")');
    assert.equal(await b.evaluate('document.querySelector(".page-heading h1").textContent'), "Manage report panels");
    assert.equal(await b.evaluate("document.documentElement.scrollWidth<=innerWidth"), true);
    await b.history(-1);
    await b.wait('location.hash==="#/governance/reporting"');
    assert.equal(await isVisible(b, ".governance-report-boundary button"), true);
    assert.equal(b.calls.some(c=>c.path.includes("/api/purchasing/") && c.method==="POST"), false);
    assert.equal(b.unexpected.length, 0);
    assert.equal(b.errors.length, 0);
    await b.screenshot("real-governance-reporting-mobile", "real-navigation-browser");
    await recordTrace(b, "governance-mobile");
  } finally { await b.close(); }
});

test("real session: draft decision and pending collection cannot silently unmount or duplicate; delivery journey", async () => {
  let release;
  let held = false;
  const gate = new Promise(resolve => { release = resolve; });
  const paymentPath = `/api/purchasing/quotes/${fixture.quote_id}/payments/`;
  const b = await browserHarness({ realBackendOrigin, allowedRealWrites: [
    "POST /api/auth/login/", `POST ${paymentPath}`,
    `POST /api/units/${fixture.unit}/select/`,
    `POST /api/purchasing/quotes/${fixture.quote_id}/ready/`,
    `POST /api/purchasing/quotes/${fixture.quote_id}/deliver/`,
  ], beforeRealResponse: async call => {
    if (call.method === "POST" && call.path === paymentPath) { held = true; await gate; }
  } });
  try {
    await login(b, "demo_multi");
    await chooseQuote(b);
    const populatedStock = await api(b, `/api/stock-balances/?unit=${fixture.unit}`);
    assert.equal(populatedStock.status, 200);
    assert.ok(populatedStock.body.results.length > 0, "Cost masking is checked on actual populated ledger rows");
    assert.equal(/unit_cost|landed_cost|inventory_value|purchase_value|supplier|gross_margin/.test(JSON.stringify(populatedStock.body)), false);
    await b.click(paymentInput); await b.text("4000.00");
    await dialog(b, false, () => attemptSection(b, "inventory", "main"));
    assert.equal(await b.evaluate("location.hash"), "#/retail/actions");
    assert.equal(await b.evaluate(`document.querySelector(${JSON.stringify(paymentInput)}).value`), "4000.00");
    await dialog(b, false, () => b.history(-1));
    assert.equal(await b.evaluate("location.hash"), "#/retail/actions");
    assert.equal(await b.evaluate(`document.querySelector(${JSON.stringify(paymentInput)}).value`), "4000.00",
      "Cancelled browser Back retains the actual draft as well as sidebar cancellation");
    // Actual backend commits the payment; the proxy delays only its real response.
    await b.click('form:has(option[value="simulated_cash"]) button[type=submit]');
    for (let i = 0; i < 150 && !held; i++) await delay(20);
    assert.ok(held, "Actual payment response reached the controlled transport gate");
    await attemptSection(b, "inventory", "main");
    assert.equal(await b.evaluate("location.hash"), "#/retail/actions");
    assert.match(await b.evaluate('document.querySelector("[data-navigation-warning]")?.textContent ?? ""'), /pending|finish/i);
    assert.equal(b.calls.filter(c => c.method === "POST" && c.path === paymentPath).length, 1);
    release();
    await b.wait('document.querySelector(".pw-quote .pw-status")?.textContent === "confirmed"');
    assert.equal(b.calls.filter(c => c.method === "POST" && c.path === paymentPath).length, 1);
    // Explicitly discard the now-completed form draft when moving sections.
    await dialog(b, true, () => attemptSection(b, "delivery", "handover"));
    await b.click("#pw-quote-id"); await b.text(String(fixture.quote_id)); await b.key("Enter");
    await b.wait('[...document.querySelectorAll("button")].some(n=>n.textContent==="Record readiness")');
    await b.evaluate('[...document.querySelectorAll("button")].find(n=>n.textContent==="Record readiness").closest("form").querySelector("input").focus()');
    await b.text("TEST ONLY readiness; recorded intent, no transport");
    await b.evaluate('[...document.querySelectorAll("button")].find(n=>n.textContent==="Record readiness").focus()');
    await b.key("Enter");
    await b.wait('document.querySelector(".pw-quote .pw-status")?.textContent === "ready"');
    await b.wait('[...document.querySelectorAll("button")].some(n=>n.textContent==="Record delivery" && !n.disabled)');
    await b.evaluate(`[...document.querySelectorAll("button")].find(n=>n.textContent==="Record delivery").focus()`);
    await b.key("Enter");
    await b.wait('document.querySelector(".pw-quote .pw-status")?.textContent === "delivered"');
    const result = await api(b, `/api/purchasing/quotes/${fixture.quote_id}/`);
    assert.equal(result.status, 200);
    assert.equal((result.body.quote ?? result.body).status, "delivered");
    await b.evaluate('document.querySelector(".pw-quote").scrollIntoView({block:"start"})');
    await b.screenshot("real-delivered", "real-navigation-browser");
    b.assertSafe();
    await recordTrace(b, "delivery");
  } finally { release(); await b.close(); }
});

test("real brand-only session: cross-BU, order, capacity and manager denial, cost allowlist and old section Favorite", async () => {
  const b = await browserHarness({ realBackendOrigin, width: 390, allowedRealWrites: ["POST /api/auth/login/"] });
  try {
    await login(b, "demo_roche");
    const units = await b.evaluate('[...document.querySelector(\'[aria-label="Business unit"]\').options].map(n=>n.value)');
    assert.deepEqual(units, [String(fixture.roche)]);
    const favorites = await api(b, "/api/favorites/");
    assert.ok(favorites.body.favorites.some(f => f.destination === "inventory"));
    assert.equal(favorites.body.favorites.some(f => f.destination === "procurement.progress"), false);
    assert.equal((await api(b, `/api/stock-balances/?unit=${fixture.unit}`)).status, 403);
    assert.equal((await api(b, `/api/purchasing/quotes/${fixture.quote_id}/`)).status, 404);
    assert.equal((await api(b, "/api/governance/report-panels/")).status, 403);
    assert.equal((await api(b, "/api/governance/warehouse-capacity/audit/")).status, 403);
    const stock = await api(b, `/api/stock-balances/?unit=${fixture.roche}`);
    assert.equal(stock.status, 200);
    assert.equal(/landed_cost|unit_cost|inventory_value|supplier/.test(JSON.stringify(stock.body)), false);
    await b.click(".my-page-link");
    await b.wait('!!document.querySelector(".my-favorite-open")');
    await b.click(".my-favorite-open");
    await b.wait('location.hash === "#/inventory/main"');
    assert.equal(await b.evaluate("document.documentElement.dataset.unitTheme"), "roche");
    await b.screenshot("real-brand-scope-mobile", "real-navigation-browser");
    b.assertSafe();
    await recordTrace(b, "brand-scope");
  } finally { await b.close(); }
});
