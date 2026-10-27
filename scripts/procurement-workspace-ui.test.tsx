import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, Profiler } from "react";
import { createRoot } from "react-dom/client";
import App from "../src/App";
import PurchasingWorkbench from "../src/PurchasingWorkbench";
import { sidebarSections } from "../src/sidebar-destinations";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let host: HTMLDivElement;
let calls: { url: URL; options: RequestInit }[];
let posts: { url: URL; options: RequestInit; payload: Record<string, unknown> }[];
let rowsByUnit: Map<number, Record<string, unknown>[]>;
let commitConflictCount: number;
let createdCounterparties: Record<string, unknown>[];
let denyCustomerCreation: boolean;
let denyOrderAccess: boolean;
let denyReceiptRegister: boolean;
let foreignReceiptDetail: boolean;
let delayCommitmentPost: boolean;
let delayedPost: { resolve: (response: Response) => void } | null;
let appSelectedUnit: number;

const username = "TEST_ONLY_procurement_operator";
const location = { id: 11, code: "WH-A", name: "TEST ONLY receiving bay", parent_id: null };
const supplier = { counterparty_id: 44, party_type: "supplier", code: "SUP-44", name: "TEST ONLY Atlas Furnishings" };
const customer = { counterparty_id: 45, party_type: "customer", code: "CUS-45", name: "TEST ONLY customer" };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const page = (rows: unknown[] = []) => ({
  rows,
  pagination: { page: 1, page_size: 20, total_count: rows.length, total_pages: 1, previous_page: null, next_page: null },
});

function rows(unitId: number) {
  return rowsByUnit.get(unitId) ?? [];
}

function updateReceived(unitId: number, payload: Record<string, unknown>) {
  const lines = payload.lines as Record<string, unknown>[];
  const current = rows(unitId);
  rowsByUnit.set(unitId, current.map((row) => {
    const receiptLine = lines.find((line) => Number(line.commitment_line_id) === Number(row.commitment_line_id));
    if (!receiptLine) return row;
    const received = Number(row.received_to_date) + Number(receiptLine.quantity);
    const ordered = Number(row.ordered_quantity);
    return { ...row, received_to_date: received.toFixed(2), remaining_quantity: (ordered - received).toFixed(2) };
  }));
}

async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
async function render(node: React.ReactNode) {
  await act(async () => root.render(node));
  await flush();
}
async function setValue(element: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement, value: string) {
  assert.ok(element, "Expected a rendered form control");
  const prototype = element.tagName === "SELECT" ? window.HTMLSelectElement.prototype
    : element.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(element, value);
  await act(async () => element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true })));
  await flush();
}
async function click(element: HTMLElement | null) {
  assert.ok(element, "Expected a rendered interactive element");
  await act(async () => element.click());
  await flush();
}
async function submit(form: HTMLFormElement | null) {
  assert.ok(form, "Expected a rendered form");
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();
}
const inputByLabel = (label: string) => host.querySelector<HTMLElement>(`[aria-label="${label}"]`);
async function uploadCsv(csv: string) {
  const input = inputByLabel("Choose supplier packing list") as HTMLInputElement | null;
  assert.ok(input);
  const bytes = new TextEncoder().encode(csv);
  const file = { name: "TEST-ONLY-import.csv", size: bytes.length, arrayBuffer: async () => bytes.buffer };
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  await flush();
  for (const [label, column] of [
    ["Supplier / item code", 0], ["Item description", 1], ["Quantity", 2],
    ["Quantity unit", 3], ["Supplier line reference", 4],
  ]) await setValue(inputByLabel(`Map ${label}`) as HTMLSelectElement, String(column));
}

function workbench(unitId = 1, staff = true, onReceiptSuccess = () => {}, view?: string) {
  return <PurchasingWorkbench
    mode="procurement"
    focusedView={view}
    selectedUnitId={unitId}
    selectedUnitName={`TEST ONLY Unit ${unitId}`}
    isStaff={staff}
    username={username}
    csrfToken="TEST_ONLY_CSRF"
    locations={[{ ...location, id: unitId * 11, code: `WH-${unitId}` }]}
    onOpenReports={() => {}}
    onReceiptSuccess={onReceiptSuccess}
  />;
}

