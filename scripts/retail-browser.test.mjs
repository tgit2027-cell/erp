// Built App with synthetic loopback-only API fixtures, never shared preview data.
import assert from "node:assert/strict";
import { test } from "node:test";
import { browserHarness } from "./browser-harness.mjs";
import { openFunction, isVisible } from "./workspace-browser-navigation.mjs";

const page = (rows = []) => ({
  rows, pagination: { page: 1, page_size: 20, total_count: rows.length,
    total_pages: 1, previous_page: null, next_page: null },
});
const favorite = { destination: "retail", label: "Retail & Sales" };
const quote = (unit) => ({
  quote_id: unit * 101, business_unit_id: unit, kind: "stock", status: "approved",
  customer_id: 11, discount_percent: "0.00", total_list_value: "4000.00",
  total_discount: "0.00", total_quote_value: "4000.00", payment_plan: "full",
  downpayment_percent: null, special_order_value_bands_configured: false,
  currency: "QAR", created_at: "2031-04-05T09:00:00Z",
  lines: [{
    quote_line_id: unit * 10, variant_id: unit * 100, quantity: "2.00",
    selling_price_snapshot: "2000.00", price_revision_snapshot: 1,
    discount_percent: "0.00", discount_amount: "0.00",
    net_unit_price: "2000.00", net_total: "4000.00",
    specifications: { fabric: `TEST_ONLY_FABRIC_${unit}` },
  }],
  payments: [], approvals: [], holds: [], events: [],
  delivery_progress: {
    ready_at: null, delivered_at: null,
    lines: [{
      quote_line_id: unit * 10, variant_id: unit * 100, ordered_quantity: "2.00",
      reserved_to_date_quantity: "0.00", delivered_quantity: "0.00", remaining_quantity: "2.00",
    }],
  },
});

function mockApi(deny = false) {
  return async (req, url) => {
    if (req.method !== "GET") return null;
    if (url.pathname === "/api/favorites/") {
      return { data: { available_destinations: [favorite], favorites: [favorite] } };
    }
    if (url.pathname === "/api/purchasing/quotes/") {
      const unit = Number(url.searchParams.get("business_unit_id"));
      return deny ? { status: 404, data: { detail: "TEST ONLY existing unit access denied" } }
        : { data: page([quote(unit)]) };
    }
    if (/^\/api\/purchasing\/quotes\/\d+\/$/.test(url.pathname)) {
      const unit = Number(url.searchParams.get("business_unit_id"));
      const id = Number(url.pathname.split("/")[4]);
      return !deny && id === unit * 101 ? { data: quote(unit) }
        : { status: 404, data: { detail: "TEST ONLY current quote access denied" } };
    }
    if (/^\/api\/reports\/(inventory\/reservations|sales\/deliveries)\/$/.test(url.pathname) ||
        url.pathname === "/api/purchasing/storage-charges/") return { data: page() };
    return null;
  };
}

for (const width of [1280, 390]) {
  test(`Retail section Favorites, native lookup, BU theme and Delivery link at ${width}px`, async () => {
    const b = await browserHarness({ width, height: 900, mockApi: mockApi() });
    try {
      await b.click(".my-page-link");
      await b.wait('document.querySelector("h1")?.textContent === "My Page"');
      await b.click(".my-favorite-open");
      await b.wait('document.querySelector("#purchasing-workbench-title")?.textContent === "Retail & Sales"');
      assert.equal(await b.evaluate(
        'document.querySelector(\'[title="Retail & Sales"].nav-section-toggle\').getAttribute("aria-expanded")',
      ), "true");
      await openFunction(b, "retail", "actions");
      await b.click("#pw-quote-id"); await b.text("101"); await b.key("Enter");
      await b.wait('document.querySelector(".pw-quote")?.textContent.includes("TEST_ONLY_FABRIC_1")');
      await openFunction(b, "retail", "quote");
      assert.equal(await isVisible(b, "#pw-create-quote-title"), true);
      assert.equal(await b.evaluate('!!document.querySelector("#pw-receipt-title, #pw-sales-return-summary")'), false);
      assert.equal(await b.evaluate(
        '[...document.querySelectorAll("button")].some(n=>/Record delivery|Record readiness/.test(n.textContent))',
      ), false);
      await openFunction(b, "retail", "actions");
      assert.equal(await b.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), true);
      await b.screenshot(`retail-${width}`, "retail-browser");

      await b.evaluate('document.querySelector(\'[aria-label="Business unit"]\').focus()');
      await b.key("ArrowDown"); await b.key("Enter");
      await b.wait('document.documentElement.dataset.unitTheme === "roche"');
      await b.wait('document.querySelector("#pw-quote-id")?.value === ""');
      assert.equal(await b.evaluate('!!document.querySelector(".pw-quote")'), false);
      await b.click("#pw-quote-id"); await b.text("202"); await b.key("Enter");
      await b.wait('document.querySelector(".pw-quote")?.textContent.includes("TEST_ONLY_FABRIC_2")');
      assert.equal(await b.evaluate('document.body.textContent.includes("TEST_ONLY_FABRIC_1")'), false);
      await openFunction(b, "retail", "main");
      await b.click(".pw-retail-delivery-link button");
      await b.wait('document.querySelector("#purchasing-workbench-title")?.textContent === "Authorized quote lookup"');
      assert.equal(await b.evaluate("location.hash"), "#/delivery/quote");
      assert.equal(await b.evaluate('document.documentElement.dataset.unitTheme'), "roche");
      assert.deepEqual(b.calls.filter(call => call.method !== "GET").map(call => call.path),
        ["/api/units/2/select/"]);
      assert.equal(b.calls.some(call => /counterparties|commitments|purchase_history|governance/.test(call.path)), false);
      b.assertSafe();
    } finally {
      await b.close();
    }
  });
}

test("Retail favorite remains navigation only when existing unit access is denied", async () => {
  const b = await browserHarness({ mockApi: mockApi(true) });
  try {
    await b.click(".favorite-shortcut .nav-item");
    await b.wait('document.querySelector("#purchasing-workbench-title")?.textContent === "Retail & Sales"');
    await openFunction(b, "retail", "actions");
    await b.wait('document.querySelector(".pw-transactions")?.textContent.includes("Transactions unavailable")');
    await b.click("#pw-quote-id"); await b.text("101"); await b.key("Enter");
    await b.wait('document.querySelector(".purchasing-workbench")?.textContent.includes("TEST ONLY current quote access denied")');
    assert.equal(await b.evaluate('!!document.querySelector(".pw-quote")'), false);
    await openFunction(b, "retail", "quote");
    assert.equal(await b.evaluate(
      '!!document.querySelector(\'#pw-create-quote-title\').closest("section").querySelector(\'button[type="submit"]:not(:disabled)\')',
    ), false);
    assert.equal(b.calls.some(call => call.method !== "GET"), false);
    b.assertSafe();
  } finally {
    await b.close();
  }
});
