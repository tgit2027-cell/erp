// Actual built App/Chromium, synthetic GET fixtures only; no Django or preview DB.
import assert from "node:assert/strict";
import { test } from "node:test";
import { browserHarness } from "./browser-harness.mjs";
import { openFunction } from "./workspace-browser-navigation.mjs";

const page = (rows = []) => ({
  rows, pagination: { page: 1, page_size: 20, total_count: rows.length,
    total_pages: 1, previous_page: null, next_page: null },
});
const quote = {
  quote_id: 101, business_unit_id: 1, kind: "stock", status: "delivered",
  customer_id: 11, discount_percent: "0.00", total_list_value: "4000.00",
  total_discount: "0.00", total_quote_value: "4000.00", payment_plan: "full",
  downpayment_percent: null, special_order_value_bands_configured: false,
  lines: [{
    quote_line_id: 10, variant_id: 100, quantity: "2.00", selling_price_snapshot: "2000.00",
    price_revision_snapshot: 1, discount_percent: "0.00", discount_amount: "0.00",
    net_unit_price: "2000.00", net_total: "4000.00", specifications: {},
  }],
  payments: [], approvals: [], holds: [], events: [],
  delivery_progress: {
    ready_at: "2031-04-05T09:07:06Z", delivered_at: "2031-04-06T11:12:13Z",
    lines: [{ quote_line_id: 10, variant_id: 100, ordered_quantity: "2.00",
      reserved_to_date_quantity: "2.00", delivered_quantity: "2.00", remaining_quantity: "0.00" }],
  },
};

for (const width of [1280, 390]) {
  test(`Delivery section is usable and scoped at ${width}px with synthetic GET-only APIs`, async () => {
    const b = await browserHarness({
      width, height: 900,
      async mockApi(req, url) {
        if (req.method !== "GET") return null;
        if (url.pathname === "/api/purchasing/quotes/" ||
            url.pathname === "/api/purchasing/storage-charges/" ||
            url.pathname === "/api/purchasing/returns/") return { data: page() };
        if (url.pathname === "/api/purchasing/quotes/101/") {
          return url.searchParams.get("business_unit_id") === "1"
            ? { data: quote } : { status: 404, data: { detail: "TEST ONLY scope denied" } };
        }
        if (/^\/api\/reports\/(inventory\/reservations|sales\/deliveries)\/$/.test(url.pathname)) {
          return { data: page() };
        }
        return null;
      },
    });
    try {
      await b.click('[title="Delivery & After-Sales"].nav-section-toggle');
      await b.click('.section-navigation-children [aria-label="Delivery & After-Sales"]');
      await openFunction(b, "delivery", "quote");
      await b.wait('!!document.querySelector("#pw-quote-id")');
      await b.click("#pw-quote-id");
      await b.text("101");
      await b.key("Enter");
      await b.wait('!!document.querySelector(\'[aria-label="Read-only delivery progress"]\')');
      assert.match(await b.evaluate('document.querySelector(".pw-quote").textContent'), /delivered/);
      assert.match(await b.evaluate('document.querySelector(\'[aria-label="Read-only delivery progress"]\').textContent'),
        /2031-04-06T11:12:13Z/);
      assert.equal(await b.evaluate('!!document.querySelector("#pw-create-quote-title, #pw-receipt-title")'), false);
      await openFunction(b, "delivery", "handover");
      assert.equal(await b.evaluate('[...document.querySelectorAll("button")].some(n=>n.textContent==="Record delivery"&&!n.disabled)'), false);
      await openFunction(b, "delivery", "returns");
      assert.equal(await b.evaluate('[...document.querySelectorAll("summary")].some(n=>n.textContent.includes("Sales-return intake"))'), true);
      assert.equal(await b.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), true,
        "Delivery page must not cause page-level horizontal overflow");
      assert.equal(b.calls.some(call => /counterparties|commitments|purchase_history|governance/.test(call.path)), false);
      assert.equal(b.calls.some(call => call.method === "POST"), false);
      await b.screenshot(`delivery-${width}`, "delivery-browser");
      b.assertSafe();
    } finally {
      await b.close();
    }
  });
}
