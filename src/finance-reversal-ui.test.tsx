// @ts-nocheck
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceReversalLab } from "./FinanceReversalLab";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const originalFetch = globalThis.fetch;
const originalSearch = window.location.search;
const context = {
  legal_entity_label: "Synthetic review entity",
  accounts: [{ id: 31, code: "6100", name_en: "Materials", name_ar: "" }, { id: 32, code: "2200", name_en: "Clearing", name_ar: "" }],
  business_units: [{ id: 41, code: "OPS", name: "Operations" }],
  dimensions: [],
  dimension_values: [{ id: 51, dimension_id: 7, code: "NORTH", label_en: "North", label_ar: "" }],
};
const source = (id = 71, revision = 2) => ({
  id, revision, title_en: `Source journal ${id}`, kind: "journal",
  lines: [{ account_id: 31, account_code: "6100", debit: "125.00", credit: "0.00", memo_en: "Original memo" }],
  review: { debit_total: "125.00", credit_total: "125.00" },
});
function response(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}
const calls: { path: string; method: string; body?: any }[] = [];
function standardFetch(extra: (path: string, method: string, body: any) => Response | Promise<Response> = () => response({})) {
  return async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path, method, body });
    if (path === "/lab/session/") return response({ authenticated: true, csrf_token: "test-token" });
    if (path === "/lab/context/") return response(context);
    if (path === "/lab/journals/") return response({ drafts: [
      { id: 71, title_en: "Source journal 71", revision: 2, kind: "journal" },
      { id: 72, title_en: "Source journal 72", revision: 1, kind: "journal" },
    ] });
    if (path === "/lab/reversals/" && method === "GET") return response({ reversals: [] });
    return extra(path, method, body);
  };
}
async function renderLab(search = "") {
  window.history.replaceState({}, "", `/lab-reversals-ui/${search}`);
  await act(async () => root.render(<FinanceReversalLab />));
  await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
}
async function choose(select: HTMLSelectElement, value: string) {
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}
async function input(label: string, value: string) {
  const field = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLTextAreaElement;
  assert.ok(field, `missing field ${label}`);
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(field), "value")?.set;
    setter?.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
  });
}
async function selectSource() {
  await choose(host.querySelector('[aria-label="Source journal"]')!, "71");
  await choose(host.querySelector('[aria-label="Source revision"]')!, "2");
}
async function enterProposal() {
  await input("Proposed date", "2025-04-12");
  await input("Explicit explanation", "Explicit test reversal proposal");
  for (let index = 0; index < 2; index++) {
    const addLine = [...host.querySelectorAll("button")].find((button) => button.textContent === "Add explicit line");
    await act(async () => addLine?.click());
  }
  await choose(host.querySelector('[aria-label="Account line 1"]')!, "32");
  await input("Debit line 1", "0");
  await input("Credit line 1", "12.34");
  await choose(host.querySelector('[aria-label="Account line 2"]')!, "31");
  await input("Debit line 2", "56.78");
  await input("Credit line 2", "0");
}

beforeEach(() => {
  calls.length = 0;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  window.history.replaceState({}, "", "/lab-reversals-ui/");
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  globalThis.fetch = originalFetch;
  window.history.replaceState({}, "", `/lab-reversals-ui/${originalSearch}`);
});

test("new proposal has no inferred date, source, lines, accounts, or amounts", async () => {
  globalThis.fetch = standardFetch((path) => {
    if (path === "/lab/journals/71/") return response({ ...source(), history: [] });
    if (path === "/lab/reversal-sources/71/2/") return response({ source: source() });
    return response({ detail: "Unexpected" }, 404);
  });
  await renderLab();
  assert.match(host.textContent ?? "", /Non-posting synthetic proposal/);
  assert.match(host.textContent ?? "", /not an operational JournalEntry/i);
  assert.equal((host.querySelector('[aria-label="Proposed date"]') as HTMLInputElement).value, "");
  assert.equal((host.querySelector('[aria-label="Source journal"]') as HTMLSelectElement).value, "");
  assert.equal(host.querySelectorAll('[aria-label^="Account line"]').length, 0);
  await selectSource();
  assert.match(host.textContent ?? "", /Original memo/);
  assert.equal(host.querySelectorAll('[aria-label^="Account line"]').length, 0);
  assert.equal((host.querySelector('[aria-label="Proposed date"]') as HTMLInputElement).value, "");
});

