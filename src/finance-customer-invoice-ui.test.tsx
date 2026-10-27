// @ts-nocheck
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceCustomerInvoiceLab } from "./FinanceCustomerInvoiceLab";
import {
  blankCustomerInvoiceDraft, calculateCustomerInvoiceReview, payloadFromCustomerInvoice,
  totalCustomerInvoiceLines, validCustomerInvoiceDate, validCustomerInvoiceMoney,
  validateCustomerInvoice,
} from "./finance-customer-invoice";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let calls: { path: string; method: string; body?: any }[];
let handler: ((call: { path: string; method: string; body?: any }) => Promise<any> | any) | null;
let saved: any;
const nativeFetch = globalThis.fetch;
const nativeConfirm = window.confirm;
const context = {
  legal_entity_label: "Synthetic customer invoice entity",
  business_units: [{ id: 7, code: "BU-7", name: "Review unit" }],
  accounts: [{ id: 12, code: "4100", name_en: "Services", name_ar: "خدمات" }],
  dimensions: [{ id: 3, code: "REGION", label_en: "Region", label_ar: "منطقة" }],
  dimension_values: [{ id: 31, dimension_id: 3, code: "NORTH", label_en: "North", label_ar: "الشمال" }],
};
const payload = {
  customer_reference: "  عميل Tivoli  ",
  customer_details: "  جهة — تفاصيل عربية  ",
  invoice_reference: " CUST—٤٢ ",
  invoice_date: "2024-03-01",
  due_date: null,
  currency: "QAR" as const,
  lines: [{
    description: "  خدمات — وصف عربي  ", amount: "0.10", account_id: 12,
    business_unit_id: 7, dimension_value_ids: [31],
  }],
};
function detail(revision = 1, body = payload) {
  const review = {
    currency: "QAR", total: totalCustomerInvoiceLines(body.lines), proposed: true,
    issued_customer_invoice: false, operational_receivable: false, approved: false, posted: false,
  };
  const snapshot = { ...body, id: 73, revision, review };
  return {
    ...snapshot, duplicate_warning: { possible_duplicate: true, matches: [{ id: 69, revision: 2 }] },
    history: [{ revision, created_at: "synthetic timestamp", snapshot }],
  };
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
    if (call.path === "/lab/customer-invoices/" && call.method === "GET") {
      return response({ customer_invoices: saved ? [{
        id: saved.id, customer_reference: saved.customer_reference,
        invoice_reference: saved.invoice_reference, revision: saved.revision,
      }] : [] });
    }
    if (call.path === "/lab/customer-invoices/73/" && call.method === "GET") return response(saved ?? detail());
    if (call.method === "POST" || call.method === "PUT") {
      saved = detail(call.method === "POST" ? 1 : saved.revision + 1, call.body);
      return response(saved);
    }
    return response({ detail: "Unexpected test endpoint." }, 404);
  };
  await act(async () => { root.render(<FinanceCustomerInvoiceLab />); });
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
  await fill("Customer reference", payload.customer_reference);
  await fill("Customer details", payload.customer_details);
  await fill("Invoice reference", payload.invoice_reference);
  await fill("Invoice date", payload.invoice_date);
  await fill("Line description 1", payload.lines[0].description);
  await fill("Line amount 1", payload.lines[0].amount);
  await fill("Line account 1", "12");
  await fill("Line business unit 1", "7");
  await selectMany("Line dimensions 1", ["31"]);
}

test("customer invoice helpers enforce strict exact dates and money with integer-cent totals", () => {
  assert.equal(validCustomerInvoiceDate("2024-02-29"), true);
  assert.equal(validCustomerInvoiceDate("2025-02-29"), false);
  assert.equal(validCustomerInvoiceDate("2024-02-30"), false);
  assert.equal(validCustomerInvoiceMoney("999999999999.99"), true);
  assert.equal(validCustomerInvoiceMoney("1.001"), false);
  assert.equal(validCustomerInvoiceMoney("1e2"), false);
  assert.equal(validCustomerInvoiceMoney("1000000000000"), false);
  assert.equal(totalCustomerInvoiceLines([{ amount: "999999999999.99" }, { amount: "0.01" }]), "1000000000000.00");
  const blank = blankCustomerInvoiceDraft();
  assert.equal(blank.due_date, "");
  assert.equal(blank.customer_details, "");
  assert.equal(blank.lines.length, 1);
  assert.ok(validateCustomerInvoice(blank, context).length);
});

test("payload whitelist preserves entered Arabic and exact values, with independent optional due date", () => {
  const draft = {
    customer_reference: payload.customer_reference, customer_details: payload.customer_details,
    invoice_reference: payload.invoice_reference, invoice_date: "2024-12-31", due_date: "2024-01-01",
    lines: [{ description: payload.lines[0].description, amount: "0.10", account_id: "12", business_unit_id: "7", dimension_value_ids: ["31"] }],
  };
  const built = payloadFromCustomerInvoice(draft, context);
  assert.deepEqual(Object.keys(built).sort(), [
    "customer_reference", "customer_details", "invoice_reference", "invoice_date", "due_date", "currency", "lines",
  ].sort());
  assert.deepEqual(Object.keys(built.lines[0]).sort(), ["description", "amount", "account_id", "business_unit_id", "dimension_value_ids"].sort());
  assert.equal(built.customer_reference, payload.customer_reference);
  assert.equal(built.customer_details, payload.customer_details);
  assert.equal(built.lines[0].description, payload.lines[0].description);
  assert.equal(built.due_date, "2024-01-01");
  assert.deepEqual(calculateCustomerInvoiceReview(built), {
    currency: "QAR", total: "0.10", proposed: true, issued_customer_invoice: false,
    operational_receivable: false, approved: false, posted: false,
  });
});

