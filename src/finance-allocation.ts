export type AllocationDestination = {
  amount: string;
  account_id: number;
  business_unit_id: number | null;
  dimension_value_ids: number[];
};

export type AllocationProposalInput = {
  source_amount: string;
  currency: string;
  date: string;
  explanation: string;
  destinations: AllocationDestination[];
};

export type AllocationJournalLine = {
  account_id: number;
  business_unit_id: number | null;
  dimension_value_ids: number[];
  debit: string;
  credit: string;
  memo_en?: string;
  memo_ar?: string;
};

export type AllocationIssue = { code: string; en: string };
export type AllocationReview = {
  source_amount: string;
  allocated_total: string;
  difference: string;
  state: "remaining" | "fully_allocated";
  currency: "QAR";
  journal: {
    debit_total: string;
    credit_total: string;
    difference: string;
    balanced: boolean;
    posted: false;
    issues: AllocationIssue[];
  };
};

export type AllocationSnapshot = {
  id: number;
  title_en: string;
  title_ar?: string;
  revision: number;
  allocation_proposal: AllocationProposalInput;
  lines: AllocationJournalLine[];
  review: AllocationReview;
};

export type SavedAllocation = AllocationSnapshot & {
  history: {
    revision: number;
    created_at: string;
    snapshot: AllocationSnapshot;
  }[];
};

export type AllocationList = {
  proposals: { id: number; title_en: string; revision: number }[];
};

export type AllocationLineDraft = {
  accountId: string;
  businessUnitId: string;
  dimensionIds: string[];
  debit: string;
  credit: string;
  memo: string;
  memoAr: string;
};

export type DestinationDraft = {
  amount: string;
  accountId: string;
  businessUnitId: string;
  dimensionIds: string[];
};

export const emptyAllocationLine = (): AllocationLineDraft => ({
  accountId: "", businessUnitId: "", dimensionIds: [], debit: "", credit: "", memo: "", memoAr: "",
});

export const emptyDestination = (): DestinationDraft => ({
  amount: "", accountId: "", businessUnitId: "", dimensionIds: [],
});

/** Parse non-negative decimal money into exact minor units. */
export function cents(value: string): bigint | null {
  const normalized = value.trim();
  if (!/^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(normalized)) return null;
  const [whole, fraction = ""] = normalized.split(".");
  const result = BigInt(whole) * 100n + BigInt((fraction + "00").slice(0, 2));
  return result <= 99999999999999n ? result : null;
}

export function moneyText(value: bigint): string {
  const sign = value < 0n ? "-" : "";
  const positive = value < 0n ? -value : value;
  return `${sign}${positive / 100n}.${String(positive % 100n).padStart(2, "0")}`;
}

export function validateProposal(input: {
  title: string;
  sourceAmount: string;
  currency: string;
  date: string;
  explanation: string;
  destinations: DestinationDraft[];
}): string[] {
  const errors: string[] = [];
  if (!input.title.trim()) errors.push("Enter an English allocation title.");
  const source = cents(input.sourceAmount);
  if (source === null || source <= 0n) errors.push("Enter a positive source amount up to 999999999999.99 with at most two decimal places.");
  if (!input.currency) errors.push("Choose an explicit allocation currency.");
  else if (input.currency !== "QAR") errors.push("Only QAR is supported; no foreign-exchange conversion is inferred.");
  if (!validDate(input.date)) errors.push("Enter a valid proposed date.");
  if (!input.explanation.trim()) errors.push("Enter the required explicit explanation.");
  if (input.explanation.length > 2000) errors.push("The explanation must be 2,000 characters or fewer.");
  if (!input.destinations.length) errors.push("Add at least one explicit destination.");
  let total = 0n;
  input.destinations.forEach((destination, index) => {
    const amount = cents(destination.amount);
    if (amount === null || amount <= 0n) {
      errors.push(`Destination ${index + 1} amount must be positive and no greater than 999999999999.99.`);
    } else total += amount;
    if (!destination.accountId) errors.push(`Choose a saved account for destination ${index + 1}.`);
  });
  if (source !== null && total > source) errors.push("Destination total cannot exceed the source amount.");
  return errors;
}

export function validateJournal(lines: AllocationLineDraft[], validAccountIds: number[]) {
  const issues: AllocationIssue[] = [];
  if (lines.length < 2) issues.push({ code: "at_least_two_lines", en: "Add at least two journal lines before review." });
  let debits = 0n;
  let credits = 0n;
  lines.forEach((line, index) => {
    const debit = cents(line.debit);
    const credit = cents(line.credit);
    if (!line.accountId || !validAccountIds.includes(Number(line.accountId))) {
      issues.push({ code: `line_${index + 1}_account_required`, en: `Choose a saved account for journal line ${index + 1}.` });
    }
    if (debit === null || credit === null) {
      issues.push({ code: `line_${index + 1}_amount_invalid`, en: `Journal line ${index + 1} needs non-negative amounts with at most two decimal places.` });
      return;
    }
    if ((debit > 0n) === (credit > 0n)) {
      issues.push({ code: `line_${index + 1}_one_side_only`, en: `Journal line ${index + 1} must contain a positive debit or credit, not both.` });
    }
    debits += debit;
    credits += credit;
  });
  if (debits !== credits) issues.push({ code: "totals_not_balanced", en: "Journal debit and credit totals must balance before save." });
  return {
    issues, debitTotal: moneyText(debits), creditTotal: moneyText(credits),
    difference: moneyText(debits - credits), balanced: issues.length === 0,
  };
}

export function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.valueOf()) && date.toISOString().slice(0, 10) === value;
}

export function allocationTotals(sourceAmount: string, destinations: DestinationDraft[]) {
  const source = cents(sourceAmount);
  const destinationCents = destinations.map((row) => cents(row.amount));
  if (source === null || destinationCents.some((amount) => amount === null)) return null;
  const allocated = destinationCents.reduce<bigint>((sum, value) => sum + (value ?? 0n), 0n);
  return { source: moneyText(source), allocated: moneyText(allocated), difference: moneyText(source - allocated) };
}
