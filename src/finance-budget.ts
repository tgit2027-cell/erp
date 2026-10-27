import { cents, moneyText, validDate } from "./finance-allocation";

export type BudgetEntryDraft = {
  startDate: string;
  endDate: string;
  amount: string;
  accountId: string;
  businessUnitId: string;
  dimensionIds: string[];
  explanation: string;
};
export type BudgetScenarioDraft = { name: string; startDate: string; endDate: string; entries: BudgetEntryDraft[] };
export type BudgetEntryInput = {
  start_date: string; end_date: string; amount: string; account_id: number;
  business_unit_id: number | null; dimension_value_ids: number[]; explanation: string;
};
export type BudgetPayload = { name: string; start_date: string; end_date: string; currency: "QAR"; entries: BudgetEntryInput[] };

export const emptyBudgetEntry = (): BudgetEntryDraft => ({
  startDate: "", endDate: "", amount: "", accountId: "", businessUnitId: "", dimensionIds: [], explanation: "",
});
export const blankBudgetScenario = (): BudgetScenarioDraft => ({
  name: "", startDate: "", endDate: "", entries: [emptyBudgetEntry()],
});

/** Python's date implementation has no year zero; keep this worksheet aligned. */
export function budgetValidDate(value: string): boolean {
  return !/^0000-/.test(value) && validDate(value);
}

export function budgetTotal(entries: BudgetEntryDraft[]): string | null {
  const parsed = entries.map((entry) => cents(entry.amount));
  if (parsed.some((amount) => amount === null)) return null;
  return moneyText(parsed.reduce<bigint>((sum, amount) => sum + (amount ?? 0n), 0n));
}

export function budgetWarnings(entries: BudgetEntryDraft[]) {
  const warnings: { code: "duplicate_entry" | "overlapping_dates"; rows: number[]; message: string }[] = [];
  const groups = new Map<string, { row: number; start: string; end: string }[]>();
  entries.forEach((entry, index) => {
    if (!entry.accountId || !budgetValidDate(entry.startDate) || !budgetValidDate(entry.endDate) || entry.startDate > entry.endDate) return;
    const key = JSON.stringify([
      entry.accountId, entry.businessUnitId || null, [...entry.dimensionIds].sort(),
    ]);
    const bucket = groups.get(key) ?? [];
    bucket.forEach((prior) => {
      if (prior.start === entry.startDate && prior.end === entry.endDate) {
        const priorEntry = entries[prior.row - 1];
        const sameAmount = cents(priorEntry.amount) !== null && cents(priorEntry.amount) === cents(entry.amount);
        warnings.push(sameAmount ? {
          code: "duplicate_entry", rows: [prior.row, index + 1],
          message: `Rows ${prior.row} and ${index + 1} have the same account, business unit, dimensions, date range, and amount.`,
        } : {
          code: "overlapping_dates", rows: [prior.row, index + 1],
          message: `Rows ${prior.row} and ${index + 1} have the same dates but different amounts for the same account, business unit, and dimensions.`,
        });
      } else if (prior.start <= entry.endDate && entry.startDate <= prior.end) {
        warnings.push({
          code: "overlapping_dates", rows: [prior.row, index + 1],
          message: `Rows ${prior.row} and ${index + 1} have intersecting inclusive dates for the same account, business unit, and dimensions.`,
        });
      }
    });
    bucket.push({ row: index + 1, start: entry.startDate, end: entry.endDate });
    groups.set(key, bucket);
  });
  return warnings;
}

export function validateBudget(draft: BudgetScenarioDraft, accountIds: number[], unitIds: number[], dimensionIds: number[]) {
  const errors: string[] = [];
  if (!draft.name.trim()) errors.push("Enter a scenario name.");
  if (draft.name.length > 200) errors.push("Scenario name must be 200 characters or fewer.");
  if (!budgetValidDate(draft.startDate) || !budgetValidDate(draft.endDate) || draft.startDate > draft.endDate) {
    errors.push("Enter valid scenario start and end dates, with the start on or before the end.");
  }
  if (draft.entries.length < 1 || draft.entries.length > 100) errors.push("A scenario must contain 1 to 100 explicit rows.");
  draft.entries.forEach((entry, index) => {
    const row = index + 1;
    if (!budgetValidDate(entry.startDate) || !budgetValidDate(entry.endDate) || entry.startDate > entry.endDate) {
      errors.push(`Row ${row}: enter valid start and end dates, with the start on or before the end.`);
    } else if (budgetValidDate(draft.startDate) && budgetValidDate(draft.endDate) &&
      (entry.startDate < draft.startDate || entry.endDate > draft.endDate)) {
      errors.push(`Row ${row}: dates must remain within the explicit scenario dates.`);
    }
    const amount = cents(entry.amount);
    if (amount === null) errors.push(`Row ${row}: enter a non-negative amount up to 999999999999.99 with at most two decimal places.`);
    if (!accountIds.includes(Number(entry.accountId)) || !entry.accountId) errors.push(`Row ${row}: choose an available account.`);
    if (entry.businessUnitId && !unitIds.includes(Number(entry.businessUnitId))) errors.push(`Row ${row}: choose an available business unit.`);
    if (entry.dimensionIds.some((id) => !dimensionIds.includes(Number(id)))) errors.push(`Row ${row}: one or more dimensions are unavailable.`);
    if (entry.explanation.length > 2000) errors.push(`Row ${row}: explanation must be 2,000 characters or fewer.`);
  });
  return errors;
}
