import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, Profiler } from "react";
import { createRoot } from "react-dom/client";
import App from "../src/App";
import InventoryRegisters from "../src/InventoryRegisters";
import InventoryStockDetail from "../src/InventoryStockDetail";
import PurchasingWorkbench from "../src/PurchasingWorkbench";
import WarehouseCapacity from "../src/WarehouseCapacity";
import { sidebarSections } from "../src/sidebar-destinations";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let host: HTMLDivElement;
let calls: { url: URL; options: RequestInit }[];
let pendingStock: { url: URL; resolve: (response: Response) => void }[];
let delayUnitAStock: boolean;
let staffMode: boolean;
let inventoryQuantity: string;
let receiptPosts: { url: URL; options: RequestInit; payload: unknown }[];
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { "Content-Type": "application/json" },
});
const pager = (rows: unknown[] = [], page = 1, totalPages = 1) => ({
  rows,
  pagination: {
    page, page_size: 20, total_count: rows.length, total_pages: totalPages,
    previous_page: page > 1 ? page - 1 : null, next_page: page < totalPages ? page + 1 : null,
  },
});
const stockPage = (unit: number, location?: string, pageSize = 50) => ({
  results: [{
    id: unit * 100 + 1, item_code: `TEST-${unit}-ITEM`, item_description: `TEST ONLY item ${unit}`,
    brand_name: "TEST ONLY brand", brand_code: "TST", variant_sku: `SKU-${unit}`,
    variant_description: "TEST ONLY variant", location_code: `WH-${unit}`,
    location_name: `TEST ONLY warehouse ${unit}`, unit_code: `U${unit}`,
    unit_name: `TEST ONLY Unit ${unit}`, quantity: inventoryQuantity, is_synthetic: true,
  }],
  pagination: {
    page: 1, page_size: pageSize, total_count: 1, total_pages: 1,
    previous_page: null, next_page: null,
  },
  location,
});

async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
async function render(node: React.ReactNode) {
  await act(async () => root.render(node));
  await flush();
}
async function setValue(element: HTMLInputElement | HTMLSelectElement, value: string) {
  assert.ok(element);
  const isSelect = element.tagName === "SELECT";
  const prototype = isSelect ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(element, value);
  await act(async () => element.dispatchEvent(new Event(isSelect ? "change" : "input", { bubbles: true })));
  await flush();
}
async function replyStock(index: number, response: Response) {
  const request = pendingStock[index];
  assert.ok(request);
  await act(async () => request.resolve(response));
  await flush();
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  calls = [];
  pendingStock = [];
  delayUnitAStock = false;
  staffMode = false;
  inventoryQuantity = "4.1250";
  receiptPosts = [];
  globalThis.fetch = (path, options = {}) => {
    const url = new URL(String(path), "https://inventory.test.invalid");
    calls.push({ url, options });
    if (url.pathname === "/api/stock-balances/" && delayUnitAStock && url.searchParams.get("unit") === "1") {
      return new Promise<Response>((resolve) => pendingStock.push({ url, resolve }));
    }
    if (url.pathname === "/api/stock-balances/") return Promise.resolve(json(stockPage(
      Number(url.searchParams.get("unit")), url.searchParams.get("location") ?? undefined,
      Number(url.searchParams.get("page_size") ?? 50),
    )));
    if (url.pathname === "/api/auth/csrf/") return Promise.resolve(json({ csrfToken: "TEST-CSRF" }));
    if (url.pathname === "/api/auth/me/") return Promise.resolve(json({ authenticated: true, username: "TEST_inventory_operator", is_staff: staffMode }));
    if (url.pathname === "/api/organization/") {
      const selected = Number((globalThis as typeof globalThis & { selectedTestUnit?: number }).selectedTestUnit ?? 1);
      return Promise.resolve(json({
        company: { name: "TEST ONLY company", code: "TST" },
        units: [
          { id: 1, code: "A", name: "TEST ONLY Unit A", theme_palette: "slate" },
          { id: 2, code: "B", name: "TEST ONLY Unit B", theme_palette: "roche" },
        ],
        selected_unit: { id: selected, code: selected === 1 ? "A" : "B", name: `TEST ONLY Unit ${selected}`, theme_palette: selected === 1 ? "slate" : "roche" },
        locations: [{ id: selected * 11, code: `WH-${selected}`, name: `TEST ONLY permitted location ${selected}`, parent_id: null }],
      }));
    }
    if (url.pathname === "/api/favorites/") return Promise.resolve(json({ available_destinations: [], favorites: [] }));
    if (url.pathname === "/api/units/2/select/") {
      (globalThis as typeof globalThis & { selectedTestUnit?: number }).selectedTestUnit = 2;
      return Promise.resolve(json({ selected_unit: { id: 2 } }));
    }
    if (url.pathname === "/api/reports/") return Promise.resolve(json({
      panels: [],
      scopes: {
        inventory: { business_units: [], companies: [], locations: [] },
        transfer_history: { business_units: [], companies: [], locations: [] },
      },
    }));
    if (url.pathname === "/api/warehouse-capacity/") return Promise.resolve(json({ results: [] }));
    if (url.pathname === "/api/purchasing/quotes/") return Promise.resolve(json(pager()));
    if (url.pathname === "/api/purchasing/commitments/") return Promise.resolve(json(pager()));
    if (url.pathname === "/api/reports/purchase_history/receipts/") return Promise.resolve(json(pager()));
    if (url.pathname === "/api/governance/purchasing/counterparties/") {
      return Promise.resolve(json(pager(url.searchParams.get("party_type") === "supplier"
        ? [{ counterparty_id: 1, party_type: "supplier", code: "SUP-1", name: "TEST ONLY supplier" }]
        : [{ counterparty_id: 2, party_type: "customer", code: "CUS-2", name: "TEST ONLY customer" }])));
    }
    if (url.pathname === "/api/governance/purchasing/1/") return Promise.resolve(json({}));
    if (/^\/api\/stock-balances\/\d+\/$/.test(url.pathname)) return Promise.resolve(json({
      item_code: "TEST-1-ITEM", item_description: "TEST ONLY item 1", variant_sku: "SKU-1",
      brand_name: "TEST ONLY brand", location_code: "WH-1", location_name: "TEST ONLY warehouse 1",
      unit_code: "A", unit_name: "TEST ONLY Unit 1", quantity: inventoryQuantity,
    }));
    if (url.pathname === "/api/purchasing/receipts/" && options.method === "POST") {
      const payload = JSON.parse(String(options.body ?? "{}"));
      receiptPosts.push({ url, options, payload });
      inventoryQuantity = "5.3750";
      return Promise.resolve(json({ purchase_receipt_id: 501 }, 201));
    }
    if (url.pathname === "/api/stock-catalog-matches/") return Promise.resolve(json({ matched_codes: [] }));
    throw new Error(`Unexpected fixture request ${options.method ?? "GET"} ${url.pathname}`);
  };
});

