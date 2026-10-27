// @ts-nocheck
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceInvoiceLab } from "./FinanceInvoiceLab";
import {
  blankInvoiceDraft, calculateInvoiceReview, payloadFromInvoice, totalInvoiceLines,
  validInvoiceDate, validInvoiceMoney, validateInvoice,
} from "./finance-invoice";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let calls: { path: string; method: string; body?: any }[];
let handler: ((call: { path: string; method: string; body?: any }) => Promise<any> | any) | null;
let saved: any;
const nativeFetch = globalThis.fetch;
const nativeConfirm = window.confirm;
const context = {
  legal_entity_label: "Synthetic invoices entity",
  business_units: [{ id: 7, code: "BU-7", name: "Review unit" }],
  accounts: [{ id: 12, code: "6100", name_en: "Materials", name_ar: "مواد" }],
  dimensions: [{ id: 3, code: "REGION", label_en: "Region", label_ar: "منطقة" }],
  dimension_values: [{ id: 31, dimension_id: 3, code: "NORTH", label_en: "North", label_ar: "الشمال" }],
};
const payload = {
  supplier_reference: "  مورد عربي  ",
  invoice_reference: " INV—٥ ",
  invoice_date: "2024-03-01",
  due_date: null,
  currency: "QAR" as const,
  lines: [{
    description: "  خدمات — وصف عربي  ", amount: "0.10", account_id: 12,
    business_unit_id: 7, dimension_value_ids: [31],
  }],
};
function detail(revision = 1, body = payload) {
  const review = { currency: "QAR", total: totalInvoiceLines(body.lines), proposed: true,
    operational_payable: false, approved: false, posted: false };
  const snapshot = { ...body, id: 73, revision, review,
    duplicate_warning: { possible_duplicate: true, matches: [{ id: 69, revision: 2 }] } };
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
    if (call.path === "/lab/invoices/" && call.method === "GET") {
      return response({ invoices: saved ? [{ id: saved.id, supplier_reference: saved.supplier_reference, invoice_reference: saved.invoice_reference, revision: saved.revision }] : [] });
    }
    if (call.path === "/lab/invoices/73/" && call.method === "GET") return response(saved ?? detail());
    if (call.method === "POST" || call.method === "PUT") {
      saved = detail(call.method === "POST" ? 1 : saved.revision + 1, call.body);
      return response(saved);
    }
    return response({ detail: "Unexpected test endpoint." }, 404);
  };
  await act(async () => { root.render(<FinanceInvoiceLab />); });
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
  await fill("Supplier reference", payload.supplier_reference);
  await fill("Invoice reference", payload.invoice_reference);
  await fill("Invoice date", payload.invoice_date);
  await fill("Line description 1", payload.lines[0].description);
  await fill("Line amount 1", payload.lines[0].amount);
  await fill("Line account 1", "12");
  await fill("Line business unit 1", "7");
  await selectMany("Line dimensions 1", ["31"]);
}

test("dates and amounts are strict while exact BigInt totals do not round", () => {
  assert.equal(validInvoiceDate("2024-02-29"), true);
  assert.equal(validInvoiceDate("2025-02-29"), false);
  assert.equal(validInvoiceDate("2024-02-30"), false);
  assert.equal(validInvoiceMoney("999999999999.99"), true);
  assert.equal(validInvoiceMoney("1.001"), false);
  assert.equal(validInvoiceMoney("1e2"), false);
  assert.equal(validInvoiceMoney("1000000000000"), false);
  assert.equal(totalInvoiceLines([{ amount: "999999999999.99" }, { amount: "0.01" }]), "1000000000000.00");
  const blank = blankInvoiceDraft();
  assert.equal(blank.due_date, "");
  assert.equal(blank.lines.length, 1);
  assert.ok(validateInvoice(blank, context).length);
});

test("payload whitelist preserves raw references and descriptions; no invoice/due date ordering is imposed", () => {
  const draft = {
    supplier_reference: payload.supplier_reference, invoice_reference: payload.invoice_reference,
    invoice_date: "2024-12-31", due_date: "2024-01-01",
    lines: [{ description: payload.lines[0].description, amount: "0.10", account_id: "12", business_unit_id: "7", dimension_value_ids: ["31"] }],
  };
  const built = payloadFromInvoice(draft, context);
  assert.deepEqual(Object.keys(built).sort(), ["supplier_reference", "invoice_reference", "invoice_date", "due_date", "currency", "lines"].sort());
  assert.deepEqual(Object.keys(built.lines[0]).sort(), ["description", "amount", "account_id", "business_unit_id", "dimension_value_ids"].sort());
  assert.equal(built.supplier_reference, payload.supplier_reference);
  assert.equal(built.invoice_reference, payload.invoice_reference);
  assert.equal(built.lines[0].description, payload.lines[0].description);
  assert.equal(built.due_date, "2024-01-01");
  assert.deepEqual(calculateInvoiceReview(built), {
    currency: "QAR", total: "0.10", proposed: true, operational_payable: false, approved: false, posted: false,
  });
});

