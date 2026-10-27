import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, Profiler } from "react";
import { createRoot } from "react-dom/client";
import App from "../src/App";
import { PurchasingGovernanceReview } from "../src/PurchasingWorkbench";
import WarehouseCapacity from "../src/WarehouseCapacity";
import WarehouseCapacityAuditRegister from "../src/WarehouseCapacityAuditRegister";
import { sidebarSections } from "../src/sidebar-destinations";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let host: HTMLDivElement;
let calls: URL[];
let selectedUnit = 1;
let activeStaff = true;
let activeSuperuser = false;
let panelManager = false;
let delayedUnitOne: ((response: Response) => void) | null = null;
let denyPanelManager = false;

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const unitConfig = (id: number) => ({
  test_only_label: `TEST ONLY config for unit ${id}`,
  business_unit: { id, code: `U${id}`, name: `TEST ONLY Unit ${id}` },
  role_assignments: [], current_prices: [],
  work_calendar: { is_configured: false, closed_weekdays: [], closed_dates: [] },
  storage_exception_approver_roles: [], special_order_value_bands: [],
  special_order_bands_used_for_approval: false, special_order_bands_note: "TEST ONLY note",
});
const auditPage = {
  results: [{
    id: 9, kind: "assignment_revoked", label: "Assignment revoked",
    location: { id: 31, code: "WH-X", name: "TEST ONLY physical location" },
    actor: "TEST_ONLY_admin", target_user: "TEST_ONLY_operator", capability: "view_capacity",
    created_at: "2032-02-03T10:00:00Z",
    total_floor_area_m2: "DO_NOT_RENDER_MEASUREMENTS",
  }],
  pagination: { page: 1, page_size: 20, total_count: 1, total_pages: 1, previous_page: null, next_page: null },
};
const adminConfiguration = {
  locations: [
    { id: 31, code: "WH-X", name: "TEST ONLY physical location", physical_capacity_configured: true, business_units: [], assignments: [] },
    { id: 32, code: "WH-Y", name: "TEST ONLY unconfigured location", physical_capacity_configured: false, business_units: [], assignments: [] },
  ],
  synthetic_users: [{ id: 71, username: "TEST_ONLY_operator" }],
};

