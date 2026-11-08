import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import ApprovalLimits from "../src/ApprovalLimits";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const overview = (limit: string | null = null) => ({
  unit: { id: 7, code: "TVL", name: "Tivoli" },
  discounts: [
    { role: "salesperson", label: "Salesperson", percent: "10.00", default: "10.00", changed: null },
    { role: "manager", label: "Manager", percent: "15.00", default: "20.00", changed: { by: "Master Admin", at: "2026-11-04T10:00:00+03:00" } },
  ],
  people_discounts: [
    { id: 5, name: "TEST ONLY Omar", roles: ["Salesperson"], role_percent: "10.00", percent: null, changed: null },
  ],
  approvals: [
    { action: "approve_payment", label: "Approve supplier payments", covers: "Supplier payments", people: [{ id: 5, name: "TEST ONLY Omar", limit, changed: null }] },
    { action: "approve_expense", label: "Approve expenses", covers: "Expenses", people: [] },
  ],
  policy: "TEST ONLY policy",
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const input = (label: string) => container.querySelector(`input[aria-label="${label}"]`) as HTMLInputElement;
async function setValue(node: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "value")?.set?.call(node, value);
    node.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
const saveNext = (node: HTMLInputElement) => node.parentElement!.querySelector("button") as HTMLButtonElement;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
    const parsed = new URL(String(url), "http://localhost");
    calls.push({ url: parsed, init });
    return json(init?.method === "POST" ? overview("50000.00") : overview());
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("discount ceilings and approval limits are shown and saved", async () => {
  act(() => root.render(<ApprovalLimits selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  const text = container.textContent ?? "";
  assert.match(text, /Manager.*20%Changed by Master Admin, 4 Nov 2026/);
  assert.match(text, /Nobody in Tivoli has this permission/);
  const limit = input("Limit for Omar: Approve supplier payments");
  assert.equal(limit.value, "");
  assert.equal(saveNext(limit).disabled, true);
  await setValue(limit, "50,000");
  await act(async () => saveNext(limit).click());
  await flush();
  const post = calls.find((call) => call.init?.method === "POST")!;
  assert.equal(post.url.pathname, "/api/governance/approval-limits/");
  assert.deepEqual(JSON.parse(String(post.init?.body)), { action: "approve_payment", user_id: 5, max_amount: "50,000" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Omar may now approve up to QAR 50,000\.00/);
  assert.match(container.textContent ?? "", /Up to 50,000\.00/);
});

test("a role's discount can be put back to the standard", async () => {
  act(() => root.render(<ApprovalLimits selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  const manager = input("Discount ceiling for Manager");
  assert.equal(manager.value, "15");
  await setValue(manager, "");
  await act(async () => saveNext(manager).click());
  await flush();
  assert.deepEqual(JSON.parse(String(calls.find((call) => call.init?.method === "POST")!.init?.body)), { role: "manager", percent: "" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Manager: back to the standard 20%/);
});

test("each person can be given their own discount for stock sales", async () => {
  act(() => root.render(<ApprovalLimits selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.textContent ?? "", /Discount per person, for selling stock items/);
  assert.match(container.textContent ?? "", /OmarSalesperson.*Up to 10% \(role\)/);
  const own = input("Own discount for Omar");
  assert.equal(own.placeholder, "Role: 10");
  await setValue(own, "15");
  await act(async () => saveNext(own).click());
  await flush();
  const post = calls.find((call) => call.init?.method === "POST")!;
  assert.deepEqual(JSON.parse(String(post.init?.body)), { kind: "person_discount", user_id: 5, percent: "15" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Omar: up to 15% on stock sales\./);
});
