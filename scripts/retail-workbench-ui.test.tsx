import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, Profiler } from "react";
import { createRoot } from "react-dom/client";
import PurchasingWorkbench from "../src/PurchasingWorkbench";
import { sidebarSections } from "../src/sidebar-destinations";
import { workspaceHash } from "../src/workspace-hash";

const nativeFetch = globalThis.fetch;
let root, host, calls, posts, pending, deniedUnits, deniedGovernance, fixturePrices, commits, warnings;
let originalError, originalWarn, deferQuotePost;
let quoteStatus;
let deferredQuoteGetCounts;
const username = "TEST_ONLY_retail_operator";
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const page = (rows = []) => ({ rows, pagination: {
  page: 1, page_size: 20, total_count: rows.length, total_pages: 1, previous_page: null, next_page: null,
} });
function quote(unit, id = unit === 1 ? 101 : 202) {
  return {
    quote_id: id, business_unit_id: unit, kind: "stock", status: quoteStatus, customer_id: 12,
    discount_percent: "4.00", total_list_value: "82.50", total_discount: "3.30",
    total_quote_value: "79.20", payment_plan: "full", downpayment_percent: null,
    currency: "QAR", created_at: "2031-04-03T10:15:00Z",
    special_order_value_bands_configured: false,
    lines: [{
      quote_line_id: id * 10, variant_id: unit * 10, quantity: "2.000",
      selling_price_snapshot: "41.25", price_revision_snapshot: 7,
      supplier_unit_cost: "TEST_ONLY_PRIVATE_COST",
      discount_percent: "4.00", discount_amount: "3.30", net_unit_price: "39.60",
      net_total: "79.20", specifications: { fabric: "TEST_ONLY_Cobalt weave", width_cm: 180 },
    }],
    payments: [], approvals: [], holds: [], events: [],
    delivery_progress: {
      ready_at: null, delivered_at: null,
      lines: [{
        quote_line_id: id * 10, variant_id: unit * 10, ordered_quantity: "2.0000",
        reserved_to_date_quantity: "1.0000", delivered_quantity: "0.0000", remaining_quantity: "2.0000",
      }],
    },
  };
}
function config(unit) {
  return {
    test_only_label: "TEST ONLY synthetic configuration",
    business_unit: { id: unit, code: `TEST${unit}`, name: `TEST ONLY Unit ${unit}` },
    role_assignments: [{ id: 1, user_id: 1, username, role: "manager" }],
    current_prices: fixturePrices ? [{
      id: 1, variant_id: unit * 10, sku: `TEST_ONLY_VARIANT_${unit}`, amount: "41.25",
      currency: "QAR", revision: 7, approved_by: 1,
    }] : [],
    work_calendar: { is_configured: true, closed_weekdays: [4], closed_dates: [] },
    storage_exception_approver_roles: [], special_order_value_bands: [],
    special_order_bands_used_for_approval: false, special_order_bands_note: "TEST ONLY not used for approval",
  };
}
function deferred(call) {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  const request = {
    ...call,
    settled: false,
    resolve(value) { request.settled = true; resolve(value); },
    reject(error) { request.settled = true; reject(error); },
  };
  pending.push(request);
  return promise;
}
async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
function holdQuoteGet(key, count = 1) {
  deferredQuoteGetCounts.set(key, (deferredQuoteGetCounts.get(key) ?? 0) + count);
}
function deferQuoteGet(key, call) {
  const remaining = deferredQuoteGetCounts.get(key) ?? 0;
  if (!remaining) return null;
  if (remaining === 1) deferredQuoteGetCounts.delete(key);
  else deferredQuoteGetCounts.set(key, remaining - 1);
  return deferred({ ...call, key });
}
async function resolvePending(request, response) {
  await act(async () => request.resolve(response));
  await flush();
}
async function renderUnit(unit, { staff = true } = {}) {
  await act(async () => root.render(
    <Profiler id="retail" onRender={() => commits.push({ unit, text: host.textContent ?? "" })}>
      <PurchasingWorkbench
        mode="retail"
        selectedUnitId={unit}
        selectedUnitName={`TEST ONLY Unit ${unit}`}
        isStaff={staff}
        username={username}
        csrfToken="TEST_ONLY_CSRF"
        locations={[{ id: unit * 11, code: `T${unit}`, name: `TEST ONLY showroom ${unit}`, parent_id: null }]}
        onOpenReports={() => {}}
        onOpenDelivery={() => { host.dataset.deliveryOpened = "true"; }}
      />
    </Profiler>,
  ));
  await flush();
}
async function setValue(element, value) {
  assert.ok(element, "Expected a rendered control");
  const prototype = element.tagName === "SELECT" ? window.HTMLSelectElement.prototype
    : element.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, String(value));
    element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}
