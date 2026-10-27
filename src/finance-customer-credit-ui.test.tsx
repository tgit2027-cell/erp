// @ts-nocheck
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceCustomerCreditLab } from "./FinanceCustomerCreditLab";
import {
  blankCustomerCreditDraft, calculateCustomerCreditTotal, payloadFromCustomerCredit, validateCustomerCredit,
} from "./finance-customer-credit";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let calls: { path: string; method: string; body?: any }[];
let handler: ((call: { path: string; method: string; body?: any }) => Promise<any> | any) | null;
let saved: any;
const nativeFetch = globalThis.fetch;
const nativeConfirm = window.confirm;
const context = {
  legal_entity_label: "Synthetic customer credit entity",
  business_units: [{ id: 7, code: "BU-7", name: "Review unit" }],
  accounts: [{ id: 12, code: "6100", name_en: "Materials", name_ar: "مواد" }],
  dimensions: [{ id: 3, code: "REGION", label_en: "Region", label_ar: "منطقة" }],
  dimension_values: [{ id: 31, dimension_id: 3, code: "NORTH", label_en: "North", label_ar: "الشمال" }],
};
const invoice = { id: 43, customer_reference: "CUS-7", invoice_reference: "CINV-43", revision: 3 };
const payload = {
  customer_reference: "CUS-7", document_reference: "CR-205", proposal_date: "2025-04-13",
  reason: "Explicit service adjustment", linked_invoice_id: 43, currency: "QAR",
  lines: [{ description: "Service variance", amount: "0.10", account_id: 12, business_unit_id: 7, dimension_value_ids: [31] }],
};
function detail(revision = 1, body = payload) {
  const review = {
    currency: "QAR", total: calculateCustomerCreditTotal(body.lines), amount_basis: "explicit_non_negative_magnitudes",
    interpretation: "Explicit non-negative magnitudes.", proposed: true, issued_credit_note: false,
    refund: false, settlement: false, operational_receivable_adjustment: false, approved: false, posted: false,
  };
  const snapshot = {
    ...body, id: 81, revision, review,
    linked_invoice: body.linked_invoice_id === null ? null : {
      id: body.linked_invoice_id, revision: 3, customer_reference: "CUS-7", invoice_reference: "CINV-43",
    },
  };
  return { ...snapshot, history: [{ revision, created_at: "synthetic timestamp", snapshot }] };
}
function response(data: any, status = 200) { return new Response(JSON.stringify(data), { status }); }
beforeEach(async () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  calls = []; handler = null; saved = null;
  globalThis.fetch = async (input: RequestInfo | URL, options: RequestInit = {}) => {
    const call = { path: String(input), method: String(options.method ?? "GET"), body: options.body ? JSON.parse(String(options.body)) : undefined };
    calls.push(call);
    const custom = await handler?.(call);
    if (custom) { if (custom.throw) throw custom.throw; return response(custom.data, custom.status ?? 200); }
    if (call.path === "/lab/session/") return response({ authenticated: true, csrf_token: "TEST" });
    if (call.path === "/lab/context/") return response(context);
    if (call.path === "/lab/customer-invoices/") return response({ customer_invoices: [invoice] });
    if (call.path === "/lab/customer-credits/" && call.method === "GET") {
      return response({ customer_credits: saved ? [{ id: saved.id, customer_reference: saved.customer_reference,
        document_reference: saved.document_reference, revision: saved.revision }] : [] });
    }
    if (call.path === "/lab/customer-credits/81/" && call.method === "GET") return response(saved ?? detail());
    if (call.method === "POST" || call.method === "PUT") {
      saved = detail(call.method === "POST" ? 1 : saved.revision + 1, call.body);
      return response(saved, call.method === "POST" ? 201 : 200);
    }
    return response({ detail: "Unexpected test endpoint." }, 404);
  };
  await act(async () => { root.render(<FinanceCustomerCreditLab />); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove(); globalThis.fetch = nativeFetch; window.confirm = nativeConfirm;
});
function button(prefix: string) {
  const found = [...host.querySelectorAll("button")].find((item) =>
    item.textContent?.startsWith(prefix) || item.getAttribute("aria-label")?.startsWith(prefix));
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
async function selectMany(label: string, values: string[]) {
  const select = field(label) as HTMLSelectElement;
  await act(async () => {
    for (const option of [...select.options]) option.selected = values.includes(option.value);
    select.dispatchEvent(new select.ownerDocument.defaultView!.Event("change", { bubbles: true }));
  });
}
async function fillValid() {
  await fill("Customer reference", payload.customer_reference);
  await fill("Document reference", payload.document_reference);
  await fill("Proposal date", payload.proposal_date);
  await fill("Reason", payload.reason);
  await fill("Linked customer invoice", "43");
  await fill("Line description 1", payload.lines[0].description);
  await fill("Line amount 1", payload.lines[0].amount);
  await fill("Line account 1", "12");
  await fill("Line business unit 1", "7");
  await selectMany("Line dimensions 1", ["31"]);
}

test("helper uses exact magnitudes and validates case-sensitive linked customer references", () => {
  assert.equal(calculateCustomerCreditTotal([{ amount: "999999999999.99" }, { amount: "0.01" }]), "1000000000000.00");
  const draft = {
    ...blankCustomerCreditDraft(), customer_reference: " \tCUS-7\r ", document_reference: "CR-1",
    proposal_date: "2025-04-13", reason: "reason", linked_invoice_id: "43",
    lines: [{ description: "Line", amount: "2.5", account_id: "12", business_unit_id: "", dimension_value_ids: [] }],
  };
  assert.deepEqual(validateCustomerCredit(draft, context, [invoice]), []);
  const built = payloadFromCustomerCredit(draft, context, [invoice]);
  assert.equal(built.customer_reference, draft.customer_reference);
  assert.equal(built.lines[0].amount, "2.5");
  assert.deepEqual(Object.keys(built).sort(), ["customer_reference", "document_reference", "proposal_date", "reason", "linked_invoice_id", "currency", "lines"].sort());
  assert.equal("proposal_type" in built, false);
  assert.ok(validateCustomerCredit({ ...draft, customer_reference: "cus-7" }, context, [invoice]).some((item) => /exactly match/.test(item)));
  for (const amount of ["-0.01", "01", "1.001", "1000000000000.00"]) {
    assert.ok(validateCustomerCredit({ ...draft, lines: [{ ...draft.lines[0], amount }] }, context, [invoice]).length);
  }
  assert.ok(validateCustomerCredit({ ...draft, proposal_date: "2025-02-29" }, context, [invoice]).length);
});

test("English-only editor links explicitly, saves whitelist payload, and reopens proposal history", async () => {
  for (const label of ["Customer reference", "Document reference", "Proposal date", "Reason", "Linked customer invoice",
    "Line description 1", "Line amount 1", "Line account 1", "Line business unit 1", "Line dimensions 1"]) assert.ok(field(label));
  assert.doesNotMatch(host.textContent ?? "", /[\u0600-\u06ff]/);
  await fillValid();
  await click("Review customer-credit proposal");
  assert.match(host.textContent ?? "", /0\.10/);
  assert.match(host.textContent ?? "", /issued credit note: no · refund: no · settlement: no · operational receivable adjustment: no/i);
  await click("Save customer-credit proposal");
  const write = calls.find((call) => call.method === "POST")!;
  assert.equal(write.path, "/lab/customer-credits/");
  assert.deepEqual(write.body, payload);
  assert.ok(calls.some((call) => call.path === "/lab/customer-credits/" && call.method === "GET"));
  await click("Open customer-credit proposal 81:");
  assert.equal((field("Customer reference") as HTMLInputElement).value, "CUS-7");
  assert.equal((field("Linked customer invoice") as HTMLSelectElement).value, "43");
  assert.match(host.textContent ?? "", /Revision 1/);
});

test("revision conflict preserves local edits, 400 preserves inputs, 403 clears, and unknown create requires inspect/reopen", async () => {
  saved = detail();
  await click("Refresh saved proposals"); await click("Open customer-credit proposal 81:");
  await fill("Reason", "Local work");
  await click("Review customer-credit proposal");
  handler = (call) => call.method === "PUT" ? { status: 409, data: { detail: "Stale revision." } } : null;
  await click("Save customer-credit revision");
  assert.equal(calls.find((call) => call.method === "PUT")!.body.expected_revision, 1);
  assert.equal((field("Reason") as HTMLTextAreaElement).value, "Local work");
  assert.match(host.textContent ?? "", /local inputs remain unchanged/i);
  window.confirm = () => true; handler = null; await click("Reload saved customer credit");
  assert.equal((field("Reason") as HTMLTextAreaElement).value, payload.reason);

  await fill("Reason", "Retained on 400"); await click("Review customer-credit proposal");
  handler = (call) => call.method === "PUT" ? { status: 400, data: { detail: "Input rejected." } } : null;
  await click("Save customer-credit revision");
  assert.equal((field("Reason") as HTMLTextAreaElement).value, "Retained on 400");
  assert.match(host.textContent ?? "", /Input rejected.*Inputs remain unchanged/i);
  handler = (call) => call.method === "PUT" ? { status: 403, data: { detail: "Expired." } } : null;
  await click("Save customer-credit revision");
  assert.equal(host.querySelector('[data-testid="customer-credit-editor-fields"]'), null);
  assert.match(host.textContent ?? "", /Protected customer-credit inputs were cleared/i);
});

test("unknown create outcome blocks replacement until saved register inspected and record reopened", async () => {
  await fillValid(); await click("Review customer-credit proposal");
  handler = (call) => call.method === "POST" ? { throw: new TypeError("connection ended") } : null;
  await click("Save customer-credit proposal");
  assert.match(host.textContent ?? "", /database may have committed/i);
  assert.equal(button("Save customer-credit proposal").disabled, true);
  assert.equal(button("New customer-credit proposal").disabled, true);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
  saved = detail(); handler = null;
  await click("Refresh saved proposals"); await click("Open customer-credit proposal 81:");
  assert.equal((field("Reason") as HTMLTextAreaElement).value, payload.reason);
  assert.match(host.textContent ?? "", /revision 1/i);
});
