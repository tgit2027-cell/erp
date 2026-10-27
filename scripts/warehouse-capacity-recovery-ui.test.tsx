import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import WarehouseCapacity from "../src/WarehouseCapacity";

const nativeFetch = globalThis.fetch;
let host, root, gets, puts, writes, holdReads;
const a = { id: 41, code: "TEST-A", name: "TEST ONLY Warehouse A" };
const b = { id: 42, code: "TEST-B", name: "TEST ONLY Warehouse B" };
const reading = (location = a, capability = "edit_capacity", total = "125.250000") => ({
  location, capability, configured: true, total_floor_area_m2: total,
  allocated_storage_floor_area_m2: "80.125000", received_goods_volume_m3: "14.750000",
  available_floor_area_m2: "45.125000", updated_at: "2026-10-20T10:00:00Z",
});
const rows = () => ({ results: [reading(), reading(b, "edit_capacity", "222.500000")] });
const flush = async () => act(async () => { await new Promise(setImmediate); });
const card = (id = 41) => [...host.querySelectorAll("article")].find(n =>
  n.getAttribute("aria-label") === `Capacity for TEST ONLY Warehouse ${id === 41 ? "A" : "B"}`);
const form = (id = 41) => card(id)?.querySelector("form");
const amount = (id = 41) => form(id)?.querySelector("input");
async function render(administrationOnly = false) {
  await act(async () => root.render(<WarehouseCapacity isSuperuser={false}
    csrfToken="TEST-ONLY-CSRF" administrationOnly={administrationOnly} />));
  await flush();
}
async function edit(id = 41, value = "150.123456") {
  const input = amount(id);
  assert.ok(input);
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function submit(id = 41) {
  assert.equal(form(id)?.querySelector("button").disabled, false);
  await act(async () => form(id).dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();
}
async function settle(entry, body, status = 200) {
  assert.ok(entry, "Expected deferred synthetic HTTP");
  await act(async () => entry.resolve(new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  })));
  await flush();
}
async function fail(entry) {
  await act(async () => entry.reject(new Error("TEST ONLY connection failure")));
  await flush();
}
async function denyThenRecheck() {
  await edit();
  await submit();
  holdReads = true;
  await settle(puts[0], { detail: "TEST ONLY edit denied" }, 403);
  assert.equal(gets.length, 2, "Recheck viewing separately from a denied write");
  return gets[1];
}
async function overlap() {
  await edit();
  await edit(42, "250.000001");
  await submit();
  await submit(42);
  holdReads = true;
  await settle(puts[0], reading());
  await settle(puts[1], reading(b));
  assert.equal(gets.length, 3);
  assert.equal(puts.length, 2);
  return [gets[1], gets[2]];
}
beforeEach(async () => {
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host); gets = []; puts = []; writes = []; holdReads = false;
  globalThis.fetch = (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    if (options.method === "PUT") {
      writes.push({ path: url.pathname, body: options.body, headers: options.headers });
      return new Promise((resolve, reject) => puts.push({ resolve, reject }));
    }
    assert.equal(url.pathname, "/api/warehouse-capacity/", "Only independently authorized measurement GET");
    return new Promise((resolve, reject) => {
      gets.push({ resolve, reject });
      if (!holdReads) resolve(new Response(JSON.stringify(rows()), { status: 200 }));
    });
  };
  await render();
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); globalThis.fetch = nativeFetch;
});

test("edit-only denial refreshes capability, retains allowed measurements and another location draft", async () => {
  await edit(42, "260.123456");
  const other = form(42);
  await settle(await denyThenRecheck(), { results: [reading(a, "view_capacity"), reading(b, "edit_capacity", "222.500000")] });
  assert.equal(form() === undefined || form() === null, true);
  assert.equal(card().textContent.includes("125.250000 m²"), true, "Show authorized stored reading, not unsaved draft as fact");
  assert.equal(form(42) === other, true);
  assert.equal(amount(42).value, "260.123456");
  assert.equal(puts.length, 1);
});

test("confirmed view loss removes only its protected measurements and dependent controls", async () => {
  await settle(await denyThenRecheck(), { results: [reading(b, "edit_capacity", "222.500000")] });
  assert.equal(card() === undefined, true);
  assert.equal(host.textContent.includes("125.250000"), false);
  assert.equal(host.querySelector('input[value="150.123456"]') === null, true);
  assert.equal(card(42) !== undefined, true);
  assert.equal(puts.length, 1);
});

for (const status of [403, 404]) test(`confirmed current capacity read ${status} removes protected cards`, async () => {
  await settle(await denyThenRecheck(), { detail: "TEST ONLY view denied" }, status);
  assert.equal(host.querySelector("article") === null, true);
  assert.equal(host.textContent.includes("TEST ONLY view denied"), true);
});

test("unrelated 400 validation denial retains exact form and draft without inferring read loss", async () => {
  const before = form(); await edit(); await submit();
  await settle(puts[0], { allocated_storage_floor_area_m2: ["TEST ONLY invalid allocation"] }, 400);
  assert.equal(form() === before, true); assert.equal(amount().value, "150.123456");
  assert.equal(gets.length, 1); assert.equal(puts.length, 1);
});