test("late journal detail response cannot restore a source after a newer explicit selection", async () => {
  let resolveFirst!: (value: Response) => void;
  globalThis.fetch = standardFetch((path) => {
    if (path === "/lab/journals/71/") return new Promise<Response>((resolve) => { resolveFirst = resolve; });
    if (path === "/lab/journals/72/") return response({ ...source(72, 1), history: [] });
    if (path === "/lab/reversal-sources/72/1/") return response({ source: source(72, 1) });
    return response({ detail: "Unexpected" }, 404);
  });
  await renderLab();
  const journal = host.querySelector('[aria-label="Source journal"]') as HTMLSelectElement;
  await act(async () => { journal.value = "71"; journal.dispatchEvent(new Event("change", { bubbles: true })); });
  await choose(journal, "72");
  await act(async () => { resolveFirst({ ...response({ ...source(71, 2), history: [] }) }); await new Promise((resolve) => setTimeout(resolve, 0)); });
  await choose(host.querySelector('[aria-label="Source revision"]')!, "1");
  const sourceRegion = host.querySelector('[aria-label="Read-only source journal snapshot"]')?.textContent ?? "";
  assert.match(sourceRegion, /Source journal 72/);
  assert.doesNotMatch(sourceRegion, /Source journal 71/);
});

test("clearing source selection cancels stale source loading and permits a later fresh selection", async () => {
  let resolveFirst!: (value: Response) => void;
  let sourceRequests = 0;
  globalThis.fetch = standardFetch((path) => {
    if (path === "/lab/journals/71/") return response({ ...source(71, 2), history: [] });
    if (path === "/lab/reversal-sources/71/2/") {
      sourceRequests++;
      if (sourceRequests === 1) return new Promise<Response>((resolve) => { resolveFirst = resolve; });
      return response({ source: source(71, 2) });
    }
    return response({ detail: "Unexpected" }, 404);
  });
  await renderLab();
  const journal = host.querySelector('[aria-label="Source journal"]') as HTMLSelectElement;
  await choose(journal, "71");
  await choose(host.querySelector('[aria-label="Source revision"]')!, "2");
  assert.match(host.textContent ?? "", /Loading selected source revision/);
  await choose(journal, "");
  assert.doesNotMatch(host.textContent ?? "", /Loading selected source revision/);
  resolveFirst(response({ source: source(71, 2) }));
  await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
  const snapshotRegion = host.querySelector('[aria-label="Read-only source journal snapshot"]')?.textContent ?? "";
  assert.doesNotMatch(snapshotRegion, /Source journal 71/);
  await choose(journal, "71");
  await choose(host.querySelector('[aria-label="Source revision"]')!, "2");
  assert.match(host.querySelector('[aria-label="Read-only source journal snapshot"]')?.textContent ?? "", /Source journal 71/);
});

test("verified-source mismatch blocks saving and cannot be repaired by stale display", async () => {
  globalThis.fetch = standardFetch((path) => {
    if (path === "/lab/journals/71/") return response({ ...source(71, 2), history: [{ revision: 1 }] });
    if (path === "/lab/reversal-sources/71/1/") return response({ source: source(72, 1) });
    return response({ detail: "Unexpected" }, 404);
  });
  await renderLab();
  await choose(host.querySelector('[aria-label="Source journal"]')!, "71");
  await choose(host.querySelector('[aria-label="Source revision"]')!, "1");
  await enterProposal();
  assert.match(host.textContent ?? "", /does not match the selected journal/);
  assert.match(host.textContent ?? "", /Load and verify the exact selected source snapshot/);
  assert.doesNotMatch(host.textContent ?? "", /Enter a valid proposed date|at least two proposed lines/);
  assert.equal([...host.querySelectorAll("button")].find((button) => button.textContent === "Save reversal proposal")?.disabled, true);
  assert.equal(calls.some((call) => call.path === "/lab/reversals/" && call.method === "POST"), false);
});

