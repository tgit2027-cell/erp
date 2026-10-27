import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import PurchasingWorkbench from "../src/PurchasingWorkbench";
import { sidebarSections } from "../src/sidebar-destinations";

const nativeFetch = globalThis.fetch;
let root, host, calls;
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const summary = (id, unit) => ({
  invoice_id: id, number: `U${unit}-INV-2026-0000${id}`, quote_id: 40 + id,
  customer: { code: "TEST-CUST", name: "TEST ONLY customer" }, issued_at: "2026-10-20T10:00:00Z",
  currency: "QAR", list_total: "4000.00", discount_total: "400.00", net_total: "3600.00",
  tax_rate_percent: "0.00", tax_total: "0.00", invoice_total: "3600.00",
  paid_before_delivery: "3240.00", balance_due: "360.00",
});
async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
async function render(unit, username = "retail_operator") {
  await act(async () => root.render(
    <PurchasingWorkbench
      mode="retail" selectedUnitId={unit} selectedUnitName={`Test Unit ${unit}`}
      isStaff={false} username={username} csrfToken="test-csrf" locations={[]}
      onOpenReports={() => {}} focusedView="invoices" onNavigateFunction={() => {}}
    />,
  ));
  await flush();
}
async function click(element) {
  assert.ok(element, "Expected an interactive control");
  await act(async () => element.click());
  await flush();
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  calls = [];
  globalThis.fetch = (path, options = {}) => {
    const url = new URL(String(path), "https://invoices.test.invalid");
    calls.push(url);
    assert.equal(options.method ?? "GET", "GET", "Customer invoices are read-only");
    const unit = Number(url.searchParams.get("business_unit_id"));
    if (url.pathname === "/api/purchasing/invoices/") {
      if (unit === 9) return Promise.resolve(json({ detail: "Not found." }, 404));
      const rows = url.searchParams.get("number") === "missing" ? [] : [summary(1, unit)];
      return Promise.resolve(json({
        business_unit: { id: unit, code: `U${unit}`, name: `Test Unit ${unit}` }, rows,
        pagination: { page: 1, total_count: rows.length, total_pages: 1, previous_page: null, next_page: null },
        test_only_label: "TEST ONLY provisional invoices",
      }));
    }
    if (url.pathname === "/api/purchasing/invoices/1/") {
      return Promise.resolve(json({
        ...summary(1, unit), business_unit: { id: unit, code: `U${unit}`, name: `Test Unit ${unit}` },
        issued_by: "retail_operator", policy_label: "TEST ONLY provisional invoice policy",
        lines: [{
          item_code: "TVF-002", description: "TEST ONLY sofa", brand_name: "Tivoli", quantity: "2.00",
          list_unit_price: "2000.00", discount_percent: "10.00", net_unit_price: "1800.00",
          line_total: "3600.00", journal_entry_ids: [7],
        }],
      }));
    }
    if (/^\/api\/governance\/purchasing\/\d+\/$/.test(url.pathname)) {
      return Promise.resolve(json({ test_only_label: "test", business_unit: { id: 1, name: "Unit" } }));
    }
    throw new Error(`Unexpected request ${url.pathname}`);
  };
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  globalThis.fetch = nativeFetch;
});

test("Retail lists customer invoices and opens one with its totals and balance due", async () => {
  const retail = sidebarSections({ isStaff: false }).find((section) => section.id === "retail");
  assert.ok(retail?.children.some((child) => child.id === "invoices" && child.favoriteable));
  await render(3);
  assert.ok(host.querySelector(".retail-invoices"));
  assert.match(host.textContent ?? "", /U3-INV-2026-00001/);
  assert.match(host.textContent ?? "", /QAR 360\.00/);
  await click([...host.querySelectorAll("button")].find((button) => button.textContent === "Open invoice"));
  const document = host.querySelector(".ri-document");
  assert.ok(document);
  assert.match(document.textContent ?? "", /TEST ONLY sofa/);
  assert.match(document.textContent ?? "", /Tax \(0\.00%\)/);
  assert.match(document.querySelector(".ri-due")?.textContent ?? "", /QAR 360\.00/);
  assert.equal(calls.at(-1)?.searchParams.get("business_unit_id"), "3");
  await click([...host.querySelectorAll("button")].find((button) => button.textContent === "Back to list"));
  assert.equal(host.querySelector(".ri-document"), null);
});

test("an unauthorized unit shows no invoices and a unit switch hides the open invoice", async () => {
  await render(3);
  await click([...host.querySelectorAll("button")].find((button) => button.textContent === "Open invoice"));
  assert.ok(host.querySelector(".ri-document"));
  await render(9);
  assert.equal(host.querySelector(".ri-document"), null);
  assert.match(host.textContent ?? "", /Invoice view not authorized/);
  assert.doesNotMatch(host.textContent ?? "", /INV-2026/);
});
