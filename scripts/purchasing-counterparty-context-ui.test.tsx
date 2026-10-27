import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, Profiler } from "react";
import { createRoot } from "react-dom/client";
import PurchasingWorkbench from "../src/PurchasingWorkbench";

// Every request terminates here; this suite forbids even mocked POSTs.
const originalFetch = globalThis.fetch;
const username = "TEST_ONLY_directory_viewer";
let root, host, requests, calls, commits, warnings, originalError, originalWarn;
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const page = (rows = []) => ({ rows, pagination: {
  page: 1, page_size: 100, total_count: rows.length, total_pages: 1, previous_page: null, next_page: null,
} });
function directory(type, version) {
  return page([{ counterparty_id: type === "supplier" ? 1 : 2, party_type: type,
    code: `TEST_ONLY_${version}_${type}`, name: `TEST ONLY ${version} ${type}` }]);
}
function configuration() {
  return {
    test_only_label: "TEST ONLY mocked configuration, not company policy",
    business_unit: { id: 1, code: "TEST_A", name: "TEST ONLY Unit A" },
    role_assignments: [{ id: 1, user_id: 1, username, role: "manager" }],
    current_prices: [{ id: 1, variant_id: 10, sku: "TEST_ONLY_CHAIR", amount: "1000.00",
      currency: "QAR", revision: 1, approved_by: 1 }],
    work_calendar: { is_configured: true, closed_weekdays: [4], closed_dates: [] },
    storage_exception_approver_roles: [], special_order_value_bands: [],
    special_order_bands_used_for_approval: false, special_order_bands_note: "TEST ONLY unconfigured",
  };
}
function quotationChoices() {
  return {
    business_unit: { id: 1, code: "TEST_A", name: "TEST ONLY Unit A" },
    current_prices: [{ id: 1, variant_id: 10, sku: "TEST_ONLY_CHAIR", amount: "1000.00", currency: "QAR",
      revision: 1, approved_by: null, item: { item_code: "TEST_ONLY_CHAIR", name_en: "TEST chair",
        name_ar: "", category_en: "TEST category", category_ar: "", description_en: "", description_ar: "", image_path: null }, stock: [] }],
    customers: [{ counterparty_id: 2, code: "TEST_QUOTE_CUSTOMER", display_name: "TEST ONLY quote customer",
      party_type: "customer", contact_en: "", contact_ar: "", name_ar: "" }],
    role_assignments: [{ id: 1, user_id: 1, username, role: "manager" }],
    discount_ceiling: "20", work_calendar: { is_configured: false, closed_weekdays: [], closed_dates: [] },
    test_only_label: "TEST ONLY quote choices",
  };
}
function receiptForm() {
  return host.querySelector(".pw-transactions form");
}
function snapshot() {
  const options = (selector) => [...host.querySelectorAll(`${selector} option`)]
    .filter((option) => option.value).map((option) => `${option.value}:${option.textContent}`);
  return {
    suppliers: options("#pw-receipt-supplier"),
    customers: options("#pw-quote-customer"),
    receiptDisabled: receiptForm().querySelector('button[type="submit"]').disabled,
    supplierDisabled: host.querySelector("#pw-receipt-supplier").disabled,
    customerDisabled: host.querySelector("#pw-quote-customer").disabled,
  };
}
function assertCleared(state = snapshot()) {
  assert.deepEqual({
    suppliers: state.suppliers,
    receiptDisabled: state.receiptDisabled, supplierDisabled: state.supplierDisabled,
  }, { suppliers: [], receiptDisabled: true, supplierDisabled: true },
  "No staff directory choices or receiving availability from a previous visibility context");
  assert.ok(state.customers.length === 0
    || state.customers[0] === "2:TEST_QUOTE_CUSTOMER · TEST ONLY quote customer",
  "Only fresh dedicated quotation choices may appear; never prior staff-directory customers");
}
function assertAllowed(version) {
  const expectedOption = (type) => {
    const row = directory(type, version).rows[0];
    return `${row.counterparty_id}:${row.code} · ${row.name}`;
  };
  assert.deepEqual(snapshot(), {
    suppliers: [expectedOption("supplier")], customers: ["2:TEST_QUOTE_CUSTOMER · TEST ONLY quote customer"],
    receiptDisabled: false, supplierDisabled: false, customerDisabled: false,
  });
}
async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
async function renderStaff(staff) {
  await act(async () => root.render(
    <Profiler id="counterparty-visibility" onRender={() => commits.push({ staff, state: snapshot() })}>
      <PurchasingWorkbench selectedUnitId={1} selectedUnitName="TEST ONLY Unit A"
        username={username} isStaff={staff} csrfToken="TEST_ONLY_CSRF"
        locations={[{ id: 11, code: "TEST", name: "TEST ONLY location", parent_id: null }]}
        onOpenReports={() => {}}
      />
    </Profiler>,
  ));
  await flush();
}
async function answer(request, version = "old", status = 200) {
  assert.ok(request);
  assert.equal(request.settled, false);
  await act(async () => {
    request.settled = true;
    request.resolve(json(status === 200 ? directory(request.type, version)
      : { detail: `TEST ONLY ${version} directory denied` }, status));
  });
  await flush();
}
async function allowPair(pair, version = "old") {
  for (const request of pair) await answer(request, version);
}
async function setValue(element, value) {
  assert.ok(element);
  const prototype = element.tagName === "SELECT" ? window.HTMLSelectElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, String(value));
    element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}
