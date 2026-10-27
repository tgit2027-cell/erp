import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import PurchasingTransactions from "../src/PurchasingTransactions";
import { QuotePanel } from "../src/PurchasingWorkbench";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let host: HTMLDivElement;
let posts: Array<{ body: Record<string, unknown>; options: RequestInit; resolve: (response: Response) => void; reject: (error: Error) => void }>;
let deniedUnits: Set<number>;
let deniedChoiceUnits: Set<number>;
let choicePrices: typeof prices;
let deferredChoiceUnits: Set<number>;
let pendingChoiceResponses: Array<{ unit: number; resolve: (response: Response) => void }>;
let warnings: string[];
let requestPaths: string[];
let originalError: typeof console.error;
let originalWarn: typeof console.warn;
const username = "TEST_ONLY_quote_lines";
const prices = [
  { id: 1, variant_id: 10, sku: "TEST-ONE-CENT", amount: "0.01", currency: "QAR", revision: 1, approved_by: 1 },
  { id: 2, variant_id: 20, sku: "TEST-TABLE", amount: "125.55", currency: "QAR", revision: 1, approved_by: 1 },
];
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const page = (rows: unknown[] = []) => ({ rows, pagination: { page: 1, page_size: 100, total_count: rows.length, total_pages: 1 } });
const configuration = (unit: number, currentPrices = prices) => ({
  test_only_label: "TEST ONLY configuration",
  business_unit: { id: unit, code: `TEST${unit}`, name: `TEST ONLY unit ${unit}` },
  role_assignments: [{ id: unit, user_id: 1, username, role: "manager" }],
  current_prices: currentPrices,
  work_calendar: { is_configured: true, closed_weekdays: [], closed_dates: [] },
  storage_exception_approver_roles: [], special_order_value_bands: [],
  special_order_bands_used_for_approval: false, special_order_bands_note: "TEST ONLY",
});
const quotationChoices = (unit: number, currentPrices = prices) => ({
  business_unit: { id: unit, code: `TEST${unit}`, name: `TEST ONLY unit ${unit}` },
  current_prices: currentPrices.map((price) => ({ ...price, item: {
    item_code: price.sku, name_en: `TEST ONLY ${price.sku}`,
    name_ar: price.sku === "TEST-ARABIC-ITEM" ? "أريكة تجريبية" : "", category_en: "TEST category",
    category_ar: price.sku === "TEST-ARABIC-ITEM" ? "أثاث تجريبي" : "",
    description_en: "TEST description",
    description_ar: price.sku === "TEST-ARABIC-ITEM" ? "وصف تجريبي للصنف" : "", image_path: null,
  }, stock: [] })),
  customers: [{ counterparty_id: 2, code: "TEST-CUSTOMER", display_name: "TEST ONLY customer",
    party_type: "customer", contact_en: "TEST ONLY client contact", contact_ar: "جهة اتصال تجريبية", name_ar: "عميل تجريبي" }],
  role_assignments: [{ id: unit, user_id: 1, username, role: "manager" }],
  discount_ceiling: "20", work_calendar: { is_configured: true, closed_weekdays: [], closed_dates: [] },
  test_only_label: "TEST ONLY choices",
});
async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
async function render(unit = 1, options: { prices?: typeof prices; denied?: boolean; deniedChoices?: boolean; staff?: boolean; focusedView?: "quote" | "actions" } = {}) {
  if (options.denied) deniedUnits.add(unit);
  if (options.deniedChoices) deniedChoiceUnits.add(unit);
  choicePrices = options.prices ?? prices;
  await act(async () => root.render(<PurchasingTransactions
    mode="retail" focusedView={options.focusedView} selectedUnitId={unit} selectedUnitName={`TEST ONLY unit ${unit}`}
    username={username} isStaff={options.staff ?? true} csrfToken="TEST_ONLY_CSRF"
    locations={[{ id: unit * 11, code: `L${unit}`, name: `TEST ONLY location ${unit}`, parent_id: null }]}
    configuration={options.staff === false ? null : configuration(unit, options.prices)} configurationState={options.staff === false ? "idle" : "ready"}
    quote={null} quoteLoading={false} quoteNavigationVersion={{ current: 0 }} refreshKey={0}
    onQuoteCreated={() => {}} onMutation={() => {}}
  />));
  await flush();
}
async function setValue(element: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | null, value: string) {
  assert.ok(element, "Expected quote form field");
  const prototype = element.tagName === "SELECT" ? window.HTMLSelectElement.prototype
    : element.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}
async function click(element: Element | null) {
  assert.ok(element, "Expected quote form action");
  await act(async () => element.dispatchEvent(new MouseEvent("click", { bubbles: true })));
}
async function submit() {
  const form = host.querySelector<HTMLFormElement>(".pw-transaction-panel form");
  assert.ok(form);
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();
}
async function setLine(index: number, variant: string, quantity: string, location = "11", specs = "{}") {
  const prefix = index === 0 ? "pw-quote" : `pw-quote-line-${index + 1}`;
  await setValue(host.querySelector(`#${prefix}-variant`), variant);
  if (host.querySelector(`#${prefix}-location`)) await setValue(host.querySelector(`#${prefix}-location`), location);
  await setValue(host.querySelector(`#${prefix}-quantity`), quantity);
  await setValue(host.querySelector(`#${prefix}-specifications`), specs);
}
async function finishPost(post: (typeof posts)[number], body: unknown, status = 201) {
  await act(async () => post.resolve(json(body, status)));
  await flush();
}