afterEach(async () => {
  try {
    await act(async () => root.unmount());
  } finally {
    host.remove();
    globalThis.fetch = nativeFetch;
    delete (globalThis as typeof globalThis & { selectedTestUnit?: number }).selectedTestUnit;
  }
});

test("authorized stock bucket detail preserves explicit location and renders only safe identity/quantity fields", async () => {
  const detailCalls: URL[] = [];
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://inventory.test.invalid");
    detailCalls.push(url);
    assert.equal(options.method ?? "GET", "GET");
    return json({
      item_code: "TEST-SAFE-CHAIR", item_description: "TEST ONLY chair", variant_sku: "SKU-SAFE",
      variant_description: "TEST ONLY finish", brand_name: "TEST ONLY brand",
      location_code: "WH-A", location_name: "TEST ONLY north bay",
      unit_code: "A", unit_name: "TEST ONLY Unit A", quantity: "8.250000",
      supplier_name: "PRIVATE_SUPPLIER", reserved_quantity: "PRIVATE_RESERVED",
      unit_cost: "PRIVATE_COST", inventory_value: "PRIVATE_VALUE", movements: ["PRIVATE_HISTORY"],
    });
  };
  await render(<InventoryStockDetail bucketId={17} unitId={1} locationId="11" onClose={() => {}} />);
  assert.equal(detailCalls[0].pathname, "/api/stock-balances/17/");
  assert.deepEqual(Object.fromEntries(detailCalls[0].searchParams), { unit: "1", location: "11" });
  assert.match(host.textContent ?? "", /TEST-SAFE-CHAIR|TEST ONLY chair/);
  assert.match(host.textContent ?? "", /SKU-SAFE/);
  assert.match(host.textContent ?? "", /8\.250000/);
  assert.doesNotMatch(host.textContent ?? "", /PRIVATE_SUPPLIER|PRIVATE_RESERVED|PRIVATE_COST|PRIVATE_VALUE|PRIVATE_HISTORY/);
});