async function submit(form) {
  assert.ok(form);
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();
}
async function lookupQuote(id) {
  await setValue(host.querySelector("#pw-quote-id"), id);
  await submit(host.querySelector(".pw-quote-form"));
}
async function click(element) {
  assert.ok(element, "Expected a rendered button");
  await act(async () => element.click());
  await flush();
}
function quoteForm() {
  return host.querySelector('form:has(#pw-quote-kind)');
}
function quoteCreateButton() {
  return quoteForm()?.querySelector('button[type="submit"]');
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  calls = []; posts = []; pending = []; deniedUnits = new Set(); deniedGovernance = new Set();
  deferredQuoteGetCounts = new Map();
  fixturePrices = true; commits = []; warnings = [];
  quoteStatus = "approved";
  originalError = console.error;
  originalWarn = console.warn;
  console.error = console.warn = (...args) => warnings.push(args.map(String).join(" "));
  deferQuotePost = false;
  globalThis.fetch = (path, options = {}) => {
    const url = new URL(String(path), "https://retail.test.invalid");
    calls.push({ url, options });
    assert.equal(options.credentials, "same-origin");
    if (options.method === "POST") {
      const post = { url, options, body: JSON.parse(options.body) };
      posts.push(post);
      if (url.pathname === "/api/purchasing/quotes/" && deferQuotePost) {
        return deferred(post);
      }
      const id = url.pathname === "/api/purchasing/quotes/" ? 303 : undefined;
      return Promise.resolve(json({ quote_id: id, success: true }, id ? 201 : 200));
    }
    assert.equal(options.method ?? "GET", "GET");
    if (url.pathname === "/api/purchasing/quotes/") {
      const unit = Number(url.searchParams.get("business_unit_id"));
      if (deniedUnits.has(unit)) return Promise.resolve(json({ detail: "TEST ONLY quote-list access denied." }, 403));
      if (url.searchParams.get("page_size") === "1") return Promise.resolve(json(page()));
      const heldList = deferQuoteGet(`list:${unit}`, { url, options });
      if (heldList) return heldList;
      return Promise.resolve(json(page([quote(unit)])));
    }
    if (/^\/api\/purchasing\/quotes\/\d+\/$/.test(url.pathname)) {
      const unit = Number(url.searchParams.get("business_unit_id"));
      if (deniedUnits.has(unit)) return Promise.resolve(json({ detail: "TEST ONLY quote detail denied." }, 403));
      const id = Number(url.pathname.split("/").at(-2));
      const heldDetail = deferQuoteGet(`detail:${unit}:${id}`, { url, options });
      if (heldDetail) return heldDetail;
      return Promise.resolve(json(quote(unit, id)));
    }
    if (url.pathname === "/api/purchasing/quotation-choices/") {
      const unit = Number(url.searchParams.get("business_unit_id"));
      if (deniedUnits.has(unit)) return Promise.resolve(json({ detail: "TEST ONLY quotation choices denied." }, 403));
      const base = config(unit);
      return Promise.resolve(json({
        business_unit: base.business_unit,
        current_prices: fixturePrices ? base.current_prices.map((price) => ({ ...price, approved_by: null, item: {
          item_code: price.sku, name_en: "TEST ONLY synthetic item", name_ar: "", category_en: "TEST category",
          category_ar: "", description_en: "", description_ar: "", image_path: null,
        }, stock: [] })) : [],
        customers: [{ counterparty_id: 12, code: "TEST-CUSTOMER", display_name: "TEST ONLY synthetic party",
          party_type: "customer", contact_en: "TEST ONLY client contact", contact_ar: "", name_ar: "" }],
        role_assignments: [{ id: 1, user_id: 1, username, role: "manager" }],
        discount_ceiling: "20", work_calendar: { is_configured: false, closed_weekdays: [], closed_dates: [] },
        test_only_label: "TEST ONLY quotation choices",
      }));
    }
    if (/^\/api\/governance\/purchasing\/\d+\/$/.test(url.pathname)) {
      const unit = Number(url.pathname.split("/").at(-2));
      if (deniedGovernance.has(unit)) return Promise.resolve(json({ detail: "TEST ONLY configuration denied." }, 403));
      return Promise.resolve(json(config(unit)));
    }
    if (url.pathname === "/api/governance/purchasing/counterparties/") {
      const customer = url.searchParams.get("party_type") === "customer";
      return Promise.resolve(json(page([{
        counterparty_id: customer ? 12 : 13, party_type: customer ? "customer" : "supplier",
        code: customer ? "TEST-CUSTOMER" : "TEST-SUPPLIER", name: "TEST ONLY synthetic party",
      }])));
    }
    if (url.pathname === "/api/purchasing/storage-charges/") return Promise.resolve(json(page()));
    throw new Error(`Unexpected request: ${options.method} ${url.pathname}`);
  };
});

