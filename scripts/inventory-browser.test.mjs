// Real built App + Chromium; synthetic loopback mocks only, never Django.
import assert from "node:assert/strict";
import { test } from "node:test";
import { browserHarness } from "./browser-harness.mjs";

function warehouseApi(denySecondDetail = false) {
  let selected = 1;
  const units = [
    { id: 1, code: "TVL", name: "TEST ONLY Tivoli", theme_palette: "tivoli" },
    { id: 2, code: "RBB", name: "TEST ONLY Roche", theme_palette: "roche" },
  ];
  const row = (unit) => ({
    id: unit * 101, item_code: `TEST-ITEM-${unit}`,
    item_description: `TEST ONLY warehouse item ${unit}`,
    brand_name: units[unit - 1].name, brand_code: units[unit - 1].code,
    variant_sku: `TEST-SKU-${unit}`, variant_description: "TEST ONLY variant",
    location_code: `TEST-LOC-${unit}`, location_name: `TEST ONLY warehouse ${unit}`,
    unit_code: units[unit - 1].code, unit_name: units[unit - 1].name,
    quantity: "7.00", is_synthetic: true,
  });
  return async (req, url) => {
    if (req.method === "POST" && /^\/api\/units\/[12]\/select\/$/.test(url.pathname)) {
      selected = Number(url.pathname.split("/")[3]);
      return null; // Existing harness's explicit synthetic BU-select handler.
    }
    if (req.method !== "GET") return null;
    if (url.pathname === "/api/organization/") return { data: {
      company: { code: "TEST", name: "TEST ONLY warehouse company" },
      units, selected_unit: units[selected - 1],
      locations: [{ id: selected * 11, code: `TEST-LOC-${selected}`,
        name: `TEST ONLY warehouse ${selected}`, parent_id: null }],
    } };
    if (url.pathname === "/api/reports/") return { data: {
      panels: [], scopes: { business_units: [], locations: [], legal_entities: [], capabilities: [] },
    } };
    if (url.pathname === "/api/warehouse-capacity/") return { data: { results: [] } };
    if (url.pathname === "/api/stock-balances/" &&
        url.searchParams.get("unit") === String(selected)) {
      const location = url.searchParams.get("location");
      assert.ok(!location || location === String(selected * 11), "No stale location request");
      const data = row(selected);
      const search = (url.searchParams.get("search") ?? "").toLowerCase();
      const rows = [data].filter(value => Object.values(value).some(
        field => String(field).toLowerCase().includes(search)));
      return { data: { results: rows, pagination: {
        page: 1, page_size: 50, total_count: rows.length, total_pages: 1,
        previous_page: null, next_page: null,
      } } };
    }
    if (/^\/api\/stock-balances\/\d+\/$/.test(url.pathname) &&
        url.searchParams.get("unit") === String(selected)) {
      assert.equal(Number(url.pathname.split("/")[3]), selected * 101);
      const location = url.searchParams.get("location");
      assert.ok(!location || location === String(selected * 11));
      return selected === 2 && denySecondDetail
        ? { status: 403, data: { detail: "TEST ONLY current stock detail denied" } }
        : { data: row(selected) };
    }
    return null;
  };
}

async function captureFailure(b, name) {
  await b.screenshot(name, "inventory-browser");
  console.log("Synthetic browser diagnostic", JSON.stringify({
    calls: b.calls,
    dom: await b.evaluate(`(() => {
      const node = document.querySelector(".inventory-view tbody tr button");
      const r = node?.getBoundingClientRect();
      return { detail: document.querySelector(".inventory-bucket-detail")?.textContent,
        row: node?.outerHTML, rect: r?.toJSON(),
        hit: r && document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML,
        active: document.activeElement?.outerHTML };
    })()`),
  }));
}

async function assertDetailContrast(b) {
  const results = await b.evaluate(`(() => {
    const rgb = value => {
      const match = value.match(/^rgba?\\(([^)]+)\\)$/);
      if (!match) throw new Error("Unsupported computed color: " + value);
      return match[1].split(",").map(Number);
    };
    const luminance = channels => {
      const linear = channels.slice(0,3).map(value => {
        const c = value / 255;
        return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
      });
      return linear[0]*0.2126 + linear[1]*0.7152 + linear[2]*0.0722;
    };
    return [...document.querySelectorAll(".inventory-bucket-fields dt")].map(label => {
      let node = label, background = null;
      while (node) {
        const channels = rgb(getComputedStyle(node).backgroundColor);
        if (channels.length === 3 || channels[3] === 1) { background = channels; break; }
        if (channels[3] !== 0) throw new Error("Unexpected translucent label background");
        node = node.parentElement;
      }
      if (!background) throw new Error("No opaque label background");
      const a = luminance(rgb(getComputedStyle(label).color)), c = luminance(background);
      return { label: label.textContent, ratio: (Math.max(a,c)+0.05)/(Math.min(a,c)+0.05) };
    });
  })()`);
  assert.ok(results.length >= 8, "Actual rendered detail labels measured");
  for (const result of results) assert.ok(result.ratio >= 4.5,
    `${result.label} needs 4.5:1 contrast, observed ${result.ratio.toFixed(2)}:1`);
}

