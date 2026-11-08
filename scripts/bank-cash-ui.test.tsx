import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import BankCash from "../src/BankCash";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let canManage = true;

const account = (id: number, name: string, kind: "bank" | "cash", balance: string, extra = {}) => ({
  id, name, kind, kind_label: kind === "bank" ? "Bank account" : "Cash account", bank_name: kind === "bank" ? "TEST ONLY QNB" : "",
  account_type: kind === "bank" ? "current" : "", account_type_label: kind === "bank" ? "Current account" : "", is_facility: false,
  account_last4: kind === "bank" ? "4417" : "", currency: "QAR", opening_balance: "0.00", opening_date: "2026-01-01",
  is_default: true, balance, opening: "0.00", money_in: balance, money_out: "0.00", ...extra,
});
const movement = (line_id: number, description: string, money_in: string, fixed = false) => ({
  line_id, date: "2026-10-20", journal_entry_id: 40 + line_id, source: fixed ? "Bank or cash entry" : "Customer payment",
  source_kind: fixed ? "cash_entry" : "payment", description, memo: "", money_in, money_out: "", balance: money_in,
  account_id: 1, fixed,
});
const overview = (url: URL) => {
  const selected = Number(url.searchParams.get("account") || 1);
  return {
    unit: { id: 1, code: "TVL", name: "Tivoli" }, start: "2026-10-01", end: "2026-10-31", can_manage: canManage,
    accounts: [account(1, "TEST ONLY QNB current", "bank", "52000.00"), account(2, "TEST ONLY Showroom till", "cash", "1500.00")],
    total: "53500.00", facilities_total: "0.00", selected_id: selected,
    account_types: [{ value: "current", label: "Current account" }, { value: "savings", label: "Savings account" },
      { value: "ltr", label: "LTR account (loan against trust receipt)" }, { value: "lg", label: "LG account (letter of guarantee margin)" }],
    movements: selected === 1 ? [movement(7, "customer payment on quote 3", "2000.00"), movement(8, "Interest on QNB", "12.50", true)] : [],
    unplaced: [], entry_kinds: [], policy_label: "TEST ONLY policy.",
  };
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

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  canManage = true;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/finance/cash/") return json(overview(url));
    if (url.pathname === "/api/finance/cash/entries/") return json({ id: 3 }, 201);
    if (url.pathname === "/api/finance/cash/move/") return json({ line_id: 7, account_id: 2 });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("each account shows its balance and the selected account lists money in and out", async () => {
  act(() => root.render(<BankCash selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  const tiles = [...container.querySelectorAll(".bc-accounts strong")].map((node) => node.textContent);
  assert.deepEqual(tiles, ["53,500.00", "52,000.00", "1,500.00"]);
  assert.match(container.querySelector(".bc-accounts")?.textContent ?? "", /Money available53,500.00.*QNBQNB current.*Current account ···4417 · in 52,000.00.*CashShowroom till/);
  const rows = container.querySelectorAll(".bc-table tbody tr");
  assert.equal(rows.length, 2);
  assert.match(rows[1].textContent ?? "", /Recorded here/);
  await act(async () => (container.querySelectorAll(".bc-accounts button")[1] as HTMLButtonElement).click());
  await flush();
  assert.equal(calls.at(-1)?.url.searchParams.get("account"), "2");
  assert.match(container.textContent ?? "", /No money went in or out of Showroom till/);
});

test("a movement that landed elsewhere can be moved to another account", async () => {
  act(() => root.render(<BankCash selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  const select = container.querySelector(".bc-table select") as HTMLSelectElement;
  await act(async () => {
    select.value = "2";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
  const move = calls.find((call) => call.url.pathname === "/api/finance/cash/move/")!;
  assert.deepEqual(JSON.parse(String(move.init?.body)), { line_id: 7, account_id: 2 });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Moved to Showroom till/);
});

test("a transfer is posted with an idempotency key; viewers cannot record or move", async () => {
  act(() => root.render(<BankCash selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Transfer")!.click());
  const form = container.querySelector("form")!;
  const amount = [...form.querySelectorAll("input")].find((input) => input.inputMode === "decimal") as HTMLInputElement;
  await act(async () => type(amount, "2500.00"));
  await act(async () => form.requestSubmit());
  await flush();
  const post = calls.find((call) => call.url.pathname === "/api/finance/cash/entries/")!;
  const body = JSON.parse(String(post.init?.body));
  assert.deepEqual([body.kind, body.account_id, body.to_account_id, body.amount], ["transfer", 1, 2, "2500.00"]);
  assert.ok(new Headers(post.init?.headers).get("Idempotency-Key"));
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Transfer recorded/);

  canManage = false;
  act(() => root.render(<BankCash key="viewer" selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  assert.equal(button("Transfer"), undefined);
  assert.equal(container.querySelector(".bc-table select"), null);
});

test("adding a second account under a bank; a unit has one cash account", async () => {
  act(() => root.render(<BankCash selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Add account")!.click());
  const form = container.querySelector('form[aria-label="Add an account"]')!;
  const selects = [...form.querySelectorAll("select")] as HTMLSelectElement[];
  assert.deepEqual([...selects[0].options].map((option) => option.value), ["bank"], "The cash account already exists");
  assert.match(form.textContent ?? "", /already has its cash account/);
  const field = (label: string) => [...form.querySelectorAll("label")].find((node) => node.textContent?.startsWith(label))!.querySelector("input, select") as HTMLInputElement;
  await act(async () => type(field("Bank"), "QNB"));
  await act(async () => {
    const select = field("Account type") as unknown as HTMLSelectElement;
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(select), "value")?.set?.call(select, "ltr");
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  assert.ok(field("Amount owed at opening"), "An LTR account opens with what is owed");
  await act(async () => type(field("Name"), "QNB LTR"));
  await act(async () => form.requestSubmit());
  await flush();
  const post = calls.find((call) => call.url.pathname === "/api/finance/cash/accounts/")!;
  const body = JSON.parse(String(post.init?.body));
  assert.deepEqual([body.kind, body.bank_name, body.account_type, body.name], ["bank", "QNB", "ltr", "QNB LTR"]);
});
