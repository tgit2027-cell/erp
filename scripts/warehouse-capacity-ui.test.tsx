import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import WarehouseCapacity from "../src/WarehouseCapacity";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; options: RequestInit }[];
let responseMode: "empty" | "denied" | "view" | "edit" | "admin";
let configured = false;
let assignments: { id: number; user_id: number; username: string; capability: "view_capacity" | "edit_capacity" }[] = [];

const location = { id: 41, code: "TEST-WH-41", name: "TEST ONLY Synthetic Warehouse" };
const initialValues = {
  total_floor_area_m2: "125.250000",
  allocated_storage_floor_area_m2: "80.125000",
  received_goods_volume_m3: "14.750000",
  available_floor_area_m2: "45.125000",
};

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}

function capacityRecord(capability: "view_capacity" | "edit_capacity", isConfigured = true) {
  return {
    location,
    capability,
    configured: isConfigured,
    total_floor_area_m2: isConfigured ? initialValues.total_floor_area_m2 : null,
    allocated_storage_floor_area_m2: isConfigured ? initialValues.allocated_storage_floor_area_m2 : null,
    received_goods_volume_m3: isConfigured ? initialValues.received_goods_volume_m3 : null,
    available_floor_area_m2: isConfigured ? initialValues.available_floor_area_m2 : null,
    updated_at: isConfigured ? "2026-10-18T12:00:00Z" : null,
  };
}

function adminData() {
  return {
    locations: [{
      ...location,
      physical_capacity_configured: configured,
      business_units: [{ id: 1, code: "TVL", name: "TEST ONLY Unit" }],
      assignments,
    }],
    synthetic_users: [{ id: 5, username: "TEST ONLY warehouse_manager" }],
  };
}

async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}

function mount(isSuperuser = false) {
  root = createRoot(container);
  act(() => root.render(<WarehouseCapacity isSuperuser={isSuperuser} csrfToken="TEST-ONLY-CSRF" />));
}

