import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import StaffRecords from "../src/StaffRecords";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const row = (id: number, extra = {}) => ({
  id, staff_number: `EMP-0000${id}`, full_name: "TEST ONLY Aisha Rahman", arabic_name: "", job_title: "Showroom consultant",
  department: "Sales", grade: "S2", nationality: "Jordanian", manager: { id: 2, name: "TEST ONLY Omar Haddad" }, joined_on: "2024-03-01",
  contract: "limited", contract_label: "Fixed term", contract_end: "2026-12-15", probation_end: null, status: "active",
  status_label: "Working", last_day: null,
  warnings: [{ what: "Fixed-term contract", date: "2026-12-15", days: 44, expired: false }], ...extra,
});
const person = (id: number, extra = {}) => ({
  ...row(id), date_of_birth: "1994-05-02", work_email: "aisha@tivoli.test", mobile: "+974 5555 1234", leaving_reason: "",
  user: null, documents: [{ id: 5, kind: "qid", kind_label: "Qatar ID (QID)", number: "28940012345", issued_by: "MOI", expires_on: "2026-11-21", note: "", added_by: "Admin" }],
  reports: [], history: [{ at: "2026-11-01T08:00:00", by: "Synthetic Admin", change: "added", what: "Showroom consultant, Sales" }],
  created_by: "Synthetic Admin", created_at: "2026-11-01T08:00:00", can_edit: true, ...extra,
});
const overview = () => ({
  unit: { id: 7, code: "TVL", name: "Tivoli" },
  staff: [row(1), row(2, { full_name: "TEST ONLY Omar Haddad", job_title: "Showroom manager", manager: null, contract: "unlimited",
    contract_label: "Open-ended", contract_end: null, warnings: [] })],
  totals: { working: 2, notice: 0, expiring: 2, expired: 1 },
  warnings: [{ employee: { id: 1, name: "TEST ONLY Aisha Rahman" }, what: "Passport N123", date: "2026-10-20", days: -12, expired: true },
    { employee: { id: 1, name: "TEST ONLY Aisha Rahman" }, what: "Qatar ID (QID) 28940012345", date: "2026-11-21", days: 20, expired: false }],
  departments: ["Sales", "Warehouse"], managers: [{ id: 2, name: "TEST ONLY Omar Haddad", job_title: "Showroom manager" }],
  users: [{ id: 30, username: "demo_sales", name: "Demo Sales" }],
  document_kinds: [{ value: "qid", label: "Qatar ID (QID)" }, { value: "passport", label: "Passport" }],
  can_manage: true, policy: "TEST ONLY policy",
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

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/hr/staff/" && init?.method === "POST") return json(person(3, { full_name: "TEST ONLY Hassan Ali", staff_number: "EMP-00003" }), 201);
    if (url.pathname === "/api/hr/staff/") return json(overview());
    if (url.pathname.startsWith("/api/hr/staff/")) return json(person(Number(url.pathname.split("/")[4])));
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the list shows staff and what runs out soon", async () => {
  act(() => root.render(<StaffRecords selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.equal(calls[0].url.searchParams.get("status"), "current");
  assert.match(container.querySelector(".ex-tiles")?.textContent ?? "", /Working2.*Serving notice0.*Ending soon2.*Already expired1/);
  assert.match(container.querySelector(".hr-coming")?.textContent ?? "", /Aisha RahmanPassport N1232026-10-20ended 12 days ago.*Qatar ID \(QID\) 289400123452026-11-21in 20 days/);
  assert.match(container.querySelector(".hr-table")?.textContent ?? "", /Aisha RahmanShowroom consultant · S2EMP-00001SalesOmar Haddad2024-03-01Fixed termto 2026-12-15Working1 to check/);
  await setValue(field("Search"), "2894");
  await flush();
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
  await flush();
  assert.equal(calls.at(-1)!.url.searchParams.get("search"), "2894");
});

test("adding a person sends the record without any pay", async () => {
  act(() => root.render(<StaffRecords selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Add a person")!.click());
  await setValue(field("Full name"), "TEST ONLY Hassan Ali");
  await setValue(field("Job title"), "Driver");
  await setValue(container.querySelector("input[list=hr-departments]") as HTMLInputElement, "Warehouse");
  await setValue(field("Manager"), "2");
  await setValue(field("Nationality"), "Egyptian");
  await setValue(field("Contract"), "limited");
  await setValue(field("Contract ends"), "2027-10-31");
  await act(async () => button("Add the person")!.click());
  await flush();
  const body = JSON.parse(String(posts()[0].init?.body));
  assert.equal("staff_number" in body, false, "The employee code is always given by the system");
  assert.deepEqual([body.full_name, body.department, body.manager_id, body.contract, body.contract_end, body.user_id],
    ["TEST ONLY Hassan Ali", "Warehouse", 2, "limited", "2027-10-31", null]);
  assert.equal(Object.keys(body).some((key) => /salary|pay|allowance/.test(key)), false);
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Hassan Ali is added as EMP-00003/);
  assert.match(container.querySelector(".hr-detail")?.textContent ?? "", /Employee codeEMP-00003/);
});

test("a person's record shows documents and records leaving", async () => {
  act(() => root.render(<StaffRecords selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Aisha Rahman")!.click());
  await flush();
  const detail = container.querySelector(".hr-detail")?.textContent ?? "";
  assert.match(detail, /Qatar ID \(QID\)28940012345MOI2026-11-21/);
  assert.match(detail, /Fixed-term contract ends 2026-12-15 \(in 44 days\)/);
  await act(async () => button("Add or renew a document")!.click());
  await setValue(field("Kind"), "passport");
  await setValue(field("Number"), "N998877");
  await setValue(field("Expires on"), "2031-01-01");
  await act(async () => button("Save the document")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { action: "add_document", kind: "passport", number: "N998877", issued_by: "", expires_on: "2031-01-01", note: "" });
  await act(async () => button("Record leaving")!.click());
  await setValue(field("Reason"), "TEST ONLY resigned");
  const leave = [...container.querySelectorAll("button")].filter((node) => node.textContent === "Record leaving").at(-1)!;
  await act(async () => leave.click());
  await flush();
  const body = JSON.parse(String(posts()[1].init?.body));
  assert.deepEqual([body.action, body.reason], ["leaving", "TEST ONLY resigned"]);
  assert.equal(posts()[1].url.pathname, "/api/hr/staff/1/");
});
