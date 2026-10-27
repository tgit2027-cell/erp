// @ts-nocheck
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceBudgetLab } from "./FinanceBudgetLab";
import { budgetValidDate } from "./finance-budget";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let calls: { path: string; method: string; body?: any }[];
let handler: ((call: { path: string; method: string; body?: any }) => Promise<any> | any) | null;
let saved: any;
const nativeFetch = globalThis.fetch;
const context = {
  legal_entity_label: "Synthetic review entity", business_units: [{ id: 7, code: "BU-7", name: "Review unit" }],
  accounts: [{ id: 11, code: "AC-11", name_en: "Services", name_ar: "خدمات" }],
  dimensions: [{ id: 3, code: "REGION", label_en: "Region", label_ar: "منطقة" }],
  dimension_values: [{ id: 31, dimension_id: 3, code: "NORTH", label_en: "North", label_ar: "الشمال" }],
};
const payload = {
  name: "Quarterly review", start_date: "2026-01-01", end_date: "2026-03-31", currency: "QAR" as const,
  entries: [{ start_date: "2026-01-01", end_date: "2026-01-31", amount: "0.10", account_id: 11,
    business_unit_id: 7, dimension_value_ids: [31], explanation: "مراجعة عربية" }],
};
function makeSaved(revision = 1, body = payload) {
  const snapshot = { ...body, id: 73, revision, review: { currency: "QAR", proposed_total: "0.10", entry_count: body.entries.length, warnings: [], posted: false } };
  return { ...snapshot, history: [{ revision, created_at: "synthetic timestamp", snapshot }] };
}
function response(data: any, status = 200) { return new Response(JSON.stringify(data), { status }); }