afterEach(async () => {
  try { await act(async () => root.unmount()); }
  finally {
    console.error = originalError;
    console.warn = originalWarn;
    host.remove();
    deferQuotePost = false;
    globalThis.fetch = nativeFetch;
  }
  assert.deepEqual(warnings, []);
});

test("retail creates stock and special-order quotes only from configured approved snapshots", async () => {
  await renderUnit(1);
  assert.ok(host.querySelector("#pw-create-quote-title"));
  assert.equal(quoteCreateButton().disabled, true, "A current price must be selected before quote creation is available");
  assert.deepEqual([...host.querySelectorAll("#pw-quote-kind option")].map((option) => option.value), ["stock", "special_order"]);

  await setValue(host.querySelector("#pw-quote-customer"), "12");
  await setValue(host.querySelector("#pw-quote-variant"), "10");
  assert.match(quoteForm().textContent, /Your assigned role ceiling is 20%; staff may request up to 50%/);
  assert.equal(quoteCreateButton().disabled, false, quoteForm().textContent);
  await setValue(host.querySelector("#pw-quote-location"), "11");
  await setValue(host.querySelector("#pw-quote-quantity"), "2");
  await setValue(quoteForm().querySelector('input[type="number"][max="50"]'), "5");
  await setValue(host.querySelector("#pw-quote-specifications"), '{"fabric":"TEST_ONLY_Cobalt weave"}');
  await setValue(quoteForm().querySelector('input[maxlength="240"]'), "TEST ONLY standard price");
  await submit(quoteForm());
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url.pathname, "/api/purchasing/quotes/");
  assert.deepEqual(posts[0].body, {
    business_unit_id: 1, customer_id: 12, kind: "stock", discount_percent: "5",
    discount_reason: "TEST ONLY standard price", payment_plan: "full",
    lines: [{ variant_id: 10, quantity: "2", specifications: { fabric: "TEST_ONLY_Cobalt weave" }, location_id: 11 }],
    presentation: { valid_until: "", payment_terms_en: "", payment_terms_ar: "", delivery_time_en: "",
      delivery_time_ar: "", department_en: "", department_ar: "" },
  });
  assert.match(host.textContent, /Synthetic quote #303 created/);

  await setValue(host.querySelector("#pw-quote-kind"), "special_order");
  assert.equal(host.querySelector("#pw-quote-location"), null, "Special orders do not ask for stock location");
  await setValue(host.querySelector("#pw-payment-plan"), "downpayment");
  await setValue(host.querySelector("#pw-payment-terms-reason"), "TEST ONLY customer agreed to synthetic terms");
  await submit(quoteForm());
  assert.equal(posts.length, 2);
  assert.deepEqual(posts[1].body, {
    business_unit_id: 1, customer_id: 12, kind: "special_order", discount_percent: "5",
    discount_reason: "TEST ONLY standard price", payment_plan: "downpayment",
    lines: [{ variant_id: 10, quantity: "2", specifications: { fabric: "TEST_ONLY_Cobalt weave" } }],
    payment_terms_reason: "TEST ONLY customer agreed to synthetic terms", downpayment_percent: "40",
    presentation: { valid_until: "", payment_terms_en: "", payment_terms_ar: "", delivery_time_en: "",
      delivery_time_ar: "", department_en: "", department_ar: "" },
  });
  assert.equal(host.querySelector("#pw-quote-location"), null);
});

