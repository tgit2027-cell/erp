import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import App from "../src/App";
import { buildPaletteColors } from "../src/custom-palette-colors.mjs";

// Fictional responses only: no live API calls, credentials, grants or DB writes.
const units = [
  { id: 1, code: "TVL", name: "TEST ONLY Unit A", theme_palette: "tivoli" },
  { id: 2, code: "RBB", name: "TEST ONLY Unit B", theme_palette: "roche" },
];
const nativeFetch = globalThis.fetch;
let root, container, selectedUnit, calls, errors, warnings, originalError, originalWarn, deferredRequests;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}

function stockPage(url) {
  const unit = Number(url.searchParams.get("unit"));
  const page = Number(url.searchParams.get("page"));
  const pageSize = Number(url.searchParams.get("page_size"));
  const total = unit === 1 ? 103 : 61;
  const letter = unit === 1 ? "A" : "B";
  const locationCode = `TEST-${letter}-WH`;
  const search = url.searchParams.get("search") ?? "";
  const locationCodeSearch = /^TEST-[AB]-WH$/i.test(search);
  const matchesLocationCode = !locationCodeSearch || locationCode.toLowerCase() === search.toLowerCase();
  const filteredTotal = locationCodeSearch ? (matchesLocationCode ? 1 : 0) : total;
  const totalPages = Math.max(1, Math.ceil(filteredTotal / pageSize));
  const offset = (page - 1) * pageSize;
  return {
    results: Array.from({ length: Math.min(pageSize, Math.max(0, filteredTotal - offset)) }, (_, index) => ({
      id: unit * 10000 + offset + index,
      item_code: `TEST ONLY ${letter} item ${offset + index + 1}`,
      item_description: `Synthetic ${letter} description`,
      brand_name: `TEST ONLY ${letter} brand`, brand_code: units[unit - 1].code,
      variant_sku: `${letter}-${offset + index + 1}`, variant_description: "Synthetic variant",
      location_code: locationCode,
      location_name: `TEST ONLY ${letter} warehouse`,
      unit_code: units[unit - 1].code, unit_name: units[unit - 1].name,
      quantity: "1.000", is_synthetic: true,
    })),
    pagination: {
      page, page_size: pageSize, total_count: filteredTotal, total_pages: totalPages,
      previous_page: page > 1 && filteredTotal > 0 ? page - 1 : null,
      next_page: page < totalPages && filteredTotal > 0 ? page + 1 : null,
    },
  };
}

async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}

async function click(label) {
  const disclosure = Array.from(container.querySelectorAll(".nav-section-toggle"))
    .find((node) => node.title === label);
  if (disclosure) {
    if (disclosure.getAttribute("aria-expanded") !== "true") {
      await act(async () => disclosure.click());
      await flush();
    }
    const firstChild = disclosure.closest(".section-navigation-group").querySelector(".section-navigation-children button");
    await act(async () => firstChild.click());
    await flush();
    return;
  }
  const button = Array.from(container.querySelectorAll("button:not(.nav-section-toggle)")).find(
    (node) => node.getAttribute("aria-label") === label || node.textContent.includes(label),
  );
  assert.ok(button, `Button "${label}" exists`);
  assert.equal(button.disabled, false);
  await act(async () => button.click());
  await flush();
}

