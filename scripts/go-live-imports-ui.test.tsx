import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import GoLiveImports from "../src/GoLiveImports";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let previewResult: unknown;

const batch = (id: number, extra = {}) => ({
  id, kind: "customers", kind_label: "Customers", status: "waiting", status_label: "Waiting for approval",
  file_name: "TEST ONLY old customers.xlsx", row_count: 3, created: 0, prepared_by: "Admin", prepared_at: "2026-11-03T09:00:00Z",
  decided_by: "", decided_at: "", decision_note: "",
  sample: [{ row: 1, name: "TEST ONLY Ahmed Al-Kuwari", qid: "28763400123", contact_en: "", register_for: "retail", client_number: "C-004512", project_number: "" }],
  can_approve: true, can_withdraw: false, ...extra,
});
const overview = () => ({
  kinds: [{ kind: "customers", label: "Customers", columns: [{ heading: "Name", required: true, help: "The customer's full name." }] },
    { kind: "suppliers", label: "Suppliers", columns: [{ heading: "Legal name", required: true, help: "As on the CR." }] },
    { kind: "trial_balance", label: "Opening trial balance", needs_date: true, columns: [{ heading: "Account", required: true, help: "TEST ONLY" }] }],
  waiting: [batch(1)], recent: [batch(2, { status: "imported", status_label: "Imported", created: 3, decided_by: "Roche", decided_at: "2026-11-03T10:00:00Z", can_approve: false })],
  can_prepare: true, can_approve: true, policy: "TEST ONLY policy", opening_left: [{ business_unit: "TVL", left: "125000.00" }],
});
const bad = { kind: "customers", rows: 2, good: 1, bad: 1, more_problems: 0,
  problems: [{ row: 3, problems: ["Name is missing.", "QID must be 11 digits starting with 2 or 3."] }],
  sample: [{ row: 2, name: "TEST ONLY Ahmed", qid: "28763400123", contact_en: "", register_for: "retail", client_number: "C-004512", project_number: "" },
    { row: 3, name: "TEST ONLY ", qid: "123", contact_en: "", register_for: "retail", client_number: "", project_number: "" }] };
const good = { ...bad, good: 2, bad: 0, problems: [] };

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent === text) as HTMLButtonElement | undefined;
const posts = () => calls.filter((call) => call.init?.method === "POST");
function choose(file: File) {
  const input = container.querySelector("input[type=file]") as HTMLInputElement;
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  previewResult = bad;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/governance/imports/preview/") return json(previewResult);
    if (url.pathname === "/api/governance/imports/" && init?.method === "POST") return json(batch(3, { row_count: 2, can_approve: false, can_withdraw: true }), 201);
    if (url.pathname === "/api/governance/imports/") return json(overview());
    if (url.pathname === "/api/governance/imports/1/") return json(batch(1, { status: "imported", created: 3, decided_by: "Roche" }));
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("checking a file shows each row's problems, and a clean file can be sent", async () => {
  act(() => root.render(<GoLiveImports csrfToken="csrf" />));
  await flush();
  assert.equal(container.querySelector("a[download]")?.getAttribute("href"), "/api/governance/imports/template/customers/");
  assert.equal(button("Check the file")!.disabled, true, "Choose a file first");
  await act(async () => choose(new File(["x"], "TEST ONLY old.xlsx")));
  await act(async () => (container.querySelector("form[aria-label='Check a file']") as HTMLFormElement).requestSubmit());
  await flush();
  const sent = posts()[0].init?.body as FormData;
  assert.equal(sent.get("kind"), "customers");
  assert.equal((sent.get("file") as File).name, "TEST ONLY old.xlsx");
  assert.match(container.querySelector("[aria-label='Check result'] [role=status]")?.textContent ?? "", /1 row of 2 has problems\. Fix them in the file and check it again\. Nothing was saved\./);
  assert.match(container.querySelector("[aria-label='Problems by row']")?.textContent ?? "", /3Name is missing\.QID must be 11 digits/);
  assert.equal(button("Send 2 rows for approval"), undefined, "A file with problems cannot be sent");

  previewResult = good;
  await act(async () => (container.querySelector("form[aria-label='Check a file']") as HTMLFormElement).requestSubmit());
  await flush();
  assert.match(container.textContent ?? "", /All 2 rows pass the checks/);
  await act(async () => button("Send 2 rows for approval")!.click());
  await flush();
  assert.equal(posts()[2].url.pathname, "/api/governance/imports/");
  assert.match(container.querySelector(".sp-notice")?.textContent ?? "", /2 customers from old customers\.xlsx sent for approval\. Nothing is created until someone else approves it\./);
});

test("approving a waiting file and rejecting needs a reason", async () => {
  act(() => root.render(<GoLiveImports csrfToken="csrf" />));
  await flush();
  const waiting = container.querySelector("[aria-label='Waiting for approval']")?.textContent ?? "";
  assert.match(waiting, /old customers\.xlsxCustomers3Admin/);
  assert.match(container.querySelector("table[aria-label='Done']")?.textContent ?? "", /Approved by Roche.*3 customers created/);
  await act(async () => button("Reject")!.click());
  assert.equal(button("Confirm reject")!.disabled, true);
  await act(async () => button("Back")!.click());
  await act(async () => button("Approve and import")!.click());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/governance/imports/1/");
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { action: "approve", note: "" });
  assert.match(container.querySelector(".sp-notice")?.textContent ?? "", /3 customers imported from old customers\.xlsx/);
});

test("opening balances need the day they are as at, and show what is still to bring in", async () => {
  act(() => root.render(<GoLiveImports csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector("[aria-label='Opening balances still to bring in']")?.textContent ?? "", /TVL QAR 125,000\.00/);
  const kind = container.querySelector("form[aria-label='Check a file'] select") as HTMLSelectElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value")!.set!.call(kind, "trial_balance");
    kind.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => choose(new File(["x"], "TEST ONLY tb.xlsx")));
  assert.equal(button("Check the file")!.disabled, true, "The day is needed first");
  const date = container.querySelector("input[type=date]") as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!.call(date, "2026-12-31");
    date.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => (container.querySelector("form[aria-label='Check a file']") as HTMLFormElement).requestSubmit());
  await flush();
  const sent = posts()[0].init?.body as FormData;
  assert.deepEqual([sent.get("kind"), sent.get("balances_on")], ["trial_balance", "2026-12-31"]);
});
