import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import StockCounts from "../src/StockCounts";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let requests: { url: string; method: string; body: unknown; headers: Record<string, string> }[];
let approver: boolean;
let stage: "review" | "differences" | "finance";

const location = { id: 4, code: "STIV-G", name: "Rayyan Showroom - Tivoli showroom - Ground Floor" };
const summary = {
  id: 9, location, status: "counting", status_label: "Counting", note: "", opened_by: { id: 1, username: "test_counter" },
  opened_at: "2026-10-24T08:00:00Z", submitted_at: null, decided_by: null, decided_at: null, line_count: 1, counted_lines: 0,
  open_differences: 0, waiting_for_finance: 0,
};
const blindLine = { id: 31, sku: "TVF-002-LIN", item: "Synthetic Tivoli dining chair", brand: "Tivoli", counted_quantity: null, counted_by: null, found_during_count: false };
function detail() {
  if (!approver) return { ...summary, decision_note: "", blind: true, lines: [blindLine], can_count: true, can_decide: false, is_involved: false, can_approve_write_off: false, history: [] };
  if (stage !== "review") return {
    ...summary, status: "differences", status_label: "Differences waiting for action", counted_lines: 1, decision_note: "", blind: false,
    lines: [{
      ...blindLine, counted_quantity: "8.00", counted_by: "test_counter", expected_quantity: "10.00", erp_quantity: "10.00", difference: "-2.00",
      moved_to_missing: "2.00", surplus_quantity: "0.00", surplus_value: "0.00", written_off_value: "0.00",
      action: stage === "finance" ? "write_off" : "", action_label: stage === "finance" ? "Write off" : "",
      action_status: stage === "finance" ? "requested" : "", action_status_label: stage === "finance" ? "Waiting for Finance" : "Needs an action",
      action_note: stage === "finance" ? "Broken in storage" : "", action_requested_by: stage === "finance" ? { id: 2, username: "test_manager" } : null,
      action_decided_by: null, action_decision_note: "",
      allowed_actions: stage === "finance" ? [] : ["write_off", "found"], can_decide_action: stage === "finance",
    }],
    can_count: false, can_decide: false, is_involved: false, can_approve_write_off: stage === "finance", history: [],
  };
  return {
    ...summary, status: "submitted", status_label: "Waiting for review", counted_lines: 1, decision_note: "", blind: false,
    lines: [{ ...blindLine, counted_quantity: "8.00", counted_by: "test_counter", expected_quantity: "10.00", erp_quantity: "10.00", difference: "-2.00" }],
    can_count: false, can_decide: true, is_involved: false, can_approve_write_off: false, history: [{ event: "submitted", actor: "test_counter", note: "", at: "2026-10-24T09:00:00Z" }],
  };
}

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (label: string) => [...container.querySelectorAll("button")].find((item) => item.textContent === label) as HTMLButtonElement;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  requests = [];
  approver = false;
  stage = "review";
  globalThis.fetch = (async (input: RequestInfo | URL, options: RequestInit = {}) => {
    const url = String(input);
    const method = options.method ?? "GET";
    requests.push({ url, method, body: options.body ? JSON.parse(String(options.body)) : undefined, headers: (options.headers ?? {}) as Record<string, string> });
    if (url.startsWith("/api/inventory/stock-counts/?")) return json({
      unit: { id: 1, code: "TVL", name: "Tivoli" }, permissions: { can_count: !approver, can_approve: approver, can_approve_write_off: stage === "finance" },
      locations: [{ ...location, has_open_count: true }, { id: 5, code: "W41-01", name: "Street 41 - Building 01", has_open_count: false }],
      counts: [summary],
    });
    if (url.includes("/approve/")) { stage = "differences"; return json({ count_id: 9, status: "differences", moved_to_missing: "2.00", held_surplus: "0.00" }); }
    if (url.includes("/lines/")) return json(detail());
    if (url.includes("/counts/")) return json({ ...detail(), counted_lines: 1, lines: [{ ...blindLine, counted_quantity: "8.00", counted_by: "test_counter" }] });
    return json(detail());
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("a counter starts from the list, counts blind and saves only what changed", async () => {
  act(() => root.render(<StockCounts selectedUnitId={1} csrfToken="TEST-CSRF" />));
  await flush();
  const options = [...container.querySelectorAll(".sc-start option")].map((option) => option.textContent);
  assert.deepEqual(options, ["Choose a location", "W41-01 · Street 41 - Building 01"], "A location with an open count cannot be started again");
  await act(async () => button("#9").click());
  await flush();
  assert.deepEqual([...container.querySelectorAll(".sc-table thead th")].map((cell) => cell.textContent), ["Item", "Counted"], "No ERP quantity while counting");
  const input = container.querySelector("input[aria-label='Counted quantity for TVF-002-LIN']") as HTMLInputElement;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(input, "8");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => button("Save counts").click());
  await flush();
  const save = requests.find((request) => request.method === "POST")!;
  assert.equal(save.url, "/api/inventory/stock-counts/9/counts/?unit=1");
  assert.deepEqual(save.body, { entries: [{ line_id: 31, counted: "8" }] });
  assert.equal(save.headers["X-CSRFToken"], "TEST-CSRF");
  assert.match(container.querySelector(".sc-notice")?.textContent ?? "", /Saved 1 count/);
});

test("the warehouse manager sees the live difference and confirms with an idempotency key", async () => {
  approver = true;
  act(() => root.render(<StockCounts selectedUnitId={1} csrfToken="TEST-CSRF" />));
  await flush();
  assert.equal(container.querySelector(".sc-start"), null, "Approvers without the count permission cannot start counts");
  await act(async () => button("#9").click());
  await flush();
  assert.match(container.querySelector(".sc-short")?.textContent ?? "", /2 short/);
  assert.match(container.querySelector(".sc-decide")?.textContent ?? "", /enter any deliveries or transfers made by hand/);
  assert.ok(button("Refresh figures"));
  assert.doesNotMatch(container.textContent ?? "", /frozen/);
  await act(async () => button("Confirm differences").click());
  await flush();
  const approve = requests.find((request) => request.url.includes("/approve/"))!;
  assert.ok(approve.headers["Idempotency-Key"]);
  assert.match(container.querySelector(".sc-notice")?.textContent ?? "", /Each one now needs an action/);
  assert.match(container.querySelector(".sc-action")?.textContent ?? "", /Needs an action/);
});

async function type(input: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

test("the manager asks for a write-off with a reason and Finance approves it", async () => {
  approver = true;
  stage = "differences";
  act(() => root.render(<StockCounts selectedUnitId={1} csrfToken="TEST-CSRF" />));
  await flush();
  await act(async () => button("#9").click());
  await flush();
  assert.ok(button("Found, move back"));
  await type(container.querySelector("input[aria-label='Reason for TVF-002-LIN']") as HTMLInputElement, "Broken in storage");
  await act(async () => button("Write off").click());
  await flush();
  const asked = requests.find((request) => request.url.includes("/lines/31/action/"))!;
  assert.deepEqual(asked.body, { action: "write_off", note: "Broken in storage" });
  assert.ok(asked.headers["Idempotency-Key"]);

  stage = "finance";
  await act(async () => button("← All counts").click());
  await act(async () => button("#9").click());
  await flush();
  assert.match(container.querySelector(".sc-action")?.textContent ?? "", /Write off: Waiting for Finance/);
  await act(async () => button("Approve").click());
  await flush();
  const decided = requests.find((request) => request.url.includes("/lines/31/decision/"))!;
  assert.deepEqual(decided.body, { approve: true, note: "" });
  assert.ok(decided.headers["Idempotency-Key"]);
});
