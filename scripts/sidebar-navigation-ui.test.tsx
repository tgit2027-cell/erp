import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import App from "../src/App";
import SectionNavigation from "../src/SectionNavigation";
import { coreSections, sidebarSections } from "../src/sidebar-destinations";

const originalFetch = globalThis.fetch;
const units = [
  { id: 1, code: "TVL", name: "TEST ONLY Tivoli", theme_palette: "tivoli" },
  { id: 2, code: "RBB", name: "TEST ONLY Roche", theme_palette: "roche" },
  { id: 3, code: "NAT", name: "TEST ONLY Natuzzi", theme_palette: "natuzzi" },
];
const purchasing = { destination: "purchasing", label: "Purchasing Workbench" };
const authorizedQuote = {
  quote_id: 501, business_unit_id: 1, kind: "stock", status: "approved", customer_id: 19,
  discount_percent: "0.00", total_list_value: "87.50", total_discount: "0.00",
  total_quote_value: "87.50", payment_plan: "full", downpayment_percent: null,
  lines: [], special_order_value_bands_configured: false, payments: [], approvals: [], holds: [], events: [],
};
let root, host, calls, profile, selectedUnit, pinned, allowStock;
const json = (data, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { "Content-Type": "application/json" },
});
const flush = async () => act(async () => { await new Promise(setImmediate); });
async function click(element) {
  assert.ok(element, "Expected rendered navigation control");
  await act(async () => element.click());
  await flush();
}
function group(id) {
  const name = coreSections.find((section) => section.id === id).name;
  return [...host.querySelectorAll(".section-navigation-group")]
    .find((node) => node.querySelector(".nav-section-toggle").title === name);
}
const toggle = (id) => group(id).querySelector(".nav-section-toggle");
async function open(id) {
  if (toggle(id).getAttribute("aria-expanded") !== "true") await click(toggle(id));
}
async function choose(id, label) {
  await open(id);
  await click([...group(id).querySelectorAll(".section-navigation-children button")]
    .find((node) => node.getAttribute("aria-label") === label));
}
const currentChild = (id) => group(id).querySelector('[aria-current="page"]');
const allReadOnly = () => {
  assert.ok(calls.every(({ options }) => (options.method ?? "GET") === "GET"),
    "Navigation/disclosure must not submit any transaction or permission mutation");
};
async function mount() {
  await act(async () => root.render(<App />));
  await flush();
}
beforeEach(() => {
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  calls = []; selectedUnit = 1; pinned = false; allowStock = false;
  profile = { authenticated: true, username: "TEST_ONLY_sidebar_reader", is_staff: false,
    is_superuser: false, can_manage_report_panels: false };
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, options });
    if (url.pathname === "/api/auth/csrf/") return json({ csrfToken: "TEST_ONLY_CSRF" });
    if (url.pathname === "/api/auth/me/") return json(profile);
    if (url.pathname === "/api/organization/") return json({
      company: { name: "TEST ONLY company", code: "TEST" },
      selected_unit: units[selectedUnit - 1], units, locations: [],
    });
    if (/^\/api\/units\/[123]\/select\/$/.test(url.pathname)) {
      assert.equal(options.method, "POST");
      selectedUnit = Number(url.pathname.split("/")[3]);
      return json({ selected_unit: units[selectedUnit - 1] });
    }
    if (url.pathname === "/api/favorites/") {
      if (options.method === "POST") {
        assert.deepEqual(JSON.parse(options.body), { destination: "purchasing" });
        pinned = true; return json({ favorite: purchasing }, 201);
      }
      return json({ available_destinations: [purchasing], favorites: pinned ? [purchasing] : [] });
    }
    if (url.pathname === "/api/favorites/purchasing/" && options.method === "DELETE") {
      pinned = false; return new Response(null, { status: 204 });
    }
    assert.equal(options.method ?? "GET", "GET", `No transaction or grant writes: ${url.pathname}`);
    if (url.pathname === "/api/purchasing/quotes/501/") return json(authorizedQuote);
    if (url.pathname === "/api/stock-balances/" && allowStock) return json({
      results: [{
        id: selectedUnit, item_code: `TEST-LEDGER-${selectedUnit}`, item_description: "TEST ONLY item",
        brand_name: units[selectedUnit - 1].name, variant_sku: "TEST-VARIANT",
        variant_description: "TEST ONLY variant", location_code: `TEST-LOC-${selectedUnit}`,
        location_name: "TEST ONLY warehouse", unit_code: units[selectedUnit - 1].code,
        unit_name: units[selectedUnit - 1].name, quantity: "2.500", is_synthetic: true,
      }],
      pagination: { page: 1, page_size: 50, total_count: 1, total_pages: 1, next_page: null, previous_page: null },
    });
    // The UI must continue through existing denied API states; links never authorize.
    return json({ detail: "TEST ONLY existing permission denied", unit_cost: "TEST_ONLY_PRIVATE_COST" }, 403);
  };
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); globalThis.fetch = originalFetch;
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
});

