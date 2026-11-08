import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import BankReconciliation from "../src/BankReconciliation";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let options: { canRead: boolean; rec: Record<string, unknown> | null; me: string; unmatched: number };

const view = () => ({
  unit: { id: 1, code: "TVL", name: "Tivoli" }, accounts: [{ id: 1, name: "TEST ONLY QNB current", kind: "bank" }],
  month: "2026-09", month_end: "2026-09-30", can_manage: true, can_approve: true, me: options.me, can_read_documents: options.canRead,
  account: { id: 1, name: "TEST ONLY QNB current", kind: "bank", kind_label: "Bank account", opening_date: "2026-09-01" },
  statement: [
    { id: 11, date: "2026-09-03", description: "TEST ONLY transfer from client", reference: "FT-1", amount: "2000.00",
      matched_to: 70, matched_date: "2026-09-02", matched_description: "customer payment on quote 3", suggested: [] },
    { id: 12, date: "2026-09-21", description: "TEST ONLY cheque 101", reference: "", amount: "-450.00",
      matched_to: null, matched_date: null, matched_description: "", suggested: [72] },
  ].slice(0, options.unmatched ? 2 : 1),
  statement_movement: options.unmatched ? "1550.00" : "2000.00", suggested_opening: "50000.00", book_balance: "51550.00",
  outstanding: [
    { line_id: 71, date: "2026-09-29", journal_entry_id: 9, description: "customer payment on quote 5", source: "Customer payment", amount: "300.00" },
    { line_id: 72, date: "2026-09-20", journal_entry_id: 8, description: "supplier payment 4", source: "Supplier payment", amount: "-450.00" },
  ],
  outstanding_in: "300.00", outstanding_out: "450.00", expected_closing: "51700.00", unmatched_count: options.unmatched, late_count: 0,
  reconciliation: options.rec, history: options.rec ? [options.rec] : [], locked: false,
});
const reading = {
  account_id: 1, file_name: "qnb-september.pdf", line_count: 3, ready_count: 1, money_in: "0.00", money_out: "12.50",
  statement: { bank_name: "TEST ONLY QNB", account_last4: "4417", currency: "QAR", period_start: "2026-09-01", period_end: "2026-09-30", opening_balance: "50000.00", closing_balance: "51537.50" },
  warnings: ["The lines read do not add up."], notice: "Read by AI. Check each line against the statement before adding it.",
  lines: [
    { date: "2026-09-03", description: "TEST ONLY transfer from client", reference: "FT-1", amount: "2000.00", checks: ["Already on the statement"], include: false },
    { date: "2026-09-30", description: "TEST ONLY bank charge", reference: "", amount: "-12.50", checks: [], include: true },
    { date: "2026-09-25", description: "TEST ONLY card deposit", reference: "", amount: "see note", checks: ["Amount not clear"], include: false },
  ],
};

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent === text) as HTMLButtonElement | undefined;
function type(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}
const posted = (path: string) => calls.filter((call) => call.url.pathname === path && call.init?.method === "POST");

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  options = { canRead: true, rec: null, me: "admin", unmatched: 1 };
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/finance/bank-reconciliation/" && !init?.method) return json(view());
    if (url.pathname === "/api/finance/bank-reconciliation/read-statement/") return json(reading);
    if (url.pathname === "/api/finance/bank-reconciliation/statement/") {
      const body = JSON.parse(String(init?.body));
      return json(body.action === "add" ? { added: body.lines.length } : body.action === "auto_match" ? { matched: 1 } : {}, body.action === "add" ? 201 : 200);
    }
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("statement lines show what is matched, and an open line is matched to the suggested record", async () => {
  act(() => root.render(<BankReconciliation selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  const rows = container.querySelectorAll("table[aria-label='Statement lines'] tbody tr");
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent ?? "", /Matched2026-09-02 · customer payment on quote 3/);
  assert.match(rows[1].textContent ?? "", /Not matched/);
  const select = rows[1].querySelector("select") as HTMLSelectElement;
  assert.equal(select.value, "72");
  assert.match(select.options[0].textContent ?? "", /supplier payment 4 \(suggested\)/);
  assert.ok(button("Record as bank charge"), "A money-out line can be recorded as a bank charge");
  await act(async () => button("Match")!.click());
  await flush();
  const match = posted("/api/finance/bank-reconciliation/statement/")[0];
  assert.deepEqual(JSON.parse(String(match.init?.body)), { action: "match", statement_line_id: 12, journal_line_id: 72 });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Matched/);
  assert.equal(container.querySelectorAll("table[aria-label='Outstanding items'] tbody tr").length, 2);
  assert.ok((button("Prepare for approval") as HTMLButtonElement).disabled, "Cannot prepare while a line is open");
});

