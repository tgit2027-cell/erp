import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import UserAdmin from "../src/UserAdmin";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let actions: string[];
let members: { id: number; username: string; name: string; is_active: boolean; added_at: string }[];
let teamActions: string[];
let viewerIsMaster: boolean;
const LEVELS = [["master_admin", "Master Admin"], ["general_admin", "General Admin"], ["finance_admin", "Finance Admin"],
  ["hr_admin", "HR Admin"], ["head", "Head"], ["manager", "Manager"], ["sales", "Sales"], ["custom", "Custom"]]
  .map(([level, label]) => ({ level, label, summary: `${label} summary`, per_unit: ["head", "manager", "sales"].includes(level) }));

const summary = (id: number, username: string, name: string, extra = {}) => ({
  id, username, name, email: "", is_active: true, is_superuser: false, is_staff: false, last_login: "",
  units: [], permission_count: 0, role_count: 0, level: "custom", level_label: "Custom", ...extra,
});
const people = () => ({
  people: [summary(1, "admin", "Synthetic Admin", { is_superuser: true, units: ["TIV", "RBB"], level: "master_admin", level_label: "Master Admin" }), summary(2, "dana", "Dana Buyer", { units: ["TIV"] })],
  units: [{ id: 10, code: "TIV", name: "Tivoli" }, { id: 11, code: "RBB", name: "Roche Bobois" }], me: 1, is_master_admin: viewerIsMaster,
});
const detail = (id = 2) => ({
  person: summary(id, id === 1 ? "admin" : "dana", id === 1 ? "Synthetic Admin" : "Dana Buyer",
    { units: ["TIV"], ...(id === 3 ? { level: "", level_label: "No access yet" } : {}) }), is_me: id === 1,
  access_level: {
    level: id === 1 ? "master_admin" : "custom", label: id === 1 ? "Master Admin" : "Custom", based_on: id === 1 ? "" : "sales",
    unit_ids: [], choices: LEVELS,
    assignable: viewerIsMaster ? LEVELS.map((row) => row.level) : id === 1 ? [] : ["head", "manager", "sales"],
    can_edit: viewerIsMaster || id !== 1, can_customize: viewerIsMaster,
  },
  groups: [
    { name: "Sales and stock", actions: [{ action: "read_stock", label: "Read stock", hint: "Lets the person open this unit and see its stock." }, { action: "create_order", label: "Create order" }] },
    { name: "Items and buying", actions: [{ action: "manage_items", label: "Edit item details and photos" }, { action: "view_costs", label: "See item costs" }] },
  ],
  roles: [{ role: "salesperson", label: "Salesperson" }, { role: "manager", label: "Manager" }],
  units: [{ id: 10, code: "TIV", name: "Tivoli", actions, from_teams: { create_order: ["TEST ONLY Sales"] }, roles: ["manager"] },
    { id: 11, code: "RBB", name: "Roche Bobois", actions: [], from_teams: {}, roles: [] }],
  company: [{ action: "open_supplier", label: "Open new suppliers (KYC)", on: false, from_teams: [] }],
  teams: [{ id: 7, name: "TEST ONLY Sales", on: true, is_active: true }, { id: 8, name: "TEST ONLY Warehouse", on: false, is_active: true }],
  reporting_roles: [{ id: 4, name: "TEST ONLY Finance", on: true, manages_panels: false }], report_panels: ["Sales"],
  chat_roles: [{ role: "requester", label: "Ask to review a chat (HR)", on: false }, { role: "approver", label: "Approve chat reviews (owner)", on: false }],
  capacity: [{ location: "TEST ONLY Warehouse", access: "View warehouse capacity" }],
  history: [{ at: "2026-10-31T10:00:00Z", by: "admin", what: "Manager in TIV", change: "granted" }],
});

