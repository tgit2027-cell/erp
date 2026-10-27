import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import SalesReturnIntake from "../src/SalesReturnIntake";
import PurchasingWorkbench from "../src/PurchasingWorkbench";

const originalFetch = globalThis.fetch;
const localDate = (date = new Date()) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const addDays = (value: string, amount: number) => {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  date.setDate(date.getDate() + amount);
  return localDate(date);
};
const deliveredDate = () => addDays(localDate(), -14);
const displayDate = (value: string) => value;
const policy = {
  provisional_calendar_days: 14,
  calendar_days: 14,
  date_basis: "delivery",
  source: "Owner-confirmed company rule: calendar days counted from actual delivery. Legal validation pending; not a verified statement of Qatar law.",
  status: "owner_confirmed_pending_legal_validation",
};
const sale = {
  quote_id: 431,
  delivered_at: `${deliveredDate()}T10:00:00Z`,
  customer: { id: 28, name: "TEST ONLY Dana Al-Kuwari" },
  business_unit: { id: 7, code: "TEST_QA", name: "TEST ONLY Doha showroom" },
  lines: [
    { quote_line_id: 501, variant_id: 91, item_id: "TVF-002", description: "TEST ONLY linen lounge chair", brand: "TEST ONLY Atelier N", quantity: "2.00" },
    { quote_line_id: 502, variant_id: 92, item_id: "TBL-014", description: "TEST ONLY side table", brand: "TEST ONLY Atelier N", quantity: "1.00" },
  ],
  locations: [{ id: 71, code: "TEST_SH", name: "TEST ONLY Showroom receiving" }],
};
const assessSale = (requestDate: string) => {
  const deliveryDate = deliveredDate();
  const deadline = addDays(deliveryDate, 14);
  const eligible = !!requestDate && requestDate >= deliveryDate && requestDate <= deadline;
  return {
    ...sale,
    delivery_window: {
      date_basis: "delivery", calendar_days: 14, delivery_date: deliveryDate,
      deadline, request_date: requestDate, eligible,
      message: eligible ? "Within the owner-confirmed delivery window." : requestDate < deliveryDate ? "Request date is before actual delivery." : "The delivery window has expired.",
    },
  };
};
const returned = (id = 19, status = "awaiting_inspection") => ({
  id,
  quote_id: 431,
  request_date: localDate(),
  customer: sale.customer,
  business_unit: sale.business_unit,
  status,
  receiving_mode: "showroom",
  location: sale.locations[0],
  receipt_collection_details: "",
  proposed_refund_amount: "285.00",
  currency: "QAR",
  original_payment_method: "cash",
  requested_refund_method: "cash",
  refund_preference: "usually_original",
  original_method_source: "user_recorded_not_verified",
  lines: [
    { quote_line_id: 501, quantity: "1", description: sale.lines[0].description, item_id: "TVF-002", brand: sale.lines[0].brand, condition: status === "finance_pending" ? "not_good" : "uninspected", inspection_notes: "" },
  ],
  events: [],
  policy,
  delivery_window: {
    date_basis: "delivery", calendar_days: 14, delivery_date: deliveredDate(),
    deadline: localDate(), request_date: localDate(), eligible: true,
    message: "Within the owner-confirmed delivery window.",
  },
});
const page = (rows = [], pageNo = 1, totalPages = 1) => ({
  rows, policy,
  pagination: {
    page: pageNo, page_size: 10, total_count: rows.length,
    total_pages: totalPages, previous_page: pageNo > 1 ? pageNo - 1 : null,
    next_page: pageNo < totalPages ? pageNo + 1 : null,
  },
});
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
let root: ReturnType<typeof createRoot>;
let host: HTMLDivElement;
let calls: Array<{ url: URL; options: RequestInit }> = [];
let listReply: (unitId: string) => Response | Promise<Response>;
let postReply: (path: string, payload: any) => Response | Promise<Response>;
let saleReply: (url: URL) => Response | Promise<Response>;