async function select(label, value) {
  const element = container.querySelector(`select[aria-label="${label}"]`);
  assert.ok(element);
  await act(async () => {
    element.value = String(value);
    element.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
}

function rowCodes() {
  return Array.from(container.querySelectorAll(".inventory-view tbody tr td:first-child > strong"),
    (node) => node.textContent);
}

function assertNoAlert() {
  // Keep failure output bounded; inspecting a DOM node also traverses React fibers.
  assert.equal(container.querySelector('[role="alert"]')?.textContent ?? null, null);
}

function assertLoading(value) {
  assert.equal(container.querySelector(".inventory-view .search-form").getAttribute("aria-busy"), String(value));
  assert.equal(container.querySelector('.inventory-view button[type="submit"]').disabled, value);
  assert.equal(container.textContent.includes("Loading authorized inventory…"), value);
  if (value) {
    assert.equal(container.querySelector('[aria-label="Previous inventory page"]').disabled, true);
    assert.equal(container.querySelector('[aria-label="Next inventory page"]').disabled, true);
  }
}

function assertPage(unit, page = 1, pageSize = 50, theme = units[unit - 1].theme_palette) {
  const expected = stockPage(new URL(`https://synthetic.invalid/api/stock-balances/?unit=${unit}&page=${page}&page_size=${pageSize}`));
  assert.deepEqual(rowCodes(), expected.results.map((row) => row.item_code));
  assert.equal(container.querySelector(".example-count").textContent, `${expected.pagination.total_count}STOCK BUCKETS`);
  const status = container.querySelector('[aria-label="Inventory pagination"] [role="status"]').textContent;
  assert.ok(status.includes(`Page ${page} of ${expected.pagination.total_pages}`), status);
  assert.ok(status.includes(`of ${expected.pagination.total_count} rows`), status);
  assert.equal(container.querySelector('[aria-label="Previous inventory page"]').disabled, page === 1);
  assert.equal(container.querySelector('[aria-label="Next inventory page"]').disabled, expected.pagination.next_page === null);
  assert.equal(container.querySelector('select[aria-label="Business unit"]').value, String(unit));
  assert.equal(document.documentElement.dataset.unitTheme, theme);
  assertLoading(false);
}

// Capture a snapshot at request time, then allow each response to settle in any
// order, even if a future implementation aborts it. Invalidation must still work.
function delayStock() {
  const baseFetch = globalThis.fetch;
  const pending = [];
  globalThis.fetch = async (path, options) => {
    const response = await baseFetch(path, options);
    const url = new URL(String(path), "https://synthetic.invalid");
    if (url.pathname !== "/api/stock-balances/") return response;
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    pending.push({ url, response, resolve });
    deferredRequests.push({ response, resolve });
    return promise;
  };
  return pending;
}

async function reply(request, response = request.response) {
  assert.ok(request, "Expected stock request was issued");
  await act(async () => request.resolve(response));
  await flush();
}

async function mountSyntheticPaletteStaff() {
  await act(async () => root.unmount());
  root = createRoot(container);
  const baseFetch = globalThis.fetch;
  let palette = {
    id: 7, value: "custom:7", kind: "custom", label: "TEST ONLY custom palette",
    accent_color: "#B24F00", colors: buildPaletteColors("#B24F00"),
  };
  const customUnits = () => units.map((unit) => unit.id === 1
    ? { ...unit, theme_palette: "custom", palette_value: palette.value, custom_palette: palette }
    : { ...unit, palette_value: unit.theme_palette });
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    if (url.pathname === "/api/auth/me/") {
      calls.push({ url, ...options });
      return json({ authenticated: true, username: "synthetic_palette_staff", is_staff: true });
    }
    if (url.pathname === "/api/organization/") {
      const response = await baseFetch(path, options);
      const data = await response.json();
      return json({ ...data, units: customUnits(), selected_unit: customUnits()[0] });
    }
    if (url.pathname === "/api/governance/business-unit-palettes/") {
      calls.push({ url, ...options });
      assert.ok(!options.method || options.method === "GET", "This fixture never creates/assigns a palette");
      return json({ palettes: [palette], units: customUnits() });
    }
    if (url.pathname === "/api/governance/business-unit-palettes/custom/7/" && options.method === "PATCH") {
      calls.push({ url, ...options });
      const data = JSON.parse(options.body);
      palette = { ...palette, label: data.name, accent_color: data.accent_color, colors: buildPaletteColors(data.accent_color) };
      return json({ palette });
    }
    if (url.pathname === "/api/governance/reports/") {
      calls.push({ url, ...options });
      return json(Object.fromEntries([
        "users", "roles", "memberships", "units", "companies", "panels", "capabilities",
        "panel_grants", "data_grants", "budgets", "items", "locations",
      ].map((key) => [key, []])));
    }
    return baseFetch(path, options);
  };
  await act(async () => root.render(<App />));
  await flush();
  await click("Inventory & Warehouses");
  assertPage(1, 1, 50, "custom");
}

async function editPaletteAndReturnToInventory() {
  const organizationReads = calls.filter((call) => call.url.pathname === "/api/organization/").length;
  const precedingWrites = calls.filter((call) => call.method && call.method !== "GET").length;
  await click("Governance & Administration");
  await click("Business-unit palettes");
  await click("Edit selected");
  const input = container.querySelector(".custom-name-field input");
  assert.ok(input);
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "TEST ONLY refreshed palette");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => container.querySelector(".custom-palette-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  ));
  await flush();
  const writes = calls.filter((call) => call.method && call.method !== "GET").slice(precedingWrites);
  assert.equal(writes.length, 1, "Only the existing fictional palette is edited; no unit selection or grant writes");
  assert.equal(writes[0].url.pathname, "/api/governance/business-unit-palettes/custom/7/");
  assert.equal(writes[0].method, "PATCH");
  assert.equal(writes[0].headers["X-CSRFToken"], "synthetic-csrf-placeholder");
  assert.deepEqual(JSON.parse(writes[0].body), { name: "TEST ONLY refreshed palette", accent_color: "#B24F00" });
  assert.equal(calls.filter((call) => call.url.pathname === "/api/organization/").length, organizationReads + 1);
  assert.ok(!container.querySelector(".custom-palette-form"), "The palette edit completed");
  await click("Inventory & Warehouses");
  assert.equal(container.querySelector('select[aria-label="Business unit"]').value, "1");
  assert.ok(container.querySelector(".page-heading").textContent.includes("TEST ONLY Unit A"));
  assert.equal(document.documentElement.dataset.unitTheme, "custom");
  assert.equal(document.documentElement.style.getPropertyValue("--brand"), "#B24F00");
}

beforeEach(async () => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  selectedUnit = 1;
  calls = [];
  errors = [];
  warnings = [];
  deferredRequests = [];
  originalError = console.error;
  originalWarn = console.warn;
  console.error = (...args) => errors.push(args.map(String).join(" "));
  console.warn = (...args) => warnings.push(args.map(String).join(" "));
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, ...options });
    if (url.pathname === "/api/auth/csrf/") return json({ csrfToken: "synthetic-csrf-placeholder" });
    if (url.pathname === "/api/auth/me/") return json({
      authenticated: true, username: "synthetic_stock_viewer", is_staff: false,
    });
    if (url.pathname === "/api/organization/") return json({
      company: { name: "TEST ONLY company", code: "TEST" }, units,
      selected_unit: units[selectedUnit - 1],
      locations: [{ id: selectedUnit, parent_id: null, name: `TEST ONLY ${selectedUnit} location` }],
    });
    if (url.pathname === "/api/favorites/") return json({ available_destinations: [], favorites: [] });
    if (url.pathname === "/api/stock-balances/") return json(stockPage(url));
    if (url.pathname === "/api/warehouse-capacity/") return json({ results: [] });
    if (options.method === "POST" && /^\/api\/units\/[12]\/select\/$/.test(url.pathname)) {
      selectedUnit = Number(url.pathname.split("/")[3]);
      return json({ selected_unit: units[selectedUnit - 1] });
    }
    throw new Error(`Unexpected synthetic request ${options.method ?? "GET"} ${url.pathname}`);
  };
  await act(async () => root.render(<App />));
  await flush();
  await click("Inventory & Warehouses");
  assertPage(1);
});