test("quotation-entry controls are English while supplied Arabic item/customer data and document fields remain", async () => {
  const arabicPrices = [{
    id: 3, variant_id: 30, sku: "TEST-ARABIC-ITEM", amount: "12.50", currency: "QAR", revision: 1, approved_by: 1,
  }];
  await render(1, { focusedView: "quote", prices: arabicPrices });
  const form = host.querySelector<HTMLFormElement>("form:has(#pw-quote-kind)");
  assert.ok(form);
  const labels = [...form.querySelectorAll("label")].map((label) => label.textContent ?? "").join(" ");
  assert.doesNotMatch(labels, /[\u0600-\u06FF]/);
  assert.match(labels, /Quantity/);
  assert.match(labels, /Discount \(%\)/);
  assert.match(labels, /Discount decision reason/);
  assert.match(labels, /Notes \(Arabic\)/);
  assert.match(labels, /Display unit \(Arabic\)/);
  assert.doesNotMatch([...form.querySelectorAll("button")].map((button) => button.textContent).join(" "), /[\u0600-\u06FF]/);
  assert.equal(form.querySelector('[aria-label="Quote line review"]')?.getAttribute("aria-label"), "Quote line review");
  assert.doesNotMatch([...form.querySelectorAll("[aria-label]")].map((node) => node.getAttribute("aria-label")).join(" "), /[\u0600-\u06FF]/);

  await setValue(host.querySelector("#pw-quote-customer"), "2");
  assert.match(host.querySelector(".pw-quote-customer-preview")?.textContent ?? "", /عميل تجريبي/);
  assert.match(host.querySelector(".pw-quote-customer-preview")?.textContent ?? "", /جهة اتصال تجريبية/);
  await setLine(0, "30", "1.00");
  const itemPreview = host.querySelector(".pw-quote-item-preview")?.textContent ?? "";
  assert.match(itemPreview, /أريكة تجريبية/);
  assert.match(itemPreview, /أثاث تجريبي/);
  assert.match(itemPreview, /وصف تجريبي للصنف/);

  await setValue(host.querySelector("#pw-notes_ar"), "ملاحظة للعميل");
  await setValue(host.querySelector("#pw-quote-quantity"), "1.001");
  await setValue([...form.querySelectorAll("label")]
    .find((label) => label.textContent.startsWith("Discount decision reason"))?.querySelector("input") ?? null, "TEST ONLY reason");
  await submit();
  assert.match(host.querySelector('[role="alert"]')?.textContent ?? "", /Line 1: Quantity/);
  assert.doesNotMatch(host.querySelector('[role="alert"]')?.textContent ?? "", /[\u0600-\u06FF]/);
  assert.equal(posts.length, 0);

  await setValue(host.querySelector("#pw-quote-quantity"), "1.00");
  await submit();
  assert.equal(posts.length, 1);
  assert.equal((posts[0].body.presentation as Record<string, unknown>).notes_ar, "ملاحظة للعميل");
  await finishPost(posts[0], { quote_id: 930 });
});

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  posts = [];
  deniedUnits = new Set();
  deniedChoiceUnits = new Set();
  deferredChoiceUnits = new Set();
  pendingChoiceResponses = [];
  requestPaths = [];
  warnings = [];
  originalError = console.error;
  originalWarn = console.warn;
  console.error = console.warn = (...args) => warnings.push(args.map(String).join(" "));
  globalThis.fetch = (path, options = {}) => {
    const url = new URL(String(path), "https://test-only.invalid");
    requestPaths.push(url.pathname);
    assert.equal(options.credentials, "same-origin");
    const unit = Number(url.searchParams.get("business_unit_id") ?? "1");
    if (options.method === "POST") {
      let resolve!: (response: Response) => void;
      let reject!: (error: Error) => void;
      const promise = new Promise<Response>((yes, no) => { resolve = yes; reject = no; });
      posts.push({ body: JSON.parse(String(options.body)), options, resolve, reject });
      return promise;
    }
    if (url.pathname === "/api/purchasing/quotes/") {
      return Promise.resolve(deniedUnits.has(unit) ? json({ detail: "TEST ONLY access denied" }, 403) : json(page()));
    }
    if (url.pathname === "/api/purchasing/quotation-choices/") {
      if (deferredChoiceUnits.has(unit)) {
        return new Promise<Response>((resolve) => pendingChoiceResponses.push({ unit, resolve }));
      }
      return Promise.resolve(deniedUnits.has(unit) || deniedChoiceUnits.has(unit)
        ? json({ detail: "TEST ONLY choices denied" }, 403)
        : json(quotationChoices(unit, choicePrices)));
    }
    if (url.pathname === "/api/purchasing/quotation-items/") {
      const term = (url.searchParams.get("search") ?? "").toUpperCase();
      return Promise.resolve(json({ rows: term.includes("SOFA") ? [{
        id: 99, variant_id: 990, sku: "TEST-FAR-SOFA-79999", amount: "4200.00", currency: "QAR", revision: 1, approved_by: null,
        item: { item_code: "TEST-FAR-SOFA", name_en: "TEST ONLY far sofa", name_ar: "", category_en: "TEST category", category_ar: "", description_en: "TEST", description_ar: "", image_path: null },
        stock: [{ location_id: unit * 11, quantity: "3.00" }],
      }] : [], more: false }));
    }
    if (url.pathname === "/api/purchasing/quotation-customers/") {
      return Promise.resolve(json({ rows: [{ counterparty_id: 77, code: "TEST-CUST-FAR", display_name: "TEST ONLY far family",
        party_type: "customer", contact_en: "", contact_ar: "", name_ar: "" }], more: false }));
    }
    if (url.pathname === "/api/governance/purchasing/counterparties/") {
      return Promise.resolve(json(page([{ counterparty_id: 2, party_type: "customer", code: "TEST-CUSTOMER", name: "TEST ONLY customer" }])));
    }
    if (url.pathname === "/api/reports/purchase_history/receipts/"
      || url.pathname === "/api/reports/inventory/reservations/"
      || url.pathname === "/api/reports/sales/deliveries/") {
      return Promise.resolve(json({ detail: "TEST ONLY unrelated report unavailable" }, 403));
    }
    throw new Error(`Unexpected test request: ${url.pathname}`);
  };
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  globalThis.fetch = nativeFetch;
  console.error = originalError;
  console.warn = originalWarn;
  assert.deepEqual(warnings, []);
});