async function flush() {
  await act(async () => { await new Promise(setImmediate); });
  await act(async () => { await new Promise(setImmediate); });
}
async function render(unitId = 7) {
  await act(async () => root.render(
    <SalesReturnIntake selectedUnitId={unitId} username="TEST_ONLY_return_operator" csrfToken="TEST_ONLY_CSRF" />,
  ));
  await flush();
}
async function setValue(element: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement, value: string) {
  assert.ok(element);
  const prototype = element.tagName === "SELECT" ? window.HTMLSelectElement.prototype
    : element.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
  await flush();
}
async function click(element: Element | null) {
  assert.ok(element);
  await act(async () => element.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await flush();
}
async function submit(form: HTMLFormElement | null) {
  assert.ok(form);
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();
}
function input(id: string) {
  return host.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(`#${id}`);
}
function labelControl(labelText: string) {
  const label = [...host.querySelectorAll("label")].find((item) => item.textContent?.startsWith(labelText));
  return label?.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("input,select,textarea") ?? null;
}
function createForm() {
  return [...host.querySelectorAll("form")].find((form) => form.querySelector("#pw-return-quote")) ?? null;
}
async function prepareIntake(amount = "285.00") {
  await setValue(input("pw-return-quote")!, "431");
  await flush();
  await setValue(input("pw-return-qty-501")!, "1");
  await setValue(labelControl("Receiving location")!, "71");
  await setValue(labelControl("Proposed refund amount")!, amount);
  await setValue(labelControl("Original payment method")!, "cash");
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  calls = [];
  listReply = () => json(page());
  postReply = (_path, payload) => json(returned(payload?.id ?? 19));
  saleReply = (url) => json(assessSale(url.searchParams.get("request_date") ?? ""));
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, options });
    if (url.pathname === "/api/purchasing/returns/") {
      if (options.method === "POST") {
        const payload = JSON.parse(String(options.body));
        return postReply(url.pathname, payload);
      }
      return listReply(url.searchParams.get("business_unit_id") ?? "");
    }
     if (url.pathname.startsWith("/api/purchasing/returns/sales/")) return saleReply(url);
    if (url.pathname.endsWith("/inspection/")) {
      const payload = JSON.parse(String(options.body));
      return postReply(url.pathname, payload);
    }
    throw new Error(`Unexpected synthetic fixture request: ${url.pathname}`);
  };
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  globalThis.fetch = originalFetch;
});

test("workbench return disclosure loads only on explicit open and preserves draft form when collapsed", async () => {
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, options });
    assert.equal(options.method ?? "GET", "GET", "Opening a register cannot submit a transaction");
    if (url.pathname === "/api/purchasing/returns/") return json(page());
    if (url.pathname.startsWith("/api/purchasing/returns/sales/")) return json(assessSale(url.searchParams.get("request_date") ?? ""));
    return json({ detail: "TEST ONLY existing workbench capability not configured" }, 403);
  };
  await act(async () => root.render(<PurchasingWorkbench
    selectedUnitId={7} selectedUnitName="TEST ONLY unit" username="synthetic_sales"
    csrfToken="synthetic-csrf" isStaff={false} locations={[]} onOpenReports={() => {}}
  />));
  await flush();
  assert.equal(calls.filter(({ url }) => url.pathname === "/api/purchasing/returns/").length, 0);
  const summary = [...host.querySelectorAll("summary")].find((node) =>
    node.textContent?.includes("Sales-return intake — synthetic, non-posting"));
  assert.ok(summary);
  const disclosure = summary.parentElement as HTMLDetailsElement;
  await act(async () => {
    disclosure.open = true;
    disclosure.dispatchEvent(new Event("toggle"));
  });
  await flush();
  assert.match(host.textContent ?? "", /Return request register/);
  await setValue(input("pw-return-quote")!, "431");
  await flush();
  const draftInput = host.querySelector<HTMLTextAreaElement>(".pw-return-details textarea");
  assert.ok(draftInput);
  await setValue(draftInput, "TEST ONLY receiving observation draft");
  await act(async () => {
    disclosure.open = false;
    disclosure.dispatchEvent(new Event("toggle"));
  });
  await act(async () => {
    disclosure.open = true;
    disclosure.dispatchEvent(new Event("toggle"));
  });
  assert.equal(host.querySelector(".pw-return-details textarea"), draftInput, "Disclosure must not remount the intake");
  assert.equal(draftInput.value, "TEST ONLY receiving observation draft");
  assert.equal(calls.filter(({ url }) => url.pathname === "/api/purchasing/returns/").length, 1);
});

