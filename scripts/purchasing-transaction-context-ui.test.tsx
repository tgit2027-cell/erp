import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, Profiler } from "react";
import { createRoot } from "react-dom/client";
import PurchasingWorkbench from "../src/PurchasingWorkbench";
import { syntheticPackingWorkbook } from "./synthetic-xlsx-fixture.mjs";

// All POSTs terminate at this in-memory mock. No server or persistent fixtures.
const originalFetch = globalThis.fetch;
const originalDOMParser = globalThis.DOMParser;
let root, host, calls, pending, posts, accessHolds, deniedUnits, quoteSnapshots, quoteHolds, commitmentRows, warnings, originalError, originalWarn, accessCommits;
const username = "TEST_ONLY_transaction_viewer";
const recheckReads = (id) => pending.filter(entry =>
  entry.url.pathname === `/api/purchasing/quotes/${id}/` && !entry.options.method?.includes("POST"));
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const page = (rows = []) => ({ rows, pagination: {
  page: 1, page_size: 20, total_count: rows.length, total_pages: 1, previous_page: null, next_page: null,
} });
function quote(unit) {
  return {
    quote_id: unit === 1 ? 101 : 202, business_unit_id: unit, customer_id: 2,
    kind: "stock", status: "confirmed", discount_percent: "0.00",
    total_list_value: "2000.00", total_discount: "0.00", total_quote_value: "2000.00",
    payment_plan: "full", downpayment_percent: null, special_order_value_bands_configured: false,
    lines: [], payments: [], approvals: [], holds: [], events: [],
  };
}
function configuration(unit) {
  return {
    test_only_label: "TEST ONLY HTTP fixtures, not approved company policy",
    business_unit: { id: unit, code: `TEST${unit}`, name: `TEST ONLY Unit ${unit}` },
    role_assignments: [{ id: 1, user_id: 1, username, role: "manager" }],
    current_prices: [{ id: 1, variant_id: 10, sku: "TEST_ONLY_CHAIR", amount: "2000.00",
      currency: "QAR", revision: 1, approved_by: 1 }],
    work_calendar: { is_configured: true, closed_weekdays: [4], closed_dates: [] },
    storage_exception_approver_roles: [], special_order_value_bands: [],
    special_order_bands_used_for_approval: false, special_order_bands_note: "TEST ONLY unconfigured",
  };
}
function quotationChoices(unit) {
  const config = configuration(unit);
  return {
    business_unit: config.business_unit,
    current_prices: config.current_prices.map((price) => ({ ...price, item: {
      item_code: "TEST-CHAIR", name_en: "TEST ONLY chair", name_ar: "كرسي تجريبي",
      category_en: "Seating", category_ar: "مقاعد", description_en: "TEST description",
      description_ar: "وصف تجريبي", image_path: null,
    }, stock: [] })),
    customers: [{ counterparty_id: 2, code: "TEST_ONLY", display_name: "TEST ONLY customer",
      party_type: "customer", contact_en: "TEST contact", contact_ar: "", name_ar: "" }],
    role_assignments: config.role_assignments,
    discount_ceiling: "20",
    work_calendar: config.work_calendar,
    test_only_label: "TEST ONLY quotation choices",
  };
}
function deferred(call) {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  const entry = { ...call, settled: false,
    resolve(value) { entry.settled = true; resolve(value); },
    reject(error) { entry.settled = true; reject(error); },
  };
  pending.push(entry);
  return { entry, promise };
}
async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
async function renderUnit(unit) {
  await act(async () => root.render(<Profiler id="purchasing-access" onRender={() => {
    const badge = host.querySelector(".pw-transactions .pw-access-chip");
    const receipt = host.querySelector(".pw-transactions form button[type='submit']");
    accessCommits.push({ unit, badge: badge?.textContent,
      confirmed: badge?.classList.contains("is-authorized"), receiptDisabled: receipt?.disabled,
      supplierDisabled: host.querySelector("#pw-receipt-supplier")?.disabled,
      accessText: host.querySelector(".pw-transactions > .pw-notice[role='status']")?.textContent ?? "",
      packingDraft: host.querySelector('[aria-label="Reviewed packing-list receipt draft"]')?.textContent ?? "",
      packingPreparation: host.querySelector(".packing-preparation")?.textContent ?? "",
      packingAcknowledged: host.querySelector('.packing-preparation input[type="checkbox"]')?.checked ?? false,
    });
  }}><PurchasingWorkbench
    selectedUnitId={unit} selectedUnitName={`TEST ONLY Unit ${unit}`}
    isStaff username={username} csrfToken="TEST_ONLY_CSRF"
    locations={[{ id: unit * 11, code: `TEST${unit}`, name: `TEST ONLY location ${unit}`, parent_id: null }]}
    onOpenReports={() => {}}
  /></Profiler>));
  await flush();
}
async function setValue(element, value) {
  assert.ok(element, "Expected rendered form control");
  const prototype = element.tagName === "SELECT" ? window.HTMLSelectElement.prototype
    : element.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, String(value));
    element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}
function formWith(buttonText) {
  const form = [...host.querySelectorAll(".pw-transactions form")]
    .find((form) => [...form.querySelectorAll("button")].some((button) => button.textContent?.startsWith(buttonText)));
  assert.ok(form, `Expected form with ${buttonText}`);
  return form;
}

for (const status of [403, 404]) {
  test(`confirmed current quote-read ${status} after action denial clears detail but retains draft and retry identity`, async () => {
    await setup();
    const form = formWith("Record test payment");
    const request = await payment("125.00");
    quoteHolds.add(101);
    await resolve(request, { detail: "TEST ONLY action no longer allowed" }, 403);
    const recheck = recheckReads(101).at(-1);
    assert.ok(recheck, "Action denial must independently recheck the displayed quotation read");
    await resolve(recheck, { detail: "TEST ONLY current quotation read denied" }, status);
    assert.equal(host.querySelector(".pw-quote") === null, true, "Confirmed read denial removes protected quote details");
    assert.ok(host.querySelector(".pw-quote-card [role=alert]")?.textContent.includes("current quotation read denied"));
    assert.equal(host.querySelector("#pw-quote-id").value, "101");
    assert.equal(host.querySelector(".pw-transactions form") !== null, true, "Keep the transaction component and its other drafts");
    assert.equal([...host.querySelectorAll(".pw-transactions button")].some(button => button.textContent === "Record test payment"), false);
    assert.equal(posts.length, 1, "No automatic POST retry");
    // Permission restoration is a mocked response only. Explicitly reloading
    // the same quote must not discard the legitimate unacknowledged retry key.
    await loadQuote(1);
    assert.equal(control(formWith("Record test payment"), "Amount").value, "125.00", "Hidden action draft survives confirmed access loss");
    const retry = await payment("125.00");
    assert.equal(retry.options.body, request.options.body);
    assert.equal(retry.options.headers["Idempotency-Key"], request.options.headers["Idempotency-Key"]);
    await resolve(retry, { detail: "TEST ONLY still not permitted" }, 400);
  });
}

test("unrelated action denial with successful quote read retains authorized detail and draft", async () => {
  await setup();
  const request = await payment("125.00");
  const before = quoteReads(101).length;
  await resolve(request, { detail: "TEST ONLY action role denied but quotation read allowed" }, 403);
  assert.equal(quoteReads(101).length, before + 1, "Check quote authority rather than infer it from action denial");
  assertSelectedQuote(101);
  assert.equal(control(formWith("Record test payment"), "Amount").value, "125.00");
  assert.ok(transactionText().includes("action role denied but quotation read allowed"));
  assert.equal(posts.length, 1);
});

for (const failure of ["network", "503"]) {
  test(`transient quote-read recheck ${failure} preserves authorized snapshot and draft`, async () => {
    await setup();
    const request = await payment("125.00");
    quoteHolds.add(101);
    await resolve(request, { detail: "TEST ONLY action-specific denial" }, 403);
    const recheck = recheckReads(101).at(-1);
    assert.ok(recheck);
    if (failure === "network") await fail(recheck);
    else await resolve(recheck, { detail: "TEST ONLY temporarily unavailable" }, 503);
    assertSelectedQuote(101);
    assert.equal(control(formWith("Record test payment"), "Amount").value, "125.00");
    assert.ok(host.textContent.includes("Quote access could not be rechecked"));
    assert.equal(posts.length, 1);
  });
}

test("transient action connection failure does not infer quotation-read revocation", async () => {
  await setup();
  const request = await payment("125.00");
  const before = quoteReads(101).length;
  await fail(request);
  assertSelectedQuote(101);
  assert.equal(quoteReads(101).length, before);
  assert.equal(control(formWith("Record test payment"), "Amount").value, "125.00");
  assert.equal(posts.length, 1);
});

for (const obsolete of [200, 403]) {
  test(`obsolete quote-read recheck ${obsolete} cannot change a newer quote selection or BU`, async () => {
    await setup();
    const old = await payment();
    quoteHolds.add(101);
    await resolve(old, { detail: "TEST ONLY action denial" }, 403);
    const recheck = recheckReads(101).at(-1);
    assert.ok(recheck);
    await loadQuote(1, 303);
    await resolve(recheck, obsolete === 200 ? quote(1) : { detail: "TEST ONLY obsolete read denied" }, obsolete);
    assertSelectedQuote(303);
    assert.equal(host.textContent.includes("TEST ONLY obsolete read denied"), false);
    const second = await payment();
    quoteHolds.add(303);
    await resolve(second, { detail: "TEST ONLY second action denial" }, 403);
    const fromA = recheckReads(303).at(-1);
    assert.ok(fromA);
    await renderUnit(2);
    await loadQuote(2);
    await resolve(fromA, obsolete === 200 ? { ...quote(1), quote_id: 303 } : { detail: "TEST ONLY obsolete BU denied" }, obsolete);
    assertSelectedQuote(202);
    assert.equal(host.textContent.includes("TEST ONLY obsolete BU denied"), false);
  });
}

for (const latestDenied of [true, false]) {
  test(`latest overlapping quotation-read recheck wins when latest is ${latestDenied ? "denied" : "allowed"}`, async () => {
    await setup();
    const first = await payment("100.00");
    quoteHolds.add(101);
    await resolve(first, { detail: "TEST ONLY first action denial" }, 403);
    const old = recheckReads(101).at(-1);
    assert.ok(old);
    const next = await payment("110.00");
    quoteHolds.add(101);
    await resolve(next, { detail: "TEST ONLY second action denial" }, 403);
    const current = recheckReads(101).at(-1);
    assert.ok(current && current !== old);
    await resolve(current, latestDenied ? { detail: "TEST ONLY newest quote read denied" } : quote(1), latestDenied ? 403 : 200);
    await resolve(old, latestDenied ? quote(1) : { detail: "TEST ONLY obsolete denied" }, latestDenied ? 200 : 403);
    if (latestDenied) {
      assert.equal(host.querySelector(".pw-quote") === null, true);
      await loadQuote(1);
    } else assertSelectedQuote(101);
    assert.equal(host.textContent.includes("TEST ONLY obsolete denied"), false);
    assert.equal(control(formWith("Record test payment"), "Amount").value, "110.00");
  });
}
function control(form, label) {
  const node = [...form.querySelectorAll("label")].find((node) => node.textContent.startsWith(label));
  return node?.querySelector("input,select,textarea") ?? (node?.htmlFor ? host.querySelector(`#${node.htmlFor}`) : null);
}
test("current quote creation denial also clears a confirmed revoked selected quotation", async () => {
  await setup();
  const creation = await createQuote();
  quoteHolds.add(101);
  await resolve(creation, { detail: "TEST ONLY create denied after grant revocation" }, 403);
  const recheck = recheckReads(101).at(-1);
  assert.ok(recheck);
  await resolve(recheck, { detail: "TEST ONLY quotation access revoked" }, 404);
  assert.equal(host.querySelector(".pw-quote") === null, true);
  assert.equal(host.querySelector("#pw-quote-customer").value, "2");
  assert.equal(host.querySelector("#pw-quote-variant").value, "10");
  assert.equal(posts.length, 1);
});

