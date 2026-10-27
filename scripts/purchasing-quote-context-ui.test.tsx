import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, Profiler } from "react";
import { createRoot } from "react-dom/client";
import PurchasingWorkbench from "../src/PurchasingWorkbench";

// Fictional HTTP fixtures only; no real account, transaction or configuration writes.
const nativeFetch = globalThis.fetch;
let root, container, pending, calls, commits, delayed, denied;
let transactionNode, transactionForm, warnings, originalError, originalWarn;

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
function quote(unit) {
  const letter = unit === 1 ? "A" : "B";
  return {
    quote_id: unit === 1 ? 101 : 202, business_unit_id: unit, kind: "stock", status: "draft",
    customer_id: unit * 100, discount_percent: "0.00", total_list_value: "2000.00",
    total_discount: "0.00", total_quote_value: "2000.00", payment_plan: "full",
    downpayment_percent: null, special_order_value_bands_configured: false,
    lines: [{
      quote_line_id: unit * 1000, variant_id: unit * 10, quantity: "1.000",
      selling_price_snapshot: "2000.00", price_revision_snapshot: 1,
      discount_percent: "0.00", discount_amount: "0.00", net_unit_price: "2000.00",
      net_total: "2000.00", specifications: { description: `TEST ONLY ${letter} quote detail` },
    }],
    payments: [], approvals: [], holds: [], events: [],
  };
}
async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
function snapshot() {
  return {
    text: container.querySelector(".purchasing-workbench")?.textContent ?? "",
    input: container.querySelector("#pw-quote-id")?.value ?? "",
    detail: container.querySelector(".pw-quote")?.textContent ?? "",
    error: container.querySelector(".pw-quote-card [role='alert']")?.textContent ?? "",
    sameTransactions: !transactionNode || container.querySelector(".pw-transactions") === transactionNode,
    sameForm: !transactionForm || container.querySelector(".pw-transactions form") === transactionForm,
  };
}
async function renderUnit(unit) {
  await act(async () => root.render(
    <Profiler id="quote-workbench" onRender={() => commits.push({ unit, ...snapshot() })}>
      <PurchasingWorkbench
        selectedUnitId={unit} selectedUnitName={`TEST ONLY Unit ${unit === 1 ? "A" : "B"}`}
        isStaff={false} username="synthetic_quote_viewer" csrfToken="synthetic-placeholder"
        locations={[]} onOpenReports={() => {}}
      />
    </Profiler>,
  ));
  await flush();
  if (!transactionNode) {
    transactionNode = container.querySelector(".pw-transactions");
    transactionForm = container.querySelector(".pw-transactions form");
    assert.ok(transactionNode);
    assert.ok(transactionForm);
  }
  assert.equal(snapshot().sameTransactions, true, "Transaction component DOM is retained");
  assert.equal(snapshot().sameForm, true, "Transaction form is not remounted");
}
async function lookup(id) {
  const input = container.querySelector("#pw-quote-id");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, String(id));
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => container.querySelector(".pw-quote-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  ));
  await flush();
}
async function reply(request, response = request.response) {
  assert.ok(request, "Expected deferred quote request");
  await act(async () => request.resolve(response));
  await flush();
}
function assertNoA(state) {
  assert.notEqual(state.input, "101", "No A lookup ID under B");
  assert.equal(state.detail.includes("#101"), false);
  assert.equal(state.text.includes("TEST ONLY A quote detail"), false);
  assert.equal(state.error.includes("obsolete A"), false);
  assert.equal(state.sameTransactions, true);
  assert.equal(state.sameForm, true);
}
function assertFirstBCommit() {
  const first = commits.find((commit) => commit.unit === 2);
  assert.ok(first, "Profiler captures B before passive-effect resets");
  assert.ok(first.text.includes("TEST ONLY Unit B"));
  assertNoA(first);
  assert.equal(first.input, "");
  assert.equal(first.detail, "");
}
function assertBQuote() {
  const state = snapshot();
  assertNoA(state);
  assert.equal(state.input, "202");
  assert.ok(state.detail.includes("#202"));
  assert.ok(state.detail.includes("TEST ONLY B quote detail"));
  assert.equal(state.error, "");
}
async function loadA() {
  await renderUnit(1);
  await lookup(101);
  assert.equal(snapshot().input, "101");
  assert.ok(snapshot().detail.includes("#101"));
  assert.ok(snapshot().detail.includes("TEST ONLY A quote detail"));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  pending = []; calls = []; commits = []; delayed = new Set(); denied = new Set();
  transactionNode = null; transactionForm = null; warnings = [];
  originalError = console.error; originalWarn = console.warn;
  console.error = console.warn = (...args) => warnings.push(args.map(String).join(" "));
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, ...options });
    assert.equal(options.method, "GET", "These tests cannot write transactions or configuration");
    assert.equal(options.credentials, "same-origin");
    if (/^\/api\/purchasing\/quotes\/\d+\/$/.test(url.pathname)) {
      const unit = Number(url.searchParams.get("business_unit_id"));
      assert.ok(unit === 1 || unit === 2);
      assert.equal(url.pathname, `/api/purchasing/quotes/${unit === 1 ? 101 : 202}/`);
      assert.deepEqual(Object.keys(Object.fromEntries(url.searchParams)), ["business_unit_id"]);
      const response = denied.has(unit)
        ? json({ detail: "TEST ONLY B quote access denied." }, 403) : json(quote(unit));
      if (!delayed.has(unit)) return response;
      let resolve;
      const promise = new Promise((done) => { resolve = done; });
      pending.push({ url, response, resolve });
      return promise;
    }
    if (url.pathname === "/api/purchasing/quotes/" || url.pathname === "/api/purchasing/storage-charges/") {
      const unit = Number(url.searchParams.get("business_unit_id"));
      return denied.has(unit) ? json({ detail: "TEST ONLY order access denied." }, 403) : json({
        rows: [], pagination: { page: 1, page_size: 20, total_count: 0, total_pages: 1, previous_page: null, next_page: null },
      });
    }
    if ([
      "/api/reports/purchase_history/receipts/", "/api/reports/inventory/reservations/",
      "/api/reports/sales/deliveries/",
    ].includes(url.pathname)) return json({ detail: "TEST ONLY unrelated report capability unavailable." }, 403);
    throw new Error(`Unexpected request: ${url.pathname}`);
  };
});
afterEach(async () => {
  try {
    await act(async () => {
      for (const request of pending) request.resolve(request.response);
      await new Promise(setImmediate);
    });
    await act(async () => root.unmount());
  } finally {
    container.remove(); globalThis.fetch = nativeFetch;
    console.error = originalError; console.warn = originalWarn;
  }
  assert.deepEqual(warnings, []);
  assert.equal(calls.some((call) => call.url.pathname.startsWith("/api/governance/")), false);
});

