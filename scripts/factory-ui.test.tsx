import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Factory from "../src/Factory";
import { sidebarSections } from "../src/sidebar-destinations";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let live = false;

const job = (id: number, extra = {}) => ({
  id, number: `JOB-000${id}`, status: "in_progress", status_label: "In production", purpose: "fitout", purpose_label: "Fit-out project",
  quote: { id: 41, customer: "TEST ONLY Al Noor villa" }, project_name: "TEST ONLY Villa 12", design_reference: "DP-12 rev B", product: null,
  due_date: "2026-11-20", late: false, destination: "", destination_label: "", finish_location: null, note: "", decision_note: "",
  boq: [{ code: "JD-00", description: "TEST ONLY oak door", quantity: "2.00", unit: "piece" }],
  planned_hours: { joinery: "10.00", paint: "0.00", cnc: "0.00", metal: "0.00" },
  materials: [{ variant_id: 5, sku: "MDF-18", name: "TEST ONLY MDF board", quantity: "6.00", unit_cost: "50.00", taken: "2.00" }],
  estimate: { materials: "300.00", labour: "350.00", overhead: "97.50", total: "747.50", limit: "822.25", overhead_percent: "15.00", overrun_percent: "10.00" },
  actual: { materials: "100.00", purchases: "0.00", labour: "0.00", overhead: "15.00", total: "115.00" },
  issues: [], labour: [], material_requests: [], short_materials: [], purchases: [{
    id: 3, description: "TEST ONLY brass handles", quantity: "4.00", estimated_cost: "120.00", supplier_name: "", reason: "Client finish",
    status: "requested", status_label: "Waiting for the Factory Manager", requested_by: "runner", decided_by: "", decision_note: "",
    bought_by: "", bought_on: "", actual_cost: "", paid_from: "", receipt_reference: "", can_decide: true, can_buy: false, can_cancel: true,
  }],
  created_by: "runner", approved_by: "manager", finished_by: "", cost_approved_by: "", received_by: "", received_on: "",
  can_edit: false, can_submit: false, can_approve: false, can_work: true, can_finish: true, can_approve_cost: false,
  can_send: false, can_receive: false, can_cancel: false, ...extra,
});
const overview = () => ({
  unit: { id: 7, name: "Tivoli" }, today: "2026-11-05",
  settings: { is_live: live, overhead_percent: "15.00", overrun_percent: "10.00", overtime_percent: "125.00",
    rates: { joinery: "35.00", paint: "30.00", cnc: "45.00", metal: "35.00" }, asset_threshold: "2000.00", machine_life_months: 60, updated_by: "", updated_at: "" },
  workshops: [{ id: "joinery", label: "Joinery" }, { id: "paint", label: "Paint" }, { id: "cnc", label: "CNC (wood and metal)" }, { id: "metal", label: "Metal" }],
  purposes: [{ id: "fitout", label: "Fit-out project" }, { id: "studio", label: "Textile and wallcovering studio client" }, { id: "showroom", label: "Showroom furniture" }],
  categories: [{ id: "boards", label: "Wood and boards" }],
  tiles: { in_production: 1, waiting: 1, late: 0, to_site: 0, work_in_progress: "100.00", to_order: 0 },
  orders: [job(1)], to_order: [],
  raw_materials: [{ variant_id: 5, code: "MDF-18", item_code: "MDF-18", description: "TEST ONLY MDF board", size: "2440 x 1220 x 18 mm",
    category: "boards", category_label: "Wood and boards", unit: "Piece", unit_price: "50.00", list_price: "", list_currency: "",
    supplier: "TEST ONLY Boards Co", on_hand: "18.00", free: "18.00", places: [{ location_id: 11, location: "W41-04.2", on_hand: "18.00", free: "18.00" }] }],
  items: [], sales_orders: [{ id: 41, customer: "TEST ONLY Al Noor villa", status_label: "Approved", total: "9000.00" }],
  people: [{ id: 2, name: "TEST ONLY Carpenter", job_title: "Carpenter", department: "Factory", section: "joinery", section_label: "Joinery" }], accounts: [],
  locations: [{ id: 11, code: "W41-04.2", name: "Raw materials store", kind: "warehouse" },
    { id: 12, code: "W41-06.2", name: "Factory metal & CNC location", kind: "factory" }, { id: 13, code: "W41-FAC", name: "Factory", kind: "factory" }],
  can_run: true, can_approve: true, can_approve_cost: false, can_buy: false, can_keep_list: true, is_master_admin: true,
  policy: "TEST ONLY policy",
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent === text) as HTMLButtonElement | undefined;
const byLabel = (label: string) => container.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLSelectElement;
function setValue(input: HTMLInputElement | HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
  input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
}

beforeEach(() => {
  live = false;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/factory/") return json(overview());
    if (url.pathname === "/api/factory/jobs/1/materials/") return json(job(1), 201);
    if (url.pathname === "/api/factory/purchases/3/") return json(job(1));
    if (url.pathname === "/api/factory/boq-upload/") return json({
      file_name: "villa.xlsx", sheets: [{ name: "Joinery", items: 2 }], problems: ["Joinery: JD-09: the quantity 'two' is not a number"], more_problems: 0,
      lines: [{ code: "JD-00", description: "TEST ONLY oak door", quantity: "2.00", unit: "nos", details: { Finish: "Oak veneer" }, materials: [{ variant_id: 5, sku: "MDF-18", quantity: "30.00" }] },
        { code: "JD-01", description: "TEST ONLY wall cladding", quantity: "12.50", unit: "m2", details: {}, materials: [] }],
      materials: [{ variant_id: 5, sku: "MDF-18", name: "TEST ONLY MDF board", quantity: "30.00" }],
      stock_check: [{ variant_id: 5, sku: "MDF-18", name: "TEST ONLY MDF board", unit: "Piece", needed: "30.00", free: "18.00", other_jobs: "4.00", to_order: "16.00", short: true }],
    });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the Factory section appears only when the sign-in allows it", () => {
  const base = { isStaff: true, isSuperuser: true, canManageReportPanels: true };
  assert.equal(sidebarSections(base).some((section) => section.id === "factory"), false);
  const factory = sidebarSections({ ...base, canSeeFactory: true }).find((section) => section.id === "factory");
  assert.deepEqual(factory?.children.map((child) => child.id), ["main", "planning", "dashboard", "team", "machines", "learning", "materials", "setup"]);
  const staff = sidebarSections({ ...base, isSuperuser: false, canSeeFactory: true }).find((section) => section.id === "factory");
  assert.deepEqual(staff?.children.map((child) => child.id), ["main", "planning", "dashboard", "team", "machines", "learning", "materials"]);
});

test("a job shows its BOQ and cost, and materials beyond the list ask for approval", async () => {
  await act(async () => root.render(<Factory selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.textContent ?? "", /Preview: this section is not live yet/);
  assert.match(container.textContent ?? "", /In production/);
  act(() => button("JOB-0001")!.click());
  assert.match(container.textContent ?? "", /JD-00 oak door · 2 piece/);
  assert.match(container.textContent ?? "", /822\.25/);
  setValue(byLabel("Material to take"), "5");
  setValue(byLabel("Taken from"), "11");
  setValue(byLabel("Quantity to take"), "5");
  assert.ok(byLabel("Why more than listed"), "taking 5 more than the 4 still listed asks why");
  setValue(byLabel("Why more than listed"), "TEST ONLY a panel split");
  await act(async () => button("Send for approval")!.click());
  await flush();
  const sent = calls.find((call) => call.url.pathname === "/api/factory/jobs/1/materials/");
  assert.deepEqual(JSON.parse(String(sent?.init?.body)), { variant_id: 5, location_id: 11, deliver_to_id: 13, quantity: "5", note: "TEST ONLY a panel split" });
  assert.match(container.textContent ?? "", /The Factory Manager approves it, then the warehouse admin/);
});

test("the Factory Manager approves a special purchase", async () => {
  await act(async () => root.render(<Factory selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  act(() => button("JOB-0001")!.click());
  await act(async () => button("Approve")!.click());
  await flush();
  const sent = calls.find((call) => call.url.pathname === "/api/factory/purchases/3/");
  assert.equal(JSON.parse(String(sent?.init?.body)).action, "approve");
});

test("raw materials list shows size, price, supplier and stock", async () => {
  live = true;
  await act(async () => root.render(<Factory selectedUnitId={7} csrfToken="csrf" view="materials" />));
  await flush();
  const text = container.textContent ?? "";
  assert.doesNotMatch(text, /Preview/);
  for (const expected of ["MDF-18", "2440 x 1220 x 18 mm", "QAR 50.00", "Boards Co", "W41-04.2 18"]) assert.ok(text.includes(expected), expected);
});

test("a BOQ uploaded from Excel fills the job order's works", async () => {
  await act(async () => root.render(<Factory selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  act(() => button("Open a job order")!.click());
  const input = byLabel("BOQ Excel file") as HTMLInputElement;
  const file = new File(["fake"], "villa.xlsx");
  Object.defineProperty(input, "files", { value: [file] });
  await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
  await flush();
  const sent = calls.find((call) => call.url.pathname === "/api/factory/boq-upload/");
  assert.ok(sent?.init?.body instanceof FormData, "the file is sent as a form upload");
  assert.match(container.textContent ?? "", /Read 2 BOQ items from villa\.xlsx \(Joinery: 2\)/);
  assert.match(container.textContent ?? "", /JD-09: the quantity 'two' is not a number/);
  assert.equal((byLabel("BOQ description 2") as HTMLInputElement).value, "TEST ONLY wall cladding");
  assert.match(container.textContent ?? "", /Finish: Oak veneer/);
});

const stage = (id: number, kind: string, label: string, status = "not_started", extra = {}) => ({
  id, kind, label, section: label, status, status_label: { not_started: "Not started", in_progress: "In progress", stuck: "Stuck", done: "Completed", skipped: "Not needed" }[status],
  planned_start: "2026-11-02", planned_end: "2026-11-03", started_on: "", finished_on: "", lead: null, helpers: [], stuck_reason: "",
  late: false, days_late: 0, can_update: true, ...extra,
});
const planning = () => ({
  today: "2026-11-05", can_update: true, can_keep_tags: true,
  people: [{ id: 2, name: "TEST ONLY Carpenter", job_title: "Carpenter", department: "Factory" }],
  tags: [{ id: 1, name: "Door", color: "#aa3300", is_active: true }],
  stages: [{ id: "drawings", label: "Drawings and cutting list", section: "Drawing office", days: 3 }, { id: "saw", label: "Saw / CNC", section: "CNC", days: 1 }],
  jobs: [{ id: 1, number: "JOB-0001", status: "in_progress", status_label: "In production", purpose_label: "Fit-out project", project_name: "TEST ONLY Villa 12",
    customer: "TEST ONLY Al Noor", product: "", due_date: "2026-11-20", late: false, payment: { total: "9000.00", paid: "4500.00", balance: "4500.00", percent: "50.00" }, payment_issue: false,
    lines: [
      { id: 10, code: "JD-01", description: "TEST ONLY entrance door", quantity: "1.00", unit: "nos", area: "Entrance lobby", floor: "Ground floor", zone: "Zone 1",
        tags: [{ id: 1, name: "Door", color: "#aa3300" }], status: "stuck", status_label: "Stuck", current: { kind: "saw", label: "Saw / CNC", section: "CNC" },
        start: "2026-11-02", end: "2026-11-10", completed_on: "", late: true, team: [{ id: 2, name: "TEST ONLY Carpenter" }],
        stages: [stage(100, "drawings", "Drawings and cutting list", "done", { finished_on: "2026-11-03" }), stage(101, "saw", "Saw / CNC", "stuck", { stuck_reason: "TEST ONLY spindle broken", late: true, days_late: 2 })] },
      { id: 11, code: "JD-02", description: "TEST ONLY architrave", quantity: "2.00", unit: "nos", area: "", floor: "", zone: "",
        tags: [], status: "done", status_label: "Completed", current: null, start: "", end: "", completed_on: "2026-11-04", late: false, team: [],
        stages: [stage(102, "drawings", "Drawings and cutting list", "done", { finished_on: "2026-11-04" })] },
    ] }],
});

test("the production schedule groups JD items by status and reports progress on a stage", async () => {
  const base = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname === "/api/factory/planning/") { calls.push({ url, init }); return json(planning()); }
    if (url.pathname === "/api/factory/stages/101/") { calls.push({ url, init }); return json(planning().jobs[0]); }
    return base(input, init);
  }) as typeof fetch;
  await act(async () => root.render(<Factory selectedUnitId={7} csrfToken="csrf" view="planning" />));
  await flush();
  const groups = [...container.querySelectorAll(".fp-group-head")].map((node) => node.textContent);
  assert.deepEqual(groups, ["Stuck1", "Completed1"]);
  assert.match(container.textContent ?? "", /Entrance lobby · Ground floor · Zone 1/);
  act(() => (container.querySelector(".fp-toggle") as HTMLButtonElement).click());
  assert.match(container.textContent ?? "", /spindle broken/);
  await act(async () => button("Start")!.click());
  await flush();
  const sent = calls.find((call) => call.url.pathname === "/api/factory/stages/101/");
  assert.deepEqual(JSON.parse(String(sent?.init?.body)), { action: "start" });
  act(() => button("Gantt")!.click());
  assert.ok(container.querySelector('[aria-label="Production Gantt"]'), "the Gantt view shows");
  assert.equal(container.querySelectorAll(".fp-g-bar").length, 3, "one bar per planned stage");
});

test("the factory dashboard shows status, bottleneck, payments and tag history", async () => {
  const base = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname === "/api/factory/dashboard/") return json({
      today: "2026-11-05", items: { total: 3, not_started: 1, in_progress: 0, stuck: 1, done: 1, late: 1 },
      jobs: { in_production: 1, waiting: 0, finished: 0, late: 0 },
      stages: [{ kind: "materials", label: "Materials ready", section: "Raw materials store", waiting: 1, late: 0, stuck: 1, average_days: "", planned_days: "3.00" }],
      bottleneck: "materials", late: [{ job: "JOB-0001", code: "JD-01", description: "TEST ONLY door", stage: "Saw / CNC", section: "CNC", planned_end: "2026-11-01", days_late: 4, lead: "" }],
      late_count: 1, stuck: [], team: [],
      workload: { summary: { people: 2, busy: 1, free: 1, no_section: 0 }, people: [
        { id: 2, name: "TEST ONLY Carpenter", section_label: "Joinery", load: "busy", load_label: "Busy", active: 3, planned_soon: 1, late: 1, assigned: 4, hours_week: "30.00", hours_month: "96.00" },
        { id: 3, name: "TEST ONLY Painter", section_label: "Paint", load: "free", load_label: "Free", active: 0, planned_soon: 0, late: 0, assigned: 0, hours_week: "0.00", hours_month: "12.00" }] },
      to_order: [{ variant_id: 5, sku: "MDF-18", name: "TEST ONLY MDF board", unit: "Piece", needed: "30.00", free: "18.00", to_order: "12.00", jobs: [{ job: "JOB-0001", due_date: "2026-11-20", short: "12.00" }] }],
      machines: { machines: 4, maintenance: [{ code: "CNC-1", name: "TEST ONLY CNC router", label: "Maintenance overdue by 3 days", overdue: true }], condition: [], parts: [{ name: "TEST ONLY CNC bit", on_hand: "2.00", unit: "pcs" }] },
      costs: [{ job: "JOB-0001", estimate: "1000.00", limit: "1100.00", spent: "1200.00", percent: "120.00", over: true }],
      work_in_progress: "1200.00", payments: [{ job: "JOB-0001", customer: "TEST ONLY Al Noor", total: "9000.00", paid: "4500.00", balance: "4500.00", percent: "50.00", issue: true }],
      tags: { years: [2024, 2025, 2026], rows: [{ id: 1, name: "Door", color: "#aa3300", in_production: 2, total: 5, years: [{ year: 2024, items: 1, quantity: "1.00" }, { year: 2025, items: 3, quantity: "6.00" }, { year: 2026, items: 1, quantity: "2.00" }] }] },
    });
    return base(input, init);
  }) as typeof fetch;
  await act(async () => root.render(<Factory selectedUnitId={7} csrfToken="csrf" view="dashboard" />));
  await flush();
  const text = container.textContent ?? "";
  for (const expected of ["Raw materials store · bottleneck", "4 d", "over the limit", "ready, not fully paid", "Door", "Payment issues1",
    "Busy", "Free", "12 piece", "Maintenance overdue by 3 days", "CNC bit"]) assert.ok(text.includes(expected), expected);
});