test("unconfigured, nonstaff and denied dependencies retain quote controls with precise existing reasons", async () => {
  fixturePrices = false;
  await renderUnit(1);
  assert.ok(quoteForm(), "Quote UI remains visible when configuration is missing");
  assert.equal(quoteCreateButton().disabled, true);
  assert.match(quoteForm().textContent, /No current approved selling-price snapshots are configured/);
  assert.equal(posts.length, 0);

  const governanceRequestCount = calls.filter(({ url }) => url.pathname.startsWith("/api/governance/purchasing/")).length;
  fixturePrices = true;
  await renderUnit(1, { staff: false });
  assert.ok(quoteForm(), "Nonstaff must still see the quote entry and its reason");
  assert.equal(quoteCreateButton().disabled, true);
  assert.equal(host.querySelector("#pw-quote-variant").disabled, false,
    "Sales quote choices do not depend on staff-only configuration.");
  assert.match(quoteForm().textContent, /Your current role ceiling: 20%/);
  assert.equal(calls.filter(({ url }) => url.pathname.startsWith("/api/governance/purchasing/")).length, governanceRequestCount,
    "Nonstaff retail does not request staff-only configuration.");

  deniedGovernance.add(3);
  await renderUnit(3);
  await flush();
  assert.equal(quoteCreateButton().disabled, true);
  await click([...host.querySelectorAll("button")].find((button) => button.textContent?.startsWith("Start fresh quote lines for this unit")));
  assert.equal(host.querySelector("#pw-quote-variant").disabled, false,
    quoteForm().textContent);
  assert.doesNotMatch(quoteForm().textContent, /Staff configuration could not be verified/);

  deniedGovernance.add(2);
  deniedUnits.add(2);
  await renderUnit(2);
  assert.ok(quoteForm());
  assert.equal(quoteCreateButton().disabled, true);
  assert.match(quoteForm().textContent, /Order access could not be verified|active create_order grant/);
  assert.ok(host.querySelector(".pr-notice")?.textContent.includes("did not authorize")
    || host.textContent.includes("quote-list access denied"));
  assert.equal(posts.length, 0);
});

test("retail quote register uses existing list GET shape, authorized detail, exact selling snapshots and no supplier costs", async () => {
  await renderUnit(1);
  assert.match(host.querySelector("#pr-title").textContent, /Quote register/);
  assert.deepEqual([...host.querySelectorAll(".pr-tabs > button")].map((button) => button.textContent), ["Quotes"]);
  assert.ok(calls.some(({ url }) => url.pathname === "/api/purchasing/quotes/"
    && url.searchParams.get("business_unit_id") === "1" && url.searchParams.get("page_size") === "20"));
  await act(async () => host.querySelector('[aria-label="Open Quotes and orders record 101"]').click());
  await flush();
  const detail = host.querySelector(".pr-detail");
  assert.ok(detail);
  assert.match(detail.textContent, /QAR 41\.25/);
  assert.match(detail.textContent, /Approved price revision7/);
  assert.match(detail.textContent, /QAR 3\.30/);
  assert.match(detail.textContent, /QAR 39\.60/);
  assert.match(detail.textContent, /TEST_ONLY_Cobalt weave/);
  assert.match(detail.textContent, /180/);
  assert.doesNotMatch(detail.textContent, /supplier unit cost|landed cost|PRIVATE_COST|procurement cost/i);
  await setValue(host.querySelector("#pw-quote-id"), "101");
  await submit(host.querySelector(".pw-quote-form"));
  const progress = host.querySelector('[aria-label="Read-only delivery progress"]');
  assert.ok(progress);
  assert.deepEqual([...progress.querySelectorAll("tbody tr")].map((row) => (
    [...row.querySelectorAll("td")].map((cell) => cell.textContent.trim())
  )), [["#1010Variant 10", "2.0000", "1.0000", "0.0000", "2.0000"]]);
  assert.equal(host.querySelector("#pw-receipt-title"), null);
  assert.equal(host.querySelector("#pw-commitment-create-title"), null);
  assert.equal(host.querySelector("#pw-sales-return-summary"), null);
  assert.ok([...host.querySelectorAll(".pw-quote-action-grid h4")].some((heading) => heading.textContent === "Request temporary hold"));
  assert.ok([...host.querySelectorAll(".pw-quote-action-grid h4")].some((heading) => heading.textContent === "Record simulated payment"));
  quoteStatus = "confirmed";
  await submit(host.querySelector(".pw-quote-form"));
  assert.equal([...host.querySelectorAll(".pw-transactions button")].some((button) => /Record delivery|Record readiness|accrue due storage|Record explicit approval/i.test(button.textContent)), false);
  assert.equal([...host.querySelectorAll(".pw-quote-action-grid h4")].some((heading) => heading.textContent === "Record simulated payment"), true);
  assert.equal(calls.some(({ url }) => /commitments|receipts|storage-charges/.test(url.pathname)), false);
  host.querySelector(".pw-retail-delivery-link button").click();
  assert.equal(host.dataset.deliveryOpened, "true");
});