test("action denial cannot cancel a pending explicit quote lookup", async () => {
  await setup();
  const old = await payment();
  const before = quoteReads(101).length;
  quoteHolds.add(303);
  await setValue(host.querySelector("#pw-quote-id"), "303");
  await act(async () => host.querySelector(".pw-quote-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  ));
  await flush();
  const lookup = recheckReads(303).at(-1);
  assert.ok(lookup);
  await resolve(old, { detail: "TEST ONLY old selected action denied" }, 403);
  assert.equal(quoteReads(101).length, before);
  assert.equal(lookup.settled, false);
  await resolve(lookup, { ...quote(1), quote_id: 303 });
  assertSelectedQuote(303);
});

test("a fresh explicit same-quote lookup supersedes an old read-denial recheck", async () => {
  await setup();
  const request = await payment();
  quoteHolds.add(101);
  await resolve(request, { detail: "TEST ONLY action denial" }, 403);
  const recheck = recheckReads(101).at(-1);
  assert.ok(recheck);
  await loadQuote(1, 101);
  await resolve(recheck, { detail: "TEST ONLY stale same-quote denial" }, 404);
  assertSelectedQuote(101);
  assert.equal(host.textContent.includes("TEST ONLY stale same-quote denial"), false);
});

async function submit(form) {
  assert.equal(form.querySelector('button[type="submit"]').disabled, false, "Action must be enabled by mocked existing access");
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();
}
async function loadQuote(unit, id = unit === 1 ? 101 : 202) {
  await setValue(host.querySelector("#pw-quote-id"), id);
  await act(async () => host.querySelector(".pw-quote-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  ));
  await flush();
  assert.ok(host.querySelector(".pw-quote").textContent.includes(`#${id}`));
}
async function setup(unit = 1) {
  await renderUnit(unit);
  await loadQuote(unit);
}
async function payment(amount = "100.00") {
  const form = formWith("Record test payment");
  await setValue(control(form, "Amount"), amount);
  await submit(form);
  return posts.at(-1);
}
async function resolve(entry, body = {}, status = 200) {
  await act(async () => entry.resolve(json(body, status)));
  await flush();
}
async function fail(entry) {
  await act(async () => entry.reject(new TypeError("TEST ONLY lost response")));
  await flush();
}
const accessCalls = (unit) => calls.filter(({ url }) => url.pathname === "/api/purchasing/quotes/"
  && url.searchParams.get("page_size") === "1" && url.searchParams.get("business_unit_id") === String(unit));
function assertB() {
  assert.equal(host.querySelector("#pw-quote-id").value, "202");
  assert.ok(host.querySelector(".pw-quote")?.textContent.includes("#202"));
  assert.equal(host.querySelector(".pw-quote")?.textContent.includes("#101"), false);
  assert.equal(host.querySelector(".pw-transactions").textContent.includes("TEST ONLY A denial"), false);
  assert.equal(host.querySelector(".pw-transactions").textContent.includes("Simulated payment recorded."), false);
  assert.equal(host.querySelector(".pw-transactions .pw-access-chip").textContent, "Order access confirmed");
}

beforeEach(() => {
  globalThis.DOMParser = window.DOMParser;
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  calls = []; pending = []; posts = []; accessHolds = new Set(); deniedUnits = new Set(); quoteSnapshots = new Map(); quoteHolds = new Set(); commitmentRows = []; warnings = []; accessCommits = [];
  originalError = console.error; originalWarn = console.warn;
  console.error = console.warn = (...args) => warnings.push(args.map(String).join(" "));
  globalThis.fetch = (path, options = {}) => {
    const url = new URL(String(path), "https://test-only.invalid");
    const call = { url, options };
    calls.push(call);
    assert.equal(options.credentials, "same-origin");
    if (url.pathname === "/api/stock-catalog-matches/") {
      return Promise.resolve(json({ matched_codes: url.searchParams.getAll("code") }));
    }
    if (url.pathname === "/api/stock-receipt-candidates/") {
      return Promise.resolve(json({ candidates: [
        { variant_id: 10, variant_sku: "TEST-PACK-CHAIR", variant_description: "TEST ONLY chair",
          item_code: "PACK1", item_description: "TEST ONLY mapped chair", brand_name: "TEST ONLY" },
        { variant_id: 20, variant_sku: "TEST-PACK-TABLE", variant_description: "TEST ONLY table",
          item_code: "PACK2", item_description: "TEST ONLY mapped table", brand_name: "TEST ONLY" },
      ] }));
    }
    if (options.method === "POST") {
      assert.equal(options.signal, undefined, "POSTs must not be aborted on context change");
      assert.equal(options.headers["X-CSRFToken"], "TEST_ONLY_CSRF");
      assert.ok(options.headers["Idempotency-Key"]);
      const request = deferred({ ...call, body: JSON.parse(options.body) });
      posts.push(request.entry);
      return request.promise;
    }
    assert.equal(options.method, "GET");
    const unit = Number(url.searchParams.get("business_unit_id"));
    if (url.pathname === "/api/purchasing/quotes/") {
      if (url.searchParams.get("page_size") === "1" && accessHolds.has(unit)) {
        accessHolds.delete(unit);
        return deferred(call).promise;
      }
      return Promise.resolve(deniedUnits.has(unit) ? json({ detail: "TEST ONLY B denied" }, 403) : json(page()));
    }
    if (url.pathname === "/api/purchasing/quotation-choices/") {
      return Promise.resolve(deniedUnits.has(unit)
        ? json({ detail: "TEST ONLY choices denied" }, 403)
        : json(quotationChoices(unit)));
    }
    if (/^\/api\/purchasing\/quotes\/\d+\/$/.test(url.pathname)) {
      const id = Number(url.pathname.split("/").at(-2));
      if (quoteHolds.delete(id)) return deferred(call).promise;
      return Promise.resolve(json({ ...quote(unit), quote_id: id, ...quoteSnapshots.get(id) }));
    }
    if (/^\/api\/governance\/purchasing\/\d+\/$/.test(url.pathname)) {
      return Promise.resolve(json(configuration(Number(url.pathname.split("/").at(-2)))));
    }
    if (url.pathname === "/api/governance/purchasing/counterparties/") {
      const supplier = url.searchParams.get("party_type") === "supplier";
      return Promise.resolve(json(page([{ counterparty_id: supplier ? 1 : 2,
        party_type: supplier ? "supplier" : "customer", code: "TEST_ONLY", name: "TEST ONLY counterparty" }])));
    }
    if (url.pathname === "/api/purchasing/commitments/") {
      return Promise.resolve(json(page(commitmentRows)));
    }
    if (url.pathname === "/api/purchasing/storage-charges/") return Promise.resolve(json(page()));
    if ([
      "/api/reports/purchase_history/receipts/", "/api/reports/inventory/reservations/",
      "/api/reports/sales/deliveries/",
    ].includes(url.pathname)) return Promise.resolve(json({ detail: "TEST ONLY unrelated report denied" }, 403));
    throw new Error(`Unexpected HTTP mock: ${options.method} ${url.pathname}`);
  };
});

async function preparePackingReceipt(commitment = false) {
  const fileInput = host.querySelector('[aria-label="Choose supplier packing list"]');
  assert.ok(fileInput);
  const content = new TextEncoder().encode("Code,Description,Qty,Unit\nPACK1,TEST ONLY source chair,2,EA\nPACK2,TEST ONLY source table,3,EA\n");
  Object.defineProperty(fileInput, "files", { configurable: true, value: [{
    name: "TEST-ONLY-PACKING.csv", size: content.length, arrayBuffer: async () => content.buffer,
  }] });
  await act(async () => fileInput.dispatchEvent(new Event("change", { bubbles: true })));
  await flush();
  const prepControl = (label) => host.querySelector(`[aria-label="${label}"]`);
  for (const [label, index] of [["Item description", 1], ["Quantity", 2], ["Quantity unit", 3], ["Supplier / item code", 0]]) {
    await setValue(prepControl(`Map ${label}`), index);
  }
  await flush();
  if (commitment) {
    await setValue(prepControl("Receiving mode"), "commitment");
    assert.equal(prepControl("Existing authorized purchase commitment").value, "");
    await setValue(prepControl("Existing authorized purchase commitment"), 77);
  }
  for (const [row, variant, quantity] of [[2, 10, "2.00"], [3, 20, "3.00"]]) {
    await setValue(prepControl(commitment
      ? `Select commitment line for source row ${row}`
      : `Select authorized variant for source row ${row}`), commitment ? (row === 2 ? 501 : 502) : variant);
    await setValue(prepControl(`Existing receiving location for source row ${row}`), 11);
    await setValue(prepControl(`Warehouse-confirmed quantity for source row ${row}`), quantity);
  }
  assert.equal(posts.length, 0);
  const handoff = [...host.querySelectorAll("button")].find((button) => button.textContent === "Use reviewed lines in receipt draft");
  assert.equal(handoff.disabled, true);
  await act(async () => host.querySelector('.packing-preparation input[type="checkbox"]').click());
  assert.equal(handoff.disabled, false);
  await act(async () => handoff.click());
  assert.equal(posts.length, 0, "Reviewing/copying lines is never a transaction");
}

const packingCommitments = () => [501, 502].map((id, index) => ({
  commitment_id: 77, commitment_line_id: id, supplier_id: 1, variant_id: index ? 20 : 10,
  variant_sku: `TEST-EXPLICIT-${id}`, item_description: "TEST ONLY selected commitment item",
  ordered_quantity: "10.00", received_to_date: "1.00", remaining_quantity: "9.00",
}));

const packingControl = (label) => host.querySelector(`[aria-label="${label}"]`);
const reviewedPackingDraft = () => packingControl("Reviewed packing-list receipt draft");
const packingHandoff = () => [...host.querySelectorAll("button")]
  .find((button) => button.textContent === "Use reviewed lines in receipt draft");

function assertPackingIsReadOnly() {
  assert.equal(posts.length, 0, "Upload, sheet selection, mapping and local draft never submit transactions");
  for (const { options } of calls) {
    assert.equal(options.method ?? "GET", "GET", "Every captured request must remain read-only");
    assert.equal(options.body, undefined, "No workbook, measurement, cost or draft is uploaded");
  }
}

async function uploadSyntheticWorkbook() {
  const bytes = syntheticPackingWorkbook();
  let localReads = 0;
  const file = new window.File([bytes], "TEST-ONLY-MULTI.xlsx", {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  // jsdom's File lacks the browser arrayBuffer method; supply the actual
  // generated ZIP bytes, without replacing the real application parser.
  Object.defineProperty(file, "arrayBuffer", { value: async () => {
    localReads++;
    return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  } });
  const input = packingControl("Choose supplier packing list");
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  for (let tries = 0; tries < 100 && !packingControl("Packing list worksheet"); tries++) {
    await act(async () => { await new Promise((done) => setTimeout(done, 10)); });
  }
  const worksheet = packingControl("Packing list worksheet");
  assert.ok(worksheet, "Real ZIP/XML parsing must finish through the file input");
  assert.equal(localReads, 1);
  assert.deepEqual([...worksheet.options].map((option) => option.textContent), [
    "TEST ONLY Cover", "TEST ONLY Receiving", "TEST ONLY Alternate",
  ]);
  assert.equal(worksheet.value, "0");
  assert.equal(reviewedPackingDraft(), null);
  assertPackingIsReadOnly();
  // The cover sheet is intentionally not a receiving source; select explicitly.
  await setValue(worksheet, "1");
  await flush();
}

async function mapWorkbookColumns(alternate = false) {
  const mapping = alternate
    ? [["Supplier / item code", 0], ["Quantity unit", 1], ["Supplier line reference", 2], ["Item description", 3], ["Quantity", 4]]
    : [["Quantity", 0], ["Supplier line reference", 1], ["Quantity unit", 2], ["Item description", 3], ["Supplier / item code", 4],
      ["Supplier cubic volume (m³)", 5], ["Supplier area (m²)", 6]];
  for (const [label, index] of mapping) await setValue(packingControl(`Map ${label}`), index);
  await flush();
  assertPackingIsReadOnly();
}

async function reviewWorkbookCommitment(alternate = false) {
  await setValue(packingControl("Receiving mode"), "commitment");
  assert.equal(packingControl("Existing authorized purchase commitment").value, "");
  await setValue(packingControl("Existing authorized purchase commitment"), alternate ? 88 : 77);
  const rows = alternate ? [[2, 601, "1.25"]] : [[2, 501, "2.50"], [3, 502, "3.00"]];
  for (const [row, lineId, quantity] of rows) {
    const choice = packingControl(`Select commitment line for source row ${row}`);
    assert.equal(choice.value, "", "Commitment lines must never be inferred from supplier codes");
    await setValue(choice, lineId);
    await setValue(packingControl(`Existing receiving location for source row ${row}`), 11);
    assert.equal(packingControl(`Warehouse-confirmed quantity for source row ${row}`).value, "");
    await setValue(packingControl(`Warehouse-confirmed quantity for source row ${row}`), quantity);
  }
  assert.equal(packingHandoff().disabled, true, "Choices alone do not acknowledge source quantities/units");
  assert.equal(reviewedPackingDraft(), null);
  await act(async () => host.querySelector('.packing-preparation input[type="checkbox"]').click());
  assert.equal(packingHandoff().disabled, false);
  await act(async () => packingHandoff().click());
  assertPackingIsReadOnly();
  return reviewedPackingDraft();
}

test("multi-worksheet XLSX upload explicitly maps authorized commitment lines into a local receipt draft only", async () => {
  commitmentRows = packingCommitments().map((row) => ({ ...row, unit_cost: "TEST_ONLY_PRIVATE_COST" }));
  await renderUnit(1);
  await uploadSyntheticWorkbook();
  await mapWorkbookColumns();
  assert.match(host.querySelector(".packing-preview").textContent, /2\.5/);
  assert.match(host.querySelector(".packing-preview").textContent, /CARTON/);
  const draft = await reviewWorkbookCommitment();
  assert.ok(draft);
  assert.match(draft.textContent, /TEST-ONLY-MULTI\.xlsx/);
  assert.match(draft.textContent, /Commitment #77/);
  assert.match(draft.textContent, /Commitment line #501/);
  assert.match(draft.textContent, /Commitment line #502/);
  assert.match(draft.textContent, /Confirmed quantity 2\.50 · TEST1\. Source: 2\.5 CARTON/);
  assert.match(draft.textContent, /Confirmed quantity 3\.00 · TEST1\. Source: 3 EA/);
  assert.doesNotMatch(draft.textContent, /IGNORE-COVER|DO-NOT-RECEIVE|PRIVATE_COST/);
  assert.doesNotMatch(host.querySelector(".packing-preparation").textContent, /PRIVATE_COST/);
  assert.equal(draft.querySelectorAll('input[type="number"]').length, 0, "Commitment costs stay masked");
  assert.equal(host.querySelector("#pw-receipt-supplier").value, "1");
  assert.equal(host.querySelector("#pw-receipt-supplier").disabled, true);
  assert.match(draft.textContent, /Nothing has been posted/);
  assertPackingIsReadOnly();
});

test("changing XLSX worksheets clears column and commitment mappings review and copied draft before fresh review", async () => {
  commitmentRows = [...packingCommitments(), {
    ...packingCommitments()[0], commitment_id: 88, commitment_line_id: 601,
    variant_id: 30, variant_sku: "TEST-ALT-COMMITMENT", item_description: "TEST ONLY alternate commitment",
  }];
  await renderUnit(1);
  await uploadSyntheticWorkbook();
  await mapWorkbookColumns();
  await setValue(packingControl("Warehouse-confirmed volume in m³ for source row 2"), "0.090");
  await reviewWorkbookCommitment();
  await setValue(packingControl("Packing list worksheet"), "2");
  assert.equal(reviewedPackingDraft(), null);
  for (const mapping of host.querySelectorAll(".packing-mapping select")) assert.equal(mapping.value, "");
  assert.equal(packingControl("Receiving mode").value, "direct");
  assert.equal(host.querySelector('.packing-preparation input[type="checkbox"]').checked, false);
  assert.equal(packingHandoff().disabled, true);
  assert.equal(packingControl("Warehouse-confirmed volume in m³ for source row 2").value, "");
  assert.equal(packingControl("Warehouse-confirmed quantity for source row 2").value, "");
  assert.equal(packingControl("Existing receiving location for source row 2").value, "");
  await mapWorkbookColumns(true);
  const alternateDraft = await reviewWorkbookCommitment(true);
  assert.match(alternateDraft.textContent, /Commitment #88/);
  assert.match(alternateDraft.textContent, /Commitment line #601/);
  assert.match(alternateDraft.textContent, /Confirmed quantity 1\.25 · TEST1\. Source: 1\.25 PALLET/);
  assert.doesNotMatch(alternateDraft.textContent, /#501|#502|CARTON|TEST-EXPLICIT-/);
  await setValue(packingControl("Packing list worksheet"), "1");
  assert.equal(reviewedPackingDraft(), null, "Returning to a sheet must not resurrect its previous review");
  assert.equal(host.querySelector('.packing-preparation input[type="checkbox"]').checked, false);
  assert.equal(packingHandoff().disabled, true);
  assertPackingIsReadOnly();
});

for (const denied of [false, true]) {
  test(`reviewed XLSX mappings never cross the first BU switch commit while new access is ${denied ? "denied" : "allowed"}`, async () => {
    commitmentRows = packingCommitments();
    await renderUnit(1);
    await uploadSyntheticWorkbook();
    await mapWorkbookColumns();
    await reviewWorkbookCommitment();
    commitmentRows = [];
    accessHolds.add(2);
    const start = accessCommits.length;
    await renderUnit(2);
    const firstB = accessCommits.slice(start).find((commit) => commit.unit === 2);
    assert.ok(firstB);
    assert.equal(firstB.packingDraft, "");
    assert.equal(firstB.packingAcknowledged, false);
    assert.doesNotMatch(firstB.packingPreparation, /TEST-EXPLICIT-501|TEST-EXPLICIT-502|TEST ONLY location 1/);
    const access = pending.find((entry) => !entry.settled
      && entry.url.pathname === "/api/purchasing/quotes/" && entry.url.searchParams.get("business_unit_id") === "2");
    assert.ok(access);
    if (denied) deniedUnits.add(2);
    await resolve(access, denied ? { detail: "TEST ONLY B receiving access denied" } : page(), denied ? 403 : 200);
    for (const commit of accessCommits.slice(start).filter((entry) => entry.unit === 2)) {
      assert.equal(commit.packingDraft, "");
      assert.equal(commit.packingAcknowledged, false);
      assert.doesNotMatch(commit.packingPreparation, /TEST-EXPLICIT-501|TEST-EXPLICIT-502|TEST ONLY location 1/);
    }
    assert.equal(accessCommits.at(-1).confirmed, !denied);
    assert.equal(packingHandoff().disabled, true);
    commitmentRows = packingCommitments();
    await renderUnit(1);
    assert.equal(reviewedPackingDraft(), null, "A to B to A requires a fresh explicit review");
    assert.equal(host.querySelector('.packing-preparation input[type="checkbox"]').checked, false);
    assert.equal(packingHandoff().disabled, true);
    assertPackingIsReadOnly();
  });
}

test("reviewed packing commitment draft posts only explicit selected lines with fixed supplier and exact retry identity", async () => {
  commitmentRows = packingCommitments();
  await renderUnit(1);
  await preparePackingReceipt(true);
  const draft = host.querySelector('[aria-label="Reviewed packing-list receipt draft"]');
  assert.ok(draft);
  assert.match(draft.textContent, /Commitment #77/);
  assert.equal(draft.querySelectorAll('input[type="number"]').length, 0, "No committed cost exposure or editable unit values");
  assert.equal(host.querySelector("#pw-receipt-supplier").value, "1");
  assert.equal(host.querySelector("#pw-receipt-supplier").disabled, true);
  assert.equal(posts.length, 0, "Upload, mapping and acknowledged review never post");
  const form = formWith("Record synthetic receipt");
  await submit(form);
  const first = posts[0];
  assert.deepEqual(first.body, {
    business_unit_id: 1, supplier_id: 1, purchase_commitment_id: 77, charges: [],
    lines: [
      { variant_id: 10, commitment_line_id: 501, location_id: 11, quantity: "2.00" },
      { variant_id: 20, commitment_line_id: 502, location_id: 11, quantity: "3.00" },
    ],
  });
  assert.equal(JSON.stringify(first.body).includes("sourceUnit"), false);
  assert.equal(JSON.stringify(first.body).includes("unit_cost"), false);
  await fail(first);
  await submit(form);
  assert.equal(posts.length, 2);
  const replay = posts[1];
  assert.equal(replay.options.body, first.options.body);
  assert.equal(replay.options.headers["Idempotency-Key"], first.options.headers["Idempotency-Key"]);
  commitmentRows = packingCommitments().map((line, index) => ({
    ...line, received_to_date: index ? "4.00" : "3.00", remaining_quantity: index ? "6.00" : "7.00",
  }));
  await resolve(replay, { purchase_receipt_id: 901, purchase_commitment_id: 77 }, 201);
  assert.equal(host.querySelector('[aria-label="Reviewed packing-list receipt draft"]'), null);
  assert.match(host.querySelector("#pw-commitment-progress-title").parentElement.textContent, /6.00/);
});

test("editing a selected packing commitment invalidates its copied draft and context round trip requires review", async () => {
  commitmentRows = packingCommitments();
  await renderUnit(1); await preparePackingReceipt(true);
  await setValue(host.querySelector('[aria-label="Select commitment line for source row 2"]'), "");
  assert.equal(host.querySelector('[aria-label="Reviewed packing-list receipt draft"]'), null);
  assert.equal(posts.length, 0);
  await setValue(host.querySelector('[aria-label="Select commitment line for source row 2"]'), 501);
  const handoff = [...host.querySelectorAll("button")].find((button) => button.textContent === "Use reviewed lines in receipt draft");
  await act(async () => host.querySelector('.packing-preparation input[type="checkbox"]').click());
  await act(async () => handoff.click());
  assert.ok(host.querySelector('[aria-label="Reviewed packing-list receipt draft"]'));
  commitmentRows = [];
  await renderUnit(2);
  assert.equal(host.querySelector('[aria-label="Reviewed packing-list receipt draft"]'), null);
  commitmentRows = packingCommitments();
  await renderUnit(1);
  assert.equal(host.querySelector('[aria-label="Reviewed packing-list receipt draft"]'), null);
  assert.equal(posts.length, 0);
});

test("reviewed packing lines require explicit receipt submission and retain exact payload/key on retry", async () => {
  await renderUnit(1);
  await preparePackingReceipt();
  const draft = host.querySelector('[aria-label="Reviewed packing-list receipt draft"]');
  assert.ok(draft);
  assert.match(draft.textContent, /Nothing has been posted/);
  assert.match(draft.textContent, /TEST-PACK-CHAIR/);
  assert.match(draft.textContent, /TEST-PACK-TABLE/);
  const form = formWith("Record synthetic receipt");
  await setValue(host.querySelector("#pw-receipt-supplier"), 1);
  await submit(form);
  assert.equal(posts.length, 0, "Missing synthetic cost inputs do not post a receipt");
  assert.match(host.textContent, /Review every mapped line/);
  await setValue(control(form, "Packing row 2"), "1000.00");
  await setValue(control(form, "Packing row 3"), "500.00");
  await submit(form);
  assert.equal(posts.length, 1);
  const first = posts[0];
  assert.equal(first.url.pathname, "/api/purchasing/receipts/");
  assert.deepEqual(first.body, {
    business_unit_id: 1, supplier_id: 1, charges: [],
    lines: [
      { variant_id: 10, location_id: 11, quantity: "2.00", unit_cost: "1000.00" },
      { variant_id: 20, location_id: 11, quantity: "3.00", unit_cost: "500.00" },
    ],
  });
  await act(async () => first.resolve(json({ detail: "TEST ONLY receipt validation retry" }, 400)));
  await flush();
  assert.equal(posts.length, 1);
  await submit(form);
  assert.equal(posts.length, 2);
  assert.equal(posts[1].options.body, first.options.body);
  assert.equal(posts[1].options.headers["Idempotency-Key"], first.options.headers["Idempotency-Key"]);
  await act(async () => posts[1].resolve(json({ receipt_id: 55, synthetic_only: true }, 201)));
  await flush();
  assert.equal(host.querySelector('[aria-label="Reviewed packing-list receipt draft"]'), null);
  assert.match(host.textContent, /Synthetic goods receipt recorded/);
});

test("a reviewed packing receipt cannot carry its mapped draft into another business unit", async () => {
  await renderUnit(1); await preparePackingReceipt();
  assert.ok(host.querySelector('[aria-label="Reviewed packing-list receipt draft"]'));
  await renderUnit(2);
  assert.equal(host.querySelector('[aria-label="Reviewed packing-list receipt draft"]'), null);
  assert.equal(posts.length, 0);
  const variants = [...host.querySelectorAll('.packing-preparation select[aria-label^="Select authorized variant"]')];
  assert.ok(variants.every((select) => select.value === ""), "New unit requires explicit fresh mapping/review");
  await renderUnit(1);
  assert.equal(Boolean(host.querySelector('[aria-label="Reviewed packing-list receipt draft"]')), false,
    "Returning to the original unit must not resurrect an invalidated reviewed draft");
  assert.equal(posts.length, 0);
});

test("editing an uploaded source invalidates copied receipt lines before any posting", async () => {
  await renderUnit(1); await preparePackingReceipt();
  assert.ok(host.querySelector('[aria-label="Reviewed packing-list receipt draft"]'));
  await setValue(host.querySelector('[aria-label="Map Quantity unit"]'), "");
  assert.equal(host.querySelector('[aria-label="Reviewed packing-list receipt draft"]'), null);
  const handoff = [...host.querySelectorAll("button")].find((button) => button.textContent === "Use reviewed lines in receipt draft");
  assert.equal(handoff.disabled, true);
  assert.equal(posts.length, 0);
});
afterEach(async () => {
  try {
    // Remove UI first, then let every fake request finish; never abort a POST.
    await act(async () => root.unmount());
    await act(async () => {
      for (const entry of pending) if (!entry.settled) entry.resolve(json({ quote_id: 101 }));
      await new Promise(setImmediate);
    });
  } finally {
    host.remove(); globalThis.fetch = originalFetch; globalThis.DOMParser = originalDOMParser;
    console.error = originalError; console.warn = originalWarn;
  }
  assert.deepEqual(warnings, []);
});

for (const first of ["receipt", "quote"]) {
  test(`overlapping creation outcomes remain distinct when ${first} validation failure finishes first`, async () => {
    await setup();
    const receiptForm = formWith("Record synthetic receipt");
    const quoteForm = formWith("Create synthetic quote");
    await setValue(host.querySelector("#pw-receipt-supplier"), 1);
    await setValue(control(receiptForm, "Catalog variant ID"), 10);
    await setValue(control(receiptForm, "Receiving location"), 11);
    await setValue(control(receiptForm, "Supplier unit cost"), "1000.00");
    await setValue(control(quoteForm, "Item / variant specifications"), '{"finish":"TEST ONLY oak"}');
    await submit(receiptForm);
    const receiptPost = posts.at(-1);
    const quotePost = await createQuote();
    assert.equal(receiptPost.url.pathname, "/api/purchasing/receipts/");
    assert.equal(quotePost.url.pathname, "/api/purchasing/quotes/");
    assert.equal(receiptPost.settled, false);
    assert.equal(quotePost.settled, false);
    const intents = posts.map((post) => ({
      path: post.url.pathname, body: post.options.body, key: post.options.headers["Idempotency-Key"],
    }));
    assert.equal(intents.length, 2);
    assert.notEqual(intents[0].key, intents[1].key);
    const getCount = calls.filter((call) => call.options.method === "GET").length;
    const selectedText = host.querySelector(".pw-quote").textContent;
    const drafts = () => [receiptForm, quoteForm].map((form) =>
      [...form.querySelectorAll("input,select,textarea")].map((node) => [node.tagName, node.id, node.value]));
    const originalDrafts = drafts();
    const receiptFailure = "TEST ONLY receipt validation failed";
    const quoteFailure = "TEST ONLY quote creation validation failed";
    const receiptFirst = first === "receipt";
    await resolve(receiptFirst ? receiptPost : quotePost,
      { detail: receiptFirst ? receiptFailure : quoteFailure }, 400);
    assert.ok(transactionText().includes(receiptFirst ? receiptFailure : quoteFailure));
    assert.equal((receiptFirst ? quotePost : receiptPost).settled, false);
    assert.equal(calls.filter((call) => call.options.method === "GET").length, getCount);
    await resolve(receiptFirst ? quotePost : receiptPost,
      { detail: receiptFirst ? quoteFailure : receiptFailure }, 400);
    for (const [operation, message] of [["Receipt creation", receiptFailure], ["Quote creation", quoteFailure]]) {
      const notice = [...host.querySelectorAll(".pw-transactions [role='alert']")]
        .find((node) => node.textContent.includes(message));
      assert.ok(notice, `Expected retained outcome: ${message}`);
      assert.ok(notice.textContent.includes(operation), "Creation outcome must identify its operation");
    }
    assertSelectedQuote(101);
    assert.equal(host.querySelector(".pw-quote").textContent, selectedText);
    assert.equal(formWith("Record synthetic receipt"), receiptForm);
    assert.equal(formWith("Create synthetic quote"), quoteForm);
    assert.deepEqual(drafts(), originalDrafts);
    assert.equal(receiptForm.querySelector('button[type="submit"]').disabled, false);
    assert.equal(quoteForm.querySelector('button[type="submit"]').disabled, false);
    assert.equal(posts.length, 2, "No automatic or additional transaction requests");
    assert.equal(calls.filter((call) => call.options.method === "GET").length, getCount,
      "400 creation outcomes must not refresh or recheck authorization");
    assert.equal(posts.every((post) => post.settled && post.options.signal === undefined), true);
    assert.deepEqual(posts.map((post) => ({
      path: post.url.pathname, body: post.options.body, key: post.options.headers["Idempotency-Key"],
    })), intents);
  });
}

async function startFeedbackPair() {
  await setup();
  const paymentForm = formWith("Record test payment");
  const readinessForm = formWith("Record readiness");
  const paymentPost = await payment();
  await setValue(control(readinessForm, "Notification note"), "TEST ONLY concurrent readiness");
  await submit(readinessForm);
  const readinessPost = posts.at(-1);
  assert.notEqual(paymentPost, readinessPost);
  assert.equal(paymentPost.settled, false);
  assert.equal(readinessPost.settled, false);
  assert.equal(paymentPost.url.pathname, "/api/purchasing/quotes/101/payments/");
  assert.equal(readinessPost.url.pathname, "/api/purchasing/quotes/101/ready/");
  return { paymentForm, readinessForm, paymentPost, readinessPost };
}
const paymentFailure = "TEST ONLY payment validation failed";
const readinessFailure = "TEST ONLY readiness validation failed";
for (const first of ["payment", "readiness"]) {
  test(`per-action pending controls stay owned until their own response when ${first} finishes first`, async () => {
    const pair = await startFeedbackPair();
    const paymentButton = pair.paymentForm.querySelector('button[type="submit"]');
    const readinessButton = pair.readinessForm.querySelector('button[type="submit"]');
    const intents = posts.map((post) => ({
      path: post.url.pathname, body: post.options.body, key: post.options.headers["Idempotency-Key"],
    }));
    const getCount = calls.filter((call) => call.options.method === "GET").length;
    const quoteText = host.querySelector(".pw-quote").textContent;
    assert.equal(posts.length, 2);
    assert.notEqual(intents[0].key, intents[1].key);
    assert.equal(paymentButton.disabled, true, "Payment must stay disabled while readiness also runs");
    assert.equal(paymentButton.textContent, "Recording…");
    assert.equal(readinessButton.disabled, true);
    assert.equal(readinessButton.textContent, "Sending…");
    const paymentFirst = first === "payment";
    await resolve(paymentFirst ? pair.paymentPost : pair.readinessPost,
      { detail: paymentFirst ? paymentFailure : readinessFailure }, 400);
    assert.equal(paymentButton.disabled, !paymentFirst);
    assert.equal(paymentButton.textContent, paymentFirst ? "Record test payment" : "Recording…");
    assert.equal(readinessButton.disabled, paymentFirst);
    assert.equal(readinessButton.textContent, paymentFirst ? "Sending…" : "Record readiness");
    assert.equal((paymentFirst ? pair.readinessPost : pair.paymentPost).settled, false);
    await resolve(paymentFirst ? pair.readinessPost : pair.paymentPost,
      { detail: paymentFirst ? readinessFailure : paymentFailure }, 400);
    assert.equal(paymentButton.disabled, false);
    assert.equal(paymentButton.textContent, "Record test payment");
    assert.equal(readinessButton.disabled, false);
    assert.equal(readinessButton.textContent, "Record readiness");
    assert.equal(formWith("Record test payment"), pair.paymentForm);
    assert.equal(formWith("Record readiness"), pair.readinessForm);
    assert.equal(control(pair.paymentForm, "Amount").value, "100.00");
    assert.equal(control(pair.readinessForm, "Notification note").value, "TEST ONLY concurrent readiness");
    assertSelectedQuote(101);
    assert.equal(host.querySelector(".pw-quote").textContent, quoteText);
    assertActionFailure("Simulated payment", paymentFailure);
    assertActionFailure("Readiness notification", readinessFailure);
    assert.equal(posts.length, 2, "No automatic or additional POSTs");
    assert.equal(calls.filter((call) => call.options.method === "GET").length, getCount,
      "Validation failures must not refresh or recheck access");
    assert.equal(posts.every((post) => post.settled && post.options.signal === undefined), true);
    assert.deepEqual(posts.map((post) => ({
      path: post.url.pathname, body: post.options.body, key: post.options.headers["Idempotency-Key"],
    })), intents);
  });
}
function assertActionFailure(action, message) {
  const notice = [...host.querySelectorAll(".pw-transactions [role='alert']")]
    .find((node) => node.textContent.includes(message));
  assert.ok(notice, `Expected retained outcome: ${message}`);
  assert.ok(notice.textContent.includes(action), "Outcome must identify its action");
  assert.ok(notice.textContent.includes("Quote #101"), "Outcome must identify its quote");
}
for (const order of ["newer", "older"]) {
  test(`overlapping action outcomes remain distinct when ${order} validation failure finishes first`, async () => {
    const pair = await startFeedbackPair();
    const intents = posts.map((post) => ({
      path: post.url.pathname, body: post.options.body, key: post.options.headers["Idempotency-Key"],
    }));
    assert.notEqual(intents[0].key, intents[1].key);
    const getCount = calls.filter((call) => call.options.method === "GET").length;
    const quoteText = host.querySelector(".pw-quote").textContent;
    const olderFirst = order === "older";
    await resolve(olderFirst ? pair.paymentPost : pair.readinessPost,
      { detail: olderFirst ? paymentFailure : readinessFailure }, 400);
    assert.ok(transactionText().includes(olderFirst ? paymentFailure : readinessFailure));
    if (olderFirst) {
      const button = pair.readinessForm.querySelector('button[type="submit"]');
      assert.equal(button.disabled, true, "Older completion cannot clear the current busy owner");
      assert.equal(button.textContent, "Sending…");
    }
    await resolve(olderFirst ? pair.readinessPost : pair.paymentPost,
      { detail: olderFirst ? readinessFailure : paymentFailure }, 400);
    assertActionFailure("Simulated payment", paymentFailure);
    assertActionFailure("Readiness notification", readinessFailure);
    assertSelectedQuote(101);
    assert.equal(host.querySelector(".pw-quote").textContent, quoteText);
    assert.equal(formWith("Record test payment") === pair.paymentForm, true);
    assert.equal(formWith("Record readiness") === pair.readinessForm, true);
    assert.equal(control(pair.paymentForm, "Amount").value, "100.00");
    assert.equal(control(pair.readinessForm, "Notification note").value, "TEST ONLY concurrent readiness");
    assert.equal(pair.paymentForm.querySelector('button[type="submit"]').disabled, false);
    assert.equal(pair.readinessForm.querySelector('button[type="submit"]').disabled, false);
    assert.equal(posts.length, 2, "No automatic transaction retries");
    assert.equal(calls.filter((call) => call.options.method === "GET").length, getCount,
      "400s must not trigger authorization rechecks or quote/register refresh");
    assert.equal(posts.every((post) => post.settled && post.options.signal === undefined), true);
    assert.deepEqual(posts.map((post) => ({
      path: post.url.pathname, body: post.options.body, key: post.options.headers["Idempotency-Key"],
    })), intents);
  });
}

test("action-result retry clears only its own failure and reuses the exact request identity", async () => {
  const pair = await startFeedbackPair();
  await resolve(pair.readinessPost, { detail: readinessFailure }, 400);
  await resolve(pair.paymentPost, { detail: paymentFailure }, 400);
  assert.equal(posts.length, 2);
  const retry = await payment();
  assert.equal(retry.options.body, pair.paymentPost.options.body);
  assert.equal(retry.options.headers["Idempotency-Key"], pair.paymentPost.options.headers["Idempotency-Key"]);
  assert.equal(transactionText().includes(paymentFailure), false);
  assertActionFailure("Readiness notification", readinessFailure);
  await resolve(retry, { detail: "TEST ONLY retried payment validation failed" }, 400);
  assertActionFailure("Simulated payment", "TEST ONLY retried payment validation failed");
  assertActionFailure("Readiness notification", readinessFailure);
  assert.equal(posts.length, 3, "Only the explicitly submitted retry is added");
});

test("action-result notices clear on explicit navigation and business-unit changes without becoming history", async () => {
  const pair = await startFeedbackPair();
  await resolve(pair.readinessPost, { detail: readinessFailure }, 400);
  await resolve(pair.paymentPost, { detail: paymentFailure }, 400);
  assertActionFailure("Readiness notification", readinessFailure);
  assertActionFailure("Simulated payment", paymentFailure);
  for (const id of [303, 101]) {
    await loadQuote(1, id);
    assert.equal(transactionText().includes(readinessFailure), false);
    assert.equal(transactionText().includes(paymentFailure), false);
  }
  const again = await startFeedbackPair();
  await resolve(again.readinessPost, { detail: readinessFailure }, 400);
  await resolve(again.paymentPost, { detail: paymentFailure }, 400);
  await setup(2);
  await setup(1);
  assert.equal(transactionText().includes(readinessFailure), false);
  assert.equal(transactionText().includes(paymentFailure), false);
});

test("action-result success remains identifiable during a failed automatic refresh but clears for explicit navigation", async () => {
  await setup();
  const post = await payment();
  quoteHolds.add(101);
  await resolve(post, { quote_id: 101, payment_id: 1 });
  const refresh = pending.find((entry) => !entry.settled
    && entry.options.method === "GET" && entry.url.pathname === "/api/purchasing/quotes/101/");
  assert.ok(refresh);
  assert.ok(transactionText().includes("Simulated payment recorded."));
  assert.ok(transactionText().includes("Quote #101"));
  await resolve(refresh, { detail: "TEST ONLY quote 101 refresh failed" }, 400);
  assert.ok(host.textContent.includes("TEST ONLY quote 101 refresh failed"));
  assert.ok(transactionText().includes("Simulated payment recorded."),
    "A failed read refresh must not hide the accepted POST outcome");
  assert.equal(posts.length, 1);
  quoteHolds.add(303);
  await setValue(host.querySelector("#pw-quote-id"), 303);
  await act(async () => host.querySelector(".pw-quote-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  ));
  await flush();
  assert.equal(transactionText().includes("Simulated payment recorded."), false);
  assert.equal(transactionText().includes("Quote #101"), false);
  const lookup = pending.find((entry) => !entry.settled
    && entry.options.method === "GET" && entry.url.pathname === "/api/purchasing/quotes/303/");
  await resolve(lookup, { ...quote(1), quote_id: 303 });
  assertSelectedQuote(303);
});

// Both fake actions start while access is confirmed. Their rejected outcomes
// independently request current-BU authorization; neither reaches a real server.
async function overlappingSameUnitChecks() {
  await setup();
  assert.equal(accessCommits.at(-1).badge, "Order access confirmed");
  const transactions = host.querySelector(".pw-transactions");
  const quoteText = host.querySelector(".pw-quote").textContent;
  const reads = quoteReads(101).length;
  const first = await payment();
  const readiness = formWith("Record readiness");
  await setValue(control(readiness, "Notification note"), "TEST ONLY rejected readiness");
  await submit(readiness);
  const second = posts.at(-1);
  assert.notEqual(first, second);
  assert.equal(first.settled, false);
  assert.equal(second.settled, false);
  assert.equal(second.url.pathname, "/api/purchasing/quotes/101/ready/");
  const intents = [first, second].map((post) => ({
    body: post.options.body, key: post.options.headers["Idempotency-Key"],
  }));
  const unresolvedChecks = () => pending.filter((entry) => !entry.settled
    && entry.options.method === "GET" && entry.url.pathname === "/api/purchasing/quotes/"
    && entry.url.searchParams.get("page_size") === "1"
    && entry.url.searchParams.get("business_unit_id") === "1");
  accessHolds.add(1);
  await resolve(first, { detail: "TEST ONLY first action rejected" }, 403);
  const older = unresolvedChecks()[0];
  assert.ok(older);
  accessHolds.add(1);
  await resolve(second, { detail: "TEST ONLY second action rejected" }, 404);
  const newer = unresolvedChecks().find((entry) => entry !== older);
  assert.ok(newer);
  assert.equal(older.settled, false);
  assert.equal(accessCalls(1).length, 3);
  assert.equal(accessCommits.at(-1).badge, "Checking access");
  assert.equal(accessCommits.at(-1).receiptDisabled, true);
  return { older, newer, transactions, quoteText, reads, intents };
}
function assertSameUnitFacts(fixture) {
  assert.equal(host.querySelector(".pw-transactions") === fixture.transactions, true);
  assertSelectedQuote(101);
  assert.equal(host.querySelector(".pw-quote").textContent, fixture.quoteText);
  assert.equal(quoteReads(101).length, fixture.reads + 2, "Each denial independently rechecks quotation read authority without retrying a transaction");
  assert.equal(control(formWith("Record test payment"), "Amount").value, "100.00");
  assert.equal(control(formWith("Record readiness"), "Notification note").value, "TEST ONLY rejected readiness");
  assert.equal(posts.length, 2, "No automatic retry or extra transaction");
  assert.equal(posts.every((post) => post.settled && post.options.signal === undefined), true);
  assert.deepEqual(posts.map((post) => ({
    body: post.options.body, key: post.options.headers["Idempotency-Key"],
  })), fixture.intents);
}
function assertAccessCommitsUnchanged(start, expected) {
  for (const commit of [accessCommits.at(-1), ...accessCommits.slice(start)]) {
    assert.deepEqual(commit, expected, "An obsolete check cannot change current status/message/loading/controls");
  }
}

for (const denial of [403, 404]) {
  test(`same-BU newer ${denial} access denial remains authoritative after older success`, async () => {
    const fixture = await overlappingSameUnitChecks();
    await resolve(fixture.newer, { detail: `TEST ONLY latest access denied ${denial}` }, denial);
    const expected = accessCommits.at(-1);
    assert.equal(expected.badge, "Actions gated");
    assert.equal(expected.receiptDisabled, true);
    assert.ok(expected.accessText.includes(`TEST ONLY latest access denied ${denial}`));
    const start = accessCommits.length;
    await resolve(fixture.older, page());
    assertAccessCommitsUnchanged(start, expected);
    assert.equal(formWith("Record test payment").querySelector('button[type="submit"]').disabled, true);
    assertSameUnitFacts(fixture);
  });
}

for (const failure of ["network", "HTTP 500"]) {
  test(`same-BU older ${failure} failure cannot replace a newer pending access check`, async () => {
    const fixture = await overlappingSameUnitChecks();
    const expected = accessCommits.at(-1);
    const start = accessCommits.length;
    if (failure === "network") await fail(fixture.older);
    else await resolve(fixture.older, { detail: "TEST ONLY obsolete access failure" }, 500);
    assert.equal(fixture.newer.settled, false);
    assertAccessCommitsUnchanged(start, expected);
    await resolve(fixture.newer, page());
    assert.equal(accessCommits.at(-1).badge, "Order access confirmed");
    assert.equal(accessCommits.at(-1).receiptDisabled, false);
    assert.equal(accessCommits.at(-1).accessText, "");
    assertSameUnitFacts(fixture);
  });
}

for (const denial of [403, 404]) {
  test(`same-BU older ${denial} access denial cannot replace newer confirmed access`, async () => {
    const fixture = await overlappingSameUnitChecks();
    await resolve(fixture.newer, page());
    const expected = accessCommits.at(-1);
    assert.equal(expected.badge, "Order access confirmed");
    assert.equal(expected.receiptDisabled, false);
    assert.equal(expected.accessText, "");
    const start = accessCommits.length;
    await resolve(fixture.older, { detail: `TEST ONLY obsolete access denied ${denial}` }, denial);
    assertAccessCommitsUnchanged(start, expected);
    assert.equal(formWith("Record test payment").querySelector('button[type="submit"]').disabled, false);
    assertSameUnitFacts(fixture);
  });
}

for (const outcome of ["allowed", "denied"]) {
  test(`first B commit gates receiving before B access is ${outcome} without discarding receipt drafts`, async () => {
    await renderUnit(1);
    const form = formWith("Record synthetic receipt");
    const transactions = host.querySelector(".pw-transactions");
    assert.equal(accessCommits.at(-1).badge, "Order access confirmed");
    assert.equal(form.querySelector('button[type="submit"]').disabled, false);
    await setValue(host.querySelector("#pw-receipt-supplier"), 1);
    await setValue(control(form, "Catalog variant ID"), 10);
    await setValue(control(form, "Receiving location"), 11);
    await setValue(control(form, "Quantity"), "3");
    await setValue(control(form, "Supplier unit cost"), "1250.00");

    accessHolds.add(2);
    const start = accessCommits.length;
    await renderUnit(2);
    const firstB = accessCommits.slice(start).find((commit) => commit.unit === 2);
    assert.ok(firstB);
    assert.equal(firstB.badge, "Checking access");
    assert.equal(firstB.confirmed, false);
    assert.equal(firstB.receiptDisabled, true);
    assert.equal(firstB.supplierDisabled, true);
    for (const commit of accessCommits.slice(start)) {
      assert.equal(commit.confirmed, false);
      assert.equal(commit.receiptDisabled, true);
    }
    const check = pending.find((entry) => entry.url.pathname === "/api/purchasing/quotes/"
      && entry.url.searchParams.get("business_unit_id") === "2" && !entry.settled);
    assert.ok(check);
    await resolve(check, outcome === "allowed" ? page() : { detail: "TEST ONLY B access denied" },
      outcome === "allowed" ? 200 : 403);
    assert.equal(accessCommits.at(-1).badge, outcome === "allowed" ? "Order access confirmed" : "Actions gated");
    assert.equal(accessCommits.at(-1).receiptDisabled, outcome !== "allowed");
    assert.equal(accessCommits.at(-1).supplierDisabled, outcome !== "allowed");
    if (outcome === "denied") assert.ok(host.querySelector(".pw-transactions").textContent.includes("TEST ONLY B access denied"));
    assert.equal(host.querySelector(".pw-transactions") === transactions, true);
    assert.equal(formWith("Record synthetic receipt") === form, true);
    assert.equal(control(form, "Catalog variant ID").value, "10");
    assert.equal(control(form, "Quantity").value, "3");
    assert.equal(control(form, "Supplier unit cost").value, "1250.00");

    // The old receiving location is not inserted into B's authorized options.
    assert.equal([...control(form, "Receiving location").options].some((option) => option.value === "11"), false);
    await renderUnit(1);
    assert.equal(control(form, "Receiving location").value, "11", "Draft remains intact when A is selected again");
    assert.equal(host.querySelector("#pw-receipt-supplier").value, "1");
    assert.equal(posts.length, 0);
    assert.equal(calls.every(({ options }) => options.method === "GET"), true);
  });
}

for (const bState of ["pending", "allowed", "denied"]) {
  for (const oldResult of ["success", "denial"]) {
    test(`obsolete A access ${oldResult} cannot change B ${bState} receiving status`, async () => {
      accessHolds.add(1);
      await renderUnit(1);
      const oldCheck = pending.find((entry) => !entry.settled);
      assert.ok(oldCheck);
      accessHolds.add(2);
      await renderUnit(2);
      const bCheck = pending.find((entry) => !entry.settled
        && entry.url.searchParams.get("business_unit_id") === "2");
      assert.ok(bCheck);
      if (bState !== "pending") await resolve(bCheck,
        bState === "allowed" ? page() : { detail: "TEST ONLY current B denied" },
        bState === "allowed" ? 200 : 403);
      const start = accessCommits.length;
      await resolve(oldCheck, oldResult === "success" ? page() : { detail: "TEST ONLY obsolete A denied" },
        oldResult === "success" ? 200 : 403);
      const expectedBadge = bState === "pending" ? "Checking access"
        : bState === "allowed" ? "Order access confirmed" : "Actions gated";
      for (const commit of [accessCommits.at(-1), ...accessCommits.slice(start)]) {
        assert.equal(commit.unit, 2);
        assert.equal(commit.badge, expectedBadge);
        assert.equal(commit.confirmed, bState === "allowed");
        assert.equal(commit.receiptDisabled, bState !== "allowed");
      }
      const text = host.querySelector(".pw-transactions").textContent;
      assert.equal(text.includes("TEST ONLY obsolete A denied"), false);
      if (bState === "denied") assert.ok(text.includes("TEST ONLY current B denied"));
      if (bState === "pending") {
        assert.equal(bCheck.settled, false);
        await resolve(bCheck, page());
        assert.equal(accessCommits.at(-1).badge, "Order access confirmed");
        assert.equal(accessCommits.at(-1).receiptDisabled, false);
      }
      assert.equal(posts.length, 0);
      assert.equal(calls.every(({ options }) => options.method === "GET"), true);
    });
  }
}

test("late A payment success cannot reopen A or show success under B", async () => {
  await setup();
  const post = await payment();
  const transactions = host.querySelector(".pw-transactions");
  await setup(2);
  const before = calls.length;
  assert.equal(post.settled, false, "Original POST is still running");
  assert.equal(host.querySelector(".pw-transactions") === transactions, true);
  await resolve(post, { payment_id: 1, quote_id: 101 });
  assertB();
  assert.equal(calls.slice(before).some(({ url }) => url.searchParams.get("business_unit_id") === "1"), false);
  assert.equal(posts.length, 1, "No automatic second POST");
});

test("late A denial cannot show an A error or launch an A access check under B", async () => {
  await setup();
  const post = await payment();
  await setup(2);
  const count = accessCalls(1).length;
  await resolve(post, { detail: "TEST ONLY A denial" }, 403);
  assertB();
  assert.equal(accessCalls(1).length, count);
});

test("A denial access check already in flight cannot overwrite B ready status", async () => {
  await setup();
  const post = await payment();
  accessHolds.add(1);
  await resolve(post, { detail: "TEST ONLY A denial" }, 403);
  const check = pending.find((entry) => entry.options.method === "GET" && !entry.settled);
  assert.ok(check);
  await setup(2);
  await resolve(check, { detail: "TEST ONLY A access unavailable" }, 403);
  assertB();
  assert.equal(formWith("Record test payment").querySelector('button[type="submit"]').disabled, false);
});

test("late A success cannot replace B denied order access", async () => {
  await setup();
  const post = await payment();
  deniedUnits.add(2);
  await renderUnit(2);
  await resolve(post, { payment_id: 1, quote_id: 101 });
  assert.equal(host.querySelector("#pw-quote-id").value, "");
  assert.equal(host.querySelector(".pw-quote") === null, true);
  assert.equal(host.querySelector(".pw-transactions .pw-access-chip").textContent, "Actions gated");
  assert.equal(host.querySelector(".pw-transactions").textContent.includes("Simulated payment recorded."), false);
});

test("late A denial cannot reauthorize a denied B through an A access check", async () => {
  await setup();
  const post = await payment();
  deniedUnits.add(2);
  await renderUnit(2);
  const count = accessCalls(1).length;
  await resolve(post, { detail: "TEST ONLY A denial" }, 403);
  assert.equal(accessCalls(1).length, count);
  assert.equal(host.querySelector(".pw-transactions .pw-access-chip").textContent, "Actions gated");
  assert.equal(host.querySelector(".pw-transactions").textContent.includes("TEST ONLY A denial"), false);
});

test("an A completion cannot clear B readiness busy indicator or its POST", async () => {
  await setup();
  const old = await payment();
  await setup(2);
  const form = formWith("Record readiness");
  await setValue(control(form, "Notification note"), "TEST ONLY B notification");
  await submit(form);
  const current = posts.at(-1);
  assert.equal(current.url.pathname, "/api/purchasing/quotes/202/ready/");
  await resolve(old, { payment_id: 1, quote_id: 101 });
  assert.equal(form.querySelector('button[type="submit"]').textContent, "Sending…");
  assert.equal(form.querySelector('button[type="submit"]').disabled, true);
  assert.equal(current.settled, false);
  await resolve(current, { quote_id: 202 });
  assert.equal(form.querySelector('button[type="submit"]').textContent, "Record readiness");
});

test("B can start a payment while A is pending and A cannot clear B payment busy state", async () => {
  await setup();
  const old = await payment();
  await setup(2);
  const current = await payment();
  assert.equal(old.settled, false);
  assert.notEqual(current.options.headers["Idempotency-Key"], old.options.headers["Idempotency-Key"]);
  await resolve(old, { quote_id: 101, payment_id: 1 });
  assertB();
  const button = [...host.querySelectorAll(".pw-transactions button")].find((node) => node.textContent === "Recording…");
  assert.ok(button);
  assert.equal(button.disabled, true);
  assert.equal(current.settled, false);
  await resolve(current, { quote_id: 202, payment_id: 2 });
  assert.equal(formWith("Record test payment").querySelector('button[type="submit"]').disabled, false);
});

test("lost response retries keep the exact payload and key until acknowledged success", async () => {
  await setup();
  const first = await payment();
  await fail(first);
  const retry = await payment();
  assert.equal(retry.options.body, first.options.body);
  assert.equal(retry.options.headers["Idempotency-Key"], first.options.headers["Idempotency-Key"]);
  await resolve(retry, { payment_id: 1, quote_id: 101 });
  assert.ok(host.querySelector(".pw-transactions").textContent.includes("Simulated payment recorded."));
  const next = await payment();
  assert.notEqual(next.options.headers["Idempotency-Key"], first.options.headers["Idempotency-Key"]);
  await resolve(next, { payment_id: 2, quote_id: 101 });
});

test("changed payment payload uses a different intent key and preserves the unchanged retry key", async () => {
  await setup();
  const first = await payment("100.00"); await fail(first);
  const changed = await payment("101.00");
  assert.notEqual(changed.options.headers["Idempotency-Key"], first.options.headers["Idempotency-Key"]);
  await fail(changed);
  const original = await payment("100.00");
  assert.equal(original.options.body, first.options.body);
  assert.equal(original.options.headers["Idempotency-Key"], first.options.headers["Idempotency-Key"]);
  await resolve(original, { payment_id: 1, quote_id: 101 });
});

test("changed BU and quote use a different key without losing A's retry identity", async () => {
  await setup();
  const first = await payment(); await fail(first);
  await setup(2);
  const changed = await payment();
  assert.notEqual(changed.options.headers["Idempotency-Key"], first.options.headers["Idempotency-Key"]);
  assert.equal(changed.url.pathname, "/api/purchasing/quotes/202/payments/");
  await fail(changed);
  await setup(1);
  const retry = await payment();
  assert.equal(retry.options.headers["Idempotency-Key"], first.options.headers["Idempotency-Key"]);
  assert.equal(retry.options.body, first.options.body);
  await resolve(retry, { payment_id: 1, quote_id: 101 });
});

test("another quote in the same BU is a distinct operation from a failed payment retry", async () => {
  await setup();
  const first = await payment(); await fail(first);
  await loadQuote(1, 303);
  const changed = await payment();
  assert.equal(changed.url.pathname, "/api/purchasing/quotes/303/payments/");
  assert.notEqual(changed.options.headers["Idempotency-Key"], first.options.headers["Idempotency-Key"]);
  await fail(changed);
  await loadQuote(1, 101);
  const retry = await payment();
  assert.equal(retry.options.headers["Idempotency-Key"], first.options.headers["Idempotency-Key"]);
  await resolve(retry, { quote_id: 101, payment_id: 1 });
});

test("unacknowledged A success retains its retry key after A to B to A", async () => {
  await setup();
  const first = await payment();
  await setup(2); await setup(1);
  const getCount = calls.length;
  await resolve(first, { payment_id: 1, quote_id: 101 });
  assert.equal(calls.length, getCount, "Obsolete A generation cannot refresh a new A generation");
  assert.equal(host.querySelector(".pw-transactions").textContent.includes("Simulated payment recorded."), false);
  const retry = await payment();
  assert.equal(retry.options.headers["Idempotency-Key"], first.options.headers["Idempotency-Key"]);
  assert.equal(retry.options.body, first.options.body);
  await resolve(retry, { payment_id: 1, quote_id: 101 });
  assert.ok(host.querySelector(".pw-transactions").textContent.includes("Simulated payment recorded."));
});

for (const kind of ["receipt", "quote"]) {
  test(`late A ${kind} creation cannot refresh or reopen A under B`, async () => {
    await setup();
    const form = formWith(kind === "receipt" ? "Record synthetic receipt" : "Create synthetic quote");
    if (kind === "receipt") {
      await setValue(host.querySelector("#pw-receipt-supplier"), 1);
      await setValue(control(form, "Catalog variant ID"), 10);
      await setValue(control(form, "Receiving location"), 11);
      await setValue(control(form, "Supplier unit cost"), "1000.00");
    } else {
      await setValue(host.querySelector("#pw-quote-customer"), 2);
      await setValue(host.querySelector("#pw-quote-variant"), 10);
      await setValue(host.querySelector("#pw-quote-location"), 11);
      await setValue(control(form, "Discount decision reason"), "TEST ONLY existing mock approval");
    }
    await submit(form);
    const post = posts.at(-1);
    assert.equal(post.body.business_unit_id, 1);
    await setup(2);
    const count = calls.length;
    await resolve(post, { quote_id: 101, receipt_id: 1 }, 201);
    assertB();
    assert.equal(calls.length, count, "Stale creation must not invoke register/quote refresh callbacks");
    assert.equal(host.querySelector(".pw-transactions").textContent.includes("Synthetic goods receipt recorded."), false);
    assert.equal(host.querySelector(".pw-transactions").textContent.includes("Synthetic quote #101 created."), false);
  });
}

const quoteReads = (id) => calls.filter(({ url, options }) =>
  options.method === "GET" && url.pathname === `/api/purchasing/quotes/${id}/`);
function assertSelectedQuote(id) {
  assert.equal(host.querySelector("#pw-quote-id").value, String(id));
  assert.ok(host.querySelector(".pw-quote")?.textContent.includes(`#${id}`));
  assert.ok(host.querySelector("#pw-quote-actions-title").parentElement.textContent.includes(`Quote #${id}`));
}
const transactionText = () => host.querySelector(".pw-transactions").textContent;

async function createQuote() {
  const form = formWith("Create synthetic quote");
  await setValue(host.querySelector("#pw-quote-customer"), 2);
  await setValue(host.querySelector("#pw-quote-variant"), 10);
  await setValue(host.querySelector("#pw-quote-location"), 11);
  await setValue(control(form, "Discount decision reason"), "TEST ONLY existing mock approval");
  await submit(form);
  const post = posts.at(-1);
  assert.equal(post.url.pathname, "/api/purchasing/quotes/");
  return post;
}

async function creationWithPendingLookup() {
  await renderUnit(1);
  assert.equal(host.querySelector("#pw-quote-id").value, "");
  const original = await createQuote();
  const transactions = host.querySelector(".pw-transactions");
  quoteHolds.add(303);
  await setValue(host.querySelector("#pw-quote-id"), 303);
  await act(async () => host.querySelector(".pw-quote-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  ));
  await flush();
  const lookup = pending.find((entry) => !entry.settled
    && entry.options.method === "GET" && entry.url.pathname === "/api/purchasing/quotes/303/");
  assert.ok(lookup, "Explicit lookup must stay pending until the test resolves it");
  assert.equal(original.settled, false);
  assertPending303();
  return { original, lookup, transactions };
}
function assertPending303() {
  assert.equal(host.querySelector("#pw-quote-id").value, "303");
  assert.ok(host.querySelector('[aria-label="Loading quote details"]'));
  assert.equal(quoteReads(505).length, 0, "Creation cannot start a lookup that supersedes 303");
  assert.equal(transactionText().includes("Synthetic quote #505 created."), false);
  assert.equal(transactionText().includes("Synthetic quote #303 created."), false);
}

test("quote creation success during pending lookup preserves explicit navigation and original replay identity", async () => {
  const { original, lookup, transactions } = await creationWithPendingLookup();
  await resolve(original, { quote_id: 505 }, 201);
  assert.equal(original.settled, true);
  assert.equal(lookup.settled, false);
  assertPending303();
  assert.equal(host.querySelector(".pw-transactions") === transactions, true);
  assert.equal(posts.length, 1, "No automatic transaction retry");
  await resolve(lookup, { ...quote(1), quote_id: 303 });
  assertSelectedQuote(303);
  assert.equal(quoteReads(505).length, 0);
  const retry = await createQuote();
  assert.equal(retry.options.body, original.options.body);
  assert.equal(retry.options.headers["Idempotency-Key"], original.options.headers["Idempotency-Key"]);
  await resolve(retry, { quote_id: 505 }, 200);
  assertSelectedQuote(505);
  assert.ok(transactionText().includes("Synthetic quote #505 created."));
});

for (const status of [403, 404]) {
  test(`quote creation ${status} denial during pending lookup preserves 303 and still rechecks current BU`, async () => {
    const { original, lookup } = await creationWithPendingLookup();
    const checks = accessCalls(1).length;
    await resolve(original, { detail: "TEST ONLY superseded creation denied" }, status);
    assert.equal(original.settled, true);
    assertPending303();
    assert.equal(transactionText().includes("TEST ONLY superseded creation denied"), false);
    assert.equal(accessCalls(1).length, checks + 1);
    assert.equal(host.querySelector(".pw-transactions .pw-access-chip").textContent, "Order access confirmed");
    await resolve(lookup, { ...quote(1), quote_id: 303 });
    assertSelectedQuote(303);
    assert.equal(posts.length, 1);
    const retry = await createQuote();
    assert.equal(retry.options.body, original.options.body);
    assert.equal(retry.options.headers["Idempotency-Key"], original.options.headers["Idempotency-Key"]);
    await resolve(retry, { quote_id: 505 }, 201);
    assertSelectedQuote(505);
  });
}

test("quote creation denial during pending lookup retains fresh BU denial and the explicit lookup outcome", async () => {
  const { original, lookup } = await creationWithPendingLookup();
  deniedUnits.add(1);
  const checks = accessCalls(1).length;
  await resolve(original, { detail: "TEST ONLY superseded creation denied" }, 403);
  assertPending303();
  assert.equal(transactionText().includes("TEST ONLY superseded creation denied"), false);
  assert.equal(accessCalls(1).length, checks + 1);
  assert.equal(host.querySelector(".pw-transactions .pw-access-chip").textContent, "Actions gated");
  assert.equal(formWith("Create synthetic quote").querySelector('button[type="submit"]').disabled, true);
  await resolve(lookup, { detail: "TEST ONLY requested quote 303 denied" }, 403);
  assert.equal(host.querySelector("#pw-quote-id").value, "303");
  assert.ok(host.textContent.includes("TEST ONLY requested quote 303 denied"));
  assert.equal(quoteReads(505).length, 0);
  assert.equal(posts.length, 1);
  assert.equal(original.settled, true);
});

test("automatic quote refresh does not supersede creation when no explicit navigation occurred", async () => {
  await setup();
  const original = await createQuote();
  const reads = quoteReads(101).length;
  const refreshAction = await payment();
  await resolve(refreshAction, { quote_id: 101, payment_id: 1 });
  assertSelectedQuote(101);
  assert.equal(quoteReads(101).length, reads + 1);
  assert.equal(original.settled, false);
  await resolve(original, { quote_id: 505 }, 201);
  assertSelectedQuote(505);
  assert.ok(transactionText().includes("Synthetic quote #505 created."));
  assert.equal(quoteReads(505).length, 1);
});

test("late quote creation success leaves newer selected quote untouched and preserves its original retry identity", async () => {
  await setup();
  const original = await createQuote();
  const transactions = host.querySelector(".pw-transactions");
  await loadQuote(1, 303);
  const reads = quoteReads(505).length;
  assert.equal(original.settled, false);
  await resolve(original, { quote_id: 505 }, 201);
  assert.equal(original.settled, true, "Original POST finishes, not aborted or discarded");
  assertSelectedQuote(303);
  assert.equal(host.querySelector(".pw-transactions") === transactions, true);
  assert.equal(quoteReads(505).length, reads, "Unacknowledged creation must not reopen its quote");
  assert.equal(transactionText().includes("Synthetic quote #505 created."), false);
  assert.equal(transactionText().includes("Synthetic quote #303 created."), false);
  assert.equal(posts.length, 1, "Selection changes and completion must not auto-submit");
  assert.equal(formWith("Create synthetic quote").querySelector('button[type="submit"]').disabled, false);
  const retry = await createQuote();
  assert.equal(retry.options.body, original.options.body);
  assert.equal(retry.options.headers["Idempotency-Key"], original.options.headers["Idempotency-Key"]);
  await resolve(retry, { quote_id: 505 }, 200);
  assertSelectedQuote(505);
  assert.ok(transactionText().includes("Synthetic quote #505 created."));
  const next = await createQuote();
  assert.equal(next.options.body, original.options.body);
  assert.notEqual(next.options.headers["Idempotency-Key"], original.options.headers["Idempotency-Key"],
    "Explicitly acknowledged replay retires the original key");
  await resolve(next, { quote_id: 606 }, 201);
  assertSelectedQuote(606);
});

for (const status of [403, 404]) {
  test(`late quote creation ${status} denial does not blame newer selection and preserves retry and current-BU recheck`, async () => {
    await setup();
    const original = await createQuote();
    await loadQuote(1, 303);
    const checks = accessCalls(1).length;
    await resolve(original, { detail: "TEST ONLY earlier creation denied" }, status);
    assertSelectedQuote(303);
    assert.equal(transactionText().includes("TEST ONLY earlier creation denied"), false);
    assert.equal(accessCalls(1).length, checks + 1);
    assert.equal(host.querySelector(".pw-transactions .pw-access-chip").textContent, "Order access confirmed");
    assert.equal(posts.length, 1);
    const retry = await createQuote();
    assert.equal(retry.options.body, original.options.body);
    assert.equal(retry.options.headers["Idempotency-Key"], original.options.headers["Idempotency-Key"]);
    await resolve(retry, { quote_id: 505 }, 201);
    assertSelectedQuote(505);
  });
}

test("late creation denial still gates the current selected quote when the current-BU recheck denies", async () => {
  await setup();
  const original = await createQuote();
  await loadQuote(1, 303);
  deniedUnits.add(1);
  const checks = accessCalls(1).length;
  await resolve(original, { detail: "TEST ONLY earlier creation denied" }, 403);
  assertSelectedQuote(303);
  assert.equal(transactionText().includes("TEST ONLY earlier creation denied"), false);
  assert.equal(accessCalls(1).length, checks + 1);
  assert.equal(host.querySelector(".pw-transactions .pw-access-chip").textContent, "Actions gated");
  assert.equal(formWith("Create synthetic quote").querySelector('button[type="submit"]').disabled, true);
  assert.equal(posts.length, 1);
});

test("creation with unchanged selection keeps normal feedback navigation and key acknowledgement", async () => {
  await setup();
  const original = await createQuote();
  const reads = quoteReads(505).length;
  await resolve(original, { quote_id: 505 }, 201);
  assertSelectedQuote(505);
  assert.equal(quoteReads(505).length, reads + 1);
  assert.ok(transactionText().includes("Synthetic quote #505 created."));
  const next = await createQuote();
  assert.equal(next.options.body, original.options.body);
  assert.notEqual(next.options.headers["Idempotency-Key"], original.options.headers["Idempotency-Key"]);
  await resolve(next, { quote_id: 606 }, 201);
  assertSelectedQuote(606);
});

test("creation from an empty quote selection keeps normal success navigation", async () => {
  await renderUnit(1);
  assert.equal(host.querySelector("#pw-quote-id").value, "");
  const original = await createQuote();
  await resolve(original, { quote_id: 505 }, 201);
  assertSelectedQuote(505);
  assert.ok(transactionText().includes("Synthetic quote #505 created."));
  assert.equal(quoteReads(505).length, 1);
  assert.equal(posts.length, 1);
});

test("creation denial with unchanged selection remains visible and safely retryable", async () => {
  await setup();
  const original = await createQuote();
  const checks = accessCalls(1).length;
  await resolve(original, { detail: "TEST ONLY current creation denied" }, 403);
  assertSelectedQuote(101);
  assert.ok(transactionText().includes("TEST ONLY current creation denied"));
  assert.equal(accessCalls(1).length, checks + 1);
  const retry = await createQuote();
  assert.equal(retry.options.body, original.options.body);
  assert.equal(retry.options.headers["Idempotency-Key"], original.options.headers["Idempotency-Key"]);
  await resolve(retry, { quote_id: 505 }, 201);
  assertSelectedQuote(505);
  assert.equal(transactionText().includes("TEST ONLY current creation denied"), false);
  assert.ok(transactionText().includes("Synthetic quote #505 created."));
});

test("same-BU late 101 payment success leaves 303 selected and retains the unseen outcome retry key", async () => {
  await setup();
  const old = await payment();
  const transactions = host.querySelector(".pw-transactions");
  await loadQuote(1, 303);
  const reads = quoteReads(101).length;
  assert.equal(old.settled, false);
  await resolve(old, { quote_id: 101, payment_id: 1 });
  assertSelectedQuote(303);
  assert.equal(host.querySelector(".pw-transactions") === transactions, true);
  assert.equal(quoteReads(101).length, reads, "Completion must not start a new old-quote lookup");
  assert.equal(transactionText().includes("Simulated payment recorded."), false);
  assert.equal(posts.length, 1, "Navigation must not submit or retry a transaction");
  await loadQuote(1, 101);
  const retry = await payment();
  assert.equal(retry.options.body, old.options.body);
  assert.equal(retry.options.headers["Idempotency-Key"], old.options.headers["Idempotency-Key"]);
  await resolve(retry, { quote_id: 101, payment_id: 1 });
  assert.ok(transactionText().includes("Simulated payment recorded."));
});

test("same-BU late 101 denial does not blame 303 and still rechecks current unit access", async () => {
  await setup();
  const old = await payment();
  await loadQuote(1, 303);
  const checks = accessCalls(1).length;
  const reads = quoteReads(101).length;
  await resolve(old, { detail: "TEST ONLY quote 101 payment denied" }, 403);
  assertSelectedQuote(303);
  assert.equal(transactionText().includes("TEST ONLY quote 101 payment denied"), false);
  assert.equal(accessCalls(1).length, checks + 1);
  assert.equal(quoteReads(101).length, reads);
  assert.equal(host.querySelector(".pw-transactions .pw-access-chip").textContent, "Order access confirmed");
  assert.equal(formWith("Record test payment").querySelector('button[type="submit"]').disabled, false);
});

test("same-BU late 101 denial can still gate 303 actions when the current unit recheck denies access", async () => {
  await setup();
  const old = await payment();
  await loadQuote(1, 303);
  const checks = accessCalls(1).length;
  deniedUnits.add(1);
  await resolve(old, { detail: "TEST ONLY quote 101 payment denied" }, 404);
  assertSelectedQuote(303);
  assert.equal(transactionText().includes("TEST ONLY quote 101 payment denied"), false);
  assert.equal(accessCalls(1).length, checks + 1);
  assert.equal(host.querySelector(".pw-transactions .pw-access-chip").textContent, "Actions gated");
  assert.equal(formWith("Record test payment").querySelector('button[type="submit"]').disabled, true);
  assert.equal(posts.length, 1);
});

test("payment for still-selected 101 shows success refreshes its facts and acknowledges its retry identity", async () => {
  await setup();
  const current = await payment();
  const reads = quoteReads(101).length;
  quoteSnapshots.set(101, { payments: [{
    payment_id: 1, kind: "quote", amount: "100.00", non_returnable_amount: "100.00",
    simulated_reference: "TEST ONLY refreshed payment 101", created_at: "2026-01-01T00:00:00Z",
  }] });
  await resolve(current, { quote_id: 101, payment_id: 1 });
  assertSelectedQuote(101);
  assert.equal(quoteReads(101).length, reads + 1);
  assert.ok(transactionText().includes("Simulated payment recorded."));
  assert.ok(host.querySelector(".pw-quote").textContent.includes("TEST ONLY refreshed payment 101"));
  const next = await payment();
  assert.equal(next.options.body, current.options.body);
  assert.notEqual(next.options.headers["Idempotency-Key"], current.options.headers["Idempotency-Key"]);
  await resolve(next, { quote_id: 101, payment_id: 2 });
});

test("303 can start its own payment and an old same-BU 101 completion cannot clear its busy indicator", async () => {
  await setup();
  const old = await payment();
  await loadQuote(1, 303);
  const current = await payment();
  assert.equal(old.settled, false, "Keep the original POST running");
  assert.equal(current.url.pathname, "/api/purchasing/quotes/303/payments/");
  assert.notEqual(current.options.headers["Idempotency-Key"], old.options.headers["Idempotency-Key"]);
  await resolve(old, { quote_id: 101, payment_id: 1 });
  assertSelectedQuote(303);
  assert.equal(transactionText().includes("Simulated payment recorded."), false);
  const button = [...host.querySelectorAll(".pw-transactions button")]
    .find((node) => node.textContent === "Recording…");
  assert.ok(button);
  assert.equal(button.disabled, true);
  assert.equal(current.settled, false);
  await resolve(current, { quote_id: 303, payment_id: 2 });
  assertSelectedQuote(303);
  assert.ok(transactionText().includes("Simulated payment recorded."));
});

test("delivered quote balance retry is idempotent and refreshes delivered status and handover timestamps", async () => {
  const readyAt = "2026-07-10T14:23:00Z";
  const deliveredAt = "2026-07-11T16:45:00Z";
  const handoverEvents = [
    { event_id: 1, kind: "readiness", message: "TEST ONLY readiness recorded",
      details: null, occurred_at: readyAt },
    { event_id: 2, kind: "delivery", message: "TEST ONLY delivery handed over",
      details: null, occurred_at: deliveredAt },
  ];
  const preCollection = {
    status: "delivered",
    total_quote_value: "4000.00",
    ready_at: readyAt,
    delivered_at: deliveredAt,
    approvals: [{
      approval_id: 1, kind: "post_handover", approver_id: 7, role: "bu_gm",
      reason: "TEST ONLY approved deferred balance",
      details: { deferred_balance_percent: "10.00" }, approved_at: "2026-07-09T10:00:00Z",
    }],
    payments: [{
      payment_id: 1, kind: "deposit", amount: "3600.00", non_returnable_amount: "3600.00",
      simulated_reference: "TEST ONLY initial synthetic payment", created_at: "2026-07-09T11:00:00Z",
    }],
    events: handoverEvents,
  };
  quoteSnapshots.set(101, preCollection);
  await setup();
  const initialQuoteReads = quoteReads(101).length;
  const handoverTimes = () => [...host.querySelectorAll(".pw-event-time")]
    .map((node) => node.textContent.trim());
  const expectedHandoverTimes = handoverEvents.map(
    (event) => new Date(event.occurred_at).toLocaleString(),
  );
  const assertDeliveredHandover = () => {
    assertSelectedQuote(101);
    assert.equal(host.querySelector(".pw-quote-banner .pw-status")?.textContent, "delivered");
    assert.deepEqual(handoverTimes(), expectedHandoverTimes);
    assert.ok(host.querySelector(".pw-quote").textContent.includes("TEST ONLY delivery handed over"));
  };
  assertDeliveredHandover();
  assert.equal(initialQuoteReads, 1);

  // The first request is committed by the synthetic server, but its response is lost.
  // A user retry must replay the same idempotent operation, not create a second payment.
  const first = await payment("400.00");
  assert.equal(first.url.pathname, "/api/purchasing/quotes/101/payments/");
  assert.deepEqual(first.body, { amount: "400.00", method: "simulated_cash", purpose: "quote" });
  const originalKey = first.options.headers["Idempotency-Key"];
  const postCollection = {
    ...preCollection,
    payments: [...preCollection.payments, {
      payment_id: 2, kind: "balance", amount: "400.00", non_returnable_amount: "0.00",
      simulated_reference: "TEST ONLY replayed balance collection",
      created_at: "2026-07-12T09:30:00Z",
    }],
    // A balance payment is not a second delivery or readiness workflow event.
    events: handoverEvents,
  };
  quoteSnapshots.set(101, postCollection);
  await fail(first);
  assert.equal(quoteReads(101).length, initialQuoteReads, "A lost response does not imply a refresh.");
  assertDeliveredHandover();
  assert.ok(host.querySelector(".pw-transactions").textContent.includes("TEST ONLY lost response"));
  assert.equal(posts.length, 1, "No automatic retry is issued.");

  const replay = await payment("400.00");
  assert.equal(posts.length, 2, "Only the explicit user retry adds a request.");
  assert.equal(replay.url.pathname, first.url.pathname);
  assert.deepEqual(replay.body, first.body);
  assert.equal(replay.options.headers["Idempotency-Key"], originalKey);
  assert.equal(quoteReads(101).length, initialQuoteReads);
  await resolve(replay, {
    quote_id: 101, payment_id: 2, amount: "400.00", total_paid: "4000.00",
    idempotency_replay: true,
  });

  assert.equal(quoteReads(101).length, initialQuoteReads + 1, "Acknowledged payment refreshes the selected quote.");
  assertDeliveredHandover();
  const refreshedQuote = host.querySelector(".pw-quote").textContent;
  assert.ok(refreshedQuote.includes("TEST ONLY initial synthetic payment"));
  assert.ok(refreshedQuote.includes("TEST ONLY replayed balance collection"));
  assert.equal((refreshedQuote.match(/TEST ONLY delivery handed over/g) ?? []).length, 1);
  assert.equal(posts.length, 2, "Refreshing the workbench does not submit another action.");
});

test("purchasing workbench creates a multi-line commitment and receives partial quantities from two lines", async () => {
  await setup(1);
  assert.ok(host.textContent.includes("No synthetic purchase commitments are available for this business unit."));
  await setValue(host.querySelector("#pw-receipt-supplier"), 1);

  const commitmentForm = formWith("Create synthetic purchase commitment");
  const commitmentDraftRows = [...commitmentForm.querySelectorAll(".pw-charge-row")];
  await setValue(control(commitmentDraftRows[0], "Line 1 · Catalog variant ID"), 10);
  await setValue(control(commitmentDraftRows[0], "Line 1 · Ordered quantity"), "10.00");
  await setValue(control(commitmentDraftRows[0], "Line 1 · Synthetic supplier unit value (QAR)"), "1000.00");
  const addCommitmentLine = [...commitmentForm.querySelectorAll("button")]
    .find((button) => button.textContent === "Add another commitment line");
  assert.ok(addCommitmentLine);
  await act(async () => addCommitmentLine.click());
  const twoCommitmentRows = [...commitmentForm.querySelectorAll(".pw-charge-row")];
  assert.equal(twoCommitmentRows.length, 2);
  await setValue(control(twoCommitmentRows[1], "Line 2 · Catalog variant ID"), 20);
  await setValue(control(twoCommitmentRows[1], "Line 2 · Ordered quantity"), "5.00");
  await setValue(control(twoCommitmentRows[1], "Line 2 · Synthetic supplier unit value (QAR)"), "200.00");
  await submit(commitmentForm);

  const commitmentPost = posts.at(-1);
  assert.equal(commitmentPost.url.pathname, "/api/purchasing/commitments/");
  assert.deepEqual(commitmentPost.body, {
    business_unit_id: 1,
    supplier_id: 1,
    lines: [
      { variant_id: 10, ordered_quantity: "10.00", unit_cost: "1000.00" },
      { variant_id: 20, ordered_quantity: "5.00", unit_cost: "200.00" },
    ],
  });
  const chairCommitmentRow = {
    commitment_id: 77, commitment_line_id: 501, supplier_id: 1, variant_id: 10,
    variant_sku: "TEST_ONLY_CHAIR", item_description: "TEST ONLY chair",
    ordered_quantity: "10.00", received_to_date: "0.00", remaining_quantity: "10.00",
  };
  const tableCommitmentRow = {
    commitment_id: 77, commitment_line_id: 502, supplier_id: 1, variant_id: 20,
    variant_sku: "TEST_ONLY_TABLE", item_description: "TEST ONLY table",
    ordered_quantity: "5.00", received_to_date: "0.00", remaining_quantity: "5.00",
  };
  commitmentRows = [chairCommitmentRow, tableCommitmentRow];
  await resolve(commitmentPost, {
    purchase_commitment_id: 77,
    business_unit_id: 1,
    supplier_id: 1,
    lines: [
      {
        commitment_line_id: 501, variant_id: 10, ordered_quantity: "10.00",
        received_to_date: "0.00", remaining_quantity: "10.00",
      },
      {
        commitment_line_id: 502, variant_id: 20, ordered_quantity: "5.00",
        received_to_date: "0.00", remaining_quantity: "5.00",
      },
    ],
    currency: "QAR",
    synthetic_only: true,
    test_only_label: "TEST ONLY",
  }, 201);
  await flush();
  const initialProgress = host.querySelector("#pw-commitment-progress-title").parentElement.textContent;
  assert.ok(initialProgress.includes("TEST_ONLY_CHAIR"));
  assert.ok(initialProgress.includes("TEST_ONLY_TABLE"));

  const receiptForm = formWith("Record synthetic receipt");
  const receiptLines = [...receiptForm.querySelectorAll(".pw-charge-row")];
  assert.equal(receiptLines.length, 2, "The newly created commitment lines are offered together for one receipt.");
  assert.equal(control(receiptLines[0], "Commitment line 1").value, "501");
  assert.equal(control(receiptLines[1], "Commitment line 2").value, "502");
  await setValue(control(receiptLines[0], "Receiving location"), 11);
  await setValue(control(receiptLines[0], "Quantity from line"), "4.00");
  await setValue(control(receiptLines[1], "Receiving location"), 11);
  await setValue(control(receiptLines[1], "Quantity from line"), "2.00");
  assert.equal(host.querySelector("#pw-receipt-supplier").disabled, true, "The linked commitment owns its supplier.");
  assert.ok(receiptForm.textContent.includes("Supplier unit value comes from the immutable synthetic commitment line."));
  await submit(receiptForm);

  const receiptPost = posts.at(-1);
  assert.equal(posts.length, 2, "Only commitment creation and the explicit receipt were submitted.");
  assert.equal(receiptPost.url.pathname, "/api/purchasing/receipts/");
  assert.deepEqual(receiptPost.body, {
    business_unit_id: 1,
    supplier_id: 1,
    purchase_commitment_id: 77,
    lines: [
      {
        commitment_line_id: 501,
        variant_id: 10,
        location_id: 11,
        quantity: "4.00",
      },
      {
        commitment_line_id: 502,
        variant_id: 20,
        location_id: 11,
        quantity: "2.00",
      },
    ],
    charges: [],
  });
  assert.equal("unit_cost" in receiptPost.body.lines[0], false, "Committed unit value is not re-exposed in the receipt payload.");
  commitmentRows = [
    { ...chairCommitmentRow, received_to_date: "4.00", remaining_quantity: "6.00" },
    { ...tableCommitmentRow, received_to_date: "2.00", remaining_quantity: "3.00" },
  ];
  await resolve(receiptPost, {
    purchase_receipt_id: 901, purchase_commitment_id: 77, purchase_value: "4400.00",
    eligible_landed_charges: "0.00", landed_cost_rate_percent: "0.0000",
    landed_cost_allocations: ["0.00", "0.00"], inventory_value: "4400.00", currency: "QAR",
  }, 201);
  await flush();
  const progress = host.querySelector("#pw-commitment-progress-title").parentElement.textContent;
  assert.ok(progress.includes("TEST_ONLY_CHAIR"));
  assert.ok(progress.includes("4.00"));
  assert.ok(progress.includes("6.00"));
  assert.ok(progress.includes("TEST_ONLY_TABLE"));
  assert.ok(progress.includes("2.00"));
  assert.ok(progress.includes("3.00"));
  assert.equal(progress.includes("1000.00"), false, "Progress does not disclose supplier unit values.");
  assert.equal(progress.includes("200.00"), false, "Progress does not disclose any commitment line unit value.");
  assert.ok(host.querySelector(".pw-transactions").textContent.includes("Purchase commitment receipt progress"));
});