const slip = (status: string, extra = {}) => ({
  id: 9, number: "JOB-0001-M01", status, status_label: status === "approved" ? "Approved: print and hand to the warehouse" : "Waiting for the Factory Manager",
  job: "JOB-0001", project: "TEST ONLY Villa 12", store: "W41-04.2 Raw materials store", deliver_to: "W41-FAC Factory", note: "", rejection_reason: "",
  lines: [{ sku: "MDF-18", name: "TEST ONLY MDF board", unit: "Piece", quantity: "4.00", beyond_plan: false }],
  approvals: [{ step: "Requested by (factory admin)", name: "runner", at: "2026-11-07T08:00:00Z" },
    { step: "Approved by the Factory Manager", name: status === "approved" ? "manager" : "", at: status === "approved" ? "2026-11-07T08:30:00Z" : "" },
    { step: "Approved by the warehouse admin", name: status === "approved" ? "storekeeper" : "", at: status === "approved" ? "2026-11-07T09:00:00Z" : "" }],
  transfer_id: 4, transfer_status: "Approved", can_decide: status === "manager", can_print: status === "approved", printed_count: 0, ...extra,
});

test("a material request slip is approved by the Factory Manager, then printed with every approval", async () => {
  const base = globalThis.fetch;
  let state = "manager";
  const printed: string[] = [];
  const realPrint = window.print;
  window.print = () => { printed.push("print"); };
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname === "/api/factory/") return json({ ...overview(), orders: [job(1, { material_requests: [slip(state)], short_materials: [{ sku: "MDF-18", name: "MDF", short: "2.00" }] })] });
    if (url.pathname === "/api/factory/material-requests/9/") {
      calls.push({ url, init });
      const action = JSON.parse(String(init?.body)).action;
      if (action === "approve") { state = "approved"; return json(job(1)); }
      return json({ ...slip("approved", { printed_count: 1 }), company: "TEST ONLY Tivoli", unit: "Tivoli", printed_at: "2026-11-07T09:10:00Z", printed_by: "runner" });
    }
    return base(input, init);
  }) as typeof fetch;
  await act(async () => root.render(<Factory selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.textContent ?? "", /1 material to order/);
  act(() => button("JOB-0001")!.click());
  assert.match(container.textContent ?? "", /Not enough in stock for this job: 2 × MDF-18/);
  assert.match(container.textContent ?? "", /Approved by the warehouse admin: waiting/);
  const approve = [...container.querySelectorAll(".fac-slip button")].find((node) => node.textContent === "Approve") as HTMLButtonElement;
  await act(async () => approve.click());
  await flush();
  assert.match(container.textContent ?? "", /sent to the warehouse admin/);
  await act(async () => button("Print the slip")!.click());
  await flush();
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
  const sheet = container.querySelector(".fac-print")?.textContent ?? "";
  for (const expected of ["Material request", "JOB-0001-M01", "MDF-18", "Approved by the Factory Manager", "manager", "storekeeper", "Handed over by (warehouse)", "copy 1"]) assert.ok(sheet.includes(expected), expected);
  assert.deepEqual(printed, ["print"]);
  window.print = realPrint;
});

