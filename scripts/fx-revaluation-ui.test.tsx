import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import FxRevaluationView from "../src/FxRevaluation";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let posted = false;
let canPost = true;

const row = (rate: string | null) => ({ receipt_id: 12, supplier: "TEST ONLY Roche Bobois", what: "Goods from TEST ONLY Roche Bobois", currency: "EUR",
  owed: "1500.00", carried: "6000.00", booked_rate: "4.0000", revalued: rate ? (1500 * Number(rate)).toFixed(2) : null,
  difference: rate ? (1500 * Number(rate) - 6000).toFixed(2) : null });
const run = { month: "2026-10", label: "October 2026", rates: { EUR: "4.10" }, source: "TEST ONLY QCB", net: "150.00", item_count: 1,
  posted_by: "Finance Controller", posted_at: "2026-11-02T08:00:00Z", journal_entry_id: 41, reversal_entry_id: 42, lines: [row("4.10")] };
function overview(rate: string | null) {
  return {
    unit: { id: 1, name: "Tivoli" }, can_post: canPost, policy_label: "TEST ONLY policy",
    months: [{ month: "2026-10", label: "October 2026", revalued: posted, blocked: "" }, { month: "2026-09", label: "September 2026", revalued: false, blocked: "September 2026 is closed. Revalue before closing the month." }],
    selected: posted ? { month: "2026-10", label: "October 2026", last_day: "2026-10-31", blocked: "October 2026 is already revalued.", can_post: false, run }
      : { month: "2026-10", label: "October 2026", last_day: "2026-10-31", blocked: "", can_post: canPost, currencies: ["EUR"],
        last_rates: { EUR: { rate: "3.95", date: "2026-10-20", source: "TEST ONLY bank" } }, rows: [row(rate)],
        summary: { loss: rate && Number(rate) > 4 ? (1500 * Number(rate) - 6000).toFixed(2) : "0.00", gain: rate && Number(rate) < 4 ? (6000 - 1500 * Number(rate)).toFixed(2) : "0.00",
          net: rate ? (1500 * Number(rate) - 6000).toFixed(2) : "0.00", complete: !!rate } },
    history: posted ? [run] : [],
  };
}

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 6; index += 1) await act(async () => { await new Promise((done) => setTimeout(done, 80)); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
const input = (label: string) => [...container.querySelectorAll("label")].find((node) => node.textContent?.startsWith(label))?.querySelector("input") as HTMLInputElement;
function setValue(target: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(target, value);
  target.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  posted = false;
  canPost = true;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname !== "/api/finance/ledger/fx-revaluation/") return json({ detail: "not used" }, 404);
    if (init?.method === "POST") { posted = true; return json(overview(null)); }
    return json(overview(url.searchParams.get("rate_EUR")));
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the last rate is offered, a new rate shows the loss, and posting needs a source", async () => {
  act(() => root.render(<FxRevaluationView unitId={1} csrfToken="csrf" />));
  await flush();
  assert.equal(input("EUR").value, "3.95", "Starts from the last rate used");
  assert.match(container.querySelector(".fx-total")?.textContent ?? "", /unrealised gain of QAR 75\.00/);
  await act(async () => setValue(input("EUR"), "4.10"));
  await flush();
  assert.equal(calls.at(-1)?.url.searchParams.get("rate_EUR"), "4.10");
  assert.match(container.querySelector("[aria-label='Owed in foreign currency at the end of October 2026']")?.textContent ?? "", /EUR 1,500\.00.*6,000\.00.*6,150\.00.*150\.00/);
  assert.match(container.querySelector(".fx-total")?.textContent ?? "", /unrealised loss of QAR 150\.00/);
  const post = button("Post revaluation for October 2026")!;
  assert.equal(post.disabled, true, "Needs the rate source");
  await act(async () => setValue(input("Where the rates come from"), "TEST ONLY QCB"));
  assert.equal(post.disabled, false);
  await act(async () => (container.querySelector("form.fx-form") as HTMLFormElement).requestSubmit());
  await flush();
  const sent = calls.find((call) => call.init?.method === "POST")!;
  assert.deepEqual(JSON.parse(String(sent.init?.body)), { month: "2026-10", rates: { EUR: "4.10" }, source: "TEST ONLY QCB" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /October 2026 revalued: loss of QAR 150\.00, reversed on the first day/);
  assert.match(container.querySelector(".fx-run")?.textContent ?? "", /Journal entries 41 and 42 \(reversal\)/);
  assert.match(container.querySelector(".fs-history")?.textContent ?? "", /October 2026.*loss of QAR 150\.00.*EUR 4\.10/);
});

test("a closed month says why, and without permission there is no post button", async () => {
  canPost = false;
  act(() => root.render(<FxRevaluationView unitId={1} csrfToken="csrf" />));
  await flush();
  assert.equal(button("Post revaluation"), undefined);
  assert.match(container.textContent ?? "", /posting needs the “Close months” permission/);
  await act(async () => button("September 2026")!.click());
  await flush();
  assert.equal(calls.at(-1)?.url.searchParams.get("month"), "2026-09");
});