for (const stale of ["success", "denial"] as const) {
  test(`obsolete stock detail ${stale} cannot replace the current business-unit detail`, async () => {
    let resolveA: ((response: Response) => void) | undefined;
    globalThis.fetch = (path) => {
      const url = new URL(String(path), "https://inventory.test.invalid");
      if (url.pathname.endsWith("/31/") && url.searchParams.get("unit") === "1") return new Promise<Response>((resolve) => { resolveA = resolve; });
      return Promise.resolve(json({
        item_code: "TEST-B-CURRENT", item_description: "TEST ONLY B item", variant_sku: "B-SKU",
        brand_name: "B brand", location_name: "B location", unit_name: "Unit B", quantity: "2.50",
      }));
    };
    await render(<InventoryStockDetail bucketId={31} unitId={1} locationId="11" onClose={() => {}} />);
    await render(<InventoryStockDetail bucketId={31} unitId={2} locationId="" onClose={() => {}} />);
    await flush();
    await flush();
    assert.match(host.textContent ?? "", /TEST-B-CURRENT/);
    await act(async () => resolveA?.(stale === "success"
      ? json({ item_code: "TEST-A-STALE", item_description: "A stale" })
      : json({ detail: "TEST ONLY stale A denial" }, 403)));
    await flush();
    assert.match(host.textContent ?? "", /TEST-B-CURRENT/);
    assert.doesNotMatch(host.textContent ?? "", /TEST-A-STALE|stale A denial/);
  });
}

function reportFixture(page: number, scopeUnit = 1, panel: "inventory" | "transfer_history" = "inventory") {
  const inventory = panel === "inventory";
  return {
    panel,
    title: `TEST ONLY ${panel}`,
    granularity: "daily",
    currency: "QAR",
    filters: { start: "2031-01-01", end: "2031-01-31", location_id: null, unit_id: scopeUnit, company_id: null },
    scope: {
      scope_type: "business_unit",
      business_unit: { id: scopeUnit, code: "TST", name: "TEST ONLY Unit" },
      business_unit_ids: [scopeUnit],
      company: null,
    },
    fields: inventory
      ? ["business_unit", "item_id", "description", "brand_name", "warehouse_location", "on_hand_quantity", "reserved_quantity", "inventory_value", "supplier_unit_cost"]
      : ["business_unit", "period", "transfer_id", "transfer_reference", "created_at", "source_location", "destination_location", "quantity", "transfer_value"],
    rows: inventory ? [{
      business_unit: "TEST ONLY Unit A", item_id: "TEST-ITEM-001", description: "TEST ONLY on-hand stock",
      brand_name: "TEST ONLY brand", warehouse_location: "WH-A · TEST ONLY receiving",
      on_hand_quantity: "13.7500", reserved_quantity: "PRIVATE_RESERVED",
      inventory_value: "PRIVATE_VALUE", supplier_unit_cost: "PRIVATE_COST",
    }] : [{
      business_unit: "TEST ONLY Unit A", period: "2031-01-10", transfer_id: 73,
      transfer_reference: "TEST-TRANSFER-73", created_at: "2031-01-10T08:30:00+03:00",
      source_location: "TEST ONLY East", destination_location: "TEST ONLY West",
      quantity: "2.250", transfer_value: "PRIVATE_TRANSFER_VALUE",
    }],
    pagination: { page, page_size: 20, total_count: 21, total_pages: 2, previous_page: page > 1 ? page - 1 : null, next_page: page < 2 ? page + 1 : null },
    policy_label: "TEST ONLY",
  };
}

function assignedCatalog() {
  const scope = { business_units: [{ id: 1, code: "TST", name: "TEST ONLY Unit" }], companies: [], locations: [{ id: 71, name: "TEST ONLY report location", business_units: [1] }] };
  return {
    panels: [{ code: "inventory", label: "Inventory" }, { code: "transfer_history", label: "Transfers" }],
    scopes: { inventory: scope, transfer_history: scope },
  };
}