afterEach(async () => {
  try {
    await act(async () => {
      for (const request of deferredRequests) request.resolve(request.response);
      await new Promise(setImmediate);
    });
    await act(async () => root.unmount());
  } finally {
    container.remove();
    globalThis.fetch = nativeFetch;
    console.error = originalError;
    console.warn = originalWarn;
  }
  assert.deepEqual(errors, [], "No React or other console errors");
  assert.deepEqual(warnings, [], "No React or other console warnings");
});

test("Inventory reads only the ledger projection and renders exact unreserved decimal strings", async () => {
  assert.ok(container.textContent.includes("Synthetic") || container.textContent.includes("SYNTHETIC"));
  assert.ok(container.textContent.includes("Unreserved quantity = on hand minus reserved"));
  assert.ok(container.textContent.includes("not available-to-promise"));
  assert.ok(container.textContent.includes("Warehouse capacity"));
  await click("Warehouse capacity");
  assert.ok(container.textContent.includes("No warehouse capacity locations are assigned"));
  await click("Inventory quantities");
  assert.equal(container.querySelector("thead th:last-child").textContent, "Unreserved quantity");
  const pending = delayStock();
  await click("Next inventory page");
  const data = stockPage(pending[0].url);
  data.results[0].quantity = "123456789012.34";
  await reply(pending[0], json(data));
  assert.equal(container.querySelector("tbody tr td:last-child").textContent, "123456789012.34");
  assert.ok(calls.some((call) => call.url.pathname === "/api/stock-balances/"));
  assert.ok(calls.every((call) => call.url.pathname !== "/api/stock/"), "No illustrative fallback");
  assert.equal(calls.filter((call) => call.method && call.method !== "GET").length, 0);
});

