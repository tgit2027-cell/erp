import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import CatalogueReader from "../src/CatalogueReader";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let state = { costs: true, ai: true, status: "review" as "review" | "done" };
const PHOTO = "/api/inventory/catalogue-imports/4/photo/0/0123456789abcdef.jpg";

const record = () => ({
  id: 4, supplier_id: 5, supplier: "TEST ONLY Milan Furniture", file_name: "lumen-2026.pdf", pages_read: "1-12", code_prefix: "TVF",
  status: state.status, status_label: state.status === "review" ? "Waiting for review" : "Items created",
  summary: "TEST ONLY 2026 price list, EUR ex-works", draft_count: 2, created_count: state.status === "done" ? 1 : 0,
  uploaded_by: "buyer", created_at: "2026-10-31T09:00:00Z", finished_by: state.status === "done" ? "buyer" : "",
  finished_at: state.status === "done" ? "2026-10-31T10:00:00Z" : "",
});
const draft = (index: number, extra = {}) => ({
  index, page: 3, brand: "TEST ONLY Lumen", brand_id: null, brand_item_code: `LM-${index}00`, description: "TEST ONLY Lounge chair",
  category: "Lounge chair", width_cm: "72.0", depth_cm: "80.0", height_cm: "78.0", material: "Ash", colour: "Ivory",
  unit_of_measure: "piece", barcode: "", item_code: `TVF-00${8 + index}`, photo_url: index === 0 ? PHOTO : "https://evil.example/x.jpg",
  check: [], duplicates: [], ...(state.costs ? { cost: "1250.00", currency: "EUR" } : {}), ...extra,
});
const detail = () => ({
  import: record(), can_see_costs: state.costs,
  drafts: [draft(0), draft(1, { duplicates: ["Brand code LM-100 is already item TVF-007 (TEST ONLY Lumen)."], check: ["The price is unclear."] })],
  brands: [{ id: 2, name: "TEST ONLY Tivoli", sold_by_company: false }, { id: 3, name: "TEST ONLY Sofa House", sold_by_company: true }],
  units: [{ value: "piece", label: "Piece" }, { value: "set", label: "Set" }],
  ...(state.status === "done" ? { created: [{ item_id: 9, item_code: "TVF-008", sku: "TVF-008-IVO", description: "TEST ONLY Lounge chair", brand: "TEST ONLY Lumen", photo_url: "/api/items/9/photo/0123456789abcdef.jpg" }] } : {}),
});
const overview = () => ({
  unit: { id: 1, code: "TIV", name: "Tivoli" }, can_edit: true, ai_available: state.ai, max_pages: 40,
  imports: [record()], suppliers: [{ id: 5, name: "TEST ONLY Milan Furniture" }],
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent === text) as HTMLButtonElement | undefined;
const fieldIn = (scope: Element, label: string) => [...scope.querySelectorAll("label")].find((node) => node.textContent?.startsWith(label))?.querySelector("input,select") as HTMLInputElement & HTMLSelectElement;
function type(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}
function choose(select: HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(select), "value")?.set?.call(select, value);
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  state = { costs: true, ai: true, status: "review" };
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/inventory/catalogue-imports/" && init?.method === "POST") return json(detail(), 201);
    if (url.pathname === "/api/inventory/catalogue-imports/") return json(overview());
    if (url.pathname === "/api/inventory/catalogue-imports/4/create/") { state.status = "done"; return json({ created: [{}] }); }
    if (url.pathname === "/api/inventory/catalogue-imports/4/") return json(detail());
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("a catalogue is uploaded for a company and the drafts come back to check", async () => {
  act(() => root.render(<CatalogueReader selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector("table")?.textContent ?? "", /lumen-2026.pdf.*Milan Furniture.*Waiting for review/);
  const form = container.querySelector("form.cr-upload") as HTMLFormElement;
  await act(async () => { choose(fieldIn(form, "Company"), "5"); type(fieldIn(form, "Pages"), "1-12"); });
  const input = form.querySelector("input[type=file]") as HTMLInputElement;
  Object.defineProperty(input, "files", { value: [new File(["%PDF"], "lumen-2026.pdf", { type: "application/pdf" })], configurable: true });
  await act(async () => form.requestSubmit());
  await flush();
  const upload = calls.find((call) => call.init?.method === "POST")!;
  const body = upload.init?.body as FormData;
  assert.deepEqual([body.get("supplier_id"), body.get("pages"), body.get("prefix"), (body.get("file") as File).name], ["5", "1-12", "TVF", "lumen-2026.pdf"]);
  const cards = container.querySelectorAll(".cr-card");
  assert.equal(cards.length, 2);
  assert.equal(cards[0].querySelector("img")?.getAttribute("src"), `${PHOTO}?unit=1`);
  assert.equal(cards[1].querySelector("img"), null, "Only this upload's own draft photos are shown");
  assert.match(cards[1].textContent ?? "", /already item TVF-007.*The price is unclear/);
  assert.equal((cards[0].querySelector("input[type=checkbox]") as HTMLInputElement).checked, true);
  assert.equal((cards[1].querySelector("input[type=checkbox]") as HTMLInputElement).checked, false, "Possible duplicates start unticked");
  assert.equal(fieldIn(cards[0], "Brand").value, "new", "An unknown brand is offered as a new brand");
  assert.equal(fieldIn(cards[0], "Brand").querySelector("optgroup")?.getAttribute("label"), "Sold by this company");
  assert.equal(fieldIn(cards[0], "Supplier price").value, "1250.00");
  assert.ok(button("Create 1 item"));
});

test("checked drafts are created with the corrections and the created items are listed", async () => {
  act(() => root.render(<CatalogueReader selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Check items")!.click());
  await flush();
  const card = container.querySelectorAll(".cr-card")[0];
  await act(async () => { type(fieldIn(card, "Description"), "TEST ONLY Lumen lounge chair"); choose(fieldIn(card, "Brand"), "3"); });
  await act(async () => button("Create 1 item")!.click());
  await flush();
  const create = calls.find((call) => call.url.pathname.endsWith("/create/"))!;
  const items = JSON.parse(String(create.init?.body)).items;
  assert.equal(items.length, 1);
  assert.deepEqual([items[0].index, items[0].description, items[0].brand_id, items[0].item_code, items[0].cost, items[0].use_photo],
    [0, "TEST ONLY Lumen lounge chair", 3, "TVF-008", "1250.00", true]);
  assert.equal("brand_name" in items[0], false);
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /1 item was created from lumen-2026.pdf/);
  assert.match(container.querySelector("table")?.textContent ?? "", /TVF-008.*TVF-008-IVO/);
});

test("without cost permission prices are hidden and not sent; without AI the upload is not offered", async () => {
  state.costs = false;
  act(() => root.render(<CatalogueReader selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Check items")!.click());
  await flush();
  assert.equal(fieldIn(container.querySelector(".cr-card")!, "Supplier price"), undefined);
  await act(async () => button("Create 1 item")!.click());
  await flush();
  const items = JSON.parse(String(calls.find((call) => call.url.pathname.endsWith("/create/"))!.init?.body)).items;
  assert.equal("cost" in items[0], false);

  state = { costs: true, ai: false, status: "review" };
  act(() => root.render(<CatalogueReader key="no-ai" selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  assert.equal(container.querySelector("form.cr-upload"), null);
  assert.match(container.textContent ?? "", /not switched on here yet/);
});