async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
async function render(node: React.ReactNode) {
  await act(async () => root.render(node));
  await flush();
}
async function click(element: HTMLElement | null) {
  assert.ok(element, "Expected a rendered interactive element");
  await act(async () => element.click());
  await flush();
}
async function setSelect(element: HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value")?.set?.call(element, value);
  await act(async () => element.dispatchEvent(new Event("change", { bubbles: true })));
  await flush();
}
async function submit(form: HTMLFormElement) {
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();
}
function appFavorite(destination: string) {
  return [...host.querySelectorAll<HTMLButtonElement>(".favorite-shortcut .nav-item")]
    .find((button) => button.textContent?.includes(destination === "governance" ? "Governance" : destination));
}
function navigateGovernanceChild(label: string) {
  const toggle = host.querySelector<HTMLButtonElement>('.nav-section-toggle[title="Governance & Administration"]');
  return (async () => {
    if (toggle && toggle.getAttribute("aria-expanded") !== "true") await click(toggle);
    const routeId: Record<string, string> = {
      "Governance & Administration": "main",
      "Business-unit palettes": "presentation",
      "Purchasing configuration review": "purchasing-config",
      "Reporting governance": "reporting",
      "Physical locations & assignments": "capacity-admin",
      "Warehouse capacity audit": "capacity-audit",
      "Stock transfer permissions": "transfer-permissions",
    };
    const child = host.querySelector<HTMLButtonElement>(`[data-workspace-link="governance/${routeId[label]}"]`);
    await click(child ?? null);
  })();
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  calls = [];
  selectedUnit = 1;
  activeStaff = true;
  activeSuperuser = false;
  panelManager = false;
  delayedUnitOne = null;
  denyPanelManager = false;
  globalThis.fetch = (path, options = {}) => {
    const url = new URL(String(path), "https://governance.test.invalid");
    calls.push(url);
    if (options.method === "POST" && url.pathname === "/api/governance/warehouse-capacity/") return Promise.resolve(json(adminConfiguration));
    if (options.method === "POST" && url.pathname === "/api/governance/warehouse-capacity/assignments/") return Promise.resolve(json({}));
    if (options.method === "DELETE" && url.pathname.startsWith("/api/governance/warehouse-capacity/assignments/")) return Promise.resolve(new Response(null, { status: 204 }));
    if (url.pathname === "/api/auth/csrf/") return Promise.resolve(json({ csrfToken: "TEST_ONLY_CSRF" }));
    if (url.pathname === "/api/auth/me/") return Promise.resolve(json({
      authenticated: true, username: "TEST_ONLY_operator", is_staff: activeStaff,
      is_superuser: activeSuperuser, can_manage_report_panels: panelManager,
    }));
    if (url.pathname === "/api/organization/") return Promise.resolve(json({
      company: { name: "TEST ONLY Retail Group", code: "TST" },
      units: [
        { id: 1, code: "U1", name: "TEST ONLY Unit 1", theme_palette: "slate" },
        { id: 2, code: "U2", name: "TEST ONLY Unit 2", theme_palette: "roche" },
      ],
      selected_unit: { id: selectedUnit, code: `U${selectedUnit}`, name: `TEST ONLY Unit ${selectedUnit}`, theme_palette: "slate" },
      locations: [],
    }));
    if (url.pathname === "/api/favorites/") {
      const available = activeStaff
        ? [{ destination: "governance", label: "Governance & Administration" }]
        : [{ destination: "reports", label: "Reports" }];
      return Promise.resolve(json({ available_destinations: available, favorites: available }));
    }
    if (url.pathname === "/api/stock-balances/") return Promise.resolve(json({
      results: [], pagination: { page: 1, page_size: 50, total_count: 0, total_pages: 1, previous_page: null, next_page: null },
    }));
    if (url.pathname.startsWith("/api/units/") && url.pathname.endsWith("/select/")) {
      selectedUnit = Number(url.pathname.split("/")[3]);
      return Promise.resolve(json({}));
    }
    if (url.pathname === "/api/governance/business-unit-palettes/") return Promise.resolve(json({
      palettes: [{ value: "slate", label: "Slate", kind: "built_in", colors: {} }],
      units: [1, 2].map((id) => ({ id, code: `U${id}`, name: `TEST ONLY Unit ${id}`, theme_palette: "slate", palette_value: "slate" })),
    }));
    if (url.pathname === "/api/governance/report-panels/") {
      return Promise.resolve(denyPanelManager ? json({ detail: "TEST ONLY panel permission revoked" }, 403) : json({
        users: [{ id: 71, username: "TEST_ONLY_operator" }], roles: [], panels: [{ code: "sales", label: "Sales" }],
        panel_grants: [{ id: 3, user_id: 71, username: "TEST_ONLY_operator", role_id: null, role_name: null, panel: "sales", revoked: false }],
      }));
    }
    if (url.pathname === "/api/reports/") return Promise.resolve(json({ panels: [], scopes: {} }));
    if (/^\/api\/governance\/purchasing\/\d+\/$/.test(url.pathname)) {
      const id = Number(url.pathname.split("/")[4]);
      if (id === 1 && delayedUnitOne) {
        return new Promise<Response>((resolve) => { delayedUnitOne = resolve; });
      }
      return Promise.resolve(json(unitConfig(id)));
    }
    if (url.pathname === "/api/governance/warehouse-capacity/") return Promise.resolve(json({
      ...adminConfiguration,
    }));
    if (url.pathname === "/api/governance/warehouse-capacity/audit/") return Promise.resolve(json(auditPage));
    if (url.pathname === "/api/warehouse-capacity/") return Promise.resolve(json({
      results: [{ total_floor_area_m2: "DO_NOT_FETCH_OR_RENDER" }],
    }));
    throw new Error(`Unexpected governance request ${url.pathname}`);
  };
});

afterEach(async () => {
  try { await act(async () => root.unmount()); }
  finally { host.remove(); globalThis.fetch = nativeFetch; }
});