test("adds, edits and removes keyed lines; submits payload and retries with the same identity", async () => {
  await render();
  await setValue(host.querySelector("#pw-quote-customer"), "2");
  await setLine(0, "10", "0.50", "11", '{"finish":"matte"}');
  await click(host.querySelector("#pw-add-quote-line"));
  await setLine(1, "20", "2.25", "11", '{"size":"large"}');
  assert.equal(host.querySelectorAll("[data-quote-line]").length, 2);
  assert.equal(host.querySelector("[data-quote-total]")?.textContent?.includes("282.50"), true);
  await click([...host.querySelectorAll("button")].find((button) => button.textContent === "Remove line 2") ?? null);
  assert.equal(host.querySelectorAll("[data-quote-line]").length, 1);
  await click(host.querySelector("#pw-add-quote-line"));
  await setLine(1, "20", "2.25", "11", '{"size":"large"}');
  const reason = [...host.querySelectorAll("label")].find((label) => label.textContent.startsWith("Discount decision reason"))?.querySelector("input") ?? null;
  await setValue(reason, "TEST ONLY quote reason");
  await submit();
  assert.equal(posts.length, 1);
  const first = posts[0];
  assert.deepEqual(first.body.lines, [
    { variant_id: 10, quantity: "0.50", specifications: { finish: "matte" }, location_id: 11 },
    { variant_id: 20, quantity: "2.25", specifications: { size: "large" }, location_id: 11 },
  ]);
  await act(async () => first.reject(new TypeError("TEST ONLY interrupted connection")));
  await flush();
  await submit();
  assert.equal(posts.length, 2);
  assert.deepEqual(posts[1].body, first.body);
  assert.equal((posts[1].options.headers as Record<string, string>)["Idempotency-Key"],
    (first.options.headers as Record<string, string>)["Idempotency-Key"]);
  await finishPost(posts[1], { quote_id: 901 });
});

