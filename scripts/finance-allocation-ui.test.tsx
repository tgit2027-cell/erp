import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceAllocationLab } from "../src/FinanceAllocationLab";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let requests: { path: string; method: string; body?: any }[];
let handler: ((call: { path: string; method: string; body?: any }) => Promise<any> | any) | null;
let saved: any;
const nativeFetch = globalThis.fetch;
const context = {
  legal_entity_label: "Synthetic reviewer entity",
  business_units: [{ id: 7, code: "TEST-BU-7", name: "Review unit" }],
  accounts: [
    { id: 11, code: "TEST-ACCT-11", name_en: "Expense account", name_ar: "" },
    { id: 12, code: "TEST-ACCT-12", name_en: "Clearing account", name_ar: "" },
  ],
  dimensions: [], dimension_values: [],
};

function makeSaved(revision = 1, title = "Saved allocation", body?: any) {
  const base = {
    id: 41, title_en: title, title_ar: body?.title_ar ?? "عنوان محفوظ", revision,
    allocation_proposal: body?.allocation_proposal ?? {
      source_amount: "0.30", currency: "QAR", date: "2026-07-14", explanation: "Explicit test allocation.",
      destinations: [{ amount: "0.10", account_id: 11, business_unit_id: null, dimension_value_ids: [] },
        { amount: "0.20", account_id: 12, business_unit_id: 7, dimension_value_ids: [] }],
    },
    lines: body?.lines ?? [
      { account_id: 11, business_unit_id: null, dimension_value_ids: [], debit: "0.30", credit: "0.00", memo_en: "Review", memo_ar: "مراجعة" },
      { account_id: 12, business_unit_id: null, dimension_value_ids: [], debit: "0.00", credit: "0.30", memo_en: "", memo_ar: "" },
    ],
    review: {
      source_amount: body?.allocation_proposal?.source_amount ?? "0.30",
      allocated_total: "0.30", difference: "0.00", state: "fully_allocated", currency: "QAR",
      journal: { debit_total: "0.30", credit_total: "0.30", difference: "0.00", balanced: true, posted: false, issues: [] },
    },
  };
  return { ...base, history: [{ revision, created_at: "synthetic test time", snapshot: base }] };
}

beforeEach(async () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  requests = []; handler = null; saved = makeSaved();
  globalThis.fetch = async (input: RequestInfo | URL, options: RequestInit = {}) => {
    const call = { path: String(input), method: String(options.method ?? "GET"), body: options.body ? JSON.parse(String(options.body)) : undefined };
    requests.push(call);
    const custom = await handler?.(call);
    if (custom) return new Response(JSON.stringify(custom.data), { status: custom.status ?? 200 });
    if (call.path === "/lab/session/") return new Response(JSON.stringify({ authenticated: true, csrf_token: "TEST" }));
    if (call.path === "/lab/context/") return new Response(JSON.stringify(context));
    if (call.path === "/lab/allocations/" && call.method === "GET") return new Response(JSON.stringify({ proposals: [{ id: saved.id, title_en: saved.title_en, revision: saved.revision }] }));
    if (call.path === "/lab/allocations/41/" && call.method === "GET") return new Response(JSON.stringify(saved));
    if (call.method === "POST" || call.method === "PUT") {
      const revision = call.method === "POST" ? 1 : saved.revision + 1;
      saved = makeSaved(revision, call.body.title_en, call.body);
      return new Response(JSON.stringify(saved));
    }
    return new Response(JSON.stringify({ detail: "Unexpected test endpoint." }), { status: 404 });
  };
  await act(async () => { root.render(<FinanceAllocationLab />); });
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); globalThis.fetch = nativeFetch;
});

