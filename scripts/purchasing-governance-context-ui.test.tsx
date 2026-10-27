import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { after, afterEach, beforeEach, test } from "node:test";
import { act, Profiler } from "react";
import { createRoot } from "react-dom/client";

// Test-only transparent wrapper: record committed props, then render the real
// transaction component. No app instrumentation, stub child, or React internals.
const probeURL = "test:purchasing-governance-probe";
const probeHook = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL?.endsWith("/src/PurchasingWorkbench.tsx")
      && specifier === "./PurchasingTransactions") return { url: probeURL, shortCircuit: true };
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url !== probeURL) return nextLoad(url, context);
    return { format: "module", shortCircuit: true, source: `
      import { createElement, useLayoutEffect } from ${JSON.stringify(import.meta.resolve("react"))};
      import Actual from ${JSON.stringify(new URL("../src/PurchasingTransactions.tsx", import.meta.url).href)};
      export default function TransactionsProbe(props) {
        useLayoutEffect(() => globalThis.__purchasingGovernanceProbe(props));
        return createElement(Actual, props);
      }
    ` };
  },
});
const { default: PurchasingWorkbench } = await import("../src/PurchasingWorkbench");
after(() => probeHook.deregister());

const originalFetch = globalThis.fetch;
const username = "TEST_ONLY_configuration_staff";
let root, host, requests, calls, commits, childProps, warnings, originalError, originalWarn;
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
function configuration(unit, version = 1) {
  const letter = unit === 1 ? "A" : "B";
  return {
    test_only_label: `TEST ONLY ${letter} configuration v${version}`,
    business_unit: { id: unit, code: `TEST_${letter}`, name: `TEST ONLY Unit ${letter}` },
    role_assignments: [
      { id: unit * 100 + version, user_id: unit, username: `TEST_ONLY_${letter}_ROLE_v${version}`, role: "manager" },
      { id: unit * 100 + 50, user_id: 99, username, role: "manager" },
    ],
    current_prices: [{ id: unit * 10 + version, variant_id: unit * 10,
      sku: `TEST_ONLY_${letter}_PRICE_v${version}`, amount: `${unit}${version}00.00`,
      currency: "QAR", revision: version, approved_by: 99 }],
    work_calendar: { is_configured: true, closed_weekdays: [4],
      closed_dates: [`2031-0${unit}-${version === 1 ? "10" : "24"}`] },
    storage_exception_approver_roles: [],
    special_order_value_bands: [], special_order_bands_used_for_approval: false,
    special_order_bands_note: "TEST ONLY unconfigured; no company policy inferred",
  };
}
function markers(unit, version = 1) {
  const data = configuration(unit, version);
  return [data.role_assignments[0].username, data.current_prices[0].sku, data.work_calendar.closed_dates[0]];
}
function quotationChoices(unit) {
  const data = configuration(unit, 1);
  const letter = unit === 1 ? "A" : "B";
  return {
    business_unit: data.business_unit,
    current_prices: [{ ...data.current_prices[0], sku: `TEST_ONLY_${letter}_QUOTE_PRICE`, approved_by: null, item: {
      item_code: `TEST_ONLY_${letter}_QUOTE_PRICE`, name_en: `TEST ONLY ${letter} quote item`, name_ar: "", category_en: "TEST category",
      category_ar: "", description_en: "", description_ar: "", image_path: null,
    }, stock: [] }],
    customers: [{ counterparty_id: 2, code: "TEST_ONLY_CUSTOMER", display_name: "TEST ONLY quote customer",
      party_type: "customer", contact_en: "", contact_ar: "", name_ar: "" }],
    role_assignments: [{ id: unit * 100 + 50, user_id: 99, username, role: "manager" }],
    discount_ceiling: "20", work_calendar: data.work_calendar, test_only_label: "TEST ONLY choices",
  };
}
function snapshot() {
  assert.ok(childProps, "Real transaction child's committed props were observed");
  return {
    text: host.textContent,
    governance: host.querySelector(".pw-governance-card")?.textContent ?? "",
    ...childProps,
  };
}
async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
async function renderUnit(unit, staff = true) {
  await act(async () => root.render(
    <Profiler id="governance-workbench" onRender={() => commits.push(snapshot())}>
      <PurchasingWorkbench selectedUnitId={unit}
        selectedUnitName={`TEST ONLY Unit ${unit === 1 ? "A" : "B"}`}
        username={username} isStaff={staff} csrfToken="TEST_ONLY_CSRF"
        locations={[{ id: unit * 11, code: "TEST", name: "TEST ONLY receiving location", parent_id: null }]}
        onOpenReports={() => {}}
      />
    </Profiler>,
  ));
  await flush();
}
async function answer(request, body = configuration(request.unit), status = 200) {
  assert.ok(request);
  assert.equal(request.settled, false);
  await act(async () => {
    request.settled = true;
    request.resolve(json(body, status));
  });
  await flush();
}
function assertNoConfiguration(state, forbidden = markers(1)) {
  assert.equal(state.configuration, null, "Never pass an old configuration to the transaction child");
  assert.notEqual(state.configurationState, "ready");
  for (const marker of forbidden) assert.equal(state.text.includes(marker), false, marker);
}
function assertReady(unit, version = 1) {
  const state = snapshot();
  assert.equal(state.selectedUnitId, unit);
  assert.equal(state.configurationState, "ready");
  assert.deepEqual(state.configuration, configuration(unit, version));
  for (const marker of markers(unit, version)) assert.ok(state.governance.includes(marker), marker);
  assert.ok(host.querySelector("#pw-quote-variant").textContent.includes(quotationChoices(unit).current_prices[0].sku));
}
function assertNoAInBCommits() {
  for (const state of commits.filter((commit) => commit.selectedUnitId === 2)) {
    for (const marker of markers(1)) {
      assert.equal(state.text.includes(marker), false);
      assert.equal(JSON.stringify(state.configuration).includes(marker), false);
    }
    if (state.configuration) assert.equal(state.configuration.business_unit.id, 2);
  }
}
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  requests = []; calls = []; commits = []; childProps = null; warnings = [];
  originalError = console.error; originalWarn = console.warn;
  console.error = console.warn = (...args) => warnings.push(args.map(String).join(" "));
  globalThis.__purchasingGovernanceProbe = (props) => {
    childProps = {
      selectedUnitId: props.selectedUnitId, isStaff: props.isStaff,
      configurationState: props.configurationState,
      configuration: JSON.parse(JSON.stringify(props.configuration)),
    };
  };
  globalThis.fetch = (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, options });
    assert.equal(options.method, "GET", "No transaction, grant or configuration writes are allowed");
    assert.equal(options.credentials, "same-origin");
    if (/^\/api\/governance\/purchasing\/\d+\/$/.test(url.pathname)) {
      let resolve;
      const promise = new Promise((done) => { resolve = done; });
      requests.push({ unit: Number(url.pathname.split("/").at(-2)), resolve, settled: false });
      return promise;
    }
    if (url.pathname === "/api/purchasing/quotation-choices/") {
      return Promise.resolve(json(quotationChoices(Number(url.searchParams.get("business_unit_id")))));
    }
    const pagination = { page: 1, page_size: 20, total_count: 0, total_pages: 1, previous_page: null, next_page: null };
    if (url.pathname === "/api/governance/purchasing/counterparties/") {
      const supplier = url.searchParams.get("party_type") === "supplier";
      return Promise.resolve(json({ rows: [{ counterparty_id: supplier ? 1 : 2,
        party_type: supplier ? "supplier" : "customer", code: "TEST_ONLY", name: "TEST ONLY counterparty" }],
      pagination: { ...pagination, total_count: 1 } }));
    }
    if (["/api/purchasing/quotes/", "/api/purchasing/storage-charges/"].includes(url.pathname)) {
      return Promise.resolve(json({ rows: [], pagination }));
    }
    if ([
      "/api/reports/purchase_history/receipts/", "/api/reports/inventory/reservations/",
      "/api/reports/sales/deliveries/",
    ].includes(url.pathname)) return Promise.resolve(json({ detail: "TEST ONLY unrelated report unavailable" }, 403));
    throw new Error(`Unexpected fixture GET ${url.pathname}`);
  };
});
afterEach(async () => {
  try {
    await act(async () => root.unmount());
    await act(async () => {
      for (const request of requests) if (!request.settled) {
        request.settled = true; request.resolve(json(configuration(request.unit)));
      }
      await new Promise(setImmediate);
    });
  } finally {
    host.remove(); globalThis.fetch = originalFetch;
    delete globalThis.__purchasingGovernanceProbe;
    console.error = originalError; console.warn = originalWarn;
  }
  assert.deepEqual(warnings, []);
});

