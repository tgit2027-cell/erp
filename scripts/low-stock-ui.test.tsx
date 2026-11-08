import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import LowStock, { LowStockAlert } from "../src/LowStock";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let canManage = true;
let alerts = 2;
let canBuy = false;
let requests: Record<string, unknown>[] = [];
const request = (id: number, sku: string, extra = {}) => ({
  id, status: "sent", status_label: "Waiting for purchasing", sku, item_code: "TVF-001", description: "TEST ONLY lounge chair", variant_description: "linen",
  quantity: "10.00", location: "TEST ONLY Showroom", supplier: "TEST ONLY Roche Bobois", supplier_id: 9, supplier_item_code: "RB-77",
  note: "TEST ONLY for the showroom", requested_by: "Warehouse Lead", requested_at: "2026-11-02T08:00:00Z", decided_by: "", decided_at: "",
  reason: "", order_id: null, matching_order: null, ...extra,
});

const row = (id: number, sku: string, status: "out" | "low" | "ok", extra = {}) => ({
  id, variant_id: id + 100, sku, item_code: "TVF-002", description: "TEST ONLY lounge chair", variant_description: "linen",
  location_id: 5, location: "TEST ONLY Showroom", minimum: "4.00", reorder_quantity: "10.00", on_hand: "3.00", free: "2.00",
  short_by: "2.00", on_order: "0.00", covered_by_order: false, suggested_order: "10.00", requested: "0.00", request_id: null, requested_at: "",
  status, status_label: { out: "Out of stock", low: "Low", ok: "Enough" }[status], note: "", updated_by: "admin", updated_at: "2026-10-30T10:00:00Z",
  ...extra,
});
const overview = () => ({
  unit: { id: 1, code: "TVL", name: "Tivoli" }, can_manage: canManage,
  rows: [
    row(1, "TVF-001", "out", { free: "0.00", on_hand: "0.00", short_by: "4.00" }),
    row(2, "TVF-002", "low", { on_order: "6.00", covered_by_order: true, suggested_order: "0.00" }),
    row(3, "TVF-003", "ok", { free: "9.00", on_hand: "9.00", short_by: "0.00", suggested_order: "0.00" }),
  ],
  counts: { out: 1, low: 1, ok: 1 }, covered: 1, policy_label: "TEST ONLY policy.", can_buy: canBuy, requests,
  ...(canManage ? { locations: [{ id: 5, name: "TEST ONLY Showroom" }], variants: [{ id: 101, sku: "TVF-001", description: "TEST ONLY lounge chair · linen" }, { id: 104, sku: "TVF-004", description: "TEST ONLY side table · oak" }] } : {}),
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent === text) as HTMLButtonElement | undefined;
function choose(select: HTMLSelectElement, value: string) {
  select.value = value;
  select.dispatchEvent(new Event("change", { bubbles: true }));
}
function type(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  canBuy = false;
  requests = [];
  canManage = true;
  alerts = 2;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/inventory/low-stock/" && url.searchParams.get("summary")) return json({ alerts });
    if (url.pathname === "/api/inventory/low-stock/" && init?.method === "POST") return json({ ...row(4, "TVF-004", "low"), location: "All locations", created: true }, 201);
    if (url.pathname === "/api/inventory/low-stock/") return json(overview());
    if (url.pathname === "/api/inventory/reorder-requests/") { requests = [request(7, "TVF-001")]; return json({ ...overview(), sent: 1 }, 201); }
    if (url.pathname === "/api/inventory/reorder-requests/7/") {
      const body = JSON.parse(String(init?.body));
      requests = [request(7, "TVF-001", { status: body.action === "ordered" ? "ordered" : "declined", status_label: body.action === "ordered" ? "Ordered" : "Not ordered",
        decided_by: "Buyer", decided_at: "2026-11-03T08:00:00Z", reason: body.reason, order_id: body.action === "ordered" ? 41 : null })];
      return json(overview());
    }
    if (url.pathname.startsWith("/api/inventory/low-stock/") && init?.method === "DELETE") return json({ removed: 1 });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("only items that need attention are listed, with what is on order", async () => {
  act(() => root.render(<LowStock selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  const rows = container.querySelectorAll("table tbody tr");
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent ?? "", /TVF-001.*Out of stock.*Short by 4/);
  assert.match(rows[1].textContent ?? "", /Low.*An order already covers it/);
  assert.equal(rows[0].querySelectorAll("td")[6].textContent, "10", "Suggested order (after the send tick box)");
  assert.equal(rows[1].querySelectorAll("td")[5].textContent, "6", "On order");
  await act(async () => (container.querySelector(".bc-check input[type=checkbox]") as HTMLInputElement).click());
  assert.equal(container.querySelectorAll("table tbody tr").length, 3);
});

test("a level is set across all locations and an old one removed", async () => {
  act(() => root.render(<LowStock selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Set a level")!.click());
  const form = container.querySelector("form")!;
  const [item] = [...form.querySelectorAll("select")] as HTMLSelectElement[];
  await act(async () => choose(item, "104"));
  const [minimum, order] = [...form.querySelectorAll("input")] as HTMLInputElement[];
  await act(async () => { type(minimum, "3"); type(order, "8"); });
  await act(async () => form.requestSubmit());
  await flush();
  const post = calls.find((call) => call.init?.method === "POST")!;
  assert.deepEqual(JSON.parse(String(post.init?.body)), { variant_id: 104, location_id: null, minimum_quantity: "3", reorder_quantity: "8", note: "" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Level saved for TVF-004 at All locations: low/);
  await act(async () => button("Remove")!.click());
  await flush();
  assert.ok(calls.some((call) => call.init?.method === "DELETE" && call.url.pathname === "/api/inventory/low-stock/1/"));
});

test("people who only read stock cannot change levels; the overview line shows only when something is low", async () => {
  canManage = false;
  act(() => root.render(<LowStock selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  assert.equal(button("Set a level"), undefined);
  assert.equal(button("Change"), undefined);
  let opened = 0;
  act(() => root.render(<LowStockAlert key="a" selectedUnitId={1} onOpen={() => { opened += 1; }} />));
  await flush();
  assert.match(container.textContent ?? "", /2 items are low or out of stock/);
  await act(async () => button("See low stock")!.click());
  assert.equal(opened, 1);
  alerts = 0;
  act(() => root.render(<LowStockAlert key="b" selectedUnitId={1} onOpen={() => undefined} />));
  await flush();
  assert.equal(container.textContent, "");
});

test("the warehouse sends ticked suggestions to purchasing with its own quantity", async () => {
  act(() => root.render(<LowStock selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  assert.equal(container.querySelector("input[aria-label='Send TVF-002 to purchasing']"), null, "Covered by an order: nothing to send");
  await act(async () => (container.querySelector("input[aria-label='Send TVF-001 to purchasing']") as HTMLInputElement).click());
  const quantity = container.querySelector("input[aria-label='Quantity of TVF-001 to order']") as HTMLInputElement;
  assert.equal(quantity.value, "10.00", "Starts from the suggestion");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(quantity, "12");
    quantity.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => button("Send 1 item to purchasing")!.click());
  await flush();
  const sent = calls.find((call) => call.url.pathname === "/api/inventory/reorder-requests/")!;
  assert.deepEqual(JSON.parse(String(sent.init?.body)), { lines: [{ level_id: 1, quantity: "12" }], note: "" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Sent 1 item to purchasing/);
  assert.match(container.querySelector("[aria-label='Waiting for purchasing: Roche Bobois']")?.textContent ?? "", /TVF-001.*Supplier's code RB-77.*10.*Warehouse Lead.*None entered yet/);
  assert.ok(button("Withdraw"), "The warehouse can withdraw it");
  assert.equal(button("Ordered"), undefined, "Only purchasing marks it ordered");
});

test("purchasing marks a request ordered or says why not", async () => {
  canBuy = true;
  canManage = false;
  requests = [request(7, "TVF-001", { matching_order: { id: 41, quantity: "10.00", date: "2026-11-02" } })];
  act(() => root.render(<LowStock selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector(".ls-requests")?.textContent ?? "", /Order 41.*10 ordered on/);
  await act(async () => button("Not ordering")!.click());
  const confirm = button("Confirm")!;
  assert.equal(confirm.disabled, true, "Needs a reason");
  const why = container.querySelector("input[aria-label='Why TVF-001 is not being ordered']") as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(why, "TEST ONLY discontinued");
    why.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => button("Confirm")!.click());
  await flush();
  const decided = calls.find((call) => call.url.pathname === "/api/inventory/reorder-requests/7/")!;
  assert.deepEqual(JSON.parse(String(decided.init?.body)), { action: "declined", reason: "TEST ONLY discontinued" });
  assert.match(container.querySelector(".ls-history")?.textContent ?? "", /TVF-001.*Not ordered.*TEST ONLY discontinued/);
});