test("location-code search displays the scoped code and cannot expose another unit's stock", async () => {
  const search = async (term) => {
    const input = container.querySelector('input[aria-label="Search inventory items"]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, term);
    await act(async () => input.dispatchEvent(new Event("input", { bubbles: true })));
    await act(async () => container.querySelector(".search-form").dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    ));
    await flush();
  };

  await search("TEST-A-WH");
  const unitASearch = calls.filter((call) => call.url.pathname === "/api/stock-balances/").at(-1);
  assert.equal(unitASearch.url.searchParams.get("unit"), "1");
  assert.equal(unitASearch.url.searchParams.get("search"), "TEST-A-WH");
  assert.deepEqual(rowCodes(), ["TEST ONLY A item 1"]);
  assert.ok(container.textContent.includes("TEST-A-WH"));
  assert.ok(container.textContent.includes("TEST ONLY A warehouse"));
  assert.ok(container.textContent.includes("of 1 rows"));

  await select("Business unit", 2);
  const unitBWithOldCode = calls.filter((call) => call.url.pathname === "/api/stock-balances/").at(-1);
  assert.equal(unitBWithOldCode.url.searchParams.get("unit"), "2");
  assert.equal(unitBWithOldCode.url.searchParams.get("search"), "TEST-A-WH");
  assert.deepEqual(rowCodes(), []);
  assert.equal(container.querySelector(".example-count").textContent, "0STOCK BUCKETS");
  assert.ok(!container.textContent.includes("TEST-A-WH"));
  assert.ok(!container.textContent.includes("TEST ONLY A warehouse"));

  await search("TEST-B-WH");
  const unitBSearch = calls.filter((call) => call.url.pathname === "/api/stock-balances/").at(-1);
  assert.equal(unitBSearch.url.searchParams.get("unit"), "2");
  assert.equal(unitBSearch.url.searchParams.get("search"), "TEST-B-WH");
  assert.deepEqual(rowCodes(), ["TEST ONLY B item 1"]);
  assert.ok(container.textContent.includes("TEST-B-WH"));
  assert.ok(container.textContent.includes("TEST ONLY B warehouse"));
  assert.ok(!container.textContent.includes("TEST ONLY A warehouse"));
});

test("an empty Unit B ledger replaces A with an honest empty state and no example fallback", async () => {
  const pending = delayStock();
  await select("Business unit", 2);
  assert.deepEqual(rowCodes(), []);
  assertLoading(true);
  await reply(pending[0], json({
    results: [],
    pagination: { page: 1, page_size: 50, total_count: 0, total_pages: 1, previous_page: null, next_page: null },
  }));
  assert.deepEqual(rowCodes(), []);
  assert.ok(container.textContent.includes("No authorized synthetic ledger stock matches this search."));
  assert.equal(container.querySelector(".example-count").textContent, "0STOCK BUCKETS");
  assert.equal(container.querySelector('[aria-label="Previous inventory page"]').disabled, true);
  assert.equal(container.querySelector('[aria-label="Next inventory page"]').disabled, true);
  assert.equal(container.querySelector('select[aria-label="Business unit"]').value, "2");
  assert.equal(document.documentElement.dataset.unitTheme, "roche");
  assert.ok(calls.every((call) => call.url.pathname !== "/api/stock/"));
});

