import type { LabContext } from "./finance-draft-lab-api";
import type { InvoiceLinePayload } from "./finance-invoice";
import { totalInvoiceLines, validInvoiceDate, validInvoiceMoney } from "./finance-invoice";

export type CustomerCreditLineDraft = {
  description: string; amount: string; account_id: string; business_unit_id: string; dimension_value_ids: string[];
};
export type CustomerCreditDraft = {
  customer_reference: string; document_reference: string; proposal_date: string; reason: string;
  linked_invoice_id: string; lines: CustomerCreditLineDraft[];
};
export type CustomerCreditPayload = {
  customer_reference: string; document_reference: string; proposal_date: string; reason: string;
  linked_invoice_id: number | null; currency: "QAR"; lines: InvoiceLinePayload[];
};
export type CustomerCreditReview = {
  currency: "QAR"; total: string; amount_basis: "explicit_non_negative_magnitudes"; interpretation: string;
  proposed: true; issued_credit_note: false; refund: false; settlement: false;
  operational_receivable_adjustment: false; approved: false; posted: false;
};
export type CustomerCreditContext = Pick<LabContext, "accounts" | "business_units" | "dimension_values">;
export type CustomerInvoiceChoice = {
  id: number; revision: number; customer_reference: string; invoice_reference: string;
};

export const blankCustomerCreditLine = (): CustomerCreditLineDraft => ({
  description: "", amount: "", account_id: "", business_unit_id: "", dimension_value_ids: [],
});
export const blankCustomerCreditDraft = (): CustomerCreditDraft => ({
  customer_reference: "", document_reference: "", proposal_date: "", reason: "",
  linked_invoice_id: "", lines: [blankCustomerCreditLine()],
});
function positiveId(value: string): number | null {
  if (!/^[1-9]\d*$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}
const trimAsciiWhitespace = (value: string) => value.replace(/^[\t\n\v\f\r ]+|[\t\n\v\f\r ]+$/g, "");

export function validateCustomerCredit(
  draft: CustomerCreditDraft, context: CustomerCreditContext | null, invoices: CustomerInvoiceChoice[],
): string[] {
  const errors: string[] = [];
  if (!draft.customer_reference.trim()) errors.push("Enter a customer reference.");
  if (draft.customer_reference.length > 200) errors.push("Customer reference must be 200 characters or fewer.");
  if (!draft.document_reference.trim()) errors.push("Enter a document reference.");
  if (draft.document_reference.length > 200) errors.push("Document reference must be 200 characters or fewer.");
  if (!validInvoiceDate(draft.proposal_date)) errors.push("Enter an explicit valid proposal date.");
  if (!draft.reason.trim()) errors.push("Enter a reason for the proposal.");
  if (draft.reason.length > 2000) errors.push("Reason must be 2,000 characters or fewer.");
  if (draft.linked_invoice_id) {
    const id = positiveId(draft.linked_invoice_id);
    const invoice = id === null ? undefined : invoices.find((item) => item.id === id);
    if (!invoice) errors.push("Choose an available saved customer invoice or leave the link not proposed.");
    else if (trimAsciiWhitespace(invoice.customer_reference) !== trimAsciiWhitespace(draft.customer_reference)) {
      errors.push("The linked invoice customer reference must exactly match apart from surrounding ASCII whitespace.");
    }
  }
  if (draft.lines.length < 1) errors.push("Add at least one proposal line.");
  if (draft.lines.length > 200) errors.push("A proposal may contain no more than 200 lines.");
  draft.lines.forEach((line, index) => {
    const n = index + 1;
    if (!line.description.trim()) errors.push(`Line ${n}: enter a description.`);
    if (line.description.length > 2000) errors.push(`Line ${n}: description must be 2,000 characters or fewer.`);
    if (!validInvoiceMoney(line.amount)) errors.push(`Line ${n}: enter a non-negative exact QAR magnitude up to 999999999999.99 with at most two decimal places.`);
    const accountId = positiveId(line.account_id);
    if (accountId === null || !context?.accounts.some((item) => item.id === accountId)) errors.push(`Line ${n}: choose an available saved account.`);
    if (line.business_unit_id) {
      const id = positiveId(line.business_unit_id);
      if (id === null || !context?.business_units.some((item) => item.id === id)) errors.push(`Line ${n}: choose an available business unit or leave it not proposed.`);
    }
    const dimensions = line.dimension_value_ids.map(positiveId);
    if (dimensions.some((id) => id === null) || new Set(dimensions).size !== dimensions.length) errors.push(`Line ${n}: dimension values must be distinct saved IDs.`);
    if (dimensions.some((id) => !context?.dimension_values.some((item) => item.id === id))) errors.push(`Line ${n}: one or more saved dimension values are unavailable.`);
  });
  return errors;
}
export function payloadFromCustomerCredit(
  draft: CustomerCreditDraft, context: CustomerCreditContext, invoices: CustomerInvoiceChoice[],
): CustomerCreditPayload {
  const errors = validateCustomerCredit(draft, context, invoices);
  if (errors.length) throw new Error(errors.join(" "));
  return {
    customer_reference: draft.customer_reference, document_reference: draft.document_reference,
    proposal_date: draft.proposal_date, reason: draft.reason,
    linked_invoice_id: draft.linked_invoice_id ? positiveId(draft.linked_invoice_id) : null,
    currency: "QAR",
    lines: draft.lines.map((line) => ({
      description: line.description, amount: line.amount, account_id: positiveId(line.account_id)!,
      business_unit_id: line.business_unit_id ? positiveId(line.business_unit_id) : null,
      dimension_value_ids: line.dimension_value_ids.map((id) => positiveId(id)!),
    })),
  };
}
export function calculateCustomerCreditTotal(lines: Pick<InvoiceLinePayload, "amount">[]) {
  return totalInvoiceLines(lines);
}
export function calculateCustomerCreditReview(payload: CustomerCreditPayload): CustomerCreditReview {
  return {
    currency: "QAR", total: calculateCustomerCreditTotal(payload.lines),
    amount_basis: "explicit_non_negative_magnitudes",
    interpretation: "Explicit non-negative magnitudes; this proposal does not imply a receivable or refund effect.",
    proposed: true, issued_credit_note: false, refund: false, settlement: false,
    operational_receivable_adjustment: false, approved: false, posted: false,
  };
}
