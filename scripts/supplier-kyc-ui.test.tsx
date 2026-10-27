import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import SupplierKyc from "../src/SupplierKyc";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let requests: { url: string; method: string; body: unknown; headers: HeadersInit | undefined }[];
let permissions: { can_open: boolean; can_approve: boolean };
let detail: Record<string, unknown>;

const person = (id: number, username: string) => ({ id, username, name: `TEST ONLY ${username}` });
const row = {
  id: 7,
  supplier: { id: 70, code: "TEST-SUP-LOCAL-1", name: "TEST ONLY Doha Timber Trading WLL" },
  legal_name: "Doha Timber Trading WLL",
  cr_number: "CR-123456",
  vat_number: "",
  status: "submitted",
  status_label: "Waiting for approval",
  opened_by: person(3, "test_it"),
  opened_at: "2026-10-20T08:00:00+03:00",
  submitted_at: "2026-10-20T09:00:00+03:00",
  decided_by: null,
  decided_at: null,
};

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}

async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  requests = [];
  permissions = { can_open: false, can_approve: true };
  detail = {
    ...row, address: "", cr_expiry: "2027-12-31", vat_registered: false, signatory_name: "Test Signatory",
    signatory_title: "Partner", contact_email: "", contact_phone: "", decision_note: "", missing_documents: [],
    documents: [{ id: 1, kind: "cr_certificate", kind_label: "Commercial registration (CR)", name: "cr.pdf", size: 2048, uploaded_by: "test_it", uploaded_at: row.opened_at }],
    history: [{ event: "opened", actor: "test_it", note: "", at: row.opened_at }],
    can_edit: false, can_decide: true,
  };
  globalThis.fetch = (async (input: RequestInfo | URL, options: RequestInit = {}) => {
    const url = String(input);
    const method = options.method ?? "GET";
    requests.push({ url, method, body: typeof options.body === "string" ? JSON.parse(options.body) : options.body, headers: options.headers });
    if (url.endsWith("/decision/")) {
      const body = JSON.parse(String(options.body));
      if (!body.approve && !body.note) return json({ detail: "Give a reason for the rejection." }, 400);
      detail = { ...detail, status: body.approve ? "approved" : "rejected", status_label: body.approve ? "Approved" : "Rejected", decided_by: person(4, "test_manager"), decided_at: row.opened_at, decision_note: body.note, can_decide: false };
      return json(detail);
    }
    if (url === "/api/suppliers/kyc/" && method === "POST") return json({ ...detail, id: 8, status: "draft", can_edit: true, can_decide: false }, 201);
    if (/\/api\/suppliers\/kyc\/\d+\/$/.test(url)) return json(detail);
    return json({
      permissions,
      counts: { draft: 0, submitted: 1, approved: 0, rejected: 0 },
      document_kinds: [{ kind: "cr_certificate", label: "Commercial registration (CR)" }, { kind: "signatory_id", label: "Signatory ID" }],
      files: [row],
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
  act(() => root.render(<SupplierKyc csrfToken="TEST-ONLY-CSRF" />));
  await flush();
}

const button = (text: string) => [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(text)) as HTMLButtonElement | undefined;
async function click(element: Element | undefined) {
  assert.ok(element);
  await act(async () => { (element as HTMLElement).click(); });
  await flush();
}

test("a manager sees waiting suppliers, opens one, and approves it", async () => {
  await mount();
  assert.match(container.textContent ?? "", /Waiting for approval \(1\)/);
  assert.equal(button("Open a new supplier"), undefined, "Only people who can open suppliers see the open button");
  await click(button("Doha Timber Trading WLL"));
  assert.match(container.textContent ?? "", /CR-123456/);
  assert.equal(container.querySelector(".kyc-documents a")?.getAttribute("href"), "/api/suppliers/kyc/7/documents/1/");
  assert.equal(button("Reject")?.disabled, true, "A rejection needs a reason first");
  await click(button("Approve supplier"));
  const decision = requests.find((request) => request.url.endsWith("/decision/"));
  assert.deepEqual(decision?.body, { approve: true, note: "" });
  assert.equal((decision?.headers as Record<string, string>)["X-CSRFToken"], "TEST-ONLY-CSRF");
  assert.match(container.textContent ?? "", /Approved/);
});

test("rejecting sends the reason and shows it on the file", async () => {
  await mount();
  await click(button("Doha Timber Trading WLL"));
  const note = container.querySelector("textarea")!;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")!.set!;
    setter.call(note, "CR copy is expired");
    note.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
  await click(button("Reject"));
  assert.deepEqual(requests.find((request) => request.url.endsWith("/decision/"))?.body, { approve: false, note: "CR copy is expired" });
  assert.match(container.querySelector(".kyc-rejection")?.textContent ?? "", /CR copy is expired/);
});

test("IT can open a new supplier and lands on its draft file", async () => {
  permissions = { can_open: true, can_approve: false };
  await mount();
  await click(button("Open a new supplier"));
  assert.ok(container.querySelector("form[aria-label='Open a new supplier']"));
  await act(async () => { container.querySelector("form")!.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
  await flush();
  const created = requests.find((request) => request.url === "/api/suppliers/kyc/" && request.method === "POST");
  assert.equal((created?.body as { code: string }).code, "TEST-SUP-");
  assert.ok(container.querySelector(".kyc-file"));
});