test("sidebar starts compact with all nine unchanged identities Overview and Favorites; disclosure makes no requests", async () => {
  await mount();
  assert.equal(host.querySelectorAll(".section-navigation-group").length, 9);
  assert.deepEqual([...host.querySelectorAll(".nav-section-toggle")].map((node) => node.title),
    coreSections.map((section) => section.name));
  const initial = calls.length;
  for (const section of coreSections) {
    const header = toggle(section.id);
    assert.equal(header.getAttribute("aria-expanded"), "false");
    assert.equal(document.getElementById(header.getAttribute("aria-controls")).hidden, true);
  }
  await open("purchasing");
  assert.equal(toggle("purchasing").getAttribute("aria-expanded"), "true");
  await open("inventory");
  assert.equal(toggle("purchasing").getAttribute("aria-expanded"), "false", "Only one compact group is open");
  await click(toggle("inventory"));
  assert.equal(toggle("inventory").getAttribute("aria-expanded"), "false");
  assert.equal(calls.length, initial, "Disclosure alone has no route, scope, or data effects");
  assert.equal(host.querySelector("h1").textContent, "Overview");
  assert.ok(host.querySelector(".favorites-label"));
  assert.ok(host.querySelector(".my-page-link"));
});

test("native disclosure buttons support keyboard expansion collapse focus and active-child semantics", async () => {
  const navigate = [];
  await act(async () => root.render(<SectionNavigation
    sections={sidebarSections({ isStaff: false, isSuperuser: false, canManageReportPanels: false })}
    activeSection="inventory" activeChild="packing" navigationRevision={1}
    onNavigate={(...args) => navigate.push(args)} renderFavorite={() => null}
  />));
  const header = toggle("inventory");
  assert.equal(header.tagName, "BUTTON");
  assert.equal(header.type, "button"); assert.equal(header.tabIndex, 0);
  assert.equal(header.getAttribute("aria-expanded"), "true");
  assert.equal(currentChild("inventory").textContent, "Packing-list preview");
  header.focus();
  for (const [key, expanded] of [["ArrowLeft", false], ["ArrowRight", true]]) {
    await act(async () => header.dispatchEvent(new window.KeyboardEvent("keydown", { key, bubbles: true })));
    assert.equal(header.getAttribute("aria-expanded"), String(expanded));
    assert.equal(document.getElementById(header.getAttribute("aria-controls")).hidden, !expanded);
    assert.equal(document.activeElement, header);
  }
  assert.ok([...group("inventory").querySelectorAll("li")].every((li) => li.parentElement.tagName === "UL"));
  await click(currentChild("inventory"));
  assert.deepEqual(navigate, [["inventory", "packing"]]);
  assert.equal(calls.length, 0);
});

