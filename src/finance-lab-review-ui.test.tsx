// @ts-nocheck
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceLabReview } from "./FinanceLabReview";
import { FinanceLabRecoveryPanel } from "./FinanceLabRecoveryPanel";
import { FinanceSupplierAdvanceLab } from "./FinanceSupplierAdvanceLab";
import { reopenFromQuery } from "./finance-lab-deep-link";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const originalFetch = globalThis.fetch;
const originalSearch = window.location.search;
const groups = [{
  resource: "assets", label: "Fixed assets", editor_path: "/lab-assets-ui/",
  items: [{ id: 42, revision: 3, label: "Studio equipment", updated_at: "2025-04-12T10:30:00Z" }],
}];
function response(data: unknown, status = 200) { return new Response(JSON.stringify(data), { status }); }

const findGroups = [...groups, {
  resource: "invoices", label: "Supplier invoices", editor_path: "/lab-invoices-ui/",
  items: [{ id: 42, revision: 1, label: "Invoice REF-42", updated_at: "2025-04-13T10:30:00Z" }],
}];
async function findSetup(data = findGroups) {
  const calls: string[] = [];
  globalThis.fetch = async (input, init) => {
    assert.equal(init?.method ?? "GET", "GET");
    calls.push(String(input));
    return response(String(input) === "/lab/session/" ? { authenticated: true } : { groups: data });
  };
  await act(async () => root.render(<FinanceLabReview />));
  await act(async () => new Promise(resolve => setTimeout(resolve, 0)));
  return calls;
}
async function findInput(value: string) {
  await act(async () => {
    const input = host.querySelector('[aria-label="Search saved drafts"]') as HTMLInputElement;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function findType(value: string) {
  await act(async () => {
    const select = host.querySelector('[aria-label="Draft type"]') as HTMLSelectElement;
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

test("saved finder searches labels without requests or mutating authorized rows", async () => {
  const original = JSON.stringify(findGroups), calls = await findSetup();
  const initial = calls.length;
  await findInput("STUDIO");
  assert.equal(host.querySelectorAll(".lab-review-reopen").length, 1);
  assert.ok(host.querySelector('a[href="/lab-assets-ui/?reopen=42"]'));
  assert.match(host.querySelector('[role="status"]')?.textContent ?? "", /1 of 2/);
  assert.equal(calls.length, initial);
  assert.equal(JSON.stringify(findGroups), original);
});

test("exact IDs can repeat across types and explicit type selection disambiguates", async () => {
  const calls = await findSetup(), initial = calls.length;
  await findInput("#42");
  assert.equal(host.querySelectorAll(".lab-review-reopen").length, 2);
  await findType("invoices");
  assert.equal(host.querySelectorAll(".lab-review-reopen").length, 1);
  assert.ok(host.querySelector('a[href="/lab-invoices-ui/?reopen=42"]'));
  assert.equal(calls.length, initial);
  assert.doesNotMatch(host.querySelector(".lab-review-groups")?.textContent ?? "", /Studio equipment/);
  assert.doesNotMatch(host.textContent ?? "", /QAR\s*[\d,]/i);
});

test("no match is distinct from no saved data and clear restores the original order", async () => {
  const calls = await findSetup(), initial = calls.length;
  await findInput("NO-MATCH");
  assert.match(host.textContent ?? "", /No saved drafts match these filters/);
  assert.doesNotMatch(host.textContent ?? "", /Nothing saved yet/);
  assert.equal(host.querySelectorAll(".lab-review-reopen").length, 0);
  await act(async () => (host.querySelector(".lab-review-filters button") as HTMLButtonElement).click());
  assert.deepEqual([...host.querySelectorAll(".lab-review-reopen")].map(node => node.getAttribute("href")),
    ["/lab-assets-ui/?reopen=42", "/lab-invoices-ui/?reopen=42"]);
  assert.equal(calls.length, initial);
});

test("empty listing stays honest and offers no saved-record links", async () => {
  await findSetup(findGroups.map(group => ({ ...group, items: [] })));
  assert.match(host.textContent ?? "", /Nothing saved yet/);
  assert.equal(host.querySelectorAll(".lab-review-reopen").length, 0);
  await findType("assets");
  assert.match(host.textContent ?? "", /No saved drafts match these filters/);
  assert.equal(host.querySelectorAll(".lab-review-reopen").length, 0);
});

test("refresh removing a selected type never silently widens the filter or retains old rows", async () => {
  await findSetup();
  await findType("assets");
  globalThis.fetch = async input => response(String(input) === "/lab/session/" ? { authenticated: true }
    : { groups: [findGroups[1]] });
  await act(async () => ([...host.querySelectorAll("button")].find(button => button.textContent === "Refresh register")!).click());
  await act(async () => new Promise(resolve => setTimeout(resolve, 0)));
  assert.match(host.textContent ?? "", /selected draft type is unavailable/i);
  assert.equal(host.querySelectorAll(".lab-review-reopen").length, 0);
  assert.doesNotMatch(host.querySelector(".lab-review-register")?.textContent ?? "", /Studio equipment/);
  await act(async () => (host.querySelector(".lab-review-filters button") as HTMLButtonElement).click());
  assert.ok(host.querySelector('a[href="/lab-invoices-ui/?reopen=42"]'));
});

test("denied refresh removes saved labels, finder and links without exposing cached results", async () => {
  await findSetup();
  await findInput("Studio");
  globalThis.fetch = async () => response({ detail: "Denied" }, 403);
  await act(async () => ([...host.querySelectorAll("button")].find(button => button.textContent === "Refresh register")!).click());
  await act(async () => new Promise(resolve => setTimeout(resolve, 0)));
  assert.match(host.textContent ?? "", /Access is not available/);
  assert.equal(host.querySelectorAll(".lab-review-reopen").length, 0);
  assert.equal(host.querySelector('[aria-label="Search saved drafts"]'), null);
  assert.doesNotMatch(host.textContent ?? "", /Studio equipment|Invoice REF-42/);
});

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  window.history.replaceState({}, "", "/lab-review-ui/");
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  globalThis.fetch = originalFetch;
  window.history.replaceState({}, "", `/lab-review-ui/${originalSearch}`);
});

test("review index renders saved groups and links directly to saved editor draft", async () => {
  globalThis.fetch = async () => response({ groups });
  await act(async () => root.render(<FinanceLabReview />));
  await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
  assert.match(host.textContent ?? "", /Fixed assets/);
  assert.match(host.textContent ?? "", /Studio equipment/);
  assert.equal(host.querySelector('a[href="/lab-assets-ui/?reopen=42"]')?.textContent?.trim(), "Reopen →");
  assert.doesNotMatch(host.textContent ?? "", /QAR\s*[\d,]/i);
});

test("review index presents a clear forbidden state and retry", async () => {
  globalThis.fetch = async () => response({ detail: "Forbidden" }, 403);
  await act(async () => root.render(<FinanceLabReview />));
  await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
  assert.match(host.textContent ?? "", /Access is not available/);
  assert.match(host.textContent ?? "", /synthetic test session and entity context/);
  assert.ok(host.querySelector('[role="alert"]'));
  assert.ok([...host.querySelectorAll("button")].some((button) => button.textContent === "Try again"));
});

test("reopen query validation opens only a positive safe integer ID", () => {
  const opened: number[] = [];
  const errors: string[] = [];
  window.history.replaceState({}, "", "/lab-assets-ui/?reopen=42");
  reopenFromQuery((id) => opened.push(id), (message) => errors.push(message));
  window.history.replaceState({}, "", "/lab-assets-ui/?reopen=0");
  reopenFromQuery((id) => opened.push(id), (message) => errors.push(message));
  assert.deepEqual(opened, [42]);
  assert.match(errors[0], /valid positive saved draft ID/);
});

test("an editor deep link waits for setup and reopens through its existing saved-record loader", async () => {
  const calls: string[] = [];
  const saved = {
    id: 42, revision: 2, supplier_reference: "SUP-42", document_reference: "ADV-42",
    proposed_date: "2025-04-12", amount: "2.00", purpose: "Review only", account_id: 12,
    business_unit_id: null, dimension_value_ids: [], currency: "QAR",
    review: { currency: "QAR", total: "2.00", proposed: true, unpaid: true, unapplied: true,
      actual_payment: false, invoice_application: false, operational_payable_change: false, approved: false, posted: false },
    history: [],
  };
  const context = {
    legal_entity_label: "Synthetic review entity", accounts: [{ id: 12, code: "6100", name_en: "Materials", name_ar: "" }],
    business_units: [], dimensions: [], dimension_values: [],
  };
  window.history.replaceState({}, "", "/lab-supplier-advances-ui/?reopen=42");
  globalThis.fetch = async (input) => {
    const path = String(input);
    calls.push(path);
    if (path === "/lab/session/") return response({ authenticated: true });
    if (path === "/lab/context/") return response(context);
    if (path === "/lab/supplier-advances/") return response({ advances: [] });
    if (path === "/lab/supplier-advances/42/") return response(saved);
    return response({ detail: "Unexpected request" }, 404);
  };
  await act(async () => root.render(<FinanceSupplierAdvanceLab />));
  await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
  assert.ok(calls.indexOf("/lab/context/") < calls.indexOf("/lab/supplier-advances/42/"));
  assert.ok(host.textContent?.includes("Editing supplier-advance proposal 42, revision 2"));
});

test("recovery panel exposes unknown outcome, caution, explicit retry, and acknowledged draft link", async () => {
  const attempt = {
    id: "request-1", path: "assets/42", canRetry: true, state: "unknown",
    detail: "The save response was interrupted.", editorPath: "/lab-assets-ui/", draftId: 42,
  };
  await act(async () => root.render(<FinanceLabRecoveryPanel attempts={[attempt]} />));
  assert.match(host.textContent ?? "", /Outcome unknown/);
  assert.match(host.textContent ?? "", /404 does not prove an earlier save did not commit/);
  assert.ok([...host.querySelectorAll("button")].some((button) => button.textContent?.includes("Retry original request")));
  assert.ok([...host.querySelectorAll("button")].some((button) => button.textContent?.includes("Check request status")));
  await act(async () => root.render(<FinanceLabRecoveryPanel attempts={[{ ...attempt, state: "saved" }]} />));
  assert.ok(host.querySelector('a[href="/lab-assets-ui/?reopen=42"]'));
});