beforeEach(async () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  calls = []; handler = null; saved = makeSaved();
  globalThis.fetch = async (input: RequestInfo | URL, options: RequestInit = {}) => {
    const call = { path: String(input), method: String(options.method ?? "GET"), body: options.body ? JSON.parse(String(options.body)) : undefined };
    calls.push(call);
    const custom = await handler?.(call);
    if (custom) return response(custom.data, custom.status ?? 200);
    if (call.path === "/lab/session/") return response({ authenticated: true, csrf_token: "TEST" });
    if (call.path === "/lab/context/") return response(context);
    if (call.path === "/lab/budgets/" && call.method === "GET") return response({ scenarios: [{ id: saved.id, name: saved.name, revision: saved.revision }] });
    if (call.path === "/lab/budgets/73/" && call.method === "GET") return response(saved);
    if (call.method === "POST" || call.method === "PUT") {
      saved = makeSaved(call.method === "POST" ? 1 : saved.revision + 1, call.body);
      return response(saved);
    }
    return response({ detail: "Unexpected test endpoint." }, 404);
  };
  await act(async () => { root.render(<FinanceBudgetLab />); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); globalThis.fetch = nativeFetch;
});
function button(prefix: string) {
  const found = [...host.querySelectorAll("button")].find((item) => item.textContent?.startsWith(prefix));
  assert.ok(found, `Missing button: ${prefix}`); return found as HTMLButtonElement;
}
async function click(prefix: string) { await act(async () => { button(prefix).click(); }); }
function field(label: string) {
  const found = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | null;
  assert.ok(found, `Missing accessible field: ${label}`); return found;
}
async function fill(label: string, value: string) {
  const input = field(label);
  await act(async () => {
    const proto = input.tagName === "TEXTAREA" ? input.ownerDocument.defaultView!.HTMLTextAreaElement.prototype
      : input.tagName === "SELECT" ? input.ownerDocument.defaultView!.HTMLSelectElement.prototype
        : input.ownerDocument.defaultView!.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(input, value);
    input.dispatchEvent(new input.ownerDocument.defaultView!.Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}
async function fillValidScenario() {
  await click("New budget scenario");
  await fill("Budget scenario name", "Quarterly review");
  await fill("Scenario start date", "2026-01-01");
  await fill("Scenario end date", "2026-03-31");
  await fill("Budget row start date 1", "2026-01-01");
  await fill("Budget row end date 1", "2026-01-31");
  await fill("Budget row amount 1", "0.10");
  await fill("Budget row account 1", "11");
  await fill("Budget row business unit 1", "7");
  await fill("Budget row explanation 1", "مراجعة عربية");
}

test("exact minor units total entered rows once and explicit dates/account validation gates save", async () => {
  await fillValidScenario();
  await click("Add scenario row");
  await fill("Budget row start date 2", "2026-02-01");
  await fill("Budget row end date 2", "2026-03-31");
  await fill("Budget row amount 2", "0.20");
  await fill("Budget row account 2", "11");
  assert.match(host.textContent ?? "", /0\.30 QAR/);
  await click("Review worksheet");
  assert.equal(button("Save budget scenario").disabled, false);
  await click("Save budget scenario");
  const write = calls.find((call) => call.method === "POST")!;
  assert.equal(write.path, "/lab/budgets/");
  assert.equal(write.body.currency, "QAR");
  assert.deepEqual(write.body.entries.map((row: any) => row.amount), ["0.10", "0.20"]);
  assert.equal(write.body.entries[0].explanation, "مراجعة عربية");
  assert.match(host.textContent ?? "", /not posted and not operative authority/i);

  await fill("Scenario start date", "2026-02-01");
  await click("Review worksheet");
  assert.match(host.textContent ?? "", /dates must remain within the explicit scenario dates/);
  assert.equal(calls.filter((call) => ["POST", "PUT"].includes(call.method)).length, 1);
  await fill("Scenario start date", "2026-01-01");
  await fill("Budget row amount 1", "1.001");
  await click("Review worksheet");
  assert.match(host.textContent ?? "", /at most two decimal places/);
});

test("duplicate and inclusive overlap warnings are advisory and duplicate rows are retained", async () => {
  await fillValidScenario();
  await click("Add scenario row");
  await fill("Budget row start date 2", "2026-01-31");
  await fill("Budget row end date 2", "2026-02-01");
  await fill("Budget row amount 2", "0.20");
  await fill("Budget row account 2", "11");
  await fill("Budget row business unit 2", "7");
  assert.match(host.textContent ?? "", /Overlapping dates/);
  await fill("Budget row start date 2", "2026-01-01");
  await fill("Budget row end date 2", "2026-01-31");
  assert.match(host.textContent ?? "", /same dates but different amounts/);
  await fill("Budget row amount 2", "0.10");
  assert.match(host.textContent ?? "", /Possible duplicate/);
  await click("Review worksheet");
  assert.equal(button("Save budget scenario").disabled, false);
  await click("Save budget scenario");
  const write = calls.find((call) => call.method === "POST")!;
  assert.equal(write.body.entries.length, 2);
  assert.equal(write.body.entries[0].amount, "0.10");
  assert.equal(write.body.entries[1].amount, "0.10");
  assert.match(host.textContent ?? "", /each entered row counted once/);
});

test("budget-only dates reject year zero and rows reject reversed dates, out-of-scope dates, invalid amounts, and unavailable accounts", async () => {
  assert.equal(budgetValidDate("0000-01-01"), false);
  assert.equal(budgetValidDate("0001-01-01"), true);
  assert.equal(budgetValidDate("2026-02-29"), false);
  await fillValidScenario();
  await fill("Budget row start date 1", "2026-02-01");
  await fill("Budget row end date 1", "2026-01-31");
  await click("Review worksheet");
  assert.match(host.textContent ?? "", /start on or before the end/);
  await fill("Budget row start date 1", "2025-12-31");
  await fill("Budget row end date 1", "2026-01-31");
  await click("Review worksheet");
  assert.match(host.textContent ?? "", /within the explicit scenario dates/);
  await fill("Budget row start date 1", "2026-01-01");
  await fill("Budget row amount 1", "1000000000000.00");
  await click("Review worksheet");
  assert.match(host.textContent ?? "", /up to 999999999999\.99/);
  await fill("Budget row amount 1", "0.10");
  await fill("Budget row account 1", "999");
  await click("Review worksheet");
  assert.match(host.textContent ?? "", /choose an available account/);
  assert.equal(calls.filter((call) => ["POST", "PUT"].includes(call.method)).length, 0);
});

test("saved scenario reopens with revision history and edit sends expected_revision", async () => {
  await click("Open budget scenario 73:");
  assert.equal((field("Budget row explanation 1") as HTMLTextAreaElement).value, "مراجعة عربية");
  assert.ok(host.querySelector('[aria-label="Budget scenario revision history"]'));
  await fill("Budget row amount 1", "0.25");
  await click("Review worksheet");
  await click("Save budget scenario");
  const put = calls.filter((call) => call.method === "PUT").at(-1)!;
  assert.equal(put.path, "/lab/budgets/73/");
  assert.equal(put.body.expected_revision, 1);
  assert.equal(put.body.entries[0].amount, "0.25");
});

test("409 preserves local work; unknown save outcome blocks retries until explicit reopen", async () => {
  await click("Open budget scenario 73:");
  await fill("Budget row explanation 1", "Keep my work.");
  await click("Review worksheet");
  handler = (call) => call.method === "PUT" ? { status: 409, data: { detail: "Stale revision." } } : null;
  await click("Save budget scenario");
  assert.equal((field("Budget row explanation 1") as HTMLTextAreaElement).value, "Keep my work.");
  assert.match(host.textContent ?? "", /local worksheet remains unchanged/i);
  assert.equal(button("Save budget scenario").disabled, true);
  assert.equal(calls.filter((call) => call.method === "PUT").length, 1);

  handler = (call) => {
    if (call.method === "PUT") throw new TypeError("connection ended after commit");
    return null;
  };
  window.confirm = () => true;
  await click("Reload saved scenario");
  await fill("Budget row explanation 1", "Unknown result.");
  await click("Review worksheet");
  await click("Save budget scenario");
  assert.match(host.textContent ?? "", /database may have committed/);
  assert.equal(button("New budget scenario").disabled, true);
  assert.equal(calls.filter((call) => call.method === "PUT").length, 2);
  await click("Refresh saved list");
  await click("Open budget scenario 73:");
  assert.equal((field("Budget row explanation 1") as HTMLTextAreaElement).value, "مراجعة عربية");
  assert.equal(calls.filter((call) => call.method === "PUT").length, 2);
});

test("403 clears protected inputs and does not create setup or session", async () => {
  await click("Open budget scenario 73:");
  handler = (call) => call.path === "/lab/context/" ? { status: 403, data: { detail: "Denied." } } : null;
  await click("Open budget scenario 73:");
  assert.match(host.textContent ?? "", /Protected budget inputs were cleared/);
  assert.equal(host.querySelector('[data-testid="budget-editor"]'), null);
  assert.equal(calls.filter((call) => call.method === "POST").length, 0);
  assert.equal(calls.filter((call) => call.path === "/lab/session/" && call.method !== "GET").length, 0);
});

test("initial context and list 403 responses clear protected state", async () => {
  for (const forbiddenPath of ["/lab/context/", "/lab/budgets/"]) {
    await act(async () => root.unmount());
    handler = (call) => call.path === forbiddenPath ? { status: 403, data: { detail: "Denied." } } : null;
    root = createRoot(host);
    await act(async () => { root.render(<FinanceBudgetLab />); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    assert.match(host.textContent ?? "", /Protected budget inputs were cleared/);
    assert.equal(host.querySelector('[data-testid="budget-editor"]'), null);
  }
});

test("HTTP 5xx save outcome is uncertain and blocks retry or new worksheet", async () => {
  await fillValidScenario();
  await click("Review worksheet");
  handler = (call) => call.method === "POST" ? { status: 503, data: { detail: "Gateway unavailable." } } : null;
  await click("Save budget scenario");
  assert.match(host.textContent ?? "", /database may have committed/);
  assert.equal(button("Save budget scenario").disabled, true);
  assert.equal(button("New budget scenario").disabled, true);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
});

test("403 during post-commit list refresh is not swallowed and clears protected state", async () => {
  await fillValidScenario();
  await click("Review worksheet");
  let committed = false;
  handler = (call) => {
    if (call.method === "POST") { committed = true; return null; }
    if (committed && call.path === "/lab/budgets/" && call.method === "GET") {
      return { status: 403, data: { detail: "Session expired." } };
    }
    return null;
  };
  await click("Save budget scenario");
  assert.match(host.textContent ?? "", /Protected budget inputs were cleared/);
  assert.equal(host.querySelector('[data-testid="budget-editor"]'), null);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
  assert.equal(calls.filter((call) => call.path === "/lab/budgets/" && call.method === "GET").length, 2);
});