function commitmentForm() {
  return host.querySelector<HTMLFormElement>("#pw-commitment-create-title")?.closest(".pw-action-form")?.querySelector("form") ?? null;
}
function receiptForm() {
  return host.querySelector<HTMLSelectElement>("#pw-receipt-supplier")?.closest("form") ?? null;
}
function accessCalls(unit?: number) {
  return calls.filter(({ url }) => url.pathname === "/api/purchasing/quotes/"
    && url.searchParams.get("page_size") === "1"
    && (unit === undefined || url.searchParams.get("business_unit_id") === String(unit)));
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  calls = [];
  posts = [];
  rowsByUnit = new Map();
  commitConflictCount = 0;
  createdCounterparties = [];
  denyCustomerCreation = false;
  denyOrderAccess = false;
  denyReceiptRegister = false;
  foreignReceiptDetail = false;
  delayCommitmentPost = false;
  delayedPost = null;
  appSelectedUnit = 1;
  globalThis.fetch = (path, options = {}) => {
    const url = new URL(String(path), "https://procurement.test.invalid");
    calls.push({ url, options });
    if (options.method === "POST") {
      const payload = JSON.parse(String(options.body ?? "{}")) as Record<string, unknown>;
      posts.push({ url, options, payload });
      if (url.pathname === "/api/purchasing/commitments/" && delayCommitmentPost) {
        return new Promise<Response>((resolve) => { delayedPost = { resolve }; });
      }
      if (url.pathname === "/api/purchasing/commitments/" && commitConflictCount > 0) {
        commitConflictCount -= 1;
        return Promise.resolve(json({ detail: "TEST ONLY same-key payload conflict" }, 409));
      }
      if (url.pathname === "/api/purchasing/commitments/") {
        const inputLines = payload.lines as { variant_id: number; ordered_quantity: string }[];
        const unit = Number(payload.business_unit_id);
        const commitmentId = 700 + unit;
        const created = inputLines.map((line, index) => ({
          commitment_id: commitmentId,
          commitment_line_id: commitmentId * 10 + index + 1,
          supplier_id: Number(payload.supplier_id),
          variant_id: line.variant_id,
          variant_sku: `TEST-SKU-${line.variant_id}`,
          item_description: `TEST ONLY chair ${line.variant_id}`,
          ordered_quantity: line.ordered_quantity,
          received_to_date: "0.00",
          remaining_quantity: line.ordered_quantity,
        }));
        rowsByUnit.set(unit, created);
        return Promise.resolve(json({ commitment_id: commitmentId, lines: created }, 201));
      }
      if (url.pathname === "/api/purchasing/receipts/") {
        updateReceived(Number(payload.business_unit_id), payload);
        return Promise.resolve(json({ purchase_receipt_id: 900 + posts.length }, 201));
      }
      if (url.pathname === "/api/governance/purchasing/counterparties/") {
        if (denyCustomerCreation) return Promise.resolve(json({ detail: "TEST ONLY staff required" }, 403));
        const created = { counterparty_id: 100 + createdCounterparties.length, party_type: payload.party_type, code: payload.code, name: payload.name };
        createdCounterparties.push(created);
        return Promise.resolve(json(created, 201));
      }
      throw new Error(`Unexpected procurement write ${url.pathname}`);
    }
    assert.equal(options.method ?? "GET", "GET", `Unexpected method for ${url.pathname}`);
    if (url.pathname === "/api/purchasing/quotes/") {
      if (url.searchParams.get("page_size") === "1") {
        return Promise.resolve(denyOrderAccess ? json({ detail: "TEST ONLY order access denied" }, 403) : json(page()));
      }
      return Promise.resolve(json(page()));
    }
    if (url.pathname === "/api/purchasing/commitments/") {
      return Promise.resolve(json(page(rows(Number(url.searchParams.get("business_unit_id"))))));
    }
    if (url.pathname === "/api/governance/purchasing/counterparties/") {
      const filter = url.searchParams.get("party_type");
      const items = filter === "supplier" ? [supplier] : filter === "customer" ? [customer, ...createdCounterparties] : [supplier, customer, ...createdCounterparties];
      return Promise.resolve(json(page(items)));
    }
    if (url.pathname === "/api/reports/purchase_history/receipts/") {
      if (denyReceiptRegister) return Promise.resolve(json({ detail: "TEST ONLY receipt history denied" }, 403));
      return Promise.resolve(json(page([{
        receipt_id: 42, created_at: "2031-05-07T09:00:00Z",
        business_unit: { id: 1, code: "A", name: "TEST ONLY Unit 1" },
        supplier: { code: "SUP-44", name: supplier.name }, currency: "QAR", fields: ["supplier"],
        lines: [], purchase_value: "PRIVATE_LIST_VALUE",
      }])));
    }
    if (/^\/api\/reports\/purchase_history\/receipts\/\d+\/$/.test(url.pathname) && foreignReceiptDetail) {
      return Promise.resolve(json({ receipt: {
        receipt_id: 42, created_at: "2031-05-07T09:00:00Z",
        business_unit: { id: 1, code: "A", name: "TEST ONLY Unit 1" },
        supplier: { code: "SUP-44", name: supplier.name }, currency: "EUR",
        exchange_rate: "4.21500000", rate_source: "TEST ONLY bank rate", rate_date: "2031-05-07",
        foreign_purchase_value: "4900.00", purchase_value: "20653.50", eligible_landed_charges: "2633.20",
        charges: [
          { category: "shipping", amount: "1383.20", eligible: true, currency: "USD", foreign_amount: "380.00", exchange_rate: "3.64000000" },
          { category: "clearance", amount: "1250.00", eligible: true },
        ],
        fields: ["supplier", "exchange_rate", "rate_source", "rate_date", "foreign_purchase_value", "purchase_value",
          "eligible_landed_charges", "charges.category", "charges.amount", "charges.eligible", "charges.currency", "charges.foreign_amount"],
        lines: [],
      } }));
    }
    if (/^\/api\/reports\/purchase_history\/receipts\/\d+\/$/.test(url.pathname)) {
      return Promise.resolve(json({ receipt: {
        receipt_id: 42,
        created_at: "2031-05-07T09:00:00Z",
        business_unit: { id: 1, code: "A", name: "TEST ONLY Unit 1" },
        supplier: { code: "SUP-44", name: supplier.name },
        currency: "QAR",
        fields: ["supplier"],
        purchase_value: "PRIVATE_DETAIL_VALUE",
        eligible_landed_charges: "PRIVATE_CHARGE_VALUE",
        inventory_value: "PRIVATE_INVENTORY_VALUE",
        charges: [{ category: "shipping", amount: "PRIVATE_CHARGE_LINE", eligible: true }],
        lines: [{
          item_id: "SKU-44", description: "TEST ONLY chair", brand_name: "TEST ONLY brand",
          warehouse_location: "WH-A", quantity: "1.00", supplier_unit_cost: "PRIVATE_UNIT_COST",
          purchase_value: "PRIVATE_LINE_VALUE", landed_cost_allocation: "PRIVATE_ALLOCATION",
        }],
      } }));
    }
    if (url.pathname === "/api/stock-catalog-matches/") return Promise.resolve(json({ matched_codes: ["SUP-2101"] }));
    if (url.pathname === "/api/stock-receipt-candidates/") {
      return Promise.resolve(json({ candidates: [{
        variant_id: 2101, item_code: "SUP-2101", variant_sku: "TEST-SKU-2101",
        variant_description: "TEST ONLY oak chair", item_description: "TEST ONLY oak chair", brand_name: "TEST ONLY Atelier",
      }] }));
    }
    if (url.pathname === "/api/auth/csrf/") return Promise.resolve(json({ csrfToken: "TEST_ONLY_CSRF" }));
    if (url.pathname === "/api/auth/me/") return Promise.resolve(json({
      authenticated: true, username, is_staff: true, is_superuser: false, can_manage_report_panels: false,
    }));
    if (url.pathname === "/api/organization/") return Promise.resolve(json({
      company: { name: "TEST ONLY Furniture Group", code: "TST" },
      units: [
        { id: 1, code: "A", name: "TEST ONLY Unit 1", theme_palette: "slate" },
        { id: 2, code: "B", name: "TEST ONLY Unit 2", theme_palette: "roche" },
      ],
      selected_unit: { id: appSelectedUnit, code: appSelectedUnit === 1 ? "A" : "B", name: `TEST ONLY Unit ${appSelectedUnit}`, theme_palette: "slate" },
      locations: [{ ...location, id: appSelectedUnit * 11 }],
    }));
    if (url.pathname === "/api/favorites/" || url.pathname === "/api/favorites/") {
      return Promise.resolve(json({
        available_destinations: [{ destination: "procurement", label: "Procurement & Imports" }],
        favorites: [{ destination: "procurement", label: "Procurement & Imports" }],
      }));
    }
    if (url.pathname === "/api/stock-balances/") {
      return Promise.resolve(json({
        results: [], pagination: { page: 1, page_size: 50, total_count: 0, total_pages: 1, previous_page: null, next_page: null },
      }));
    }
    throw new Error(`Unexpected procurement read ${url.pathname}`);
  };
});