function control(label) {
  return [...receiptForm().querySelectorAll("label")]
    .find((element) => element.textContent.startsWith(label))?.querySelector("input,select");
}

beforeEach(() => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  requests = []; calls = []; commits = []; warnings = [];
  originalError = console.error; originalWarn = console.warn;
  console.error = console.warn = (...args) => warnings.push(args.map(String).join(" "));
  globalThis.fetch = (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, options });
    assert.equal(options.method, "GET", "No transaction or configuration submissions");
    assert.equal(options.credentials, "same-origin");
    if (url.pathname === "/api/governance/purchasing/counterparties/") {
      assert.equal(url.searchParams.has("business_unit_id"), false, "Directory scope remains global staff-only");
      assert.equal(url.searchParams.get("page"), "1");
      assert.equal(url.searchParams.get("page_size"), "100");
      let resolve;
      const promise = new Promise((done) => { resolve = done; });
      requests.push({ type: url.searchParams.get("party_type"), settled: false, resolve });
      return promise;
    }
    if (url.pathname === "/api/governance/purchasing/1/") return Promise.resolve(json(configuration()));
    if (url.pathname === "/api/purchasing/quotation-choices/") return Promise.resolve(json(quotationChoices()));
    if (["/api/purchasing/quotes/", "/api/purchasing/storage-charges/"].includes(url.pathname)) {
      return Promise.resolve(json(page()));
    }
    if ([
      "/api/reports/purchase_history/receipts/", "/api/reports/inventory/reservations/",
      "/api/reports/sales/deliveries/",
    ].includes(url.pathname)) return Promise.resolve(json({ detail: "TEST ONLY report unavailable" }, 403));
    throw new Error(`Unexpected fixture GET ${url.pathname}`);
  };
});
afterEach(async () => {
  try {
    await act(async () => root.unmount());
    await act(async () => {
      for (const request of requests) if (!request.settled) {
        request.settled = true; request.resolve(json(directory(request.type, "cleanup")));
      }
      await new Promise(setImmediate);
    });
  } finally {
    host.remove(); globalThis.fetch = originalFetch;
    console.error = originalError; console.warn = originalWarn;
  }
  assert.deepEqual(warnings, []);
  assert.equal(calls.every(({ options }) => options.method === "GET"), true);
});

