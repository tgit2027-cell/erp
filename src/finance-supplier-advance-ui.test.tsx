// @ts-nocheck
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceSupplierAdvanceLab } from "./FinanceSupplierAdvanceLab";
import {
  blankSupplierAdvanceDraft, calculateSupplierAdvanceReview, payloadFromSupplierAdvance, validateSupplierAdvance,
} from "./finance-supplier-advance";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let calls: { path: string; method: string; body?: any }[];
let handler: ((call: { path: string; method: string; body?: any }) => Promise<any> | any) | null;
let saved: any;
const nativeFetch = globalThis.fetch;
const nativeConfirm = window.confirm;
const context = {
  legal_entity_label: "Synthetic supplier advance entity",
  business_units: [{ id: 7, code: "BU-7", name: "Review unit" }],
  accounts: [{ id: 12, code: "6100", name_en: "Materials", name_ar: "مواد" }],
  dimensions: [{ id: 3, code: "REGION", label_en: "Region", label_ar: "منطقة" }],
  dimension_values: [{ id: 31, dimension_id: 3, code: "NORTH", label_en: "North", label_ar: "الشمال" }],
};
const payload = {
  supplier_reference: "SUP-7", document_reference: "ADV-205", proposed_date: "2025-04-13",
  amount: "0.10", purpose: "دفعة مقدمة لخدمات التهيئة", account_id: 12, business_unit_id: 7,
  dimension_value_ids: [31], currency: "QAR",
};
function detail(revision = 1, body = payload) {
  const review = {
    currency: "QAR", total: `${body.amount.split(".")[0]}.${(body.amount.split(".")[1] ?? "").padEnd(2, "0")}`,
    proposed: true, unpaid: true, unapplied: true, actual_payment: false, invoice_application: false,
    operational_payable_change: false, approved: false, posted: false,
  };
  const snapshot = { ...body, id: 81, revision, review };
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
    if (call.path === "/lab/supplier-advances/" && call.method === "GET") {
      return response({ advances: saved ? [{ id: saved.id, supplier_reference: saved.supplier_reference,
        document_reference: saved.document_reference, proposed_date: saved.proposed_date, amount: saved.amount, revision: saved.revision }] : [] });
    }
    if (call.path === "/lab/supplier-advances/81/" && call.method === "GET") return response(saved ?? detail());
    if (call.method === "POST" || call.method === "PUT") {
      saved = detail(call.method === "POST" ? 1 : saved.revision + 1, call.body);
      return response(saved, call.method === "POST" ? 201 : 200);
    }
    return response({ detail: "Unexpected test endpoint." }, 404);
  };
  await act(async () => { root.render(<FinanceSupplierAdvanceLab />); });
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
  await fill("Supplier reference", payload.supplier_reference);
  await fill("Document reference", payload.document_reference);
  await fill("Proposed date", payload.proposed_date);
  await fill("Amount", payload.amount);
  await fill("Purpose", payload.purpose);
  await fill("Account", "12");
  await fill("Business unit", "7");
  await selectMany("Dimensions", ["31"]);
}

test("exact magnitude/date bounds and independent optional saved references", () => {
  const draft = {
    ...blankSupplierAdvanceDraft(), supplier_reference: "SUP-7", document_reference: "ADV-1",
    proposed_date: "2025-04-13", amount: "999999999999.99", purpose: "review",
  };
  assert.deepEqual(validateSupplierAdvance(draft, context), []);
  assert.equal(calculateSupplierAdvanceReview(payloadFromSupplierAdvance(draft, context)).total, "999999999999.99");
  const optional = payloadFromSupplierAdvance({ ...draft, amount: "0", account_id: "", business_unit_id: "", dimension_value_ids: [] }, context);
  assert.equal(optional.account_id, null); assert.equal(optional.business_unit_id, null); assert.deepEqual(optional.dimension_value_ids, []);
  for (const amount of ["-0.01", "01", "1.001", "1000000000000.00", ""]) {
    assert.ok(validateSupplierAdvance({ ...draft, amount }, context).length, `accepted ${amount}`);
  }
  for (const proposed_date of ["2025-02-29", "2025-2-09", "2025-04-31", ""]) {
    assert.ok(validateSupplierAdvance({ ...draft, proposed_date }, context).length, `accepted ${proposed_date}`);
  }
  assert.ok(validateSupplierAdvance({ ...draft, supplier_reference: "" }, context).some((item) => /supplier reference/i.test(item)));
  assert.ok(validateSupplierAdvance({ ...draft, document_reference: "D".repeat(201) }, context).some((item) => /200 characters/i.test(item)));
  assert.ok(validateSupplierAdvance({ ...draft, account_id: "999" }, context).length);
});

