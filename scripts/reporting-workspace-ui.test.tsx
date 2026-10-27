import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { PersonalReportingWorkspace } from "../src/PersonalReportingWorkspace";
import { GovernanceReportPanelManager, ReportsArea } from "../src/ReportingArea";
import { useReportData } from "../src/reporting-api";

const filters = { unit: 3, location: 7, period: "monthly", start: "2026-01-01", end: "2026-01-31", page: 1, page_size: 25 };
const saved = { id: 1, name: "Fictional monthly review", panels: ["sales", "best_sellers"], filters, updated_at: "2026-01-01" };
const panels = [{ code: "sales", label: "Sales" }, { code: "best_sellers", label: "Best sellers" }];
const available = saved.panels.map((panel) => ({ panel, available: true, reason: "" }));
let container;
let root;
let calls;
let views;
let detail;
let custom;

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
function report(panel = "sales", label = "SYNTHETIC-ROW") {
  return {
    panel, title: panel, rows: [{ item_id: label }], fields: ["item_id"],
    summary: {}, filters: { start: filters.start, end: filters.end },
    scope: { scope_type: "business_unit", business_unit: { name: "Synthetic unit" }, business_unit_ids: [3] },
    pagination: { page: 1, total_pages: 1, total_count: 1, previous_page: null, next_page: null },
    policy_label: "TEST ONLY", currency: "QAR",
  };
}
function salesExamples({
  example_count = 0,
  illustrative_revenue = "0.00",
  illustrative_quantity = "0.00",
  start = filters.start,
  end = filters.end,
  unitId = 3,
  locationId = null,
} = {}) {
  return {
    panel: "sales_examples",
    title: "Synthetic examples — not operational sales",
    source: "DemoReportSalesFact",
    example_count,
    summary: { illustrative_revenue, illustrative_quantity },
    currency: "QAR",
    filters: {
      start,
      end,
      location_id: locationId,
      unit_id: unitId,
      company_id: null,
    },
    scope: report().scope,
    policy_label: "TEST ONLY — illustrative synthetic examples, not approved accounting policy.",
    note: "DemoReportSalesFact examples are separate from operational SalesReportLine reporting.",
  };
}
async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
async function renderWorkspace(currentFilters = { ...filters, unit: 999, location: 888 }) {
  await act(async () => root.render(
    <PersonalReportingWorkspace csrfToken="synthetic-csrf-placeholder" panels={panels} filters={currentFilters} />
  ));
  await flush();
}
async function renderReportsArea(canManagePanels = false, selectedUnitId = 3) {
  await act(async () => root.render(
    <ReportsArea csrfToken="synthetic-csrf-placeholder" selectedUnitId={selectedUnitId} canManagePanels={canManagePanels} />
  ));
  for (let index = 0; index < 5; index += 1) await flush();
}
function button(text) {
  const found = [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(text));
  assert.ok(found, `Missing button: ${text}`);
  return found;
}
function selectFor(labelText) {
  const label = [...container.querySelectorAll("label")]
    .find((item) => item.textContent?.includes(labelText) && item.querySelector("select"));
  const found = label?.querySelector("select");
  assert.ok(found, `Missing select for: ${labelText}`);
  return found;
}
async function click(element) {
  await act(async () => element.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await flush();
}
async function choose(select, value) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(select), "value")!.set!.call(select, value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
}
async function input(label, value) {
  const element = [...container.querySelectorAll("label")].find((item) => item.textContent?.includes(label))?.querySelector("input");
  assert.ok(element, `Missing input: ${label}`);
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function submit() {
  await act(async () => container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();
}
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve: (value) => resolve(value) };
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  views = [{ ...saved }];
  detail = { ...saved, availability: available };
  custom = null;
  globalThis.fetch = async (path, options = {}) => {
    const url = String(path);
    calls.push({ url, ...options });
    if (custom) {
      const response = custom(url, options);
      if (response !== undefined) return response;
    }
    if (url === "/api/report-views/" && options.method === "POST") {
      const body = JSON.parse(options.body);
      views = [{ ...saved, ...body }];
      detail = { ...views[0], availability: available };
      return json(views[0], 201);
    }
    if (url === "/api/report-views/1/" && options.method === "PUT") {
      views = [{ ...saved, ...JSON.parse(options.body) }];
      detail = { ...views[0], availability: available };
      return json(views[0]);
    }
    if (url === "/api/report-views/1/" && options.method === "DELETE") {
      views = [];
      return new Response(null, { status: 204 });
    }
    if (url === "/api/report-views/") return json({ views });
    if (url.startsWith("/api/report-views/")) return json(detail);
    if (url.startsWith("/api/reports/sales-examples/")) return json(salesExamples());
    if (url.startsWith("/api/reports/")) return json(report(url.split("/")[3]));
    throw new Error(`Unexpected request ${url}`);
  };
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

test("save sends only name, assigned panel order and retained filters with CSRF", async () => {
  views = [];
  await renderWorkspace(filters);
  assert.match(container.textContent, /No saved views yet/);
  await click(button("Create saved view"));
  await input("View name", "Synthetic ordered view");
  const checkboxes = [...container.querySelectorAll('input[type="checkbox"]')];
  assert.equal(checkboxes.length, 2);
  for (const checkbox of checkboxes) await click(checkbox);
  await click(container.querySelector('[aria-label="Move Best sellers up"]'));
  await submit();
  const request = calls.find((item) => item.method === "POST");
  assert.deepEqual(JSON.parse(request.body), {
    name: "Synthetic ordered view", panels: ["best_sellers", "sales"], filters,
  });
  assert.equal(request.headers["X-CSRFToken"], "synthetic-csrf-placeholder");
  assert.equal(request.cache, "no-store");
  assert.match(container.textContent, /Report rows are never saved/);
});

test("operational sales view separates report-only examples and masks unauthorized cost fields", async () => {
  const operationalPayload = {
    panel: "sales",
    title: "Sales",
    granularity: "monthly",
    currency: "QAR",
    filters: {
      start: "2026-10-01",
      end: "2026-10-18",
      location_id: null,
      unit_id: 3,
      company_id: null,
    },
    scope: {
      scope_type: "business_unit",
      business_unit: { id: 3, code: "TVL", name: "Synthetic Tivoli" },
      business_unit_ids: [3],
      company: null,
    },
    fields: ["selling_price", "quantity", "description", "item_id", "warehouse_location", "brand_name"],
    rows: [{
      selling_price: "200.00",
      quantity: "1.00",
      description: "Operational synthetic chair",
      item_id: "TVF-OP-01",
      warehouse_location: "Synthetic showroom",
      brand_name: "Tivoli",
      supplier_unit_cost: "SECRET-COST",
      gross_margin: "SECRET-MARGIN",
    }],
    pagination: {
      page: 1,
      page_size: 50,
      total_count: 1,
      total_pages: 1,
      previous_page: null,
      next_page: null,
    },
    summary: { sales_amount: "200.00", quantity: "1.00" },
    trend: [{ period: "2026-10-18", sales_amount: "200.00", quantity: "1.00" }],
    policy_label: "TEST ONLY",
    note: "Operational sales rows, totals, trends, and exports use transactional SalesReportLine records only. Report-only examples are excluded.",
    // This deliberately unexpected payload is not part of the operational report contract.
    synthetic_examples: [{ item_id: "DEMO-ONLY-DO-NOT-RENDER", revenue: "90000.00" }],
  };
  let exampleRequest;
  custom = (url) => {
    if (url === "/api/reports/") {
      return json({
        panels: [{ code: "sales", label: "Sales" }],
        scopes: {
          sales: {
            business_units: [{ id: 3, code: "TVL", name: "Synthetic Tivoli" }],
            companies: [],
            locations: [{ id: 7, name: "Synthetic showroom", business_units: [3] }],
          },
        },
      });
    }
    if (url.startsWith("/api/reports/sales-examples/")) {
      exampleRequest = new URL(url, "https://synthetic.invalid");
      return json({
        ...salesExamples({
          example_count: 2,
          illustrative_revenue: "111346.90",
          illustrative_quantity: "4.50",
          start: operationalPayload.filters.start,
          end: operationalPayload.filters.end,
          unitId: 3,
          locationId: Number(exampleRequest.searchParams.get("location")),
        }),
        supplier_cost: "SECRET-EXAMPLE-COST",
        gross_margin: "SECRET-EXAMPLE-MARGIN",
      });
    }
    if (url.startsWith("/api/reports/sales/")) return json(operationalPayload);
    if (url === "/api/report-views/") return json({ views: [] });
    return undefined;
  };

  await renderReportsArea();
  await choose(selectFor("Location"), "7");
  const trendDetails = container.querySelector("details.report-detail");
  assert.ok(trendDetails);
  await act(async () => trendDetails.querySelector("summary").click());
  assert.ok(trendDetails.open);
  assert.match(trendDetails.textContent, /200\.00/);
  assert.doesNotMatch(trendDetails.textContent, /111346\.90|4\.50/);

  const text = container.textContent ?? "";
  assert.match(text, /Operational sales only/);
  assert.match(text, /SalesReportLine records only/);
  assert.match(text, /Sales Amount/);
  assert.match(text, /200\.00/);
  assert.match(text, /TVF-OP-01/);
  assert.match(text, /showing 1–1 of 1 rows/);
  assert.doesNotMatch(text, /DEMO-ONLY-DO-NOT-RENDER/);
  assert.doesNotMatch(text, /SECRET-COST|SECRET-MARGIN|Supplier Unit Cost|Gross Margin/);
  const operationalSummary = container.querySelector('[aria-label="Operational sales summary"]');
  assert.ok(operationalSummary);
  assert.match(operationalSummary.textContent, /200\.00/);
  assert.doesNotMatch(operationalSummary.textContent, /111346\.90|4\.50/);
  const examplesPanel = container.querySelector('[aria-label="Synthetic examples — not operational sales"]');
  assert.ok(examplesPanel);
  assert.match(examplesPanel.textContent, /Filtered example count\s*2/);
  assert.match(examplesPanel.textContent, /Illustrative revenue · examples only\s*111346\.90 QAR/);
  assert.match(examplesPanel.textContent, /Illustrative quantity\s*4\.50/);
  assert.match(examplesPanel.textContent, /never enter operational SalesReportLine/);
  assert.doesNotMatch(examplesPanel.textContent, /SECRET-EXAMPLE-COST|SECRET-EXAMPLE-MARGIN/);
  const operationalNote = container.querySelector('[aria-label="Operational sales source"]');
  assert.ok(operationalNote);
  assert.ok(exampleRequest);
  const operationalRequests = calls.filter((item) => item.url.startsWith("/api/reports/sales/"));
  const activeOperationalRequest = new URL(operationalRequests.at(-1).url, "https://synthetic.invalid");
  assert.equal(exampleRequest.searchParams.get("unit"), "3");
  assert.equal(exampleRequest.searchParams.get("location"), "7");
  assert.equal(exampleRequest.searchParams.get("start"), activeOperationalRequest.searchParams.get("start"));
  assert.equal(exampleRequest.searchParams.get("end"), activeOperationalRequest.searchParams.get("end"));
  assert.equal(exampleRequest.searchParams.get("page"), null);
  assert.equal(exampleRequest.searchParams.get("page_size"), null);
  assert.equal(operationalRequests.length, 2);
  assert.equal(
    new URL(operationalRequests.at(-1).url, "https://synthetic.invalid").searchParams.get("location"),
    "7",
  );
});

test("server-authorized synthetic Managing Partner assigns and revokes a panel without granting data access or exposing costs", async () => {
  const panelChoices = [
    { code: "sales", label: "Sales" },
    { code: "costs_expenses", label: "Costs and expenses" },
  ];
  const emptyScope = { business_units: [], companies: [], locations: [] };
  const salesScope = {
    business_units: [{ id: 4, code: "RBB", name: "Synthetic Roche Bobois" }],
    companies: [],
    locations: [],
  };
  const dataGrants = [{ user_id: 12, capability: "sales", business_unit_id: 4 }];
  const dataGrantsBefore = structuredClone(dataGrants);
  const deniedCostQueries = [];
  let activeActor = "manager";
  let assignments = [
    {
      id: 20, user_id: 12, username: "demo_roche", role_id: null, role_name: null,
      panel: "sales", revoked: false,
    },
  ];
  let nextGrantId = 21;
  const managementUrl = "/api/governance/report-panels/";
  const scopes = {
    sales: salesScope,
    category_sales: emptyScope,
    supplier_cost: emptyScope,
    landed_cost: emptyScope,
    purchase_history: emptyScope,
    inventory: emptyScope,
    transfer_history: emptyScope,
    expenses: emptyScope,
    gross_margin: emptyScope,
    budgets: emptyScope,
  };
  const operationalSales = {
    ...report("sales", "RBB-ORDINARY-SALE"),
    granularity: "monthly",
    fields: ["selling_price", "quantity", "description", "item_id", "warehouse_location", "brand_name"],
    rows: [{
      selling_price: "100.00",
      quantity: "1.00",
      description: "Synthetic chair",
      item_id: "RBB-SYN-01",
      warehouse_location: "Synthetic showroom",
      brand_name: "Roche Bobois",
      supplier_unit_cost: "SECRET-SUPPLIER-COST",
      landed_cost_allocation: "SECRET-LANDED-COST",
    }],
    pagination: {
      page: 1, page_size: 50, total_count: 1, total_pages: 1, previous_page: null, next_page: null,
    },
  };
  custom = (url, options = {}) => {
    if (url === managementUrl && options.method === "POST") {
      const payload = JSON.parse(options.body);
      if (payload.action === "assign_panel") {
        const id = nextGrantId++;
        assignments.push({
          id, user_id: payload.user_id ?? null, username: payload.user_id === 12 ? "demo_roche" : null,
          role_id: payload.role_id ?? null, role_name: null, panel: payload.panel, revoked: false,
        });
        return json({ grant_id: id }, 201);
      }
      if (payload.action === "revoke_panel") {
        assignments = assignments.map((grant) => grant.id === payload.grant_id ? { ...grant, revoked: true } : grant);
        return json({ revoked: true });
      }
      return json({ detail: "Unsupported panel-only action." }, 400);
    }
    if (url === managementUrl) {
      return json({
        users: [{ id: 12, username: "demo_roche" }],
        roles: [{ id: 4, code: "test-report-sales-rbb", name: "TEST ONLY Synthetic Roche Sales" }],
        panels: panelChoices,
        panel_grants: assignments,
      });
    }
    if (url === "/api/reports/") {
      const visiblePanels = activeActor === "recipient"
        ? assignments.filter((grant) => grant.user_id === 12 && !grant.revoked)
            .map((grant) => panelChoices.find((panel) => panel.code === grant.panel))
            .filter(Boolean)
        : [];
      return json({ panels: visiblePanels, scopes });
    }
    if (url === "/api/report-views/") return json({ views: [] });
    if (url.startsWith("/api/reports/sales/")) return json(operationalSales);
    if (url.startsWith("/api/reports/costs_expenses/")) {
      deniedCostQueries.push(url);
      return json({ detail: "No active cost or expense capability." }, 403);
    }
    return undefined;
  };

  await renderReportsArea(true);
  assert.match(container.textContent, /Manage report panels/);
  assert.doesNotMatch(container.textContent, /RESTRICTED · PANEL ACCESS ONLY/);
  await act(async () => root.unmount());
  root = createRoot(container);
  await act(async () => root.render(<GovernanceReportPanelManager csrfToken="synthetic-csrf-placeholder" />));
  await flush();
  await choose(selectFor("Report panel"), "costs_expenses");
  await click(button("Assign panel only"));

  const assignRequest = calls.find((item) =>
    item.url === managementUrl && item.method === "POST" &&
    JSON.parse(item.body).action === "assign_panel"
  );
  assert.deepEqual(JSON.parse(assignRequest.body), {
    action: "assign_panel",
    panel: "costs_expenses",
    user_id: 12,
  });
  assert.equal(assignRequest.headers["X-CSRFToken"], "synthetic-csrf-placeholder");
  assert.match(container.textContent, /Data access remains separate/);
  assert.deepEqual(dataGrants, dataGrantsBefore);
  assert.equal(calls.some((item) => item.url === "/api/governance/reports/" && item.method === "POST"), false);

  const recipientCallsStart = calls.length;
  await act(async () => root.unmount());
  root = createRoot(container);
  activeActor = "recipient";
  await renderReportsArea(false);
  const recipientPanels = [...selectFor("Assigned panel").options].map((option) => option.value);
  assert.deepEqual(recipientPanels, ["sales", "costs_expenses"]);
  assert.doesNotMatch(container.textContent, /Manage report panels/);
  await choose(selectFor("Assigned panel"), "costs_expenses");
  for (let index = 0; index < 5; index += 1) await flush();
  assert.match(container.textContent, /No report-data scope assigned/);
  assert.match(container.textContent, /No scopes listed for this capability/);
  assert.doesNotMatch(container.textContent, /SECRET-SUPPLIER-COST|SECRET-LANDED-COST/);
  assert.equal(
    calls.slice(recipientCallsStart).some((item) => item.url === managementUrl),
    false,
    "ordinary recipient must not load manager controls",
  );
  assert.equal(
    deniedCostQueries.length,
    0,
    "panel selection must not issue a cost query with the prior sales scope",
  );
  assert.equal(
    calls.slice(recipientCallsStart).some((item) => item.url.startsWith("/api/reports/costs_expenses/")),
    false,
  );

  await act(async () => root.unmount());
  root = createRoot(container);
  activeActor = "manager";
  await act(async () => root.render(<GovernanceReportPanelManager csrfToken="synthetic-csrf-placeholder" />));
  for (let index = 0; index < 5; index += 1) await flush();
  const oldConfirm = window.confirm;
  window.confirm = () => true;
  try {
    const costGrant = [...container.querySelectorAll(".grant-row")]
      .find((row) => row.textContent?.includes("Costs and expenses"));
    assert.ok(costGrant, "Assigned cost panel should be listed for revocation.");
    await click(costGrant.querySelector("button"));
  } finally {
    window.confirm = oldConfirm;
  }
  const revokeRequest = calls.find((item) =>
    item.url === managementUrl && item.method === "POST" &&
    JSON.parse(item.body).action === "revoke_panel"
  );
  assert.deepEqual(JSON.parse(revokeRequest.body), { action: "revoke_panel", grant_id: 21 });
  assert.match(container.textContent, /Data access is unchanged/);
  assert.deepEqual(dataGrants, dataGrantsBefore);

  await act(async () => root.unmount());
  root = createRoot(container);
  activeActor = "recipient";
  await renderReportsArea(false);
  const panelsAfterRevoke = [...selectFor("Assigned panel").options].map((option) => option.value);
  assert.deepEqual(panelsAfterRevoke, ["sales"]);
  assert.doesNotMatch(container.textContent, /SECRET-SUPPLIER-COST|SECRET-LANDED-COST/);
  assert.deepEqual(dataGrants, dataGrantsBefore);
});

test("changing panel suppresses a request with the previous panel's scope", async () => {
  const emptyScope = { business_units: [], companies: [], locations: [] };
  const unitAScope = {
    business_units: [{ id: 3, code: "TVL", name: "Synthetic Unit A" }],
    companies: [],
    locations: [],
  };
  custom = (url) => {
    if (url === "/api/reports/") {
      return json({
        panels: [
          { code: "sales", label: "Sales" },
          { code: "costs_expenses", label: "Costs and expenses" },
        ],
        scopes: {
          sales: unitAScope,
          category_sales: emptyScope,
          supplier_cost: emptyScope,
          landed_cost: emptyScope,
          expenses: emptyScope,
        },
      });
    }
    if (url === "/api/report-views/") return json({ views: [] });
    if (url.startsWith("/api/reports/sales/")) {
      return json({ ...report("sales", "UNIT-A-SALES"), granularity: "monthly" });
    }
    if (url.startsWith("/api/reports/costs_expenses/")) {
      return json({ detail: "No cost capability is authorized." }, 403);
    }
    return undefined;
  };

  await renderReportsArea();
  assert.match(container.textContent, /UNIT-A-SALES/);
  const before = calls.length;
  await choose(selectFor("Assigned panel"), "costs_expenses");
  for (let index = 0; index < 5; index += 1) await flush();

  assert.match(container.textContent, /No report-data scope assigned/);
  assert.equal(
    calls.slice(before).some((item) => item.url.startsWith("/api/reports/costs_expenses/")),
    false,
    "a panel without an authorized scope must not request using Unit A's prior scope",
  );
  assert.doesNotMatch(container.textContent, /UNIT-A-SALES|SECRET-COST|SECRET-LANDED-COST/);
});

test("changing business-unit/location scope ignores a delayed old-scope response", async () => {
  const delayedUnitALocation = deferred();
  const wrongUnitLocationRequests = [];
  const scopedReport = (unitId, itemId, locationId = null) => ({
    ...report("sales", itemId),
    granularity: "monthly",
    filters: {
      start: filters.start,
      end: filters.end,
      location_id: locationId,
      unit_id: unitId,
    },
    scope: {
      scope_type: "business_unit",
      business_unit: { id: unitId, code: unitId === 3 ? "TVL" : "RBB", name: `Synthetic Unit ${unitId}` },
      business_unit_ids: [unitId],
    },
    rows: [{ item_id: itemId }],
    fields: ["item_id"],
    pagination: {
      page: 1, page_size: 50, total_count: 1, total_pages: 1, previous_page: null, next_page: null,
    },
  });
  custom = (url) => {
    if (url === "/api/reports/") {
      return json({
        panels: [{ code: "sales", label: "Sales" }],
        scopes: {
          sales: {
            business_units: [
              { id: 3, code: "TVL", name: "Synthetic Unit A" },
              { id: 4, code: "RBB", name: "Synthetic Unit B" },
            ],
            companies: [],
            locations: [
              { id: 7, name: "Unit A location", business_units: [3] },
              { id: 8, name: "Unit B location", business_units: [4] },
            ],
          },
        },
      });
    }
    if (url === "/api/report-views/") return json({ views: [] });
    if (url.startsWith("/api/reports/sales/")) {
      const query = new URL(url, "https://synthetic.invalid").searchParams;
      const unitId = Number(query.get("unit"));
      const locationId = query.get("location");
      if (unitId === 3 && locationId === null) return json(scopedReport(3, "UNIT-A-INITIAL"));
      if (unitId === 3 && locationId === "7") return delayedUnitALocation.promise;
      if (unitId === 4 && locationId === "7") {
        wrongUnitLocationRequests.push(url);
        return json({ detail: "Unit A location is not authorized for Unit B." }, 403);
      }
      if (unitId === 4 && locationId === null) return json(scopedReport(4, "UNIT-B-CURRENT"));
    }
    return undefined;
  };

  await renderReportsArea();
  assert.match(container.textContent, /UNIT-A-INITIAL/);
  await choose(selectFor("Location"), "7");
  assert.ok(calls.some((item) => {
    const query = new URL(item.url, "https://synthetic.invalid").searchParams;
    return item.url.startsWith("/api/reports/sales/") && query.get("unit") === "3" && query.get("location") === "7";
  }), "the Unit A location lookup should be pending before the context switch");

  await choose(selectFor("Reporting scope"), "unit:4");
  for (let index = 0; index < 5; index += 1) await flush();
  assert.match(container.textContent, /UNIT-B-CURRENT/);
  assert.deepEqual(wrongUnitLocationRequests, []);
  const unitBRequests = calls.filter((item) => {
    if (!item.url.startsWith("/api/reports/sales/")) return false;
    const query = new URL(item.url, "https://synthetic.invalid").searchParams;
    return query.get("unit") === "4";
  });
  assert.ok(unitBRequests.some((item) =>
    new URL(item.url, "https://synthetic.invalid").searchParams.get("location") === null
  ));

  await act(async () => delayedUnitALocation.resolve(json(scopedReport(3, "UNIT-A-LATE", 7))));
  await flush();
  assert.match(container.textContent, /UNIT-B-CURRENT/);
  assert.doesNotMatch(container.textContent, /UNIT-A-INITIAL|UNIT-A-LATE/);
});

test("global business-unit context change immediately replaces the report scope", async () => {
  const delayedUnitA = deferred();
  const scopes = {
    sales: {
      business_units: [
        { id: 3, code: "TVL", name: "Synthetic Unit A" },
        { id: 4, code: "RBB", name: "Synthetic Unit B" },
      ],
      companies: [],
      locations: [],
    },
  };
  custom = (url) => {
    if (url === "/api/reports/") return json({ panels: [{ code: "sales", label: "Sales" }], scopes });
    if (url === "/api/report-views/") return json({ views: [] });
    if (url.startsWith("/api/reports/sales/")) {
      const query = new URL(url, "https://synthetic.invalid").searchParams;
      if (query.get("unit") === "3") return delayedUnitA.promise;
      if (query.get("unit") === "4") {
        return json({
          ...report("sales", "UNIT-B-CURRENT"),
          granularity: "monthly",
          scope: {
            scope_type: "business_unit",
            business_unit: { id: 4, code: "RBB", name: "Synthetic Unit B" },
            business_unit_ids: [4],
          },
        });
      }
    }
    return undefined;
  };

  await renderReportsArea(false, 3);
  assert.ok(calls.some((item) => {
    const query = new URL(item.url, "https://synthetic.invalid").searchParams;
    return item.url.startsWith("/api/reports/sales/") && query.get("unit") === "3";
  }));
  await renderReportsArea(false, 4);
  assert.equal(selectFor("Reporting scope").value, "unit:4");
  assert.match(container.textContent, /UNIT-B-CURRENT/);
  const afterSwitch = calls.slice();
  assert.ok(afterSwitch.some((item) => {
    const query = new URL(item.url, "https://synthetic.invalid").searchParams;
    return item.url.startsWith("/api/reports/sales/") && query.get("unit") === "4";
  }));

  await act(async () => delayedUnitA.resolve(json({
    ...report("sales", "UNIT-A-LATE"),
    granularity: "monthly",
  })));
  await flush();
  assert.equal(selectFor("Reporting scope").value, "unit:4");
  assert.match(container.textContent, /UNIT-B-CURRENT/);
  assert.doesNotMatch(container.textContent, /UNIT-A-LATE/);
});

test("a delayed report from the previous panel cannot replace the newly selected panel data", async () => {
  const delayedSales = deferred();
  const unitScope = {
    business_units: [{ id: 3, code: "TVL", name: "Synthetic Unit A" }],
    companies: [],
    locations: [],
  };
  custom = (url) => {
    if (url === "/api/reports/") {
      return json({
        panels: [
          { code: "sales", label: "Sales" },
          { code: "inventory", label: "Inventory" },
        ],
        scopes: { sales: unitScope, inventory: unitScope },
      });
    }
    if (url === "/api/report-views/") return json({ views: [] });
    if (url.startsWith("/api/reports/sales/")) return delayedSales.promise;
    if (url.startsWith("/api/reports/inventory/")) {
      return json({
        ...report("inventory", "UNIT-A-INVENTORY"),
        title: "Inventory",
        granularity: "monthly",
      });
    }
    return undefined;
  };

  await renderReportsArea();
  assert.ok(calls.some((item) => item.url.startsWith("/api/reports/sales/")));
  await choose(selectFor("Assigned panel"), "inventory");
  for (let index = 0; index < 5; index += 1) await flush();
  assert.equal(selectFor("Assigned panel").value, "inventory");
  assert.match(container.textContent, /UNIT-A-INVENTORY/);

  await act(async () => delayedSales.resolve(json({
    ...report("sales", "STALE-UNIT-A-SALES"),
    granularity: "monthly",
  })));
  await flush();
  assert.equal(selectFor("Assigned panel").value, "inventory");
  assert.match(container.textContent, /UNIT-A-INVENTORY/);
  assert.doesNotMatch(container.textContent, /STALE-UNIT-A-SALES/);
});

test("load after remount retains saved scope/filter values and panel order, not current ad-hoc scope", async () => {
  await renderWorkspace();
  await click(button(saved.name));
  const queries = calls.filter((item) => item.url.startsWith("/api/reports/"));
  assert.deepEqual(queries.map((item) => item.url.split("/")[3]), saved.panels);
  assert.equal(calls.some((item) => item.url.startsWith("/api/reports/sales-examples/")), false);
  for (const request of queries) {
    const query = new URL(request.url, "https://synthetic.invalid").searchParams;
    for (const [key, value] of Object.entries(filters)) assert.equal(query.get(key), String(value));
    assert.equal(request.cache, "no-store");
  }
  await act(async () => root.unmount());
  root = createRoot(container);
  await renderWorkspace();
  await click(button(saved.name));
  assert.match(container.textContent, /Business unit 3/);
  assert.doesNotMatch(container.querySelector(".personal-active-view").textContent, /999|888/);
  assert.equal(calls.filter((item) => item.url === "/api/report-views/1/").length, 2);
});

test("preferences update and delete are owner-endpoint mutations, not report writes", async () => {
  await renderWorkspace();
  await click(button(saved.name));
  await input("View name", "Renamed synthetic review");
  await submit();
  const update = calls.find((item) => item.method === "PUT");
  assert.deepEqual(JSON.parse(update.body).filters, filters);
  assert.equal(JSON.parse(update.body).name, "Renamed synthetic review");
  window.confirm = () => true;
  await click(button("Delete view"));
  assert.ok(calls.some((item) => item.method === "DELETE" && item.url === "/api/report-views/1/"));
  assert.doesNotMatch(container.textContent, /SYNTHETIC-ROW/);
  assert.equal(calls.filter((item) => item.url.startsWith("/api/reports/") && item.method).length, 0);
});

test("unavailable saved panels show denial without querying or displaying report rows", async () => {
  detail = { ...saved, availability: saved.panels.map((panel) => ({ panel, available: false, reason: "Assignment revoked" })) };
  await renderWorkspace();
  await click(button(saved.name));
  assert.match(container.textContent, /Assignment revoked/);
  assert.doesNotMatch(container.textContent, /SYNTHETIC-ROW/);
  assert.equal(calls.filter((item) => item.url.startsWith("/api/reports/")).length, 0);
});

test("focus reauthorization immediately clears old rows, then shows revoked access", async () => {
  await renderWorkspace();
  await click(button(saved.name));
  assert.match(container.textContent, /SYNTHETIC-ROW/);
  const pending = deferred();
  custom = (url) => url === "/api/report-views/1/" ? pending.promise : undefined;
  await act(async () => window.dispatchEvent(new Event("focus")));
  assert.doesNotMatch(container.textContent, /SYNTHETIC-ROW/);
  assert.match(container.textContent, /Rechecking report availability/);
  await act(async () => pending.resolve(json({
    ...saved, availability: saved.panels.map((panel) => ({ panel, available: false, reason: "Scope revoked" })),
  })));
  await flush();
  assert.match(container.textContent, /Scope revoked/);
  assert.doesNotMatch(container.textContent, /SYNTHETIC-ROW/);
});

test("failed export uses saved filters and CSRF, clears data and rechecks current access", async () => {
  await renderWorkspace();
  await click(button(saved.name));
  custom = (url, options) => {
    if (url.endsWith("/export/")) {
      detail = { ...saved, availability: saved.panels.map((panel) => ({ panel, available: false, reason: "Data access revoked" })) };
      return json({ detail: "Data access revoked" }, 403);
    }
    return undefined;
  };
  await click(button("Export Excel"));
  const request = calls.find((item) => item.url.endsWith("/export/"));
  assert.equal(request.method, "POST");
  assert.equal(request.headers["X-CSRFToken"], "synthetic-csrf-placeholder");
  assert.deepEqual(JSON.parse(request.body), { ...filters, format: "xlsx" });
  assert.match(container.textContent, /Data access revoked/);
  assert.doesNotMatch(container.textContent, /SYNTHETIC-ROW/);
  assert.equal(calls.filter((item) => item.url === "/api/report-views/1/").length, 2);
});

test("authorized Word export uses the existing handler, saved filters and server file response", async () => {
  const oldCreate = URL.createObjectURL;
  const oldRevoke = URL.revokeObjectURL;
  const oldClick = HTMLAnchorElement.prototype.click;
  const downloads = [];
  URL.createObjectURL = () => "blob:synthetic-test-file";
  URL.revokeObjectURL = () => {};
  HTMLAnchorElement.prototype.click = function () { downloads.push(this.download); };
  try {
    custom = (url) => url.endsWith("/export/") ? new Response("synthetic-test-only-file") : undefined;
    await renderWorkspace();
    await click(button(saved.name));
    await click(button("Export Word"));
    const request = calls.find((item) => item.url.endsWith("/export/"));
    assert.equal(request.url, "/api/reports/sales/export/");
    assert.deepEqual(JSON.parse(request.body), { ...filters, format: "docx" });
    assert.deepEqual(downloads, ["synthetic-sales-report.docx"]);
  } finally {
    URL.createObjectURL = oldCreate;
    URL.revokeObjectURL = oldRevoke;
    HTMLAnchorElement.prototype.click = oldClick;
  }
});

test("workspace preserves authorized report breakdowns and explicit page navigation", async () => {
  custom = (url) => {
    if (!url.startsWith("/api/reports/")) return undefined;
    const page = new URL(url, "https://synthetic.invalid").searchParams.get("page");
    return json({
      ...report("sales", page === "2" ? "SECOND-PAGE" : "FIRST-PAGE"),
      best_selling_items: [{ item_id: "AUTHORIZED-RANKING", quantity: "2.00" }],
      best_selling_categories: null,
      categories_permission_required: true,
      pagination: {
        page: Number(page), total_pages: 2, total_count: 26,
        previous_page: page === "2" ? 1 : null, next_page: page === "2" ? null : 2,
      },
    });
  };
  await renderWorkspace();
  await click(button(saved.name));
  assert.match(container.textContent, /AUTHORIZED-RANKING/);
  assert.match(container.textContent, /separate category-sales permission/);
  await click(button("Next"));
  assert.match(container.textContent, /SECOND-PAGE/);
  const pages = calls.filter((item) => item.url.startsWith("/api/reports/"));
  assert.ok(pages.some((item) => new URL(item.url, "https://synthetic.invalid").searchParams.get("page") === "2"));
});

test("a late saved-view response cannot replace a more recently selected view", async () => {
  views = [saved, { ...saved, id: 2, name: "Second synthetic view" }];
  const late = deferred();
  custom = (url) => {
    if (url === "/api/report-views/1/") return late.promise;
    if (url === "/api/report-views/2/") return json({ ...detail, id: 2, name: "Second synthetic view", panels: ["sales"] });
  };
  await renderWorkspace();
  await click(button(saved.name));
  await click(button("Second synthetic view"));
  await act(async () => late.resolve(json({ ...detail, name: "STALE VIEW" })));
  await flush();
  assert.equal(container.querySelector(".personal-active-heading h3").textContent, "Second synthetic view");
  assert.doesNotMatch(container.textContent, /STALE VIEW/);
});

test("report hook clears rows synchronously on scope change and ignores an aborted late response", async () => {
  const old = deferred();
  const next = deferred();
  custom = (url) => url.includes("unit=3") ? old.promise : url.includes("unit=4") ? next.promise : undefined;
  function Probe({ unit }) {
    const result = useReportData("sales", { unit });
    return <div>{result.data ? JSON.stringify(result.data.rows) : result.error || "LOADING"}</div>;
  }
  await act(async () => root.render(<Probe unit={3} />));
  await act(async () => root.render(<Probe unit={4} />));
  assert.equal(container.textContent, "LOADING");
  await act(async () => next.resolve(json(report("sales", "NEW-AUTHORIZED"))));
  await act(async () => old.resolve(json(report("sales", "OLD-SCOPE"))));
  await flush();
  assert.match(container.textContent, /NEW-AUTHORIZED/);
  assert.doesNotMatch(container.textContent, /OLD-SCOPE/);
  const denied = deferred();
  custom = () => denied.promise;
  await act(async () => root.render(<Probe unit={5} />));
  assert.equal(container.textContent, "LOADING");
  await act(async () => denied.resolve(json({ detail: "Forbidden scope" }, 403)));
  await flush();
  assert.equal(container.textContent, "Forbidden scope");
});

test("a fresh user mount only displays the server-owned list and no prior user's rows", async () => {
  await renderWorkspace();
  await click(button(saved.name));
  assert.match(container.textContent, /SYNTHETIC-ROW/);
  await act(async () => root.unmount());
  root = createRoot(container);
  views = [];
  await renderWorkspace();
  assert.match(container.textContent, /No saved views yet/);
  assert.doesNotMatch(container.textContent, /Fictional monthly review|SYNTHETIC-ROW/);
});
