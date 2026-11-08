import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import StaffLoans from "../src/StaffLoans";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const loan = (id: number, extra = {}) => ({
  id, kind: "loan", kind_label: "Staff loan", employee: { id: 3, name: "TEST ONLY Ravi Kumar", staff_number: "TVL-0003", department: "Delivery" },
  amount: "3000.00", instalment: "500.00", first_month: "2026-11", first_month_label: "November 2026", reason: "TEST ONLY family need",
  status: "repaying", status_label: "Being repaid", repaid: "500.00", balance: "2500.00", months_left: 5,
  repayments: [{ month: "October 2026", amount: "500.00", from_settlement: false }], requested_by: "Admin", decided_by: "Roche",
  decision_note: "", paid_from: "TEST ONLY QNB current", paid_on: "2026-10-02", paid_by: "Admin", can_decide: false, can_pay: false,
  can_cancel: false, ...extra,
});
const overview = () => ({
  unit: { id: 7, name: "Tivoli" }, today: "2026-11-03", next_month: "2026-11",
  tiles: { owed: "2500.00", repaying: 1, waiting: 1, to_pay: 1 },
  loans: [
    loan(1, { kind: "advance", kind_label: "Salary advance", employee: { id: 4, name: "TEST ONLY Joseph Mendoza", staff_number: "TVL-0004", department: "Workshop" },
      amount: "2000.00", instalment: "2000.00", status: "requested", status_label: "Waiting for approval", repaid: "0.00", balance: "0.00",
      months_left: 0, repayments: [], decided_by: "", paid_from: "", paid_on: "", can_decide: true, can_cancel: true }),
    loan(2, { status: "approved", status_label: "Approved, to pay out", repaid: "0.00", balance: "0.00", repayments: [], paid_from: "", paid_on: "", can_pay: true }),
    loan(5),
  ],
  people: [{ id: 3, name: "TEST ONLY Ravi Kumar", department: "Delivery",
    limits: { eos: "9500.00", month_pay: "2000.00", owed: "2500.00", room: "9000.00", basic: "2000.00" } }],
  accounts: [{ id: 9, name: "TEST ONLY QNB current", kind_label: "Bank account" }],
  can_request: true, policy: "TEST ONLY policy",
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent === text) as HTMLButtonElement | undefined;
const field = (label: string) => [...container.querySelectorAll("label")].find((node) => node.textContent?.trim().startsWith(label))?.querySelector("input, select") as HTMLInputElement | HTMLSelectElement;
function setValue(input: HTMLInputElement | HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
  input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
}
const posts = () => calls.filter((call) => call.init?.method === "POST");

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/hr/loans/" && init?.method === "POST") return json(loan(8, { status: "requested", amount: "4000.00" }), 201);
    if (url.pathname === "/api/hr/loans/") return json(overview());
    if (url.pathname === "/api/hr/loans/2/") return json(loan(2, { amount: "3000.00" }));
    if (url.pathname === "/api/hr/loans/1/") return json(loan(1, { kind_label: "Salary advance", status: "approved" }));
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("tiles, the list and recording a loan for approval", async () => {
  act(() => root.render(<StaffLoans selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector("[aria-label='Loans at a glance']")?.textContent ?? "", /Still owed by staff.*2,500\.00.*Waiting for approval1/);
  const table = container.querySelector("[aria-label='Loans and advances']")?.textContent ?? "";
  assert.match(table, /Ravi Kumar.*Staff loan.*2,500\.00.*5 months left/);
  assert.match(table, /1 repayment, QAR 500\.00/);

  await act(async () => {
    setValue(field("Type"), "loan");
    setValue(field("Employee"), "3");
    setValue(field("Amount"), "4000");
    setValue(field("Monthly instalment"), "500");
    setValue(field("Reason"), "TEST ONLY rent deposit");
  });
  assert.match(container.textContent ?? "", /end of service earned so far plus the coming month's salary\. Ravi Kumar can borrow up to QAR 9,000\.00 now \(end of service 9,500\.00 \+ salary 2,000\.00 − already owed 2,500\.00\)\. At this instalment it takes 8 months/);
  await act(async () => (container.querySelector("form[aria-label='Record a loan or an advance']") as HTMLFormElement).requestSubmit());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)),
    { kind: "loan", employee_id: 3, amount: "4000", instalment: "500", first_month: "2026-11", reason: "TEST ONLY rent deposit" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Staff loan of QAR 4,000\.00 for Ravi Kumar is recorded/);
});

test("approving, and paying out from a chosen account", async () => {
  act(() => root.render(<StaffLoans selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Approve")!.click());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/hr/loans/1/");
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { action: "approve", note: "" });

  await act(async () => button("Pay out")!.click());
  assert.equal(button("Confirm payout")!.disabled, true, "Choose the account first");
  await act(async () => setValue(container.querySelector("select[aria-label='Paid from']") as HTMLSelectElement, "9"));
  await act(async () => button("Confirm payout")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[1].init?.body)), { action: "pay", account_id: 9, date: "2026-11-03" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /QAR 3,000\.00 is paid out to Ravi Kumar from QNB current/);
});