test("English-only controls preserve raw business text; whitelist payload saves and reopens persisted history", async () => {
  for (const label of ["Supplier reference", "Document reference", "Proposed date", "Amount", "Purpose", "Account", "Business unit", "Dimensions"]) assert.ok(field(label));
  assert.doesNotMatch(host.textContent ?? "", /[\u0600-\u06ff]/);
  assert.equal((field("Amount") as HTMLInputElement).value, "");
  assert.equal((field("Proposed date") as HTMLInputElement).value, "");
  await fillValid(); await click("Review supplier-advance proposal");
  assert.match(host.textContent ?? "", /0\.10/);
  assert.match(host.textContent ?? "", /unpaid: yes · unapplied: yes/i);
  await click("Save supplier-advance proposal");
  const write = calls.find((call) => call.method === "POST")!;
  assert.equal(write.path, "/lab/supplier-advances/");
  assert.deepEqual(write.body, payload);
  assert.deepEqual(Object.keys(write.body).sort(), [
    "supplier_reference", "document_reference", "proposed_date", "amount", "purpose", "account_id",
    "business_unit_id", "dimension_value_ids", "currency",
  ].sort());
  assert.ok(calls.some((call) => call.path === "/lab/supplier-advances/" && call.method === "GET"));
  await click("Open supplier-advance proposal 81:");
  assert.equal((field("Purpose") as HTMLTextAreaElement).value, payload.purpose);
  assert.equal((field("Amount") as HTMLInputElement).value, "0.10");
  assert.match(host.textContent ?? "", /Revision 1/);
  assert.match(host.textContent ?? "", /دفعة مقدمة لخدمات التهيئة/);
});

test("editing uses expected revision; 409 preserves edits, confirmed reload, 400 retains and 403 clears", async () => {
  saved = detail();
  await click("Refresh saved proposals"); await click("Open supplier-advance proposal 81:");
  await fill("Purpose", "Local edit"); await click("Review supplier-advance proposal");
  handler = (call) => call.method === "PUT" ? { status: 409, data: { detail: "Stale revision." } } : null;
  await click("Save supplier-advance revision");
  assert.equal(calls.find((call) => call.method === "PUT")!.body.expected_revision, 1);
  assert.equal((field("Purpose") as HTMLTextAreaElement).value, "Local edit");
  assert.match(host.textContent ?? "", /local inputs remain unchanged/i);
  window.confirm = () => true; handler = null; await click("Reload saved supplier advance");
  assert.equal((field("Purpose") as HTMLTextAreaElement).value, payload.purpose);

  await fill("Purpose", "Retained on 400"); await click("Review supplier-advance proposal");
  handler = (call) => call.method === "PUT" ? { status: 400, data: { detail: "Input rejected." } } : null;
  await click("Save supplier-advance revision");
  assert.equal((field("Purpose") as HTMLTextAreaElement).value, "Retained on 400");
  assert.match(host.textContent ?? "", /Input rejected.*Inputs remain unchanged/i);
  handler = (call) => call.method === "PUT" ? { status: 403, data: { detail: "Expired." } } : null;
  await click("Save supplier-advance revision");
  assert.equal(host.querySelector('[data-testid="supplier-advance-editor-fields"]'), null);
  assert.match(host.textContent ?? "", /Protected supplier-advance inputs were cleared/i);
});

test("unknown save outcome requires register inspection and explicit reopen before further work", async () => {
  await fillValid(); await click("Review supplier-advance proposal");
  handler = (call) => call.method === "POST" ? { throw: new TypeError("connection ended") } : null;
  await click("Save supplier-advance proposal");
  assert.match(host.textContent ?? "", /database may have committed/i);
  assert.match(host.textContent ?? "", /No exactly-once guarantee/i);
  assert.equal(button("Save supplier-advance proposal").disabled, true);
  assert.equal(button("New supplier-advance proposal").disabled, true);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
  saved = detail(); handler = null;
  await click("Refresh saved proposals"); await click("Open supplier-advance proposal 81:");
  assert.equal((field("Purpose") as HTMLTextAreaElement).value, payload.purpose);
  assert.match(host.textContent ?? "", /revision 1/i);
});