test("child shortcuts render distinct function views and active navigation reveals its parent without posting", async () => {
  await mount();
  await choose("purchasing", "Quote lookup");
  assert.equal(host.querySelector("h1").textContent, "Quote lookup");
  assert.ok(host.querySelector('[data-workspace-view="purchasing/quote"]'));
  assert.equal(host.querySelector(".pw-quote-form")?.closest("[hidden]"), null);
  const quoteId = host.querySelector<HTMLInputElement>("#pw-quote-id");
  assert.ok(quoteId);
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set?.call(quoteId, "731");
    quoteId.dispatchEvent(new Event("input", { bubbles: true }));
  });
  assert.equal(currentChild("purchasing").textContent, "Quote lookup");
  await choose("purchasing", "Transaction workbench");
  assert.ok(host.querySelector('[data-workspace-view="purchasing/transactions"]'));
  assert.equal(host.querySelector(".pw-transactions")?.hidden, false);
  assert.equal(host.querySelector(".pw-quote-card")?.hidden, false,
    "The transaction function owns its quote lookup instead of navigating away.");
  assert.match(host.querySelector(".pw-quote-card h2")?.textContent ?? "", /Select a quote for this transaction/);
  assert.equal(host.querySelector("#pw-receipt-title")?.closest(".pw-transaction-panel")?.hidden, false,
    "The focused transaction view keeps its actual receipt form in view.");
  assert.equal(host.querySelector("#pw-receipt-supplier")?.closest("form")?.hidden, false);
  assert.equal(host.querySelector("#pw-create-quote-title")?.closest(".pw-transaction-panel")?.hidden, false,
    "Quote creation remains inside the transaction function.");
  assert.equal(host.querySelector("#pw-commitment-create-title")?.closest(".pw-action-subform")?.hidden, false);
  assert.equal(host.querySelector(".pw-selected-quote-actions")?.hidden, false);
  const transactionQuoteId = host.querySelector<HTMLInputElement>("#pw-quote-id");
  assert.ok(transactionQuoteId);
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set?.call(transactionQuoteId, "501");
  await act(async () => transactionQuoteId.dispatchEvent(new Event("input", { bubbles: true })));
  await act(async () => host.querySelector<HTMLFormElement>(".pw-quote-form")
    ?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();
  assert.equal(host.querySelector(".pw-selected-quote-actions h4")?.textContent, "Request temporary hold",
    "Loading a quote shows its existing eligible action form without leaving Transactions.");
  assert.ok(host.querySelector('.pw-selected-quote-actions form[aria-label="Record simulated payment"]')
    || [...host.querySelectorAll(".pw-selected-quote-actions form")].some((form) => form.textContent?.includes("Record simulated payment")));
  assert.equal(window.location.hash, "#/purchasing/transactions");
  await choose("purchasing", "Quote lookup");
  assert.equal(host.querySelector<HTMLInputElement>("#pw-quote-id")?.value, "501",
    "Focused navigation preserves the already-mounted quote lookup draft.");
  await choose("purchasing", "Purchasing records");
  assert.ok(host.querySelector('[data-workspace-view="purchasing/registers"]'));
  assert.equal(host.querySelector(".pr-tabs")?.closest("[hidden]"), null);
  await choose("inventory", "Packing-list preview");
  assert.equal(host.querySelector('[data-workspace-view="inventory/packing"]')?.hidden, false);
  assert.equal(host.querySelector(".packing-preview")?.closest("[hidden]"), null);
  await choose("inventory", "Warehouse capacity");
  assert.equal(host.querySelector('[data-workspace-view="inventory/capacity"]')?.hidden, false);
  assert.equal(host.querySelector(".capacity-section")?.closest("[hidden]"), null);
  allReadOnly();
});

test("Retail, Procurement and Delivery main views are useful catalog-driven launchers", async () => {
  await mount();
  const cases = [
    ["retail", "Retail & Sales", "retail/quote"],
    ["procurement", "Procurement & Imports", "procurement/commitments"],
    ["delivery", "Delivery & After-Sales", "delivery/quote"],
  ] as const;
  for (const [section, mainLabel, firstRoute] of cases) {
    await choose(section, mainLabel);
    const launcher = host.querySelector<HTMLElement>(`.pw-overview-launcher[aria-label="${section} functions"]`);
    assert.ok(launcher, `${section} main presents real child destinations`);
    const choices = sidebarSections({ isStaff: false, isSuperuser: false, canManageReportPanels: false })
      .find((item) => item.id === section)?.children.filter((item) => item.id !== "main") ?? [];
    assert.deepEqual([...launcher.querySelectorAll("button strong")].map((item) => item.textContent), choices.map((item) => item.label));
    await click(launcher.querySelector("button"));
    assert.equal(window.location.hash, `#/${firstRoute}`);
    assert.ok(host.querySelector(`[data-workspace-view="${firstRoute}"]`));
  }
});