test("rendered editor is English, fully labeled, allows line editing and displays proposal boundary", async () => {
  assert.ok(field("Supplier reference"));
  assert.ok(field("Invoice reference"));
  assert.ok(field("Invoice date"));
  assert.ok(field("Due date"));
  for (const label of ["Line description 1", "Line amount 1", "Line account 1", "Line business unit 1", "Line dimensions 1"]) assert.ok(field(label));
  assert.match(host.textContent ?? "", /not a supplier master/i);
  assert.match(host.textContent ?? "", /operational payable/i);
  await click("Add invoice line");
  assert.ok(field("Line description 2"));
  await click("Remove invoice line 2");
  assert.equal(host.querySelector('[aria-label="Line description 2"]'), null);
});

test("create submits exact whitelisted body, review totals, refreshes register and preserves duplicate warning", async () => {
  await fillValid();
  await click("Review invoice proposal");
  assert.match(host.textContent ?? "", /0\.10/);
  assert.equal(button("Save invoice proposal").disabled, false);
  await click("Save invoice proposal");
  const write = calls.find((call) => call.method === "POST")!;
  assert.equal(write.path, "/lab/invoices/");
  assert.deepEqual(write.body, payload);
  assert.ok(calls.some((call) => call.path === "/lab/invoices/" && call.method === "GET"));
  assert.equal(button("Open supplier invoice 73:").textContent?.includes("INV—٥"), true);
  await click("Open supplier invoice 73:");
  assert.equal((field("Supplier reference") as HTMLInputElement).value, payload.supplier_reference);
  assert.match(host.textContent ?? "", /possible reference match/i);
  assert.match(host.textContent ?? "", /not a uniqueness rule/i);
  assert.match(host.textContent ?? "", /Revision 1/);
});

test("revision writes expected_revision and 409 keeps local edits pending confirmed reload", async () => {
  saved = detail();
  await click("Refresh saved invoices");
  await click("Open supplier invoice 73:");
  await fill("Line description 1", "Local work");
  await click("Review invoice proposal");
  handler = (call) => call.method === "PUT" ? { status: 409, data: { detail: "Stale revision." } } : null;
  await click("Save invoice revision");
  const write = calls.find((call) => call.method === "PUT")!;
  assert.equal(write.path, "/lab/invoices/73/");
  assert.equal(write.body.expected_revision, 1);
  handler = null;
  assert.equal((field("Line description 1") as HTMLTextAreaElement).value, "Local work");
  assert.match(host.textContent ?? "", /local inputs remain unchanged/i);
  window.confirm = () => true;
  handler = null;
  await click("Reload saved invoice");
  assert.equal((field("Line description 1") as HTMLTextAreaElement).value, payload.lines[0].description);
});

test("400 keeps local invoice edits intact and does not mark the save outcome uncertain", async () => {
  saved = detail();
  await click("Refresh saved invoices");
  await click("Open supplier invoice 73:");
  await fill("Line description 1", "Local inputs survive a validation response");
  await click("Review invoice proposal");
  handler = (call) => call.method === "PUT" ? { status: 400, data: { detail: "Invalid reference." } } : null;
  await click("Save invoice revision");
  assert.equal((field("Line description 1") as HTMLTextAreaElement).value, "Local inputs survive a validation response");
  assert.match(host.textContent ?? "", /Invalid reference.*Inputs remain unchanged/i);
  assert.equal(button("Save invoice revision").disabled, false);
  assert.doesNotMatch(host.textContent ?? "", /unknown save outcome/i);
});

test("unknown create outcome requires list inspection and explicit reopen; 403 clears private inputs", async () => {
  await fillValid();
  await click("Review invoice proposal");
  handler = (call) => call.method === "POST" ? { throw: new TypeError("connection ended") } : null;
  await click("Save invoice proposal");
  assert.match(host.textContent ?? "", /database may have committed/i);
  assert.equal(button("Save invoice proposal").disabled, true);
  assert.equal(button("New invoice proposal").disabled, true);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
  handler = null;
  saved = detail();
  await click("Refresh saved invoices");
  window.confirm = () => true;
  await click("Open supplier invoice 73:");
  assert.equal((field("Supplier reference") as HTMLInputElement).value, payload.supplier_reference);

  await fill("Line description 1", "Retained until forbidden response");
  await click("Review invoice proposal");
  handler = (call) => call.method === "PUT" ? { status: 403, data: { detail: "Expired." } } : null;
  await click("Save invoice revision");
  assert.equal(host.querySelector('[data-testid="invoice-editor"]'), null);
  assert.match(host.textContent ?? "", /Protected invoice inputs were cleared/i);
});

test("missing saved choices remain visible and prevent silent substitution", async () => {
  saved = detail();
  await click("Refresh saved invoices");
  await click("Open supplier invoice 73:");
  handler = (call) => call.path === "/lab/context/" ? {
    data: { ...context, accounts: [], business_units: [], dimension_values: [] },
  } : null;
  await fill("Line description 1", "Changed line");
  await click("Review invoice proposal");
  await click("Save invoice revision");
  assert.equal(calls.filter((call) => call.method === "PUT").length, 0);
  assert.equal((field("Line account 1") as HTMLSelectElement).value, "12");
  assert.match((field("Line account 1") as HTMLSelectElement).selectedOptions[0].textContent ?? "", /Unavailable saved choice \(12\)/);
  assert.equal((field("Line description 1") as HTMLTextAreaElement).value, "Changed line");
  assert.match(host.textContent ?? "", /Original choices remain visible/i);
});