test("Governance destinations follow independent gates; a nonstaff panel manager retains Reports and its existing Favorite contract", async () => {
  const regular = sidebarSections({ isStaff: false, isSuperuser: false, canManageReportPanels: false }).find((item) => item.id === "governance");
  const manager = sidebarSections({ isStaff: false, isSuperuser: false, canManageReportPanels: true }).find((item) => item.id === "governance");
  const staff = sidebarSections({ isStaff: true, isSuperuser: false, canManageReportPanels: false }).find((item) => item.id === "governance");
  const rootAccess = sidebarSections({ isStaff: true, isSuperuser: true, canManageReportPanels: false }).find((item) => item.id === "governance");
  assert.deepEqual(regular?.children.map(({ id }) => id), ["main"],
    "Ordinary accounts have one authority overview, not a duplicate authority page");
  assert.ok(manager?.children.some(({ id }) => id === "report-panels"));
  assert.equal(manager?.children.some(({ id }) => id === "capacity-admin"), false);
  assert.ok(staff?.children.some(({ id }) => id === "presentation"));
  assert.equal(staff?.children.some(({ id }) => id === "report-panels"), false);
  assert.ok(rootAccess?.children.some(({ id }) => id === "capacity-audit"));

  activeStaff = false;
  panelManager = true;
  await render(<App />);
  await flush();
  await flush();
  assert.equal(appFavorite("governance"), undefined, "The app does not add Governance to server-available Favorites");
  assert.ok(calls.some((url) => url.pathname === "/api/favorites/" && url.searchParams.get("unit") === "1"));
  const reportsToggle = [...host.querySelectorAll<HTMLButtonElement>(".nav-section-toggle")]
    .find((button) => button.title === "Reports");
  if (reportsToggle?.getAttribute("aria-expanded") !== "true") await click(reportsToggle ?? null);
  const reportsChild = [...host.querySelectorAll<HTMLButtonElement>(".section-navigation-children button")]
    .find((button) => button.textContent?.includes("Reports & Dashboards"));
  await click(reportsChild ?? null);
  assert.equal(host.querySelector(".report-panel-manager"), null, "Reports does not duplicate the management form");
  await click(host.querySelector<HTMLButtonElement>(".report-manage-link button"));
  assert.equal(window.location.hash, "#/governance/report-panels");
  assert.ok(host.querySelector(".report-panel-manager"), "Reports links to the dedicated management page independently of staff");
  assert.match(host.textContent ?? "", /Assign or revoke which report panels appear/);
  assert.equal(calls.some((url) => url.pathname === "/api/governance/business-unit-palettes/"), false);
  assert.equal(calls.some((url) => url.pathname === "/api/governance/purchasing/1/"), false);
});

for (const manager of [false, true]) test(`Reporting governance explains existing panel authority accurately (manager=${manager})`, async () => {
  panelManager = manager;
  await render(<App />);
  await navigateGovernanceChild("Reporting governance");
  const boundary = host.querySelector(".governance-report-boundary");
  assert.ok(boundary);
  const text = boundary.textContent ?? "";
  const link = [...boundary.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent === "Manage report panels");
  if (manager) {
    assert.doesNotMatch(text, /Panel management is not part of this account/);
    assert.match(text, /This account can manage panel visibility/);
    assert.ok(link, "Existing managers have a contextual link to the dedicated function");
    assert.equal(host.querySelector(".report-panel-manager"), null);
    await click(link);
    assert.equal(window.location.hash, "#/governance/report-panels");
    assert.ok(host.querySelector(".report-panel-manager"));
    await act(async () => { window.history.back(); await new Promise((resolve) => setTimeout(resolve, 20)); });
    await flush();
    assert.equal(window.location.hash, "#/governance/reporting");
    assert.ok(host.querySelector(".governance-report-boundary"));
  } else {
    assert.match(text, /Panel management is not part of this account/);
    assert.equal(link, undefined);
    assert.equal(host.querySelector(".report-panel-manager"), null);
  }
  assert.equal(calls.some((url) => url.pathname === "/api/governance/reports/"), false);
});

