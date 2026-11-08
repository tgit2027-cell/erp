import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import StockItems from "../src/StockItems";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let rights = { edit: true, costs: true };
const PHOTO = "/api/items/7/photo/0123456789abcdef.png";

const row = (variant: number, sku: string, extra = {}) => ({
  item_id: 7, variant_id: variant, item_code: "TVF-002", sku, description: "TEST ONLY dining chair", variant_description: "linen",
  brand: "TEST ONLY Tivoli", company: "TEST ONLY Milan Furniture", photo_url: PHOTO, unit: "Piece",
  locations: [{ location_id: 3, code: "STIV-G", name: "Showroom", quantity: "4.00", free: "3.00" }], quantity: "4.00",
  selling_price: "2000.00", is_active: true, ...extra,
});
const list = () => ({
  unit: { id: 1, code: "TVL", name: "Tivoli" }, total: 2, shown: 2, can_edit: rights.edit, can_see_costs: rights.costs, currency: "QAR",
  rows: [
    row(1, "TVF-002-LIN", rights.costs ? { costs: { stock_value: "4400.00", average_cost: "1100.00", unit_cost: "1000.00", landed_unit_cost: "1100.00", received: "2026-10-20" } } : {}),
    row(2, "TVF-002-OAK", { photo_url: "https://evil.example/x.png", company: "", locations: [], quantity: "0.00" }),
  ],
});
const detail = () => ({
  can_edit: rights.edit,
  item: {
    id: 7, item_code: "TVF-002", description: "TEST ONLY dining chair", name_en: "", name_ar: "", description_ar: "", category_label: "Chairs",
    category_ar: "", brand_id: 2, brand: "TEST ONLY Tivoli", supplier_id: 5, company: "TEST ONLY Milan Furniture", supplier_item_code: "",
    barcode: "", unit_of_measure: "piece", width_cm: "", depth_cm: "", height_cm: "", material: "", colour: "", weight_kg: "",
    volume_m3: "", hs_code: "", lead_time_days: "", is_active: true, photo_url: "", variants: [{ id: 1, sku: "TVF-002-LIN", description: "linen" }],
    updated_by: "", updated_at: "",
  },
  suppliers: [{ id: 5, name: "TEST ONLY Milan Furniture", brand_ids: [3] }, { id: 6, name: "TEST ONLY Other", brand_ids: [] }],
  brands: [{ id: 2, name: "TEST ONLY Tivoli" }, { id: 3, name: "TEST ONLY Sofa House" }],
  units: [{ value: "piece", label: "Piece" }, { value: "set", label: "Set" }],
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent === text) as HTMLButtonElement | undefined;
const field = (label: string) => [...container.querySelectorAll("label")].find((node) => node.textContent?.startsWith(label))?.querySelector("input,select") as HTMLInputElement & HTMLSelectElement;
function type(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  rights = { edit: true, costs: true };
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/inventory/items/") return json(list());
    if (url.pathname === "/api/inventory/items/7/" && init?.method === "PATCH") return json(detail().item);
    if (url.pathname === "/api/inventory/items/7/") return json(detail());
    if (url.pathname === "/api/inventory/items/7/photo/") return json({ ...detail().item, photo_url: PHOTO });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the list shows photo, code, company, where, quantity and price; costs only for people allowed", async () => {
  act(() => root.render(<StockItems selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  const rows = container.querySelectorAll("table tbody tr");
  assert.equal(rows.length, 2);
  assert.equal(rows[0].querySelector("img")?.getAttribute("src"), PHOTO);
  assert.equal(rows[1].querySelector("img"), null, "Only same-origin item photo paths are shown");
  assert.match(rows[0].textContent ?? "", /TVF-002 · TVF-002-LIN.*Milan Furniture.*STIV-G: 4.*2,000.00.*1,100.00/);
  assert.match(rows[1].textContent ?? "", /Not set.*None in stock/);
  assert.ok([...container.querySelectorAll("th")].some((node) => node.textContent === "Landed cost"));

  rights = { edit: false, costs: false };
  act(() => root.render(<StockItems key="reader" selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  assert.equal([...container.querySelectorAll("th")].some((node) => node.textContent === "Landed cost"), false);
  assert.ok(button("Details"));
  assert.equal(button("Edit"), undefined);
});

test("an item is edited, the company's brands come first, and a photo is uploaded", async () => {
  act(() => root.render(<StockItems selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  await act(async () => [...container.querySelectorAll("button")].find((node) => node.textContent === "Edit")!.click());
  await flush();
  const brand = field("Brand");
  assert.match(brand.options[0].textContent ?? "", /Sofa House \(sold by this company\)/);
  await act(async () => { type(field("Colour"), "Sand"); type(field("Width (cm)"), "210"); });
  await act(async () => (container.querySelector("form.si-panel") as HTMLFormElement).requestSubmit());
  await flush();
  const patch = calls.find((call) => call.init?.method === "PATCH")!;
  const body = JSON.parse(String(patch.init?.body));
  assert.deepEqual([body.colour, body.width_cm, body.supplier_id, body.brand_id, body.is_active], ["Sand", "210", 5, 2, true]);
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /TVF-002 saved/);

  const input = container.querySelector("input[type=file]") as HTMLInputElement;
  Object.defineProperty(input, "files", { value: [new File(["png"], "chair.png", { type: "image/png" })], configurable: true });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  await flush();
  const upload = calls.find((call) => call.url.pathname === "/api/inventory/items/7/photo/")!;
  assert.equal(((upload.init?.body as FormData).get("file") as File).name, "chair.png");
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Quotations and invoices show it/);
  assert.equal(container.querySelector(".si-photo img")?.getAttribute("src"), PHOTO);
});

test("people who cannot edit see the details read-only", async () => {
  rights = { edit: false, costs: false };
  act(() => root.render(<StockItems selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Details")!.click());
  await flush();
  assert.ok((container.querySelector("fieldset") as HTMLFieldSetElement).disabled);
  assert.equal(button("Save item"), undefined);
  assert.equal(button("Upload photo"), undefined);
});