for (const failure of ["validation", "connection"] as const) {
  test(`focused Create a quote shows ${failure} feedback and recovers without losing the draft or retry identity`, async () => {
    await render(1, { staff: false, focusedView: "quote" });
    await setValue(host.querySelector("#pw-quote-customer"), "2");
    await setLine(0, "20", "2.25", "11", '{"finish":"TEST ONLY linen"}');
    const reason = [...host.querySelectorAll("label")]
      .find((label) => label.textContent.startsWith("Discount decision reason"))?.querySelector("input") ?? null;
    await setValue(reason, failure === "validation" ? "   " : "TEST ONLY valid reason");
    await setValue(host.querySelector("#pw-notes_ar"), "ملاحظة تجريبية");
    const form = host.querySelector("form:has(#pw-quote-kind)");
    const requestsBefore = [...requestPaths];
    await submit();
    assert.equal(posts.length, 1);
    if (failure === "validation") {
      await finishPost(posts[0], { detail: "TEST ONLY discount reason must not be blank" }, 400);
    } else {
      await act(async () => posts[0].reject(new TypeError("TEST ONLY connection interrupted")));
      await flush();
    }
    const alert = [...host.querySelectorAll("[role=alert]")]
      .find((node) => node.textContent?.includes("Quote creation"));
    assert.ok(alert, "The existing server/connection outcome identifies quote creation");
    assert.equal(alert.closest("[hidden]") === null, true, "Creation failure must be visible on the focused entry page");
    assert.match(alert.textContent ?? "", failure === "validation" ? /reason must not be blank/ : /connection interrupted/);
    assert.equal(host.querySelector("form:has(#pw-quote-kind)"), form);
    assert.equal((host.querySelector("#pw-quote-quantity") as HTMLInputElement).value, "2.25");
    assert.equal((host.querySelector("#pw-notes_ar") as HTMLTextAreaElement).value, "ملاحظة تجريبية");
    assert.equal(posts.length, 1, "No automatic retry");
    assert.equal(requestPaths.length, requestsBefore.length + 1, "A 400/connection failure does not refresh or recheck authority");
    if (failure === "validation") await setValue(reason, "TEST ONLY corrected reason");
    await submit();
    assert.equal(posts.length, 2);
    const key = (post: typeof posts[number]) => (post.options.headers as Record<string, string>)["Idempotency-Key"];
    if (failure === "connection") {
      assert.deepEqual(posts[1].body, posts[0].body);
      assert.equal(key(posts[1]), key(posts[0]));
    } else {
      assert.notEqual(key(posts[1]), key(posts[0]), "A changed request retains existing changed-payload semantics");
      assert.deepEqual(posts[1].body.lines, posts[0].body.lines);
    }
    await finishPost(posts[1], { quote_id: 901 });
    const status = [...host.querySelectorAll("[role=status]")]
      .find((node) => node.textContent?.includes("Quote creation"));
    assert.ok(status);
    assert.equal(status.closest("[hidden]") === null, true);
    assert.match(status.textContent ?? "", /#901 created/);
    assert.equal([...host.querySelectorAll("[role=alert]")].some((node) => node.textContent?.includes("Quote creation")), false);
    assert.equal(posts.length, 2);
  });
}

test("removing the first row promotes the next line into legacy controls and keeps the remaining order", async () => {
  await render();
  await setValue(host.querySelector("#pw-quote-customer"), "2");
  await setLine(0, "10", "0.50", "11", '{"finish":"first"}');
  await setValue(host.querySelector("#pw-quote-unit-en"), "TEST first unit");
  await click(host.querySelector("#pw-add-quote-line"));
  await setLine(1, "20", "2.25", "11", '{"size":"promoted"}');
  await setValue(host.querySelector("#pw-quote-line-2-unit-ar"), "وحدة ثانية");
  await click(host.querySelector("#pw-add-quote-line"));
  await setLine(2, "10", "4.00", "11", '{"finish":"remaining"}');
  await setValue(host.querySelector("#pw-quote-line-3-unit-en"), "TEST remaining unit");
  const form = host.querySelector(".pw-transaction-panel form");
  const revision = host.querySelector<HTMLInputElement>('input[name="quote-line-draft-revision"]');
  const firstIdentity = host.querySelector<HTMLInputElement>('input[name="quote-line-identities"]');
  assert.equal(host.querySelectorAll("[data-quote-line]").length, 3);
  await click([...host.querySelectorAll("button")].find((button) => button.textContent === "Remove line 1") ?? null);
  assert.equal(host.querySelector(".pw-transaction-panel form"), form, "The quote form remains mounted.");
  assert.equal((host.querySelector("#pw-quote-variant") as HTMLSelectElement).value, "20");
  assert.equal((host.querySelector("#pw-quote-quantity") as HTMLInputElement).value, "2.25");
  assert.equal((host.querySelector("#pw-quote-unit-en") as HTMLInputElement).value, "");
  assert.equal((host.querySelector("#pw-quote-unit-ar") as HTMLInputElement).value, "وحدة ثانية");
  assert.equal((host.querySelector("#pw-quote-location") as HTMLSelectElement).value, "11");
  assert.equal((host.querySelector("#pw-quote-specifications") as HTMLTextAreaElement).value, '{"size":"promoted"}');
  assert.equal((host.querySelector("#pw-quote-line-2-variant") as HTMLSelectElement).value, "10");
  assert.equal((host.querySelector("#pw-quote-line-2-quantity") as HTMLInputElement).value, "4.00");
  assert.equal((host.querySelector("#pw-quote-line-2-specifications") as HTMLTextAreaElement).value, '{"finish":"remaining"}');
  assert.equal(revision?.value, "3");
  assert.notEqual(firstIdentity?.value, "first,1,2");
  await click([...host.querySelectorAll("button")].find((button) => button.textContent === "Remove line 1") ?? null);
  assert.equal(host.querySelectorAll("[data-quote-line]").length, 1);
  assert.equal((host.querySelector("#pw-quote-variant") as HTMLSelectElement).value, "10");
  assert.equal((host.querySelector("#pw-quote-quantity") as HTMLInputElement).value, "4.00");
  assert.equal((host.querySelector("#pw-quote-unit-en") as HTMLInputElement).value, "TEST remaining unit");
  assert.equal([...host.querySelectorAll("button")].some((button) => button.textContent?.startsWith("Remove line")), false);
  assert.equal(revision?.value, "4");
  const reason = [...host.querySelectorAll("label")].find((label) => label.textContent.startsWith("Discount decision reason"))?.querySelector("input") ?? null;
  await setValue(reason, "TEST ONLY promotion");
  await submit();
  assert.deepEqual(posts[0].body.lines, [
    { variant_id: 10, quantity: "4.00", specifications: { finish: "remaining" }, location_id: 11 },
  ]);
  await finishPost(posts[0], { quote_id: 903 });
});

test("special-order quote omits location and invalid precision blocks submission", async () => {
  await render();
  await setValue(host.querySelector("#pw-quote-customer"), "2");
  await setValue(host.querySelector("#pw-quote-kind"), "special_order");
  await setLine(0, "10", "1.001", "11");
  const reason = [...host.querySelectorAll("label")].find((label) => label.textContent.startsWith("Discount decision reason"))?.querySelector("input") ?? null;
  await setValue(reason, "TEST ONLY quote reason");
  await submit();
  assert.equal(posts.length, 0);
  assert.match(host.querySelector('[role="alert"]')?.textContent ?? "", /Line 1: Quantity.*two decimal places/);
  await setLine(0, "10", "1.00", "11");
  await submit();
  assert.equal(posts.length, 1);
  assert.equal("location_id" in (posts[0].body.lines as Array<Record<string, unknown>>)[0], false);
  await finishPost(posts[0], { quote_id: 902 });
});

test("BU switch blocks old lines; missing prices and denied access stay disabled", async () => {
  await render();
  await setValue(host.querySelector("#pw-quote-customer"), "2");
  await setLine(0, "10", "1.00");
  await render(2);
  assert.match(host.textContent, /Quote line context is out of date/);
  assert.equal(posts.length, 0);
  await click([...host.querySelectorAll("button")].find((button) => button.textContent === "Start fresh quote lines for this unit") ?? null);
  assert.equal((host.querySelector("#pw-quote-variant") as HTMLSelectElement).value, "");
  await render(3, { prices: [] });
  await click([...host.querySelectorAll("button")].find((button) => button.textContent === "Start fresh quote lines for this unit") ?? null);
  assert.equal((host.querySelector("#pw-quote-variant") as HTMLSelectElement).disabled, true);
  await render(4, { denied: true });
  await click([...host.querySelectorAll("button")].find((button) => button.textContent === "Start fresh quote lines for this unit") ?? null);
  assert.equal((host.querySelector("#pw-quote-variant") as HTMLSelectElement).disabled, true);
  assert.equal(posts.length, 0);
});

test("a denied latest-context choices response keeps quote entry disabled", async () => {
  await render(8, { deniedChoices: true });
  assert.equal((host.querySelector("#pw-quote-variant") as HTMLSelectElement).disabled, true);
  assert.match(host.textContent ?? "", /quotation choices could not be authorized/i);
  assert.equal(posts.length, 0);
});

test("a late A choices response cannot restore A customer or prices while B is denied", async () => {
  deferredChoiceUnits.add(41);
  await render(41);
  assert.equal(pendingChoiceResponses.length, 1);
  assert.equal((host.querySelector("#pw-quote-variant") as HTMLSelectElement).disabled, true);
  await render(42, { denied: true });
  assert.equal((host.querySelector("#pw-quote-variant") as HTMLSelectElement).disabled, true);
  await act(async () => pendingChoiceResponses[0].resolve(json(quotationChoices(41))));
  await flush();
  assert.equal((host.querySelector("#pw-quote-variant") as HTMLSelectElement).disabled, true);
  assert.equal(host.querySelectorAll("#pw-quote-variant option").length, 1, "Only the placeholder remains after B is denied.");
  assert.equal([...host.querySelectorAll("#pw-quote-customer option")].some((option) => /TEST-CUSTOMER/.test(option.textContent ?? "")), false);
  assert.match(host.textContent ?? "", /TEST ONLY access denied/);
});

test("nonstaff sales uses only guarded quotation choices and posts presentation with role-bounded discount", async () => {
  await render(7, { staff: false });
  assert.equal(requestPaths.includes("/api/governance/purchasing/7/"), false);
  assert.equal(requestPaths.includes("/api/governance/purchasing/counterparties/"), false);
  assert.match(host.textContent ?? "", /Your current role discount ceiling is 20%/);
  assert.equal((host.querySelector("#pw-quote-variant") as HTMLSelectElement).disabled, false);
  await setValue(host.querySelector("#pw-quote-customer"), "2");
  assert.match(host.querySelector(".pw-quote-customer-preview")?.textContent ?? "", /TEST ONLY client contact/);
  assert.match(host.querySelector(".pw-quote-customer-preview")?.textContent ?? "", /جهة اتصال تجريبية/);
  assert.equal([...host.querySelectorAll('.pw-quote-customer-preview [lang="ar"][dir="rtl"]')]
    .some((value) => value.textContent === "عميل تجريبي"), true);
  await setLine(0, "10", "2.00", "77");
  assert.match(host.querySelector(".pw-quote-item-preview")?.textContent ?? "", /Unreserved quantity/);
  assert.match(host.querySelector(".pw-quote-item-preview")?.textContent ?? "", /no authorized ledger match returned/);
  assert.match(host.querySelector(".pw-quote-item-preview")?.textContent ?? "", /does not reserve inventory/);
  const reason = [...host.querySelectorAll("label")].find((label) => label.textContent.startsWith("Discount decision reason"))?.querySelector("input") ?? null;
  await setValue(reason, "TEST ONLY discount reason");
  await setValue(host.querySelector("#pw-quote-discount"), "20.01");
  await submit();
  assert.equal(posts.length, 0);
  assert.match(host.querySelector('[role="alert"]')?.textContent ?? "", /role ceiling of 20%/);
  await setValue(host.querySelector("#pw-quote-discount"), "20");
  await setValue(host.querySelector("#pw-valid-until"), "2027-03-18");
  await setValue(host.querySelector("#pw-payment_terms_en"), "TEST ONLY display term");
  await setValue(host.querySelector("#pw-payment_terms_ar"), "نص تجريبي");
  await setValue(host.querySelector("#pw-department_en"), "TEST ONLY showroom");
  await setValue(host.querySelector("#pw-department_ar"), "قسم تجريبي");
  await submit();
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body.presentation, {
    valid_until: "2027-03-18", payment_terms_en: "TEST ONLY display term", payment_terms_ar: "نص تجريبي",
    delivery_time_en: "", delivery_time_ar: "", department_en: "TEST ONLY showroom", department_ar: "قسم تجريبي",
  });
  assert.deepEqual(posts[0].body.lines, [
    { variant_id: 10, quantity: "2.00", specifications: {}, location_id: 77 },
  ]);
  assert.equal(/TEST_ONLY_(?:UNIT_)?COST|UNIT_COST|PROCUREMENT_AMOUNT/i.test(host.textContent ?? ""), false);
});

