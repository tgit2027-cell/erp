import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import PayRecords from "../src/PayRecords";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const pay = (extra = {}) => ({ id: 1, effective_from: "2024-03", basic: "6000.00", housing: "2500.00", transport: "500.00", mobile: "150.00",
  other: "0.00", other_label: "", total: "9150.00", bank_name: "TEST ONLY Doha Bank", iban: "QA58 •••• DEFG", reason: "TEST ONLY offer letter",
  set_by: "Admin", set_at: "2024-03-01T08:00:00", ...extra });
const overview = (extra = {}) => ({
  unit: { id: 7, name: "Tivoli" }, month: "2026-11",
  people: [
    { id: 1, name: "TEST ONLY Aisha Rahman", staff_number: "EMP-00001", job_title: "Consultant", department: "Sales", joined_on: "2024-03-10", is_me: false, hours_id: 11,
      pay: pay(), upcoming: pay({ id: 2, effective_from: "2026-12", basic: "6500.00", total: "9650.00", reason: "TEST ONLY yearly raise" }),
      history: [pay({ id: 2, effective_from: "2026-12", total: "9650.00", reason: "TEST ONLY yearly raise" }), pay()] },
    { id: 3, name: "TEST ONLY Ravi Kumar", staff_number: "EMP-00003", job_title: "Driver", department: "Warehouse", joined_on: "2025-06-01", is_me: false, hours_id: null,
      pay: null, upcoming: null, history: [] },
    { id: 4, name: "TEST ONLY Omar Haddad", staff_number: "EMP-00004", job_title: "Manager", department: "Sales", joined_on: "2018-09-01", is_me: true, hours_id: 11,
      pay: pay({ id: 5, basic: "12000.00", total: "15150.00" }), upcoming: null, history: [pay({ id: 5 })] },
  ],
  missing: 1, totals: { basic: "18000.00", housing: "5000.00", transport: "1000.00", mobile: "300.00", other: "0.00", total: "24300.00" },
  working_hours: [
    { id: 11, name: "Back office, Admin and Interiors", starts_at: "08:00", ends_at: "17:30", grace_minutes: 10, people: 2, set_by: "Admin", set_at: "2026-10-01T08:00:00" },
    { id: 12, name: "Workshop and Warehouse", starts_at: "07:00", ends_at: "16:00", grace_minutes: 0, people: 0, set_by: "Admin", set_at: "2026-10-01T08:00:00" }],
  no_hours: 1,
  can_manage: true, can_set_hours: true, policy: "TEST ONLY policy", ...extra,
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 5; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
const field = (scope: Element, label: string) => [...scope.querySelectorAll("label")].find((node) => node.textContent?.startsWith(label))?.querySelector("input") as HTMLInputElement;
async function setValue(input: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
const posts = () => calls.filter((call) => call.init?.method === "POST");
let current: ReturnType<typeof overview> | null;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  current = overview();
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/hr/pay/" && init?.method === "POST") return json(pay({ effective_from: "2025-06", total: "3700.00" }), 201);
    if (url.pathname === "/api/hr/pay/") return current ? json(current) : json({ detail: "Salaries are shown only to people with payroll access in this unit." }, 403);
    if (init?.method === "POST") return json({ ok: true, added: [] });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("totals, each person's pay, a raise waiting and nobody changes their own", async () => {
  act(() => root.render(<PayRecords selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.equal(calls[0].url.search, "?unit=7");
  assert.match(container.querySelector(".ex-tiles")?.textContent ?? "", /Monthly pay24,300QAR for 2 people.*Allowances6,300.*No pay set1/);
  const rows = [...container.querySelectorAll('[aria-label="Pay"] tbody tr')];
  assert.match(rows[0].textContent ?? "", /Aisha Rahman.*Change payHistory6,0002,5005001509,1509,650 from 2026-12Doha BankQA58 •••• DEFG/);
  assert.match(rows[1].textContent ?? "", /Ravi Kumar.*Enter payNo pay entered yet/);
  assert.equal(rows[2].textContent?.includes("Change pay"), false, "Not for yourself");
  assert.match(container.querySelector('[aria-label="Working hours"]')?.textContent ?? "", /Back office, Admin and Interiors 08:00 to 17:30, 10 minutes' grace · 2 people.*Workshop and Warehouse 07:00 to 16:00 · 0 people.*1 person has no working hours yet/);
  await act(async () => button("History")!.click());
  assert.match(container.querySelector(".py-history")?.textContent ?? "", /2026-12 · 9,650 QAR.*yearly raise/);
});

test("entering someone's first pay", async () => {
  act(() => root.render(<PayRecords selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Enter pay")!.click());
  const form = container.querySelector('[aria-label="Change pay for Ravi Kumar"]')!;
  assert.equal(field(form, "From month").value, "2025-06", "First pay starts from the joining month");
  await setValue(field(form, "Basic"), "3200");
  await setValue(field(form, "Housing"), "500");
  await setValue(field(form, "Other allowance"), "0");
  await setValue(field(form, "IBAN"), "QA58DOHB00001234567890ABCDEFG");
  await setValue(field(form, "Bank"), "TEST ONLY Doha Bank");
  await setValue(field(form, "Reason"), "TEST ONLY offer letter");
  assert.match(form.textContent ?? "", /New monthly total: 3,700 QAR/);
  await act(async () => button("Save the pay")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { employee_id: 3, effective_from: "2025-06", basic: "3200", housing: "500",
    transport: "", mobile: "", other: "0", other_label: "", bank_name: "TEST ONLY Doha Bank", iban: "QA58DOHB00001234567890ABCDEFG", reason: "TEST ONLY offer letter" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Ravi Kumar's pay is 3,700 QAR a month from 2025-06/);
});

test("setting working hours, and someone without payroll access", async () => {
  act(() => root.render(<PayRecords selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Change")!.click());
  const hours = container.querySelector('[aria-label="Working hours"]')!;
  await setValue(field(hours, "Start"), "07:30");
  await setValue(field(hours, "Grace"), "15");
  await act(async () => [...hours.querySelectorAll("button")].find((node) => node.textContent === "Save")!.click());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/hr/working-hours/");
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { id: 11, name: "Back office, Admin and Interiors", starts_at: "07:30", ends_at: "17:30", grace_minutes: 15 });
  const ravi = container.querySelector('select[aria-label="Working hours for Ravi Kumar"]') as HTMLSelectElement;
  assert.equal(ravi.value, "");
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(ravi), "value")?.set?.call(ravi, "12");
    ravi.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[1].init?.body)), { action: "assign", employee_id: 3, hours_id: 12 });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Ravi Kumar works Workshop and Warehouse hours/);
  act(() => root.unmount());
  root = createRoot(container);
  current = overview({ working_hours: [], no_hours: 3 });
  act(() => root.render(<PayRecords selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector('[aria-label="Working hours"]')?.textContent ?? "", /None set yet/);
  assert.equal(container.querySelector('select[aria-label^="Working hours for"]'), null);
  await act(async () => button("Add Tivoli's standard hours")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[2].init?.body)), { action: "standard" });
  act(() => root.unmount());
  root = createRoot(container);
  current = null;
  act(() => root.render(<PayRecords selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector("[role=alert]")?.textContent ?? "", /payroll access/);
});