const team = () => ({
  team: { id: 7, name: "TEST ONLY Sales", description: "Showroom sales", is_active: true, members: members.length, permissions: teamActions.length,
    created_at: "2026-10-31T09:00:00Z", created_by: "admin", chat_id: 40 },
  members, people: [{ id: 1, name: "Synthetic Admin", username: "admin" }],
  groups: [{ name: "Sales and stock", actions: [{ action: "read_stock", label: "Read stock" }, { action: "create_order", label: "Create order" }] }],
  units: [{ id: 10, code: "TIV", name: "Tivoli", actions: teamActions }, { id: 11, code: "RBB", name: "Roche Bobois", actions: [] }],
  company: [{ action: "open_supplier", label: "Open new suppliers (KYC)", on: false }],
  history: [{ at: "2026-10-31T09:00:00Z", by: "admin", change: "created", what: "the team" }],
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
const box = (label: string) => [...container.querySelectorAll("label")].find((node) => node.textContent?.startsWith(label))?.querySelector("input") as HTMLInputElement;
function type(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  actions = ["read_stock"];
  members = [{ id: 2, username: "dana", name: "Dana Buyer", is_active: true, added_at: "2026-10-31T09:00:00Z" }];
  teamActions = ["create_order"];
  viewerIsMaster = true;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/governance/people/" && init?.method === "POST") return json(detail(3), 201);
    if (url.pathname === "/api/governance/people/") return json(people());
    const match = url.pathname.match(/^\/api\/governance\/people\/(\d+)\/$/);
    if (match && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      if (body.password === "weak") return json({ detail: "This password is too short." }, 400);
      if (body.password) return json({ done: true });
      if (body.kind === "action") actions = body.enabled ? [...actions, body.action] : actions.filter((each) => each !== body.action);
      return json(detail(Number(match[1])));
    }
    if (match) return json(detail(Number(match[1])));
    if (url.pathname === "/api/governance/teams/" && init?.method === "POST") return json(team(), 201);
    if (url.pathname === "/api/governance/teams/") {
      return json({ teams: [{ id: 7, name: "TEST ONLY Sales", description: "Showroom sales", is_active: true, members: members.length, permissions: teamActions.length }] });
    }
    if (url.pathname === "/api/governance/teams/7/" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      if (body.kind === "member") members = body.enabled ? [...members, { id: body.user_id, username: "admin", name: "Synthetic Admin", is_active: true, added_at: "" }] : members.filter((row) => row.id !== body.user_id);
      if (body.kind === "action") teamActions = body.enabled ? [...teamActions, body.action] : teamActions.filter((each) => each !== body.action);
      return json(team());
    }
    if (url.pathname === "/api/governance/teams/7/") return json(team());
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("a person's permissions per unit, roles and history show in one place and switch on with one tick", async () => {
  act(() => root.render(<UserAdmin csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector(".ua-people")?.textContent ?? "", /Synthetic Admin.*Master Admin.*TIV, RBB.*Dana Buyer.*Custom/);
  await act(async () => button("Dana Buyer")!.click());
  await flush();
  assert.equal(box("Read stock").checked, true);
  assert.equal(box("See item costs").checked, false);
  assert.equal(box("Manager").checked, true);
  assert.equal(box("Finance").checked, true, "Reporting roles show too");
  assert.match(container.textContent ?? "", /Report panels given directly: Sales/);
  assert.match(container.textContent ?? "", /Warehouse: view warehouse capacity/);
  assert.match(container.querySelector(".ua-history")?.textContent ?? "", /admin: granted Manager in TIV/);

  await act(async () => box("See item costs").click());
  await flush();
  const change = calls.find((call) => call.init?.method === "POST")!;
  assert.deepEqual(JSON.parse(String(change.init?.body)), { kind: "action", unit_id: 10, action: "view_costs", enabled: true });
  assert.equal(box("See item costs").checked, true);
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /See item costs in TIV switched on for Dana Buyer/);

  await act(async () => button("Roche Bobois")!.click());
  assert.equal(box("Read stock").checked, false, "Each unit has its own permissions");
});

test("access is copied from a colleague, accounts switch off, and passwords are checked by the server", async () => {
  act(() => root.render(<UserAdmin csrfToken="csrf" />));
  await flush();
  await act(async () => button("Dana Buyer")!.click());
  await flush();
  const select = container.querySelector(".ua-copy select") as HTMLSelectElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(select), "value")?.set?.call(select, "1");
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => button("Copy access")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(calls.at(-2)!.init?.body)), { copy_from: 1, unit_id: 10 });

  await act(async () => button("Switch account off")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(calls.filter((call) => call.init?.method === "POST").at(-1)!.init?.body)), { kind: "active", enabled: false });

  await act(async () => button("Set a temporary password")!.click());
  await act(async () => type(container.querySelector(".ua-password input") as HTMLInputElement, "weak"));
  await act(async () => (container.querySelector(".ua-password") as HTMLFormElement).requestSubmit());
  await flush();
  assert.match(container.querySelector("[role=alert]")?.textContent ?? "", /too short/);

  await act(async () => button("Synthetic Admin")!.click());
  await flush();
  assert.ok(button("Switch account off")!.disabled, "Nobody can switch off their own account");
});

