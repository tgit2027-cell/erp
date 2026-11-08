import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import EndOfService from "../src/EndOfService";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let prepared = false;

const figures = (extra = {}) => ({
  last_day: "2026-09-15", service_days: 1339, unpaid_days: 0, years: "3.67", basic: "3000.00", gratuity: "7703.84", already_paid: "0.00",
  leave_days: "35", leave_pay: "3500.00", additions: [], deductions: [], total: "11203.84", day_rate: "100.00",
  leave: "30 days from 16 Jan 2026, 15 carried over, 10 taken", joined_on: "2023-01-16", notes: [], ...extra,
});
const settlement = () => ({
  id: 4, status: "prepared", status_label: "Waiting for approval", pay_month: "2026-09", pay_month_label: "September 2026", paid_in: "",
  figures: figures({ additions: [{ label: "TEST ONLY air ticket", amount: "1800.00" }], deductions: [{ label: "laptop not returned", amount: "500.00" }], total: "12503.84" }),
  note: "", prepared_by: "Admin", prepared_at: "2026-10-01T10:00:00", approved_by: "", approved_at: "",
  can_approve: true, can_change: true, can_cancel: true,
});
const overview = () => ({
  unit: { id: 7, name: "Tivoli" }, can_prepare: true, policy: "TEST ONLY policy",
  people: [{ id: 3, name: "TEST ONLY Joseph Mendoza", staff_number: "TVL-0003", job_title: "Upholsterer", department: "Workshop",
    status_label: "Left", last_day: "2026-09-15", leaving_reason: "TEST ONLY resigned", problem: "",
    settlement: prepared ? settlement() : null, preview: prepared ? null : figures(), pay_month_label: "September 2026" }],
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent === text) as HTMLButtonElement | undefined;
function type(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  prepared = false;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/hr/end-of-service/" && init?.method === "POST") { prepared = true; return json(settlement(), 201); }
    if (url.pathname === "/api/hr/end-of-service/") return json(overview());
    if (url.pathname === "/api/hr/end-of-service/4/") return json({ ...settlement(), status: "approved", payroll_to_redo: true });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("a first look at what is owed, then preparing it with an extra amount and a deduction", async () => {
  await act(async () => root.render(<EndOfService selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  const text = container.textContent ?? "";
  assert.match(text, /End of service3\.67 years from 16 Jan 2023 × basic 3,000\.00 × 21 ÷ 307,703\.84/);
  assert.match(text, /Unused annual leave30 days from 16 Jan 2026, 15 carried over, 10 taken: 35 days × 100\.003,500\.00/);
  assert.match(text, /Total to pay11,203\.84 QAR/);
  assert.match(text, /Not prepared yet/);

  await act(async () => button("Prepare settlement")?.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>(".eos-items .fa-link")][0].click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>(".eos-items .fa-link")].at(-1)!.click());
  const inputs = [...container.querySelectorAll<HTMLInputElement>(".eos-item input")];
  await act(async () => { type(inputs[0], "TEST ONLY air ticket"); type(inputs[1], "1800"); type(inputs[2], "laptop not returned"); type(inputs[3], "500"); });
  await act(async () => container.querySelector<HTMLFormElement>(".eos-form")!.requestSubmit());
  await flush();
  const sent = JSON.parse(String(calls.find((call) => call.init?.method === "POST")?.init?.body));
  assert.deepEqual(sent, { employee_id: 3, additions: [{ label: "TEST ONLY air ticket", amount: "1800" }],
    deductions: [{ label: "laptop not returned", amount: "500" }], note: "" });
  assert.match(container.textContent ?? "", /settlement of 12,503\.84 QAR is ready/);
  assert.match(container.textContent ?? "", /Less laptop not returned−500\.00/);
});

test("approving tells HR to work the payroll out again when it is already prepared", async () => {
  prepared = true;
  await act(async () => root.render(<EndOfService selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.textContent ?? "", /Waiting for approval/);
  await act(async () => button("Approve")?.click());
  await flush();
  assert.equal(JSON.parse(String(calls.at(-2)?.init?.body)).action, "approve");
  assert.match(container.textContent ?? "", /paid in the September 2026 payroll\. That payroll is already worked out, so work it out again/);
});
