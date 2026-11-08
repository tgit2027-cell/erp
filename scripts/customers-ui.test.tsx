import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Customers from "../src/Customers";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let requests: { url: string; method: string; body: Record<string, string | boolean> | undefined }[];

const row = {
  id: 5, client_number: "C-000005" as string | null, project_number: null as string | null, qid: "28463400123" as string | null, qid_declined: false,
  title: "", title_label: "", title_ar: "", code: "TEST-CUST-28463400123", name: "TEST ONLY Al Sadd family", name_ar: "عائلة السد",
  contact_en: "+974 5555 0101", contact_ar: "", registered_at: "2026-10-20T08:00:00+03:00",
};
const declinedRow = {
  ...row, id: 7, client_number: "C-000007", qid: null, qid_declined: true, code: "TEST-CUST-C-000007",
  name: "TEST ONLY Walk-in buyer", name_ar: "", contact_en: "+974 3333 0707",
};
const detail = {
  ...row,
  quotes: [{ id: 41, business_unit: "TVL", kind: "Available stock", status: "Delivered", total: "4200.00", currency: "QAR", created_at: row.registered_at }],
  quote_count: 1, invoices: [], invoice_count: 0,
  contact_changes: [{ changed_by: "test_sales", changed_at: row.registered_at, fields: [{ field: "contact_en", before: "+974 4444 0000", after: "+974 5555 0101" }] }],
};

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  requests = [];
  globalThis.fetch = (async (input: RequestInfo | URL, options: RequestInit = {}) => {
    const url = String(input);
    const method = options.method ?? "GET";
    const body = typeof options.body === "string" ? JSON.parse(options.body) : undefined;
    requests.push({ url, method, body });
    if (url === "/api/customers/" && method === "POST") {
      if (body.qid === row.qid) return json({ detail: "This QID is already registered to TEST ONLY Al Sadd family (TEST-CUST-28463400123). Open that customer instead." }, 400);
      return json({
        ...detail, id: 6, qid: body.qid_declined ? null : body.qid, qid_declined: Boolean(body.qid_declined),
        client_number: body.register_for === "project" ? null : "C-000006",
        project_number: body.register_for === "client" ? null : "P-000002",
        title: body.title, title_label: body.title === "sheikha" ? "Sheikha" : "",
        name: `TEST ONLY ${body.name}`, quotes: [], quote_count: 0, contact_changes: [],
      }, 201);
    }
    if (url === "/api/customers/7/" && method === "PATCH") return json({ ...detail, ...declinedRow, qid: body.qid, qid_declined: false });
    if (url === "/api/customers/7/") return json({ ...detail, ...declinedRow });
    if (url === "/api/customers/5/" && method === "PATCH" && body?.add_number === "project") return json({ ...detail, project_number: "P-000003" });
    if (url === "/api/customers/5/" && method === "PATCH") return json({ ...detail, contact_en: body.contact_en });
    if (url === "/api/customers/5/") return json(detail);
    return json({
      customers: url.includes("title=sheikha") ? [] : [row, declinedRow], more: false,
      titles: [{ code: "mr", label: "Mr", label_ar: "السيد" }, { code: "sheikha", label: "Sheikha", label_ar: "الشيخة" }],
      title_counts: [{ title: "", label: "No title", label_ar: "", customers: 2, bought: 1 }, { title: "sheikha", label: "Sheikha", label_ar: "الشيخة", customers: 3, bought: 2 }],
    });
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

async function mount() {
  root = createRoot(container);
  act(() => root.render(<Customers csrfToken="TEST-ONLY-CSRF" />));
  await flush();
}
const button = (text: string) => [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(text)) as HTMLButtonElement | undefined;
async function click(element: Element | undefined) {
  assert.ok(element);
  await act(async () => { (element as HTMLElement).click(); });
  await flush();
}
async function type(input: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
}

test("searching by QID opens the customer's file with history", async () => {
  await mount();
  await type(container.querySelector("input[type=search]") as HTMLInputElement, "2846 3400");
  await flush();
  assert.ok(requests.some((request) => request.url === "/api/customers/?search=2846%203400"));
  await click(button("C-000005"));
  const file = container.querySelector(".cust-file")!.textContent ?? "";
  assert.match(file, /Client number \(retail\)C-000005/);
  assert.match(file, /Project number \(projects & fitout\)Give a project number/);
  assert.match(file, /QID \(permanent\)28463400123/);
  assert.match(file, /Quotes \(1\)/);
  assert.match(file, /Phone and contact: \+974 4444 0000 → \+974 5555 0101/);
});

test("registering needs an 11-digit QID and shows a duplicate clearly", async () => {
  await mount();
  await click(button("Register a customer"));
  const form = container.querySelector("form[aria-label='Register a new customer']")!;
  const qid = form.querySelector("input[inputmode=numeric]") as HTMLInputElement;
  const name = [...form.querySelectorAll(".cust-fields input")][0] as HTMLInputElement;
  await type(qid, "12345");
  assert.equal(button("Register customer")?.disabled, true);
  assert.match(form.textContent ?? "", /11 digits and starts with 2 or 3/);
  await type(qid, "28463400123");
  await type(name, "Someone else");
  assert.equal(button("Register customer")?.disabled, false);
  await act(async () => { form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
  await flush();
  assert.match(container.querySelector("[role=alert]")?.textContent ?? "", /already registered to TEST ONLY Al Sadd family/);
  await type(qid, "291 6340 0777");
  await act(async () => { form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
  await flush();
  const created = requests.filter((request) => request.method === "POST").at(-1)!;
  assert.equal(created.body?.qid, "291 6340 0777");
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Registered Someone else as C-000006 with QID 291 6340 0777/);
});

test("changing contact never sends the QID", async () => {
  await mount();
  await click(button("C-000005"));
  await click(button("Change name or contact"));
  const form = container.querySelector("form[aria-label='Change customer details']")!;
  assert.match(form.textContent ?? "", /QID 28463400123 cannot be changed/);
  const phone = [...form.querySelectorAll("input")][2] as HTMLInputElement;
  await type(phone, "+974 6666 0202");
  await act(async () => { form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
  await flush();
  const patch = requests.find((request) => request.method === "PATCH")!;
  assert.equal(patch.url, "/api/customers/5/");
  assert.equal("qid" in (patch.body ?? {}), false);
  assert.equal(patch.body?.contact_en, "+974 6666 0202");
  assert.match(container.querySelector(".cust-facts")?.textContent ?? "", /\+974 6666 0202/);
});

test("a customer who declines the QID is registered with a phone number instead", async () => {
  await mount();
  assert.match(container.querySelector(".cust-table tbody")?.textContent ?? "", /C-000007—Declined/);
  await click(button("Register a customer"));
  const form = container.querySelector("form[aria-label='Register a new customer']")!;
  const qid = form.querySelector("input[inputmode=numeric]") as HTMLInputElement;
  const declined = form.querySelector(".cust-decline input") as HTMLInputElement;
  const [name, , phone] = [...form.querySelectorAll(".cust-fields input")] as HTMLInputElement[];
  await type(qid, "2846");
  await act(async () => { declined.click(); });
  assert.equal(qid.disabled, true);
  assert.equal(qid.value, "", "Ticking declined clears a half-typed QID");
  assert.match(form.textContent ?? "", /phone number or other contact detail is required/);
  await type(name, "Walk-in buyer");
  assert.equal(button("Register customer")?.disabled, true, "A declined customer needs a contact detail");
  await type(phone, "+974 3333 0808");
  assert.equal(button("Register customer")?.disabled, false);
  await act(async () => { form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
  await flush();
  const created = requests.filter((request) => request.method === "POST").at(-1)!;
  assert.equal(created.body?.qid_declined, true);
  assert.equal("qid" in (created.body ?? {}), false);
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Registered Walk-in buyer as C-000006, without a QID/);
  assert.match(container.querySelector(".cust-facts")?.textContent ?? "", /QIDNot given\. The customer declined\./);
});

test("a QID can be added later when the customer agrees", async () => {
  await mount();
  await click(button("C-000007"));
  await click(button("Change details or add QID"));
  const form = container.querySelector("form[aria-label='Change customer details']")!;
  const qid = form.querySelector("input[inputmode=numeric]") as HTMLInputElement;
  await type(qid, "2916");
  assert.equal(button("Save changes")?.disabled, true);
  await type(qid, "29163400888");
  await act(async () => { form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
  await flush();
  const patch = requests.find((request) => request.method === "PATCH")!;
  assert.equal(patch.url, "/api/customers/7/");
  assert.equal(patch.body?.qid, "29163400888");
  assert.match(container.querySelector(".cust-facts")?.textContent ?? "", /QID \(permanent\)29163400888/);
});

test("a customer can be registered for projects and retail with a title, and given a second number later", async () => {
  await mount();
  assert.match(container.querySelector(".cust-title-counts")?.textContent ?? "", /Sheikha3 registered2 bought/);
  await click(button("Register a customer"));
  const form = container.querySelector("form[aria-label='Register a new customer']")!;
  const both = [...form.querySelectorAll("input[type=radio]")].find((input) => (input as HTMLInputElement).value === "both") as HTMLInputElement;
  await act(async () => { both.click(); });
  const title = form.querySelector(".cust-fields select") as HTMLSelectElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value")!.set!.call(title, "sheikha");
    title.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  const kind = form.querySelectorAll(".cust-fields select")[1] as HTMLSelectElement;
  assert.equal(kind.value, "individual", "Individual unless chosen otherwise");
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value")!.set!.call(kind, "government");
    kind.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  await type(form.querySelector("input[inputmode=numeric]") as HTMLInputElement, "29163400999");
  await type([...form.querySelectorAll(".cust-fields input")][0] as HTMLInputElement, "Al Thani residence");
  await act(async () => { form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
  await flush();
  const created = requests.filter((request) => request.method === "POST").at(-1)!;
  assert.equal(created.body?.register_for, "both");
  assert.equal(created.body?.title, "sheikha");
  assert.equal(created.body?.customer_type, "government");
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /as C-000006 · P-000002 with QID/);
  assert.match(container.querySelector(".cust-file h3")?.textContent ?? "", /^Sheikha Al Thani residence/);

  await click(button("C-000005"));
  await click(button("Give a project number"));
  const added = requests.filter((request) => request.method === "PATCH").at(-1)!;
  assert.deepEqual(added.body, { add_number: "project" });
  assert.match(container.querySelector(".cust-facts")?.textContent ?? "", /Project number \(projects & fitout\)P-000003/);
});

test("customers can be filtered by title", async () => {
  await mount();
  const filter = container.querySelector(".cust-title-filter select") as HTMLSelectElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value")!.set!.call(filter, "sheikha");
    filter.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  await flush();
  assert.ok(requests.some((request) => request.url === "/api/customers/?search=&title=sheikha"));
});

