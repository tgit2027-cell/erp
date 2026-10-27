import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, Profiler } from "react";
import { createRoot } from "react-dom/client";
import PurchasingWorkbench from "../src/PurchasingWorkbench";
import { sidebarSections } from "../src/sidebar-destinations";

const nativeFetch = globalThis.fetch;
let root, host, calls, pending, commits, opened;
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const collectionPage = (unit, rows = [], pagination = {}) => ({
  business_unit: { id: unit, code: `U${unit}`, name: `Test Unit ${unit}` },
  rows,
  pagination: {
    page: 1, page_size: 20, total_count: rows.length, total_pages: 1,
    previous_page: null, next_page: null, ...pagination,
  },
  test_only_label: "TEST ONLY synthetic collection fixture",
});
const row = (id) => ({
  quote_id: id, kind: "stock", status: "approved", currency: "QAR",
  total_quote_value: "875.25", recorded_collections: "250.00",
  remaining_balance: "611.37", ready_at: "2031-04-03T10:00:00Z", delivered_at: null,
});
function deferred() {
  let resolve;
  const promise = new Promise((yes) => { resolve = yes; });
  const request = { resolve };
  pending.push(request);
  return promise;
}
async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
async function render(unit, focus = "collections", username = "retail_operator") {
  await act(async () => root.render(
    <Profiler id="collections" onRender={() => commits.push({ unit, text: host.textContent ?? "" })}>
      <PurchasingWorkbench
        mode="retail" selectedUnitId={unit} selectedUnitName={`Test Unit ${unit}`}
        isStaff={false} username={username} csrfToken="test-csrf" locations={[]}
        onOpenReports={() => {}} focusedView={focus}
        onNavigateFunction={(child) => { opened.push(child); }}
      />
    </Profiler>,
  ));
  await flush();
}
async function click(element) {
  assert.ok(element, "Expected an interactive control");
  await act(async () => element.click());
  await flush();
}
async function setValue(element, value) {
  assert.ok(element);
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function submit(form) {
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  calls = []; pending = []; commits = []; opened = [];
  globalThis.fetch = (path, options = {}) => {
    const url = new URL(String(path), "https://collections.test.invalid");
    calls.push({ url, options });
    assert.equal(options.method ?? "GET", "GET", "Collection follow-up is read-only");
    assert.equal(options.credentials, "same-origin");
    if (url.pathname === "/api/purchasing/collections/") {
      if (url.searchParams.get("business_unit_id") === "9") return Promise.resolve(json({ detail: "Denied by current scope." }, 403));
      if (url.searchParams.get("business_unit_id") === "10") return Promise.resolve(json({ detail: "Not found." }, 404));
      if (url.searchParams.get("quote_id") === "0") return Promise.resolve(json({ detail: "Bad ID" }, 422));
      if (url.searchParams.get("hold") === "1") return deferred();
      const unit = Number(url.searchParams.get("business_unit_id"));
      const page = Number(url.searchParams.get("page"));
      const rows = url.searchParams.has("quote_id") ? [row(Number(url.searchParams.get("quote_id")))] : [row(unit * 100 + page)];
      return Promise.resolve(json(collectionPage(unit, rows, page === 1
        ? { total_count: 21, total_pages: 2, next_page: 2 }
        : { page: 2, total_count: 21, total_pages: 2, previous_page: 1 })));
    }
    if (/^\/api\/purchasing\/quotes\/\d+\/$/.test(url.pathname)) {
      const quoteId = Number(url.pathname.split("/").at(-2));
      return Promise.resolve(json({
        quote_id: quoteId, business_unit_id: Number(url.searchParams.get("business_unit_id")),
        kind: "stock", status: "approved", customer_id: 3, discount_percent: "0",
        total_list_value: "10", total_discount: "0", total_quote_value: "10",
        payment_plan: "full", downpayment_percent: null, lines: [], payments: [],
        approvals: [], holds: [], events: [], special_order_value_bands_configured: false,
      }));
    }
    if (/^\/api\/governance\/purchasing\/\d+\/$/.test(url.pathname)) {
      return Promise.resolve(json({ test_only_label: "test", business_unit: { id: 1, name: "Unit" } }));
    }
    throw new Error(`Unexpected request ${url.pathname}`);
  };
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  globalThis.fetch = nativeFetch;
});

test("focused worklist has no request while inactive and uses a read-only server-calculated collection contract", async () => {
  await render(1, "main");
  assert.equal(calls.some(({ url }) => url.pathname === "/api/purchasing/collections/"), false);
  await render(1);
  const request = calls.find(({ url }) => url.pathname === "/api/purchasing/collections/");
  assert.ok(request);
  assert.equal(request.url.searchParams.get("business_unit_id"), "1");
  assert.equal(request.url.searchParams.get("page"), "1");
  assert.equal(request.url.searchParams.get("page_size"), "20");
  assert.equal(request.url.searchParams.get("remaining_only"), "true");
  assert.match(host.textContent, /QAR 250\.00/);
  assert.match(host.textContent, /QAR 611\.37/, "the server-returned balance is displayed rather than recomputed from totals");
  assert.match(host.textContent, /not real payments, receivables, overdue amounts or new payment terms/);
  assert.doesNotMatch(host.textContent, /supplier cost|procurement cost/i);
  assert.equal(calls.some(({ options }) => options.method === "POST"), false);
  const readCount = calls.filter(({ url }) => url.pathname === "/api/purchasing/collections/").length;
  await click(host.querySelector(".rc-refresh"));
  assert.equal(calls.filter(({ url }) => url.pathname === "/api/purchasing/collections/").length, readCount + 1);
});

