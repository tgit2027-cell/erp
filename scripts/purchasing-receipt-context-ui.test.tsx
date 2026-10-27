import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, Profiler } from "react";
import { createRoot } from "react-dom/client";
import PurchasingWorkbench from "../src/PurchasingWorkbench";

// Actual workbench, fictional GET responses only: no live account or data writes.
const nativeFetch = globalThis.fetch;
let root, container, calls, pending, commits, delayed, originalError, originalWarn, warnings;

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
function receipt(unit, offset) {
  const letter = unit === 1 ? "A" : "B";
  return {
    receipt_id: unit * 1000 + offset,
    created_at: "2026-10-01T09:00:00+03:00",
    business_unit: { id: unit, code: letter, name: `TEST ONLY Unit ${letter}` },
    supplier: { code: `S-${letter}`, name: `TEST ONLY ${letter} supplier` },
    currency: "QAR", fields: [],
    lines: [{
      item_id: `TEST ONLY ${letter} item ${offset}`,
      description: `TEST ONLY ${letter} receipt line`,
      brand_name: `TEST ONLY ${letter} brand`,
      warehouse_location: `TEST ONLY ${letter} warehouse`, quantity: "1.000",
    }],
  };
}
function page(url) {
  const unit = Number(url.searchParams.get("unit"));
  const current = Number(url.searchParams.get("page"));
  const total = unit === 1 ? 41 : 3;
  const start = (current - 1) * 20;
  return {
    rows: Array.from({ length: Math.max(0, Math.min(20, total - start)) }, (_, i) => receipt(unit, start + i + 1)),
    pagination: {
      page: current, page_size: 20, total_count: total, total_pages: Math.ceil(total / 20),
      previous_page: current > 1 ? current - 1 : null,
      next_page: current * 20 < total ? current + 1 : null,
    },
  };
}
async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
function snapshot() {
  const scope = container.querySelector(".purchasing-readside");
  return {
    text: scope?.textContent ?? "",
    rows: Array.from(scope?.querySelectorAll(".pr-table tbody tr") ?? [], (row) => row.cells[0].textContent),
    pager: scope?.querySelector(".pr-pager")?.textContent ?? "",
    detail: scope?.querySelector(".pr-detail")?.textContent ?? "",
  };
}
async function renderUnit(unit) {
  await act(async () => root.render(
    <Profiler id="actual-workbench" onRender={() => commits.push({ unit, ...snapshot() })}>
      <PurchasingWorkbench
        selectedUnitId={unit} selectedUnitName={`TEST ONLY Unit ${unit === 1 ? "A" : "B"}`}
        isStaff={false} username="synthetic_receipt_viewer" csrfToken="synthetic-placeholder"
        locations={[]} onOpenReports={() => {}}
      />
    </Profiler>,
  ));
  await flush();
}
async function click(label) {
  const button = container.querySelector(`.purchasing-readside button[aria-label="${label}"]`);
  assert.ok(button, `Expected ${label}`);
  assert.equal(button.disabled, false);
  await act(async () => button.click());
  await flush();
}
async function reply(request, response = request.response) {
  assert.ok(request, "Expected deferred receipt request");
  await act(async () => request.resolve(response));
  await flush();
}
function assertNoA(state) {
  assert.equal(state.text.includes("TEST ONLY Unit A"), false);
  assert.equal(state.text.includes("TEST ONLY A"), false);
  assert.equal(state.pager.includes("41 records"), false);
  assert.equal(state.rows.some((id) => Number(id) < 2000), false);
  assert.equal(state.detail, "", "No A selected-record detail under B");
}
function assertFirstBCommit() {
  const first = commits.find((commit) => commit.unit === 2);
  assert.ok(first, "Profiler captured B's first commit before passive-effect resets");
  assert.ok(first.text.includes("TEST ONLY Unit B"));
  assertNoA(first);
}
function assertBPage() {
  const state = snapshot();
  assertNoA(state);
  assert.deepEqual(state.rows, ["2001", "2002", "2003"]);
  assert.ok(state.pager.includes("Page 1 of 1 · 3 records"));
  for (const label of ["Previous page", "Next page"]) {
    assert.equal(container.querySelector(`.pr-pager button[aria-label="${label}"]`).disabled, true);
  }
  assert.equal(state.text.includes("Loading receipts"), false);
}
function bPageOne() {
  return pending.findLast((request) => request.url.searchParams.get("unit") === "2"
    && request.url.searchParams.get("page") === "1");
}
async function loadAWithDetail() {
  await renderUnit(1);
  await click("Next page");
  assert.ok(snapshot().pager.includes("Page 2 of 3 · 41 records"));
  await click("Open Receipts and landed charges record 1021");
  assert.ok(snapshot().detail.includes("TEST ONLY A item 21"));
  assert.deepEqual(snapshot().rows, Array.from({ length: 20 }, (_, i) => String(1021 + i)));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = []; pending = []; commits = []; delayed = new Set(); warnings = [];
  originalError = console.error; originalWarn = console.warn;
  console.error = console.warn = (...args) => warnings.push(args.map(String).join(" "));
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, ...options });
    assert.equal(options.method, "GET", "No transaction or configuration writes");
    assert.equal(options.credentials, "same-origin");
    if (url.pathname === "/api/reports/purchase_history/receipts/") {
      assert.equal(url.searchParams.get("page_size"), "20");
      const response = json(page(url));
      if (!delayed.has(Number(url.searchParams.get("unit")))) return response;
      let resolve;
      const promise = new Promise((done) => { resolve = done; });
      pending.push({ url, response, resolve });
      return promise;
    }
    const match = url.pathname.match(/^\/api\/reports\/purchase_history\/receipts\/(\d+)\/$/);
    if (match) {
      const unit = Number(url.searchParams.get("unit"));
      const id = Number(match[1]);
      return id > unit * 1000 && id < (unit + 1) * 1000
        ? json({ receipt: receipt(unit, id - unit * 1000) })
        : json({ detail: "TEST ONLY wrong-unit detail denied." }, 403);
    }
    if ([
      "/api/purchasing/quotes/", "/api/purchasing/storage-charges/",
      "/api/reports/inventory/reservations/", "/api/reports/sales/deliveries/",
    ].includes(url.pathname)) return json({ detail: "TEST ONLY unrelated capability unavailable." }, 403);
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

