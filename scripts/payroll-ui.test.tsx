import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Payroll from "../src/Payroll";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const detail = (extra = {}) => ({
  pay: { basic: "3000.00", housing: "1000.00", transport: "0.00", mobile: "0.00", other: "0.00", total: "4000.00", other_label: "", from: "2020-01" },
  day_rate: "133.33", hour_basic: "12.50", hour_total: "16.67",
  overtime: [{ order_id: 41, label: "Quote #41", customer: "TEST ONLY Al Waab Villas", hours: "2", amount: "31.25" }],
  absent_days: ["2026-09-23"], absences: "133.33", unpaid_days: 0, unpaid: "0.00", lateness: [], lateness_total: "0.00",
  warning_letters: 0, end_of_service: null, capped: false, notes: [], bank_name: "TEST ONLY QNB", iban: "QA57 •••• 9012", ...extra });
const run = (extra = {}) => ({
  id: 9, month: "2026-10", month_label: "October 2026", period_start: "2026-09-21", period_end: "2026-10-21", pay_date: "2026-11-01",
  status: "prepared", status_label: "Waiting for approval", totals: { basic: "9000.00", allowances: "4150.00", overtime: "31.25",
    end_of_service: "0.00", deductions: "171.46", net: "13009.79", people: 2 },
  warnings: ["TEST ONLY Ravi Kumar: 2 working days without attendance between 21 Sep and 21 Oct are paid as present."],
  prepared_by: "Admin", prepared_at: "2026-10-25T09:00:00", approved_by: "", approved_at: "", journal_entry: null,
  lines: [
    { id: 1, employee: { id: 1, name: "TEST ONLY Aisha Rahman", department: "Sales", job_title: "Consultant" }, days_paid: 30,
      basic: "6000.00", allowances: "3150.00", overtime: "0.00", overtime_hours: "0", end_of_service: "0.00", deductions: "38.13", net: "9111.87",
      detail: detail({ overtime: [], absent_days: [], absences: "0.00", pay: { basic: "6000.00", housing: "2500.00", transport: "500.00", mobile: "150.00", other: "0.00", total: "9150.00", other_label: "", from: "2024-03" },
        lateness: [{ day: "2026-09-22", minutes: 30, outcome: "no effect", amount: "0.00" }, { day: "2026-10-01", minutes: 30, outcome: "deducted at double", amount: "38.13" }],
        lateness_total: "38.13", warning_letters: 3 }) },
    { id: 2, employee: { id: 3, name: "TEST ONLY Ravi Kumar", department: "Workshop", job_title: "Fitter" }, days_paid: 30,
      basic: "3000.00", allowances: "1000.00", overtime: "31.25", overtime_hours: "2", end_of_service: "0.00", deductions: "133.33", net: "3897.92", detail: detail() },
  ],
  can_recalculate: true, can_approve: false, can_download: false, ...extra });
