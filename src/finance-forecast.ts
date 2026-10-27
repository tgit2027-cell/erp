import type { LabContext } from "./finance-draft-lab-api";

export type ForecastEntryDraft = {
  date: string;
  direction: "inflow" | "outflow";
  amount: string;
  description: string;
  business_unit_id: string;
  dimension_value_ids: string[];
};
export type ForecastDraft = { name: string; as_of_date: string; opening_balance: string; entries: ForecastEntryDraft[] };
export type ForecastEntryPayload = {
  date: string;
  direction: "inflow" | "outflow";
  amount: string;
  description: string;
  business_unit_id: number | null;
  dimension_value_ids: number[];
};
export type ForecastPayload = {
  name: string;
  as_of_date: string;
  opening_balance: string;
  currency: "QAR";
  entries: ForecastEntryPayload[];
};
export type ForecastReview = {
  currency: "QAR";
  opening_balance: string;
  total_inflows: string;
  total_outflows: string;
  net_change: string;
  closing_balance: string;
  ordering: "date_ascending_then_input_order";
  proposed: true;
  actual_cash: false;
  posted: false;
  approved: false;
  entries: (ForecastEntryPayload & { line_number: number; running_balance: string })[];
};
export type ForecastChoiceContext = Pick<LabContext, "business_units" | "dimension_values">;

export const blankForecastEntry = (): ForecastEntryDraft => ({
  date: "", direction: "inflow", amount: "", description: "", business_unit_id: "", dimension_value_ids: [],
});
export const blankForecastDraft = (): ForecastDraft => ({
  name: "", as_of_date: "", opening_balance: "", entries: [blankForecastEntry()],
});

export function validForecastDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000-")) return false;
  const [year, month, day] = value.split("-").map(Number);
  if (month < 1 || month > 12 || day < 1) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  return day <= [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
}
const MONEY = /^(?:0|[1-9]\d{0,11})(?:\.\d{1,2})?$/;
export function validForecastMoney(value: string, signed = false): boolean {
  return (signed ? /^-?(?:0|[1-9]\d{0,11})(?:\.\d{1,2})?$/ : MONEY).test(value);
}
function cents(value: string): bigint {
  const negative = value.startsWith("-");
  const unsigned = negative ? value.slice(1) : value;
  const [whole, fraction = ""] = unsigned.split(".");
  const amount = BigInt(whole) * 100n + BigInt((fraction + "00").slice(0, 2));
  return negative ? -amount : amount;
}
export function canonicalMoney(value: bigint): string {
  const negative = value < 0n;
  const absolute = negative ? -value : value;
  const major = absolute / 100n;
  const minor = String(absolute % 100n).padStart(2, "0");
  return `${negative ? "-" : ""}${major}.${minor}`;
}
function positiveId(value: string): number | null {
  if (!/^[1-9]\d*$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}
export function validateForecast(draft: ForecastDraft, context: ForecastChoiceContext | null): string[] {
  const errors: string[] = [];
  if (!draft.name.trim()) errors.push("Enter a forecast name.");
  if (draft.name.length > 200) errors.push("Forecast name must be 200 characters or fewer.");
  if (!validForecastDate(draft.as_of_date)) errors.push("Enter an explicit valid opening as-of date.");
  if (!validForecastMoney(draft.opening_balance, true)) errors.push("Enter a signed exact QAR opening balance up to 999999999999.99 with at most two decimal places.");
  if (draft.entries.length < 1) errors.push("Add at least one cash-flow row.");
  if (draft.entries.length > 200) errors.push("A forecast may contain no more than 200 cash-flow rows.");
  draft.entries.forEach((entry, index) => {
    const line = index + 1;
    if (!validForecastDate(entry.date)) errors.push(`Flow ${line}: enter an explicit valid date.`);
    else if (validForecastDate(draft.as_of_date) && entry.date < draft.as_of_date) errors.push(`Flow ${line}: date must be on or after the opening as-of date.`);
    if (entry.direction !== "inflow" && entry.direction !== "outflow") errors.push(`Flow ${line}: choose inflow or outflow.`);
    if (!validForecastMoney(entry.amount)) errors.push(`Flow ${line}: enter a non-negative exact amount up to 999999999999.99 with at most two decimal places.`);
    if (!entry.description.trim()) errors.push(`Flow ${line}: enter a description.`);
    if (entry.description.length > 2000) errors.push(`Flow ${line}: description must be 2,000 characters or fewer.`);
    if (entry.business_unit_id && !context?.business_units.some((unit) => unit.id === positiveId(entry.business_unit_id))) {
      errors.push(`Flow ${line}: selected business unit is unavailable; choose an available unit or clear it.`);
    }
    const ids = entry.dimension_value_ids.map(positiveId);
    if (ids.some((id) => id === null) || new Set(ids).size !== ids.length) errors.push(`Flow ${line}: dimension values must be distinct saved IDs.`);
    if (ids.some((id) => !context?.dimension_values.some((item) => item.id === id))) {
      errors.push(`Flow ${line}: one or more selected dimension values are unavailable; remove or replace them.`);
    }
  });
  return errors;
}
export function payloadFromForecast(draft: ForecastDraft, context: ForecastChoiceContext): ForecastPayload {
  const errors = validateForecast(draft, context);
  if (errors.length) throw new Error(errors.join(" "));
  return {
    name: draft.name,
    as_of_date: draft.as_of_date,
    opening_balance: draft.opening_balance,
    currency: "QAR",
    entries: draft.entries.map((entry) => ({
      date: entry.date,
      direction: entry.direction,
      amount: entry.amount,
      description: entry.description,
      business_unit_id: entry.business_unit_id ? positiveId(entry.business_unit_id) : null,
      dimension_value_ids: entry.dimension_value_ids.map((id) => positiveId(id)!),
    })),
  };
}
export function calculateForecast(payload: ForecastPayload): ForecastReview {
  const ordered = payload.entries.map((entry, index) => ({ entry, index }))
    .sort((left, right) => left.entry.date.localeCompare(right.entry.date) || left.index - right.index);
  let running = cents(payload.opening_balance);
  let inflows = 0n;
  let outflows = 0n;
  const entries = ordered.map(({ entry, index }) => {
    const amount = cents(entry.amount);
    if (entry.direction === "inflow") { inflows += amount; running += amount; }
    else { outflows += amount; running -= amount; }
    return { ...entry, line_number: index + 1, running_balance: canonicalMoney(running) };
  });
  return {
    currency: "QAR",
    opening_balance: canonicalMoney(cents(payload.opening_balance)),
    total_inflows: canonicalMoney(inflows),
    total_outflows: canonicalMoney(outflows),
    net_change: canonicalMoney(inflows - outflows),
    closing_balance: canonicalMoney(running),
    ordering: "date_ascending_then_input_order",
    proposed: true,
    actual_cash: false,
    posted: false,
    approved: false,
    entries,
  };
}
