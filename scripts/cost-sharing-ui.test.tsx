import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import CostSharing from "../src/CostSharing";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
const month = new Date().toLocaleDateString("en-CA").slice(0, 7);

const share = (id: number, extra = {}) => ({
  id, number: `CS-TVL-2026-0000${id}`, date: `${month}-01`, account_code: "TEST_EXP_RENT", account: "Rent",
  description: "TEST ONLY rent for the Salwa Road building", amount: "30000.00", moved: "18000.00", kept_percent: "40.00", kept: "12000.00",
  from_unit: { id: 7, name: "Tivoli" }, direction: "sent", received: null,
  lines: [{ unit: { id: 8, name: "Roche Bobois" }, percent: "35.00", amount: "10500.00", journal_entry_id: 41 },
    { unit: { id: 9, name: "Natuzzi" }, percent: "25.00", amount: "7500.00", journal_entry_id: 42 }],
  status: "approved", status_label: "Shared", prepared_by: "Synthetic Admin", decided_by: "Omar", decision_note: "",
  journal_entry_id: 40, can_decide: false, ...extra,
});
const overview = () => ({
  unit: { id: 7, code: "TVL", name: "Tivoli" }, month, month_label: "This month",
  shares: [share(1), share(2, { status: "submitted", status_label: "Waiting for approval", decided_by: "", can_decide: true, account: "Electricity and water",
    account_code: "TEST_EXP_UTILITIES", amount: "900.00", moved: "300.00", kept: "600.00", lines: [{ unit: { id: 8, name: "Roche Bobois" }, percent: "33.33", amount: "300.00", journal_entry_id: null }] }),
  share(3, { direction: "received", from_unit: { id: 9, name: "Natuzzi" }, received: "1250.00", account: "Marketing and advertising" })],
  totals: { sent: "18000.00", received: "1250.00", waiting: 1, waiting_for_me: 1 },
  accounts: [{ code: "TEST_EXP_RENT", name: "Rent", available: "20000.00" }, { code: "TEST_EXP_UTILITIES", name: "Electricity and water", available: "600.00" }],
  units: [{ id: 8, name: "Roche Bobois" }, { id: 9, name: "Natuzzi" }],
  can_prepare: true, can_approve: true, currency: "QAR", policy: "TEST ONLY policy",
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
const field = (label: string) => (container.querySelector(`[aria-label="${label}"]`)
  ?? [...container.querySelectorAll("label")].find((node) => node.textContent?.startsWith(label))?.querySelector("input, select")) as HTMLInputElement;
async function setValue(input: HTMLInputElement | HTMLSelectElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
    input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
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
    if (url.pathname === "/api/finance/cost-sharing/" && init?.method === "POST") return json(share(4, { status: "submitted" }), 201);
    if (url.pathname === "/api/finance/cost-sharing/") return json(overview());
    if (init?.method === "POST") return json({ ok: true });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the list shows what was shared out and what came in from other units", async () => {
  act(() => root.render(<CostSharing selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.equal(calls[0].url.searchParams.get("month"), month);
  assert.match(container.querySelector(".ex-tiles")?.textContent ?? "", /Shared out in This month18,000\.00.*Received in This month1,250\.00.*Waiting for approval11 for you/);
  const table = container.querySelector(".cs-table")?.textContent ?? "";
  assert.match(table, /CS-TVL-2026-00001rent for the Salwa Road building.*RentTo Roche Bobois, Natuzzi−18,000\.00of 30,000\.00Sharedby Omar/);
  assert.match(table, /From Natuzzi\+1,250\.00received/);
  await act(async () => button("CS-TVL-2026-00002")!.click());
  await act(async () => button("Approve and share")!.click());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/finance/cost-sharing/2/");
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { action: "approve" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /CS-TVL-2026-00002 is approved/);
});

test("the split shows each unit's amount and what the paying unit keeps, and stops at the unshared cost", async () => {
  act(() => root.render(<CostSharing selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Share a cost")!.click());
  await setValue(field("Kind of cost"), "TEST_EXP_RENT");
  await setValue(field("Cost to share (QAR)"), "30000");
  await setValue(field("Description"), "TEST ONLY rent");
  await setValue(field("Unit 1"), "8");
  await setValue(field("Share % for unit 1"), "35");
  await act(async () => button("Add a unit")!.click());
  await setValue(field("Unit 2"), "9");
  await setValue(field("Share % for unit 2"), "25");
  const form = container.querySelector(".an-form")?.textContent ?? "";
  assert.match(form, /Tivoli keeps40%12,000\.00/);
  assert.match(form, /On approval 18,000\.00 QAR moves out of Tivoli: Roche Bobois 10,500\.00, Natuzzi 7,500\.00/);
  await setValue(field("Share % for unit 2"), "35");
  assert.match(container.querySelector("[role=alert]")?.textContent ?? "", /more than the 20,000\.00 QAR not already shared/);
  assert.equal(button("Send for approval")!.disabled, true);
  await setValue(field("Share % for unit 2"), "25");
  await act(async () => button("Send for approval")!.click());
  await flush();
  const body = JSON.parse(String(posts()[0].init?.body));
  assert.deepEqual(body.units, [{ business_unit_id: 8, percent: "35" }, { business_unit_id: 9, percent: "25" }]);
  assert.deepEqual([body.account_code, body.amount], ["TEST_EXP_RENT", "30000"]);
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /CS-TVL-2026-00004 is waiting for someone else to approve it/);
});

test("a past share can be repeated with the same split", async () => {
  act(() => root.render(<CostSharing selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("CS-TVL-2026-00001")!.click());
  assert.match(container.querySelector(".an-detail")?.textContent ?? "", /Roche Bobois35%10,500\.00Journal 41/);
  await act(async () => button("Share again with the same split")!.click());
  assert.equal(field("Kind of cost").value, "TEST_EXP_RENT");
  assert.equal(field("Unit 2").value, "9");
  assert.equal(field("Share % for unit 1").value, "35");
  assert.equal(field("Cost to share (QAR)").value, "", "The amount is entered fresh each time");
});
