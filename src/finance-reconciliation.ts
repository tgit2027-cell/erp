import { cents, moneyText, validDate } from "./finance-allocation";

export type ReconciliationRow = {
  row_id: string;
  date: string;
  amount: string;
  direction: "increase" | "decrease";
  reference: string;
  explanation: string;
};
export type ReconciliationRowDraft = Omit<ReconciliationRow, "direction"> & {
  direction: "increase" | "decrease" | "";
};
export type ReconciliationMatch = {
  statement_row_id: string;
  book_row_id: string;
  explanation: string;
};
export type ReconciliationPayload = {
  name: string;
  currency: "QAR";
  statement_date: string;
  statement_balance: string;
  book_date: string;
  book_balance: string;
  explanation: string;
  statement_rows: ReconciliationRow[];
  book_rows: ReconciliationRow[];
  matches: ReconciliationMatch[];
};
export type ReconciliationDraft = Omit<ReconciliationPayload, "statement_rows" | "book_rows"> & {
  statement_rows: ReconciliationRowDraft[];
  book_rows: ReconciliationRowDraft[];
};
export const emptyReconciliationRow = (): ReconciliationRowDraft => ({
  row_id: "", date: "", amount: "", direction: "", reference: "", explanation: "",
});
export const blankReconciliation = (): ReconciliationDraft => ({
  name: "", currency: "QAR", statement_date: "", statement_balance: "",
  book_date: "", book_balance: "", explanation: "",
  statement_rows: [], book_rows: [], matches: [],
});

export function reconciliationDate(value: string): boolean {
  return !/^0000-/.test(value) && validDate(value);
}

