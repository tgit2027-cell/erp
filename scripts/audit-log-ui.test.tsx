import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import AuditLog, { formatDuration } from "../src/AuditLog";
import { sidebarSections } from "../src/sidebar-destinations";

const nativeFetch = globalThis.fetch;
let root, host, requests, denied;
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const event = (id, kind, label, extra = {}) => ({
  id, occurred_at: "2026-10-22T08:00:00Z", kind, label, username: "demo_roche", user_id: 7,
  ip_address: "10.1.2.3", user_agent: "TEST", method: "", path: "", status_code: null, detail: {}, ...extra,
});
const overview = {
  security: {
    sign_ins: 4, failed_sign_ins: 3, unknown_account_attempts: 1, lockouts: 1, refused_sign_ins: 0,
    sign_ins_from_new_address: 1, access_denied: 2, accounts_created: 0, accounts_changed: 0, background_jobs: 1,
    failed_by_address: [{ ip_address: "203.0.113.9", attempts: 3 }],
    recent_alerts: [event(1, "sign_in_failed", "Failed sign-in", { username: "nobody-here", user_id: null, detail: { unknown_account: true } })],
    recent_jobs: [event(9, "system_job", "Background job", { username: "system", user_id: null, ip_address: null,
      detail: { command: "seed_demo", succeeded: true, records: { "foundation.Item": { created: 6, updated: 0, deleted: 0, ids: [1] } } } })],
  },
  users: [{ user_id: 7, username: "demo_roche", name: "Lina Roche", is_active: true, sessions: 2, active_seconds: 5400,
    actions: 3, failed_sign_ins: 1, access_denied: 0, last_sign_in: "2026-10-22T08:00:00Z", last_address: "10.1.2.3" }],
};
const userEvents = (kind) => ({
  user: { id: 7, username: "demo_roche", name: "Lina Roche", is_active: true, is_staff: false, is_superuser: false },
  events: kind === "action" ? [event(3, "action", "Changed data", { method: "POST", path: "/api/favorites/", status_code: 201 }),
    event(4, "action", "Changed data", { method: "PATCH", path: "/api/prices/5/", status_code: 200, detail: { succeeded: true,
      records: { "foundation.ApprovedSellingPrice": { created: 0, updated: 1, deleted: 0, ids: [5] } },
      changes: [{ model: "foundation.ApprovedSellingPrice", id: 5, action: "updated", changed: { selling_price: ["1200.00", "1150.00"] } }] } })]
    : [event(2, "sign_in", "Signed in", { detail: { new_address: true } }), event(3, "action", "Changed data", { method: "POST", path: "/api/favorites/", status_code: 201 })],
  pagination: { page: 1, total_pages: 1, total_count: 2 },
});

async function settle() { await act(async () => { await new Promise(setImmediate); }); }
async function click(element) { await act(async () => element.dispatchEvent(new window.MouseEvent("click", { bubbles: true }))); await settle(); }
async function choose(select, value) {
  Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set.call(select, value);
  await act(async () => select.dispatchEvent(new window.Event("change", { bubbles: true })));
  await settle();
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  requests = [];
  denied = false;
  globalThis.fetch = (path, options = {}) => {
    assert.equal(options.method, "GET");
    const url = new URL(String(path), "https://a.test");
    requests.push(url);
    if (denied) return Promise.resolve(json({ detail: "Only system administrators can read the audit log." }, 403));
    if (url.pathname === "/api/audit/") return Promise.resolve(json(overview));
    if (url.pathname === "/api/audit/users/7/") return Promise.resolve(json(userEvents(url.searchParams.get("kind"))));
    throw new Error(`Unexpected ${url}`);
  };
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  globalThis.fetch = nativeFetch;
});

test("Reports shows the audit log to system administrators only", () => {
  const reports = (access) => sidebarSections(access).find((section) => section.id === "reports")?.children.map((child) => child.id);
  assert.deepEqual(reports({ isStaff: true, isSuperuser: true }), ["main", "reconciliation", "audit"]);
  assert.deepEqual(reports({ isStaff: true, isSuperuser: false }), ["main", "reconciliation"]);
});

test("usage per user, security counts and alerts are shown, and a user opens their activity", async () => {
  await act(async () => root.render(<AuditLog />));
  await settle();
  const text = host.textContent ?? "";
  assert.match(text, /Lina Roche \(demo_roche\)/);
  assert.match(text, /1 h 30 min/);
  assert.match(text, /203\.0\.113\.9 · 3 attempts/);
  assert.match(text, /No account has this username/);
  assert.match(text, /seed_demo · finished/);
  assert.match(text, /Item 6 created/);
  assert.ok(host.querySelector(".al-flag"), "non-zero failure counts are highlighted");
  assert.ok(requests[0].searchParams.get("from"));

  await click(host.querySelector('[aria-label="Show activity of demo_roche"]'));
  assert.match(host.textContent ?? "", /ACTIVITY OF ONE ACCOUNT/);
  assert.match(host.textContent ?? "", /POST \/api\/favorites\/ · 201/);
  assert.match(host.textContent ?? "", /From an address not seen before/);
  await choose(host.querySelector(".al-filter select"), "action");
  assert.equal(requests.at(-1).searchParams.get("kind"), "action");
  assert.match(host.textContent ?? "", /ApprovedSellingPrice 1 updated/);
  assert.match(host.textContent ?? "", /ApprovedSellingPrice #5: selling_price 1200\.00 → 1150\.00/);
  assert.doesNotMatch(host.querySelector(".al-user")?.textContent ?? "", /Signed in/);
  await click([...host.querySelectorAll("button")].find((b) => b.textContent === "Back to all users"));
  assert.match(host.textContent ?? "", /Usage by user/);
});

test("the period selector changes the window and a refusal is explained", async () => {
  await act(async () => root.render(<AuditLog />));
  await settle();
  const before = requests.at(-1).searchParams.get("from");
  await choose(host.querySelector(".al-controls select"), "7");
  assert.notEqual(requests.at(-1).searchParams.get("from"), before);
  denied = true;
  await click([...host.querySelectorAll("button")].find((b) => b.textContent === "Refresh"));
  assert.match(host.textContent ?? "", /Only system administrators can read the audit log/);
});

test("durations read in hours and minutes", () => {
  assert.equal(formatDuration(59), "0 min");
  assert.equal(formatDuration(3660), "1 h 1 min");
});