afterEach(async () => {
  try { await act(async () => root.unmount()); }
  finally {
    host.remove();
    globalThis.fetch = nativeFetch;
  }
});

test("Procurement presents only existing receipt workflows, with traceable targets and no sales or policy controls", async () => {
  await render(workbench());
  assert.match(host.querySelector("#purchasing-workbench-title")?.textContent ?? "", /Procurement & Imports/);
  assert.ok(host.querySelector("#pw-commitment-create-title"));
  assert.ok(host.querySelector("#pw-commitment-progress-title"));
  assert.ok(host.querySelector("#pw-receipt-title"));
  assert.ok(host.querySelector("#packing-preview-title"));
  assert.ok(host.querySelector("#pr-tabs"));
  assert.equal(host.querySelector("#pw-quote-id"), null);
  assert.equal(host.querySelector("#pw-create-quote-title"), null);
  assert.equal(host.querySelector("#pw-quote-actions-title"), null);
  assert.equal(host.querySelector("#pw-sales-return-summary"), null);
  assert.deepEqual([...host.querySelectorAll("#pr-tabs > button")].map((button) => button.getAttribute("data-register-tab")), ["receipts", "counterparties"]);
  assert.equal(host.querySelector(".pw-procurement-nav"), null, "The focused workspace replaces scroll-only quicklinks.");
  const destinations = sidebarSections({ isStaff: true, isSuperuser: false, canManageReportPanels: false }).find((section) => section.id === "procurement");
  assert.deepEqual(destinations?.children.map((child) => child.id), ["main", "commitments", "progress", "receipt", "packing", "registers", "kyc", "suppliers"]);
  assert.deepEqual(destinations?.children.map((child) => child.id), ["main", "commitments", "progress", "receipt", "packing", "registers", "kyc", "suppliers"]);
  assert.equal(posts.length, 0, "Rendering and navigation targets never post transactions");
  assert.equal(calls.some(({ url }) => url.pathname === "/api/purchasing/quotes/" && url.searchParams.get("page_size") !== "1"), false);
});

