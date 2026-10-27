import assert from "node:assert/strict";
import { test } from "node:test";
import { browserHarness } from "./browser-harness.mjs";
import { assertFunction, isVisible, openFunction } from "./workspace-browser-navigation.mjs";

const sensitive = "TEST_ONLY_NEVER_RENDER_COST_OR_MEASUREMENT";
const units = [
  { id: 1, code: "TVL", name: "TEST ONLY Tivoli", theme_palette: "tivoli" },
  { id: 2, code: "RBB", name: "TEST ONLY Roche", theme_palette: "roche" },
];
const favorites = [
  ["retail", "Retail & Sales"], ["procurement", "Procurement & Imports"],
  ["inventory", "Inventory & Warehouses"], ["delivery", "Delivery & After-Sales"],
  ["governance", "Governance & Administration"],
].map(([destination, label]) => ({ destination, label }));
const page = (rows = []) => ({ rows, pagination: {
  page: 1, page_size: 20, total_count: rows.length, total_pages: 1,
  previous_page: null, next_page: null,
} });

function mockPages({ privileged = true } = {}) {
  let selected = 1;
  return async (request, url) => {
    const get = request.method === "GET";
    if (get && url.pathname === "/api/auth/me/") return { data: {
      authenticated: true, username: "TEST_ONLY_FUNCTION_PAGES",
      is_staff: privileged, is_superuser: privileged, can_manage_report_panels: privileged,
    } };
    if (get && url.pathname === "/api/organization/") return { data: {
      company: { code: "TEST", name: "TEST ONLY company" }, units,
      selected_unit: units[selected - 1],
      locations: [{ id: selected * 11, code: `TEST-LOC-${selected}`,
        name: `TEST ONLY warehouse ${selected}`, parent_id: null }],
    } };
    if (get && url.pathname === "/api/favorites/") return { data: {
      favorites: favorites.filter(item => privileged || item.destination !== "governance"),
      available_destinations: [
        ...favorites.filter(item => privileged || item.destination !== "governance"),
        ...Object.entries(routes).flatMap(([section, children]) => children
          .filter(child => child !== "main"
            && (privileged || (section !== "governance" && child !== "suppliers")))
          .map(child => ({ destination: `${section}.${child}`, label: `${section} · ${child}` }))),
      ],
    } };
    if (request.method === "POST" && /^\/api\/units\/[12]\/select\/$/.test(url.pathname)) {
      selected = Number(url.pathname.split("/")[3]);
      return { data: { selected_unit: units[selected - 1] } };
    }
    if (get && url.pathname === "/api/stock-balances/") {
      assert.equal(url.searchParams.get("unit"), String(selected), "Stock request uses current BU");
      const location = url.searchParams.get("location");
      assert.ok(!location || location === String(selected * 11), "No old location scope");
      return { data: {
        results: [{
          id: selected, item_code: `TEST-LEDGER-${selected}`, item_description: `TEST ONLY stock ${selected}`,
          brand_name: units[selected - 1].name, variant_sku: `TEST-SKU-${selected}`,
          variant_description: "TEST ONLY variant", location_code: `TEST-LOC-${selected}`,
          location_name: `TEST ONLY warehouse ${selected}`, unit_code: units[selected - 1].code,
          unit_name: units[selected - 1].name, quantity: "2.500", is_synthetic: true,
          unit_cost: sensitive,
        }], pagination: { ...page().pagination, page_size: 50, total_count: 1 },
      } };
    }
    if (get && url.pathname === "/api/governance/business-unit-palettes/") return {
      status: privileged ? 200 : 403, data: privileged ? {
        palettes: [{ value: "tivoli", label: "Tivoli orange", kind: "built_in", colors: {} },
          { value: "roche", label: "Roche light blue", kind: "built_in", colors: {} }],
        units: units.map(unit => ({ ...unit, palette_value: unit.theme_palette })),
      } : { detail: "TEST ONLY staff denied" },
    };
    if (get && url.pathname === "/api/governance/report-panels/") return {
      status: privileged ? 200 : 403, data: privileged ? {
        users: [], roles: [], panels: [{ code: "sales", label: "Sales" }], panel_grants: [],
      } : { detail: "TEST ONLY manager denied" },
    };
    if (get && /^\/api\/governance\/purchasing\/[12]\/$/.test(url.pathname)) return {
      status: privileged ? 200 : 403, data: privileged ? {
        test_only_label: `TEST ONLY configuration ${selected}`,
        business_unit: units[selected - 1], role_assignments: [], current_prices: [],
        work_calendar: { is_configured: false, closed_weekdays: [], closed_dates: [] },
        storage_exception_approver_roles: [], special_order_value_bands: [],
        special_order_bands_used_for_approval: false, special_order_bands_note: "TEST ONLY unconfigured",
        supplier_cost: sensitive,
      } : { detail: "TEST ONLY staff denied" },
    };
    if (get && url.pathname === "/api/governance/purchasing/counterparties/") return {
      status: 403, data: { detail: "TEST ONLY current directory access denied" },
    };
    if (get && url.pathname === "/api/governance/warehouse-capacity/") return {
      status: privileged ? 200 : 403,
      data: privileged ? { locations: [], synthetic_users: [] } : { detail: "TEST ONLY administrator denied" },
    };
    if (get && url.pathname === "/api/governance/warehouse-capacity/audit/") return {
      status: privileged ? 200 : 403,
      data: privileged ? { results: [], pagination: page().pagination } : { detail: "TEST ONLY administrator denied" },
    };
    if (get && url.pathname === "/api/reports/") return { data: {
      panels: [], scopes: { business_units: [], locations: [], legal_entities: [], capabilities: [] },
    } };
    if (get && url.pathname === "/api/purchasing/quotes/501/") return { data: {
      quote_id: 501, business_unit_id: selected, kind: "stock", status: "approved", customer_id: 19,
      discount_percent: "0.00", total_list_value: "87.50", total_discount: "0.00",
      total_quote_value: "87.50", payment_plan: "full", downpayment_percent: null,
      lines: [], special_order_value_bands_configured: false, payments: [], approvals: [], holds: [], events: [],
    } };
    if (get && url.pathname === "/api/report-views/") return { data: { views: [] } };
    if (get && url.pathname === "/api/warehouse-capacity/") return {
      status: 403, data: { detail: "TEST ONLY no physical-location capacity assignment" },
    };
    if (get && /^\/api\/purchasing\/(quotes|receipts|storage-charges|commitments|counterparties|returns)\/$/.test(url.pathname))
      return { status: 403, data: { detail: "TEST ONLY operation unavailable without existing grant" } };
    if (get && url.pathname === "/api/stock-receipt-candidates/") return {
      status: 403, data: { detail: "TEST ONLY catalog authorization absent" },
    };
    if (get && /^\/api\/reports\/(purchase_history\/receipts|inventory\/reservations|inventory\/transfers|sales\/deliveries)\/$/.test(url.pathname))
      return { status: 403, data: { detail: "TEST ONLY report capability absent" } };
    return null;
  };
}

