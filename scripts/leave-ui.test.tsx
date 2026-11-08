import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Leave from "../src/Leave";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const balance = (extra = {}) => ({
  year: 2026, eligible_from: "2024-02-14", leave_year: { start: "2026-02-14", end: "2027-02-13" }, entitlement: "30", carried: 10,
  adjusted: "4.5", taken: 6, waiting: 4, left: "34.5", parts_used: 1, parts_allowed: 2,
  sick: { used: 16, bands: [{ label: "full pay", days: 14, used: 14 }, { label: "half pay", days: 28, used: 2 }, { label: "unpaid", days: 42, used: 0 }], beyond: 0, qualifies: true },
  unpaid: 0, ...extra,
});
const request = (id: number, extra = {}) => ({
  id, employee: { id: 1, name: "TEST ONLY Aisha Rahman", staff_number: "EMP-00001", department: "Sales" }, kind: "annual",
  kind_label: "Annual leave", start: "2026-11-09", end: "2026-11-12", days: 4, reason: "TEST ONLY family visit",
  status: "requested", status_label: "Waiting for approval", requested_by: "demo_multi", requested_at: "2026-11-01T08:00:00",
  decided_by: "", decision_note: "", special_approval: null as null | { why: string; manager: string; waiting_for: string },
  can_decide: false, can_cancel: true, ...extra,
});
const overview = (extra = {}) => ({
  unit: { id: 7, name: "Tivoli" }, year: 2026, today: "2026-11-01",
  me: { id: 1, name: "TEST ONLY Aisha Rahman", staff_number: "EMP-00001", job_title: "Consultant", department: "Sales", joined_on: "2023-01-01",
    balance: balance(), requests: [request(1)] },
  to_decide: [request(2, { employee: { id: 3, name: "TEST ONLY Ravi Kumar", staff_number: "EMP-00003", department: "Warehouse" }, kind: "sick", kind_label: "Sick leave", can_decide: true, can_cancel: false })],
  away: [], people: [{ id: 3, name: "TEST ONLY Ravi Kumar", staff_number: "EMP-00003", job_title: "Driver", department: "Warehouse", joined_on: "2026-09-01",
    balance: balance({ leave_year: { start: "2026-09-01", end: "2027-08-31" }, carried: 0, adjusted: "0", taken: 0, waiting: 0, left: "30", parts_used: 0, sick: { used: 0, bands: [], beyond: 0, qualifies: false } }) }],
  can_request_for: [{ id: 1, name: "TEST ONLY Aisha Rahman" }, { id: 3, name: "TEST ONLY Ravi Kumar" }], can_adjust: true,
  kinds: [{ value: "annual", label: "Annual leave" }, { value: "sick", label: "Sick leave" }, { value: "unpaid", label: "Unpaid leave" }],
  policy: "TEST ONLY policy", ...extra,
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 5; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
const field = (label: string) => [...container.querySelectorAll("label")].find((node) => node.textContent?.startsWith(label))?.querySelector("input, select") as HTMLInputElement;
async function setValue(input: HTMLInputElement | HTMLSelectElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
    input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}
const posts = () => calls.filter((call) => call.init?.method === "POST");
let current = overview();
let refuseOnce = false;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  current = overview();
  refuseOnce = false;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/hr/leave/" && init?.method === "POST" && refuseOnce) {
      refuseOnce = false;
      return json({ detail: "TEST ONLY Sara Nasser, at the same level in Sales, is away from 09 Nov to 12 Nov 2026 (approved). Leave at the same level must be a week apart. Otherwise ask for a special approval from the line manager and HR, with the reason." }, 400);
    }
    if (url.pathname === "/api/hr/leave/" && init?.method === "POST") return json(request(9), 201);
    if (url.pathname === "/api/hr/leave/") return json(current);
    if (init?.method === "POST") return json({ ok: true });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("my balance, what waits for me and everyone's balances", async () => {
  act(() => root.render(<Leave selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector(".ex-tiles")?.textContent ?? "", /Annual leave left34\.5of 30 \+ 10 carried over, \+4\.5 from HR, until 2027-02-13Taken64 more waiting for approvalParts used1 of 2.*Sick leave1614 on full pay, 2 on half pay/);
  assert.match(container.querySelector('[aria-label="Waiting for you"]')?.textContent ?? "", /Ravi KumarWarehouseSick leave/);
  assert.match(container.querySelector('[aria-label="Balances"]')?.textContent ?? "", /Ravi KumarDriver · WarehouseChange balance2026-09-01 to 2027-08-31300300\/2under 3 months/);
  await act(async () => button("Approve")!.click());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/hr/leave/2/");
  assert.equal(JSON.parse(String(posts()[0].init?.body)).action, "approve");
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Ravi Kumar's sick leave is approved/);
});

test("asking for leave, for myself or for someone else", async () => {
  act(() => root.render(<Leave selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await setValue(field("First day"), "2026-11-09");
  assert.equal(field("Last day").value, "2026-11-09", "The last day follows the first");
  await setValue(field("Last day"), "2026-11-12");
  await act(async () => button("Send for approval")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { kind: "annual", start: "2026-11-09", end: "2026-11-12", reason: "" });
  await setValue(field("For"), "3");
  await setValue(field("Kind of leave"), "unpaid");
  await setValue(field("First day"), "2026-11-15");
  await act(async () => button("Send for approval")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[1].init?.body)), { kind: "unpaid", start: "2026-11-15", end: "2026-11-15", reason: "", employee_id: 3 });
});

test("HR changes a balance and someone without a staff record sees why", async () => {
  act(() => root.render(<Leave selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Change balance")!.click());
  await setValue(container.querySelector('[aria-label="Days to add for Ravi Kumar"]') as HTMLInputElement, "-2");
  await setValue(container.querySelector('[aria-label="Why"]') as HTMLInputElement, "TEST ONLY correction");
  await act(async () => button("Save")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { employee_id: 3, days: "-2", reason: "TEST ONLY correction" });
  act(() => root.unmount());
  root = createRoot(container);
  globalThis.fetch = (async () => json({ detail: "Your ERP sign-in is not linked to a staff record, so there is no leave to show. Ask HR to link it." }, 403)) as typeof fetch;
  act(() => root.render(<Leave selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector("[role=alert]")?.textContent ?? "", /not linked to a staff record/);
});

test("a colleague at the same level is away: ask for a special approval", async () => {
  current = overview({ to_decide: [request(4, { employee: { id: 5, name: "TEST ONLY Sara Nasser", staff_number: "EMP-00005", department: "Sales" },
    can_decide: true, can_cancel: false, special_approval: { why: "TEST ONLY Aisha Rahman, at the same level in Sales, is away from 09 Nov to 12 Nov 2026 (approved).", manager: "", waiting_for: "line manager" } })] });
  refuseOnce = true;
  act(() => root.render(<Leave selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector('[aria-label="Waiting for you"]')?.textContent ?? "", /Special approval: Aisha Rahman, at the same level in Sales.*Waiting for line manager/);
  assert.equal(container.querySelector(".lv-special"), null, "Only offered when it is needed");
  await setValue(field("First day"), "2026-11-10");
  await act(async () => button("Send for approval")!.click());
  await flush();
  assert.match(container.querySelector("[role=alert]")?.textContent ?? "", /a week apart/);
  const tick = container.querySelector(".lv-special input") as HTMLInputElement;
  await act(async () => tick.click());
  assert.equal(button("Send for approval")!.disabled, true, "A reason is needed");
  await setValue(field("Reason"), "TEST ONLY brother's wedding");
  await act(async () => button("Send for approval")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[1].init?.body)), { kind: "annual", start: "2026-11-10", end: "2026-11-10", reason: "TEST ONLY brother's wedding", special_approval: true });
  await act(async () => button("Approve")!.click());
  await flush();
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /You approved Sara Nasser's annual leave\. It now goes to HR/);
});