test("a new person is added with a temporary password and opens with no permissions", async () => {
  act(() => root.render(<UserAdmin csrfToken="csrf" />));
  await flush();
  await act(async () => button("Add a person")!.click());
  const form = container.querySelector("form.ua-add") as HTMLFormElement;
  await act(async () => { type(box("Username"), "new_buyer"); type(box("Temporary password"), "Correct-Horse-Battery-71"); });
  await act(async () => form.requestSubmit());
  await flush();
  const add = calls.find((call) => call.url.pathname === "/api/governance/people/" && call.init?.method === "POST")!;
  assert.equal(JSON.parse(String(add.init?.body)).username, "new_buyer");
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /was added with no access yet/);
  assert.ok(container.querySelector(".ua-person"));
});

test("a permission that comes from a team shows as such, and team membership is ticked on the person", async () => {
  act(() => root.render(<UserAdmin csrfToken="csrf" />));
  await flush();
  await act(async () => button("Dana Buyer")!.click());
  await flush();
  const order = box("Create order");
  assert.equal(order.checked, true);
  assert.equal(order.disabled, true, "A team permission is changed on the team, not on the person");
  assert.match(order.closest("label")?.textContent ?? "", /Given by team: Sales/);
  const teams = [...container.querySelectorAll("fieldset")].find((node) => node.querySelector("legend")?.textContent === "Teams")!;
  const team = (name: string) => [...teams.querySelectorAll("label")].find((node) => node.textContent === name)!.querySelector("input")!;
  assert.equal(team("Sales").checked, true);
  assert.equal(team("Warehouse").checked, false);
  await act(async () => team("Warehouse").click());
  await flush();
  const change = calls.filter((call) => call.init?.method === "POST").at(-1)!;
  assert.deepEqual(JSON.parse(String(change.init?.body)), { kind: "team", team_id: 8, enabled: true });
});

test("teams: members are added and taken out, and a team permission switches on for everyone", async () => {
  act(() => root.render(<UserAdmin csrfToken="csrf" />));
  await flush();
  await act(async () => button("Teams")!.click());
  await flush();
  assert.match(container.querySelector(".ua-people")?.textContent ?? "", /Sales1 member · 1 permission/);
  await act(async () => button("Sales")!.click());
  await flush();
  assert.match(container.querySelector(".tm-members")?.textContent ?? "", /Dana Buyer/);
  assert.equal(box("Create order").checked, true);
  assert.match(container.textContent ?? "", /reaches everyone in the team/);

  await act(async () => box("Read stock").click());
  await flush();
  let change = calls.filter((call) => call.init?.method === "POST").at(-1)!;
  assert.deepEqual(JSON.parse(String(change.init?.body)), { kind: "action", unit_id: 10, action: "read_stock", enabled: true });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Read stock in TIV switched on for everyone in Sales/);

  const select = container.querySelector(".tm-add select") as HTMLSelectElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(select), "value")?.set?.call(select, "1");
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => button("Add to team")!.click());
  await flush();
  change = calls.filter((call) => call.init?.method === "POST").at(-1)!;
  assert.deepEqual(JSON.parse(String(change.init?.body)), { kind: "member", user_id: 1, enabled: true });
  assert.match(container.querySelector(".tm-members")?.textContent ?? "", /Synthetic Admin/);

  await act(async () => container.querySelector<HTMLButtonElement>(".tm-members li button")!.click());
  await flush();
  assert.doesNotMatch(container.querySelector(".tm-members")?.textContent ?? "", /Dana Buyer/);
});

