import type { LabContext } from "./finance-draft-lab-api";
import { validInvoiceDate, validInvoiceMoney } from "./finance-invoice";

export type SupplierAdvanceDraft = {
  supplier_reference: string;
  document_reference: string;
  proposed_date: string;
  amount: string;
  purpose: string;
  account_id: string;
  business_unit_id: string;
  dimension_value_ids: string[];
};
export type SupplierAdvancePayload = {
  supplier_reference: string;
  document_reference: string;
  proposed_date: string;
  amount: string;
  purpose: string;
  account_id: number | null;
  business_unit_id: number | null;
  dimension_value_ids: number[];
  currency: "QAR";
};
export type SupplierAdvanceReview = {
  currency: "QAR";
  total: string;
  proposed: true;
  unpaid: true;
  unapplied: true;
  actual_payment: false;
  invoice_application: false;
  operational_payable_change: false;
  approved: false;
  posted: false;
};
export type SupplierAdvanceContext = Pick<LabContext, "accounts" | "business_units" | "dimension_values">;

export const blankSupplierAdvanceDraft = (): SupplierAdvanceDraft => ({
  supplier_reference: "", document_reference: "", proposed_date: "", amount: "",
  purpose: "", account_id: "", business_unit_id: "", dimension_value_ids: [],
});

function positiveId(value: string): number | null {
  if (!/^[1-9]\d*$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) ? id : null;
}

export function validateSupplierAdvance(
  draft: SupplierAdvanceDraft,
  context: SupplierAdvanceContext | null,
): string[] {
  const errors: string[] = [];
  if (!draft.supplier_reference.trim()) errors.push("Enter a supplier reference.");
  if (draft.supplier_reference.length > 200) errors.push("Supplier reference must be 200 characters or fewer.");
  if (!draft.document_reference.trim()) errors.push("Enter a document reference.");
  if (draft.document_reference.length > 200) errors.push("Document reference must be 200 characters or fewer.");
  if (!validInvoiceDate(draft.proposed_date)) errors.push("Enter an explicit valid proposed date.");
  if (!validInvoiceMoney(draft.amount)) errors.push("Enter a non-negative exact QAR amount up to 999999999999.99 with at most two decimal places.");
  if (!draft.purpose.trim()) errors.push("Enter a purpose for the proposal.");
  if (draft.purpose.length > 2000) errors.push("Purpose must be 2,000 characters or fewer.");
  if (draft.account_id) {
    const id = positiveId(draft.account_id);
    if (id === null || !context?.accounts.some((item) => item.id === id)) {
      errors.push("Choose an available saved account or leave it not proposed.");
    }
  }
  if (draft.business_unit_id) {
    const id = positiveId(draft.business_unit_id);
    if (id === null || !context?.business_units.some((item) => item.id === id)) {
      errors.push("Choose an available saved business unit or leave it not proposed.");
    }
  }
  const dimensions = draft.dimension_value_ids.map(positiveId);
  if (dimensions.some((id) => id === null) || new Set(dimensions).size !== dimensions.length) {
    errors.push("Dimension values must be distinct positive saved IDs.");
  }
  if (dimensions.some((id) => !context?.dimension_values.some((item) => item.id === id))) {
    errors.push("One or more saved dimension values are unavailable.");
  }
  return errors;
}

export function payloadFromSupplierAdvance(
  draft: SupplierAdvanceDraft,
  context: SupplierAdvanceContext | null,
): SupplierAdvancePayload {
  const errors = validateSupplierAdvance(draft, context);
  if (errors.length) throw new Error(errors.join(" "));
  return {
    supplier_reference: draft.supplier_reference,
    document_reference: draft.document_reference,
    proposed_date: draft.proposed_date,
    amount: draft.amount,
    purpose: draft.purpose,
    account_id: draft.account_id ? positiveId(draft.account_id) : null,
    business_unit_id: draft.business_unit_id ? positiveId(draft.business_unit_id) : null,
    dimension_value_ids: draft.dimension_value_ids.map((id) => positiveId(id)!),
    currency: "QAR",
  };
}

export function calculateSupplierAdvanceReview(payload: SupplierAdvancePayload): SupplierAdvanceReview {
  const [whole = "0", fraction = ""] = payload.amount.split(".");
  const total = `${whole}.${fraction.padEnd(2, "0")}`;
  return {
    currency: "QAR", total, proposed: true, unpaid: true, unapplied: true,
    actual_payment: false, invoice_application: false, operational_payable_change: false,
    approved: false, posted: false,
  };
}