test("loaded A quote ID and details are absent from the first B commit without remounting forms", async () => {
  await loadA();
  await renderUnit(2);
  assertFirstBCommit();
  assertNoA(snapshot());
  assert.ok(snapshot().text.includes("No quote requested"));
  await lookup(202);
  assertBQuote();
});

test("B denied quote lookup shows its current denial without any A quote data", async () => {
  await loadA();
  denied.add(2);
  await renderUnit(2);
  assertFirstBCommit();
  await lookup(202);
  assertNoA(snapshot());
  assert.equal(snapshot().detail, "");
  assert.ok(snapshot().error.includes("TEST ONLY B quote access denied."));
  for (const commit of commits.filter((commit) => commit.unit === 2)) assertNoA(commit);
});

for (const outcome of ["success", "denial"]) {
  test(`delayed A quote ${outcome} cannot restore content after switching to B`, async () => {
    await renderUnit(1);
    delayed.add(1);
    await lookup(101);
    assert.equal(pending.length, 1);
    assert.ok(container.querySelector('[aria-label="Loading quote details"]'));
    await renderUnit(2);
    assertFirstBCommit();
    await lookup(202);
    assertBQuote();
    await reply(pending[0], outcome === "success" ? pending[0].response
      : json({ detail: "TEST ONLY obsolete A quote denial." }, 403));
    assertBQuote();
    for (const commit of commits.filter((commit) => commit.unit === 2)) assertNoA(commit);
  });
}