test("every implemented child across the five workspaces renders its own focused function view", async () => {
  profile.is_staff = true;
  profile.is_superuser = true;
  profile.can_manage_report_panels = true;
  const routes: [string, string, string, string][] = [
    ["retail", "Retail & Sales", "Create a quote", "#pw-create-quote-title"],
    ["retail", "Retail & Sales", "Quote collection & holds", "#pw-quote-actions-title"],
    ["retail", "Retail & Sales", "Authorized quote register", ".pr-tabs"],
    ["retail", "Retail & Sales", "Retail & Sales", "#purchasing-workbench-title"],
    ["procurement", "Procurement & Imports", "Create purchase commitment", "#pw-commitment-create-title"],
    ["procurement", "Procurement & Imports", "Commitment receipt progress", "#pw-commitment-progress-title"],
    ["procurement", "Procurement & Imports", "Prepare imported-goods receipt", "#pw-receipt-title"],
    ["procurement", "Procurement & Imports", "Packing-list mapping & review", ".packing-preview"],
    ["procurement", "Procurement & Imports", "Receipt & landed-charge registers", ".pr-tabs"],
    ["procurement", "Procurement & Imports", "Procurement & Imports", "#purchasing-workbench-title"],
    ["inventory", "Inventory & Warehouses", "Inventory quantities", ".inventory-heading"],
    ["inventory", "Inventory & Warehouses", "Permitted locations", ".inventory-locations-area"],
    ["inventory", "Inventory & Warehouses", "Prepare imported-goods receipt", "#pw-receipt-title"],
    ["inventory", "Inventory & Warehouses", "Purchase commitments", "#pw-commitment-create-title"],
    ["inventory", "Inventory & Warehouses", "Commitment receipt progress", "#pw-commitment-progress-title"],
    ["inventory", "Inventory & Warehouses", "Packing-list preview", ".packing-preview"],
    ["inventory", "Inventory & Warehouses", "Inventory & transfer registers", ".inventory-registers"],
    ["inventory", "Inventory & Warehouses", "Warehouse capacity", ".capacity-section"],
    ["delivery", "Delivery & After-Sales", "Authorized quote lookup", ".pw-quote-form"],
    ["delivery", "Delivery & After-Sales", "Readiness & handover actions", "#pw-quote-actions-title"],
    ["delivery", "Delivery & After-Sales", "Quote register", ".pr-tabs"],
    ["delivery", "Delivery & After-Sales", "Storage-charge register", ".pr-tabs"],
    ["delivery", "Delivery & After-Sales", "Reservation register", ".pr-tabs"],
    ["delivery", "Delivery & After-Sales", "Delivery register", ".pr-tabs"],
    ["delivery", "Delivery & After-Sales", "Sales-return intake", "#pw-sales-return-summary"],
    ["governance", "Governance & Administration", "Governance & Administration", '[data-governance-pane="authority"]'],
    ["governance", "Governance & Administration", "Manage report panels", ".report-panel-manager"],
    ["purchasing", "Purchasing Workbench", "Purchasing Workbench", "#purchasing-workbench-title"],
    ["purchasing", "Purchasing Workbench", "Quote lookup", ".pw-quote-form"],
    ["purchasing", "Purchasing Workbench", "Transaction workbench", ".pw-transactions"],
    ["purchasing", "Purchasing Workbench", "Purchasing records", ".pr-tabs"],
    ["reports", "Reports", "Reports & Dashboards", ".reports-area"],
    ["finance", "Finance & Accounting", "Finance & Accounting", "#finance-status-title"],
    ["hr", "HR & Payroll", "HR & Payroll", ".planned-panel"],
  ];
  await mount();
  for (const [section, _parentName, label, target] of routes) {
    await choose(section, label);
    const expectedRoute = `${section}/${sidebarSections({
      isStaff: profile.is_staff,
      isSuperuser: profile.is_superuser,
      canManageReportPanels: profile.can_manage_report_panels,
    }).find((item) => item.id === section)?.children.find((child) => child.label === label)?.id}`;
    const view = host.querySelector<HTMLElement>(`[data-workspace-view="${expectedRoute}"]`);
    assert.ok(view, `Expected distinct rendered view ${expectedRoute}`);
    assert.equal(view.hidden, false);
    assert.equal(host.querySelectorAll("[data-workspace-view]:not([hidden])").length, 1, `${expectedRoute} should have one active marker: ${[...host.querySelectorAll("[data-workspace-view]:not([hidden])")].map((node) => node.getAttribute("data-workspace-view")).join(",")}`);
    const content = host.querySelector<HTMLElement>(target);
    assert.ok(content, `Expected ${target} in ${expectedRoute}`);
    assert.equal(content.closest("[hidden]"), null, `${target} must be outside every hidden/inert pane`);
    assert.equal(currentChild(section)?.getAttribute("aria-current"), "page");
    assert.equal(window.location.hash, `#/${expectedRoute}`);
    if (expectedRoute === "retail/quote") {
      const crossWorkspace = host.querySelector<HTMLButtonElement>(".pw-retail-delivery-link button");
      assert.ok(crossWorkspace, "The retail quote view retains its delivery-progress crosslink.");
      await click(crossWorkspace);
      assert.equal(window.location.hash, "#/delivery/quote");
      await choose(section, label);
      assert.ok(host.querySelector('[data-workspace-view="retail/quote"]'));
    }
  }
});