const routes = {
  retail: ["main", "quote", "actions", "register"],
  procurement: ["main", "commitments", "progress", "receipt", "packing", "registers", "suppliers"],
  inventory: ["main", "locations", "receipt", "commitments", "progress", "packing", "registers", "transfers", "capacity"],
  delivery: ["main", "quote", "handover", "quotes", "storage", "reservations", "deliveries", "returns"],
  governance: ["main", "presentation", "purchasing-config", "reporting", "report-panels", "capacity-admin", "capacity-audit", "transfer-permissions"],
  purchasing: ["main", "quote", "transactions", "registers"],
  reports: ["main"],
  finance: ["main"],
  hr: ["main"],
};

for (const width of [1280, 390, 320]) {
  test(`distinct ERP function pages support native navigation and fit ${width}px`, async () => {
    const b = await browserHarness({ width, height: 900, mockApi: mockPages() });
    try {
      const overflows = [];
      for (const [section, children] of Object.entries(routes)) {
        for (const child of children) {
          await openFunction(b, section, child);
          const linkSelector = `[data-workspace-link="${section}/${child}"]`;
          const expectedTitle = await b.evaluate(`document.querySelector(${JSON.stringify(linkSelector)})?.getAttribute("aria-label")`);
          assert.equal(await b.evaluate('document.querySelector(".page-heading h1")?.textContent'),
            expectedTitle, `Function breadcrumb/headline must name ${section}/${child}`);
          const visible = await b.evaluate(`[...document.querySelectorAll("[data-workspace-view]")]
            .filter(node => node.getClientRects().length).map(node => node.dataset.workspaceView)`);
          assert.deepEqual([...new Set(visible)], [`${section}/${child}`]);
          if (section === "purchasing") {
            const panels = await b.evaluate(`({
              quoteHidden: document.querySelector(".pw-quote-card")?.hidden,
              transactionsHidden: document.querySelector(".pw-transactions")?.hidden,
              recordsHidden: document.querySelector(".purchasing-readside")?.closest("[hidden]") !== null
            })`);
            assert.equal(panels.quoteHidden, child === "main" || child === "registers");
            assert.equal(panels.transactionsHidden, !["transactions"].includes(child));
            assert.equal(panels.recordsHidden, child !== "registers");
            if (child === "transactions") {
              assert.equal(await isVisible(b, "#pw-receipt-supplier"), true, "Transaction focus shows its receipt form");
              assert.equal(await isVisible(b, "#pw-quote-variant"), true, "Transaction focus shows quote creation");
              assert.equal(await isVisible(b, ".pw-selected-quote-actions"), true, "Transaction focus shows selected-quote actions");
              await b.evaluate(`(() => {
                const input=document.querySelector("#pw-quote-id");
                const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set;
                setter.call(input,"501"); input.dispatchEvent(new Event("input",{bubbles:true}));
                document.querySelector(".pw-quote-form").dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));
              })()`);
              await b.wait('document.querySelector(".pw-selected-quote-actions h4")?.textContent === "Request temporary hold"');
              assert.equal(await isVisible(b, ".pw-selected-quote-actions form"), true,
                "Loading an authorized selection renders its eligible action form without route change");
              assert.equal(await b.evaluate('location.hash'), "#/purchasing/transactions");
            }
          }
          if (section === "delivery" && ["quotes", "storage", "reservations", "deliveries"].includes(child)) {
            assert.equal(await b.evaluate(`document.querySelector('[data-register-tab="${child}"]')?.getAttribute("aria-current")`), "page",
              `${child} function must select its own register`);
            assert.equal(await isVisible(b, ".pw-transactions"), false, "Read-side register pages hide transaction actions");
          }
          if (section === "inventory" && ["receipt", "commitments", "progress", "packing"].includes(child)) {
            const activeForms = await b.evaluate(`({
              receipt: document.querySelector("#pw-receipt-title")?.closest(".pw-transaction-panel")?.hidden === false,
              commitment: document.querySelector("#pw-commitment-create-title")?.closest(".pw-action-subform")?.hidden === false,
              progress: document.querySelector("#pw-commitment-progress-title")?.hidden === false,
              packing: document.querySelector(".packing-preview")?.closest("details")?.open === true
            })`);
            assert.equal(activeForms[child === "receipt" ? "receipt" : child === "commitments" ? "commitment" : child], true,
              `${section}/${child} shows its focused function form`);
          }
          assert.equal(await b.evaluate(`document.body.textContent.includes(${JSON.stringify(sensitive)})`), false);
          const layout = await b.evaluate(`({
            width: document.documentElement.clientWidth, viewport: innerWidth,
            scroll: document.documentElement.scrollWidth,
            outside: [...document.querySelectorAll("body *")]
              .filter(node => node.getClientRects().length
                && node.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
              .slice(0, 12).map(node => ({tag:node.tagName, class:node.className,
                width:node.getBoundingClientRect().width,
                parent:node.parentElement?.outerHTML.slice(0, 500)}))
          })`);
          if (layout.scroll > layout.width + 1) {
            await b.screenshot(`overflow-${section}-${child}-${width}`, "workspace-pages-browser");
            overflows.push({ route: `${section}/${child}`, ...layout });
          }
        }
      }
      await openFunction(b, "retail", "register");
      assert.equal(await isVisible(b, "#pw-create-quote-title"), false);
      assert.equal(await isVisible(b, "#pw-quote-actions-title"), false);
      await openFunction(b, "governance", "purchasing-config");
      assert.equal(await isVisible(b, ".palette-admin, .capacity-section, .capacity-audit-register"), false);
      await b.screenshot(`function-pages-${width}`, "workspace-pages-browser");
      b.assertSafe();
      assert.equal(b.calls.filter(call => call.method !== "GET").length, 0,
        "Function navigation never submits a transaction or grant change");
      assert.deepEqual(overflows, [], `Function-page overflow at ${width}px: ${JSON.stringify(overflows)}`);
    } finally { await b.close(); }
  });
}