test("the BOQ upload reads materials and shows what to order", async () => {
  await act(async () => root.render(<Factory selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  act(() => button("Open a job order")!.click());
  const file = new File(["x"], "villa.xlsx");
  const input = byLabel("BOQ Excel file") as HTMLInputElement;
  Object.defineProperty(input, "files", { value: [file] });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  await flush();
  const text = container.textContent ?? "";
  assert.match(text, /1 of 1 materials are not in stock in full/);
  assert.match(text, /16 piece/);
  assert.match(text, /Materials: 30 × MDF-18/);
  assert.equal((byLabel("Material quantity 1") as HTMLInputElement).value, "30.00");
});

test("team, machines and learning pages", async () => {
  const base = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname === "/api/factory/team/") return json({
      today: "2026-11-07", sections: [{ id: "joinery", label: "Joinery" }, { id: "paint", label: "Paint" }],
      summary: { people: 1, busy: 1, free: 0, no_section: 0 }, can_keep_team: true, staff: [],
      people: [{ id: 2, name: "TEST ONLY Carpenter", job_title: "Carpenter", department: "Factory", section: "joinery", section_label: "Joinery", active: 3, planned_soon: 1, assigned: 4, late: 1,
        load: "busy", load_label: "Busy", hours_week: "30.00", hours_month: "96.00", hours_year: "900.00", completed_year: 12, work: [] }],
    });
    if (url.pathname === "/api/factory/machines/") return json({
      today: "2026-11-07", alerts: [{ level: "red", kind: "maintenance", text: "CNC-1 TEST ONLY CNC router: maintenance overdue by 5 days" }],
      machines: [{ id: 1, code: "CNC-1", name: "TEST ONLY CNC router", kind: "cnc", kind_label: "CNC router", section: "cnc", section_label: "CNC (wood and metal)", make_model: "", serial_number: "", quantity: 1,
        condition: "good", condition_label: "Good condition", bought_on: "", cost: "185000.00", service_every_days: 90, last_service_on: "2026-08-04", next_service_on: "2026-11-02",
        service: "overdue", service_label: "Maintenance overdue by 5 days", key_machine: true, treatment: "asset", treatment_label: "Fixed asset (depreciated)", fixed_asset: null, finance_missing: true,
        note: "", is_active: true, services: [], can_update: true }],
      parts: [{ id: 1, name: "TEST ONLY CNC bit 6mm", kind: "consumable", kind_label: "Consumable", machine_id: 1, machine: "CNC-1 TEST ONLY CNC router", unit: "pcs", on_hand: "3.00", reorder_at: "4.00", unit_cost: "60.00", value: "180.00", order_needed: true, is_active: true }],
      summary: { machines: 1, need_action: 0, maintenance_due: 1, parts_to_order: 1, by_kind: [{ kind: "cnc", label: "CNC router", count: 1 }], by_condition: [] },
      kinds: [{ id: "cnc", label: "CNC router" }], conditions: [{ id: "good", label: "Good condition" }], sections: [{ id: "cnc", label: "CNC (wood and metal)" }],
      service_kinds: [{ id: "routine", label: "Routine maintenance" }], part_kinds: [{ id: "consumable", label: "Consumable" }],
      policy: { asset_threshold: "2000.00", life_months: 60, due_soon_days: 7 }, can_update: true,
    });
    if (url.pathname === "/api/factory/learning/") return json({
      min_samples: 2, years: 3, finished_items: 14, stages: [{ id: "joinery", label: "Joinery", usual_days: 6 }, { id: "paint", label: "Paint", usual_days: 6 }],
      tags: [{ id: 1, name: "Door", color: "#aa3300", finished: 9, profile: { samples: 9, units: "30.00", total_days: 9, accessories_per_unit: "3.00",
        stages: { joinery: { days: 5, samples: 9, usually_skipped: false }, paint: { days: 4, samples: 9, usually_skipped: false } },
        materials: [{ variant_id: 5, sku: "MDF-18", name: "TEST ONLY MDF board", accessory: false, per_unit: "1.50" }], hours_per_unit: { joinery: "6.00" },
        leads: { joinery: { id: 2, name: "TEST ONLY Carpenter", times: 7 } } } }],
    });
    return base(input, init);
  }) as typeof fetch;
  await act(async () => root.render(<Factory selectedUnitId={7} csrfToken="csrf" view="team" />));
  await flush();
  assert.match(container.textContent ?? "", /Carpenter.*Joinery.*Busy/s);
  await act(async () => root.render(<Factory selectedUnitId={7} csrfToken="csrf" view="machines" />));
  await flush();
  for (const expected of ["Maintenance overdue by 5 days", "not yet in Finance's fixed assets", "CNC bit 6mm", "order more", "QAR 2,000.00 or more is a fixed asset"]) assert.ok((container.textContent ?? "").includes(expected), expected);
  await act(async () => root.render(<Factory selectedUnitId={7} csrfToken="csrf" view="learning" />));
  await flush();
  for (const expected of ["Learned from 14 JDs", "9 working days", "Joinery: Carpenter", "Based on 9 JDs"]) assert.ok((container.textContent ?? "").includes(expected), expected);
});