/** Signed snapshot balances use exact cents while row magnitudes stay non-negative. */
export function signedCents(value: string): bigint | null {
  const normalized = value.trim();
  if (!/^-?(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(normalized)) return null;
  const negative = normalized.startsWith("-");
  const magnitude = cents(negative ? normalized.slice(1) : normalized);
  return magnitude === null ? null : negative ? -magnitude : magnitude;
}

export function rowNet(rows: ReconciliationRowDraft[]): bigint | null {
  let total = 0n;
  for (const row of rows) {
    const amount = cents(row.amount);
    if (amount === null) return null;
    if (row.direction !== "increase" && row.direction !== "decrease") return null;
    total += row.direction === "increase" ? amount : -amount;
  }
  return total;
}

export function reconciliationMath(draft: ReconciliationDraft) {
  const statement = signedCents(draft.statement_balance);
  const book = signedCents(draft.book_balance);
  const statementNet = rowNet(draft.statement_rows);
  const bookNet = rowNet(draft.book_rows);
  if (statement === null || book === null || statementNet === null || bookNet === null) return null;
  const unmatchedStatement = new Set(draft.statement_rows.map((row) => row.row_id));
  const unmatchedBook = new Set(draft.book_rows.map((row) => row.row_id));
  const usedStatement = new Set<string>(), usedBook = new Set<string>();
  for (const match of draft.matches) {
    const statement = draft.statement_rows.find((row) => row.row_id === match.statement_row_id);
    const book = draft.book_rows.find((row) => row.row_id === match.book_row_id);
    if (!statement || !book || usedStatement.has(match.statement_row_id) || usedBook.has(match.book_row_id)) continue;
    if (cents(statement.amount) === null || cents(statement.amount) !== cents(book.amount) ||
      statement.direction !== book.direction) continue;
    unmatchedStatement.delete(match.statement_row_id);
    unmatchedBook.delete(match.book_row_id);
    usedStatement.add(match.statement_row_id);
    usedBook.add(match.book_row_id);
  }
  const statementUnmatched = draft.statement_rows.filter((row) => unmatchedStatement.has(row.row_id));
  const bookUnmatched = draft.book_rows.filter((row) => unmatchedBook.has(row.row_id));
  const unmatchedStatementNet = rowNet(statementUnmatched)!;
  const unmatchedBookNet = rowNet(bookUnmatched)!;
  const difference = statement - book;
  const unmatchedDifference = unmatchedStatementNet - unmatchedBookNet;
  return {
    statementNet: moneyText(statementNet), bookNet: moneyText(bookNet),
    balanceDifference: moneyText(difference),
    unmatchedStatementIds: [...unmatchedStatement], unmatchedBookIds: [...unmatchedBook],
    unmatchedStatementNet: moneyText(unmatchedStatementNet), unmatchedBookNet: moneyText(unmatchedBookNet),
    unmatchedRowDifference: moneyText(unmatchedDifference),
    comparisonResidual: moneyText(difference - unmatchedDifference),
  };
}

export function validateReconciliation(draft: ReconciliationDraft): string[] {
  const errors: string[] = [];
  if (!draft.name.trim()) errors.push("Enter a reconciliation proposal name.");
  if (draft.name.length > 200) errors.push("Proposal name must be 200 characters or fewer.");
  if (draft.currency !== "QAR") errors.push("Currency is fixed at QAR; no foreign-exchange conversion is inferred.");
  for (const [label, date, balance] of [
    ["Statement", draft.statement_date, draft.statement_balance],
    ["Book", draft.book_date, draft.book_balance],
  ]) {
    if (!reconciliationDate(date)) errors.push(`${label}: enter an explicit valid date (year 0000 is not valid).`);
    const amount = signedCents(balance);
    if (amount === null) errors.push(`${label}: enter a signed balance with at most two decimal places and magnitude below 1,000,000,000,000.00.`);
  }
  if (draft.explanation.length > 2000) errors.push("Proposal explanation must be 2,000 characters or fewer.");
  validateRows(draft.statement_rows, "Statement", errors);
  validateRows(draft.book_rows, "Book", errors);
  if (draft.matches.length > 100) errors.push("Enter no more than 100 explicit matches.");
  const statementById = new Map(draft.statement_rows.map((row) => [row.row_id, row]));
  const bookById = new Map(draft.book_rows.map((row) => [row.row_id, row]));
  const usedStatement = new Set<string>();
  const usedBook = new Set<string>();
  draft.matches.forEach((match, index) => {
    const label = `Match ${index + 1}`;
    const statement = statementById.get(match.statement_row_id);
    const book = bookById.get(match.book_row_id);
    if (!statement) errors.push(`${label}: statement row “${match.statement_row_id || "(blank)"}” is missing or dangling.`);
    if (!book) errors.push(`${label}: book row “${match.book_row_id || "(blank)"}” is missing or dangling.`);
    if (usedStatement.has(match.statement_row_id)) errors.push(`${label}: statement row ${match.statement_row_id} is matched more than once.`);
    if (usedBook.has(match.book_row_id)) errors.push(`${label}: book row ${match.book_row_id} is matched more than once.`);
    usedStatement.add(match.statement_row_id);
    usedBook.add(match.book_row_id);
    if (statement && book) {
      const a = cents(statement.amount), b = cents(book.amount);
      if (a !== null && b !== null && (a !== b || statement.direction !== book.direction)) {
        errors.push(`${label}: full-row match requires the same exact magnitude and direction.`);
      }
    }
    if (match.explanation.length > 2000) errors.push(`${label}: explanation must be 2,000 characters or fewer.`);
  });
  return errors;
}

function validateRows(rows: ReconciliationRowDraft[], source: string, errors: string[]) {
  if (rows.length > 100) errors.push(`${source}: enter no more than 100 explicit source rows.`);
  const ids = new Set<string>();
  rows.forEach((row, index) => {
    const label = `${source} row ${index + 1}`;
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(row.row_id)) errors.push(`${label}: enter a unique explicit row ID (first character must be an ASCII letter or digit, followed by up to 63 letters, digits, underscores, or hyphens).`);
    else if (ids.has(row.row_id)) errors.push(`${label}: row ID ${row.row_id} is duplicated on this side.`);
    ids.add(row.row_id);
    if (!reconciliationDate(row.date)) errors.push(`${label}: enter a valid date (year 0000 is not valid).`);
    if (cents(row.amount) === null) errors.push(`${label}: enter a non-negative amount up to 999999999999.99 with at most two decimal places.`);
    if (row.direction !== "increase" && row.direction !== "decrease") errors.push(`${label}: choose increase or decrease.`);
    if (row.reference.length > 200) errors.push(`${label}: reference must be 200 characters or fewer.`);
    if (row.explanation.length > 2000) errors.push(`${label}: explanation must be 2,000 characters or fewer.`);
  });
}

export function payloadFromDraft(draft: ReconciliationDraft): ReconciliationPayload {
  return {
    name: draft.name.trim(), currency: "QAR",
    statement_date: draft.statement_date, book_date: draft.book_date,
    explanation: draft.explanation,
    statement_balance: draft.statement_balance.trim(), book_balance: draft.book_balance.trim(),
    statement_rows: draft.statement_rows.map((row) => ({
      ...row, amount: row.amount.trim(), direction: row.direction as ReconciliationRow["direction"],
    })),
    book_rows: draft.book_rows.map((row) => ({
      ...row, amount: row.amount.trim(), direction: row.direction as ReconciliationRow["direction"],
    })),
    matches: draft.matches.map((match) => ({ ...match })),
  };
}
