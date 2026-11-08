import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Incentives from "../src/Incentives";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const choices = {
  plans: { sales_commission: "Sales commission", performance_reward: "Performance reward" },
  channels: { retail: "Retail", "": "No sales channel" }, ratings: { meets: "Meets expectations" },
  categories: { hub_admin: "Hub and administration" }, operational: { collections: "Collections and cash flow" },
};
const aisha = { entitlement: 11, employee: 4, name: "TEST ONLY Aisha", staff_number: "E-004", unit: "Tivoli", plan: "sales_commission",
  plan_label: "Sales commission", months: "Jan to Dec", kind: "commission", amount: "6786.00", pending: [],
  steps: ["Progressive commission 9,425.00"] };
const layla = { ...aisha, entitlement: 12, employee: 5, name: "TEST ONLY Layla", plan: "performance_reward", plan_label: "Performance reward",
  kind: "reward", amount: "", pending: ["The individual performance rating is missing."], steps: [] };
const run = (status: string) => ({
  id: 3, number: 1, status, status_label: status === "prepared" ? "Waiting to be checked" : "Approved, waiting for release", policy_version: 1,
  lines: [aisha, layla], commission_total: "6786.00", reward_total: "0.00", pending_count: 1, prepared_by: "TEST ONLY Admin",
  prepared_at: "2026-01-10T09:00:00+03:00", checked_by: status === "prepared" ? "" : "Hana", approved_by: "", released_by: "",
  instalments: [], can_redo: false, can_check: status === "prepared", can_approve: false, can_release: status === "approved",
});
const full = (status: string | null) => ({
  entity: "TEST ONLY Tivoli Group", year: 2025, policy_label: "TEST ONLY: provisional", full: true, exists: true, can_prepare: true,
  can_check: false, can_approve: false, year_over: true, choices, years: [2025],
  mine: { plans: [], lines: [], instalments: [] },
  policy: { label: "x", reward_bands: { PR3: "3000" }, max_multiplier: "150", instalments: [{ month: 4, percent: "40" }, { month: 8, percent: "30" }, { month: 12, percent: "30" }] },
  policy_version: 1, policy_status: "provisional", policy_status_label: "Provisional (Finance-test)", policy_approved_by: "",
  reward_payment_month: "", frozen: false,
  company: { pending: ["Company EBITDA: the actual figure is missing."], steps: [], revenue_target: "1200000.00", revenue_actual: "1260000.00",
    ebitda_target: "300000.00", ebitda_actual: "", operational: {}, cpi: "", company_factor: "", negative_ebitda: false },
  units: [{ id: 1, unit_id: 7, unit: "Tivoli", counts_in_company: true, gp_margin: "40.5", minimum_margin: "35", validated: true,
    validated_by: "TEST ONLY Admin", unit_factor: "101.94", pending: [], steps: [],
    figures: { revenue_target: "1200000.00", revenue_actual: "1260000.00", gp_target: "480000.00", gp_actual: "510000.00",
      collections_target: "1000000.00", collections_actual: "900000.00", operational: "100", cost_target: "", cost_actual: "" } }],
  teams: [], units_available: [], staff: [{ id: 4, name: "TEST ONLY Aisha", staff_number: "E-004", grade: "G05", unit_id: 7 }],
  entitlements: [{ id: 11, employee: 4, name: "TEST ONLY Aisha", staff_number: "E-004", grade: "G05", unit_id: 7, unit: "Tivoli",
    plan: "sales_commission", plan_label: "Sales commission", channel: "retail", reward_band: "", category: "", first_month: 1, last_month: 12,
    revenue_target: "1000000.00", gp_target: "300000.00", revenue_actual: "1250000.00", gp_actual: "288000.00", erp_revenue: "1249000.00",
    collection_fraction: "0.8", rating: "", team: null, team_share: "", note: "", status: "proposed", status_label: "Proposed",
    proposed_by: "TEST ONLY Admin", approved_by: "" }],
  preview: { lines: [aisha, layla], pending: 1, commission: "6786.00", reward: "0.00" },
  runs: status ? [run(status)] : [], history: [],
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent === text) as HTMLButtonElement;
const posts = () => calls.filter((call) => call.init?.method === "POST").map((call) => JSON.parse(String(call.init?.body)));
let next: unknown = full(null);

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: new URL(String(url), "http://localhost"), init });
    return json(next);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("a year not set up yet can be set up by Finance", async () => {
  next = { ...full(null), exists: false };
  act(() => root.render(<Incentives selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.equal(calls[0].url.pathname, "/api/hr/incentives/");
  assert.ok(calls[0].url.searchParams.get("year"));
  next = full(null);
  await act(async () => { button("Set up 2025").click(); });
  await flush();
  assert.equal(posts()[0].action, "open_year");
  assert.match(container.textContent ?? "", /Company Performance Index/);
});

test("company figures show what is missing, and people's plans show amounts or pending reasons", async () => {
  next = full(null);
  act(() => root.render(<Incentives selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  const text = container.textContent ?? "";
  assert.match(text, /Still missing, so results stay pending/);
  assert.match(text, /Company EBITDA: the actual figure is missing/);
  assert.match(text, /101\.94%/);
  assert.match(text, /Validated by Admin/);
  await act(async () => { button("People and plans").click(); });
  const people = container.textContent ?? "";
  assert.match(people, /Aisha/);
  assert.doesNotMatch(people, /TEST ONLY Aisha/);
  assert.match(people, /6,786\.00/);
  assert.match(people, /Proposed by Admin/);
  await act(async () => { button("Edit").click(); });
  const revenue = container.querySelector('input[aria-label="Validated revenue (QAR)"]') as HTMLInputElement;
  assert.equal(revenue.placeholder, "ERP: 1,249,000.00");
  await act(async () => { button("Save plan").click(); });
  await flush();
  assert.deepEqual([posts()[0].action, posts()[0].id, posts()[0].channel], ["plan", 11, "retail"]);
});

test("a run is checked and released from the runs tab", async () => {
  next = full("prepared");
  act(() => root.render(<Incentives selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => { button("Runs and payments").click(); });
  assert.match(container.textContent ?? "", /The individual performance rating is missing/);
  next = full("approved");
  await act(async () => { button("Mark as checked").click(); });
  await flush();
  assert.deepEqual([posts()[0].action, posts()[0].run_id], ["check", 3]);
  await act(async () => { button("Release to payroll").click(); });
  await flush();
  assert.equal(posts()[1].action, "release");
});

test("someone without incentive access sees only their own statement", async () => {
  next = { entity: "Tivoli", year: 2025, policy_label: "", full: false, exists: true, can_prepare: false, can_check: false,
    can_approve: false, year_over: true, choices, years: [2025],
    mine: { plans: [{ plan_label: "Sales commission", months: "1–12", status_label: "Approved", revenue_target: "1000000.00", revenue_actual: "1250000.00" }],
      lines: [aisha], instalments: [{ kind: "Commission", month: "2026-04", amount: "2714.40", paid: true }] } };
  act(() => root.render(<Incentives selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  const text = container.textContent ?? "";
  assert.match(text, /My commission and rewards/);
  assert.match(text, /2,714\.40/);
  assert.match(text, /April 2026/);
  assert.equal(button("People and plans"), undefined);
});
