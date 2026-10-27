import type { LabContext } from "./finance-draft-lab-api";
import type { InvoiceLinePayload } from "./finance-invoice";

export type CustomerInvoiceLineDraft = {
  description: string;
  amount: string;
  account_id: string;
  business_unit_id: string;
  dimension_value_ids: string[];
};
export type CustomerInvoiceDraft = {
  customer_reference: string;
  customer_details: string;
  invoice_reference: string;
  invoice_date: string;
  due_date: string;
  lines: CustomerInvoiceLineDraft[];
};
export type CustomerInvoicePayload = {
  customer_reference: string;
  customer_details: string;
  invoice_reference: string;
  invoice_date: string;
  due_date: string | null;
  currency: "QAR";
  lines: InvoiceLinePayload[];
};
export type CustomerInvoiceReview = {
  currency: "QAR";
  total: string;
  proposed: true;
  issued_customer_invoice: false;
  operational_receivable: false;
  approved: false;
  posted: false;
};
export type CustomerInvoiceChoiceContext = Pick<LabContext, "accounts" | "business_units" | "dimension_values">;

export const blankCustomerInvoiceLine = (): CustomerInvoiceLineDraft => ({
  description: "", amount: "", account_id: "", business_unit_id: "", dimension_value_ids: [],
});
export const blankCustomerInvoiceDraft = (): CustomerInvoiceDraft => ({
  customer_reference: "", customer_details: "", invoice_reference: "", invoice_date: "", due_date: "",
  lines: [blankCustomerInvoiceLine()],
});

export function validCustomerInvoiceDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000-")) return false;
  const [year, month, day] = value.split("-").map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  return month >= 1 && month <= 12 && day >= 1 &&
    day <= [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
}
const CUSTOMER_MONEY = /^(?:0|[1-9]\d{0,11})(?:\.\d{1,2})?$/;
export function validCustomerInvoiceMoney(value: string): boolean { return CUSTOMER_MONEY.test(value); }
function cents(value: string): bigint {
  const [whole, fraction = ""] = value.split(".");
  return BigInt(whole) * 100n + BigInt((fraction + "00").slice(0, 2));
}
export function totalCustomerInvoiceLines(lines: Pick<InvoiceLinePayload, "amount">[]): string {
  const total = lines.reduce((sum, line) => sum + cents(line.amount), 0n);
  return `${total / 100n}.${String(total % 100n).padStart(2, "0")}`;
}
function positiveId(value: string): number | null {
  if (!/^[1-9]\d*$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) ? id : null;
}
export function validateCustomerInvoice(
  draft: CustomerInvoiceDraft, context: CustomerInvoiceChoiceContext | null,
): string[] {
  const issues: string[] = [];
  if (!draft.customer_reference.trim()) issues.push("Enter a customer reference.");
  if (draft.customer_reference.length > 200) issues.push("Customer reference must be 200 characters or fewer.");
  if (draft.customer_details.length > 2000) issues.push("Customer details must be 2,000 characters or fewer.");
  if (!draft.invoice_reference.trim()) issues.push("Enter an invoice reference.");
  if (draft.invoice_reference.length > 200) issues.push("Invoice reference must be 200 characters or fewer.");
  if (!validCustomerInvoiceDate(draft.invoice_date)) issues.push("Enter an explicit valid invoice date.");
  if (draft.due_date && !validCustomerInvoiceDate(draft.due_date)) issues.push("Enter a valid due date or leave it blank.");
  if (draft.lines.length < 1) issues.push("Add at least one invoice line.");
  if (draft.lines.length > 200) issues.push("An invoice may contain no more than 200 lines.");
  draft.lines.forEach((line, index) => {
    const n = index + 1;
    if (!line.description.trim()) issues.push(`Line ${n}: enter a description.`);
    if (line.description.length > 2000) issues.push(`Line ${n}: description must be 2,000 characters or fewer.`);
    if (!validCustomerInvoiceMoney(line.amount)) issues.push(`Line ${n}: enter a non-negative exact QAR amount up to 999999999999.99 with at most two decimal places.`);
    const account = positiveId(line.account_id);
    if (account === null || !context?.accounts.some((item) => item.id === account)) issues.push(`Line ${n}: choose an available saved account.`);
    if (line.business_unit_id) {
      const unit = positiveId(line.business_unit_id);
      if (unit === null || !context?.business_units.some((item) => item.id === unit)) issues.push(`Line ${n}: choose an available business unit or leave it not proposed.`);
    }
    const ids = line.dimension_value_ids.map(positiveId);
    if (ids.some((id) => id === null) || new Set(ids).size !== ids.length) issues.push(`Line ${n}: dimension values must be distinct saved IDs.`);
    if (ids.some((id) => !context?.dimension_values.some((item) => item.id === id))) issues.push(`Line ${n}: one or more saved dimension values are unavailable.`);
  });
  return issues;
}
export function payloadFromCustomerInvoice(
  draft: CustomerInvoiceDraft, context: CustomerInvoiceChoiceContext,
): CustomerInvoicePayload {
  const issues = validateCustomerInvoice(draft, context);
  if (issues.length) throw new Error(issues.join(" "));
  return {
    customer_reference: draft.customer_reference,
    customer_details: draft.customer_details,
    invoice_reference: draft.invoice_reference,
    invoice_date: draft.invoice_date,
    due_date: draft.due_date || null,
    currency: "QAR",
    lines: draft.lines.map((line) => ({
      description: line.description, amount: line.amount, account_id: positiveId(line.account_id)!,
      business_unit_id: line.business_unit_id ? positiveId(line.business_unit_id) : null,
      dimension_value_ids: line.dimension_value_ids.map((id) => positiveId(id)!),
    })),
  };
}
export function calculateCustomerInvoiceReview(payload: CustomerInvoicePayload): CustomerInvoiceReview {
  return {
    currency: "QAR", total: totalCustomerInvoiceLines(payload.lines), proposed: true,
    issued_customer_invoice: false, operational_receivable: false, approved: false, posted: false,
  };
}

