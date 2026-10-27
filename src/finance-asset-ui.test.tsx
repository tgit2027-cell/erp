// @ts-nocheck
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceAssetLab } from "./FinanceAssetLab";
import { blankAssetDraft, payloadFromAsset, validAssetCost, validAssetDate, validateAsset } from "./finance-asset";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let calls: { path: string; method: string; body?: any }[];
let handler: ((call: { path: string; method: string; body?: any }) => Promise<any> | any) | null;
let saved: any;
const nativeFetch = globalThis.fetch;
const nativeConfirm = window.confirm;
const context = {
  legal_entity_label: "Synthetic review entity",
  business_units: [{ id: 7, code: "BU-7", name: "Review unit" }],
  accounts: [{ id: 11, code: "AC-11", name_en: "Equipment", name_ar: "معدات" }],
  dimensions: [{ id: 3, code: "REGION", label_en: "Region", label_ar: "منطقة" }],
  dimension_values: [
    { id: 31, dimension_id: 3, code: "NORTH", label_en: "North", label_ar: "الشمال" },
    { id: 32, dimension_id: 3, code: "SOUTH", label_en: "South", label_ar: "الجنوب" },
  ],
};
const payload = {
  asset_identifier: "  REF-01  ",
  name: "  أجهزة مختبر  ",
  description: "  أصل للاختبار — وصف عربي  ",
  acquisition_date: "2026-02-14",
  currency: "QAR" as const,
  cost: "0.10",
  account_id: 11,
  business_unit_id: null,
  dimension_value_ids: [31, 32],
  reference: "  PO-REF only  ",
  location_details: "  الطابق الثاني — ملاحظات نصية فقط  ",
};
function makeSaved(revision = 1, body = payload) {
  const snapshot = {
    ...body, id: 73, revision,
    review: { currency: "QAR", proposed_cost: body.cost, capitalized: false, posted: false, approved: false },
  };
  return { ...snapshot, history: [{ revision, created_at: "synthetic timestamp", snapshot }] };
}
function response(data: any, status = 200) { return new Response(JSON.stringify(data), { status }); }
beforeEach(async () => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  calls = [];
  handler = null;
  saved = makeSaved();
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
    if (call.path === "/lab/context/") return response(context);
    if (call.path === "/lab/assets/" && call.method === "GET") {
      return response({ assets: saved ? [{ id: saved.id, asset_identifier: saved.asset_identifier, name: saved.name, revision: saved.revision }] : [] });
    }
    if (call.path === "/lab/assets/73/" && call.method === "GET") return response(saved);
    if (call.method === "POST" || call.method === "PUT") {
      saved = makeSaved(call.method === "POST" ? 1 : saved.revision + 1, call.body);
      return response(saved);
    }
    return response({ detail: "Unexpected test endpoint." }, 404);
  };
  await act(async () => { root.render(<FinanceAssetLab />); });
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
async function fillValid() {
  await click("New asset draft");
  await fill("Asset identifier", "  REF-01  ");
  await fill("Asset name", "  أجهزة مختبر  ");
  await fill("Asset description", "  أصل للاختبار — وصف عربي  ");
  await fill("Acquisition date", "2026-02-14");
  await fill("Acquisition cost", "0.10");
  await fill("Asset account", "11");
  await fill("Asset dimensions", "31");
  const select = field("Asset dimensions") as HTMLSelectElement;
  await act(async () => {
    for (const option of [...select.options]) option.selected = option.value === "31" || option.value === "32";
    select.dispatchEvent(new select.ownerDocument.defaultView!.Event("change", { bubbles: true }));
  });
  await fill("Asset reference", "  PO-REF only  ");
  await fill("Asset location details", "  الطابق الثاني — ملاحظات نصية فقط  ");
}

test("strict exact cost and calendar date validation; required explicit account, with no defaults", () => {
  assert.equal(validAssetCost("0"), true);
  assert.equal(validAssetCost("0.00"), true);
  assert.equal(validAssetCost("999999999999.99"), true);
  assert.equal(validAssetCost("01"), false);
  assert.equal(validAssetCost("-0.01"), false);
  assert.equal(validAssetCost("1.001"), false);
  assert.equal(validAssetCost("1000000000000"), false);
  assert.equal(validAssetDate("2024-02-29"), true);
  assert.equal(validAssetDate("2025-02-29"), false);
  const draft = blankAssetDraft();
  assert.equal(draft.name, "");
  assert.equal(draft.acquisition_date, "");
  assert.equal(draft.cost, "");
  assert.equal(draft.account_id, "");
  Object.assign(draft, {
    asset_identifier: "asset", name: "name", description: "description", acquisition_date: "2026-02-30", cost: "1.001", account_id: "",
  });
  const errors = validateAsset(draft, context);
  assert.ok(errors.some((item) => /valid acquisition date/.test(item)));
  assert.ok(errors.some((item) => /at most two decimal places/.test(item)));
  assert.ok(errors.some((item) => /available saved account/.test(item)));
  assert.ok(validateAsset({ ...draft, acquisition_date: "2026-02-14", cost: "0", account_id: "11" }, context).length === 0);
});