test("Governance Favorite opens its overview and each authorized child mounts only its function", async () => {
  activeSuperuser = true;
  await render(<App />);
  await click(appFavorite("governance") ?? null);
  assert.match(host.textContent ?? "", /Authority, clearly separated/);
  assert.ok(host.querySelector("#governance-workspace-title"));
  assert.ok(host.querySelector('[data-governance-pane="authority"]'));
  assert.equal(host.querySelector('[data-governance-pane="palettes"]'), null);
  assert.equal(host.querySelector(".palette-admin"), null, "Governance overview does not mount configuration panes.");
  assert.equal(calls.some((url) => url.pathname === "/api/governance/purchasing/1/"), false);
  await navigateGovernanceChild("Business-unit palettes");
  assert.ok(host.querySelector(".palette-admin"));
  assert.equal(host.querySelector(".governance-purchasing-review"), null);
  assert.equal(calls.filter((url) => url.pathname === "/api/governance/business-unit-palettes/").length, 1);
  await navigateGovernanceChild("Purchasing configuration review");
  assert.ok(host.querySelector(".governance-purchasing-review"));
  assert.equal(host.querySelector(".palette-admin"), null);
  assert.equal(calls.filter((url) => url.pathname === "/api/governance/purchasing/1/").length, 1);
  await navigateGovernanceChild("Reporting governance");
  assert.ok(host.querySelector(".governance-report-boundary"));
  assert.equal(host.querySelector(".governance-purchasing-review"), null);
  await navigateGovernanceChild("Physical locations & assignments");
  assert.ok(host.querySelector(".capacity-section"));
  assert.equal(host.querySelector(".palette-admin"), null);
  await navigateGovernanceChild("Warehouse capacity audit");
  assert.ok(host.querySelector(".capacity-audit-register"));
  assert.equal(host.querySelector(".capacity-section"), null);
  assert.equal(calls.some((url) => url.pathname === "/api/governance/reports/"), false, "Governance does not load broad report/data grant administration");
});

test("Superuser-only physical authority is independent of staff presentation and purchasing gates", async () => {
  activeStaff = false;
  activeSuperuser = true;
  await render(<App />);
  const toggle = [...host.querySelectorAll<HTMLButtonElement>(".nav-section-toggle")]
    .find((button) => button.title === "Governance & Administration");
  if (toggle?.getAttribute("aria-expanded") !== "true") await click(toggle ?? null);
  await navigateGovernanceChild("Governance & Administration");
  assert.ok(host.querySelector(".governance-boundaries"));
  assert.equal(host.querySelector(".capacity-section"), null);
  await navigateGovernanceChild("Physical locations & assignments");
  assert.ok(host.querySelector(".capacity-section"));
  assert.equal(host.querySelector(".palette-admin"), null);
  assert.equal(host.querySelector(".governance-purchasing-review"), null);
  assert.equal(calls.some((url) => url.pathname === "/api/governance/purchasing/1/"), false);
  assert.ok(calls.some((url) => url.pathname === "/api/governance/warehouse-capacity/"));
});

test("Purchasing configuration does not cross business units on first commit or a delayed Unit A response", async () => {
  delayedUnitOne = null;
  const commits: { unit: number; text: string }[] = [];
  const node = (unit: number) => <Profiler id={`unit-${unit}`} onRender={() => commits.push({ unit, text: host.textContent ?? "" })}>
    <PurchasingGovernanceReview selectedUnitId={unit} selectedUnitName={`TEST ONLY Unit ${unit}`} isStaff />
  </Profiler>;
  delayedUnitOne = (() => {}) as (response: Response) => void;
  await render(node(1));
  // Allow the request to enter the held response branch.
  await flush();
  const beforeTwo = commits.length;
  await render(node(2));
  const firstTwo = commits.slice(beforeTwo).find((commit) => commit.unit === 2);
  assert.ok(firstTwo);
  assert.doesNotMatch(firstTwo.text, /config for unit 1/);
  assert.doesNotMatch(firstTwo.text, /TEST ONLY Unit 1/);
  assert.match(host.textContent ?? "", /TEST ONLY config for unit 2/);
  const late = delayedUnitOne;
  assert.ok(late);
  await act(async () => late(json(unitConfig(1))));
  await flush();
  assert.doesNotMatch(host.textContent ?? "", /TEST ONLY config for unit 1/);
  assert.match(host.textContent ?? "", /TEST ONLY config for unit 2/);
});