test("two-line commitment receives successive partial receipts and server-returned remaining quantities", async () => {
  await render(workbench());
  await setValue(host.querySelector("#pw-receipt-supplier")!, "44");
  const createForm = commitmentForm();
  assert.ok(createForm);
  await setValue(createForm.querySelectorAll("input")[0], "2101");
  await setValue(createForm.querySelectorAll("input")[1], "5");
  await setValue(createForm.querySelectorAll("input")[2], "125.50");
  await click([...createForm.querySelectorAll("button")].find((button) => button.textContent?.includes("Add another commitment line")) ?? null);
  await setValue(createForm.querySelectorAll("input")[3], "2102");
  await setValue(createForm.querySelectorAll("input")[4], "3");
  await setValue(createForm.querySelectorAll("input")[5], "88.25");
  await submit(createForm);
  assert.equal(posts.filter((post) => post.url.pathname === "/api/purchasing/commitments/").length, 1);
  assert.deepEqual(posts[0].payload.lines, [
    { variant_id: 2101, ordered_quantity: "5", unit_cost: "125.50" },
    { variant_id: 2102, ordered_quantity: "3", unit_cost: "88.25" },
  ]);
  assert.match(host.textContent ?? "", /Synthetic QAR purchase commitment created/);
  assert.match(host.querySelector("#pw-commitment-progress-title")?.parentElement?.textContent ?? "", /5\.00|5/);
  assert.doesNotMatch(host.querySelector("#pw-commitment-progress-title")?.parentElement?.textContent ?? "", /125\.50|88\.25/);

  const receipt = receiptForm();
  assert.ok(receipt);
  let receiptSelects = receipt.querySelectorAll("select");
  // Supplier, currency, then a commitment-line and location picker per line.
  assert.equal(receiptSelects.length, 6);
  await setValue(receiptSelects[3], "11");
  await setValue(receiptSelects[5], "11");
  let receiptQuantities = receipt.querySelectorAll<HTMLInputElement>('input[type="number"]');
  await setValue(receiptQuantities[0], "2");
  await setValue(receiptQuantities[1], "1");
  await submit(receipt);
  assert.equal(posts.filter((post) => post.url.pathname === "/api/purchasing/receipts/").length, 1);
  assert.deepEqual((posts.find((post) => post.url.pathname === "/api/purchasing/receipts/")?.payload.lines as Record<string, unknown>[]).map((line) => [
    line.commitment_line_id, line.quantity, line.location_id,
  ]), [[7011, "2", 11], [7012, "1", 11]]);
  assert.match(host.textContent ?? "", /3\.00/);
  assert.match(host.textContent ?? "", /2\.00/);

  await click([...receipt.querySelectorAll("button")].find((button) => button.textContent?.includes("Add commitment lines to receipt")) ?? null);
  receiptSelects = receipt.querySelectorAll("select");
  await setValue(receiptSelects[2], "7011");
  await setValue(receiptSelects[3], "11");
  receiptQuantities = receipt.querySelectorAll<HTMLInputElement>('input[type="number"]');
  await setValue(receiptQuantities[0], "1.25");
  await submit(receipt);
  const receiptPosts = posts.filter((post) => post.url.pathname === "/api/purchasing/receipts/");
  assert.equal(receiptPosts.length, 2);
  assert.deepEqual((receiptPosts[1].payload.lines as Record<string, unknown>[]).map((line) => [
    line.commitment_line_id, line.quantity,
  ]), [[7011, "1.25"]]);
  const progressRows = [...host.querySelectorAll("#pw-commitment-progress-title + p, #pw-commitment-progress-title ~ .pw-table-wrap tbody tr")];
  assert.ok(progressRows.length);
  const tableText = host.querySelector("#pw-commitment-progress-title")?.parentElement?.parentElement?.textContent ?? "";
  assert.match(tableText, /3\.25/);
  assert.match(tableText, /1\.75/);
  assert.equal(host.textContent?.match(/Completed · Receipt creation/g)?.length, 1);
});