test("description is required for review and save; rejected whitespace keeps the rest of the draft", async () => {
  await fillValid();
  await fill("Asset description", "   ");
  await click("Review asset draft");
  assert.match(host.textContent ?? "", /enter an asset description/i);
  assert.equal(button("Save asset draft").disabled, true);
  assert.equal(calls.filter((call) => call.method === "POST" || call.method === "PUT").length, 0);
  assert.equal((field("Asset identifier") as HTMLInputElement).value, "  REF-01  ");
  assert.equal((field("Asset name") as HTMLInputElement).value, "  أجهزة مختبر  ");
  assert.equal((field("Asset description") as HTMLTextAreaElement).value, "   ");
  assert.equal((field("Acquisition cost") as HTMLInputElement).value, "0.10");
  assert.equal((field("Asset account") as HTMLSelectElement).value, "11");
});

test("required rendered control names, explicit review validation, and forbidden finance fields are absent", async () => {
  await click("New asset draft");
  for (const label of [
    "Asset identifier", "Asset name", "Asset description", "Acquisition date", "Acquisition cost",
    "Asset account", "Asset business unit", "Asset dimensions", "Asset reference", "Asset location details",
  ]) assert.ok(field(label));
  await click("Review asset draft");
  assert.equal(button("Save asset draft").disabled, true);
  assert.match(host.textContent ?? "", /explicit valid acquisition date/i);
  assert.match(host.textContent ?? "", /choose an available saved account/i);
  assert.equal(host.querySelector('input[type="file"]'), null);
  for (const forbidden of ["Useful life", "Residual value", "Depreciation", "Custody", "Disposal", "Capitalization threshold"]) {
    assert.equal(host.querySelector(`[aria-label*="${forbidden}" i]`), null, `${forbidden} must not be an input field`);
  }
  assert.match(host.textContent ?? "", /location details are optional text only/i);
});

test("single record uses an exact whitelisted payload, retains raw Arabic and whitespace, then list/reopen/history/edit", async () => {
  await fillValid();
  assert.equal((field("Asset business unit") as HTMLSelectElement).value, "");
  await click("Review asset draft");
  assert.equal(button("Save asset draft").disabled, false);
  await click("Save asset draft");
  const write = calls.find((call) => call.method === "POST")!;
  assert.equal(write.path, "/lab/assets/");
  assert.deepEqual(Object.keys(write.body).sort(), [
    "account_id", "acquisition_date", "asset_identifier", "business_unit_id", "cost",
    "currency", "description", "dimension_value_ids", "location_details", "name", "reference",
  ].sort());
  assert.equal(write.body.asset_identifier, "  REF-01  ");
  assert.equal(write.body.name, "  أجهزة مختبر  ");
  assert.equal(write.body.description, "  أصل للاختبار — وصف عربي  ");
  assert.equal(write.body.reference, "  PO-REF only  ");
  assert.equal(write.body.location_details, "  الطابق الثاني — ملاحظات نصية فقط  ");
  assert.equal(write.body.cost, "0.10");
  assert.equal(write.body.currency, "QAR");
  assert.equal(write.body.business_unit_id, null);
  assert.deepEqual(write.body.dimension_value_ids, [31, 32]);
  assert.ok(calls.some((call) => call.path === "/lab/assets/" && call.method === "GET"));
  await click("Open asset draft 73:");
  assert.equal((field("Asset name") as HTMLInputElement).value, "  أجهزة مختبر  ");
  assert.equal((field("Asset reference") as HTMLInputElement).value, "  PO-REF only  ");
  assert.ok(host.querySelector('[data-testid="asset-revision-history"]'));
  assert.match(host.querySelector('[data-testid="asset-editor"]')?.querySelector("legend")?.textContent ?? "", /revision 1/i);
  await fill("Acquisition cost", "0.25");
  await click("Review asset draft");
  await click("Save asset revision");
  const put = calls.filter((call) => call.method === "PUT").at(-1)!;
  assert.equal(put.path, "/lab/assets/73/");
  assert.equal(put.body.expected_revision, 1);
  assert.equal(Object.hasOwn(put.body, "review"), false);
  assert.equal(Object.hasOwn(put.body, "history"), false);
});

test("unavailable saved account, business unit, and dimensions remain visible and block writes without substitution", async () => {
  const savedBody = { ...payload, business_unit_id: 7 };
  saved = makeSaved(1, savedBody);
  handler = (call) => call.path === "/lab/context/" ? {
    data: { ...context, accounts: [], business_units: [], dimension_values: [] },
  } : null;
  await click("Open asset draft 73:");
  assert.equal((field("Asset account") as HTMLSelectElement).value, "11");
  assert.match((field("Asset account") as HTMLSelectElement).selectedOptions[0].textContent ?? "", /Unavailable saved choice \(11\)/);
  assert.equal((field("Asset business unit") as HTMLSelectElement).value, "7");
  assert.match((field("Asset business unit") as HTMLSelectElement).selectedOptions[0].textContent ?? "", /Unavailable saved choice \(7\)/);
  assert.deepEqual([...(field("Asset dimensions") as HTMLSelectElement).selectedOptions].map((item) => item.value), ["31", "32"]);
  assert.equal(button("Save asset revision").disabled, true);
  assert.equal(calls.filter((call) => call.method === "PUT" || call.method === "POST").length, 0);
  assert.ok(host.querySelector('[data-testid="asset-revision-history"]'));
});