test("first B commit passes no A prices roles or calendar to real transactions and displays no A configuration", async () => {
  await renderUnit(1); await answer(requests[0]); assertReady(1);
  const transactionRoot = host.querySelector(".pw-transactions");
  await renderUnit(2);
  const firstB = commits.find((state) => state.selectedUnitId === 2);
  assert.ok(firstB);
  assertNoConfiguration(firstB);
  assertNoConfiguration(snapshot());
  assert.equal(host.querySelector(".pw-transactions") === transactionRoot, true);
  await answer(requests[1]);
  assertReady(2); assertNoAInBCommits();
});

for (const outcome of ["success", "denial"]) {
  test(`delayed A configuration ${outcome} cannot replace B authorized configuration or child props`, async () => {
    await renderUnit(1);
    const old = requests[0];
    await renderUnit(2); await answer(requests[1]); assertReady(2);
    await answer(old, outcome === "success" ? configuration(1) : { detail: "TEST ONLY obsolete A denied" },
      outcome === "success" ? 200 : 403);
    assertReady(2); assertNoAInBCommits();
    assert.equal(host.textContent.includes("TEST ONLY obsolete A denied"), false);
    assert.equal(host.querySelector(".pw-governance-card [role='alert']") === null, true);
  });
}

test("B configuration denial leaves no A values in the first commit final display or transaction props", async () => {
  await renderUnit(1); await answer(requests[0]); assertReady(1);
  await renderUnit(2);
  assertNoConfiguration(commits.find((state) => state.selectedUnitId === 2));
  await answer(requests[1], { detail: "TEST ONLY B configuration denied" }, 403);
  assertNoConfiguration(snapshot()); assertNoAInBCommits();
  assert.equal(snapshot().configurationState, "error");
  assert.ok(host.querySelector(".pw-governance-card [role='alert']").textContent.includes("TEST ONLY B configuration denied"));
  assert.equal(host.querySelector("#pw-quote-variant").disabled, true);
});