test("staff may request above the assigned seller ceiling up to 50% for the pending-approval workflow", async () => {
  await render();
  await setValue(host.querySelector("#pw-quote-customer"), "2");
  await setLine(0, "10", "1.00", "11");
  assert.equal((host.querySelector("#pw-quote-discount") as HTMLInputElement).max, "50");
  assert.match(host.querySelector("#pw-quote-discount-ceiling")?.textContent ?? "", /role ceiling is 20%; staff may request up to 50%/);
  await setValue(host.querySelector("#pw-quote-discount"), "25");
  const reason = [...host.querySelectorAll("label")].find((label) => label.textContent.startsWith("Discount decision reason"))?.querySelector("input") ?? null;
  await setValue(reason, "TEST ONLY manager review request");
  await submit();
  assert.equal(posts.length, 1);
  assert.equal(posts[0].body.discount_percent, "25");
  assert.equal(posts[0].body.discount_reason, "TEST ONLY manager review request");
  await finishPost(posts[0], { quote_id: 920 });
});

test("saved bilingual presentation flags missing Arabic and never exposes procurement cost fields", async () => {
  await act(async () => root.render(<QuotePanel quote={{
    quote_id: 44, business_unit_id: 7, kind: "stock", status: "approved", customer_id: 2,
    discount_percent: "5", total_list_value: "100.00", total_discount: "5.00", total_quote_value: "95.00",
    payment_plan: "full", downpayment_percent: null, special_order_value_bands_configured: false,
    lines: [], payments: [], approvals: [], holds: [], events: [],
    presentation_document: {
      quote_date: "2026-10-20", valid_until: "2026-10-27",
      client: { name_en: "TEST ONLY client", name_ar: "عميل تجريبي", contact_en: "TEST contact", contact_ar: "" },
      salesperson: "TEST_ONLY_sales", business_unit: { code: "TEST7", name: "TEST ONLY showroom" },
      department_en: "", department_ar: "قسم المعرض", payment_terms_en: "TEST display-only terms", payment_terms_ar: "",
      delivery_time_en: "TEST delivery window", delivery_time_ar: "", terms_status: "pending_owner_text",
      currency: "QAR", total_list_value: "100.00", total_discount: "5.00", total_quote_value: "95.00",
      lines: [{ variant_id: 10, item_code: "ITEM-10", name_en: "TEST ONLY sofa", name_ar: "",
        category_en: "Seating", category_ar: "", description_en: "TEST item description", description_ar: "",
        image_path: "https://outside.invalid/image.png", quantity: "1.00", unit_selling_price: "100.00",
        discount_percent: "5", discount_amount: "5.00", net_total: "95.00" }],
    },
  }} />));
  assert.match(host.textContent ?? "", /Quotation/);
  assert.match(host.textContent ?? "", /عرض سعر/);
  assert.match(host.textContent ?? "", /Arabic client contact not supplied/);
  assert.match(host.textContent ?? "", /Arabic item name not supplied/);
  assert.match(host.textContent ?? "", /قسم المعرض/);
  assert.match(host.textContent ?? "", /Arabic business-unit name was not supplied; the English name above is not translated/);
  assert.equal([...host.querySelectorAll('.pw-presentation-meta strong[lang="ar"][dir="rtl"]')]
    .some((value) => value.textContent === "عميل تجريبي"), true);
  assert.match(host.textContent ?? "", /Pending owner text/);
  assert.match(host.textContent ?? "", /TEST delivery window/);
  assert.equal(host.querySelectorAll('img[src^="https://"]').length, 0);
  assert.match(host.querySelector('[aria-label="Product image unavailable"]')?.textContent ?? "", /Image unavailable/);
  assert.equal(/unit_cost|procurement cost|supplier cost/i.test(host.textContent ?? ""), false);
});

