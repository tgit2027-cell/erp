// @ts-nocheck
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceReconciliationLab } from "./FinanceReconciliationLab";
import { blankReconciliation, reconciliationMath, signedCents, validateReconciliation } from "./finance-reconciliation";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let calls: { path: string; method: string; body?: any }[];
let handler: ((call: { path: string; method: string; body?: any }) => Promise<any> | any) | null;
let stored: any;
const nativeFetch = globalThis.fetch;
const nativeConfirm = window.confirm;
const row = (row_id: string, amount: string, direction = "increase", date = "2026-02-03") => ({
  row_id, date, amount, direction, reference: `source-${row_id}`, explanation: "",
});
const validPayload = {
  name: "February comparison", currency: "QAR" as const, statement_date: "2026-02-28",
  statement_balance: "100.30", book_date: "2026-02-28", book_balance: "100.00", explanation: "Explicit source comparison",
  statement_rows: [row("STMT_1", "0.30")], book_rows: [row("BOOK-1", "0.30")],
  matches: [{ statement_row_id: "STMT_1", book_row_id: "BOOK-1", explanation: "Reviewed source documents" }],
};
function makeSaved(revision = 1, payload = validPayload) {
  const review = {
    currency: "QAR", balance_difference: "0.30", statement_net: "0.30", book_net: "0.30",
    unmatched_statement_ids: [], unmatched_book_ids: [], unmatched_statement_net: "0.00",
    unmatched_book_net: "0.00", unmatched_row_difference: "0.00", comparison_residual: "0.30",
    matched_count: payload.matches.length, warnings: [], posted: false, operational_reconciled: false,
  };
  const snapshot = { ...payload, id: 73, revision, review };
  return { ...snapshot, history: [{ revision, created_at: "2026-03-01T10:00:00Z", snapshot }] };
}
function response(data: any, status = 200) { return new Response(JSON.stringify(data), { status }); }
beforeEach(async () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  calls = []; handler = null; stored = makeSaved();
  globalThis.fetch = async (input: RequestInfo | URL, options: RequestInit = {}) => {
    const call = { path: String(input), method: String(options.method ?? "GET"), body: options.body ? JSON.parse(String(options.body)) : undefined };
    calls.push(call);
    const custom = await handler?.(call);
    if (custom) return response(custom.data, custom.status ?? 200);
    if (call.path === "/lab/session/") return response({ authenticated: true, csrf_token: "TEST" });
    if (call.path === "/lab/reconciliations/" && call.method === "GET") {
      return response({ proposals: [{ id: stored.id, name: stored.name, revision: stored.revision }] });
    }
    if (call.path === "/lab/reconciliations/73/" && call.method === "GET") return response(stored);
    if (call.method === "POST" || call.method === "PUT") {
      stored = makeSaved(call.method === "POST" ? 1 : stored.revision + 1, call.body);
      return response(stored);
    }
    return response({ detail: "Unexpected test endpoint." }, 404);
  };
  await act(async () => { root.render(<FinanceReconciliationLab />); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); globalThis.fetch = nativeFetch; window.confirm = nativeConfirm;
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
async function fillValid() {
  await click("New reconciliation proposal");
  await click("Add statement row");
  await click("Add book row");
  await fill("Reconciliation proposal name", "February comparison");
  await fill("Statement balance date", "2026-02-28");
  await fill("Statement balance", "100.30");
  await fill("Book balance date", "2026-02-28");
  await fill("Book balance", "100.00");
  await fill("Statement row ID 1", "STMT_1");
  await fill("Statement row date 1", "2026-02-03");
  await fill("Statement row amount 1", "0.30");
  await fill("Statement row direction 1", "increase");
  await fill("Book row ID 1", "BOOK-1");
  await fill("Book row date 1", "2026-02-04");
  await fill("Book row amount 1", "0.30");
  await fill("Book row direction 1", "increase");
  await fill("Match statement row", "STMT_1");
  await fill("Match book row", "BOOK-1");
  await fill("Match explanation", "Reviewed source documents");
  await click("Add explicit match");
}

test("reopened proposal submits only editable inputs, not API metadata or revision history", async () => {
  await click("Open proposal 73:");
  await fill("Reconciliation proposal name", "Explicit reopened edit");
  await click("Review proposal");
  await click("Save proposal revision");
  const request = calls.find((call) => call.method === "PUT");
  assert.ok(request);
  assert.deepEqual(Object.keys(request.body).sort(), [
    "name", "currency", "statement_date", "statement_balance", "book_date", "book_balance",
    "explanation", "statement_rows", "book_rows", "matches", "expected_revision",
  ].sort());
  assert.equal(request.body.expected_revision, 1);
  assert.deepEqual(request.body.statement_rows, validPayload.statement_rows);
});

test("exact bigint formulas handle negative snapshot balances and decimal addition", () => {
  const draft = blankReconciliation();
  Object.assign(draft, {
    statement_balance: "-1.00", book_balance: "-1.30",
    statement_rows: [row("a", "0.10"), row("b", "0.20")],
    book_rows: [row("c", "0.30")],
  });
  const totals = reconciliationMath(draft)!;
  assert.equal(totals.statementNet, "0.30");
  assert.equal(totals.bookNet, "0.30");
  assert.equal(totals.balanceDifference, "0.30");
  assert.equal(totals.comparisonResidual, "0.30");
  assert.equal(signedCents("-999999999999.99"), -99999999999999n);
  assert.equal(signedCents("-1000000000000.00"), null);
  assert.equal(signedCents("1.001"), null);
  const blankDirection = { ...draft, statement_rows: [{ ...row("unknown", "0.10"), direction: "" }] };
  assert.equal(reconciliationMath(blankDirection), null);
  assert.ok(validateReconciliation({ ...blankReconciliation(), name: "Direction required", statement_date: "2026-01-01",
    statement_balance: "0", book_date: "2026-01-01", book_balance: "0",
    statement_rows: [{ ...row("valid", "0.10"), direction: "" }] }).some((issue) => /choose increase or decrease/.test(issue)));
});

test("manual pair validation reports duplicate, incompatible and dangling pairs without dropping them", () => {
  const draft = {
    ...blankReconciliation(), ...validPayload,
    statement_rows: validPayload.statement_rows.map((item) => ({ ...item })),
    book_rows: validPayload.book_rows.map((item) => ({ ...item })),
    matches: validPayload.matches.map((item) => ({ ...item })),
  };
  draft.matches = [
    ...validPayload.matches,
    { statement_row_id: "STMT_1", book_row_id: "missing", explanation: "" },
  ];
  let errors = validateReconciliation(draft);
  assert.ok(errors.some((issue) => /matched more than once/.test(issue)));
  assert.ok(errors.some((issue) => /missing or dangling/.test(issue)));
  draft.matches = [{ statement_row_id: "STMT_1", book_row_id: "BOOK-1", explanation: "" }];
  draft.book_rows[0] = row("BOOK-1", "0.31");
  errors = validateReconciliation(draft);
  assert.ok(errors.some((issue) => /same exact magnitude and direction/.test(issue)));
  draft.book_rows[0] = row("BOOK-1", "0.30");
  draft.statement_rows.push(row("STMT_1", "0.30"));
  errors = validateReconciliation(draft);
  assert.ok(errors.some((issue) => /row ID STMT_1 is duplicated/.test(issue)));
  draft.statement_rows[1] = row("_invalid", "0.30");
  errors = validateReconciliation(draft);
  assert.ok(errors.some((issue) => /first character must be an ASCII letter or digit/.test(issue)));
  draft.book_rows[0] = row("BOOK-1", "0.30", "decrease");
  errors = validateReconciliation(draft);
  assert.ok(errors.some((issue) => /same exact magnitude and direction/.test(issue)));
});

test("dates reject year zero, leap-day errors, while source dates need not align to snapshot dates", async () => {
  await fillValid();
  await fill("Statement row date 1", "2026-02-29");
  await click("Review proposal");
  assert.match(host.textContent ?? "", /Statement row 1: enter a valid date/);
  await fill("Statement row date 1", "2026-01-01");
  await fill("Statement balance date", "0000-02-28");
  await click("Review proposal");
  assert.match(host.textContent ?? "", /year 0000 is not valid/);
  await fill("Statement balance date", "2026-02-28");
  await fill("Statement row date 1", "2025-12-31");
  await click("Review proposal");
  assert.doesNotMatch(host.textContent ?? "", /row dates must remain within|must match the balance date/i);
});

test("explicit rows, differences, date warning, explanation and QAR save with exact API payload", async () => {
  await fillValid();
  await fill("Book balance date", "2026-03-01");
  await fill("Statement row reference 1", "  Ref text, no bank format  ");
  await fill("Statement row explanation 1", " ملاحظة عربية ");
  await click("Review proposal");
  assert.match(host.textContent ?? "", /Balance dates differ/);
  assert.match(host.textContent ?? "", /Comparison residual \(balance difference − unmatched row difference\)/);
  assert.match(host.textContent ?? "", /Zero residual is not proof/);
  await click("Save reconciliation proposal");
  const write = calls.find((call) => call.method === "POST")!;
  assert.equal(write.path, "/lab/reconciliations/");
  assert.deepEqual(Object.keys(write.body).sort(), ["book_balance", "book_date", "book_rows", "currency", "explanation", "matches", "name", "statement_balance", "statement_date", "statement_rows"].sort());
  assert.equal(write.body.currency, "QAR");
  assert.equal(write.body.statement_rows[0].reference, "  Ref text, no bank format  ");
  assert.equal(write.body.statement_rows[0].explanation, " ملاحظة عربية ");
  assert.equal(write.body.matches.length, 1);
  assert.equal(host.querySelector('[data-testid="reconciliation-formula-statement-net"]')?.textContent, "0.30");
  assert.equal(host.querySelector('[data-testid="reconciliation-formula-book-net"]')?.textContent, "0.30");
  assert.equal(host.querySelector('[data-testid="reconciliation-formula-comparison-residual"]')?.textContent, "0.30");
  assert.match(host.textContent ?? "", /not posted or operationally reconciled/i);
  assert.equal(button("Save proposal revision").disabled, false);
});

test("new source rows require explicit direction and do not auto-match, mutate, or invent references", async () => {
  await click("New reconciliation proposal");
  await click("Add statement row");
  await click("Add book row");
  assert.ok(host.querySelector('[aria-label="Statement source row 1"]'));
  assert.ok(host.querySelector('[data-testid="reconciliation-row-book-1"]'));
  assert.equal((field("Statement row direction 1") as HTMLSelectElement).value, "");
  assert.equal(field("Statement row reference 1").value, "");
  assert.equal(host.querySelectorAll('[data-testid^="reconciliation-match-"]').length, 0);
  await fill("Statement row ID 1", "manual-stmt");
  await fill("Statement row amount 1", "0.10");
  await click("Review proposal");
  assert.match(host.textContent ?? "", /Statement row 1: choose increase or decrease/);
  assert.equal(field("Statement row ID 1").value, "manual-stmt");
  assert.equal(host.querySelectorAll('[data-testid^="reconciliation-match-"]').length, 0);
  assert.ok(host.querySelector('[aria-label="Add manual match"]'));
  assert.ok(host.querySelector('[data-testid="reconciliation-formula-statement-net"]') === null);
});

test("matched source cannot be removed until explicit unmatch; editable rows and history reopen", async () => {
  await click("Open proposal 73:");
  assert.ok(host.querySelector('[aria-label="Reconciliation proposal revision history"]'));
  assert.equal(field("Statement row explanation 1").value, "");
  const matchedRemove = button("Remove statement row 1");
  assert.equal(matchedRemove.disabled, true);
  await click("Unmatch pair 1");
  assert.equal(button("Remove statement row 1").disabled, false);
  await fill("Statement row explanation 1", "Preserve this source note.");
  await click("Review proposal");
  await click("Save proposal revision");
  const put = calls.filter((call) => call.method === "PUT").at(-1)!;
  assert.equal(put.path, "/lab/reconciliations/73/");
  assert.equal(put.body.expected_revision, 1);
  assert.equal(put.body.matches.length, 0);
  assert.equal(put.body.statement_rows[0].explanation, "Preserve this source note.");
  assert.equal(stored.revision, 2);
});

test("409 retains local work; uncertain save blocks blind retry until explicit reopen", async () => {
  await click("Open proposal 73:");
  await fill("Reconciliation explanation", "Keep my local revision.");
  await click("Review proposal");
  handler = (call) => call.method === "PUT" ? { status: 409, data: { detail: "Stale revision." } } : null;
  await click("Save proposal revision");
  assert.equal(field("Reconciliation explanation").value, "Keep my local revision.");
  assert.match(host.textContent ?? "", /local work remains unchanged/i);
  assert.equal(button("Save proposal revision").disabled, true);
  assert.equal(calls.filter((call) => call.method === "PUT").length, 1);

  handler = (call) => call.method === "PUT" ? { status: 503, data: { detail: "Gateway unavailable." } } : null;
  window.confirm = () => true;
  await click("Reload saved proposal");
  await fill("Reconciliation explanation", "Uncertain local edit.");
  await click("Review proposal");
  await click("Save proposal revision");
  assert.match(host.textContent ?? "", /database may have committed/);
  assert.equal(button("Save proposal revision").disabled, true);
  assert.equal(button("New reconciliation proposal").disabled, true);
  assert.equal(calls.filter((call) => call.method === "PUT").length, 2);
  handler = null;
  await click("Refresh saved list");
  await click("Open proposal 73:");
  assert.equal(field("Reconciliation explanation").value, "Explicit source comparison");
  assert.equal(calls.filter((call) => call.method === "PUT").length, 2);
});

test("403 on initial list or post-save list refresh clears protected state; no setup/session write", async () => {
  await act(async () => root.unmount());
  handler = (call) => call.path === "/lab/reconciliations/" && call.method === "GET"
    ? { status: 403, data: { detail: "Denied." } } : null;
  root = createRoot(host);
  await act(async () => { root.render(<FinanceReconciliationLab />); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  assert.match(host.textContent ?? "", /Protected reconciliation inputs were cleared/);
  assert.equal(host.querySelector('[data-testid="reconciliation-editor"]'), null);
  assert.equal(calls.some((call) => call.path === "/lab/session/" && call.method !== "GET"), false);
});

test("post-commit 403 is not swallowed and clears committed protected proposal state", async () => {
  await fillValid();
  await click("Review proposal");
  let committed = false;
  handler = (call) => {
    if (call.method === "POST") { committed = true; return null; }
    if (committed && call.path === "/lab/reconciliations/" && call.method === "GET") return { status: 403, data: { detail: "Expired." } };
    return null;
  };
  await click("Save reconciliation proposal");
  assert.match(host.textContent ?? "", /Protected reconciliation inputs were cleared/);
  assert.equal(host.querySelector('[data-testid="reconciliation-editor"]'), null);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
});

test("server validation errors retain draft and committed list refresh errors state the save succeeded", async () => {
  await fillValid();
  await fill("Reconciliation explanation", "Retain this exact explanation.");
  await click("Review proposal");
  handler = (call) => call.method === "POST" ? { status: 400, data: { detail: "Invalid explicit pair." } } : null;
  await click("Save reconciliation proposal");
  assert.equal(field("Reconciliation explanation").value, "Retain this exact explanation.");
  assert.match(host.textContent ?? "", /Invalid explicit pair.*Inputs remain unchanged/);
  assert.equal(button("Save reconciliation proposal").disabled, false);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);

  handler = null;
  await click("Save reconciliation proposal");
  assert.match(host.textContent ?? "", /Saved proposal 73, revision 1/);
  let committed = false;
  handler = (call) => {
    if (call.method === "PUT") { committed = true; return null; }
    if (committed && call.path === "/lab/reconciliations/" && call.method === "GET") {
      return { status: 503, data: { detail: "List unavailable." } };
    }
    return null;
  };
  await fill("Reconciliation explanation", "A subsequent saved revision.");
  await click("Review proposal");
  await click("Save proposal revision");
  assert.match(host.textContent ?? "", /Proposal saved, but the saved-list refresh failed/);
  assert.match(host.textContent ?? "", /Saved proposal 73, revision 2/);
});
