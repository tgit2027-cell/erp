// @ts-nocheck
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceForecastLab } from "./FinanceForecastLab";
import {
  blankForecastDraft, calculateForecast, payloadFromForecast, validForecastDate, validForecastMoney, validateForecast,
} from "./finance-forecast";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let calls: { path: string; method: string; body?: any }[];
let handler: ((call: { path: string; method: string; body?: any }) => Promise<any> | any) | null;
let saved: any;
let freshContext: any;
const nativeFetch = globalThis.fetch;
const nativeConfirm = window.confirm;
const context = {
  legal_entity_label: "Synthetic forecast entity",
  business_units: [{ id: 7, code: "BU-7", name: "Review unit" }],
  accounts: [],
  dimensions: [{ id: 3, code: "REGION", label_en: "Region", label_ar: "منطقة" }],
  dimension_values: [
    { id: 31, dimension_id: 3, code: "NORTH", label_en: "North", label_ar: "الشمال" },
    { id: 32, dimension_id: 3, code: "SOUTH", label_en: "South", label_ar: "الجنوب" },
  ],
};
const payload = {
  name: "  تدفق نقدي ",
  as_of_date: "2024-02-29",
  opening_balance: "-12.30",
  currency: "QAR" as const,
  entries: [{
    date: "2024-03-01", direction: "inflow" as const, amount: "0.10",
    description: "  إيراد — text retained  ", business_unit_id: 7, dimension_value_ids: [31, 32],
  }],
};
function detail(revision = 1, body = payload) {
  const entries = body.entries.map((entry: any, index: number) => ({
    ...entry, line_number: index + 1, running_balance: "0.00",
  }));
  const review = {
    currency: "QAR", opening_balance: body.opening_balance, total_inflows: "0.10", total_outflows: "0.00",
    net_change: "0.10", closing_balance: "-12.20", ordering: "date_ascending_then_input_order",
    proposed: true, actual_cash: false, posted: false, approved: false, entries,
  };
  const snapshot = { ...body, id: 73, revision, review };
  return { ...snapshot, history: [{ revision, created_at: "synthetic timestamp", snapshot }] };
}
function response(data: any, status = 200) { return new Response(JSON.stringify(data), { status }); }
beforeEach(async () => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  calls = [];
  handler = null;
  freshContext = context;
  saved = detail();
  globalThis.fetch = async (input: RequestInfo | URL, options: RequestInit = {}) => {
    const call = {
      path: String(input),
      method: String(options.method ?? "GET"),
      body: options.body ? JSON.parse(String(options.body)) : undefined,
    };
    calls.push(call);
    const custom = await handler?.(call);
    if (custom) {
      if (custom.throw) throw custom.throw;
      return response(custom.data, custom.status ?? 200);
    }
    if (call.path === "/lab/session/") return response({ authenticated: true, csrf_token: "TEST" });
    if (call.path === "/lab/context/") return response(freshContext);
    if (call.path === "/lab/forecasts/" && call.method === "GET") {
      return response({ forecasts: saved ? [{ id: saved.id, name: saved.name, revision: saved.revision }] : [] });
    }
    if (call.path === "/lab/forecasts/73/" && call.method === "GET") return response(saved);
    if (call.method === "POST" || call.method === "PUT") {
      saved = detail(call.method === "POST" ? 1 : saved.revision + 1, call.body);
      return response(saved);
    }
    return response({ detail: "Unexpected test endpoint." }, 404);
  };
  await act(async () => { root.render(<FinanceForecastLab />); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  globalThis.fetch = nativeFetch;
  window.confirm = nativeConfirm;
});
function button(prefix: string) {
  const found = [...host.querySelectorAll("button")].find((item) =>
    item.textContent?.startsWith(prefix) || item.getAttribute("aria-label")?.startsWith(prefix));
  assert.ok(found, `Missing button: ${prefix}`);
  return found as HTMLButtonElement;
}
async function click(prefix: string) { await act(async () => { button(prefix).click(); }); }
function field(label: string) {
  const found = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | null;
  assert.ok(found, `Missing accessible field: ${label}`);
  return found;
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
async function selectMany(label: string, ids: string[]) {
  const select = field(label) as HTMLSelectElement;
  await act(async () => {
    for (const option of [...select.options]) option.selected = ids.includes(option.value);
    select.dispatchEvent(new select.ownerDocument.defaultView!.Event("change", { bubbles: true }));
  });
}
async function startDraft() { await click("New forecast draft"); }
async function fillValid() {
  await startDraft();
  await fill("Forecast name", "  تدفق نقدي ");
  await fill("Opening as-of date", "2024-02-29");
  await fill("Opening QAR balance", "-12.30");
  await fill("Flow date 1", "2024-03-01");
  await fill("Flow direction 1", "inflow");
  await fill("Flow amount 1", "0.10");
  await fill("Flow description 1", "  إيراد — text retained  ");
  await fill("Flow business unit 1", "7");
  await selectMany("Flow dimensions 1", ["31", "32"]);
}

test("exact decimal and real calendar date validation; blanks are untouched", () => {
  assert.equal(validForecastMoney("-0.01", true), true);
  assert.equal(validForecastMoney("999999999999.99", true), true);
  assert.equal(validForecastMoney("1e2", true), false);
  assert.equal(validForecastMoney("1.001", true), false);
  assert.equal(validForecastMoney("1000000000000", true), false);
  assert.equal(validForecastMoney("-1", false), false);
  assert.equal(validForecastDate("2024-02-29"), true);
  assert.equal(validForecastDate("2025-02-29"), false);
  assert.equal(validForecastDate("2024-02-30"), false);
  const blank = blankForecastDraft();
  assert.equal(blank.name, "");
  assert.equal(blank.as_of_date, "");
  assert.equal(blank.opening_balance, "");
  assert.equal(blank.entries[0].date, "");
  assert.equal(blank.entries[0].amount, "");
  assert.equal(blank.entries[0].business_unit_id, "");
  assert.ok(validateForecast(blank, context).length);
});

test("local arithmetic handles negative running balances and date sort with stable same-date input order", () => {
  const input = {
    ...payload, opening_balance: "-1.00",
    entries: [
      { date: "2024-03-04", direction: "outflow" as const, amount: "2.20", description: "later input first", business_unit_id: null, dimension_value_ids: [] },
      { date: "2024-03-02", direction: "inflow" as const, amount: "0.40", description: "same date A", business_unit_id: null, dimension_value_ids: [] },
      { date: "2024-03-02", direction: "outflow" as const, amount: "0.10", description: "same date B", business_unit_id: null, dimension_value_ids: [] },
    ],
  };
  const result = calculateForecast(input);
  assert.deepEqual(result.entries.map((entry) => entry.description), ["same date A", "same date B", "later input first"]);
  assert.deepEqual(result.entries.map((entry) => entry.line_number), [2, 3, 1]);
  assert.deepEqual(result.entries.map((entry) => entry.running_balance), ["-0.60", "-0.70", "-2.90"]);
  assert.equal(result.total_inflows, "0.40");
  assert.equal(result.total_outflows, "2.30");
  assert.equal(result.net_change, "-1.90");
  assert.equal(result.closing_balance, "-2.90");
  assert.equal(result.actual_cash, false);
  assert.equal(result.posted, false);
  assert.equal(result.ordering, "date_ascending_then_input_order");
});

test("flow date must follow opening date, descriptions are required, and raw values are never normalized", async () => {
  await fillValid();
  await fill("Flow date 1", "2024-02-28");
  await click("Review forecast");
  assert.match(host.textContent ?? "", /on or after the opening as-of date/i);
  assert.equal(button("Save forecast draft").disabled, true);
  assert.equal(calls.filter((call) => call.method === "POST").length, 0);
  assert.equal((field("Forecast name") as HTMLInputElement).value, "  تدفق نقدي ");
  assert.equal((field("Flow description 1") as HTMLTextAreaElement).value, "  إيراد — text retained  ");
  await fill("Flow date 1", "2024-03-01");
  await fill("Flow description 1", "   ");
  await click("Review forecast");
  assert.match(host.textContent ?? "", /flow 1: enter a description/i);
});

test("required controls, add/remove, review output and safety boundary are rendered", async () => {
  await startDraft();
  for (const label of ["Forecast name", "Opening as-of date", "Opening QAR balance", "Flow date 1", "Flow direction 1", "Flow amount 1", "Flow description 1", "Flow business unit 1", "Flow dimensions 1"]) assert.ok(field(label));
  await click("Review forecast");
  assert.match(host.textContent ?? "", /explicit valid opening as-of date/i);
  assert.equal(button("Save forecast draft").disabled, true);
  await click("Add cash flow");
  assert.ok(field("Flow date 2"));
  await click("Remove cash flow 2");
  assert.equal(host.querySelector('[aria-label="Flow date 2"]'), null);
  assert.match(host.textContent ?? "", /not execution or payment priority/i);
  assert.match(host.textContent ?? "", /no bank connection/i);
});

test("writes an exact whitelisted payload and supports list, reopen, revision history and edit", async () => {
  await fillValid();
  await click("Review forecast");
  assert.equal(button("Save forecast draft").disabled, false);
  await click("Save forecast draft");
  const write = calls.find((call) => call.method === "POST")!;
  assert.equal(write.path, "/lab/forecasts/");
  assert.deepEqual(Object.keys(write.body).sort(), ["as_of_date", "currency", "entries", "name", "opening_balance"].sort());
  assert.deepEqual(Object.keys(write.body.entries[0]).sort(), ["amount", "business_unit_id", "date", "description", "dimension_value_ids", "direction"].sort());
  assert.equal(write.body.name, "  تدفق نقدي ");
  assert.equal(write.body.opening_balance, "-12.30");
  assert.equal(write.body.currency, "QAR");
  assert.equal(write.body.entries[0].description, "  إيراد — text retained  ");
  assert.deepEqual(write.body.entries[0].dimension_value_ids, [31, 32]);
  assert.ok(calls.some((call) => call.path === "/lab/forecasts/" && call.method === "GET"));
  await click("Open forecast draft 73:");
  assert.equal((field("Forecast name") as HTMLInputElement).value, "  تدفق نقدي ");
  assert.ok(host.textContent?.includes("revision 1"));
  assert.match(host.querySelector('[data-testid="forecast-editor"]')?.querySelector("legend")?.textContent ?? "", /revision 1/i);
  await fill("Opening QAR balance", "-10.00");
  await click("Review forecast");
  await click("Save forecast revision");
  const put = calls.filter((call) => call.method === "PUT").at(-1)!;
  assert.equal(put.path, "/lab/forecasts/73/");
  assert.equal(put.body.expected_revision, 1);
  assert.equal(Object.hasOwn(put.body, "review"), false);
  assert.equal(Object.hasOwn(put.body, "history"), false);
});

test("missing existing choices are retained and fresh context blocks save without substitution", async () => {
  await click("Open forecast draft 73:");
  freshContext = { ...context, business_units: [], dimension_values: [] };
  await fill("Forecast name", "Local retained edits");
  await click("Review forecast");
  await click("Save forecast revision");
  assert.equal(calls.filter((call) => call.method === "PUT").length, 0);
  assert.equal((field("Flow business unit 1") as HTMLSelectElement).value, "7");
  assert.match((field("Flow business unit 1") as HTMLSelectElement).selectedOptions[0].textContent ?? "", /Unavailable saved choice \(7\)/);
  assert.deepEqual([...(field("Flow dimensions 1") as HTMLSelectElement).selectedOptions].map((item) => item.value), ["31", "32"]);
  assert.equal((field("Forecast name") as HTMLInputElement).value, "Local retained edits");
  assert.match(host.textContent ?? "", /Original choices remain visible and are not replaced/i);
});

test("409 preserves local work until confirmed reload", async () => {
  await click("Open forecast draft 73:");
  await fill("Flow description 1", "Local work remains");
  await click("Review forecast");
  handler = (call) => call.method === "PUT" ? { status: 409, data: { detail: "Stale revision." } } : null;
  await click("Save forecast revision");
  assert.equal((field("Flow description 1") as HTMLTextAreaElement).value, "Local work remains");
  assert.match(host.textContent ?? "", /local inputs remain unchanged/i);
  assert.equal(button("Save forecast revision").disabled, true);
  window.confirm = () => true;
  await click("Reload saved forecast");
  assert.equal((field("Flow description 1") as HTMLTextAreaElement).value, "  إيراد — text retained  ");
});

test("uncertain create blocks blind retry/new until saved list inspection and explicit reopen", async () => {
  await fillValid();
  await click("Review forecast");
  handler = (call) => call.method === "POST" ? { throw: new TypeError("connection ended") } : null;
  await click("Save forecast draft");
  assert.match(host.textContent ?? "", /database may have committed/i);
  assert.equal(button("Save forecast draft").disabled, true);
  assert.equal(button("New forecast draft").disabled, true);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
  await click("Refresh saved forecasts");
  saved = detail();
  window.confirm = () => true;
  await click("Open forecast draft 73:");
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
  assert.equal((field("Forecast name") as HTMLInputElement).value, payload.name);
});

test("403 clears protected forecast state", async () => {
  await fillValid();
  await click("Review forecast");
  handler = (call) => call.method === "POST" ? { status: 403, data: { detail: "Expired." } } : null;
  await click("Save forecast draft");
  assert.equal(host.querySelector('[data-testid="forecast-editor"]'), null);
  assert.match(host.textContent ?? "", /Protected forecast inputs were cleared/i);
});

test("acknowledged save with list refresh failure is not treated as an uncertain write", async () => {
  await fillValid();
  await click("Review forecast");
  let committed = false;
  handler = (call) => {
    if (call.method === "POST") { committed = true; return null; }
    if (committed && call.path === "/lab/forecasts/" && call.method === "GET") return { status: 503, data: { detail: "List unavailable." } };
    return null;
  };
  await click("Save forecast draft");
  assert.match(host.textContent ?? "", /Forecast saved successfully, but the saved-list refresh failed/i);
  assert.match(host.textContent ?? "", /revision 1/i);
  assert.equal(button("New forecast draft").disabled, false);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
  assert.equal((field("Forecast name") as HTMLInputElement).value, payload.name);
});

test("payload builder rejects unknown context IDs and enforces the 200-row limit", () => {
  const draft = {
    name: "name", as_of_date: "2024-02-29", opening_balance: "-0.00",
    entries: [{
      date: "2024-03-01", direction: "outflow" as const, amount: "0", description: "description",
      business_unit_id: "", dimension_value_ids: ["31", "32"],
    }],
  };
  const built = payloadFromForecast(draft, context);
  assert.deepEqual(Object.keys(built).sort(), ["as_of_date", "currency", "entries", "name", "opening_balance"].sort());
  assert.equal(built.opening_balance, "-0.00");
  assert.equal(built.entries[0].business_unit_id, null);
  assert.deepEqual(built.entries[0].dimension_value_ids, [31, 32]);
  assert.throws(() => payloadFromForecast({ ...draft, entries: [{ ...draft.entries[0], dimension_value_ids: ["999"] }] }, context), /unavailable/i);
  assert.ok(validateForecast({ ...draft, entries: Array.from({ length: 201 }, () => draft.entries[0]) }, context).some((issue) => /no more than 200/i.test(issue)));
});
