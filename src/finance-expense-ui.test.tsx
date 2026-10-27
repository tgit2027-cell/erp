// @ts-nocheck
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceExpenseLab } from "./FinanceExpenseLab";
import { expenseTotal, validateExpense, blankExpenseDraft } from "./finance-expense";

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
  accounts: [{ id: 11, code: "AC-11", name_en: "Services", name_ar: "خدمات" }],
  dimensions: [
    { id: 3, code: "REGION", label_en: "Region", label_ar: "منطقة" },
    { id: 4, code: "PROJECT", label_en: "Project", label_ar: "مشروع" },
  ],
  dimension_values: [
    { id: 31, dimension_id: 3, code: "NORTH", label_en: "North", label_ar: "الشمال" },
    { id: 32, dimension_id: 3, code: "SOUTH", label_en: "South", label_ar: "الجنوب" },
    { id: 41, dimension_id: 4, code: "P-1", label_en: "Project one", label_ar: "مشروع ١" },
  ],
};
const payload = {
  name: "  Travel note  ", proposal_type: "expense" as const, currency: "QAR" as const,
  explanation: "  مراجعة عربية  ",
  entries: [{
    expense_date: "2026-02-14", party_reference: "  Synthetic claimant  ", description: "  Taxi  ",
    amount: "0.10", account_id: 11, business_unit_id: null, dimension_value_ids: [31, 41],
    receipt_reference: "  REF-ONLY  ",
  }],
};
function makeSaved(revision = 1, body = payload) {
  const snapshot = {
    ...body, id: 73, revision,
    review: { currency: "QAR", proposed_total: "0.10", entry_count: body.entries.length,
      missing_receipt_rows: [], unproposed_business_unit_rows: [], approved: false, paid: false, posted: false },
  };
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
    if (call.path === "/lab/expenses/" && call.method === "GET") return response({ proposals: [{ id: saved.id, name: saved.name, proposal_type: saved.proposal_type, revision: saved.revision }] });
    if (call.path === "/lab/expenses/73/" && call.method === "GET") return response(saved);
    if (call.method === "POST" || call.method === "PUT") {
      saved = makeSaved(call.method === "POST" ? 1 : saved.revision + 1, call.body);
      return response(saved);
    }
    return response({ detail: "Unexpected test endpoint." }, 404);
  };
  await act(async () => { root.render(<FinanceExpenseLab />); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); globalThis.fetch = nativeFetch; window.confirm = nativeConfirm;
});
function button(prefix: string) {
  const found = [...host.querySelectorAll("button")].find((item) => item.textContent?.startsWith(prefix) || item.getAttribute("aria-label")?.startsWith(prefix));
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
async function fillValid(type = "expense") {
  await click("New expense proposal");
  await fill("Expense proposal name", "  Travel note  ");
  await fill("Proposal type", type);
  await fill("Proposal explanation", "  مراجعة عربية  ");
  await fill("Expense date 1", "2026-02-14");
  await fill("Claimant/custodian reference 1", "  Synthetic claimant  ");
  await fill("Expense description 1", "  Taxi  ");
  await fill("Expense amount 1", "0.10");
  await fill("Expense account 1", "11");
  await fill("Synthetic receipt reference 1", "  REF-ONLY  ");
}

test("exact bigint arithmetic adds 0.10 + 0.20; both explicit proposal types are valid", async () => {
  assert.equal(expenseTotal([{ amount: "0.10" }, { amount: "0.20" }] as any), "0.30");
  await fillValid("expense");
  await click("Add proposal row");
  assert.equal(button("Remove proposal row 2").getAttribute("aria-label"), "Remove proposal row 2");
  await fill("Expense date 2", "2026-02-15");
  await fill("Claimant/custodian reference 2", "  Cash custodian  ");
  await fill("Expense description 2", "Small cash purchase");
  await fill("Expense amount 2", "0.20");
  await fill("Expense account 2", "11");
  assert.match(host.querySelector('[data-testid="expense-proposed-total"]')?.textContent ?? "", /0\.30 QAR/);
  await click("Review proposal");
  assert.equal(button("Save expense proposal").disabled, false);
  await click("Save expense proposal");
  assert.equal(calls.find((call) => call.method === "POST")?.body.proposal_type, "expense");
  assert.match(host.textContent ?? "", /not approved, not paid, and not posted/i);

  const retainedName = (field("Expense proposal name") as HTMLInputElement).value;
  const retainedClaimant = (field("Claimant/custodian reference 1") as HTMLInputElement).value;
  await fill("Proposal type", "petty_cash");
  assert.equal((field("Expense proposal name") as HTMLInputElement).value, retainedName);
  assert.equal((field("Claimant/custodian reference 1") as HTMLInputElement).value, retainedClaimant);
  assert.equal(button("Save proposal revision").disabled, true);
  assert.match(host.textContent ?? "", /review claimant\/custodian references/i);
  await click("Review proposal");
  await click("Save proposal revision");
  assert.equal(calls.filter((call) => call.method === "PUT").at(-1)?.body.proposal_type, "petty_cash");
});

test("blank type, date, required account, malformed amount and duplicate IDs block; same-entity values are not over-restricted", async () => {
  const draft = blankExpenseDraft();
  draft.name = "x"; draft.entries[0] = {
    expenseDate: "2026-02-30", partyReference: "claimant", description: "description", amount: "1.001",
    accountId: "", businessUnitId: "", dimensionIds: ["31", "31"], receiptReference: "",
  };
  const errors = validateExpense(draft, context);
  assert.ok(errors.some((item) => /Choose a proposal type/.test(item)));
  assert.ok(errors.some((item) => /valid expense date/.test(item)));
  assert.ok(errors.some((item) => /available saved account/.test(item)));
  assert.ok(errors.some((item) => /at most two decimal places/.test(item)));
  assert.ok(errors.some((item) => /dimensions must be distinct/.test(item)));
  const multiValueDraft = {
    ...draft, proposalType: "expense" as const,
    entries: [{ ...draft.entries[0], expenseDate: "2026-02-14", amount: "0.00", accountId: "11", dimensionIds: ["31", "32"] }],
  };
  assert.equal(validateExpense(multiValueDraft, context).length, 0);
  await fillValid();
  await fill("Expense date 1", "");
  await fill("Expense amount 1", "1.001");
  await fill("Expense account 1", "");
  await click("Review proposal");
  assert.equal(button("Save expense proposal").disabled, true);
  assert.match(host.textContent ?? "", /explicit valid expense date/);
  assert.match(host.textContent ?? "", /choose an available saved account/);
});

test("save sends precise whitelisted payload, preserves entered whitespace, then list/reopen/history/edit revision", async () => {
  await fillValid();
  assert.equal(host.querySelector('input[type="file"]'), null, "receipt references are text only");
  await fill("Expense dimensions 1", "31");
  await click("Review proposal");
  await click("Save expense proposal");
  const write = calls.find((call) => call.method === "POST")!;
  assert.equal(write.path, "/lab/expenses/");
  assert.deepEqual(Object.keys(write.body).sort(), ["currency", "entries", "explanation", "name", "proposal_type"].sort());
  assert.deepEqual(Object.keys(write.body.entries[0]).sort(), [
    "expense_date", "party_reference", "description", "amount", "account_id", "business_unit_id", "dimension_value_ids", "receipt_reference",
  ].sort());
  assert.equal(write.body.name, "  Travel note  ");
  assert.equal(write.body.explanation, "  مراجعة عربية  ");
  assert.equal(write.body.entries[0].party_reference, "  Synthetic claimant  ");
  assert.equal(write.body.entries[0].description, "  Taxi  ");
  assert.equal(write.body.entries[0].receipt_reference, "  REF-ONLY  ");
  assert.deepEqual(write.body.entries[0].dimension_value_ids, [31]);
  assert.ok(calls.some((call) => call.path === "/lab/expenses/" && call.method === "GET"));

  await click("Open expense proposal 73:");
  assert.equal((field("Claimant/custodian reference 1") as HTMLInputElement).value, "  Synthetic claimant  ");
  assert.ok(host.querySelector('[data-testid="expense-revision-history"]'));
  await fill("Expense amount 1", "0.25");
  await click("Review proposal");
  await click("Save proposal revision");
  const put = calls.filter((call) => call.method === "PUT").at(-1)!;
  assert.equal(put.path, "/lab/expenses/73/");
  assert.equal(put.body.expected_revision, 1);
  assert.equal(Object.keys(put.body).includes("review"), false);
});

test("missing receipt reference is informational and does not block an explicit proposal", async () => {
  await fillValid();
  await fill("Synthetic receipt reference 1", "");
  await click("Review proposal");
  assert.equal(button("Save expense proposal").disabled, false);
  assert.match(host.textContent ?? "", /No synthetic receipt reference in the current draft · information only/);
  await click("Save expense proposal");
  assert.equal(calls.find((call) => call.method === "POST")?.body.entries[0].receipt_reference, "");
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
});

test("reopened edits derive missing receipt and business-unit notes from current draft, separate from saved review", async () => {
  const populatedBody = {
    ...payload,
    entries: [{ ...payload.entries[0], business_unit_id: 7, receipt_reference: "  SAVED-REF  " }],
  };
  saved = makeSaved(1, populatedBody);
  await click("Open expense proposal 73:");
  assert.match(host.textContent ?? "", /Saved review at revision 1: 1 entries · 0 missing receipt references · 0 rows without proposed business unit/);
  await fill("Synthetic receipt reference 1", "   ");
  await fill("Expense business unit 1", "");
  await click("Review proposal");
  assert.match(host.textContent ?? "", /No synthetic receipt reference in the current draft · information only/);
  assert.match(host.textContent ?? "", /Business unit not proposed in the current draft · information only/);
  assert.match(host.textContent ?? "", /Saved review at revision 1: 1 entries · 0 missing receipt references · 0 rows without proposed business unit/);
  assert.equal(button("Save proposal revision").disabled, false);
});

test("reopened snapshot with vanished choices stays visible and requires explicit correction", async () => {
  handler = (call) => call.path === "/lab/context/"
    ? { data: { ...context, accounts: [], dimension_values: [] } }
    : null;
  await click("Open expense proposal 73:");
  assert.equal((field("Expense account 1") as HTMLSelectElement).value, "11");
  assert.match((field("Expense account 1") as HTMLSelectElement).selectedOptions[0].textContent ?? "", /Unavailable saved choice \(11\)/);
  assert.deepEqual([...(field("Expense dimensions 1") as HTMLSelectElement).selectedOptions].map((item) => item.value), ["31", "41"]);
  assert.equal((field("Expense description 1") as HTMLTextAreaElement).value, "  Taxi  ");
  assert.equal(button("Save proposal revision").disabled, true);
  assert.ok(host.querySelector('[data-testid="expense-revision-history"]'));
  assert.equal(calls.filter((call) => call.method === "PUT" || call.method === "POST").length, 0);
});

test("409 retains work; transport failure blocks blind retry until explicit reopen", async () => {
  await click("Open expense proposal 73:");
  await fill("Expense description 1", "Keep my local work");
  await click("Review proposal");
  handler = (call) => call.method === "PUT" ? { status: 409, data: { detail: "Stale revision." } } : null;
  await click("Save proposal revision");
  assert.equal((field("Expense description 1") as HTMLTextAreaElement).value, "Keep my local work");
  assert.match(host.textContent ?? "", /local inputs remain unchanged/i);
  assert.equal(button("Save proposal revision").disabled, true);
  handler = (call) => call.method === "PUT" ? (() => { throw new TypeError("connection ended"); })() : null;
  window.confirm = () => true;
  await click("Reload saved proposal");
  await fill("Expense description 1", "Unknown write result");
  await click("Review proposal");
  await click("Save proposal revision");
  assert.match(host.textContent ?? "", /database may have committed/i);
  assert.equal(button("New expense proposal").disabled, true);
  assert.equal(calls.filter((call) => call.method === "PUT").length, 2);
  handler = null;
  await click("Refresh saved list");
  await click("Open expense proposal 73:");
  assert.equal(calls.filter((call) => call.method === "PUT").length, 2);
});

test("403 clears protected values; stale context blocks save without substituting the vanished account", async () => {
  await fillValid();
  await click("Review proposal");
  handler = (call) => call.path === "/lab/context/" ? { status: 403, data: { detail: "Denied." } } : null;
  await click("Save expense proposal");
  assert.equal(host.querySelector('[data-testid="expense-editor"]'), null);
  assert.match(host.textContent ?? "", /Protected expense inputs were cleared/);
  assert.equal(calls.filter((call) => call.method === "POST").length, 0);
  assert.equal(calls.filter((call) => call.path === "/lab/session/" && call.method !== "GET").length, 0);

  handler = null;
  await act(async () => { root.unmount(); });
  root = createRoot(host);
  await act(async () => { root.render(<FinanceExpenseLab />); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  await fillValid();
  await fill("Expense dimensions 1", "31");
  await click("Review proposal");
  handler = (call) => call.path === "/lab/context/" ? { data: { ...context, accounts: [], dimension_values: [] } } : null;
  await click("Save expense proposal");
  assert.equal(calls.filter((call) => call.method === "POST").length, 0);
  assert.equal((field("Expense account 1") as HTMLSelectElement).value, "11");
  assert.deepEqual([...(field("Expense dimensions 1") as HTMLSelectElement).selectedOptions].map((item) => item.value), ["31"]);
  assert.match(host.textContent ?? "", /Original choices remain visible/i);
  assert.match(host.textContent ?? "", /dimensions are unavailable/i);
});

test("400 and 5xx recovery semantics retain draft or block retry; post-save list denial clears protected state", async () => {
  await fillValid();
  await click("Review proposal");
  handler = (call) => call.method === "POST" ? { status: 400, data: { detail: "Bad request." } } : null;
  await click("Save expense proposal");
  assert.equal((field("Expense proposal name") as HTMLInputElement).value, "  Travel note  ");
  assert.equal(button("New expense proposal").disabled, false);

  handler = (call) => call.method === "POST" ? { status: 503, data: { detail: "Gateway unavailable." } } : null;
  await click("Save expense proposal");
  assert.match(host.textContent ?? "", /database may have committed/i);
  assert.equal(button("Save expense proposal").disabled, true);

  handler = null;
  await act(async () => { root.unmount(); });
  root = createRoot(host);
  let committed = false;
  handler = (call) => {
    if (call.method === "POST") { committed = true; return null; }
    if (committed && call.path === "/lab/expenses/" && call.method === "GET") return { status: 403, data: { detail: "Expired." } };
    return null;
  };
  await act(async () => { root.render(<FinanceExpenseLab />); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  await fillValid();
  await click("Review proposal");
  await click("Save expense proposal");
  assert.equal(host.querySelector('[data-testid="expense-editor"]'), null);
  assert.match(host.textContent ?? "", /Protected expense inputs were cleared/);
});

test("non-403 saved-list refresh failure reports the committed save without treating it as an uncertain write", async () => {
  await fillValid();
  await click("Review proposal");
  let committed = false;
  handler = (call) => {
    if (call.method === "POST") { committed = true; return null; }
    if (committed && call.path === "/lab/expenses/" && call.method === "GET") return { status: 503, data: { detail: "List unavailable." } };
    return null;
  };
  await click("Save expense proposal");
  assert.match(host.textContent ?? "", /Proposal saved successfully, but the saved-list refresh failed/i);
  assert.match(host.textContent ?? "", /revision 1/i);
  assert.equal(button("New expense proposal").disabled, false);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
});