for (const deniedA of [false, true]) {
  test(`stale A detail GET ${deniedA ? "denial" : "success"} cannot replace retail B after first-commit isolation`, async () => {
    await renderUnit(1);
    await lookupQuote(101);
    assert.ok(host.querySelector(".pw-quote")?.textContent.includes("#101"));
    await click(host.querySelector('[aria-label="Open Quotes and orders record 101"]'));
    assert.ok(host.querySelector(".pr-detail")?.textContent.includes("TEST_ONLY_Cobalt weave"));

    holdQuoteGet("detail:1:101");
    await lookupQuote(101);
    const obsoleteA = pending.find((request) => request.key === "detail:1:101");
    assert.ok(obsoleteA, "The second A detail GET should be held open");

    holdQuoteGet("list:2");
    const firstBCommitIndex = commits.length;
    await renderUnit(2);
    const bCommits = commits.slice(firstBCommitIndex).filter((commit) => commit.unit === 2);
    const firstBCommit = bCommits[0];
    assert.ok(firstBCommit);
    assert.doesNotMatch(firstBCommit.text, /#101|1010|TEST_ONLY_VARIANT_1|Unit 1/);
    assert.doesNotMatch(firstBCommit.text, /Page 1 of 1 · 1 records/);
    assert.equal(host.querySelector(".pr-pager"), null, "The pending B list must not display A pagination/counts");
    assert.equal(host.querySelector(".pr-detail"), null, "A register detail must clear at B's first commit");
    assert.equal(host.querySelector(".pw-quote"), null, "A lookup detail must clear at B's first commit");

    const bList = pending.find((request) => request.key === "list:2");
    assert.ok(bList, "B quote-list GET should be delayed");
    await resolvePending(bList, json(page([quote(2)])));
    assert.ok(host.querySelector('[aria-label="Open Quotes and orders record 202"]'));

    holdQuoteGet("detail:2:202", 2);
    await click(host.querySelector('[aria-label="Open Quotes and orders record 202"]'));
    await lookupQuote(202);
    const bDetails = pending.filter((request) => request.key === "detail:2:202");
    assert.equal(bDetails.length, 2, "B register and lookup detail reads are both delayed");
    for (const request of bDetails) await resolvePending(request, json(quote(2, 202)));
    assert.ok(host.querySelector(".pw-quote")?.textContent.includes("#202"));
    assert.ok(host.querySelector(".pr-detail")?.textContent.includes("TEST_ONLY_Cobalt weave"));
    assert.doesNotMatch(host.querySelector(".pr-detail").textContent, /#101/);
    assert.equal(host.querySelector(".pr-pager")?.textContent.includes("1 records"), true);

    await resolvePending(obsoleteA, json(
      deniedA ? { detail: "TEST ONLY obsolete A detail denial" } : quote(1, 101),
      deniedA ? 403 : 200,
    ));
    assert.ok(host.querySelector(".pw-quote")?.textContent.includes("#202"));
    assert.ok(host.querySelector(".pr-detail")?.textContent.includes("TEST_ONLY_Cobalt weave"));
    assert.doesNotMatch(host.textContent, /#101|1010|TEST_ONLY_VARIANT_1|obsolete A detail denial/);
    assert.ok(host.textContent.includes("TEST ONLY Unit 2"));
    assert.equal(posts.length, 0, "All requests in this scenario are GET-only");
  });
}

test("current B denied quote lookup and register replace all loaded A detail and list state", async () => {
  await renderUnit(1);
  await lookupQuote(101);
  assert.ok(host.querySelector(".pw-quote")?.textContent.includes("#101"));
  await click(host.querySelector('[aria-label="Open Quotes and orders record 101"]'));
  assert.ok(host.querySelector(".pr-detail")?.textContent.includes("TEST_ONLY_Cobalt weave"));

  deniedUnits.add(2);
  const firstBCommitIndex = commits.length;
  await renderUnit(2);
  const firstBCommit = commits.slice(firstBCommitIndex).find((commit) => commit.unit === 2);
  assert.ok(firstBCommit);
  assert.doesNotMatch(firstBCommit.text, /#101|1010|TEST_ONLY_VARIANT_1|Unit 1/);
  assert.equal(host.querySelector(".pw-quote"), null);
  assert.equal(host.querySelector(".pr-detail"), null);
  assert.equal(host.querySelector(".pr-pager"), null);
  assert.match(host.querySelector(".pr-notice").textContent, /did not authorize this quotes and orders view/);

  await lookupQuote(202);
  assert.equal(host.querySelector(".pw-quote"), null);
  assert.match(host.querySelector(".pw-quote-card [role='alert']").textContent, /TEST ONLY quote detail denied/);
  assert.equal(host.querySelector(".pr-detail"), null);
  assert.doesNotMatch(host.textContent, /#101|1010|TEST_ONLY_VARIANT_1|TEST_ONLY_Cobalt weave/);
  assert.equal(posts.length, 0, "Denied GET-only state does not create a quote or mutate data");
});

for (const denied of [false, true]) {
  test(`first retail BU switch commit isolates prior quote and delayed ${denied ? "denial" : "success"} cannot replace B`, async () => {
    await renderUnit(1);
    assert.ok(host.querySelector("#pw-quote-id"));
    deferQuotePost = true;
    await setValue(host.querySelector("#pw-quote-customer"), "12");
    await setValue(host.querySelector("#pw-quote-variant"), "10");
    await setValue(host.querySelector("#pw-quote-location"), "11");
    await setValue(quoteForm().querySelector('input[maxlength="240"]'), "TEST ONLY authorized price");
    await submit(quoteForm());
    const delayed = pending.find((request) => request.url.pathname === "/api/purchasing/quotes/");
    assert.ok(delayed);

    const firstBCommit = commits.length;
    await renderUnit(2);
    const bCommit = commits.slice(firstBCommit).find((commit) => commit.unit === 2);
    assert.ok(bCommit);
    assert.doesNotMatch(bCommit.text, /#101|Unit 1|TEST_ONLY_VARIANT_1/);
    assert.equal(host.querySelector("#pw-quote-id").value, "");
    assert.equal(host.querySelector(".pw-quote"), null);

    await act(async () => delayed.resolve(json(
      denied ? { detail: "TEST ONLY delayed A denial" } : { quote_id: 909 },
      denied ? 403 : 201,
    )));
    await flush();
    assert.equal(host.querySelector("#pw-quote-id").value, "");
    assert.equal(host.querySelector(".pw-quote"), null);
    assert.doesNotMatch(host.querySelector(".pw-transactions").textContent, /delayed A denial|quote #909 created/i);
    assert.ok(host.textContent.includes("TEST ONLY Unit 2"));
    assert.equal(posts.length, 1);
  });
}

test("retail sidebar children map to stable function hashes and preserve destination ids", async () => {
  await renderUnit(1);
  const sections = sidebarSections({ isStaff: false, isSuperuser: false, canManageReportPanels: false });
  const retail = sections.find((section) => section.id === "retail");
  assert.deepEqual(retail.children.map(({ id }) => id), ["main", "quote", "actions", "register", "collections", "invoices"]);
  assert.deepEqual(retail.children.map(({ id }) => workspaceHash("retail", id)), [
    "#/retail/main", "#/retail/quote", "#/retail/actions", "#/retail/register", "#/retail/collections", "#/retail/invoices",
  ]);
  assert.equal(host.querySelector('[data-workspace-view="retail/main"]'), null, "Legacy direct component rendering does not opt into routed focused views.");
  assert.deepEqual(sections.map(({ id }) => id).slice(0, 4), ["retail", "procurement", "inventory", "delivery"]);
});