test("loads authorized sale, captures item quantities and posts only synthetic intake payload", async () => {
  let createdPayload: any;
  let createOptions: RequestInit | undefined;
  postReply = (_path, payload) => {
    createdPayload = payload;
    createOptions = calls.at(-1)?.options;
    return json(returned(33));
  };
  await render();
  assert.match(host.textContent ?? "", /TEST ONLY · SYNTHETIC SALES-RETURN INTAKE/);
  await setValue(input("pw-return-quote")!, "431");
  await flush();
  assert.match(host.textContent ?? "", /TEST ONLY Dana Al-Kuwari/);
  assert.match(host.textContent ?? "", /TEST ONLY linen lounge chair/);
  assert.equal((labelControl("Receiving location") as HTMLSelectElement).value, "", "Location requires an explicit selection.");
  await setValue(input("pw-return-qty-501")!, "1.25");
  await setValue(labelControl("Receiving location")!, "71");
  await setValue(labelControl("Proposed refund amount")!, "285.00");
  await setValue(labelControl("Original payment method")!, "cash");
  await submit(createForm());
  assert.deepEqual(createdPayload, {
    business_unit_id: 7,
    quote_id: 431,
    request_date: localDate(),
    receiving_mode: "showroom",
    location_id: 71,
    receipt_collection_details: "",
    proposed_refund_amount: "285.00",
    original_payment_method: "cash",
    requested_refund_method: "cash",
    refund_preference: "usually_original",
    lines: [{ quote_line_id: 501, quantity: "1.25" }],
  });
  assert.equal(createOptions?.method, "POST");
  const saleLookup = calls.find(({ url }) => url.pathname.startsWith("/api/purchasing/returns/sales/"));
  assert.equal(saleLookup?.url.searchParams.get("business_unit_id"), "7");
  assert.equal(saleLookup?.url.searchParams.get("request_date"), localDate());
  assert.equal((createOptions?.headers as Record<string, string>)["X-CSRFToken"], "TEST_ONLY_CSRF");
  assert.ok((createOptions?.headers as Record<string, string>)["Idempotency-Key"]);
  assert.match(host.textContent ?? "", /recorded for authorized review/);
  assert.equal(calls.filter(({ options }) => options.method === "POST").length, 1);
});

test("denied register and denied sale show composed states without leaking prior data", async () => {
  listReply = () => json({ detail: "TEST ONLY access not granted" }, 403);
  saleReply = () => json({ detail: "TEST ONLY sale access not granted" }, 404);
  await render();
  assert.match(host.textContent ?? "", /Return register unavailable/);
  assert.match(host.textContent ?? "", /TEST ONLY access not granted/);
  await setValue(input("pw-return-quote")!, "431");
  await flush();
  assert.match(host.textContent ?? "", /Sale details unavailable/);
  assert.equal(host.textContent?.includes("TEST ONLY Dana Al-Kuwari"), false);
  assert.equal(input("pw-return-quote")?.disabled, false, "Sale lookup remains available after a denied response");
  assert.equal(host.querySelector('.pw-return-block form button[type="submit"]'), null, "No intake submit path is rendered without authorized sale details");
});

test("empty register remains usable and states are scoped to the selected business unit", async () => {
  const deferred: Array<{ unit: string; resolve: (response: Response) => void }> = [];
  listReply = (unit) => new Promise((resolve) => deferred.push({ unit, resolve }));
  await render(7);
  assert.equal(deferred[0].unit, "7");
  await render(8);
  assert.equal(deferred[1].unit, "8");
  await act(async () => deferred[1].resolve(json(page([{
    ...returned(81), business_unit: { id: 8, code: "TEST_8", name: "TEST ONLY Unit Eight" },
    customer: { id: 80, name: "TEST ONLY Unit Eight customer" },
  }]))));
  await flush();
  await act(async () => deferred[0].resolve(json(page([{
    ...returned(71), customer: { id: 70, name: "TEST ONLY stale Unit Seven" },
  }]))));
  await flush();
  assert.match(host.textContent ?? "", /TEST ONLY Unit Eight customer/);
  assert.equal(host.textContent?.includes("TEST ONLY stale Unit Seven"), false);
  assert.equal(calls.filter(({ url }) => url.pathname === "/api/purchasing/returns/").every(({ url }) => url.searchParams.has("page")), true);
});

test("inspection requires each returned line and a not-good finding sends no disposition; result waits for Finance", async () => {
  listReply = () => json(page([returned()]));
  let inspectionPayload: any;
  postReply = (_path, payload) => {
    inspectionPayload = payload;
    return json(returned(19, "finance_pending"));
  };
  await render();
  await click([...host.querySelectorAll("button")].find((button) => button.textContent === "Inspect return") ?? null);
  assert.equal(host.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled, true);
  await setValue(host.querySelector('select[aria-label="Condition for line 501"]') as HTMLSelectElement, "not_good");
  assert.match(host.textContent ?? "", /No exclusion, disposition or stock decision is inferred/);
  await submit([...host.querySelectorAll("form")].find((form) => form.querySelector('select[aria-label="Condition for line 501"]')) ?? null);
  assert.deepEqual(inspectionPayload, {
    business_unit_id: 7,
    lines: [{ quote_line_id: 501, condition: "not_good", inspection_notes: "" }],
  });
  assert.equal(JSON.stringify(inspectionPayload).toLowerCase().includes("disposition"), false);
  assert.match(host.textContent ?? "", /pending authorized Finance review/);
  assert.match(host.textContent ?? "", /No refund, credit, inventory or disposition action was taken/);
  assert.equal(calls.filter(({ options }) => options.method === "POST").length, 1);
});