test("fresh context is required before write and vanished choice retains local value for correction", async () => {
  await fillValid();
  await click("Review asset draft");
  handler = (call) => call.path === "/lab/context/" ? {
    data: { ...context, accounts: [] },
  } : null;
  await click("Save asset draft");
  assert.equal(calls.filter((call) => call.method === "POST").length, 0);
  assert.equal((field("Asset account") as HTMLSelectElement).value, "11");
  assert.match(host.textContent ?? "", /Original choices remain visible/i);
  assert.match(host.textContent ?? "", /available saved account/i);
});

test("409 preserves local draft until confirmed reload; unknown write requires list inspection and explicit reopen", async () => {
  await click("Open asset draft 73:");
  await fill("Asset description", "Local work remains");
  await click("Review asset draft");
  handler = (call) => call.method === "PUT" ? { status: 409, data: { detail: "Stale revision." } } : null;
  await click("Save asset revision");
  assert.equal((field("Asset description") as HTMLTextAreaElement).value, "Local work remains");
  assert.match(host.textContent ?? "", /local inputs remain unchanged/i);
  assert.equal(button("Save asset revision").disabled, true);
  handler = (call) => call.method === "PUT" ? { throw: new TypeError("connection ended") } : null;
  window.confirm = () => true;
  await click("Reload saved asset draft");
  await fill("Asset description", "Unknown result");
  await click("Review asset draft");
  await click("Save asset revision");
  assert.match(host.textContent ?? "", /database may have committed/i);
  assert.equal(button("Save asset revision").disabled, true);
  assert.equal(button("New asset draft").disabled, true);
  assert.equal(calls.filter((call) => call.method === "PUT").length, 2);
  await click("Refresh saved assets");
  await click("Open asset draft 73:");
  assert.equal(calls.filter((call) => call.method === "PUT").length, 2);
});

test("400 retains editable draft; 403 after save clears all protected state", async () => {
  await fillValid();
  await click("Review asset draft");
  handler = (call) => call.method === "POST" ? { status: 400, data: { detail: "Bad request." } } : null;
  await click("Save asset draft");
  assert.equal((field("Asset name") as HTMLInputElement).value, "  أجهزة مختبر  ");
  assert.equal(button("New asset draft").disabled, false);
  handler = (call) => call.method === "POST" ? null
    : call.path === "/lab/assets/" && call.method === "GET" ? { status: 403, data: { detail: "Expired." } } : null;
  await click("Save asset draft");
  assert.equal(host.querySelector('[data-testid="asset-editor"]'), null);
  assert.match(host.textContent ?? "", /Protected asset inputs were cleared/i);
  assert.equal(calls.filter((call) => call.method === "POST").length, 2);
  assert.equal(calls.filter((call) => call.path === "/lab/session/" && call.method !== "GET").length, 0);
});

test("non-403 list failure after acknowledged save reports saved, not an uncertain write", async () => {
  await fillValid();
  await click("Review asset draft");
  let committed = false;
  handler = (call) => {
    if (call.method === "POST") { committed = true; return null; }
    if (committed && call.path === "/lab/assets/" && call.method === "GET") {
      return { status: 503, data: { detail: "List unavailable." } };
    }
    return null;
  };
  await click("Save asset draft");
  assert.match(host.textContent ?? "", /Asset saved successfully, but the saved-list refresh failed/i);
  assert.match(host.textContent ?? "", /revision 1/i);
  assert.equal(button("New asset draft").disabled, false);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
  assert.equal((field("Asset name") as HTMLInputElement).value, "  أجهزة مختبر  ");
});

test("payload constructor uses allowed fields only and refuses IDs not in explicit context", () => {
  const draft = {
    asset_identifier: "id", name: "name", description: "description", acquisition_date: "2026-02-14",
    cost: "0", account_id: "11", business_unit_id: "", dimension_value_ids: ["31", "32"],
    reference: "", location_details: "",
  };
  const built = payloadFromAsset(draft, context);
  assert.deepEqual(built.dimension_value_ids, [31, 32]);
  assert.equal(built.business_unit_id, null);
  assert.deepEqual(Object.keys(built).sort(), [
    "account_id", "acquisition_date", "asset_identifier", "business_unit_id", "cost",
    "currency", "description", "dimension_value_ids", "location_details", "name", "reference",
  ].sort());
  assert.throws(() => payloadFromAsset({ ...draft, account_id: "999" }, context), /available saved account/);
});
