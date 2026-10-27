import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import PackingListPreview from "../src/PackingListPreview";

const nativeFetch = globalThis.fetch;
let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let calls: { url: URL; options: RequestInit }[];

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}

async function upload(name: string, content: string) {
  const input = container.querySelector<HTMLInputElement>('input[aria-label="Choose supplier packing list"]');
  assert.ok(input);
  const bytes = new TextEncoder().encode(content);
  const file = {
    name,
    size: bytes.byteLength,
    arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
  };
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await new Promise(setImmediate);
  });
  await flush();
}

async function mapColumn(label: string, index: number) {
  const select = container.querySelector<HTMLSelectElement>(`select[aria-label="Map ${label}"]`);
  assert.ok(select, `mapping selector exists for ${label}`);
  await act(async () => {
    select.value = String(index);
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await new Promise(setImmediate);
  });
  await flush();
}

function mount(unitId = 1, unitName = `TEST ONLY Unit ${unitId}`) {
  root = createRoot(container);
  act(() => root.render(<PackingListPreview selectedUnitId={unitId} selectedUnitName={unitName} />));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  calls = [];
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, options });
    assert.equal(url.pathname, "/api/stock-catalog-matches/");
    assert.equal(options.method ?? "GET", "GET", "preview sends only a read-only code lookup");
    assert.equal(options.body, undefined, "file contents and preview rows are never uploaded");
    return json({ matched_codes: url.searchParams.getAll("code").filter((code) => code === "MATCH-1") });
  };
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  root = undefined as unknown as ReturnType<typeof createRoot>;
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("CSV preview supports remapped columns, unmatched codes, invalid quantity, and distinct units", async () => {
  mount(12, "TEST ONLY Unit Twelve");
  await upload(
    "supplier-list.csv",
    "Ref,Qty,Area,Item,Description,Unit,Volume,Dimensions\n" +
      "REF-1,2,1.2,MATCH-1,TEST ONLY chair,EA,0.5,raw package dimensions\n" +
      "REF-2,3,2.3,UNKNOWN-2,TEST ONLY table,EA,0.25,another raw dimension\n" +
      "REF-3,0,0.5,BAD-QTY,TEST ONLY stool,EA,0.1,not converted\n",
  );
  await mapColumn("Item description", 4);
  await mapColumn("Quantity", 1);
  await mapColumn("Quantity unit", 5);
  await mapColumn("Supplier line reference", 0);
  await mapColumn("Package dimensions (source text)", 7);
  await mapColumn("Supplier cubic volume (m³)", 6);
  await mapColumn("Supplier area (m²)", 2);
  await mapColumn("Supplier / item code", 3);
  await flush();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url.searchParams.get("unit"), "12");
  assert.deepEqual(calls[0].url.searchParams.getAll("code"), ["MATCH-1", "UNKNOWN-2", "BAD-QTY"]);
  assert.equal(container.textContent.includes("Authorized inventory match"), true);
  assert.equal(container.textContent.includes("No authorized inventory match"), true);
  assert.equal(container.textContent.includes("Quantity must be a positive decimal."), true);
  assert.equal(container.textContent.includes("raw package dimensions"), true);
  assert.deepEqual(
    Array.from(container.querySelectorAll(".packing-summary > div"), (node) => node.textContent),
    ["3Source rows", "2Valid rows", "1Invalid rows", "1Authorized matches", "1Unmatched codes"],
  );
  assert.equal(container.textContent.includes("Supplier cubic volume0.85 m³"), true);
  assert.equal(container.textContent.includes("Warehouse-confirmed volumeNot entered"), true);
  assert.equal(container.textContent.includes("Supplier area4 m²"), true);

  const warehouseInput = container.querySelector<HTMLInputElement>(
    '[aria-label="Warehouse-confirmed volume in m³ for source row 2"]',
  );
  assert.ok(warehouseInput);
  await act(async () => {
    const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    assert.ok(valueSetter);
    valueSetter.call(warehouseInput, "0.125");
    warehouseInput.dispatchEvent(new Event("input", { bubbles: true }));
    warehouseInput.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
  assert.equal(container.textContent.includes("Warehouse-confirmed volume0.125 m³"), true);
  assert.equal(container.textContent.includes("Supplier area4 m²"), true);
  assert.equal(container.textContent.includes("converted or derived from one another"), true);
});

test("empty or malformed local file shows an error and sends no match request", async () => {
  mount();
  await upload("bad.csv", 'Code,Qty\n"unterminated,2');
  assert.match(container.querySelector('[role="alert"]')?.textContent ?? "", /unterminated quoted field/i);
  assert.equal(calls.length, 0);
});

test("changing business unit clears prior code matches and keeps a fresh denial visible", async () => {
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, options });
    if (url.searchParams.get("unit") === "1") return json({ matched_codes: ["MATCH-1"] });
    return json({ detail: "No active business-unit access is granted." }, 403);
  };
  mount(1, "TEST ONLY Unit One");
  await upload("one.csv", "Item,Qty,Unit\nMATCH-1,1,EA\n");
  await mapColumn("Quantity", 1);
  await mapColumn("Quantity unit", 2);
  await mapColumn("Supplier / item code", 0);
  await flush();
  assert.equal(container.textContent.includes("Authorized inventory match"), true);

  await act(async () => root.render(<PackingListPreview selectedUnitId={2} selectedUnitName="TEST ONLY Unit Two" />));
  await flush();
  assert.equal(container.textContent.includes("No active business-unit access is granted."), true);
  assert.equal(container.textContent.includes("Match unavailable"), true);
  assert.equal(container.textContent.includes("Authorized inventory match"), false);
});
