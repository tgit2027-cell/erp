// @ts-nocheck
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceAdjustmentLab } from "./FinanceAdjustmentLab";
import {
  blankAdjustmentDraft, calculateAdjustmentTotal, payloadFromAdjustment, validateAdjustment,
} from "./finance-adjustment";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let calls: { path: string; method: string; body?: any }[];
let handler: ((call: { path: string; method: string; body?: any }) => Promise<any> | any) | null;
let saved: any;
const nativeFetch = globalThis.fetch;
const nativeConfirm = window.confirm;
const context = {
  legal_entity_label: "Synthetic supplier adjustment entity",
  business_units: [{ id: 7, code: "BU-7", name: "Review unit" }],
  accounts: [{ id: 12, code: "6100", name_en: "Materials", name_ar: "مواد" }],
  dimensions: [{ id: 3, code: "REGION", label_en: "Region", label_ar: "منطقة" }],
  dimension_values: [{ id: 31, dimension_id: 3, code: "NORTH", label_en: "North", label_ar: "الشمال" }],
};
const invoice = { id: 43, supplier_reference: "SUP-7", invoice_reference: "INV-43", revision: 3 };
const payload = {
  proposal_type: "credit" as const,
  supplier_reference: "SUP-7",
  document_reference: "CR-205",
  proposal_date: "2025-04-13",
  reason: "Explicit service adjustment",
  linked_invoice_id: 43,
  currency: "QAR" as const,
  lines: [{
    description: "Service variance", amount: "0.10", account_id: 12,
    business_unit_id: 7, dimension_value_ids: [31],
  }],
};
function detail(revision = 1, body = payload) {
  const review = {
    currency: "QAR", total: calculateAdjustmentTotal(body.lines),
    interpretation: "Explicit non-negative magnitudes; credit/debit is classification only.",
    amount_basis: "explicit_non_negative_magnitudes", proposed: true, issued_credit_note: false,
    settlement: false, operational_payable_adjustment: false, approved: false, posted: false,
  };
  const snapshot = {
    ...body, id: 81, revision, review,
    linked_invoice: body.linked_invoice_id === null ? null : {
      id: body.linked_invoice_id, revision: 3, supplier_reference: "SUP-7", invoice_reference: "INV-43",
    },
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
  saved = null;
  globalThis.fetch = async (input: RequestInfo | URL, options: RequestInit = {}) => {
    const call = {
      path: String(input), method: String(options.method ?? "GET"),
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
    if (call.path === "/lab/invoices/" && call.method === "GET") return response({ invoices: [invoice] });
    if (call.path === "/lab/adjustments/" && call.method === "GET") {
      return response({ adjustments: saved ? [{ id: saved.id, proposal_type: saved.proposal_type,
        supplier_reference: saved.supplier_reference, document_reference: saved.document_reference, revision: saved.revision }] : [] });
    }
    if (call.path === "/lab/adjustments/81/" && call.method === "GET") return response(saved ?? detail());
    if (call.method === "POST" || call.method === "PUT") {
      saved = detail(call.method === "POST" ? 1 : saved.revision + 1, call.body);
      return response(saved);
    }
    return response({ detail: "Unexpected test endpoint." }, 404);
  };
  await act(async () => { root.render(<FinanceAdjustmentLab />); });
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
async function selectMany(label: string, values: string[]) {
  const select = field(label) as HTMLSelectElement;
  await act(async () => {
    for (const option of [...select.options]) option.selected = values.includes(option.value);
    select.dispatchEvent(new select.ownerDocument.defaultView!.Event("change", { bubbles: true }));
  });
}
async function fillValid() {
  await fill("Proposal type", "credit");
  await fill("Supplier reference", payload.supplier_reference);
  await fill("Document reference", payload.document_reference);
  await fill("Proposal date", payload.proposal_date);
  await fill("Reason", payload.reason);
  await fill("Linked invoice", "43");
  await fill("Line description 1", payload.lines[0].description);
  await fill("Line amount 1", payload.lines[0].amount);
  await fill("Line account 1", "12");
  await fill("Line business unit 1", "7");
  await selectMany("Line dimensions 1", ["31"]);
}

test("helper validates exact non-negative magnitudes, classification and explicit link consistency", () => {
  assert.equal(calculateAdjustmentTotal([{ amount: "999999999999.99" }, { amount: "0.01" }]), "1000000000000.00");
  const draft = {
    ...blankAdjustmentDraft(),
    proposal_type: "debit",
    supplier_reference: "SUP-7",
    document_reference: "D-2",
    proposal_date: "2025-04-13",
    reason: "reason",
    linked_invoice_id: "43",
    lines: [{ description: "Line", amount: "2.5", account_id: "12", business_unit_id: "", dimension_value_ids: [] }],
  };
  assert.deepEqual(validateAdjustment(draft, context, [invoice]), []);
  assert.equal(blankAdjustmentDraft().proposal_type, "");
  assert.ok(validateAdjustment({ ...draft, proposal_type: "" }, context, [invoice]).some((item) => /classification/.test(item)));
  assert.deepEqual(validateAdjustment({ ...draft, supplier_reference: " \tSUP-7\r " }, context, [invoice]), []);
  const built = payloadFromAdjustment(draft, context, [invoice]);
  assert.equal(built.proposal_type, "debit");
  assert.equal(built.lines[0].amount, "2.5");
  assert.equal(built.linked_invoice_id, 43);
  assert.ok(validateAdjustment({ ...draft, lines: [{ ...draft.lines[0], amount: "-2" }] }, context, [invoice]).some((item) => /non-negative/.test(item)));
  assert.ok(validateAdjustment({ ...draft, supplier_reference: "different" }, context, [invoice]).some((item) => /exactly match/.test(item)));
  assert.ok(validateAdjustment({ ...draft, linked_invoice_id: "" }, context, [invoice]).length === 0);
});

test("rendered lab is English-only, exposes classifications, exact amount and explicit optional invoice link", async () => {
  assert.equal(field("Proposal type").value, "");
  for (const label of ["Proposal type", "Supplier reference", "Document reference", "Proposal date", "Reason", "Linked invoice",
    "Line description 1", "Line amount 1", "Line account 1", "Line business unit 1", "Line dimensions 1"]) assert.ok(field(label));
  assert.match(host.textContent ?? "", /classification is a label only/i);
  assert.match(host.textContent ?? "", /not an issued credit note/i);
  assert.match(host.textContent ?? "", /does not infer the supplier or copy lines/i);
  await click("Add proposal line");
  assert.ok(field("Line description 2"));
  await click("Remove proposal line 2");
  assert.equal(host.querySelector('[aria-label="Line description 2"]'), null);
});

test("create sends explicit whitelisted payload, review remains proposal-only and reopening shows revision history", async () => {
  await fillValid();
  await click("Review adjustment proposal");
  assert.match(host.textContent ?? "", /0\.10/);
  assert.match(host.textContent ?? "", /operational payable adjustment: no/i);
  await click("Save adjustment proposal");
  const write = calls.find((call) => call.method === "POST")!;
  assert.equal(write.path, "/lab/adjustments/");
  assert.deepEqual(write.body, payload);
  assert.ok(calls.some((call) => call.path === "/lab/adjustments/" && call.method === "GET"));
  await click("Open adjustment proposal 81:");
  assert.equal((field("Supplier reference") as HTMLInputElement).value, "SUP-7");
  assert.equal((field("Linked invoice") as HTMLSelectElement).value, "43");
  assert.match(host.textContent ?? "", /Revision 1/);
  assert.match(host.textContent ?? "", /linked invoice 43/);
});

test("revision includes expected_revision and 409 preserves edits until confirmed reload", async () => {
  saved = detail();
  await click("Refresh saved proposals");
  await click("Open adjustment proposal 81:");
  await fill("Reason", "Local unsaved work");
  await click("Review adjustment proposal");
  handler = (call) => call.method === "PUT" ? { status: 409, data: { detail: "Stale revision." } } : null;
  await click("Save adjustment revision");
  const write = calls.find((call) => call.method === "PUT")!;
  assert.equal(write.path, "/lab/adjustments/81/");
  assert.equal(write.body.expected_revision, 1);
  assert.equal((field("Reason") as HTMLTextAreaElement).value, "Local unsaved work");
  assert.match(host.textContent ?? "", /local inputs remain unchanged/i);
  window.confirm = () => true;
  handler = null;
  await click("Reload saved adjustment");
  assert.equal((field("Reason") as HTMLTextAreaElement).value, payload.reason);
});

test("400 keeps local work, 403 clears protected fields, and uncertain write requires inspect and explicit reopen", async () => {
  saved = detail();
  await click("Refresh saved proposals");
  await click("Open adjustment proposal 81:");
  await fill("Reason", "Retained after validation");
  await click("Review adjustment proposal");
  handler = (call) => call.method === "PUT" ? { status: 400, data: { detail: "Supplier does not match." } } : null;
  await click("Save adjustment revision");
  assert.equal((field("Reason") as HTMLTextAreaElement).value, "Retained after validation");
  assert.match(host.textContent ?? "", /Supplier does not match.*Inputs remain unchanged/i);
  assert.doesNotMatch(host.textContent ?? "", /unknown save outcome/i);

  handler = (call) => call.method === "PUT" ? { status: 403, data: { detail: "Expired." } } : null;
  await click("Save adjustment revision");
  assert.equal(host.querySelector('[data-testid="adjustment-editor"]'), null);
  assert.match(host.textContent ?? "", /Protected adjustment inputs were cleared/i);
});

test("unknown create outcome blocks replacement until refreshed list is explicitly reopened", async () => {
  await fillValid();
  await click("Review adjustment proposal");
  handler = (call) => call.method === "POST" ? { throw: new TypeError("connection ended") } : null;
  await click("Save adjustment proposal");
  assert.match(host.textContent ?? "", /database may have committed/i);
  assert.equal(button("Save adjustment proposal").disabled, true);
  assert.equal(button("New adjustment proposal").disabled, true);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
  saved = detail();
  handler = null;
  await click("Refresh saved proposals");
  await click("Open adjustment proposal 81:");
  assert.equal((field("Reason") as HTMLTextAreaElement).value, payload.reason);
  assert.match(host.textContent ?? "", /revision 1/i);
});