test("optional customer context and per-line units submit in presentation only and retry identically", async () => {
  await render();
  await setValue(host.querySelector("#pw-quote-customer"), "2");
  await setLine(0, "10", "1.00");
  await setValue(host.querySelector("#pw-quote-unit-en"), "piece");
  await setValue(host.querySelector("#pw-quote-unit-ar"), "قطعة");
  await click(host.querySelector("#pw-add-quote-line"));
  await setLine(1, "20", "2.00");
  await setValue(host.querySelector("#pw-quote-line-2-unit-ar"), "صندوق");
  await setValue(host.querySelector("#pw-contact_person_en"), "TEST ONLY purchasing lead");
  await setValue(host.querySelector("#pw-project_ar"), "مشروع تجريبي");
  await setValue(host.querySelector("#pw-email"), "test-only@example.invalid");
  const reason = [...host.querySelectorAll("label")].find((label) => label.textContent.startsWith("Discount decision reason"))?.querySelector("input") ?? null;
  await setValue(reason, "TEST ONLY context reason");
  await submit();
  const first = posts[0];
  assert.deepEqual(first.body.presentation, {
    valid_until: "", payment_terms_en: "", payment_terms_ar: "",
    delivery_time_en: "", delivery_time_ar: "", department_en: "", department_ar: "",
    contact_person_en: "TEST ONLY purchasing lead", project_ar: "مشروع تجريبي",
    email: "test-only@example.invalid",
    line_units: [{ en: "piece", ar: "قطعة" }, { en: "", ar: "صندوق" }],
  });
  assert.deepEqual(first.body.lines, [
    { variant_id: 10, quantity: "1.00", specifications: {}, location_id: 11 },
    { variant_id: 20, quantity: "2.00", specifications: {}, location_id: 11 },
  ]);
  assert.equal("unit_en" in (first.body.lines as Array<Record<string, unknown>>)[0], false);
  await act(async () => first.reject(new TypeError("TEST ONLY interrupted connection")));
  await flush();
  await submit();
  assert.deepEqual(posts[1].body, first.body);
  assert.equal((posts[1].options.headers as Record<string, string>)["Idempotency-Key"],
    (first.options.headers as Record<string, string>)["Idempotency-Key"]);
  await finishPost(posts[1], { quote_id: 944 });
});