function button(prefix: string) {
  const found = [...host.querySelectorAll("button")].find((node) => node.textContent?.startsWith(prefix));
  assert.ok(found, `Missing button ${prefix}`); return found as HTMLButtonElement;
}
async function click(prefix: string) { await act(async () => button(prefix).click()); }
function field(label: string) {
  const input = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement | null;
  assert.ok(input, `Missing accessible control: ${label}`); return input;
}
async function fill(label: string, value: string) {
  const input = field(label);
  await act(async () => {
    const proto = input.tagName === "TEXTAREA" ? input.ownerDocument.defaultView!.HTMLTextAreaElement.prototype
      : input.tagName === "SELECT" ? input.ownerDocument.defaultView!.HTMLSelectElement.prototype
        : input.ownerDocument.defaultView!.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(input, value);
    input.dispatchEvent(new input.ownerDocument.defaultView!.Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}
async function createValidProposal(source = "0.30", dest1 = "0.10", dest2 = "0.20") {
  await click("New allocation proposal");
  await fill("Allocation title", "Explicit monthly allocation");
  await fill("Allocation source amount", source);
  await fill("Allocation currency", "QAR");
  await fill("Allocation proposed date", "2026-07-14");
  await fill("Allocation explanation", "Explicit proposal for reviewer consideration.");
  await fill("Destination amount 1", dest1);
  await fill("Destination account 1", "11");
  await click("Add destination");
  await fill("Destination amount 2", dest2);
  await fill("Destination account 2", "12");
  await fill("Allocation journal account 1", "11");
  await fill("Allocation debit 1", "0.30");
  await fill("Allocation credit 1", "0");
  await fill("Allocation journal account 2", "12");
  await fill("Allocation debit 2", "0");
  await fill("Allocation credit 2", "0.30");
}

test("exact decimal destinations render 0.10 + 0.20 and clearly show permitted remaining amount", async () => {
  await createValidProposal("0.31", "0.10", "0.20");
  assert.match(host.textContent ?? "", /Unallocated remainder\s*0\.01/);
  assert.match(host.textContent ?? "", /Remaining — not fully allocated/);
  await click("Review allocation proposal");
  assert.equal(button("Save allocation proposal").disabled, false);
  await click("Save allocation proposal");
  const post = requests.find((call) => call.method === "POST")!;
  assert.equal(post.path, "/lab/allocations/");
  assert.deepEqual(post.body.allocation_proposal.destinations.map((row: any) => row.amount), ["0.10", "0.20"]);
  assert.equal(post.body.allocation_proposal.source_amount, "0.31");
  assert.equal(post.body.lines.length, 2);
  assert.equal(host.textContent?.includes("Saved allocation proposal 41, revision 1"), true);
  assert.equal(post.body.allocation_proposal.currency, "QAR");
});

test("rejects overallocated, missing-currency and unsupported-currency inputs before write", async () => {
  await createValidProposal("0.29", "0.10", "0.20");
  assert.match(host.textContent ?? "", /Overallocated by\s*0\.01/);
  assert.match(host.textContent ?? "", /destination total exceeds the source/i);
  await click("Review allocation proposal");
  assert.match(host.textContent ?? "", /Destination total cannot exceed the source amount/);
  assert.equal(requests.filter((row) => ["POST", "PUT"].includes(row.method)).length, 0);
  await fill("Allocation source amount", "0.30");
  await fill("Allocation currency", "");
  await click("Review allocation proposal");
  assert.match(host.textContent ?? "", /Choose an explicit allocation currency/);
  await fill("Allocation currency", "USD");
  await click("Review allocation proposal");
  assert.match(host.textContent ?? "", /Only QAR is supported/);
  assert.equal(requests.filter((row) => ["POST", "PUT"].includes(row.method)).length, 0);
});

test("Arabic business fields survive reopen and English-only editing without being rewritten", async () => {
  await click("Open allocation proposal 41:");
  assert.equal((field("Allocation title Arabic (optional)") as HTMLInputElement).value, "عنوان محفوظ");
  assert.equal((field("Allocation journal memo Arabic (optional) 1") as HTMLInputElement).value, "مراجعة");
  await fill("Allocation explanation", "Changed English explanation only.");
  await click("Review allocation proposal");
  await click("Save allocation proposal");
  const write = requests.filter((call) => call.method === "PUT").at(-1)!;
  assert.equal(write.body.title_ar, "عنوان محفوظ");
  assert.equal(write.body.lines[0].memo_ar, "مراجعة");
  window.confirm = () => true;
  await click("Open allocation proposal 41:");
  assert.equal((field("Allocation title Arabic (optional)") as HTMLInputElement).value, "عنوان محفوظ");
  assert.equal((field("Allocation journal memo Arabic (optional) 1") as HTMLInputElement).value, "مراجعة");
});

test("validates exact minor-unit math and destination amount ceiling", async () => {
  await createValidProposal("0.30", "0.10", "0.20");
  await click("Review allocation proposal");
  assert.equal(button("Save allocation proposal").disabled, false);
  await fill("Destination amount 1", "1000000000000.00");
  await click("Review allocation proposal");
  assert.match(host.textContent ?? "", /Destination 1 amount must be positive/);
  await fill("Destination amount 1", "0.10");
  await fill("Allocation source amount", "0.30");
  await fill("Destination amount 2", "0.20");
});

test("saved proposal reopens with destination identities, edit uses expected revision and preserves history", async () => {
  await click("Open allocation proposal 41:");
  assert.equal((field("Allocation title") as HTMLInputElement).value, "Saved allocation");
  assert.equal((field("Destination amount 1") as HTMLInputElement).value, "0.10");
  assert.ok(host.querySelector('[aria-label="Allocation proposal revision history"]'));
  await fill("Allocation explanation", "Revised rationale.");
  await click("Review allocation proposal");
  await click("Save allocation proposal");
  const put = requests.filter((call) => call.method === "PUT").at(-1)!;
  assert.equal(put.path, "/lab/allocations/41/");
  assert.equal(put.body.expected_revision, 1);
  assert.equal(put.body.allocation_proposal.explanation, "Revised rationale.");
  assert.equal(host.textContent?.includes("Saved allocation proposal 41, revision 2"), true);
});

test("409 and unknown save outcomes retain edits; unknown outcome blocks duplicate submission and recovers by reopen", async () => {
  await click("Open allocation proposal 41:");
  await fill("Allocation explanation", "Keep this exact local text.");
  await click("Review allocation proposal");
  handler = (call) => call.method === "PUT" ? { status: 409, data: { detail: "Stale revision." } } : null;
  await click("Save allocation proposal");
  assert.equal((field("Allocation explanation") as HTMLTextAreaElement).value, "Keep this exact local text.");
  assert.match(host.textContent ?? "", /Conflicting edit/);
  assert.equal(button("Save allocation proposal").disabled, true);
  assert.equal(requests.filter((call) => call.method === "PUT").length, 1);
  window.confirm = () => true;
  await click("Reload saved allocation");
  await fill("Allocation explanation", "Second save outcome is unknown.");
  await click("Review allocation proposal");
  handler = (call) => {
    if (call.method === "PUT") throw new TypeError("connection ended after commit");
    return null;
  };
  await click("Save allocation proposal");
  assert.equal(button("Save allocation proposal").disabled, true);
  assert.match(host.textContent ?? "", /database may have committed/);
  assert.equal(requests.filter((call) => call.method === "PUT").length, 2);
  await click("Refresh saved list");
  window.confirm = () => true;
  await click("Open allocation proposal 41:");
  assert.equal((field("Allocation explanation") as HTMLTextAreaElement).value, "Explicit test allocation.");
  assert.equal(requests.filter((call) => call.method === "PUT").length, 2);
});

test("server validation, list refresh failure and history preserve successful save semantics", async () => {
  await createValidProposal();
  await click("Review allocation proposal");
  handler = (call) => {
    if (call.method === "POST") return { status: 400, data: { detail: "Invalid saved choice." } };
    return null;
  };
  await click("Save allocation proposal");
  assert.match(host.textContent ?? "", /Invalid saved choice/);
  assert.equal((field("Allocation title") as HTMLInputElement).value, "Explicit monthly allocation");
  assert.equal(requests.filter((call) => call.method === "POST").length, 1);
  handler = (call) => call.path === "/lab/allocations/" && call.method === "GET"
    ? { status: 503, data: { detail: "List refresh unavailable." } } : null;
  await click("Save allocation proposal");
  assert.match(host.textContent ?? "", /Allocation saved, but list refresh failed/);
});

test("session denial clears protected allocation data; inaccessible setup is never seeded", async () => {
  handler = (call) => call.path === "/lab/context/" ? { status: 403, data: { detail: "Synthetic context denied." } } : null;
  await click("Open allocation proposal 41:");
  assert.match(host.textContent ?? "", /Synthetic test access is unavailable/);
  assert.doesNotMatch(host.textContent ?? "", /Inputs remain unchanged/);
  assert.equal(host.querySelector(".allocation-editor"), null);
  assert.equal(requests.filter((call) => call.method === "POST").length, 0);
});
