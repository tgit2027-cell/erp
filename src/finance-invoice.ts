import type { LabContext } from "./finance-draft-lab-api";

export type InvoiceLineDraft = {
  description: string;
  amount: string;
  account_id: string;
  business_unit_id: string;
  dimension_value_ids: string[];
};
export type InvoiceDraft = {
  supplier_reference: string;
  invoice_reference: string;
  invoice_date: string;
  due_date: string;
  lines: InvoiceLineDraft[];
};
export type InvoiceLinePayload = {
  description: string;
  amount: string;
  account_id: number;
  business_unit_id: number | null;
  dimension_value_ids: number[];
};
export type InvoicePayload = {
  supplier_reference: string;
  invoice_reference: string;
  invoice_date: string;
  due_date: string | null;
  currency: "QAR";
  lines: InvoiceLinePayload[];
};
export type InvoiceReview = {
  currency: "QAR";
  total: string;
  proposed: true;
  operational_payable: false;
  approved: false;
  posted: false;
};
export type InvoiceChoiceContext = Pick<LabContext, "accounts" | "business_units" | "dimension_values">;

export const blankInvoiceLine = (): InvoiceLineDraft => ({
  description: "", amount: "", account_id: "", business_unit_id: "", dimension_value_ids: [],
});
export const blankInvoiceDraft = (): InvoiceDraft => ({
  supplier_reference: "", invoice_reference: "", invoice_date: "", due_date: "", lines: [blankInvoiceLine()],
});

export function validInvoiceDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000-")) return false;
  const [year, month, day] = value.split("-").map(Number);
  if (month < 1 || month > 12 || day < 1) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  return day <= [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
}

const INVOICE_MONEY = /^(?:0|[1-9]\d{0,11})(?:\.\d{1,2})?$/;
export function validInvoiceMoney(value: string): boolean {
  return INVOICE_MONEY.test(value);
}
function toCents(value: string): bigint {
  const [whole, fraction = ""] = value.split(".");
  return BigInt(whole) * 100n + BigInt((fraction + "00").slice(0, 2));
}
export function invoiceMoneyFromCents(value: bigint): string {
  return `${value / 100n}.${String(value % 100n).padStart(2, "0")}`;
}
export function totalInvoiceLines(lines: Pick<InvoiceLinePayload, "amount">[]): string {
  return invoiceMoneyFromCents(lines.reduce((total, line) => total + toCents(line.amount), 0n));
}

function positiveId(value: string): number | null {
  if (!/^[1-9]\d*$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}
export function validateInvoice(draft: InvoiceDraft, context: InvoiceChoiceContext | null): string[] {
  const errors: string[] = [];
  if (!draft.supplier_reference.trim()) errors.push("Enter a supplier reference.");
  if (draft.supplier_reference.length > 200) errors.push("Supplier reference must be 200 characters or fewer.");
  if (!draft.invoice_reference.trim()) errors.push("Enter an invoice reference.");
  if (draft.invoice_reference.length > 200) errors.push("Invoice reference must be 200 characters or fewer.");
  if (!validInvoiceDate(draft.invoice_date)) errors.push("Enter an explicit valid invoice date.");
  if (draft.due_date && !validInvoiceDate(draft.due_date)) errors.push("Enter a valid due date or leave it blank.");
  if (draft.lines.length < 1) errors.push("Add at least one invoice line.");
  if (draft.lines.length > 200) errors.push("An invoice may contain no more than 200 lines.");
  draft.lines.forEach((line, index) => {
    const number = index + 1;
    if (!line.description.trim()) errors.push(`Line ${number}: enter a description.`);
    if (line.description.length > 2000) errors.push(`Line ${number}: description must be 2,000 characters or fewer.`);
    if (!validInvoiceMoney(line.amount)) errors.push(`Line ${number}: enter a non-negative exact QAR amount up to 999999999999.99 with at most two decimal places.`);
    const account = positiveId(line.account_id);
    if (account === null || !context?.accounts.some((item) => item.id === account)) errors.push(`Line ${number}: choose an available saved account.`);
    if (line.business_unit_id) {
      const unit = positiveId(line.business_unit_id);
      if (unit === null || !context?.business_units.some((item) => item.id === unit)) errors.push(`Line ${number}: choose an available business unit or leave it not proposed.`);
    }
    const ids = line.dimension_value_ids.map(positiveId);
    if (ids.some((id) => id === null) || new Set(ids).size !== ids.length) errors.push(`Line ${number}: dimension values must be distinct saved IDs.`);
    if (ids.some((id) => !context?.dimension_values.some((item) => item.id === id))) errors.push(`Line ${number}: one or more saved dimension values are unavailable.`);
  });
  return errors;
}
export function payloadFromInvoice(draft: InvoiceDraft, context: InvoiceChoiceContext): InvoicePayload {
  const errors = validateInvoice(draft, context);
  if (errors.length) throw new Error(errors.join(" "));
  return {
    supplier_reference: draft.supplier_reference,
    invoice_reference: draft.invoice_reference,
    invoice_date: draft.invoice_date,
    due_date: draft.due_date || null,
    currency: "QAR",
    lines: draft.lines.map((line) => ({
      description: line.description,
      amount: line.amount,
      account_id: positiveId(line.account_id)!,
      business_unit_id: line.business_unit_id ? positiveId(line.business_unit_id) : null,
      dimension_value_ids: line.dimension_value_ids.map((id) => positiveId(id)!),
    })),
  };
}
export function calculateInvoiceReview(payload: InvoicePayload): InvoiceReview {
  return {
    currency: "QAR",
    total: totalInvoiceLines(payload.lines),
    proposed: true,
    operational_payable: false,
    approved: false,
    posted: false,
  };
}
