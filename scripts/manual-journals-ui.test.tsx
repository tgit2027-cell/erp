import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import ManualJournals from "../src/ManualJournals";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const journal = (id: number, extra = {}) => ({
  id, number: `MJ-TVL-2026-0000${id}`, date: "2026-10-31", description: "TEST ONLY October electricity not billed yet",
  status: "submitted", status_label: "Waiting for approval", reverse_next_month: true, total: "1200.00", balanced: true, difference: "0.00",
  prepared_by: "Synthetic Admin", prepared_at: "2026-10-31T09:00:00Z", returned_reason: "", approved_by: "", approved_at: "",
  journal_entry_id: null, auto_reversal_entry_id: null, reversal_of: "", reversed_by: "", can_edit: false, can_decide: true, can_reverse: false,
  ...extra,
});
const lines = [
  { account_code: "TEST_EXP_UTILITIES", account: "Utilities", debit: "1200.00", credit: "0.00", memo: "estimate" },
  { account_code: "TEST_ACCRUALS", account: "Costs owed but not yet billed (accruals)", debit: "0.00", credit: "1200.00", memo: "" },
];
const overview = () => ({
  unit: { id: 7, code: "TVL", name: "Tivoli" },
  journals: [journal(1), journal(2, { status: "posted", status_label: "Posted", reverse_next_month: false, approved_by: "Omar", journal_entry_id: 5, can_decide: false })],
  waiting: 1, waiting_for_me: 1,
  accounts: [
    { code: "TEST_PREPAYMENTS", name: "Costs paid in advance (prepayments)", kind: "asset" },
    { code: "TEST_ACCRUALS", name: "Costs owed but not yet billed (accruals)", kind: "liability" },
    { code: "TEST_EXP_UTILITIES", name: "Utilities", kind: "expense" },
  ],
  control_accounts: ["TEST_CASH"], can_prepare: true, can_approve: true, policy: "TEST ONLY policy",
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
const labelled = (label: string) => container.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLSelectElement;
const field = (label: string) => [...container.querySelectorAll("label")].find((node) => node.textContent?.startsWith(label))?.querySelector("input, select") as HTMLInputElement;
function setValue(input: HTMLInputElement | HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
  input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
}
const posts = () => calls.filter((call) => call.init?.method === "POST");

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/finance/journals/" && init?.method === "POST") return json({ ...journal(3), lines }, 201);
    if (url.pathname === "/api/finance/journals/") return json(overview());
    if (url.pathname === "/api/finance/journals/1/" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      return body.action === "approve"
        ? json({ ...journal(1, { status: "posted", status_label: "Posted", approved_by: "Omar", journal_entry_id: 8, can_decide: false }), lines, history: [] })
        : json({ ...journal(1, { status: "draft", status_label: "Draft", returned_reason: body.reason, can_decide: false }), lines, history: [] });
    }
    if (url.pathname === "/api/finance/journals/1/") return json({ ...journal(1), lines, history: [{ at: "2026-10-31T09:00:00Z", by: "Synthetic Admin", change: "sent for approval", note: "" }] });
    if (url.pathname === "/api/finance/journals/2/" && init?.method === "POST") return json({ ...journal(4, { status: "draft", status_label: "Draft", reversal_of: "MJ-TVL-2026-00002" }), lines });
    if (url.pathname === "/api/finance/journals/2/") return json({ ...journal(2, { status: "posted", status_label: "Posted", can_decide: false, can_reverse: true }), lines, history: [] });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the list shows what waits for approval and who approved what", async () => {
  act(() => root.render(<ManualJournals selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector(".ex-tiles")?.textContent ?? "", /Waiting for approval11 for you to approve/);
  const table = container.querySelector(".mj-table")?.textContent ?? "";
  assert.match(table, /MJ-TVL-2026-00001October electricity not billed yet2026-10-31undone on the 1st of next month1,200\.00Waiting for approvalprepared by Synthetic Admin · for you to approve/);
  assert.match(table, /Postedapproved by Omar/);
  setValue(field("Show"), "submitted");
  await flush();
  assert.equal(calls.at(-1)?.url.searchParams.get("status"), "submitted");
});

test("a journal is prepared line by line and cannot be sent until it balances", async () => {
  act(() => root.render(<ManualJournals selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Prepare a journal")!.click());
  setValue(field("What it is for"), "TEST ONLY October electricity");
  setValue(labelled("Account, line 1"), "TEST_EXP_UTILITIES");
  setValue(labelled("Debit, line 1"), "1200");
  setValue(labelled("Account, line 2"), "TEST_ACCRUALS");
  setValue(labelled("Credit, line 2"), "1000");
  await flush();
  assert.equal(button("Send for approval")!.disabled, true, "Out of balance");
  setValue(labelled("Credit, line 2"), "1200");
  await flush();
  assert.equal(button("Send for approval")!.disabled, false);
  await act(async () => button("Send for approval")!.click());
  await flush();
  const body = JSON.parse(String(posts()[0].init?.body));
  assert.equal(body.submit, true);
  assert.deepEqual(body.lines.map((line: { account_code: string; debit: string; credit: string }) => [line.account_code, line.debit, line.credit]),
    [["TEST_EXP_UTILITIES", "1200", ""], ["TEST_ACCRUALS", "", "1200"]]);
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /MJ-TVL-2026-00003 is waiting for someone else to approve it/);
});

test("an approver approves or sends back, and a posted journal is reversed by a new draft", async () => {
  act(() => root.render(<ManualJournals selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("MJ-TVL-2026-00001")!.click());
  await flush();
  assert.match(container.querySelector(".mj-detail")?.textContent ?? "", /Utilities/);
  await act(async () => button("Send back")!.click());
  setValue(labelled("Why it is sent back"), "TEST ONLY use the bill amount");
  await flush();
  const sendBack = [...container.querySelectorAll("button")].filter((node) => node.textContent === "Send back").at(-1)!;
  await act(async () => sendBack.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { action: "return", reason: "TEST ONLY use the bill amount" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /is sent back to Synthetic Admin/);

  await act(async () => button("MJ-TVL-2026-00002")!.click());
  await flush();
  await act(async () => button("Reverse")!.click());
  await act(async () => button("Prepare the reversal")!.click());
  await flush();
  assert.equal(JSON.parse(String(posts()[1].init?.body)).action, "reverse");
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /MJ-TVL-2026-00004 is prepared as a draft that reverses MJ-TVL-2026-00002/);
});