test("Purchasing permission denial and staff visibility loss clear prior configuration", async () => {
  await render(<PurchasingGovernanceReview selectedUnitId={2} selectedUnitName="TEST ONLY Unit 2" isStaff />);
  assert.match(host.textContent ?? "", /TEST ONLY config for unit 2/);
  globalThis.fetch = (path, options = {}) => {
    const url = new URL(String(path), "https://governance.test.invalid");
    calls.push(url);
    if (url.pathname === "/api/governance/purchasing/2/") return Promise.resolve(json({ detail: "TEST ONLY purchasing 403" }, 403));
    if (url.pathname === "/api/governance/purchasing/1/") return Promise.resolve(json(unitConfig(1)));
    return Promise.resolve(json({}));
  };
  await render(<PurchasingGovernanceReview selectedUnitId={1} selectedUnitName="TEST ONLY Unit 1" isStaff />);
  await render(<PurchasingGovernanceReview selectedUnitId={2} selectedUnitName="TEST ONLY Unit 2" isStaff />);
  assert.match(host.textContent ?? "", /TEST ONLY purchasing 403/);
  assert.doesNotMatch(host.textContent ?? "", /TEST ONLY config for unit 2/);
  await render(<PurchasingGovernanceReview selectedUnitId={2} selectedUnitName="TEST ONLY Unit 2" isStaff={false} />);
  assert.match(host.textContent ?? "", /Staff access required/);
  assert.doesNotMatch(host.textContent ?? "", /TEST ONLY config for unit 2|TEST ONLY purchasing 403/);
});

test("Superuser administration is metadata-only in Governance and shows unconfigured locations without measurement reads", async () => {
  await render(<>
    <WarehouseCapacity isSuperuser administrationOnly csrfToken="TEST_ONLY_CSRF" />
    <WarehouseCapacityAuditRegister isSuperuser />
  </>);
  assert.ok(calls.some((url) => url.pathname === "/api/governance/warehouse-capacity/"));
  assert.ok(calls.some((url) => url.pathname === "/api/governance/warehouse-capacity/audit/"));
  assert.equal(calls.some((url) => url.pathname === "/api/warehouse-capacity/"), false);
  assert.match(host.textContent ?? "", /TEST ONLY physical location/);
  assert.match(host.textContent ?? "", /TEST ONLY unconfigured location/);
  assert.match(host.textContent ?? "", /Assignment revoked/);
  assert.doesNotMatch(host.textContent ?? "", /DO_NOT_FETCH_OR_RENDER|DO_NOT_RENDER_MEASUREMENTS/);
  assert.equal((host.querySelector('[aria-label="Capacity assignment physical location"]') as HTMLSelectElement | null)?.value ?? "", "");
  assert.equal((host.querySelector('[aria-label="Capacity assignment synthetic account"]') as HTMLSelectElement | null)?.value ?? "", "");
  const configureSelect = host.querySelector<HTMLSelectElement>('[aria-label="Configure physical capacity location"]');
  assert.ok(configureSelect);
  await setSelect(configureSelect, "32");
  const capacityForms = host.querySelectorAll<HTMLFormElement>(".capacity-section form");
  assert.equal(capacityForms.length, 2);
  await submit(capacityForms[0]);
  assert.ok(calls.some((url) => url.pathname === "/api/governance/warehouse-capacity/" && url.search === ""));
  await setSelect(host.querySelector<HTMLSelectElement>('[aria-label="Assignment physical location"]')!, "31");
  await setSelect(host.querySelector<HTMLSelectElement>('[aria-label="Capacity assignment synthetic account"]')!, "71");
  await submit(host.querySelectorAll<HTMLFormElement>(".capacity-section form")[1]);
  assert.ok(calls.some((url) => url.pathname === "/api/governance/warehouse-capacity/assignments/"));
  assert.equal(calls.some((url) => url.pathname === "/api/warehouse-capacity/"), false, "Governance never calls the measurement endpoint, including after admin writes");
});