test("authorized preview paginates long snapshots, repeats reference, and shows totals only on final page", async () => {
  const lines = Array.from({ length: 9 }, (_, index) => ({
    variant_id: index + 1, variant_sku: `TEST-SKU-${index + 1}`, item_code: `TEST-${index + 1}`, name_en: `TEST ONLY item ${index + 1}`,
    name_ar: "", category_en: "TEST category", category_ar: "", description_en: "Long synthetic description ".repeat(8),
    description_ar: "", image_path: null, quantity: "1.00", unit_selling_price: "12.50",
    discount_percent: "0", discount_amount: "0.00", net_total: "12.50",
  }));
  await act(async () => root.render(<QuotePanel quote={{
    quote_id: 955, business_unit_id: 7, kind: "stock", status: "approved", customer_id: 2,
    discount_percent: "0", total_list_value: "112.50", total_discount: "0.00", total_quote_value: "112.50",
    payment_plan: "full", downpayment_percent: null, special_order_value_bands_configured: false,
    lines: [], payments: [], approvals: [], holds: [], events: [],
    presentation_document: {
      quote_date: "2026-10-20", salesperson: "TEST_ONLY operator",
      company: { name_en: "Tivoli Group L.L.C", trademark_en: "Tivoli", name_ar: "",
        identity_status: "owner_confirmed_registered_name", contact_en: "TEST contact", contact_ar: "" },
      client: {
        account: "TEST-ACCOUNT", name_en: "TEST ONLY client", name_ar: "", contact_en: "", contact_ar: "",
        contact_person_en: "TEST ONLY procurement contact", contact_person_ar: "جهة اتصال تجريبية",
        project_en: "TEST ONLY project", project_ar: "مشروع تجريبي",
        address_en: "TEST ONLY address", address_ar: "عنوان تجريبي",
        phone: "TEST-ONLY-111", mobile: "TEST-ONLY-222", email: "preview@example.invalid",
      },
      notes_en: "TEST ONLY note", notes_ar: "ملاحظة تجريبية",
      currency: "QAR", total_list_value: "112.50", total_discount: "0.00", total_quote_value: "112.50",
      lines,
    },
  }} />));
  const preview = host.querySelector(".pw-presentation-document");
  assert.ok(preview);
  assert.equal(
    preview.querySelector(".pw-company-identity .pw-kicker")?.textContent,
    "ON-SCREEN PREVIEW · REVIEW ONLY",
  );
  assert.equal(
    preview.querySelector('.pw-company-identity .pw-kicker[lang="ar"][dir="rtl"]')?.textContent,
    "معاينة عرض السعر على الشاشة · للمراجعة فقط",
  );
  assert.doesNotMatch(preview.textContent ?? "", /CLIENT COPY/i);
  assert.equal(preview.querySelectorAll(".pw-preview-page").length, 3);
  assert.equal(preview.querySelectorAll(".pw-presentation-line").length, 9);
  assert.equal(preview.querySelectorAll(".pw-preview-reference").length, 3);
  assert.equal(preview.querySelectorAll(".pw-preview-page-number").length, 3);
  assert.match(preview.textContent ?? "", /Page 1 of 3/);
  assert.match(preview.textContent ?? "", /Page 2 of 3/);
  assert.match(preview.textContent ?? "", /Page 3 of 3/);
  assert.equal(preview.querySelectorAll(".pw-presentation-totals").length, 1);
  assert.match(preview.textContent ?? "", /TEST-ACCOUNT/);
  assert.match(preview.textContent ?? "", /TEST ONLY procurement contact/);
  assert.match(preview.textContent ?? "", /TEST ONLY project/);
  assert.match(preview.textContent ?? "", /TEST ONLY address/);
  assert.match(preview.textContent ?? "", /preview@example.invalid/);
   assert.match(preview.textContent ?? "", /Owner-confirmed registered name/);
   for (const page of preview.querySelectorAll(".pw-preview-page")) {
     assert.equal(page.querySelector(".pw-company-identity h3")?.textContent, "Tivoli");
     assert.match(page.querySelector(".pw-company-identity")?.textContent ?? "", /Registered company.*Tivoli Group L\.L\.C/);
   }
   assert.match(preview.textContent ?? "", /TEST ONLY note/);
   assert.match(preview.textContent ?? "", /ملاحظة تجريبية/);
  assert.match(preview.textContent ?? "", /TEST-SKU-1/);
  assert.doesNotMatch(preview.textContent ?? "", /identity_status/);
  assert.match(preview.textContent ?? "", /Arabic company name not supplied/);
  assert.match(preview.textContent ?? "", /Arabic item name not supplied/);
  assert.equal(preview.querySelectorAll("button, [role='button']").length, 0);
  assert.doesNotMatch(preview.textContent ?? "", /print|download|unit_cost|supplier cost/i);
});