test("inventory register requires assigned panel and BU scope, sends explicit calendar filters and masks sensitive fields", async () => {
  const reportCalls: URL[] = [];
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://inventory.test.invalid");
    assert.equal(options.method ?? "GET", "GET");
    if (url.pathname === "/api/reports/") return json(assignedCatalog());
    if (url.pathname === "/api/reports/inventory/") {
      reportCalls.push(url);
      return json(reportFixture(Number(url.searchParams.get("page"))));
    }
    throw new Error(`Unexpected report request ${url.pathname}`);
  };
  await render(<InventoryRegisters selectedUnitId={1} />);
  assert.equal(host.querySelector("#inventory-registers-title")?.textContent, "Inventory register");
  assert.equal(reportCalls.length, 1);
  const query = reportCalls[0].searchParams;
  assert.equal(query.get("unit"), "1");
  assert.equal(query.get("period"), "daily");
  assert.match(query.get("start") ?? "", /^\d{4}-\d{2}-\d{2}$/);
  assert.match(query.get("end") ?? "", /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(query.get("page"), "1");
  assert.equal(query.get("page_size"), "20");
  assert.match(host.textContent ?? "", /TEST-ITEM-001/);
  assert.match(host.textContent ?? "", /13\.7500/);
  assert.match(host.textContent ?? "", /TEST ONLY Unit A/);
  assert.match(host.textContent ?? "", /TEST ONLY receiving/);
  assert.doesNotMatch(host.textContent ?? "", /PRIVATE_COST|PRIVATE_VALUE|PRIVATE_MARGIN|PRIVATE_RESERVED/);
  const next = host.querySelector<HTMLButtonElement>('[aria-label="Next report page"]');
  assert.ok(next && !next.disabled);
  await act(async () => next.click());
  await flush();
  assert.equal(reportCalls.at(-1)?.searchParams.get("page"), "2");
});

test("transfer register displays service period and business unit and offers scoped refresh", async () => {
  const catalogCalls: URL[] = [];
  const reportCalls: URL[] = [];
  globalThis.fetch = async (path) => {
    const url = new URL(String(path), "https://inventory.test.invalid");
    if (url.pathname === "/api/reports/") {
      catalogCalls.push(url);
      return json(assignedCatalog());
    }
    if (url.pathname === "/api/reports/transfer_history/") {
      reportCalls.push(url);
      return json(reportFixture(1, 1, "transfer_history"));
    }
    throw new Error(`Unexpected transfer report request ${url.pathname}`);
  };
  await render(<InventoryRegisters selectedUnitId={1} />);
  await setValue(host.querySelector<HTMLSelectElement>('[aria-label="Inventory report panel"]')!, "transfer_history");
  assert.equal(host.querySelector("#inventory-registers-title")?.textContent, "Transfer history register");
  assert.equal(reportCalls.length, 1);
  assert.match(host.textContent ?? "", /TEST-TRANSFER-73/);
  assert.match(host.textContent ?? "", /2031-01-10/);
  assert.match(host.textContent ?? "", /TEST ONLY Unit A/);
  const transferHeaders = [...host.querySelectorAll("thead th")].map((header) => header.textContent?.trim());
  assert.ok(transferHeaders.includes("Business Unit"));
  assert.ok(transferHeaders.includes("Period"));
  assert.doesNotMatch(host.textContent ?? "", /PRIVATE_TRANSFER_VALUE/);
  const refresh = host.querySelector<HTMLButtonElement>('[aria-label="Refresh current inventory register"]');
  assert.ok(refresh);
  assert.equal(refresh.disabled, false);
  await act(async () => refresh.click());
  await flush();
  assert.equal(catalogCalls.length, 2);
  assert.equal(reportCalls.length, 2);
  assert.equal(reportCalls.at(-1)?.searchParams.get("unit"), "1");
  assert.equal(reportCalls.at(-1)?.pathname, "/api/reports/transfer_history/");
});

test("unassigned report panel/data scope does not call a broader report", async () => {
  let requestReports = 0;
  globalThis.fetch = async (path) => {
    const url = new URL(String(path), "https://inventory.test.invalid");
    if (url.pathname === "/api/reports/") return json({
      panels: [{ code: "inventory", label: "Inventory" }],
      scopes: {
        inventory: { business_units: [], companies: [], locations: [] },
        transfer_history: { business_units: [], companies: [], locations: [] },
      },
    });
    requestReports += 1;
    return json(reportFixture(1), 403);
  };
  await render(<InventoryRegisters selectedUnitId={1} />);
  assert.match(host.textContent ?? "", /No inventory data scope is assigned/);
  assert.match(host.textContent ?? "", /on-hand is a current snapshot/);
  assert.match(host.textContent ?? "", /transfer created-at calendar/);
  assert.equal(requestReports, 0);
});

