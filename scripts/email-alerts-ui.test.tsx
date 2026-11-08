import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import EmailAlerts from "../src/EmailAlerts";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const TYPES = [
  { key: "payments", label: "Supplier payments and advances", kind: "approvals" },
  { key: "imports", label: "Go-live imports", kind: "approvals" },
  { key: "supplier_documents", label: "Supplier CR and KYC documents expiring", kind: "expiring" },
];
const omar = (types: string[]) => ({ id: 5, name: "TEST ONLY Omar", account_email: "omar@example.invalid", email: "", sends_to: "omar@example.invalid",
  offered: ["payments", "supplier_documents"], types });
const overview = (extra = {}) => ({
  types: TYPES,
  emails: [{ id: 1, kind: "approvals", kind_label: "Waiting for your approval", user: "TEST ONLY Omar", to: "omar@example.invalid",
    subject: "Tivoli ERP: 1 thing is waiting for your approval", items: [{ key: "advance:3", title: "Advance ADV-1 to TEST ONLY Gulf Timber, QAR 12,000.00", link: "#/finance/advances" }],
    status: "sent", status_label: "Sent", error: "", at: "2026-11-04T10:00:00+03:00" }],
  people: [omar(["payments"]),
    { id: 6, name: "TEST ONLY Roche", account_email: "", email: "", sends_to: "", offered: ["imports"], types: [] }],
  waiting: 3, expiring: 1, mail_configured: false, policy: "TEST ONLY policy", ...extra,
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: new URL(String(url), "http://localhost"), init });
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      return json(body.action === "send" ? overview({ just_sent: 2 }) : overview({ people: [{ ...omar(body.types ?? ["payments"]), email: body.email ?? "" }] }));
    }
    return json(overview());
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the log, the counts and who receives which alerts are shown", async () => {
  act(() => root.render(<EmailAlerts csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector("h2")?.textContent ?? "", /^Alerts$/);
  assert.match(container.querySelector(".ex-tiles")?.textContent ?? "", /Waiting for approval3.*Documents to watch1.*Email serverNot set up/);
  const table = container.querySelector("table[aria-label='Who receives alerts']")?.textContent ?? "";
  assert.match(table, /OmarSaveSupplier payments and advancesSupplier CR and KYC documents expiring/);
  assert.match(table, /RocheNot assignedSaveNo email address, so nothing can be sentGo-live imports/);
  assert.doesNotMatch(table, /OmarSave.*Go-live imports.*Roche/);
  assert.equal((container.querySelector("input[aria-label='Supplier payments and advances for Omar']") as HTMLInputElement).checked, true);
  assert.equal((container.querySelector("input[aria-label='Alert email for Omar']") as HTMLInputElement).placeholder, "omar@example.invalid");
  assert.match(container.querySelector("table[aria-label='Recent alerts']")?.textContent ?? "", /Omaromar@example\.invalid1 thing is waiting for your approvalAdvance ADV-1 to Gulf Timber/);
});

test("alert types and the address are assigned, and alerts are sent on demand", async () => {
  act(() => root.render(<EmailAlerts csrfToken="csrf" />));
  await flush();
  const box = container.querySelector("input[aria-label='Supplier CR and KYC documents expiring for Omar']") as HTMLInputElement;
  await act(async () => box.click());
  await flush();
  assert.deepEqual(JSON.parse(String(calls.find((call) => call.init?.method === "POST")!.init?.body)), { user_id: 5, types: ["payments", "supplier_documents"] });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Omar now receives alerts for supplier cr and kyc documents expiring/);
  const address = container.querySelector("input[aria-label='Alert email for Omar']") as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(address), "value")?.set?.call(address, "omar.alerts@example.invalid");
    address.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => (address.parentElement!.querySelector("button") as HTMLButtonElement).click());
  await flush();
  assert.deepEqual(JSON.parse(String(calls.filter((call) => call.init?.method === "POST")[1].init?.body)), { user_id: 5, email: "omar.alerts@example.invalid" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Omar: alerts now go to omar\.alerts@example\.invalid/);
  const send = [...container.querySelectorAll("button")].find((node) => node.textContent === "Send what is new now")!;
  await act(async () => send.click());
  await flush();
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /2 alert emails recorded/);
});