test("a denied B ledger never displays A rows or static examples and ignores late A success", async () => {
  const pending = delayStock();
  await click("Next inventory page");
  await select("Business unit", 2);
  assert.deepEqual(rowCodes(), []);
  await reply(pending[1], json({ detail: "TEST ONLY B ledger denied." }, 403));
  await reply(pending[0]);
  assert.deepEqual(rowCodes(), []);
  assert.equal(container.querySelector('[role="alert"]').textContent, "TEST ONLY B ledger denied.");
  assert.equal(container.querySelector(".example-count").textContent, "0STOCK BUCKETS");
  assert.equal(container.querySelector('select[aria-label="Business unit"]').value, "2");
  assert.equal(document.documentElement.dataset.unitTheme, "roche");
  assertLoading(false);
  assert.ok(calls.every((call) => call.url.pathname !== "/api/stock/"));
});

test("late Unit A stock cannot overwrite Unit B rows totals ordering or pagination", async () => {
  const pending = delayStock();
  await click("Next inventory page");
  await select("Business unit", 2);
  assert.deepEqual(pending.map((request) => [
    request.url.searchParams.get("unit"), request.url.searchParams.get("page"),
    request.url.searchParams.get("page_size"),
  ]), [["1", "2", "50"], ["2", "1", "50"]]);
  await reply(pending[1]);
  assertPage(2);
  await reply(pending[0]);
  assertPage(2);
  await click("Next inventory page");
  await reply(pending[2]);
  assertPage(2, 2);
});

test("obsolete stock success cannot clear the current context loading state", async () => {
  const pending = delayStock();
  await click("Next inventory page");
  await select("Business unit", 2);
  assertLoading(true);
  await reply(pending[0]);
  assertLoading(true);
  assert.deepEqual(rowCodes(), []);
  assert.equal(container.querySelector(".example-count").textContent, "0STOCK BUCKETS");
  assert.ok(container.textContent.includes("Inventory page not loaded"));
  await reply(pending[1]);
  assertPage(2);
});

test("returning to Unit A cannot revive its page from an earlier context generation", async () => {
  const pending = delayStock();
  await click("Next inventory page");
  await select("Business unit", 2);
  await select("Business unit", 1);
  assert.equal(pending.length, 3);
  await reply(pending[2]);
  assertPage(1);
  await reply(pending[1]);
  assertPage(1);
  await reply(pending[0]);
  assertPage(1); // Same unit ID is insufficient: old page 2 must not replace page 1.
  assertNoAlert();
});

for (const timing of ["while current stock is loading", "after current stock succeeds"]) {
  test(`obsolete stock denial cannot show a stale error ${timing}`, async () => {
    const pending = delayStock();
    await click("Next inventory page");
    await select("Business unit", 2);
    if (timing === "after current stock succeeds") await reply(pending[1]);
    await reply(pending[0], json({ detail: "TEST ONLY obsolete Unit A denial." }, 403));
    assertNoAlert();
    if (timing === "while current stock is loading") {
      assertLoading(true);
      assert.deepEqual(rowCodes(), []);
      await reply(pending[1]);
    }
    assertPage(2);
  });
}

for (const timing of ["before recovery", "after recovery"]) {
  test(`failed organization refresh cannot restore old stock settling ${timing}`, async () => {
    const pending = delayStock();
    await click("Next inventory page");
    const baseFetch = globalThis.fetch;
    let failRefresh = true;
    globalThis.fetch = async (path, options) => {
      if (String(path) === "/api/organization/" && failRefresh) {
        return json({ detail: "TEST ONLY organization refresh unavailable." }, 503);
      }
      return baseFetch(path, options);
    };
    await select("Business unit", 2);
    assert.ok(!container.querySelector(".inventory-view"));
    assert.equal(container.querySelector('[role="alert"]').textContent, "TEST ONLY organization refresh unavailable.");
    assert.equal(document.documentElement.dataset.unitTheme, "slate");
    if (timing === "before recovery") await reply(pending[0]);
    failRefresh = false;
    await click("Reload business-unit context");
    assert.equal(container.querySelector('select[aria-label="Business unit"]').value, "2");
    assert.deepEqual(rowCodes(), []);
    assertLoading(true);
    await reply(pending[1]);
    assertPage(2);
    if (timing === "after recovery") await reply(pending[0]);
    assertPage(2);
    assertNoAlert();
    assert.equal(calls.filter((call) => call.method === "POST").length, 1);
  });
}

