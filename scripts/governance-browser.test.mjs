import assert from "node:assert/strict";
import { test } from "node:test";
import { browserHarness } from "./browser-harness.mjs";
import { openFunction } from "./workspace-browser-navigation.mjs";

const group = '.section-navigation-group:has([title="Governance & Administration"])';
const units = [
  { id: 1, code: "TVL", name: "TEST ONLY Tivoli", theme_palette: "tivoli" },
  { id: 2, code: "RBB", name: "TEST ONLY Roche", theme_palette: "roche" },
];
const sentinel = "TEST_SENSITIVE_CAPACITY_OR_COST_NEVER_RENDER";

function mockedGovernance({ staff, superuser, manager }) {
  let selected = 1;
  return async (req, url) => {
    const get = req.method === "GET";
    if (get && url.pathname === "/api/auth/me/") return { data: {
      authenticated: true, username: "TEST_ONLY_GOVERNANCE",
      is_staff: staff, is_superuser: superuser, can_manage_report_panels: manager,
    } };
    if (get && url.pathname === "/api/organization/") return { data: {
      company: { code: "TEST", name: "TEST ONLY company" },
      units, selected_unit: units[selected - 1], locations: [],
    } };
    if (get && url.pathname === "/api/favorites/") {
      const destination = staff ? "governance" : "reports";
      const label = staff ? "Governance & Administration" : "Reports";
      return { data: { available_destinations: [{ destination, label }], favorites: [{ destination, label }] } };
    }
    if (req.method === "POST" && /^\/api\/units\/[12]\/select\/$/.test(url.pathname)) {
      selected = Number(url.pathname.split("/")[3]);
      return { data: { selected_unit: units[selected - 1] } };
    }
    if (get && url.pathname === "/api/stock-balances/") {
      return { status: 403, data: { detail: "TEST ONLY no stock capability in this browser fixture" } };
    }
    if (get && url.pathname === "/api/governance/business-unit-palettes/") return {
      status: staff ? 200 : 403,
      data: staff ? {
        palettes: [
          { value: "tivoli", label: "Tivoli orange", kind: "built_in", colors: {} },
          { value: "roche", label: "Roche light blue", kind: "built_in", colors: {} },
        ],
        units: units.map(unit => ({ ...unit, palette_value: unit.theme_palette })),
      } : { detail: "TEST ONLY staff required" },
    };
    if (get && url.pathname === "/api/governance/report-panels/") return {
      status: manager ? 200 : 403,
      data: manager ? {
        users: [{ id: 71, username: "TEST_ONLY_recipient" }], roles: [],
        panels: [{ code: "sales", label: "Sales" }], panel_grants: [],
      } : { detail: "TEST ONLY panel management denied" },
    };
    if (get && url.pathname === "/api/reports/") return { data: { panels: [], scopes: {} } };
    if (get && url.pathname === "/api/report-views/") return { data: { views: [] } };
    if (get && /^\/api\/governance\/purchasing\/[12]\/$/.test(url.pathname)) {
      const id = Number(url.pathname.split("/")[4]);
      return { status: staff ? 200 : 403, data: staff ? {
        test_only_label: `TEST ONLY configuration ${id}`,
        business_unit: units[id - 1], role_assignments: [], current_prices: [],
        work_calendar: { is_configured: false, closed_weekdays: [], closed_dates: [] },
        storage_exception_approver_roles: [], special_order_value_bands: [],
        special_order_bands_used_for_approval: false,
        special_order_bands_note: "TEST ONLY no approved bands",
        supplier_cost: sentinel,
      } : { detail: "TEST ONLY staff required" } };
    }
    if (get && url.pathname === "/api/governance/warehouse-capacity/") return {
      status: superuser ? 200 : 403, data: superuser ? {
        locations: [{
          id: 31, code: "TEST-WH", name: "TEST ONLY physical location",
          physical_capacity_configured: true, business_units: [], assignments: [],
          total_floor_area_m2: sentinel,
        }], synthetic_users: [{ id: 71, username: "TEST_ONLY_recipient" }],
      } : { detail: "TEST ONLY system administrator required" },
    };
    if (get && url.pathname === "/api/governance/warehouse-capacity/audit/") return {
      status: superuser ? 200 : 403, data: superuser ? {
        results: [{
          id: 9, kind: "assignment_revoked", label: "Assignment revoked",
          location: { id: 31, code: "TEST-WH", name: "TEST ONLY physical location" },
          actor: "TEST_ONLY_admin", target_user: "TEST_ONLY_recipient",
          capability: "view_capacity", created_at: "2032-02-03T10:00:00Z",
          before_values: { total_floor_area_m2: sentinel },
        }],
        pagination: { page: 1, page_size: 20, total_count: 1, total_pages: 1, previous_page: null, next_page: null },
      } : { detail: "TEST ONLY system administrator required" },
    };
    return null;
  };
}