test("read_stock-only account with no assigned report panel never requests report data", async () => {
  let reportDataRequests = 0;
  globalThis.fetch = async (path) => {
    const url = new URL(String(path), "https://inventory.test.invalid");
    if (url.pathname === "/api/reports/") return json({
      panels: [],
      scopes: {
        inventory: { business_units: [], companies: [], locations: [] },
        transfer_history: { business_units: [], companies: [], locations: [] },
      },
    });
    reportDataRequests += 1;
    return json(reportFixture(1), 403);
  };
  await render(<InventoryRegisters selectedUnitId={1} />);
  assert.match(host.textContent ?? "", /Inventory panel is not assigned/);
  assert.equal(reportDataRequests, 0);
});

test("server-denied or mismatched report scope displays no rows", async () => {
  let deny = true;
  globalThis.fetch = async (path) => {
    const url = new URL(String(path), "https://inventory.test.invalid");
    if (url.pathname === "/api/reports/") return json(assignedCatalog());
    if (url.pathname === "/api/reports/inventory/") {
      return deny ? json({ detail: "TEST ONLY report denied" }, 403) : json(reportFixture(1, 2));
    }
    throw new Error(`Unexpected report request ${url.pathname}`);
  };
  await render(<InventoryRegisters selectedUnitId={1} />);
  assert.match(host.textContent ?? "", /server denied or could not return/);
  assert.doesNotMatch(host.textContent ?? "", /TEST-ITEM-001/);
  await act(async () => root.unmount());
  root = createRoot(host);
  deny = false;
  await render(<InventoryRegisters selectedUnitId={1} />);
  assert.match(host.textContent ?? "", /did not confirm the explicitly selected business unit/);
  assert.doesNotMatch(host.textContent ?? "", /TEST-ITEM-001/);
});

test("warehouse workbench reuses receiving preparation and receipts only, without duplicate forms or implicit writes", async () => {
  const workbenchCalls: URL[] = [];
  const writes: URL[] = [];
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://inventory.test.invalid");
    workbenchCalls.push(url);
    if (options.method && options.method !== "GET") writes.push(url);
    if (url.pathname === "/api/purchasing/quotes/") return json(pager());
    if (url.pathname === "/api/purchasing/commitments/") return json(pager());
    if (url.pathname === "/api/reports/purchase_history/receipts/") return json(pager());
    throw new Error(`Unexpected warehouse-workbench request ${options.method ?? "GET"} ${url.pathname}`);
  };
  await render(<PurchasingWorkbench
    mode="warehouse"
    selectedUnitId={1}
    selectedUnitName="TEST ONLY Unit A"
    isStaff={false}
    username="TEST_ONLY_operator"
    csrfToken="TEST-CSRF"
    locations={[{ id: 11, code: "WH-A", name: "TEST ONLY warehouse", parent_id: null }]}
    onOpenReports={() => {}}
  />);
  assert.equal(host.querySelectorAll("#pw-receipt-title").length, 1);
  assert.equal(host.querySelectorAll("#pw-commitment-create-title").length, 1);
  assert.equal(host.querySelectorAll(".packing-preview").length, 1);
  assert.equal(host.querySelectorAll('input[type="file"]').length, 1);
  assert.equal(host.querySelector("#pw-quote-id"), null);
  assert.equal(host.querySelector("#pw-create-quote-title"), null);
  assert.equal(host.querySelector("#pw-quote-actions-title"), null);
  assert.equal(host.querySelector("#pw-sales-return-summary"), null);
  assert.deepEqual([...host.querySelectorAll(".pr-tabs > button")].map((button) => button.textContent?.trim()), ["Receipts"]);
  assert.equal(workbenchCalls.some((url) => url.pathname === "/api/purchasing/quotes/" && url.searchParams.get("page_size") === "20"), false);
  assert.equal(workbenchCalls.some((url) => url.pathname.includes("/reports/sales/") || url.pathname.includes("/reservations/")), false);
  assert.deepEqual(writes, []);
});

test("ordinary stock-reader status does not imply capacity permission; capacity units remain independent", async () => {
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://inventory.test.invalid");
    assert.equal(options.method ?? "GET", "GET");
    if (url.pathname === "/api/warehouse-capacity/") return json({ results: [] });
    throw new Error(`Unexpected capacity request ${url.pathname}`);
  };
  await render(<WarehouseCapacity isSuperuser={false} csrfToken="TEST-CSRF" />);
  assert.match(host.textContent ?? "", /No warehouse capacity locations are assigned/);
  assert.match(host.textContent ?? "", /Only explicitly assigned accounts can view or edit each physical location/);
  assert.equal(host.querySelector("input"), null);
  assert.doesNotMatch(host.textContent ?? "", /stock grants allow capacity|4\.125/);
});