test("a new team is made with a name and opens ready for members", async () => {
  members = [];
  teamActions = [];
  act(() => root.render(<UserAdmin csrfToken="csrf" />));
  await flush();
  await act(async () => button("Teams")!.click());
  await flush();
  await act(async () => button("Make a team")!.click());
  await act(async () => type(box("Team name"), "TEST ONLY Sales"));
  await act(async () => (container.querySelector("form.ua-add") as HTMLFormElement).requestSubmit());
  await flush();
  const add = calls.find((call) => call.url.pathname === "/api/governance/teams/" && call.init?.method === "POST")!;
  assert.deepEqual(JSON.parse(String(add.init?.body)), { name: "TEST ONLY Sales", description: "" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Sales is made/);
  assert.match(container.textContent ?? "", /Nobody is in this team yet/);
});

function choose(select: HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(select), "value")?.set?.call(select, value);
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

test("a Master Admin puts a person on a preset level in chosen business units", async () => {
  act(() => root.render(<UserAdmin csrfToken="csrf" />));
  await flush();
  await act(async () => button("Dana Buyer")!.click());
  await flush();
  const level = container.querySelector(".ua-level");
  assert.match(level?.textContent ?? "", /Custom.*started from Sales/);
  await act(async () => choose(container.querySelector(".ua-level-select select") as HTMLSelectElement, "manager"));
  assert.match(level?.textContent ?? "", /Manager summary/);
  assert.ok(button("Apply level")!.disabled, "Head, Manager and Sales need at least one business unit");
  await act(async () => box("Roche Bobois")!.click());
  await act(async () => button("Apply level")!.click());
  await flush();
  const change = calls.filter((call) => call.init?.method === "POST").at(-1)!;
  assert.deepEqual(JSON.parse(String(change.init?.body)), { kind: "level", level: "manager", unit_ids: [11] });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Dana Buyer is now on the Manager level/);
});

test("a General Admin sees permissions but cannot change them one by one or copy access, and can open teams", async () => {
  viewerIsMaster = false;
  act(() => root.render(<UserAdmin csrfToken="csrf" />));
  await flush();
  assert.ok(button("Teams"), "General Admins manage teams within their limits (setup lock, Nov 2026)");
  await act(async () => button("Dana Buyer")!.click());
  await flush();
  assert.equal(box("Read stock").closest("fieldset")!.disabled, true);
  assert.equal(container.querySelector(".ua-copy"), null);
  const options = [...(container.querySelector(".ua-level-select select") as HTMLSelectElement).options].map((node) => node.value);
  assert.deepEqual(options, ["head", "manager", "sales"]);
  assert.match(container.textContent ?? "", /Only a Master Admin can switch them on or off one by one/);

  await act(async () => button("Synthetic Admin")!.click());
  await flush();
  assert.equal(container.querySelector(".ua-level-select"), null);
  assert.ok(button("Set a temporary password")!.disabled, "A General Admin cannot change a Master Admin");

  await act(async () => button("Add a person")!.click());
  const levels = [...(container.querySelector("form.ua-add select") as HTMLSelectElement).options].map((node) => node.value);
  assert.deepEqual(levels, ["", "head", "manager", "sales"]);
});