test("current stock 403 clears prior rows totals and pagination and permits a scoped search retry", async () => {
  const pending = delayStock();
  await click("Next inventory page");
  await reply(pending[0], json({ detail: "TEST ONLY current stock access denied." }, 403));
  assert.equal(container.querySelector('[role="alert"]').textContent, "TEST ONLY current stock access denied.");
  assert.deepEqual(rowCodes(), []);
  assert.equal(container.querySelector(".example-count").textContent, "0STOCK BUCKETS");
  assert.ok(container.textContent.includes("Inventory page not loaded"));
  assert.equal(container.querySelector('[aria-label="Previous inventory page"]').disabled, true);
  assert.equal(container.querySelector('[aria-label="Next inventory page"]').disabled, true);
  assertLoading(false);
  const input = container.querySelector('input[aria-label="Search inventory items"]');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "  synthetic chair  ");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => container.querySelector(".search-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  ));
  await flush();
  assert.deepEqual(Object.fromEntries(pending[1].url.searchParams), {
    unit: "1", page: "1", page_size: "50", search: "synthetic chair",
  });
  assertLoading(true);
  await reply(pending[1]);
  assertPage(1);
  assertNoAlert();
  assert.equal(calls.filter((call) => call.method && call.method !== "GET").length, 0);
});

test("same-BU palette organization refresh starts a fresh stock load with the retained search", async () => {
  await mountSyntheticPaletteStaff();
  const input = container.querySelector('input[aria-label="Search inventory items"]');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "  synthetic chair  ");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => container.querySelector(".search-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  ));
  await flush();
  const pending = delayStock();
  await editPaletteAndReturnToInventory();
  assert.equal(pending.length, 1, "A new stock request is required even though the confirmed BU ID is unchanged");
  assert.deepEqual(Object.fromEntries(pending[0].url.searchParams), {
    unit: "1", page: "1", page_size: "50", search: "synthetic chair",
  });
  assert.deepEqual(rowCodes(), []);
  assertLoading(true);
  await reply(pending[0]);
  assertPage(1, 1, 50, "custom");
  assertNoAlert();
});

test("same-BU refreshed stock rejects a delayed pre-refresh page and its stale totals", async () => {
  await mountSyntheticPaletteStaff();
  const pending = delayStock();
  await click("Next inventory page");
  await editPaletteAndReturnToInventory();
  assert.equal(pending.length, 2, "The previous page request must be followed by a fresh context request");
  assert.deepEqual(pending.map((request) => Object.fromEntries(request.url.searchParams)), [
    { unit: "1", page: "2", page_size: "50" }, { unit: "1", page: "1", page_size: "50" },
  ]);
  await reply(pending[1]);
  assertPage(1, 1, 50, "custom");
  const oldPage = stockPage(pending[0].url);
  oldPage.pagination.total_count = 999;
  oldPage.pagination.total_pages = 20;
  await reply(pending[0], json(oldPage));
  assertPage(1, 1, 50, "custom");
  assertNoAlert();
});

test("same-BU refreshed stock 403 stays visible and cannot be undone by an old response", async () => {
  await mountSyntheticPaletteStaff();
  const pending = delayStock();
  await click("Next inventory page");
  await editPaletteAndReturnToInventory();
  assert.equal(pending.length, 2, "The same-BU refresh must recheck stock access");
  await reply(pending[1], json({ detail: "TEST ONLY refreshed stock access denied." }, 403));
  await reply(pending[0]);
  assert.deepEqual(rowCodes(), []);
  assert.equal(container.querySelector(".example-count").textContent, "0STOCK BUCKETS");
  assert.ok(container.textContent.includes("Inventory page not loaded"));
  assert.equal(container.querySelector('[aria-label="Previous inventory page"]').disabled, true);
  assert.equal(container.querySelector('[aria-label="Next inventory page"]').disabled, true);
  assert.equal(container.querySelector('[role="alert"]').textContent, "TEST ONLY refreshed stock access denied.");
  assertLoading(false);
  assert.equal(container.querySelector('select[aria-label="Business unit"]').value, "1");
  assert.equal(document.documentElement.dataset.unitTheme, "custom");
  assert.equal(document.documentElement.style.getPropertyValue("--brand"), "#B24F00");
});