test("the factory planner asks first, then shows the plan, what moved and materials on the way", async () => {
  const plan = {
    today: "2026-11-07", can_plan: true,
    rules: { order_lead_days: 21, stages_per_person: 2, ask_days: 3 },
    summary: { jobs: 1, to_brief: 1, changes: 0, late_jobs: [{ number: "JOB-0001", late_by: 4 }], waiting_materials: 1, not_ordered: 1 },
    runs: [{ id: 1, ran_on: "2026-11-07", ran_at: "2026-11-07T06:00:00Z", by: "The planner", reason: "Morning re-plan", changes: 1,
      moves: [{ job: "JOB-0001", code: "JD-00", stage: "Paint", from: "12 Nov to 18 Nov", to: "20 Nov to 26 Nov", lead: "TEST ONLY Sunil", why: "TEST ONLY Sunil is on other work until 19 Nov." }] }],
    materials: [{ variant_id: 5, sku: "MDF-18", name: "TEST ONLY MDF board", unit: "Piece", needed: "40.00", free: "20.00", not_ordered: "5.00", covered_by_stock: false,
      jobs: [{ job: "JOB-0001", ready_on: "2026-11-24" }],
      coming: [{ kind: "order", order_id: 9, supplier: "TEST ONLY Boards Co", status: "ordered", status_label: "Ordered", ordered_on: "2026-11-03", expected_on: "2026-11-24", expected_given: false, overdue: false, payment: "advance", payment_label: "Advance paid", quantity: "15.00" }] }],
    jobs: [
      { id: 1, number: "JOB-0001", project: "TEST ONLY Villa 12", status: "in_progress", status_label: "In production", due_date: "2026-12-01", finish_by: "2026-12-01", finishes_on: "2026-12-05", late_by: 4,
        materials_ready_on: "2026-11-24", waiting_for: [{ sku: "MDF-18", name: "TEST ONLY MDF board", ready_on: "2026-11-24", source: "Ordered, expected 24 Nov" }], not_ordered: [{ sku: "MDF-18", name: "TEST ONLY MDF board", quantity: "5.00" }],
        planned: true, brief: { start_on: "2026-11-09", floor_order: ["Ground floor"], first_codes: [], finish_by: "2026-12-01", notes: "", given_by: "TEST ONLY Admin", given_at: "2026-11-07T08:00:00Z" },
        lines: [{ id: 11, code: "JD-00", description: "TEST ONLY oak door", floor: "Ground floor", tags: ["Door"], learned_from: 3, stages: [
          { id: 101, kind: "joinery", label: "Joinery", section: "Joinery", status: "not_started", status_label: "Not started", kept: false, changed: false, by_hand: false,
            now: { start: "2026-11-25", end: "2026-11-30", lead: "TEST ONLY Jomon" }, plan: { start: "2026-11-25", end: "2026-11-30", lead_id: 3, lead: "TEST ONLY Jomon" },
            reasons: ["5 working days, as in 3 finished JDs (same tags).", "Starts after the materials arrive (24 Nov)."] }] }] },
      { id: 2, number: "JOB-0002", project: "TEST ONLY Office", status: "waiting", status_label: "Waiting", due_date: "2026-12-20", finish_by: "2026-12-20", finishes_on: "2026-12-18", late_by: 0,
        materials_ready_on: "2026-11-07", waiting_for: [], not_ordered: [], planned: false, brief: null, lines: [],
        questions: { floors: ["Ground floor", "First floor"], codes: ["JD-00", "JD-01"],
          suggested: { start_on: "2026-11-12", start_why: "The first day the team can take it.", floor_order: ["Ground floor", "First floor"], finish_by: "2026-12-20", first_codes: [] } } },
    ],
  };
  const base = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname === "/api/factory/plan/" || url.pathname.endsWith("/plan-brief/") || url.pathname === "/api/factory/arrivals/") { calls.push({ url, init }); return json(plan); }
    if (url.pathname === "/api/factory/planning/") return json(planning());
    return base(input, init);
  }) as typeof fetch;
  await act(async () => root.render(<Factory selectedUnitId={7} csrfToken="csrf" view="planning" />));
  await flush();
  const text = () => container.textContent ?? "";
  assert.match(text(), /1 job planned, 1 waiting for your answers/);
  assert.match(text(), /Before I plan JOB-0002/);
  assert.match(text(), /Suggested 12 Nov: The first day the team can take it/);
  // Put the first floor first and ask for JD-01 before the others.
  await act(async () => (container.querySelector('[aria-label="Move First floor up"]') as HTMLButtonElement).click());
  await act(async () => ([...container.querySelectorAll(".fpl-codes label")].find((node) => node.textContent?.includes("JD-01"))!.querySelector("input") as HTMLInputElement).click());
  await act(async () => button("Plan this job")!.click());
  await flush();
  const sent = calls.find((call) => call.url.pathname === "/api/factory/orders/2/plan-brief/");
  assert.deepEqual(JSON.parse(String(sent?.init?.body)), { start_on: "2026-11-12", floor_order: ["First floor", "Ground floor"], first_codes: ["JD-01"], finish_by: "2026-12-20", notes: "" });
  assert.match(text(), /Finishes 5 Dec, 4 days after 1 Dec/);
  act(() => ([...container.querySelectorAll(".fpl-job .fp-toggle")][0] as HTMLButtonElement).click());
  assert.match(text(), /Starts after the materials arrive \(24 Nov\)/);
  act(() => button("What moved (1)")!.click());
  assert.match(text(), /Sunil is on other work until 19 Nov/);
  act(() => button("Materials on the way (1)")!.click());
  assert.match(text(), /Ordered from Boards Co on 3 Nov/);
  assert.match(text(), /Advance paid/);
  const arrival = container.querySelector('[aria-label="Expected arrival of order 9"]') as HTMLInputElement;
  await act(async () => setValue(arrival, "2026-11-18"));
  await flush();
  const given = calls.find((call) => call.url.pathname === "/api/factory/arrivals/");
  assert.deepEqual(JSON.parse(String(given?.init?.body)), { order_id: 9, expected_on: "2026-11-18" });
});