test("first nonstaff commit hides both directories and gates receiving; restoration waits for fresh choices without losing drafts", async () => {
  await renderStaff(true); await allowPair(requests.slice()); assertAllowed("old");
  const form = receiptForm();
  const transactions = host.querySelector(".pw-transactions");
  await setValue(host.querySelector("#pw-receipt-supplier"), 1);
  await setValue(host.querySelector("#pw-quote-customer"), 2);
  await setValue(control("Catalog variant ID"), 10);
  await setValue(control("Receiving location"), 11);
  await setValue(control("Quantity"), "3");
  await setValue(control("Supplier unit cost"), "1250.00");
  const start = commits.length;
  await renderStaff(false);
  assertCleared(commits.slice(start).find((commit) => !commit.staff).state);
  for (const commit of commits.slice(start)) assertCleared(commit.state);
  assert.equal(requests.length, 2, "Nonstaff must not request the directory");
  const restored = commits.length;
  await renderStaff(true);
  assertCleared(commits[restored].state);
  assertCleared();
  assert.equal(requests.length, 4);
  await answer(requests[2], "fresh");
  assertCleared(); // Neither partial nor cached choices while the pair is loading.
  await answer(requests[3], "fresh"); assertAllowed("fresh");
  assert.equal(receiptForm() === form, true);
  assert.equal(host.querySelector(".pw-transactions") === transactions, true);
  assert.equal(host.querySelector("#pw-receipt-supplier").value, "1");
  assert.equal(host.querySelector("#pw-quote-customer").value, "2");
  assert.equal(control("Catalog variant ID").value, "10");
  assert.equal(control("Receiving location").value, "11");
  assert.equal(control("Quantity").value, "3");
  assert.equal(control("Supplier unit cost").value, "1250.00");
  assert.equal(host.textContent.includes("TEST_ONLY_old_"), false);
});

for (const deniedType of ["supplier", "customer"]) {
  test(`restored staff ${deniedType} denial leaves no old or partial fresh directory choices`, async () => {
    await renderStaff(true); await allowPair(requests.slice()); assertAllowed("old");
    await renderStaff(false);
    await renderStaff(true); assertCleared();
    const pair = requests.slice(2);
    await answer(pair.find((request) => request.type === deniedType), "fresh", 403);
    assertCleared();
    await answer(pair.find((request) => request.type !== deniedType), "fresh");
    assertCleared();
    assert.ok(host.querySelector(".pw-transactions").textContent.includes("directory could not be verified"));
    assert.equal(host.textContent.includes("TEST_ONLY_old_"), false);
    assert.equal(host.textContent.includes("TEST_ONLY_fresh_"), false);
  });
}

for (const outcome of ["success", "denial"]) {
  test(`delayed prior directory ${outcome} cannot restore choices after staff visibility is removed`, async () => {
    await renderStaff(true);
    const old = requests.slice();
    await renderStaff(false); assertCleared();
    const start = commits.length;
    for (const request of old) await answer(request, "obsolete", outcome === "success" ? 200 : 403);
    for (const commit of commits.slice(start)) assertCleared(commit.state);
    assertCleared();
    assert.equal(requests.length, 2);
    assert.equal(host.textContent.includes("TEST_ONLY_obsolete_"), false);
    assert.equal(host.textContent.includes("directory could not be verified"), false);
  });
}

for (const outcome of ["success", "denial"]) {
  test(`delayed prior directory ${outcome} cannot replace fresh choices after staff visibility is restored`, async () => {
    await renderStaff(true);
    const old = requests.slice();
    await renderStaff(false);
    await renderStaff(true); assertCleared();
    await allowPair(requests.slice(2), "fresh"); assertAllowed("fresh");
    const expected = snapshot();
    const start = commits.length;
    for (const request of old) await answer(request, "obsolete", outcome === "success" ? 200 : 403);
    for (const commit of commits.slice(start)) assert.deepEqual(commit.state, expected);
    assertAllowed("fresh");
    assert.equal(host.textContent.includes("TEST_ONLY_obsolete_"), false);
    assert.equal(host.textContent.includes("directory could not be verified"), false);
  });
}

test("a nonstaff mount never requests or displays staff-only supplier or customer choices", async () => {
  await renderStaff(false);
  assertCleared();
  assert.equal(requests.length, 0);
  assert.equal(calls.some(({ url }) => url.pathname === "/api/governance/purchasing/1/"), false);
});