test("refund preference resolves from recorded payment method while delivery window permits synthetic intake", async () => {
  let posted: any;
  postReply = (_path, payload) => {
    posted = payload;
    return json(returned(53));
  };
  await render();
  await setValue(input("pw-return-quote")!, "431");
  await flush();
  assert.match(host.textContent ?? "", /14 calendar days from actual delivery/);
  assert.match(host.textContent ?? "", /date basis: delivery/i);
  assert.match(host.textContent ?? "", /Within delivery window/);
  await setValue(input("pw-return-qty-501")!, "1");
  await setValue(labelControl("Receiving location")!, "71");
  await setValue(labelControl("Proposed refund amount")!, "285.00");
  await setValue(labelControl("Original payment method")!, "cash");
  assert.equal((createForm()?.querySelector('button[type="submit"]') as HTMLButtonElement).disabled, false);
  await setValue(labelControl("Refund preference")!, "card");
  assert.equal((labelControl("Requested refund method") as HTMLInputElement).value, "card");
  await submit(createForm());
  assert.equal(posted.refund_preference, "card");
  assert.equal(posted.original_payment_method, "cash");
  assert.equal(posted.requested_refund_method, "card");
  assert.equal([...host.querySelectorAll("button")].some((button) => /approve refund|issue credit|eligibility decision/i.test(button.textContent ?? "")), false);
});

test("the rendered inclusive boundary day +14 is eligible; +15 is blocked without posting", async () => {
  await render();
  await setValue(input("pw-return-quote")!, "431");
  await flush();
  const requestDate = labelControl("Request date") as HTMLInputElement;
  assert.equal(requestDate.value, addDays(deliveredDate(), 14));
  await setValue(labelControl("Original payment method")!, "cash");
  assert.equal((createForm()?.querySelector('button[type="submit"]') as HTMLButtonElement).disabled, false);
  await setValue(requestDate, addDays(deliveredDate(), 15));
  assert.equal((labelControl("Request date") as HTMLInputElement).value, addDays(deliveredDate(), 15), "Date control stays mounted while reassessment runs.");
  assert.match(host.textContent ?? "", /delivery window has expired/i);
  assert.equal((createForm()?.querySelector('button[type="submit"]') as HTMLButtonElement).disabled, true);
  await setValue(requestDate, addDays(deliveredDate(), -1));
  assert.match(host.textContent ?? "", /before actual delivery/i);
  assert.equal((createForm()?.querySelector('button[type="submit"]') as HTMLButtonElement).disabled, true);
  await submit(createForm());
  assert.equal(calls.filter(({ options }) => options.method === "POST").length, 0);
});

test("a stale prior-date delivery response cannot re-enable intake after a newer date is selected", async () => {
  const assessments: Array<{ requestDate: string; resolve: (response: Response) => void }> = [];
  saleReply = (url) => {
    const requestDate = url.searchParams.get("request_date") ?? "";
    if (!assessments.length) {
      assessments.push({ requestDate, resolve: () => {} });
      return json(assessSale(requestDate));
    }
    return new Promise((resolve) => assessments.push({ requestDate, resolve }));
  };
  await render();
  await setValue(input("pw-return-quote")!, "431");
  assert.equal(assessments.length, 1);
  const dateInput = host.querySelector<HTMLInputElement>('input[type="date"]');
  assert.ok(dateInput, "Request date remains usable while initial assessment is pending.");
  const staleDate = addDays(dateInput.value, -1);
  const currentDate = addDays(dateInput.value, 1);
  await setValue(dateInput, staleDate);
  assert.equal(assessments.length, 2);
  await setValue(dateInput, currentDate);
  assert.equal(assessments.length, 3);
  await act(async () => assessments[2].resolve(json(assessSale(currentDate))));
  await flush();
  assert.equal((createForm()?.querySelector('button[type="submit"]') as HTMLButtonElement).disabled, true);
  await act(async () => assessments[1].resolve(json(assessSale(staleDate))));
  await flush();
  assert.equal((labelControl("Request date") as HTMLInputElement).value, currentDate);
  assert.equal((createForm()?.querySelector('button[type="submit"]') as HTMLButtonElement).disabled, true);
  assert.match(host.textContent ?? "", /delivery window has expired/i);
});