test("same-BU staff-context refresh hides prior configuration and loads latest authorized prices roles and calendar", async () => {
  await renderUnit(1); await answer(requests[0]); assertReady(1);
  const count = commits.length;
  const transactionRoot = host.querySelector(".pw-transactions");
  await renderUnit(1, false);
  const firstNonstaff = commits.slice(count).find((state) => !state.isStaff);
  assertNoConfiguration(firstNonstaff);
  assert.ok(firstNonstaff.governance.includes("Staff access required"));
  assert.equal(requests.length, 1, "No governance request for a nonstaff context");
  const refreshStart = commits.length;
  await renderUnit(1, true);
  assert.equal(requests.length, 2);
  assertNoConfiguration(commits[refreshStart]);
  assert.equal(snapshot().configurationState, "loading");
  await answer(requests[1], configuration(1, 2));
  assertReady(1, 2);
  for (const marker of markers(1)) assert.equal(host.textContent.includes(marker), false);
  assert.equal(host.querySelector(".pw-transactions") === transactionRoot, true);
});

test("same-BU Retry configuration control requests and loads the latest authorized response", async () => {
  await renderUnit(1);
  await answer(requests[0], { detail: "TEST ONLY transient configuration failure" }, 500);
  assertNoConfiguration(snapshot());
  const retry = [...host.querySelectorAll(".pw-governance-card button")]
    .find((button) => button.textContent === "Retry configuration");
  assert.ok(retry);
  await act(async () => retry.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await flush();
  assert.equal(requests.length, 2); assert.equal(requests[1].unit, 1);
  assert.equal(snapshot().configurationState, "loading");
  assertNoConfiguration(snapshot());
  await answer(requests[1], configuration(1, 2));
  assertReady(1, 2);
  assert.equal(host.textContent.includes("TEST ONLY transient configuration failure"), false);
});