test("rendered editor exposes English controls, line management, and customer-document boundaries", async () => {
  for (const label of ["Customer reference", "Customer details", "Invoice reference", "Invoice date", "Due date",
    "Line description 1", "Line amount 1", "Line account 1", "Line business unit 1", "Line dimensions 1"]) assert.ok(field(label));
  assert.match(host.textContent ?? "", /internal proposal only/i);
  assert.match(host.textContent ?? "", /customer-facing issued documents remain separately bilingual/i);
  assert.match(host.textContent ?? "", /not translated/i);
  assert.match(host.textContent ?? "", /no customer master lookup/i);
  await click("Add invoice line");
  assert.ok(field("Line description 2"));
  await click("Remove invoice line 2");
  assert.equal(host.querySelector('[aria-label="Line description 2"]'), null);
});

test("create persists exact whitelist, refreshes register, reopens saved revision and renders history", async () => {
  await fillValid();
  await click("Review customer-invoice proposal");
  assert.match(host.textContent ?? "", /0\.10/);
  await click("Save customer-invoice proposal");
  const write = calls.find((call) => call.method === "POST")!;
  assert.equal(write.path, "/lab/customer-invoices/");
  assert.deepEqual(write.body, payload);
  assert.ok(calls.some((call) => call.path === "/lab/customer-invoices/" && call.method === "GET"));
  assert.equal(button("Open customer invoice 73:").textContent?.includes("CUST—٤٢"), true);
  await click("Open customer invoice 73:");
  assert.equal((field("Customer reference") as HTMLInputElement).value, payload.customer_reference);
  assert.equal((field("Customer details") as HTMLTextAreaElement).value, payload.customer_details);
  assert.match(host.textContent ?? "", /possible reference match/i);
  assert.match(host.textContent ?? "", /not a uniqueness rule/i);
  assert.match(host.textContent ?? "", /Revision 1/);
});

test("revision write sends expected_revision and conflict preserves edits until confirmed reload", async () => {
  saved = detail();
  await click("Refresh saved customer invoices");
  await click("Open customer invoice 73:");
  await fill("Line description 1", "Local customer work");
  await click("Review customer-invoice proposal");
  handler = (call) => call.method === "PUT" ? { status: 409, data: { detail: "Stale revision." } } : null;
  await click("Save customer-invoice revision");
  const write = calls.find((call) => call.method === "PUT")!;
  assert.equal(write.path, "/lab/customer-invoices/73/");
  assert.equal(write.body.expected_revision, 1);
  handler = null;
  assert.equal((field("Line description 1") as HTMLTextAreaElement).value, "Local customer work");
  assert.match(host.textContent ?? "", /local inputs remain unchanged/i);
  window.confirm = () => true;
  await click("Reload saved customer invoice");
  assert.equal((field("Line description 1") as HTMLTextAreaElement).value, payload.lines[0].description);
});

test("400 response keeps customer inputs and does not mark save outcome uncertain", async () => {
  saved = detail();
  await click("Refresh saved customer invoices");
  await click("Open customer invoice 73:");
  await fill("Line description 1", "Inputs survive validation response");
  await click("Review customer-invoice proposal");
  handler = (call) => call.method === "PUT" ? { status: 400, data: { detail: "Invalid reference." } } : null;
  await click("Save customer-invoice revision");
  assert.equal((field("Line description 1") as HTMLTextAreaElement).value, "Inputs survive validation response");
  assert.match(host.textContent ?? "", /Invalid reference.*Inputs remain unchanged/i);
  assert.equal(button("Save customer-invoice revision").disabled, false);
  assert.doesNotMatch(host.textContent ?? "", /unknown save outcome/i);
});

test("unknown create outcome requires register inspection and explicit reopen; 403 clears protected inputs", async () => {
  await fillValid();
  await click("Review customer-invoice proposal");
  handler = (call) => call.method === "POST" ? { throw: new TypeError("connection ended") } : null;
  await click("Save customer-invoice proposal");
  assert.match(host.textContent ?? "", /database may have committed/i);
  assert.equal(button("Save customer-invoice proposal").disabled, true);
  assert.equal(button("New customer-invoice proposal").disabled, true);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
  handler = null;
  saved = detail();
  await click("Refresh saved customer invoices");
  window.confirm = () => true;
  await click("Open customer invoice 73:");
  assert.equal((field("Customer reference") as HTMLInputElement).value, payload.customer_reference);
  await fill("Line description 1", "Retained until forbidden response");
  await click("Review customer-invoice proposal");
  handler = (call) => call.method === "PUT" ? { status: 403, data: { detail: "Expired." } } : null;
  await click("Save customer-invoice revision");
  assert.equal(host.querySelector('[data-testid="customer-invoice-editor"]'), null);
  assert.match(host.textContent ?? "", /Protected customer-invoice inputs were cleared/i);
});