async function keyboardChild(b, label) {
  const parent = `${group} .nav-section-toggle`;
  if (await b.evaluate(`document.querySelector(${JSON.stringify(parent)}).getAttribute("aria-expanded") !== "true"`)) {
    await b.evaluate(`document.querySelector(${JSON.stringify(parent)}).focus()`);
    await b.key("Enter");
  }
  await b.evaluate(`[...document.querySelectorAll(${JSON.stringify(`${group} .section-navigation-children button`)})]
    .find(button => button.textContent.trim() === ${JSON.stringify(label)}).focus()`);
  await b.key("Enter");
  await b.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
}

for (const width of [1280, 390, 320]) {
  test(`Governance native navigation, independent admin controls and BU review fit ${width}px`, async () => {
    const b = await browserHarness({
      width, height: 900,
      mockApi: mockedGovernance({ staff: true, superuser: true, manager: true }),
    });
    try {
      await b.wait('!!document.querySelector(".favorite-shortcut .nav-item")');
      await b.click(".favorite-shortcut .nav-item");
      await b.wait('!!document.querySelector(".governance-workspace")');
      await openFunction(b, "governance", "presentation");
      assert.equal(await b.evaluate('!!document.querySelector(".planned-panel")'), false);
      await keyboardChild(b, "Business-unit palettes");
      await b.wait('!!document.querySelector(".palette-admin select")');
      if (width < 760) await b.click(`${group} .nav-section-toggle`);
      await b.evaluate('document.querySelector(".palette-admin select").focus()');
      await b.key("Tab");
      const focus = await b.evaluate(`({
        element: document.activeElement.outerHTML,
        outline: getComputedStyle(document.activeElement).outlineStyle,
        shadow: getComputedStyle(document.activeElement).boxShadow,
        visible: document.activeElement.matches(":focus-visible")
      })`);
      assert.ok(focus.visible && (focus.outline !== "none" || focus.shadow !== "none"), JSON.stringify(focus));

      await keyboardChild(b, "Purchasing configuration review");
      await b.wait('document.body.textContent.includes("TEST ONLY configuration 1")');
      if (width < 760) await b.click(`${group} .nav-section-toggle`);
      await b.click('[aria-label="Business unit"]');
      await b.key("ArrowDown"); await b.key("Enter");
      await b.wait('document.body.textContent.includes("TEST ONLY configuration 2")');
      assert.equal(await b.evaluate('document.body.textContent.includes("TEST ONLY configuration 1")'), false);
      assert.equal(await b.evaluate('document.documentElement.dataset.unitTheme'), "roche");

      await keyboardChild(b, "Capacity locations & assignments");
      await b.wait('!!document.querySelector(".capacity-section")');
      await keyboardChild(b, "Warehouse capacity audit");
      await b.wait('document.body.textContent.includes("Assignment revoked")');
      assert.equal(await b.evaluate(`document.body.textContent.includes(${JSON.stringify(sentinel)})`), false);
      if (width < 760) await b.click(`${group} .nav-section-toggle`);
      await b.screenshot(`governance-${width}`, "governance-browser");
      const layout = await b.evaluate(`({
        width: innerWidth, scroll: document.documentElement.scrollWidth,
        outside: [...document.querySelectorAll("body *")]
          .filter(node => node.getBoundingClientRect().right > innerWidth + 1)
          .slice(0, 12).map(node => ({tag:node.tagName, class:node.className, width:node.getBoundingClientRect().width}))
      })`);
      assert.ok(layout.scroll <= layout.width + 1, JSON.stringify(layout));
      assert.equal(b.calls.some(call => call.path === "/api/warehouse-capacity/"), false,
        "Administration-only presentation must not fetch capacity measurements");
      assert.equal(b.calls.some(call => call.path === "/api/governance/reports/"), false,
        "Panel assignment must not load broader role/data-grant administration");
      assert.ok(b.calls.filter(call => call.method !== "GET")
        .every(call => /^\/api\/units\/[12]\/select\/$/.test(call.path)));
      b.assertSafe();
    } finally { await b.close(); }
  });
}

test("nonstaff report manager retains existing Reports Favorite without staff or capacity requests", async () => {
  const b = await browserHarness({
    width: 390, height: 900,
    mockApi: mockedGovernance({ staff: false, superuser: false, manager: true }),
  });
  try {
    await b.wait('!!document.querySelector(".favorite-shortcut .nav-item")');
    await b.click(".favorite-shortcut .nav-item");
    await b.wait('!!document.querySelector(".reports-area")');
    await b.click(".report-manage-link button");
    await b.wait('!!document.querySelector(".report-panel-manager")');
    assert.equal(await b.evaluate("location.hash"), "#/governance/report-panels");
    assert.equal(await b.evaluate('!!document.querySelector(".palette-admin, .capacity-section, .capacity-audit-register")'), false);
    assert.equal(b.calls.some(call => /\/governance\/(business-unit-palettes|warehouse-capacity|purchasing)\//.test(call.path)), false);
    assert.deepEqual(b.calls.filter(call => call.method !== "GET"), []);
    b.assertSafe();
  } finally { await b.close(); }
});
