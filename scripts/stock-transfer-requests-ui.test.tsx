import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import StockTransferRequests from "../src/StockTransferRequests";
import type { TransferRequest } from "../src/StockTransferRequests";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; options: RequestInit }[];
let listingStatus = 200;
let permissions = { can_request: false, can_approve: true, can_execute: false };
let results: TransferRequest[] = [];

const showroom = { id: 11, name: "TEST ONLY Showroom" };
const warehouse = { id: 12, name: "TEST ONLY Warehouse" };

function record(overrides: Partial<TransferRequest> = {}): TransferRequest {
  return {
    id: 7,
    status: "submitted",
    status_label: "Waiting for showroom manager",
    source_location: showroom,
    destination_location: warehouse,
    note: "",
    requested_by: "TEST ONLY admin",
    requested_at: "2026-10-21T08:00:00Z",
    decided_by: null,
    rejection_reason: "",
    transfer_date: null,
    received_by: null,
    received_at: null,
    is_mine: false,
    lines: [{ variant_id: 3, sku: "TEST-SKU-1", description: "TEST ONLY chair", quantity: "2.00" }],
    next_steps: ["approve", "reject"],
    ...overrides,
  };
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}

async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}

async function mount() {
  root = createRoot(container);
  act(() => root.render(<StockTransferRequests selectedUnitId={1} csrfToken="TEST-ONLY-CSRF" />));
  await flush();
}

function button(name: string) {
  const found = Array.from(container.querySelectorAll("button")).find((item) => item.textContent?.trim() === name);
  assert.ok(found, `Expected button ${name}`);
  return found;
}

async function click(name: string) {
  await act(async () => button(name).click());
  await flush();
}

async function setValue(element: HTMLInputElement | HTMLSelectElement, value: string) {
  const prototype = element.tagName === "SELECT" ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  assert.ok(setter);
  await act(async () => {
    setter.call(element, value);
    element.dispatchEvent(new window.Event("input", { bubbles: true }));
    element.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  await flush();
}

function field<T extends Element>(label: string, selector: string) {
  const found = Array.from(container.querySelectorAll("label")).find((item) => item.textContent?.startsWith(label));
  const control = found?.querySelector<T>(selector) ?? null;
  assert.ok(control, `Expected field ${label}`);
  return control;
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  calls = [];
  listingStatus = 200;
  permissions = { can_request: false, can_approve: true, can_execute: false };
  results = [record()];
  globalThis.fetch = (async (input: RequestInfo | URL, options: RequestInit = {}) => {
    const url = new URL(String(input), "https://synthetic.invalid");
    calls.push({ url, options });
    if (url.pathname === "/api/transfer-requests/" && !options.method) {
      if (listingStatus !== 200) return json({ detail: "No permission." }, listingStatus);
      return json({ permissions, locations: [showroom, warehouse], results });
    }
    if (url.pathname === "/api/transfer-requests/stock/") {
      return json({ location: showroom, items: [{ variant_id: 3, sku: "TEST-SKU-1", description: "TEST ONLY chair", item_code: "T1", free_quantity: "5.00" }] });
    }
    if (url.pathname === "/api/transfer-requests/7/reject/") {
      results = [record({ status: "rejected", status_label: "Rejected", decided_by: "TEST ONLY manager", rejection_reason: "TEST ONLY no space", next_steps: [] })];
      return json({ request_id: 7, status: "rejected" });
    }
    if (url.pathname === "/api/transfer-requests/" && options.method === "POST") return json({ request_id: 8, status: "submitted" }, 201);
    return json({ detail: "Unexpected request." }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("someone without a transfer permission sees a plain explanation", async () => {
  listingStatus = 403;
  await mount();
  assert.match(container.textContent ?? "", /no stock transfer permission/);
  assert.equal(container.querySelector("form"), null);
});

test("a manager rejects with a reason and the requester-facing reason appears", async () => {
  await mount();
  assert.match(container.textContent ?? "", /Waiting for you \(1\)/);
  await click("Reject");
  assert.equal(button("Send rejection").disabled, true);
  await setValue(field<HTMLInputElement>("Reason for the requester", "input"), "TEST ONLY no space");
  await click("Send rejection");
  const posted = calls.find((call) => call.url.pathname === "/api/transfer-requests/7/reject/");
  assert.ok(posted);
  assert.equal(posted.options.method, "POST");
  const headers = posted.options.headers as Record<string, string>;
  assert.equal(headers["X-CSRFToken"], "TEST-ONLY-CSRF");
  assert.ok(headers["Idempotency-Key"]);
  assert.deepEqual(JSON.parse(String(posted.options.body)), { reason: "TEST ONLY no space" });
  assert.match(container.textContent ?? "", /Rejected by TEST ONLY manager: TEST ONLY no space/);
});

test("a showroom admin picks items at the source and sends a request", async () => {
  permissions = { can_request: true, can_approve: false, can_execute: false };
  results = [];
  await mount();
  assert.equal(button("Send request").disabled, true);
  await setValue(field<HTMLSelectElement>("From", "select"), "11");
  assert.ok(calls.some((call) => call.url.pathname === "/api/transfer-requests/stock/" && call.url.searchParams.get("location") === "11"));
  await setValue(field<HTMLSelectElement>("To", "select"), "12");
  await setValue(field<HTMLSelectElement>("Item", "select"), "3");
  await setValue(field<HTMLInputElement>("Quantity", "input"), "2");
  await click("Send request");
  const posted = calls.find((call) => call.url.pathname === "/api/transfer-requests/" && call.options.method === "POST");
  assert.ok(posted);
  assert.deepEqual(JSON.parse(String(posted.options.body)), {
    source_location_id: 11,
    destination_location_id: 12,
    lines: [{ variant_id: 3, quantity: "2" }],
    note: "",
  });
  assert.match(container.textContent ?? "", /Request sent to the showroom manager/);
});

test("in-transit requests say the goods cannot be sold and offer arrival confirmation", async () => {
  permissions = { can_request: false, can_approve: false, can_execute: true };
  results = [record({ status: "in_transit", status_label: "In transit", transfer_date: "2026-10-22", next_steps: ["receive"] })];
  await mount();
  assert.match(container.textContent ?? "", /cannot be sold from either location/);
  assert.ok(button("Confirm arrival"));
});