test("stored delivery-window assessment is shown on the selected return; legacy missing eligibility is fail-closed", async () => {
  const legacy = { ...returned(77), delivery_window: { date_basis: "delivery", calendar_days: 14, delivery_date: null, deadline: null, request_date: null, eligible: null, message: "Legacy eligibility unavailable." } };
  listReply = () => json(page([returned(76), legacy]));
  await render();
  await click([...host.querySelectorAll("button")].find((button) => button.textContent === "Inspect return") ?? null);
  assert.match(host.textContent ?? "", /Stored delivery-window assessment/);
  assert.ok((host.textContent ?? "").includes(displayDate(deliveredDate())));
  assert.match(host.textContent ?? "", /Eligible under stored assessment/);
  await click([...host.querySelectorAll("button")].find((button) => button.textContent === "Close") ?? null);
  const inspectButtons = [...host.querySelectorAll("button")].filter((button) => button.textContent === "Inspect return");
  await click(inspectButtons[1] ?? null);
  assert.match(host.textContent ?? "", /not recorded/);
  assert.match(host.textContent ?? "", /Legacy eligibility unavailable/);
  assert.match(host.textContent ?? "", /Inspection is unavailable/);
  assert.equal(host.querySelector('select[aria-label="Condition for line 501"]'), null);
  assert.equal(host.querySelector<HTMLButtonElement>('button[type="submit"]'), null);
});

test("same payload retry reuses idempotency key and body; changed payload receives a new key", async () => {
  const resolvePosts: Array<(response: Response) => void> = [];
  postReply = () => new Promise((resolve) => resolvePosts.push(resolve));
  await render();
  await prepareIntake();
  await submit(createForm());
  assert.equal(resolvePosts.length, 1);
  const postRecords = () => calls.filter(({ options }) => options.method === "POST");
  const first = postRecords()[0];
  const firstBody = String(first.options.body);
  const firstKey = (first.options.headers as Record<string, string>)["Idempotency-Key"];
  await act(async () => resolvePosts[0](json({ detail: "TEST ONLY transient response failure" }, 503)));
  await flush();
  assert.match(host.textContent ?? "", /TEST ONLY transient response failure/);

  await submit(createForm());
  assert.equal(resolvePosts.length, 2);
  const second = postRecords()[1];
  assert.equal((second.options.headers as Record<string, string>)["Idempotency-Key"], firstKey);
  assert.equal(second.options.body, firstBody, "An exact retry reuses the serialized request body.");
  await act(async () => resolvePosts[1](json({ detail: "TEST ONLY second transient failure" }, 503)));
  await flush();

  await setValue(labelControl("Proposed refund amount")!, "286.00");
  await submit(createForm());
  assert.equal(resolvePosts.length, 3);
  const third = postRecords()[2];
  assert.notEqual((third.options.headers as Record<string, string>)["Idempotency-Key"], firstKey);
  assert.notEqual(third.options.body, firstBody, "A changed proposed amount is a different idempotent request.");
  await act(async () => resolvePosts[2](json(returned(54))));
  await flush();
  assert.match(host.textContent ?? "", /Synthetic intake recorded/);
});

test("a POST remains in flight across a business-unit change but its old success and record never enter the new context", async () => {
  let resolveCreate: ((response: Response) => void) | undefined;
  postReply = () => new Promise((resolve) => { resolveCreate = resolve; });
  await render(7);
  await prepareIntake();
  await submit(createForm());
  assert.equal(typeof resolveCreate, "function");
  assert.equal(calls.filter(({ options }) => options.method === "POST").length, 1);

  await render(8);
  assert.equal(calls.filter(({ options }) => options.method === "POST").length, 1, "Changing unit does not abort or resubmit the pending POST.");
  assert.equal(host.textContent?.includes("Synthetic return request 99 recorded"), false);
  assert.equal(host.textContent?.includes("TEST ONLY Dana Al-Kuwari"), false);
  assert.match(host.textContent ?? "", /No synthetic return requests/);

  await act(async () => resolveCreate!(json(returned(99))));
  await flush();
  assert.equal(host.textContent?.includes("Synthetic intake recorded"), false);
  assert.equal(host.textContent?.includes("SR-99"), false);
  assert.equal(host.textContent?.includes("TEST ONLY Dana Al-Kuwari"), false);
});