for (const kind of ["network", "503"]) test(`transient capacity write ${kind} preserves authorized draft`, async () => {
  const before = form(); await edit(); await submit();
  if (kind === "network") await fail(puts[0]); else await settle(puts[0], { detail: "TEST ONLY temporarily unavailable" }, 503);
  assert.equal(form() === before, true); assert.equal(amount().value, "150.123456");
  assert.equal(form().querySelector("button").disabled, false);
  assert.equal(gets.length, 1); assert.equal(puts.length, 1);
});

for (const kind of ["network", "503"]) test(`denied edit with transient ${kind} read verification keeps draft and offers safe read retry`, async () => {
  const before = form();
  const recheck = await denyThenRecheck();
  if (kind === "network") await fail(recheck); else await settle(recheck, { detail: "TEST ONLY read temporarily unavailable" }, 503);
  assert.equal(form() === before, true); assert.equal(amount().value, "150.123456");
  assert.equal(form().querySelector("button").disabled, true, "Do not reuse denied edit authority until verified");
  assert.equal(host.textContent.includes("Previously loaded"), true);
  const retry = [...host.querySelectorAll("button")].find(n => n.textContent === "Refresh capacity access");
  assert.ok(retry);
  await act(async () => retry.click()); await flush();
  await settle(gets.at(-1), rows());
  assert.equal(form() === before, true); assert.equal(amount().value, "150.123456");
  assert.equal(form().querySelector("button").disabled, false);
  assert.equal(host.textContent.includes("Previously loaded"), false);
  assert.equal(puts.length, 1, "Retry only reads, never repeats the denied write");
});

test("another location save and authorized refresh do not unmount or reset unsaved draft", async () => {
  await edit(); const before = form();
  await submit(42); holdReads = true; await settle(puts[0], reading(b));
  assert.equal(form() === before, true, "Keep current cards while rechecking");
  await settle(gets[1], rows());
  assert.equal(form() === before, true); assert.equal(amount().value, "150.123456");
});

for (const when of ["during write", "during refresh"]) test(`saved snapshot cannot erase newer draft ${when}`, async () => {
  await edit(); await submit(); holdReads = true;
  if (when === "during write") await edit(41, "175.654321");
  await settle(puts[0], reading(a, "edit_capacity", "150.123456"));
  if (when === "during refresh") await edit(41, "175.654321");
  await settle(gets[1], { results: [reading(a, "edit_capacity", "150.123456")] });
  assert.equal(amount().value, "175.654321", "A committed snapshot is not the newer unsaved edit");
  assert.equal(puts.length, 1);
  assert.equal(JSON.parse(writes[0].body).total_floor_area_m2, "150.123456");
});

for (const result of ["empty", "denied", "allowed"]) test(`older allowed capacity response cannot overwrite newer ${result}`, async () => {
  const [older, newer] = await overlap();
  await settle(newer, result === "empty" ? { results: [] } : result === "denied"
    ? { detail: "TEST ONLY current denied" } : { results: [reading(b, "view_capacity", "333.000000")] }, result === "denied" ? 403 : 200);
  const current = host.textContent;
  await settle(older, { results: [reading(a, "edit_capacity", "999.000000")] });
  assert.equal(host.textContent, current);
  assert.equal(host.textContent.includes("999.000000"), false);
  assert.equal(puts.length, 2);
});

test("older failed refresh cannot clear a newer pending indicator or replace its outcome", async () => {
  const [older, newer] = await overlap();
  await fail(older);
  assert.equal(host.textContent.includes("Loading assigned warehouse capacities"), true);
  assert.equal(host.textContent.includes("TEST ONLY connection failure"), false);
  await settle(newer, { results: [] });
  assert.equal(host.textContent.includes("No warehouse capacity locations are assigned"), true);
});

test("administration context and return to measurements suppress obsolete responses and cached cards", async () => {
  await submit(); holdReads = true; await settle(puts[0], reading());
  const old = gets[1];
  await render(true);
  assert.equal(host.querySelector("article") === null, true);
  await render(false);
  const fresh = gets.at(-1);
  assert.notEqual(fresh, old);
  assert.equal(host.querySelector("article") === null, true, "Returning context must be freshly authorized");
  await settle(fresh, { results: [reading(b, "view_capacity", "333.000000")] });
  await settle(old, rows());
  assert.equal(card() === undefined, true); assert.equal(card(42) !== undefined, true);
});

test("removed location's late save acknowledgement cannot refresh or replace the current list", async () => {
  await submit(); await submit(42);
  holdReads = true; await settle(puts[1], reading(b));
  await settle(gets[1], { results: [reading(b, "view_capacity", "333.000000")] });
  assert.equal(card() === undefined, true);
  const count = gets.length;
  await settle(puts[0], reading());
  assert.equal(gets.length, count, "Do not run a removed card's stale completion callback");
  assert.equal(card() === undefined, true);
  assert.equal(puts.length, 2);
});
