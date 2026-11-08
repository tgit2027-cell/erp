import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Overtime from "../src/Overtime";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const order = { id: 41, label: "Quote #41", customer: "TEST ONLY Al Waab Villa", status: "Confirmed" };
const row = (id: number, extra = {}) => ({
  id, number: `OT-0000${id}`, unit: "Tivoli", order, work_date: "2026-11-05", rest_day: false, rate_percent: 125,
  work: "TEST ONLY install the wardrobe", status: "submitted", status_label: "Waiting for approval",
  people: [{ id: 3, name: "TEST ONLY Ravi Kumar", department: "Workshop", hours: "2" }], total_hours: "2",
  prepared_by: "Omar", prepared_at: "2026-11-01T08:00:00", decided_by: "", decision_note: "", can_decide: true, can_cancel: true, ...extra,
});
const overview = (extra = {}) => ({
  unit: { id: 7, name: "Tivoli" }, month: "2026-11", month_label: "November 2026", today: "2026-11-01",
  rows: [row(1), row(2, { status: "approved", status_label: "Approved", rest_day: true, rate_percent: 150, total_hours: "6", can_decide: false, decided_by: "HR" })],
  by_order: [{ ...order, normal_hours: "0", rest_hours: "6", people: 1 }], orders: [order],
  people: [{ id: 3, name: "TEST ONLY Ravi Kumar", department: "Workshop", job_title: "Upholsterer" }, { id: 4, name: "TEST ONLY Joseph Mendoza", department: "Workshop", job_title: "Carpenter" }],
  can_prepare: true, is_hr: true, max_hours: { working_day: "2", rest_day: "10" }, rates: { working_day: 125, rest_day: 150 },
  policy: "TEST ONLY policy", ...extra,
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 5; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
async function setValue(input: HTMLInputElement | HTMLSelectElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
    input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}
const posts = () => calls.filter((call) => call.init?.method === "POST");
let current: ReturnType<typeof overview>;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  current = overview();
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/hr/overtime/" && init?.method === "POST") return json(row(9, { work_date: "2026-11-06", total_hours: "3.5" }), 201);
    if (url.pathname === "/api/hr/overtime/") return json(current);
    if (init?.method === "POST") return json(row(1, { status: "approved" }));
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("tiles, the month's overtime and hours by sales order", async () => {
  act(() => root.render(<Overtime selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.equal(calls[0].url.search, "?unit=7");
  assert.match(container.querySelector(".ex-tiles")?.textContent ?? "", /Waiting for approval12 hoursApproved hours6.*On rest days6.*Sales orders1/);
  assert.match(container.querySelector('[aria-label="Overtime in November 2026"]')?.textContent ?? "", /2026-11-05OT-00001Quote #41 · Al Waab Villainstall the wardrobeRavi Kumar 2h2\+25%/);
  assert.match(container.querySelector('[aria-label="By sales order"]')?.textContent ?? "", /Quote #41Al Waab Villa61/);
});

test("recording overtime picks people and hours", async () => {
  act(() => root.render(<Overtime selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.equal(button("Send for approval")!.disabled, true);
  await setValue(container.querySelector('[aria-label="Record overtime"] input[type="date"]') as HTMLInputElement, "2026-11-06");
  await setValue([...container.querySelectorAll('[aria-label="Record overtime"] input')].find((node) => (node as HTMLInputElement).placeholder.startsWith("e.g.")) as HTMLInputElement, "TEST ONLY fit the sofa");
  const boxes = container.querySelectorAll('[aria-label="Who works the overtime"] input[type="checkbox"]');
  await act(async () => (boxes[0] as HTMLInputElement).click());
  await act(async () => (boxes[1] as HTMLInputElement).click());
  await setValue(container.querySelector('[aria-label="Hours for Joseph Mendoza"]') as HTMLInputElement, "1.5");
  await act(async () => button("Send for approval")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { sales_order_id: 41, work_date: "2026-11-06", work: "TEST ONLY fit the sofa",
    lines: [{ employee_id: 3, hours: "2" }, { employee_id: 4, hours: "1.5" }] });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /OT-00009: 3\.5 hours on 2026-11-06 for Quote #41 · Al Waab Villa are waiting for approval/);
});

test("approving, rejecting with a reason, and no order to charge", async () => {
  act(() => root.render(<Overtime selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Approve")!.click());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/hr/overtime/1/");
  assert.equal(JSON.parse(String(posts()[0].init?.body)).action, "approve");
  await act(async () => button("Reject")!.click());
  assert.equal(button("Reject the overtime")!.disabled, true);
  await setValue(container.querySelector('[aria-label="Why it is rejected"]') as HTMLInputElement, "TEST ONLY not needed");
  await act(async () => button("Reject the overtime")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[1].init?.body)), { action: "reject", note: "TEST ONLY not needed" });
  act(() => root.unmount());
  root = createRoot(container);
  current = overview({ orders: [], rows: [], by_order: [] });
  act(() => root.render(<Overtime selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.textContent ?? "", /no confirmed sales order to charge overtime to.*No overtime in November 2026/);
});