test("receipt BU switch isolates the first commit then renders B page one and controls", async () => {
  await loadAWithDetail();
  delayed.add(2);
  await renderUnit(2);
  assertFirstBCommit();
  assertNoA(snapshot());
  assert.deepEqual(snapshot().rows, []);
  assert.equal(snapshot().pager, "");
  assert.deepEqual(pending.filter((request) => request.url.searchParams.get("unit") === "2")
    .map((request) => Object.fromEntries(request.url.searchParams)), [
    { unit: "2", page: "1", page_size: "20" },
  ]);
  assert.equal(calls.some((call) => call.url.searchParams.get("unit") === "2"
    && /\/receipts\/\d+\//.test(call.url.pathname)), false, "No old A detail is requested under B");
  await reply(bPageOne());
  assertBPage();
});

test("denied B receipts never retain A rows counts or detail at any B commit", async () => {
  await loadAWithDetail();
  delayed.add(2);
  await renderUnit(2);
  assertFirstBCommit();
  await reply(bPageOne(), json({ detail: "TEST ONLY B receipts denied." }, 403));
  assertNoA(snapshot());
  assert.deepEqual(snapshot().rows, []);
  assert.equal(snapshot().pager, "");
  assert.ok(snapshot().text.includes("The server did not authorize this receipts and landed charges view."));
  for (const commit of commits.filter((commit) => commit.unit === 2)) assertNoA(commit);
});

for (const outcome of ["success", "denial"]) {
  test(`delayed A receipt ${outcome} cannot replace B rows counts or detail`, async () => {
    await loadAWithDetail();
    delayed.add(1);
    await click("Next page");
    const old = pending.find((request) => request.url.searchParams.get("unit") === "1");
    assert.equal(old.url.searchParams.get("page"), "3");
    delayed.add(2);
    await renderUnit(2);
    assertFirstBCommit();
    await reply(bPageOne());
    assertBPage();
    await reply(old, outcome === "success" ? old.response : json({ detail: "TEST ONLY stale A denial." }, 403));
    assertBPage();
    assert.equal(snapshot().text.includes("TEST ONLY stale A denial."), false);
    for (const commit of commits.filter((commit) => commit.unit === 2)) assertNoA(commit);
  });
}
