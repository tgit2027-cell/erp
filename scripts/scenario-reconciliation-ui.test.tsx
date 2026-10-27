import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import ScenarioReconciliation from "../src/ScenarioReconciliation";
import { sidebarSections } from "../src/sidebar-destinations";

const nativeFetch = globalThis.fetch;
let root, host, responder;
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const check = (area, label, matches = true) => ({
  area, label, unit: "QAR", expected: "12000.00", actual: matches ? "12000.00" : "11999.00",
  difference: matches ? "0.00" : "-1.00", matches,
});
const report = (matches) => ({
  business_unit: { id: 4, code: "TVL", name: "Tivoli" }, currency: "QAR",
  summary: { supplier_value: "10000.00", landed_charges: "2000.00", received_value: "12000.00", stock_value: "9600.00",
    revenue: "4000.00", cost_of_goods_sold: "2400.00", gross_margin: "1600.00", invoiced_total: "4000.00",
    collected_on_invoices: "4000.00", still_to_collect: "0.00", invoice_count: 1 },
  checks: [check("receipts", "Received stock value"), check("stock", "Stock on hand value", matches)],
  all_match: matches, policy_label: "TEST ONLY reconciliation",
});
async function render() {
  await act(async () => root.render(<ScenarioReconciliation selectedUnitId={4} selectedUnitName="Tivoli" />));
  await act(async () => { await new Promise(setImmediate); });
}
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  globalThis.fetch = (path, options = {}) => {
    assert.equal(options.method, "GET");
    assert.equal(new URL(String(path), "https://r.test").pathname, "/api/reports/scenario-reconciliation/");
    return Promise.resolve(responder());
  };
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  globalThis.fetch = nativeFetch;
});

test("Reports shows the reconciliation page to staff only", () => {
  const reports = (access) => sidebarSections(access).find((section) => section.id === "reports")?.children.map((child) => child.id);
  assert.deepEqual(reports({ isStaff: true }), ["main", "reconciliation"]);
  assert.deepEqual(reports({ isStaff: false }), ["main"]);
});

test("a clean run shows the summary and every check matching", async () => {
  responder = () => json(report(true));
  await render();
  assert.match(host.querySelector(".sr-verdict")?.textContent ?? "", /Everything reconciles.*2 of 2/);
  assert.match(host.textContent ?? "", /Gross marginQAR 1600\.00/);
  assert.equal(host.querySelectorAll(".sr-off").length, 0);
});

test("a mismatch is highlighted with its difference", async () => {
  responder = () => json(report(false));
  await render();
  assert.match(host.querySelector(".sr-verdict")?.textContent ?? "", /Some figures do not agree.*1 of 2/);
  const row = host.querySelector(".sr-off");
  assert.match(row?.textContent ?? "", /Stock on hand value.*QAR -1\.00.*Mismatch/);
});

test("a non-staff account sees no figures", async () => {
  responder = () => json({ detail: "A synthetic Django staff account is required." }, 403);
  await render();
  assert.match(host.textContent ?? "", /Reconciliation not authorized/);
  assert.doesNotMatch(host.textContent ?? "", /QAR/);
});