for (const width of [1280, 390]) {
  test(`Inventory native location/code lookup and quantity detail remain BU-scoped at ${width}px`, async () => {
    const b = await browserHarness({ width, height: 900, mockApi: warehouseApi() });
    try {
      await b.click('[title="Inventory & Warehouses"].nav-section-toggle');
      await b.evaluate(`[...document.querySelectorAll("button")].find(
        n => n.textContent.trim() === "Inventory quantities")?.click()`);
      await b.wait('!!document.querySelector(".inventory-heading")');
      if (width < 760) {
        // The active parent's existing narrow-screen flyout stays open until
        // its disclosure is collapsed; dismiss it before using page controls.
        await b.click('[title="Inventory & Warehouses"].nav-section-toggle');
        await b.wait('document.querySelector(\'[title="Inventory & Warehouses"].nav-section-toggle\')?.getAttribute("aria-expanded") === "false"');
      }
      await b.wait('document.querySelector(".inventory-view tbody")?.textContent.includes("TEST-SKU-1")');
      await b.click('[aria-label="Search inventory items"]');
      await b.text("TEST-LOC-1"); await b.key("Enter");
      await b.wait('document.querySelector(".inventory-view tbody")?.textContent.includes("TEST-LOC-1")');
      await b.evaluate('document.querySelector(\'[aria-label="Inventory location filter"]\').focus()');
      await b.key("ArrowDown"); await b.key("Enter");
      await b.wait('document.querySelector(\'[aria-label="Inventory location filter"]\')?.value === "11"');
      await b.click(".inventory-view tbody tr button");
      await b.wait('document.querySelector(".inventory-bucket-detail")?.textContent.includes("TEST-SKU-1")');
      assert.equal(await b.evaluate(
        'document.querySelector(".inventory-bucket-detail")?.textContent.includes("7.00")'), true);
      await assertDetailContrast(b);
      assert.equal(await b.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), true);
      await b.screenshot(`inventory-${width}`, "inventory-browser");
      await b.evaluate('document.querySelector(\'[aria-label="Business unit"]\').focus()');
      await b.key("ArrowDown"); await b.key("Enter");
      await b.wait('document.documentElement.dataset.unitTheme === "roche"');
      assert.equal(await b.evaluate('!!document.querySelector(".inventory-bucket-detail")'), false);
      assert.equal(await b.evaluate('document.body.textContent.includes("TEST-SKU-1")'), false);
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Inventory location filter"]\')?.value'), "");
      await b.click('[aria-label="Search inventory items"]');
      await b.evaluate('document.querySelector(\'[aria-label="Search inventory items"]\').select()');
      await b.key("Backspace"); await b.text("TEST-SKU-2"); await b.key("Enter");
      await b.wait('document.querySelector(".inventory-view tbody")?.textContent.includes("TEST-SKU-2")');
      await b.click(".inventory-view tbody tr button");
      await b.wait('document.querySelector(".inventory-bucket-detail")?.textContent.includes("TEST-SKU-2")');
      await assertDetailContrast(b);
      assert.deepEqual(b.calls.filter(call => call.method !== "GET").map(call => call.path),
        ["/api/units/2/select/"]);
      b.assertSafe();
    } catch (error) {
      await captureFailure(b, `inventory-failure-${width}`);
      throw error;
    } finally {
      await b.close();
    }
  });
}

test("Inventory current detail denial displays no prior-unit bucket", async () => {
  const b = await browserHarness({ mockApi: warehouseApi(true) });
  try {
    await b.click('[title="Inventory & Warehouses"].nav-section-toggle');
    await b.evaluate(`[...document.querySelectorAll("button")].find(
      n => n.textContent.trim() === "Inventory quantities")?.click()`);
    await b.wait('document.querySelector(".inventory-view tbody")?.textContent.includes("TEST-SKU-1")');
    await b.click(".inventory-view tbody tr button");
    await b.wait('document.querySelector(".inventory-bucket-detail")?.textContent.includes("TEST-SKU-1")');
    await b.evaluate('document.querySelector(\'[aria-label="Business unit"]\').focus()');
    await b.key("ArrowDown"); await b.key("Enter");
    await b.wait('document.documentElement.dataset.unitTheme === "roche"');
    await b.click('[aria-label="Search inventory items"]');
    await b.evaluate('document.querySelector(\'[aria-label="Search inventory items"]\').select()');
    await b.key("Backspace"); await b.key("Enter");
    await b.wait('document.querySelector(".inventory-view tbody")?.textContent.includes("TEST-SKU-2")');
    await b.click(".inventory-view tbody tr button");
    await b.wait('document.querySelector(".inventory-bucket-detail")?.textContent.includes("This bucket is not available in the current business-unit and location scope.")');
    assert.equal(await b.evaluate('document.body.textContent.includes("TEST-SKU-1")'), false);
    assert.deepEqual(b.calls.filter(call => call.method !== "GET").map(call => call.path),
      ["/api/units/2/select/"]);
    b.assertSafe();
  } catch (error) {
    await captureFailure(b, "inventory-denial-failure");
    throw error;
  } finally {
    await b.close();
  }
});