test("successful same-BU refresh removes a resolved stock error and restores the fresh page", async () => {
  await mountSyntheticPaletteStaff();
  const pending = delayStock();
  await click("Next inventory page");
  await reply(pending[0], json({ detail: "TEST ONLY temporary stock failure." }, 503));
  assert.deepEqual(rowCodes(), []);
  assert.equal(container.querySelector('[role="alert"]').textContent, "TEST ONLY temporary stock failure.");
  await editPaletteAndReturnToInventory();
  assert.equal(pending.length, 2);
  assert.deepEqual(Object.fromEntries(pending[1].url.searchParams), { unit: "1", page: "1", page_size: "50" });
  assertLoading(true);
  await reply(pending[1]);
  assertPage(1, 1, 50, "custom");
  assertNoAlert();
});

test("an obsolete failure cannot restore a stock error after same-BU recovery", async () => {
  await mountSyntheticPaletteStaff();
  const pending = delayStock();
  await click("Next inventory page");
  await editPaletteAndReturnToInventory();
  await reply(pending[1], json({ detail: "TEST ONLY current temporary stock failure." }, 503));
  await editPaletteAndReturnToInventory();
  assert.equal(pending.length, 3);
  await reply(pending[2]);
  assertPage(1, 1, 50, "custom");
  assertNoAlert();
  await reply(pending[0], json({ detail: "TEST ONLY obsolete stock denial." }, 403));
  assertPage(1, 1, 50, "custom");
  assertNoAlert();
});

test("a current same-BU stock 403 replaces the earlier failure and stays visible", async () => {
  await mountSyntheticPaletteStaff();
  const pending = delayStock();
  await click("Next inventory page");
  await reply(pending[0], json({ detail: "TEST ONLY previous stock failure." }, 503));
  await editPaletteAndReturnToInventory();
  await reply(pending[1], json({ detail: "TEST ONLY current refreshed stock denial." }, 403));
  assert.deepEqual(Array.from(container.querySelectorAll('[role="alert"]'), (node) => node.textContent),
    ["TEST ONLY current refreshed stock denial."]);
  assert.deepEqual(rowCodes(), []);
  assert.equal(container.querySelector(".example-count").textContent, "0STOCK BUCKETS");
  assert.ok(container.textContent.includes("Inventory page not loaded"));
  assert.equal(container.querySelector('[aria-label="Previous inventory page"]').disabled, true);
  assert.equal(container.querySelector('[aria-label="Next inventory page"]').disabled, true);
  assertLoading(false);
});

test("stock recovery and search retry preserve an unrelated current sign-out error", async () => {
  await mountSyntheticPaletteStaff();
  const pending = delayStock();
  await click("Next inventory page");
  await reply(pending[0], json({ detail: "TEST ONLY temporary stock failure." }, 503));
  await editPaletteAndReturnToInventory();
  const baseFetch = globalThis.fetch;
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    if (url.pathname === "/api/auth/logout/") {
      calls.push({ url, ...options });
      assert.equal(options.method, "POST");
      assert.equal(options.headers["X-CSRFToken"], "synthetic-csrf-placeholder");
      return json({ detail: "TEST ONLY sign-out service unavailable." }, 503);
    }
    return baseFetch(path, options);
  };
  await click("Sign out");
  await reply(pending[1]);
  assertPage(1, 1, 50, "custom");
  const assertUnrelatedAlert = () => assert.deepEqual(
    Array.from(container.querySelectorAll('[role="alert"]'), (node) => node.textContent),
    ["TEST ONLY sign-out service unavailable."],
  );
  assertUnrelatedAlert();
  await act(async () => container.querySelector(".search-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  ));
  await flush();
  assertUnrelatedAlert();
  await reply(pending[2], json({ detail: "TEST ONLY current stock denial alongside sign-out failure." }, 403));
  assert.deepEqual(rowCodes(), []);
  assert.deepEqual(Array.from(container.querySelectorAll('[role="alert"]'), (node) => node.textContent), [
    "TEST ONLY sign-out service unavailable.",
    "TEST ONLY current stock denial alongside sign-out failure.",
  ]);
  await act(async () => container.querySelector(".search-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  ));
  await flush();
  assertUnrelatedAlert();
  await reply(pending[3]);
  assertPage(1, 1, 50, "custom");
  assertUnrelatedAlert();
});