test("Procurement CSV mapping explicitly reviews a commitment line before a partial receipt POST", async () => {
  rowsByUnit.set(1, [{
    commitment_id: 80, commitment_line_id: 801, supplier_id: 44, variant_id: 2101,
    variant_sku: "TEST-SKU-2101", item_description: "TEST ONLY oak chair",
    ordered_quantity: "5.00", received_to_date: "1.00", remaining_quantity: "4.00",
  }]);
  await render(workbench());
  const disclosure = host.querySelector<HTMLDetailsElement>("#pw-receipt-title")?.closest("section")?.querySelector("details");
  assert.ok(disclosure);
  assert.equal(disclosure.open, false);
  await uploadCsv("Code,Description,Qty,Unit,Reference\nSUP-2101,TEST ONLY oak chair,2.50,EA,PO-LINE-801\n");
  await setValue(inputByLabel("Receiving mode") as HTMLSelectElement, "commitment");
  assert.equal((inputByLabel("Receiving mode") as HTMLSelectElement).value, "commitment");
  await setValue(inputByLabel("Existing authorized purchase commitment") as HTMLSelectElement, "80");
  await setValue(inputByLabel("Select commitment line for source row 2") as HTMLSelectElement, "801");
  await setValue(inputByLabel("Existing receiving location for source row 2") as HTMLSelectElement, "11");
  await setValue(inputByLabel("Warehouse-confirmed quantity for source row 2") as HTMLInputElement, "2.50");
  assert.match(host.textContent ?? "", /Source: 2\.50 EA/);
  assert.match(host.textContent ?? "", /Ordered 5\.00 · Received 1\.00 · Remaining 4\.00/);
  const acknowledge = host.querySelector<HTMLInputElement>(".packing-preparation input[type='checkbox']");
  assert.ok(acknowledge);
  await click(acknowledge);
  const handoff = [...host.querySelectorAll("button")].find((button) => button.textContent === "Use reviewed lines in receipt draft");
  assert.ok(handoff && !handoff.disabled);
  assert.equal(posts.length, 0, "File reading, mapping and explicit review remain local/read-only");
  await click(handoff);
  assert.match(host.textContent ?? "", /Reviewed packing-list lines/);
  assert.match(host.textContent ?? "", /Commitment #80/);
  assert.match(host.textContent ?? "", /Source: 2\.50 EA/);
  assert.match(host.textContent ?? "", /Source quantities, units, references, and the separate/);
  assert.equal(posts.length, 0, "Handoff only creates an in-memory receipt draft; no implicit receipt POST");
  assert.equal(receiptForm()?.querySelector('button[type="submit"]')?.textContent?.trim(), "Record synthetic receipt");
  await submit(receiptForm());
  const receiptPost = posts.find((post) => post.url.pathname === "/api/purchasing/receipts/");
  assert.ok(receiptPost, "Only the explicit receipt form submission posts");
  assert.equal(receiptPost.payload.purchase_commitment_id, 80);
  assert.deepEqual(receiptPost.payload.lines, [{
    variant_id: 2101, location_id: 11, quantity: "2.50", commitment_line_id: 801,
  }]);
  const progressText = host.querySelector("#pw-commitment-progress-title")?.parentElement?.parentElement?.textContent ?? "";
  assert.match(progressText, /3\.50/);
  assert.match(progressText, /1\.50/);
});

test("conflict feedback is retained and an unchanged commitment retry reuses its idempotency key", async () => {
  commitConflictCount = 1;
  await render(workbench());
  await setValue(host.querySelector("#pw-receipt-supplier")!, "44");
  const form = commitmentForm();
  assert.ok(form);
  await setValue(form.querySelectorAll("input")[0], "301");
  await setValue(form.querySelectorAll("input")[1], "2");
  await setValue(form.querySelectorAll("input")[2], "40.50");
  await submit(form);
  assert.match(host.querySelector('[role="alert"]')?.textContent ?? "", /TEST ONLY same-key payload conflict/);
  assert.equal(posts.length, 1);
  const firstKey = new Headers(posts[0].options.headers).get("Idempotency-Key");
  await submit(form);
  assert.equal(posts.length, 2);
  assert.equal(new Headers(posts[1].options.headers).get("Idempotency-Key"), firstKey);
  assert.match(host.textContent ?? "", /Synthetic QAR purchase commitment created/);
  assert.doesNotMatch(host.textContent ?? "", /TEST ONLY same-key payload conflict/);
});

test("commitment draft and pending retry identity survive focused-view navigation without remount", async () => {
  await render(workbench(1, true, () => {}, "commitments"));
  await setValue(host.querySelector("#pw-receipt-supplier")!, "44");
  const form = commitmentForm();
  assert.ok(form);
  await setValue(form.querySelectorAll("input")[0], "781");
  await setValue(form.querySelectorAll("input")[1], "6.25");
  await setValue(form.querySelectorAll("input")[2], "91.40");
  delayCommitmentPost = true;
  await submit(form);
  assert.ok(delayedPost);
  const firstKey = new Headers(posts[0].options.headers).get("Idempotency-Key");
  assert.ok(firstKey);
  assert.equal(form.querySelector('button[type="submit"]')?.disabled, true);

  await render(workbench(1, true, () => {}, "progress"));
  assert.strictEqual(commitmentForm(), form, "Function navigation must preserve the mounted transaction form.");
  assert.equal(form.querySelector('button[type="submit"]')?.disabled, true, "The in-flight action remains visibly unavailable even while its pane is hidden.");
  await act(async () => delayedPost?.resolve(json({ detail: "TEST ONLY retry after navigation" }, 409)));
  await flush();

  await render(workbench(1, true, () => {}, "commitments"));
  assert.strictEqual(commitmentForm(), form);
  assert.deepEqual([...form.querySelectorAll("input")].map((input) => input.value), ["781", "6.25", "91.40"]);
  assert.match(host.querySelector('[role="alert"]')?.textContent ?? "", /TEST ONLY retry after navigation/);
  delayCommitmentPost = false;
  await submit(form);
  assert.equal(posts.length, 2);
  assert.equal(new Headers(posts[1].options.headers).get("Idempotency-Key"), firstKey);
  assert.match(host.textContent ?? "", /Synthetic QAR purchase commitment created/);
});

test("denials expose no receipt rows, supplier cost detail is masked by the returned field allowlist", async () => {
  denyOrderAccess = true;
  denyReceiptRegister = true;
  await render(workbench(1, true));
  assert.match(host.textContent ?? "", /TEST ONLY order access denied/);
  assert.match(host.textContent ?? "", /did not authorize this receipts and landed charges view|TEST ONLY receipt history denied/);
  assert.equal(host.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled, true);
  assert.equal(posts.length, 0);

  await act(async () => root.unmount());
  root = createRoot(host);
  denyOrderAccess = false;
  denyReceiptRegister = false;
  await render(workbench());
  const record = host.querySelector<HTMLButtonElement>('[aria-label="Open Receipts and landed charges record 42"]');
  await click(record);
  const detail = host.querySelector(".pr-detail");
  assert.ok(detail);
  assert.match(detail.textContent ?? "", /TEST ONLY Atlas Furnishings/);
  assert.doesNotMatch(detail.textContent ?? "", /PRIVATE_LIST_VALUE|PRIVATE_DETAIL_VALUE|PRIVATE_CHARGE_VALUE|PRIVATE_INVENTORY_VALUE|PRIVATE_UNIT_COST|PRIVATE_LINE_VALUE|PRIVATE_ALLOCATION|PRIVATE_CHARGE_LINE/);
});

test("a foreign receipt shows its rate, supplier total and each charge's billed currency, with QAR values labelled", async () => {
  foreignReceiptDetail = true;
  await render(workbench());
  await click(host.querySelector<HTMLButtonElement>('[aria-label="Open Receipts and landed charges record 42"]'));
  const text = host.querySelector(".pr-detail")?.textContent ?? "";
  assert.match(text, /4\.21500000 QAR per EUR \(TEST ONLY bank rate, 2031-05-07\)/);
  assert.match(text, /EUR 4900\.00/);
  assert.match(text, /QAR 20653\.50/);
  assert.match(text, /shipping · QAR 1383\.20 \(USD 380\.00 at 3\.64000000\)/);
  assert.match(text, /clearance · QAR 1250\.00/);
});

test("supplier directory remains staff-only and Procurement makes only the existing supplier-scoped picker request", async () => {
  await render(workbench(1, false));
  assert.equal(host.querySelector('[data-register-tab="counterparties"]'), null);
  assert.equal(calls.some(({ url }) => url.pathname === "/api/governance/purchasing/counterparties/"), false);
  assert.equal(host.querySelector("#pw-receipt-supplier")?.disabled, true);

  await act(async () => root.unmount());
  root = createRoot(host);
  calls = [];
  await render(workbench(1, true));
  assert.ok(host.querySelector('[data-register-tab="counterparties"]'));
  const supplierRequest = calls.filter(({ url }) => url.pathname === "/api/governance/purchasing/counterparties/"
    && url.searchParams.get("party_type") === "supplier");
  assert.ok(supplierRequest.length > 0);
  assert.equal(calls.some(({ url }) => url.pathname === "/api/governance/purchasing/counterparties/"
    && url.searchParams.get("party_type") === "customer"), false);
  await click(host.querySelector('[data-register-tab="counterparties"]'));
  assert.match(host.textContent ?? "", /TEST ONLY Atlas Furnishings/);
  assert.match(host.textContent ?? "", /STAFF-ONLY DIRECTORY/);
  assert.ok(host.querySelector("#pr-customer-create-title"));

  await setValue(host.querySelector<HTMLInputElement>("#pr-new-customer-code")!, "test-cust-new");
  await setValue(host.querySelector<HTMLInputElement>("#pr-new-customer-name")!, "TEST ONLY New customer");
  await submit(host.querySelector<HTMLFormElement>("#pr-new-customer-code")?.closest("form") ?? null);
  const create = posts.find((post) => post.url.pathname === "/api/governance/purchasing/counterparties/");
  assert.ok(create);
  assert.deepEqual(create.payload, { party_type: "customer", code: "TEST-CUST-NEW", name: "TEST ONLY New customer" });
  assert.equal(new Headers(create.options.headers).get("X-CSRFToken"), "TEST_ONLY_CSRF");
  assert.match(host.textContent ?? "", /Synthetic customer TEST-CUST-NEW created/);
  assert.match(host.textContent ?? "", /TEST-CUST-NEW/);
});

test("first Unit B commit clears Unit A supplier context and late Unit A POST outcome cannot replace B", async () => {
  const commits: { unit: number; text: string }[] = [];
  const renderUnit = (unit: number) => render(
    <Profiler id={`unit-${unit}`} onRender={() => commits.push({ unit, text: host.textContent ?? "" })}>
      {workbench(unit, true)}
    </Profiler>,
  );
  await renderUnit(1);
  await setValue(host.querySelector("#pw-receipt-supplier")!, "44");
  const form = commitmentForm();
  await setValue(form!.querySelectorAll("input")[0], "401");
  await setValue(form!.querySelectorAll("input")[1], "4");
  await setValue(form!.querySelectorAll("input")[2], "75");
  delayCommitmentPost = true;
  await submit(form);
  assert.ok(delayedPost);
  const previousCommitCount = commits.length;
  await renderUnit(2);
  const firstB = commits.slice(previousCommitCount).find((commit) => commit.unit === 2);
  assert.ok(firstB);
  assert.match(firstB.text, /TEST ONLY Unit 2/);
  assert.doesNotMatch(firstB.text, /Commitment #701|TEST-SKU-401|Request not completed|Request completed/);
  assert.equal(host.querySelector("#pw-receipt-supplier")?.value, "44", "The existing supplier reference remains a reusable form draft, not A-scoped access data");
  assert.equal(accessCalls(2).length > 0, true);
  await act(async () => delayedPost?.resolve(json({ commitment_id: 701, lines: [] }, 201)));
  await flush();
  assert.doesNotMatch(host.textContent ?? "", /Synthetic QAR purchase commitment created|TEST-SKU-401|Commitment #701/);
  assert.match(host.textContent ?? "", /TEST ONLY Unit 2/);
  assert.equal(posts.length, 1);
});

test("existing Procurement Favorite opens the workspace and is not a separate subpage favorite", async () => {
  await render(<App />);
  const favoriteLink = host.querySelector<HTMLButtonElement>(".favorite-shortcut .nav-item");
  assert.ok(favoriteLink);
  await click(favoriteLink);
  assert.match(host.querySelector("#purchasing-workbench-title")?.textContent ?? "", /Procurement & Imports/);
  assert.equal(host.querySelector(".planned-panel"), null);
  assert.ok(host.querySelector("#pw-commitment-create-title"));
  assert.equal(host.querySelectorAll(".favorite-toggle[aria-label*='Commitment']").length, 0);
  assert.ok(calls.some(({ url }) => url.pathname === "/api/favorites/" && url.searchParams.has("unit")));

  const procurementToggle = [...host.querySelectorAll<HTMLButtonElement>(".nav-section-toggle")]
    .find((button) => button.title === "Procurement & Imports") ?? null;
  assert.ok(procurementToggle);
  if (procurementToggle.getAttribute("aria-expanded") !== "true") await click(procurementToggle);
  const packingChild = [...host.querySelectorAll<HTMLButtonElement>(".section-navigation-children button")]
    .find((button) => button.textContent?.includes("Packing-list mapping"));
  await click(packingChild ?? null);
  const packingDisclosure = host.querySelector<HTMLDetailsElement>("#pw-receipt-title")?.closest("section")?.querySelector("details");
  assert.ok(packingDisclosure?.open, "Sidebar packing target opens the enclosing receipt-preparation disclosure");
  assert.ok(host.querySelector("#packing-preview-title"));
  const formBeforeDirectoryNavigation = commitmentForm();
  assert.ok(formBeforeDirectoryNavigation);
  const supplierChild = [...host.querySelectorAll<HTMLButtonElement>(".section-navigation-children button")]
    .find((button) => button.textContent?.includes("Staff counterparty directory"));
  await click(supplierChild ?? null);
  assert.equal(host.querySelector('[data-register-tab="counterparties"]')?.getAttribute("aria-current"), "page");
  assert.match(host.textContent ?? "", /STAFF-ONLY DIRECTORY/);
  assert.strictEqual(commitmentForm(), formBeforeDirectoryNavigation, "Supplier-tab navigation does not remount the transaction form");
});
