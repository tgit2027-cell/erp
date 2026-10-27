export type ExpenseProposalType = "expense" | "petty_cash";
export type ExpenseEntryDraft = {
  expenseDate: string;
  partyReference: string;
  description: string;
  amount: string;
  accountId: string;
  businessUnitId: string;
  dimensionIds: string[];
  receiptReference: string;
};
export type ExpenseDraft = {
  name: string;
  proposalType: ExpenseProposalType | "";
  explanation: string;
  entries: ExpenseEntryDraft[];
};
export type ExpenseEntryInput = {
  expense_date: string;
  party_reference: string;
  description: string;
  amount: string;
  account_id: number;
  business_unit_id: number | null;
  dimension_value_ids: number[];
  receipt_reference: string;
};
export type ExpensePayload = {
  name: string;
  proposal_type: ExpenseProposalType;
  currency: "QAR";
  explanation: string;
  entries: ExpenseEntryInput[];
};
export type ExpenseChoiceContext = {
  accounts: { id: number }[];
  business_units: { id: number }[];
  dimension_values: { id: number; dimension_id: number }[];
};

export const emptyExpenseEntry = (): ExpenseEntryDraft => ({
  expenseDate: "", partyReference: "", description: "", amount: "", accountId: "",
  businessUnitId: "", dimensionIds: [], receiptReference: "",
});
export const blankExpenseDraft = (): ExpenseDraft => ({
  name: "", proposalType: "", explanation: "", entries: [emptyExpenseEntry()],
});
export function expenseCents(value: string): bigint | null {
  if (!/^(?:0|[1-9]\d{0,11})(?:\.\d{1,2})?$/.test(value)) return null;
  const [whole, fraction = ""] = value.split(".");
  return BigInt(whole) * 100n + BigInt((fraction + "00").slice(0, 2));
}
export function expenseTotal(entries: ExpenseEntryDraft[]): string | null {
  const amounts = entries.map((entry) => expenseCents(entry.amount));
  if (amounts.some((amount) => amount === null)) return null;
  const total = amounts.reduce<bigint>((sum, amount) => sum + (amount ?? 0n), 0n);
  return `${total / 100n}.${String(total % 100n).padStart(2, "0")}`;
}
export function expenseValidDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000-")) return false;
  const [year, month, day] = value.split("-").map(Number);
  if (month < 1 || month > 12 || day < 1) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day <= days[month - 1];
}
export function validateExpense(draft: ExpenseDraft, context: ExpenseChoiceContext | null): string[] {
  const errors: string[] = [];
  if (!draft.name.trim()) errors.push("Enter a proposal name.");
  if (draft.name.length > 200) errors.push("Proposal name must be 200 characters or fewer.");
  if (draft.proposalType !== "expense" && draft.proposalType !== "petty_cash") errors.push("Choose a proposal type.");
  if (draft.explanation.length > 2000) errors.push("Proposal explanation must be 2,000 characters or fewer.");
  if (draft.entries.length < 1 || draft.entries.length > 100) errors.push("A proposal must contain 1 to 100 explicit rows.");
  draft.entries.forEach((entry, index) => {
    const row = index + 1;
    if (!expenseValidDate(entry.expenseDate)) errors.push(`Row ${row}: enter an explicit valid expense date.`);
    if (!entry.partyReference.trim()) errors.push(`Row ${row}: enter a claimant/custodian reference.`);
    if (entry.partyReference.length > 200) errors.push(`Row ${row}: claimant/custodian reference must be 200 characters or fewer.`);
    if (!entry.description.trim()) errors.push(`Row ${row}: enter an expense description.`);
    if (entry.description.length > 2000) errors.push(`Row ${row}: description must be 2,000 characters or fewer.`);
    if (expenseCents(entry.amount) === null) errors.push(`Row ${row}: enter a non-negative amount up to 999999999999.99 with at most two decimal places.`);
    const account = Number(entry.accountId);
    if (!entry.accountId || !context?.accounts.some((item) => item.id === account)) errors.push(`Row ${row}: choose an available saved account.`);
    const unit = Number(entry.businessUnitId);
    if (entry.businessUnitId && !context?.business_units.some((item) => item.id === unit)) errors.push(`Row ${row}: choose an available business unit or leave it not proposed.`);
    const ids = entry.dimensionIds.map(Number);
    if (new Set(ids).size !== ids.length) errors.push(`Row ${row}: dimensions must be distinct.`);
    const selected = ids.map((id) => context?.dimension_values.find((item) => item.id === id));
    if (selected.some((item) => !item)) errors.push(`Row ${row}: one or more dimensions are unavailable.`);
    if (entry.receiptReference.length > 200) errors.push(`Row ${row}: synthetic receipt reference must be 200 characters or fewer.`);
  });
  return errors;
}
export function payloadFromExpense(draft: ExpenseDraft, context: ExpenseChoiceContext): ExpensePayload {
  if (draft.proposalType !== "expense" && draft.proposalType !== "petty_cash") throw new Error("Choose a proposal type.");
  const entries = draft.entries.map((entry, index) => {
    const accountId = Number(entry.accountId);
    const businessUnitId = entry.businessUnitId ? Number(entry.businessUnitId) : null;
    const dimensionIds = entry.dimensionIds.map(Number);
    if (!context.accounts.some((choice) => choice.id === accountId) ||
      (businessUnitId !== null && !context.business_units.some((choice) => choice.id === businessUnitId)) ||
      dimensionIds.some((id) => !context.dimension_values.some((choice) => choice.id === id))) {
      throw new Error(`Row ${index + 1} contains a saved choice unavailable in the refreshed context. Original choice remains visible; correct it before saving.`);
    }
    return {
      expense_date: entry.expenseDate, party_reference: entry.partyReference, description: entry.description,
      amount: entry.amount, account_id: accountId, business_unit_id: businessUnitId,
      dimension_value_ids: [...dimensionIds], receipt_reference: entry.receiptReference,
    };
  });
  return {
    name: draft.name, proposal_type: draft.proposalType, currency: "QAR",
    explanation: draft.explanation, entries,
  };
}