test("Inventory sidebar children are stable function destinations rather than scroll selectors", () => {
  const inventory = sidebarSections({ isStaff: false, isSuperuser: false, canManageReportPanels: false })
    .find((section) => section.id === "inventory");
  assert.ok(inventory);
  assert.deepEqual(inventory.children.map((child) => child.id), ["main", "locations", "receipt", "commitments", "progress", "packing", "registers", "transfers", "capacity"]);
  assert.ok(inventory.children.every((child) => !("selector" in child)));
});

test("App location filter is scoped on first BU commit and stale A stock outcomes cannot alter B", async () => {
  const snapshots: string[] = [];
  const profiler = (id: string, phase: string) => {
    if (id === "inventory-app") snapshots.push(`${phase}:${host.textContent ?? ""}`);
  };
  await render(<Profiler id="inventory-app" onRender={profiler}><App /></Profiler>);
  const inventoryButton = [...host.querySelectorAll<HTMLButtonElement>(".nav-section-toggle")]
    .find((button) => button.title === "Inventory & Warehouses");
  assert.ok(inventoryButton);
  await act(async () => inventoryButton.click());
  await flush();
  const inventoryChild = inventoryButton.closest(".section-navigation-group")?.querySelector<HTMLButtonElement>(".section-navigation-children button");
  assert.ok(inventoryChild);
  await act(async () => inventoryChild.click());
  await flush();
  const locationSelect = host.querySelector<HTMLSelectElement>('select[aria-label="Inventory location filter"]');
  assert.ok(locationSelect);
  await act(async () => host.querySelector<HTMLButtonElement>(".inventory-item-link")?.click());
  await flush();
  assert.match(host.querySelector(".inventory-bucket-detail")?.textContent ?? "", /4\.1250/);
  delayUnitAStock = true;
  const filterCommitStart = snapshots.length;
  await setValue(locationSelect, "11");
  const firstLocationCommit = snapshots.slice(filterCommitStart).find((snapshot) => snapshot.includes("Loading authorized inventory…"));
  assert.ok(firstLocationCommit, "The location change commits synchronously into a cleared loading state.");
  assert.doesNotMatch(firstLocationCommit, /4\.1250|TEST ONLY item 1|STOCK BUCKETS\s*1/);
  assert.match(firstLocationCommit, /0STOCK BUCKETS/);
  assert.equal(host.querySelector(".inventory-bucket-detail"), null, "A detail opened before the scope change is cleared in the same commit.");
  const locationRequestIndex = pendingStock.length - 1;
  assert.equal(pendingStock[locationRequestIndex]?.url.searchParams.get("location"), "11");
  await replyStock(locationRequestIndex, json(stockPage(1, "11")));

  delayUnitAStock = true;
  await setValue(locationSelect, "");
  const staleRequestIndex = pendingStock.length - 1;
  assert.equal(pendingStock[staleRequestIndex]?.url.searchParams.get("unit"), "1");
  await setValue(host.querySelector<HTMLSelectElement>('select[aria-label="Business unit"]')!, "2");
  const bList = calls.findLast((call) => call.url.pathname === "/api/stock-balances/" && call.url.searchParams.get("unit") === "2");
  assert.ok(bList);
  assert.equal(bList.url.searchParams.has("location"), false, "Unit A location must not be sent with the first Unit B request");
  const bCommit = snapshots.find((snapshot) => snapshot.includes("TEST ONLY Unit 2"));
  assert.ok(bCommit);
  assert.doesNotMatch(bCommit, /TEST ONLY permitted location 1|TEST ONLY item 1/);
  await replyStock(staleRequestIndex, json({ detail: "TEST ONLY stale A denied" }, 403));
  assert.doesNotMatch(host.querySelector(".inventory-view")?.textContent ?? "", /stale A denied|TEST ONLY permitted location 1/);
  assert.equal(host.querySelector('select[aria-label="Inventory location filter"]')?.value, "");

  const receivingNavigation = [...host.querySelectorAll<HTMLButtonElement>(".section-navigation-children button")]
    .find((button) => button.getAttribute("data-workspace-link") === "inventory/receipt");
  assert.ok(receivingNavigation);
  await act(async () => receivingNavigation.click());
  await flush();
  assert.ok(host.querySelector("#pw-receipt-title"), "Receiving child navigation reveals the existing receipt form.");
  const packingNavigation = [...host.querySelectorAll<HTMLButtonElement>(".section-navigation-children button")]
    .find((button) => button.textContent?.includes("Packing-list preview"));
  assert.ok(packingNavigation);
  await act(async () => packingNavigation.click());
  await flush();
  const packingPreview = host.querySelector<HTMLElement>(".packing-preview");
  assert.ok(packingPreview);
  assert.equal(packingPreview.closest("details")?.open, true, "Packing-list child opens its preparation disclosure.");
  const registerNavigation = [...host.querySelectorAll<HTMLButtonElement>(".section-navigation-children button")]
    .find((button) => button.textContent?.includes("Inventory & transfer registers"));
  assert.ok(registerNavigation);
  await act(async () => registerNavigation.click());
  await flush();
  assert.equal(host.querySelector<HTMLDetailsElement>(".inventory-registers-disclosure")?.open, true);
  assert.ok(host.querySelector("#inventory-registers-title"), "Register child navigation reveals the read-only report area.");
});