test("cold function links and native browser back/forward restore the selected child", async () => {
  const b = await browserHarness({ initialHash: "#/procurement/progress", mockApi: mockPages() });
  try {
    await assertFunction(b, "procurement", "progress");
    await openFunction(b, "inventory", "locations");
    await openFunction(b, "governance", "capacity-audit");
    await b.history(-1); await assertFunction(b, "inventory", "locations");
    await b.history(-1); await assertFunction(b, "procurement", "progress");
    await b.history(1); await assertFunction(b, "inventory", "locations");
    await b.navigate("#/delivery/returns");
    await assertFunction(b, "delivery", "returns");
    await b.reload();
    await assertFunction(b, "delivery", "returns");
    assert.equal(await isVisible(b, "#pw-create-quote-title, .capacity-section"), false);
    b.assertSafe();
  } finally { await b.close(); }
});

test("BU scope/theme survive child history and existing Favorites open useful default views", async () => {
  const b = await browserHarness({ width: 390, mockApi: mockPages() });
  try {
    await openFunction(b, "inventory", "main");
    await b.wait('document.querySelector(".inventory-view")?.textContent.includes("TEST-LEDGER-1")');
    await b.evaluate('document.querySelector(\'[aria-label="Business unit"]\').focus()');
    await b.key(" "); await b.key("ArrowDown"); await b.key("Enter");
    await b.wait('document.documentElement.dataset.unitTheme === "roche"');
    await b.wait('document.querySelector(".inventory-view")?.textContent.includes("TEST-LEDGER-2")');
    assert.equal(await b.evaluate('document.querySelector(".inventory-view")?.textContent.includes("TEST-LEDGER-1")'), false);
    await openFunction(b, "inventory", "locations");
    await b.history(-1); await assertFunction(b, "inventory", "main");
    assert.equal(await b.evaluate('document.documentElement.dataset.unitTheme'), "roche");
    await b.click(".my-page-link");
    await b.wait('!!document.querySelector(".my-favorite-open")');
    await b.evaluate('[...document.querySelectorAll(".my-favorite-open")].find(node => node.textContent.includes("Procurement")).focus()');
    await b.key("Enter");
    await assertFunction(b, "procurement", "main");
    b.assertSafe();
    assert.equal(b.calls.filter(call => call.method !== "GET").length, 1, "Only explicit mock BU selection POST");
  } finally { await b.close(); }
});

