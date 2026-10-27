import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import InventoryRegisters from "../src/InventoryRegisters";

const originalFetch = globalThis.fetch;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
let root: ReturnType<typeof createRoot>, host: HTMLDivElement;
let locations: number[], catalogStatus: number, catalogReads: number;
let requests: URL[], deferred: ((value: Response) => void)[], delay: boolean;
function catalog() {
  const scope = {
    business_units: [{ id: 1, name: "TEST ONLY BU" }], companies: [],
    locations: locations.map(id => ({ id, name: `TEST ONLY location ${id}`, business_units: [1] })),
  };
  return {
    panels: ["inventory", "transfer_history"].map(code => ({ code, label: code })),
    scopes: { inventory: scope, transfer_history: scope },
  };
}
function report(location: string, page = 1) {
  return {
    fields: ["item_code", "quantity", "landed_cost", "supplier_cost"],
    rows: [{ item_code: `TEST-LOCATION-${location || "CURRENT"}`, quantity: "2.1250",
      landed_cost: "PRIVATE_LANDED", supplier_cost: "PRIVATE_COST" }],
    scope: { scope_type: "business_unit", business_unit: { id: 1 } },
    filters: { unit_id: 1, location_id: location || null },
    pagination: { page, page_size: 20, total_count: 21, total_pages: 2,
      previous_page: page > 1 ? page - 1 : null, next_page: page < 2 ? page + 1 : null },
  };
}
async function flush() { await act(async () => { await new Promise(setImmediate); }); }
async function change(selector: string, value: string) {
  const element = host.querySelector<HTMLInputElement | HTMLSelectElement>(selector)!;
  assert.equal(Boolean(element), true);
  const select = element.tagName === "SELECT";
  Object.getOwnPropertyDescriptor(select ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype, "value")!
    .set!.call(element, value);
  await act(async () => element.dispatchEvent(new Event(select ? "change" : "input", { bubbles: true })));
  await flush();
}
async function click(label: string) {
  const button = host.querySelector<HTMLButtonElement>(`[aria-label="${label}"]`)!;
  assert.equal(Boolean(button), true, `Missing ${label}`);
  await act(async () => button.click()); await flush();
}
async function mount(panel: string) {
  await act(async () => root.render(<InventoryRegisters selectedUnitId={1} />)); await flush();
  if (panel !== "inventory") await change('[aria-label="Inventory report panel"]', panel);
  await change('[aria-label="Report calendar start date"]', "2031-01-01");
  await change('[aria-label="Report calendar end date"]', "2031-02-01");
  await change('[aria-label="Report location filter"]', "71");
}
function noOldRows() {
  assert.equal(host.textContent!.includes("TEST-LOCATION-71"), false);
  assert.equal(host.textContent!.includes("PRIVATE_"), false);
}
async function removeLocation() {
  locations = [72];
  await click("Refresh current inventory register");
}
beforeEach(() => {
  locations = [71, 72]; catalogStatus = 200; catalogReads = 0;
  requests = []; deferred = []; delay = false;
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  globalThis.fetch = async (input, options = {}) => {
    assert.equal(options.method ?? "GET", "GET", "Register viewing never writes");
    const url = new URL(String(input), "https://inventory.test.invalid");
    if (url.pathname === "/api/reports/") {
      catalogReads++;
      return json(catalogStatus === 200 ? catalog() : { detail: "TEST ONLY unrelated scope failure" }, catalogStatus);
    }
    assert.match(url.pathname, /^\/api\/reports\/(inventory|transfer_history)\/$/);
    requests.push(url);
    if (delay) return new Promise<Response>(resolve => deferred.push(resolve));
    return json(report(url.searchParams.get("location") || "", Number(url.searchParams.get("page"))));
  };
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); globalThis.fetch = originalFetch;
});
for (const panel of ["inventory", "transfer_history"]) {
  test(`${panel}: removed filter stays visibly unavailable until explicit clear, then loads page one in current scope`, async () => {
    await mount(panel);
    await click("Next report page");
    const start = host.querySelector('[aria-label="Report calendar start date"]');
    const before = requests.length;
    await removeLocation();
    noOldRows();
    assert.equal(requests.length, before, "Scope removal must not issue an all-location request");
    const select = host.querySelector<HTMLSelectElement>('[aria-label="Report location filter"]')!;
    assert.equal(select.value, "71", "Do not silently display All while filter state is still 71");
    assert.match(select.selectedOptions[0].textContent!, /unavailable/i);
    await click("Clear unavailable location filter");
    const request = requests.at(-1)!;
    assert.equal(request.pathname, `/api/reports/${panel}/`);
    assert.equal(request.searchParams.has("location"), false);
    assert.equal(request.searchParams.get("unit"), "1");
    assert.equal(request.searchParams.get("page"), "1");
    assert.equal(request.searchParams.get("start"), "2031-01-01");
    assert.equal(request.searchParams.get("end"), "2031-02-01");
    assert.equal(host.querySelector('[aria-label="Report calendar start date"]') === start, true);
    assert.equal(host.textContent!.includes("TEST-LOCATION-CURRENT"), true);
    noOldRows();
  });
  test(`${panel}: explicit reselect uses only a currently permitted location and retains dates`, async () => {
    await mount(panel); await removeLocation();
    assert.equal(host.querySelector('option[value="71"]')?.hasAttribute("disabled"), true);
    await change('[aria-label="Report location filter"]', "72");
    assert.equal(requests.at(-1)!.searchParams.get("location"), "72");
    assert.equal(requests.at(-1)!.searchParams.get("start"), "2031-01-01");
    assert.equal(host.textContent!.includes("TEST-LOCATION-72"), true);
    noOldRows();
  });
}
for (const status of [200, 403, 503]) test(`late removed-location ${status} cannot replace current recovered register`, async () => {
  await mount("inventory");
  delay = true;
  await click("Next report page"); // Old location request remains in flight.
  locations = [72];
  await act(async () => window.dispatchEvent(new Event("focus"))); await flush();
  noOldRows();
  delay = false;
  await click("Clear unavailable location filter");
  assert.equal(host.textContent!.includes("TEST-LOCATION-CURRENT"), true);
  await act(async () => deferred[0](status === 200
    ? json(report("71", 2)) : json({ detail: "OBSOLETE LOCATION ERROR" }, status)));
  await flush();
  noOldRows();
  assert.equal(host.textContent!.includes("OBSOLETE"), false);
  assert.equal(host.textContent!.includes("TEST-LOCATION-CURRENT"), true);
});
test("unrelated scope failure retains the exact valid filter/date controls and retry does not broaden scope", async () => {
  await mount("inventory"); const inputs = [...host.querySelectorAll("input")];
  const before = requests.length; catalogStatus = 503;
  await click("Refresh current inventory register");
  assert.equal(requests.length, before);
  assert.equal(host.querySelector('select[aria-label="Report location filter"]')?.value, "71");
  assert.equal(host.querySelector('[aria-label="Clear unavailable location filter"]') === null, true);
  assert.equal(host.textContent!.includes("unrelated scope failure"), true);
  assert.equal(host.querySelector("input") === inputs[0], true);
  catalogStatus = 200;
  await click("Refresh current inventory register");
  assert.equal(requests.at(-1)!.searchParams.get("location"), "71");
  assert.equal(requests.at(-1)!.searchParams.get("start"), "2031-01-01");
  assert.equal(host.textContent!.includes("TEST-LOCATION-71"), true);
});
test("clear cannot bypass revoked panel or BU data authority", async () => {
  await mount("inventory"); await removeLocation();
  const before = requests.length;
  globalThis.fetch = async (input) => {
    const url = new URL(String(input), "https://inventory.test.invalid");
    assert.equal(url.pathname, "/api/reports/", "Revoked panel must never issue a data request");
    return json({ panels: [], scopes: {} });
  };
  await click("Refresh current inventory register");
  assert.equal(host.textContent!.includes("panel is not assigned"), true);
  // Clearing a retained invalid filter must not grant a panel or data capability.
  const clear = host.querySelector<HTMLButtonElement>('[aria-label="Clear unavailable location filter"]');
  if (clear) { await act(async () => clear.click()); await flush(); }
  assert.equal(requests.length, before); noOldRows();
});