test("legacy sparse preview marks unavailable identity and does not invent default validity or terms", async () => {
  await act(async () => root.render(<QuotePanel quote={{
    quote_id: 956, business_unit_id: 3, kind: "stock", status: "approved", customer_id: 1,
    discount_percent: "0", total_list_value: "0", total_discount: "0", total_quote_value: "0",
    payment_plan: "full", downpayment_percent: null, special_order_value_bands_configured: false,
    lines: [], payments: [], approvals: [], holds: [], events: [],
    presentation_document: { lines: [] },
  }} />));
  const preview = host.querySelector(".pw-presentation-document");
  assert.ok(preview);
  assert.match(preview.textContent ?? "", /Company legal name unavailable/);
  assert.match(preview.textContent ?? "", /Valid until/);
  assert.match(preview.textContent ?? "", /Not supplied/);
  assert.match(preview.textContent ?? "", /Pending owner text/);
  assert.equal(preview.querySelectorAll(".pw-preview-page-number").length, 1);
  assert.equal(preview.querySelectorAll(".pw-presentation-totals").length, 1);
  assert.equal(preview.querySelectorAll("button, [role='button']").length, 0);
});

test("historical company snapshots render as recorded rather than adopting the newly confirmed identity", async () => {
  const document = Object.freeze({
    company: Object.freeze({
      name_en: "TEST ONLY historical company", name_ar: "اسم تجريبي سابق",
      identity_status: "configured_identity_pending_legal_name_confirmation",
    }),
    lines: [],
  });
  const original = JSON.stringify(document);
  await act(async () => root.render(<QuotePanel quote={{
    quote_id: 957, business_unit_id: 3, kind: "stock", status: "delivered", customer_id: 1,
    discount_percent: "0", total_list_value: "0", total_discount: "0", total_quote_value: "0",
    payment_plan: "full", downpayment_percent: null, special_order_value_bands_configured: false,
    lines: [], payments: [], approvals: [], holds: [], events: [], presentation_document: document,
  }} />));
  const preview = host.querySelector(".pw-presentation-document");
  assert.match(preview?.textContent ?? "", /TEST ONLY historical company/);
  assert.match(preview?.textContent ?? "", /اسم تجريبي سابق/);
  assert.match(preview?.textContent ?? "", /Historical identity snapshot · not rewritten/);
  assert.doesNotMatch(preview?.textContent ?? "", /Tivoli Group L\.L\.C/);
  assert.equal(JSON.stringify(document), original);
});

test("items and customers beyond the first page are found by server search and submitted", async () => {
  await render(1, { focusedView: "quote" });
  assert.match(host.textContent ?? "", /Search to find any item/);
  await setValue(host.querySelector("#pw-quote-customer-search"), "far");
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
  await flush();
  assert.ok([...host.querySelectorAll<HTMLOptionElement>("#pw-quote-customer option")].some((option) => option.value === "77"));
  await setValue(host.querySelector("#pw-quote-customer"), "77");
  await setValue(host.querySelector("#pw-quote-item-search"), "sofa");
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
  await flush();
  assert.ok(requestPaths.includes("/api/purchasing/quotation-items/"));
  assert.match(host.textContent ?? "", /1 match for “sofa”/);
  await setLine(0, "990", "1.00", "11");
  assert.match(host.textContent ?? "", /TEST ONLY far sofa/);
  assert.match(host.textContent ?? "", /Unreserved quantity: 3.00/);
  await setValue(host.querySelector("#pw-quote-item-search"), "");
  await flush();
  assert.equal(host.querySelector<HTMLSelectElement>("#pw-quote-variant")?.value, "990", "The chosen item stays selected after the search is cleared");
  assert.match(host.querySelector("[data-quote-total]")?.textContent ?? "", /4,200\.00|4200\.00/);
});
