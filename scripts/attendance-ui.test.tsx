import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Attendance from "../src/Attendance";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const DAYS = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"];
const days = DAYS.map((date, index) => ({ date, weekday: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"][index], day: 26 + index }));
const cells = (codes: Record<string, Partial<{ code: string; kind: string; status: string; status_label: string; check_in: string; recorded_by: string }>>) =>
  Object.fromEntries(DAYS.map((date) => [date, { code: "?", kind: "missing", ...(codes[date] ?? {}) }]));
const totals = (extra = {}) => ({ present: 0, late: 0, absent: 0, leave: 0, missing: 0, ...extra });
const month = (extra = {}) => ({
  unit: { id: 7, name: "Tivoli" }, month: "2026-10", month_label: "October 2026", today: "2026-10-29", days,
  people: [
    { id: 1, name: "TEST ONLY Aisha Rahman", staff_number: "EMP-00001", department: "Sales", job_title: "Consultant", can_record: true,
      cells: cells({ "2026-10-26": { code: "L", kind: "late", status: "late", status_label: "Late", check_in: "08:40", recorded_by: "Omar" },
        "2026-10-29": { code: "AL", kind: "leave" }, "2026-10-30": { code: "", kind: "closed" } }), totals: totals({ late: 1, leave: 1, missing: 2 }) },
    { id: 3, name: "TEST ONLY Ravi Kumar", staff_number: "EMP-00003", department: "Warehouse", job_title: "Driver", can_record: true,
      cells: cells({ "2026-10-26": { code: "A", kind: "absent", status: "absent", status_label: "Absent", recorded_by: "Omar" }, "2026-10-30": { code: "", kind: "closed" } }),
      totals: totals({ absent: 1, missing: 3 }) },
  ],
  me_id: null, confirmation: null, locked: false, can_confirm: false, can_reopen: false, is_hr: true, manager_days_back: 7,
  statuses: [{ value: "present", label: "Present" }, { value: "late", label: "Late" }, { value: "absent", label: "Absent" }],
  policy: "TEST ONLY policy", ...extra,
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 5; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
const byLabel = (label: string) => container.querySelector(`[aria-label="${label}"]`) as HTMLInputElement;
async function setValue(input: HTMLInputElement | HTMLSelectElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
    input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}
const posts = () => calls.filter((call) => call.init?.method === "POST");
let current: ReturnType<typeof month>;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  current = month();
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/hr/attendance/" && init?.method === "POST") return json({ day: "2026-10-28", saved: 2, changed: 0 });
    if (url.pathname === "/api/hr/attendance/") return json(current);
    if (init?.method === "POST") return json({ ok: true });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the month grid shows each day, leave and what is missing", async () => {
  act(() => root.render(<Attendance selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.equal(calls[0].url.search, "?unit=7");
  assert.match(container.querySelector(".ex-tiles")?.textContent ?? "", /Days present1.*Late1.*Absent1.*Not recorded yet5/);
  const aisha = container.querySelector('[aria-label="Attendance grid for October 2026"] tbody tr')!;
  assert.deepEqual([...aisha.querySelectorAll(".at-cell")].map((cell) => cell.textContent), ["L", "?", "?", "AL", ""]);
  assert.equal(aisha.querySelector(".at-late")?.getAttribute("title"), "Late 08:40");
  await setValue(container.querySelector('input[type="month"]') as HTMLInputElement, "2026-09");
  await flush();
  assert.equal(calls.at(-1)!.url.search, "?unit=7&month=2026-09");
});

test("recording a day starts everyone as present and sends only who can be recorded", async () => {
  act(() => root.render(<Attendance selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  const day = container.querySelector('[aria-label="Record a day"] input[type="date"]') as HTMLInputElement;
  assert.equal(day.value, "2026-10-29", "Today by default");
  assert.match(container.querySelector('[aria-label="Attendance on 2026-10-29"]')?.textContent ?? "", /Aisha RahmanSalesOn annual leave/);
  await setValue(day, "2026-10-28");
  await setValue(byLabel("Attendance for Ravi Kumar") as unknown as HTMLSelectElement, "late");
  await setValue(byLabel("Arrived Ravi Kumar"), "08:35");
  await setValue(byLabel("Note for Aisha Rahman"), "TEST ONLY client visit");
  await act(async () => byLabel("Late or early approved for Ravi Kumar").click());
  await act(async () => button("Save the day")!.click());
  await flush();
  assert.equal(posts()[0].url.search, "?unit=7");
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { day: "2026-10-28", entries: [
    { employee_id: 1, status: "present", check_in: "", check_out: "", note: "TEST ONLY client visit", excused: false },
    { employee_id: 3, status: "late", check_in: "08:35", check_out: "", note: "", excused: true },
  ] });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /saved for 2 people/);
  await setValue(container.querySelector('[aria-label="Record a day"] input[type="date"]') as HTMLInputElement, "2026-10-26");
  assert.equal((byLabel("Attendance for Ravi Kumar") as unknown as HTMLSelectElement).value, "absent", "An earlier record is shown for correction");
  assert.equal(byLabel("Arrived Ravi Kumar").disabled, true);
});

test("HR confirms a finished month, and a confirmed month can only be reopened with a reason", async () => {
  current = month({ today: "2026-11-02", can_confirm: true });
  act(() => root.render(<Attendance selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Confirm October 2026")!.click());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/hr/attendance/confirm/");
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { month: "2026-10", action: "confirm", reason: "" });
  act(() => root.unmount());
  root = createRoot(container);
  current = month({ today: "2026-11-02", locked: true, can_reopen: true,
    confirmation: { status: "confirmed", confirmed_by: "demo_admin", confirmed_at: "2026-11-02T08:00:00", reopened_by: "", reopen_reason: "" } });
  act(() => root.render(<Attendance selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.textContent ?? "", /Confirmed by demo_admin on 2026-11-02/);
  assert.equal(container.querySelector('[aria-label="Record a day"]'), null, "No recording in a confirmed month");
  await act(async () => button("Reopen October 2026")!.click());
  assert.equal(button("Reopen the month")!.disabled, true);
  await setValue(byLabel("Why it is reopened"), "TEST ONLY late sick note");
  await act(async () => button("Reopen the month")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts().at(-1)!.init?.body)), { month: "2026-10", action: "reopen", reason: "TEST ONLY late sick note" });
});
