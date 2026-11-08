import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { code128Modules } from "../src/Barcode";
import StockCounts from "../src/StockCounts";
import StockItems from "../src/StockItems";

const nativeFetch = globalThis.fetch;
const nativePrint = window.print;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: URL[];
let printed = 0;

const row = (variant: number, sku: string, quantity: string, extra = {}) => ({
  item_id: 7, variant_id: variant, item_code: "TVF-002", sku, description: "TEST ONLY dining chair", variant_description: variant === 1 ? "linen" : "oak",
  brand: "TEST ONLY Tivoli", company: "TEST ONLY Milan Furniture", photo_url: "", unit: "Piece", name_ar: "كرسي طعام", barcode: "4006381333931",
  locations: quantity === "0.00" ? [] : [{ location_id: 3, code: "STIV-G", name: "Showroom", quantity, free: quantity }], quantity,
  selling_price: "2000.00", is_active: true, ...extra,
});
const rows = [row(1, "TVF-002-LIN", "3.00"), row(2, "TVF-002-OAK", "0.00")];
const list = { unit: { id: 1, code: "TVL", name: "Tivoli" }, total: 2, shown: 2, can_edit: false, can_see_costs: false, currency: "QAR", rows };
const line = { id: 31, sku: "TVF-002-LIN", item: "dining chair", brand: "Tivoli", counted_quantity: null, counted_by: null, found_during_count: false };
const place = { id: 4, code: "STIV-G", name: "Showroom" };
const count = { id: 9, location: place, status: "counting", status_label: "Counting", note: "", opened_by: { id: 1, username: "test_counter" },
  opened_at: "2026-10-24T08:00:00Z", submitted_at: null, decided_by: null, decided_at: null, line_count: 1, counted_lines: 0, open_differences: 0, waiting_for_finance: 0 };

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
function type(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}
async function scan(form: string, code: string) {
  const input = container.querySelector(`form[aria-label='${form}'] input`) as HTMLInputElement;
  await act(async () => type(input, code));
  await act(async () => (container.querySelector(`form[aria-label='${form}']`) as HTMLFormElement).requestSubmit());
  await flush();
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  printed = 0;
  window.print = () => { printed += 1; };
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    calls.push(url);
    if (url.pathname === "/api/inventory/items/") return json(list);
    if (url.pathname === "/api/inventory/scan/") {
      const code = url.searchParams.get("code")!;
      if (code === "4006381333931") return json({ ...list, code, matched_by: "barcode", rows });
      const found = rows.filter((each) => each.sku === code.toUpperCase());
      return found.length ? json({ ...list, code, matched_by: "sku", rows: found }) : json({ detail: `Nothing found for ${code}. Check the label, or search by name.` }, 400);
    }
    if (url.pathname === "/api/inventory/stock-counts/") return json({ unit: list.unit, permissions: { can_count: true, can_approve: false, can_approve_write_off: false },
      locations: [{ ...place, has_open_count: true }], counts: [count] });
    if (url.pathname === "/api/inventory/stock-counts/9/") return json({ ...count, decision_note: "", blind: true, lines: [line], can_count: true, can_decide: false,
      is_involved: false, can_approve_write_off: false, history: [] });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
  window.print = nativePrint;
});

test("the barcode is standard Code 128", () => {
  // Checked against an independent Code 128 implementation.
  assert.equal(code128Modules("TVF-002-LIN"), "110100100001101110001011101011000100011000101001101110010011101100100111011001100111001010011011100100011011101100010001010111000110111100100101100011101011");
  assert.equal(code128Modules("12345678"), "1101001110010110011100100010110001110001011011000010100100011101101100011101011");
});

test("ticked items print as labels, one per piece by default", async () => {
  act(() => root.render(<StockItems selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  await act(async () => (container.querySelector("input[aria-label='Label for TVF-002-LIN']") as HTMLInputElement).click());
  await act(async () => (container.querySelector("input[aria-label='Label for TVF-002-OAK']") as HTMLInputElement).click());
  await act(async () => button("Print labels (2 items)")!.click());
  const copies = container.querySelector("input[aria-label='Copies of TVF-002-LIN']") as HTMLInputElement;
  assert.equal(copies.value, "3", "One label for each piece in stock");
  assert.equal((container.querySelector("input[aria-label='Copies of TVF-002-OAK']") as HTMLInputElement).value, "1", "At least one");
  assert.equal(container.querySelectorAll(".label-print-area .label").length, 4);
  await act(async () => type(copies, "2"));
  assert.equal(container.querySelectorAll(".label-print-area .label").length, 3);
  const first = container.querySelector(".label-print-area .label")!;
  assert.equal(first.querySelector("svg")?.getAttribute("data-value"), "TVF-002-LIN");
  assert.match(first.textContent ?? "", /TEST ONLY dining chair.*linen.*QAR 2,000.*TVF-002-LINTVF-002/);
  await act(async () => (container.querySelector(".label-check input") as HTMLInputElement).click());
  assert.doesNotMatch(first.textContent ?? "", /QAR/, "The price can be left off");
  await act(async () => { const select = container.querySelector(".label-options select") as HTMLSelectElement;
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(select), "value")!.set!.call(select, "tag"); select.dispatchEvent(new Event("change", { bubbles: true })); });
  assert.ok(container.querySelector(".label-tag .label-ar"), "The showroom tag shows the Arabic name");
  await act(async () => button("Print 3 labels")!.click());
  assert.equal(printed, 1);
});

test("scanning finds the item and its stock, by our label or the maker's barcode", async () => {
  act(() => root.render(<StockItems selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  await scan("Scan a label", "tvf-002-lin");
  assert.equal(calls.at(-1)?.searchParams.get("code"), "tvf-002-lin");
  assert.match(container.querySelector("[aria-label='Scanned item']")?.textContent ?? "", /our label.*TVF-002 · TVF-002-LIN.*STIV-G: 3 \(3 free\).*QAR 2,000\.00/);
  await act(async () => button("Add to labels")!.click());
  assert.ok((container.querySelector("input[aria-label='Label for TVF-002-LIN']") as HTMLInputElement).checked);
  await scan("Scan a label", "4006381333931");
  assert.match(container.querySelector("[aria-label='Scanned item']")?.textContent ?? "", /the maker's barcode, 2 finishes/);
  await scan("Scan a label", "NOPE");
  assert.match(container.querySelector("[role=alert]")?.textContent ?? "", /Nothing found for NOPE/);
});

test("scanning during a count adds one piece at a time", async () => {
  act(() => root.render(<StockCounts selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  await act(async () => button("#9")!.click());
  await flush();
  await scan("Scan to count", "TVF-002-LIN");
  await scan("Scan to count", "TVF-002-LIN");
  assert.equal((container.querySelector("input[aria-label='Counted quantity for TVF-002-LIN']") as HTMLInputElement).value, "2");
  assert.match(container.querySelector(".sc-scan-note")?.textContent ?? "", /TVF-002-LIN: 2 counted/);
  await scan("Scan to count", "TVF-002-OAK");
  const found = container.querySelector("form[aria-label='Add an item found here']")!;
  assert.deepEqual([...found.querySelectorAll("input")].map((input) => input.value), ["TVF-002-OAK", "1"], "Not on the list: goes to found items");
  await scan("Scan to count", "4006381333931");
  assert.match(container.querySelector(".sc-scan-note")?.textContent ?? "", /matches 2 finishes/);
});