test("cold direct hashes survive app loading; denied and unknown destinations fail closed without governance reads", async () => {
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#/procurement/progress`);
  await mount();
  assert.ok(host.querySelector('[data-workspace-view="procurement/progress"]'));
  assert.equal(host.querySelector('[data-workspace-link="procurement/progress"]')?.getAttribute("aria-current"), "page");

  await act(async () => {
    window.history.pushState(null, "", `${window.location.pathname}${window.location.search}#/governance/capacity-admin`);
    window.dispatchEvent(new window.PopStateEvent("popstate"));
  });
  await flush();
  assert.ok(host.querySelector("[data-workspace-unavailable]"));
  assert.equal(host.querySelector(".capacity-section"), null);
  assert.equal(calls.some(({ url }) => url.pathname.startsWith("/api/governance/")), false);

  await act(async () => {
    window.history.pushState(null, "", `${window.location.pathname}${window.location.search}#/retail/not-a-function`);
    window.dispatchEvent(new window.PopStateEvent("popstate"));
  });
  await flush();
  assert.ok(host.querySelector("[data-workspace-unavailable]"));
  assert.equal(host.querySelector('[data-workspace-view="retail/not-a-function"]'), null);
});

test("native hash changes and history back/forward select the matching focused route", async () => {
  await mount();
  await act(async () => {
    window.location.hash = "#/retail/main";
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
  await flush();
  assert.ok(host.querySelector('[data-workspace-view="retail/main"]'));
  await act(async () => {
    window.location.hash = "#/retail/quote";
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
  await flush();
  assert.ok(host.querySelector('[data-workspace-view="retail/quote"]'));
  await act(async () => {
    window.history.back();
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
  await flush();
  assert.equal(window.location.hash, "#/retail/main");
  assert.ok(host.querySelector('[data-workspace-view="retail/main"]'));
  await act(async () => {
    window.history.forward();
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
  await flush();
  assert.equal(window.location.hash, "#/retail/quote");
  assert.ok(host.querySelector('[data-workspace-view="retail/quote"]'));
});

test("pin unpin and My Page keep the current purchasing route and reveal a collapsed active parent", async () => {
  await mount();
  await click(host.querySelector('[aria-label="Add Purchasing Workbench to favorites"]'));
  assert.equal(pinned, true);
  await click(host.querySelector(".my-page-link"));
  await click(host.querySelector(".my-favorite-open"));
  assert.equal(toggle("purchasing").getAttribute("aria-expanded"), "true");
  assert.equal(currentChild("purchasing").textContent, "Purchasing Workbench");
  assert.equal(host.querySelector(".pw-overview-launcher")?.hidden, false, "Section favorite opens the useful workbench overview.");
  await click(toggle("purchasing"));
  await click(host.querySelector(".favorite-shortcut .nav-item"));
  assert.equal(toggle("purchasing").getAttribute("aria-expanded"), "true",
    "Reopening the same section from Favorites must reveal it again");
  await click(host.querySelector('[aria-label="Remove Purchasing Workbench from favorites"]'));
  assert.equal(pinned, false);
  assert.equal(host.querySelector(".favorite-shortcut"), null);
  assert.deepEqual(calls.filter(({ options }) => ["POST", "DELETE"].includes(options.method))
    .map(({ url, options }) => [url.pathname, options.method]), [
    ["/api/favorites/", "POST"], ["/api/favorites/purchasing/", "DELETE"],
  ]);
});

test("Finance availability stays informational through existing BU/theme switching", async () => {
  window.history.replaceState(null, "", `${window.location.pathname}#/finance/main`);
  await act(async () => root.render(<App />)); await flush(); await flush();
  assert.ok(host.querySelector("#finance-status-title"));
  const selector = host.querySelector('[aria-label="Business unit"]') as HTMLSelectElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value")!.set!.call(selector, "2");
    selector.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush(); await flush();
  assert.equal(selector.value, "2");
  assert.equal(document.documentElement.dataset.unitTheme, "roche");
  assert.equal(window.location.hash, "#/finance/main");
  assert.ok(host.querySelector("#finance-status-title"));
  assert.equal(host.querySelector(".page-heading h1")?.textContent, "Finance & Accounting");
  assert.ok(calls.every(({ url }) => !url.pathname.includes("finance") && !url.pathname.includes("/lab/")));
  assert.deepEqual(calls.filter(({ options }) => options.method && options.method !== "GET").map(({ url }) => url.pathname),
    ["/api/units/2/select/"], "Only the mocked existing BU selection is written; no transactions or grants.");
});

test("child navigation retains selected BU and its theme while existing unit selection reloads scoped ledger only", async () => {
  allowStock = true;
  await mount();
  await choose("inventory", "Inventory quantities");
  assert.equal(document.documentElement.dataset.unitTheme, "tivoli");
  assert.match(host.querySelector(".inventory-view tbody").textContent, /TEST-LEDGER-1/);
  for (const [id, theme] of [[2, "roche"], [3, "natuzzi"]]) {
    const selector = host.querySelector('[aria-label="Business unit"]');
    await act(async () => {
      Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set.call(selector, String(id));
      selector.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();
    assert.equal(selector.value, String(id));
    assert.equal(document.documentElement.dataset.unitTheme, theme);
    assert.equal(currentChild("inventory").textContent, "Inventory quantities");
    assert.match(host.querySelector(".inventory-view tbody").textContent, new RegExp(`TEST-LEDGER-${id}`));
    assert.doesNotMatch(host.querySelector(".inventory-view tbody").textContent, /TEST-LEDGER-1/);
    await choose("inventory", "Packing-list preview");
    await choose("inventory", "Inventory quantities");
    assert.equal(host.querySelector('[aria-label="Business unit"]').value, String(id));
    assert.equal(document.documentElement.dataset.unitTheme, theme);
  }
  assert.deepEqual(calls.filter(({ options }) => options.method === "POST")
    .map(({ url }) => url.pathname), ["/api/units/2/select/", "/api/units/3/select/"]);
  assert.equal(calls.filter(({ url }) => url.pathname === "/api/stock-balances/").at(-1).url.searchParams.get("unit"), "3");
});

test("ordinary navigation cannot reveal staff controls costs capacity or enable denied purchasing actions", async () => {
  await mount();
  await choose("governance", "Governance & Administration");
  assert.equal(host.querySelector(".palette-admin"), null);
  assert.equal(host.querySelector(".capacity-audit-register"), null);
  assert.equal(host.querySelector('[aria-label="Manage report panels"]'), null);
  await choose("inventory", "Warehouse capacity");
  assert.match(host.querySelector(".capacity-section").textContent, /denied|unassigned|unavailable/i);
  await choose("reports", "Reports & Dashboards");
  assert.match(host.querySelector(".reports-area").textContent, /denied|unavailable/i);
  await choose("purchasing", "Transaction workbench");
  assert.match(host.querySelector(".pw-transactions").textContent, /Transactions unavailable/);
  assert.equal(host.querySelector('.pw-transactions form button[type="submit"]')?.disabled, true);
  assert.doesNotMatch(host.textContent, /TEST_ONLY_PRIVATE_COST/);
  assert.ok(calls.every(({ url }) => !url.pathname.startsWith("/api/governance/")),
    "Ordinary links do not invoke staff-only governance endpoints");
  allReadOnly();
});

for (const role of ["staff", "panel-manager", "superuser"]) {
  test(`existing ${role} visibility gates only the corresponding configuration shortcuts`, async () => {
    profile.is_staff = role !== "panel-manager";
    profile.is_superuser = role === "superuser";
    profile.can_manage_report_panels = role === "panel-manager";
    await mount();
    await open("governance");
    assert.equal(Boolean(host.querySelector('[aria-label="Business-unit palettes"]')), profile.is_staff);
    assert.equal(Boolean(host.querySelector('[aria-label="Warehouse capacity audit"]')), profile.is_staff && profile.is_superuser);
    assert.equal(Boolean(host.querySelector('[aria-label="Manage report panels"]')), profile.can_manage_report_panels);
    if (profile.can_manage_report_panels) {
      await choose("governance", "Manage report panels");
      assert.ok(host.querySelector('[data-workspace-view="governance/report-panels"]'));
      assert.ok(host.querySelector(".report-panel-manager"));
    }
    if (profile.is_staff) {
      await choose("governance", "Reporting governance");
      assert.equal(host.querySelector('[data-workspace-view="governance/reporting"]')?.hidden, false);
      assert.equal(host.querySelector(".report-admin")?.closest("[hidden]"), null);
    }
    allReadOnly();
  });
}