const overview = (extra = {}) => ({
  unit: { id: 7, name: "Tivoli" }, today: "2026-11-01", month: "2026-10", month_label: "October 2026",
  period: { start: "2026-09-21", cut_off: "2026-10-21", pay_date: "2026-11-01" }, run: run(), can_prepare: false, cut_off_passed: true,
  recent: [{ month: "2026-10", status_label: "Waiting for approval", net: "13009.79" }],
  wps: null, wps_banks: [{ code: "CBQ", name: "Commercial Bank of Qatar" }, { code: "QNB", name: "Qatar National Bank" }],
  can_manage: true, policy: "TEST ONLY policy", ...extra });

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 5; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
const posts = () => calls.filter((call) => call.init?.method === "POST");
let current: ReturnType<typeof overview>;
let file: Response | null;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  current = overview();
  file = null;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname.endsWith("/wps-file/")) return file ?? json({ detail: "The WPS file cannot be made yet: TEST ONLY Ravi Kumar has no Qatar ID on the staff record." }, 400);
    if (url.pathname === "/api/hr/payroll/" && init?.method === "POST") return json(run(), 201);
    if (url.pathname === "/api/hr/payroll/") return json(current);
    if (init?.method === "POST") return json({ ok: true });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("a prepared payroll shows totals, warnings and each person's workings", async () => {
  act(() => root.render(<Payroll selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.equal(calls[0].url.search, "?unit=7");
  assert.match(container.querySelector(".pr-bar")?.textContent ?? "", /from 21 Sept? 2026 to the cut-off on 21 Oct 2026\. Paid on 1 Nov 2026/);
  assert.match(container.querySelector(".ex-tiles")?.textContent ?? "", /Net to pay13,009\.79QAR to 2 people on 1 Nov 2026.*Overtime31\.25.*Deductions171\.46/);
  assert.match(container.querySelector(".pr-warnings")?.textContent ?? "", /Ravi Kumar: 2 working days without attendance/);
  assert.equal(button("Approve and post"), undefined, "The person who prepared it cannot approve");
  const rows = [...container.querySelectorAll('[aria-label="October 2026 payroll"] tbody tr')];
  assert.match(rows[1].textContent ?? "", /Ravi Kumar.*303,000\.001,000\.0031\.252 h−133\.333,897\.92/);
  await act(async () => [...rows[0].querySelectorAll("button")][0].click());
  const workings = container.querySelector(".pr-detail")?.textContent ?? "";
  assert.match(workings, /basic 6,000\.00, housing 2,500\.00, transport 500\.00, mobile 150\.00 = 9,150\.00 QAR/);
  assert.match(workings, /2\. 1 Oct 2026 30 min, deducted at double 38\.13.* 3 warning letters to issue/);
  await act(async () => button("Work it out again")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { month: "2026-10" });
  assert.match(container.querySelector('[role="status"]')?.textContent ?? "", /13,009\.79 QAR for 2 people/);
});

test("the approver posts it and the WPS file problems are shown", async () => {
  current = overview({ run: run({ can_recalculate: false, can_approve: true }) });
  act(() => root.render(<Payroll selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.equal(button("Discard"), undefined);
  current = overview({ run: run({ status: "posted", status_label: "Approved and posted", approved_by: "Omar", approved_at: "2026-10-30T10:00:00",
    journal_entry: 77, can_recalculate: false, can_download: true }) });
  await act(async () => button("Approve and post")!.click());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/hr/payroll/9/");
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { action: "approve" });
  assert.match(container.querySelector(".pr-status")?.textContent ?? "", /approved by Omar on 30 Oct 2026 and posted as journal #77/);
  await act(async () => button("Download WPS file")!.click());
  await flush();
  assert.match(container.querySelector('[role="alert"]')?.textContent ?? "", /Ravi Kumar has no Qatar ID/);
});

test("nothing prepared yet, and setting the WPS details", async () => {
  current = overview({ run: null, can_prepare: true, recent: [] });
  act(() => root.render(<Payroll selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector(".pr-empty")?.textContent ?? "", /October 2026 payroll has not been prepared yet/);
  assert.match(container.querySelector('[aria-label="WPS details"]')?.textContent ?? "", /not set yet/);
  await act(async () => button("Set them")!.click());
  const scope = container.querySelector('[aria-label="WPS details"]')!;
  const set = async (input: HTMLInputElement | HTMLSelectElement, value: string) => act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
    input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
  await set(scope.querySelector("input")!, "1234567");
  await set(scope.querySelector("select")!, "CBQ");
  await set(scope.querySelectorAll("input")[1] as HTMLInputElement, "QA16CBQA000000000987654321098");
  await act(async () => button("Save")!.click());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/hr/wps-settings/");
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { employer_eid: "1234567", payer_bank: "CBQ", payer_iban: "QA16CBQA000000000987654321098" });
  current = overview({ run: null, can_prepare: true, recent: [] });
  await act(async () => button("Work out October 2026 payroll")!.click());
  await flush();
  assert.equal(posts()[1].url.pathname, "/api/hr/payroll/");
});
