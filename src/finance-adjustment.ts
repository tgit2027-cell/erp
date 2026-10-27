import type { InvoiceLinePayload } from "./finance-invoice";
import { validInvoiceDate, validInvoiceMoney } from "./finance-invoice";

export type ProposalType = "credit" | "debit";
export type AdjustmentLineDraft = {
  description: string;
  amount: string;
  account_id: string;
  business_unit_id: string;
  dimension_value_ids: string[];
};
export type AdjustmentDraft = {
  proposal_type: ProposalType | "";
  supplier_reference: string;
  document_reference: string;
  proposal_date: string;
  reason: string;
  linked_invoice_id: string;
  lines: AdjustmentLineDraft[];
};
export type AdjustmentPayload = {
  proposal_type: ProposalType;
  supplier_reference: string;
  document_reference: string;
  proposal_date: string;
  reason: string;
  linked_invoice_id: number | null;
  currency: "QAR";
  lines: InvoiceLinePayload[];
};
export type AdjustmentReview = {
  currency: "QAR";
  total: string;
  interpretation: string;
  amount_basis: "explicit_non_negative_magnitudes";
  proposed: true;
  issued_credit_note: false;
  settlement: false;
  operational_payable_adjustment: false;
  approved: false;
  posted: false;
};
export type AdjustmentContext = {
  accounts: { id: number }[];
  business_units: { id: number }[];
  dimension_values: { id: number }[];
};

export const blankAdjustmentLine = (): AdjustmentLineDraft => ({
  description: "", amount: "", account_id: "", business_unit_id: "", dimension_value_ids: [],
});
export const blankAdjustmentDraft = (): AdjustmentDraft => ({
  proposal_type: "", supplier_reference: "", document_reference: "", proposal_date: "",
  reason: "", linked_invoice_id: "", lines: [blankAdjustmentLine()],
});

function positiveId(value: string): number | null {
  if (!/^[1-9]\d*$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}
function amountCents(value: string): bigint {
  const [whole, fraction = ""] = value.split(".");
  return BigInt(whole) * 100n + BigInt((fraction + "00").slice(0, 2));
}
export function totalAdjustmentLines(lines: { amount: string }[]): string {
  const cents = lines.reduce((sum, line) => sum + amountCents(line.amount), 0n);
  return `${cents / 100n}.${String(cents % 100n).padStart(2, "0")}`;
}
export function validateAdjustment(
  draft: AdjustmentDraft,
  context: AdjustmentContext | null,
  invoices: { id: number; supplier_reference: string }[],
): string[] {
  const errors: string[] = [];
  if (draft.proposal_type !== "credit" && draft.proposal_type !== "debit") errors.push("Choose credit or debit classification.");
  if (!draft.supplier_reference.trim()) errors.push("Enter a supplier reference.");
  if (draft.supplier_reference.length > 200) errors.push("Supplier reference must be 200 characters or fewer.");
  if (!draft.document_reference.trim()) errors.push("Enter a document reference.");
  if (draft.document_reference.length > 200) errors.push("Document reference must be 200 characters or fewer.");
  if (!validInvoiceDate(draft.proposal_date)) errors.push("Enter an explicit valid proposal date.");
  if (!draft.reason.trim()) errors.push("Enter a reason for the proposal.");
  if (draft.reason.length > 2000) errors.push("Reason must be 2,000 characters or fewer.");
  if (draft.linked_invoice_id) {
    const id = positiveId(draft.linked_invoice_id);
    const invoice = id === null ? undefined : invoices.find((item) => item.id === id);
    if (!invoice) errors.push("Choose an available saved invoice or leave the link not proposed.");
    else if (invoice.supplier_reference.replace(/^[ \t\n\r\v\f]+|[ \t\n\r\v\f]+$/g, "") !==
      draft.supplier_reference.replace(/^[ \t\n\r\v\f]+|[ \t\n\r\v\f]+$/g, "")) {
      errors.push("The linked invoice supplier reference must exactly match apart from surrounding ASCII whitespace.");
    }
  }
  if (draft.lines.length < 1) errors.push("Add at least one proposal line.");
  if (draft.lines.length > 200) errors.push("A proposal may contain no more than 200 lines.");
  draft.lines.forEach((line, index) => {
    const number = index + 1;
    if (!line.description.trim()) errors.push(`Line ${number}: enter a description.`);
    if (line.description.length > 2000) errors.push(`Line ${number}: description must be 2,000 characters or fewer.`);
    if (!validInvoiceMoney(line.amount)) errors.push(`Line ${number}: enter a non-negative exact QAR magnitude with at most two decimal places.`);
    const accountId = positiveId(line.account_id);
    if (accountId === null || !context?.accounts.some((item) => item.id === accountId)) errors.push(`Line ${number}: choose an available saved account.`);
    if (line.business_unit_id) {
      const id = positiveId(line.business_unit_id);
      if (id === null || !context?.business_units.some((item) => item.id === id)) errors.push(`Line ${number}: choose an available business unit or leave it not proposed.`);
    }
    const dimensionIds = line.dimension_value_ids.map(positiveId);
    if (dimensionIds.some((id) => id === null) || new Set(dimensionIds).size !== dimensionIds.length) errors.push(`Line ${number}: dimension values must be distinct saved IDs.`);
    if (dimensionIds.some((id) => !context?.dimension_values.some((item) => item.id === id))) errors.push(`Line ${number}: one or more saved dimension values are unavailable.`);
  });
  return errors;
}
export function payloadFromAdjustment(
  draft: AdjustmentDraft,
  context: AdjustmentContext,
  invoices: { id: number; supplier_reference: string }[],
): AdjustmentPayload {
  const errors = validateAdjustment(draft, context, invoices);
  if (errors.length) throw new Error(errors.join(" "));
  return {
    proposal_type: draft.proposal_type as ProposalType,
    supplier_reference: draft.supplier_reference,
    document_reference: draft.document_reference,
    proposal_date: draft.proposal_date,
    reason: draft.reason,
    linked_invoice_id: draft.linked_invoice_id ? positiveId(draft.linked_invoice_id) : null,
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
export function calculateAdjustmentTotal(lines: { amount: string }[]) {
  return totalAdjustmentLines(lines);
}