test("an uploaded statement is read into lines to check, and only ticked lines are added", async () => {
  act(() => root.render(<BankReconciliation selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  assert.ok(button("Upload statement"));
  const input = container.querySelector("input[type=file]") as HTMLInputElement;
  const file = new File(["%PDF-1.4 synthetic"], "qnb-september.pdf", { type: "application/pdf" });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  await flush();
  const read = posted("/api/finance/bank-reconciliation/read-statement/")[0];
  const form = read.init?.body as FormData;
  assert.equal((form.get("file") as File).name, "qnb-september.pdf");
  assert.deepEqual([form.get("account_id"), form.get("month")], ["1", "2026-09"]);
  assert.equal(new Headers(read.init?.headers).get("Content-Type"), null, "The browser sets the multipart boundary");
  const drafts = container.querySelectorAll("table[aria-label='Lines to add'] tbody tr");
  assert.equal(drafts.length, 3);
  assert.match(drafts[0].textContent ?? "", /Already on the statement/);
  assert.deepEqual([...container.querySelectorAll<HTMLInputElement>("table[aria-label='Lines to add'] input[type=checkbox]")].map((box) => box.checked), [false, true, false]);
  assert.match(container.textContent ?? "", /Read by AI\. Check each line/);
  assert.match(container.textContent ?? "", /do not add up/);

  const amount = drafts[2].querySelector("input[aria-label='Amount of line 3']") as HTMLInputElement;
  await act(async () => type(amount, "340.00"));
  await act(async () => (drafts[2].querySelector("input[type=checkbox]") as HTMLInputElement).click());
  assert.ok(button("Add 2 lines"));
  await act(async () => (container.querySelector("form[aria-label='Check the lines read from the statement']") as HTMLFormElement).requestSubmit());
  await flush();
  const add = posted("/api/finance/bank-reconciliation/statement/")[0];
  const body = JSON.parse(String(add.init?.body));
  assert.equal(body.action, "add");
  assert.deepEqual(body.lines, [
    { date: "2026-09-30", description: "TEST ONLY bank charge", reference: "", amount: "-12.50" },
    { date: "2026-09-25", description: "TEST ONLY card deposit", reference: "", amount: "340.00" },
  ]);
  assert.ok(new Headers(add.init?.headers).get("Idempotency-Key"));
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /2 statement lines added/);
});

test("without AI the lines are typed in; the preparer sees whether it ties; another person approves", async () => {
  options = { canRead: false, rec: null, me: "admin", unmatched: 0 };
  act(() => root.render(<BankReconciliation selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  assert.equal(button("Upload statement"), undefined);
  assert.match(container.textContent ?? "", /not switched on here, so type the lines in/);
  const prepare = container.querySelector("form[aria-label='Prepare the reconciliation']") as HTMLFormElement;
  const [opening, closing] = [...prepare.querySelectorAll("input")] as HTMLInputElement[];
  assert.equal(opening.value, "50000.00");
  await act(async () => type(closing, "51700.00"));
  assert.match(prepare.textContent ?? "", /Opening plus the lines is 52,000.00/);
  await act(async () => type(opening, "49700.00"));
  assert.match(prepare.textContent ?? "", /The statement ties to our records/);
  await act(async () => prepare.requestSubmit());
  await flush();
  const sent = calls.find((call) => call.url.pathname === "/api/finance/bank-reconciliation/" && call.init?.method === "POST")!;
  assert.deepEqual(JSON.parse(String(sent.init?.body)), { account_id: 1, month: "2026-09", statement_opening: "49700.00", statement_closing: "51700.00", note: "" });

  options = { canRead: false, me: "checker", unmatched: 0, rec: {
    id: 5, month: "2026-09", status: "prepared", status_label: "Prepared", statement_opening: "49700.00", statement_closing: "51700.00",
    statement_movement: "2000.00", book_balance: "51550.00", outstanding_in: "300.00", outstanding_out: "450.00", note: "",
    prepared_by: "admin", prepared_at: "2026-10-02T09:00:00Z", decided_by: null, decision_note: "" } };
  act(() => root.render(<BankReconciliation key="checker" selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  assert.equal(button("Type lines in"), undefined, "A prepared month cannot change");
  await act(async () => button("Approve")!.click());
  await flush();
  assert.ok(calls.some((call) => call.url.pathname === "/api/finance/bank-reconciliation/5/approve/"));
});