test("forbidden and unknown direct function links fail closed without privileged requests", async () => {
  const b = await browserHarness({ initialHash: "#/governance/capacity-admin",
    mockApi: mockPages({ privileged: false }) });
  try {
    await b.wait('!!document.querySelector("[data-workspace-unavailable]")');
    assert.equal(await isVisible(b, ".capacity-admin, .palette-admin, .report-panel-manager"), false);
    assert.equal(b.calls.some(call => call.path.startsWith("/api/governance/")), false);
    await b.navigate("#/procurement/not-a-function");
    await b.wait('!!document.querySelector("[data-workspace-unavailable]")');
    assert.equal(await isVisible(b, "#pw-receipt-title"), false);
    await b.navigate("#/retail/quote");
    await assertFunction(b, "retail", "quote");
    await b.wait('document.querySelector(".pw-transactions")?.textContent.includes("Transactions unavailable")');
    assert.equal(await b.evaluate(`document.querySelector("#pw-create-quote-title")?.closest("section")
      .querySelector('button[type="submit"]')?.disabled`), true);
    b.assertSafe();
  } catch (error) {
    console.error("Synthetic denied-route diagnostic", JSON.stringify({
      calls: b.calls,
      visible: await b.evaluate("document.body.innerText.slice(-5000)"),
    }));
    throw error;
  } finally { await b.close(); }
});