test("save sends only manually entered reversal lines and retains English draft after validation error", async () => {
  let posted: any;
  globalThis.fetch = standardFetch((path, method, body) => {
    if (path === "/lab/journals/71/") return response({ ...source(71, 2), history: [] });
    if (path === "/lab/reversal-sources/71/2/") return response({ source: source(71, 2) });
    if (path === "/lab/reversals/" && method === "POST") { posted = body; return response({ detail: "Validation rejected" }, 400); }
    return response({ detail: "Unexpected" }, 404);
  });
  await renderLab();
  await selectSource();
  await enterProposal();
  const save = [...host.querySelectorAll("button")].find((button) => button.textContent === "Save reversal proposal") as HTMLButtonElement;
  assert.equal(save.disabled, false);
  await act(async () => { save.click(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  assert.ok(posted, host.textContent);
  assert.equal(posted.source_journal_id, 71);
  assert.equal(posted.source_revision, 2);
  assert.deepEqual(posted.lines, [{
    account_id: 32, business_unit_id: null, dimension_value_ids: [],
    debit: "0", credit: "12.34",
  }, {
    account_id: 31, business_unit_id: null, dimension_value_ids: [],
    debit: "56.78", credit: "0",
  }]);
  assert.equal(posted.proposed_date, "2025-04-12");
  assert.equal(posted.explanation, "Explicit test reversal proposal");
  assert.equal((host.querySelector('[aria-label="Credit line 1"]') as HTMLInputElement).value, "12.34");
  assert.equal((host.querySelector('[aria-label="Explicit explanation"]') as HTMLTextAreaElement).value, "Explicit test reversal proposal");
  assert.match(host.textContent ?? "", /Inputs remain unchanged/);
  assert.doesNotMatch(JSON.stringify(posted), /Original memo|125\.00.*125\.00/);
});

test("reopen uses exact linked source and conflict preserves edits until confirmed reload", async () => {
  const detail = {
    id: 9, revision: 3, source_journal_id: 71, source_revision: 2, proposed_date: "2025-04-12",
    explanation: "Saved English explanation", lines: [
      { account_id: 32, business_unit_id: null, dimension_value_ids: [], debit: "0.00", credit: "125.00" },
      { account_id: 31, business_unit_id: null, dimension_value_ids: [], debit: "125.00", credit: "0.00" },
    ],
    source: source(71, 2),
    review: { currency: "QAR", debit_total: "0.00", credit_total: "125.00", difference: "-125.00", balanced: false,
      issues: ["Explicit server issue"], offsets_source: false, comparison: "Arithmetic net comparison only.", bucket_differences: [] },
    history: [{ revision: 3, created_at: "2025-04-12", snapshot: { source_journal_id: 71, source_revision: 2,
      proposed_date: "2025-04-12", explanation: "Saved English explanation", lines: [
        { account_id: 32, business_unit_id: null, dimension_value_ids: [], debit: "0.00", credit: "125.00" },
        { account_id: 31, business_unit_id: null, dimension_value_ids: [], debit: "125.00", credit: "0.00" },
      ] } }],
  };
  let detailCalls = 0;
  let loseEditAcknowledgement = false;
  globalThis.fetch = standardFetch((path, method) => {
    if (path === "/lab/reversals/9/" && method === "PUT") {
      if (loseEditAcknowledgement) throw new Error("Synthetic edit acknowledgement unavailable");
      return response({ detail: "Revision conflict" }, 409);
    }
    if (path === "/lab/reversals/9/") { detailCalls++; return response(detail); }
    return response({ detail: "Unexpected" }, 404);
  });
  await renderLab("?reopen=9");
  assert.ok(calls.some((call) => call.path === "/lab/reversals/9/"));
  assert.match(host.textContent ?? "", /Saved review/);
  assert.match(host.textContent ?? "", /Not approved\. Not posted\./i);
  assert.equal((host.querySelector('[aria-label="Source revision"]') as HTMLSelectElement).value, "2");
  await input("Explicit explanation", "Keep my unsaved English edit");
  assert.match(host.textContent ?? "", /Unsaved inputs are not included in this saved review/);
  assert.match(host.textContent ?? "", /No comparison has been calculated for the current unsaved inputs/);
  const save = [...host.querySelectorAll("button")].find((button) => button.textContent === "Save reversal revision") as HTMLButtonElement;
  await act(async () => { save.click(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  assert.match(host.textContent ?? "", /Saved revision changed/);
  assert.equal((host.querySelector('[aria-label="Explicit explanation"]') as HTMLTextAreaElement).value, "Keep my unsaved English edit");
  const originalConfirm = window.confirm;
  window.confirm = () => true;
  const reload = [...host.querySelectorAll("button")].find((button) => button.textContent === "Reload saved reversal") as HTMLButtonElement;
  await act(async () => { reload.click(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  window.confirm = originalConfirm;
  assert.equal(detailCalls, 2);
  assert.equal((host.querySelector('[aria-label="Explicit explanation"]') as HTMLTextAreaElement).value, "Saved English explanation");
  loseEditAcknowledgement = true;
  await input("Explicit explanation", "Uncertain local edit remains");
  const edit = [...host.querySelectorAll("button")].find((button) => button.textContent === "Save reversal revision") as HTMLButtonElement;
  await act(async () => { edit.click(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  assert.match(host.textContent ?? "", /Create-request status and replay do not recover edits/);
  assert.equal((host.querySelector('[aria-label="Explicit explanation"]') as HTMLTextAreaElement).value, "Uncertain local edit remains");
  assert.equal(calls.filter((call) => call.method === "PUT").length, 2);
  assert.equal(calls.filter((call) => call.method === "POST").length, 0);
});