async function changeInput(label: string, value: string) {
  const input = container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`);
  assert.ok(input);
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  assert.ok(setter);
  await act(async () => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
}

async function submit(buttonName: string) {
  const button = Array.from(container.querySelectorAll("button")).find((item) => item.textContent?.includes(buttonName));
  assert.ok(button, `Expected button ${buttonName}`);
  await act(async () => button.click());
  await flush();
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  calls = [];
  responseMode = "empty";
  configured = false;
  assignments = [];
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, options });
    if (url.pathname === "/api/warehouse-capacity/") {
      if (responseMode === "denied") return json({ detail: "TEST ONLY capacity access denied." }, 403);
      if (responseMode === "view") return json({ results: [capacityRecord("view_capacity")] });
      if (responseMode === "edit") return json({ results: [capacityRecord("edit_capacity")] });
      return json({ results: [] });
    }
    if (url.pathname === "/api/governance/warehouse-capacity/" && options.method === "POST") {
      configured = true;
      return json({ location, physical_capacity_configured: true, created: true }, 201);
    }
    if (url.pathname === "/api/governance/warehouse-capacity/" && !options.method) {
      return json(adminData());
    }
    if (url.pathname === "/api/governance/warehouse-capacity/assignments/" && options.method === "POST") {
      assignments = [{
        id: 99,
        user_id: 5,
        username: "TEST ONLY warehouse_manager",
        capability: "edit_capacity",
      }];
      return json({ assignment: assignments[0] }, 201);
    }
    if (url.pathname === "/api/governance/warehouse-capacity/assignments/99/" && options.method === "DELETE") {
      assignments = [];
      return json({ revoked: true, assignment_id: 99 });
    }
    if (url.pathname === `/api/warehouse-capacity/${location.id}/` && options.method === "PUT") {
      return json({ ...capacityRecord("edit_capacity"), configured: true });
    }
    throw new Error(`Unexpected synthetic request ${options.method ?? "GET"} ${url.pathname}`);
  };
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("ordinary stock reader receives only an unassigned state; direct denial reveals no capacity values", async () => {
  responseMode = "empty";
  mount();
  await flush();
  assert.match(container.textContent ?? "", /No warehouse capacity locations are assigned/);
  assert.doesNotMatch(container.textContent ?? "", /125\.250000|80\.125000|14\.750000/);
  assert.equal(container.querySelector("input"), null);
  assert.equal(calls.some(({ url }) => url.pathname === "/api/governance/warehouse-capacity/"), false);

  if (root) await act(async () => root.unmount());
  calls = [];
  responseMode = "denied";
  mount();
  await flush();
  assert.equal(container.querySelector('[role="alert"]')?.textContent, "TEST ONLY capacity access denied.");
  assert.doesNotMatch(container.textContent ?? "", /125\.250000|80\.125000|14\.750000/);
});

test("assigned view-only manager sees separate m²/m³ measures but cannot edit", async () => {
  responseMode = "view";
  mount();
  await flush();
  assert.match(container.textContent ?? "", /TEST ONLY Synthetic Warehouse/);
  assert.match(container.textContent ?? "", /Available floor area/);
  assert.match(container.textContent ?? "", /45\.125000 m²/);
  assert.match(container.textContent ?? "", /14\.750000 m³/);
  assert.equal(container.querySelector("input"), null);
  assert.match(container.textContent ?? "", /m² floor area and m³ received-goods volume are separate measures/);
  assert.match(container.textContent ?? "", /No conversion or volumetric capacity is inferred/);
});

test("editor gets exact decimal preview and successful save feedback", async () => {
  responseMode = "edit";
  mount();
  await flush();
  assert.match(container.textContent ?? "", /45\.125000 m²/);
  await changeInput("Total floor area in m² for TEST ONLY Synthetic Warehouse", "100.000001");
  await changeInput("Allocated storage floor area in m² for TEST ONLY Synthetic Warehouse", "99.000002");
  await changeInput("Received-goods volume in m³ for TEST ONLY Synthetic Warehouse", "0.125");
  assert.match(container.textContent ?? "", /0\.999999 m²/);
  await submit("Save capacity");
  const put = calls.find(({ url, options }) => url.pathname === "/api/warehouse-capacity/41/" && options.method === "PUT");
  assert.ok(put);
  assert.equal(put.options.headers && (put.options.headers as Record<string, string>)["X-CSRFToken"], "TEST-ONLY-CSRF");
  assert.deepEqual(JSON.parse(String(put.options.body)), {
    total_floor_area_m2: "100.000001",
    allocated_storage_floor_area_m2: "99.000002",
    received_goods_volume_m3: "0.125",
  });
  assert.match(container.textContent ?? "", /Capacity measurements saved and audited/);
});

test("system administrator can designate synthetic physical locations and assign/revoke per-location access", async () => {
  responseMode = "empty";
  mount(true);
  await flush();
  assert.match(container.textContent ?? "", /ERP\/system administrator/);
  assert.doesNotMatch(container.textContent ?? "", /125\.250000|80\.125000|14\.750000/);
  const physicalLocation = container.querySelector<HTMLSelectElement>('select[aria-label="Configure physical capacity location"]');
  assert.ok(physicalLocation);
  const setter = Object.getOwnPropertyDescriptor(physicalLocation.constructor.prototype, "value")?.set;
  assert.ok(setter);
  await act(async () => {
    setter.call(physicalLocation, String(location.id));
    physicalLocation.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
  await submit("Configure location");
  assert.ok(calls.some(({ url, options }) => url.pathname === "/api/governance/warehouse-capacity/" && options.method === "POST"));
  await submit("Assign capacity permission");
  assert.ok(calls.some(({ url, options }) => url.pathname === "/api/governance/warehouse-capacity/assignments/" && options.method === "POST"));
  assert.match(container.textContent ?? "", /TEST ONLY warehouse_manager — view and edit/);
  await submit("Revoke");
  assert.ok(calls.some(({ url, options }) => url.pathname === "/api/governance/warehouse-capacity/assignments/99/" && options.method === "DELETE"));
  assert.match(container.textContent ?? "", /No active capacity assignments/);
});

test("successful assignment revocation also refreshes independently assigned measurements outside administration-only mode", async () => {
  responseMode = "edit";
  configured = true;
  assignments = [{ id: 99, user_id: 5, username: "TEST ONLY warehouse_manager", capability: "edit_capacity" }];
  mount(true); await flush();
  assert.equal(container.querySelector(".capacity-form") !== null, true);
  const before = calls.filter(({ url }) => url.pathname === "/api/warehouse-capacity/").length;
  responseMode = "empty"; // Current authoritative measurement list after revocation.
  await submit("Revoke");
  assert.equal(calls.filter(({ url }) => url.pathname === "/api/warehouse-capacity/").length, before + 1);
  assert.equal(container.querySelector(".capacity-form") === null, true);
  assert.equal(container.textContent?.includes("125.250000"), false);
});