test("first BU render isolates old rows while loading; obsolete 404 failure cannot replace current denial", async () => {
  globalThis.fetch = (path, options = {}) => {
    const url = new URL(String(path), "https://collections.test.invalid");
    calls.push({ url, options });
    if (url.pathname === "/api/purchasing/collections/") {
      if (url.searchParams.get("business_unit_id") === "9") return deferred();
      if (url.searchParams.get("business_unit_id") === "1") return deferred();
    }
    if (/^\/api\/governance\/purchasing\/\d+\/$/.test(url.pathname)) return Promise.resolve(json({ business_unit: { id: 2, name: "Test Unit 2" } }));
    throw new Error(`Unexpected request ${url.pathname}`);
  };
  await render(1);
  assert.equal(pending.length, 1);
  const firstBCommit = commits.length;
  await render(9);
  const bCommits = commits.slice(firstBCommit).filter((commit) => commit.unit === 9);
  const visibleB = bCommits[0];
  assert.ok(visibleB);
  assert.doesNotMatch(visibleB.text, /QAR 875\.25|Test Unit 1|#101/);
  assert.ok(host.querySelector('[aria-label="Loading collection follow-up"]'),
    `the new BU shows loading rather than retaining the previous unit's results; commits: ${JSON.stringify(bCommits)}`);
  assert.equal(pending.length, 2);
  await act(async () => pending[1].resolve(json({ detail: "Concealed." }, 403)));
  await flush();
  assert.match(host.textContent, /Collection view not authorized/);
  await act(async () => pending[0].resolve(json({ detail: "Concealed for old unit." }, 404)));
  await flush();
  assert.doesNotMatch(host.textContent, /QAR 875\.25|Test Unit 1/);
  assert.match(host.textContent, /Collection view not authorized/);
});

test("concealed unit-level 404 renders an unavailable state and no collection rows", async () => {
  await render(10);
  assert.match(host.textContent, /Collection view not authorized/);
  assert.match(host.textContent, /No collection rows are displayed/);
  assert.equal(host.querySelector(".rc-table"), null);
  assert.equal(host.querySelector(".rc-empty"), null);
});

test("exact quote search and server-driven paging preserve request parameters", async () => {
  await render(1);
  await setValue(host.querySelector("#rc-quote-search"), "743");
  await submit(host.querySelector(".rc-search"));
  const searchCall = calls.filter(({ url }) => url.pathname === "/api/purchasing/collections/").at(-1);
  assert.equal(searchCall.url.searchParams.get("quote_id"), "743");
  assert.equal(searchCall.url.searchParams.get("page"), "1");
  assert.match(host.textContent, /#743/);
  await click(host.querySelector('[aria-label="Next page"]'));
  const pageCall = calls.filter(({ url }) => url.pathname === "/api/purchasing/collections/").at(-1);
  assert.equal(pageCall.url.searchParams.get("page"), "2");
  assert.equal(pageCall.url.searchParams.get("quote_id"), "743");
  await click(host.querySelector(".rc-toggle input"));
  const fullyCollectedCall = calls.filter(({ url }) => url.pathname === "/api/purchasing/collections/").at(-1);
  assert.equal(fullyCollectedCall.url.searchParams.get("remaining_only"), "false");
  assert.equal(fullyCollectedCall.url.searchParams.get("page"), "1");
});

test("row action selects the quote through existing lookup and opens existing collection actions without a write", async () => {
  await render(1);
  await click(host.querySelector(".rc-row-action"));
  assert.deepEqual(opened, ["actions"]);
  const quoteRequest = calls.find(({ url }) => /^\/api\/purchasing\/quotes\/\d+\/$/.test(url.pathname));
  assert.ok(quoteRequest);
  assert.equal(quoteRequest.url.pathname, "/api/purchasing/quotes/101/");
  assert.equal(quoteRequest.url.searchParams.get("business_unit_id"), "1");
  await flush();
  assert.match(host.textContent, /Quote #101/);
  assert.equal(calls.some(({ options }) => options.method === "POST"), false);
});

test("retail collection destination is a stable favoriteable function", () => {
  const retail = sidebarSections({ isStaff: false, isSuperuser: false, canManageReportPanels: false }).find(({ id }) => id === "retail");
  const destination = retail.children.find(({ id }) => id === "collections");
  assert.deepEqual(destination, { id: "collections", label: "Collection follow-up", favoriteable: true });
});