test("successful warehouse receipt posts once, refreshes current ledger quantity and clears selected stock detail", async () => {
  staffMode = true;
  await render(<App />);
  const inventoryDisclosure = [...host.querySelectorAll<HTMLButtonElement>(".nav-section-toggle")]
    .find((button) => button.title === "Inventory & Warehouses");
  assert.ok(inventoryDisclosure);
  await act(async () => inventoryDisclosure.click());
  await flush();
  const inventoryNavigation = inventoryDisclosure.closest(".section-navigation-group")?.querySelector<HTMLButtonElement>(".section-navigation-children button");
  assert.ok(inventoryNavigation);
  await act(async () => inventoryNavigation.click());
  await flush();

  await setValue(host.querySelector<HTMLSelectElement>('[aria-label="Inventory rows per page"]')!, "25");
  await setValue(host.querySelector<HTMLInputElement>('[aria-label="Search inventory items"]')!, "receipt-refresh-term");
  await act(async () => host.querySelector<HTMLFormElement>(".search-form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();
  await setValue(host.querySelector<HTMLSelectElement>('[aria-label="Inventory location filter"]')!, "11");

  await act(async () => host.querySelector<HTMLButtonElement>(".inventory-item-link")?.click());
  await flush();
  assert.match(host.querySelector(".inventory-bucket-detail")?.textContent ?? "", /4\.1250/);

  const receivingNavigation = [...host.querySelectorAll<HTMLButtonElement>(".section-navigation-children button")]
    .find((button) => button.getAttribute("data-workspace-link") === "inventory/receipt");
  assert.ok(receivingNavigation);
  await act(async () => receivingNavigation.click());
  await flush();
  const supplier = host.querySelector<HTMLSelectElement>("#pw-receipt-supplier");
  const form = supplier?.closest("form");
  assert.ok(supplier && form);
  const grid = form.querySelector(".pw-form-grid");
  const variant = grid?.querySelector<HTMLInputElement>('input[inputmode="numeric"][required]');
  const location = grid?.querySelector<HTMLSelectElement>("select");
  const quantity = grid?.querySelectorAll<HTMLInputElement>('input[type="number"]')[0];
  const unitCost = grid?.querySelectorAll<HTMLInputElement>('input[type="number"]')[1];
  assert.ok(variant && location && quantity && unitCost);
  await setValue(supplier, "1");
  await setValue(variant, "17");
  await setValue(location, "11");
  await setValue(quantity, "1.25");
  await setValue(unitCost, "2.50");
  const submit = form.querySelector<HTMLButtonElement>('button[type="submit"]');
  assert.ok(submit && !submit.disabled);
  const initialLedgerReads = calls.filter((call) => call.url.pathname === "/api/stock-balances/").length;
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();

  assert.equal(receiptPosts.length, 1, "One explicit submit results in exactly one receipt POST.");
  const [post] = receiptPosts;
  assert.equal(post.url.pathname, "/api/purchasing/receipts/");
  assert.deepEqual(post.payload, {
    business_unit_id: 1,
    supplier_id: 1,
    lines: [{ variant_id: 17, location_id: 11, quantity: "1.25", unit_cost: "2.50" }],
    charges: [],
  });
  const idempotencyKey = new Headers(post.options.headers).get("Idempotency-Key");
  assert.ok(idempotencyKey);
  assert.equal(calls.filter((call) => call.url.pathname === "/api/purchasing/receipts/" && call.options.method === "POST").length, 1);
  const refreshedLedgerRequest = calls.filter((call) => call.url.pathname === "/api/stock-balances/").at(-1)?.url;
  assert.ok(refreshedLedgerRequest);
  assert.equal(calls.filter((call) => call.url.pathname === "/api/stock-balances/").length, initialLedgerReads + 1);
  assert.equal(refreshedLedgerRequest.searchParams.get("page_size"), "25");
  assert.equal(refreshedLedgerRequest.searchParams.get("search"), "receipt-refresh-term");
  assert.equal(refreshedLedgerRequest.searchParams.get("location"), "11");
  assert.equal(refreshedLedgerRequest.searchParams.get("page"), "1");
  assert.equal(host.querySelector(".inventory-bucket-detail"), null, "Successful receipt clears the separately cached selected detail.");
  assert.match(host.querySelector(".inventory-view tbody tr")?.textContent ?? "", /5\.3750/);
  assert.doesNotMatch(host.querySelector(".inventory-view")?.textContent ?? "", /4\.1250/);
  assert.equal(new Headers(post.options.headers).get("Idempotency-Key"), idempotencyKey, "Refresh leaves the exact submitted operation identity untouched.");
});

test("authenticated capacity-only user reaches independently assigned m²/m³ edit controls with no business-unit data reads", async () => {
  const isolatedCalls: { url: URL; options: RequestInit }[] = [];
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://capacity-only.test.invalid");
    isolatedCalls.push({ url, options });
    if (url.pathname === "/api/auth/csrf/") return json({ csrfToken: "CAPACITY-ONLY-CSRF" });
    if (url.pathname === "/api/auth/me/") return json({
      authenticated: true, username: "TEST_capacity_only_operator", is_staff: false, is_superuser: false,
    });
    if (url.pathname === "/api/organization/") return json({ detail: "No business-unit access is assigned." }, 403);
    if (url.pathname === "/api/warehouse-capacity/") return json({ results: [{
      location: { id: 41, code: "TEST-WH-41", name: "TEST ONLY capacity-only warehouse" },
      capability: "edit_capacity", configured: true,
      total_floor_area_m2: "125.250000", allocated_storage_floor_area_m2: "80.125000",
      received_goods_volume_m3: "14.750000", available_floor_area_m2: "45.125000",
      updated_at: "2031-01-10T08:00:00Z",
    }] });
    throw new Error(`Unexpected capacity-only request ${options.method ?? "GET"} ${url.pathname}`);
  };
  await render(<App />);
  assert.match(host.textContent ?? "", /TEST_capacity_only_operator/);
  const inventoryDisclosure = [...host.querySelectorAll<HTMLButtonElement>(".nav-section-toggle")]
    .find((button) => button.title === "Inventory & Warehouses");
  assert.ok(inventoryDisclosure);
  await act(async () => inventoryDisclosure.click());
  await flush();
  const capacityNavigation = [...(inventoryDisclosure.closest(".section-navigation-group")
    ?.querySelectorAll<HTMLButtonElement>(".section-navigation-children button") ?? [])]
    .find((button) => button.textContent?.includes("Warehouse capacity"));
  assert.ok(capacityNavigation);
  await act(async () => capacityNavigation.click());
  await flush();
  assert.match(host.querySelector(".capacity-section")?.textContent ?? "", /TEST ONLY capacity-only warehouse/);
  assert.match(host.querySelector(".capacity-section")?.textContent ?? "", /45\.125000 m²/);
  assert.equal(host.querySelector<HTMLInputElement>('[aria-label="Total floor area in m² for TEST ONLY capacity-only warehouse"]')?.value, "125.250000");
  assert.equal(host.querySelector<HTMLInputElement>('[aria-label="Received-goods volume in m³ for TEST ONLY capacity-only warehouse"]')?.value, "14.750000");
  assert.match(host.querySelector(".capacity-section")?.textContent ?? "", /Assigned: view and edit/);
  assert.ok(host.querySelector('[aria-label="Total floor area in m² for TEST ONLY capacity-only warehouse"]'));
  assert.match(host.textContent ?? "", /Business-unit context unavailable/);
  assert.equal(isolatedCalls.filter((call) => call.url.pathname === "/api/warehouse-capacity/").length, 1);
  assert.equal(isolatedCalls.some((call) => call.url.pathname === "/api/stock-balances/"), false);
  assert.equal(isolatedCalls.some((call) => call.url.pathname.startsWith("/api/reports/")), false);
  assert.equal(isolatedCalls.some((call) => call.url.pathname.startsWith("/api/purchasing/")), false);
});
