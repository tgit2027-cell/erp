import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import ChatAccessReview from "../src/ChatAccessReview";

const nativeFetch = globalThis.fetch;
const nativeConfirm = window.confirm;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { method: string; path: string; body?: unknown }[];

const hr = { id: 5, username: "hr_lead", name: "Hana Rahman" };
const amal = { id: 1, username: "amal", name: "Amal Haddad" };
const owner = { id: 6, username: "owner", name: "Sam Owner" };
const base = {
  requested_by: hr, subject: amal, reason: "hr_investigation", reason_label: "HR investigation",
  explanation: "HR case 2026-14: complaint about messages in October.", period_start: "2026-10-01", period_end: "2026-10-31",
  created_at: "2026-10-31T10:00:00Z", decided_by: null, decided_at: null, decision_note: "", access_until: null,
  can_read: false, can_decide: false, can_withdraw: false,
};
let requests: unknown[];

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 6; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (label: string) => [...container.querySelectorAll("button")].find((item) => item.textContent === label) as HTMLButtonElement;
async function click(element: HTMLElement) { await act(async () => { element.click(); }); await flush(); }

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  window.confirm = () => true;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: url.pathname, body });
    if (url.pathname === "/api/chat/access-review/") return json({ requests });
    if (url.pathname === "/api/chat/access-review/7/decision/") {
      requests = [{ ...base, id: 7, status: "approved", status_label: "Approved", decided_by: owner, decided_at: "2026-10-31T11:00:00Z", access_until: "2026-11-03T11:00:00Z" }];
      return json(requests[0]);
    }
    if (url.pathname === "/api/chat/access-review/8/conversations/") return json({ conversations: [{ id: 10, kind: "direct", title: "Amal Haddad and Badr Saleh", members: [], messages_in_period: 1 }] });
    if (url.pathname === "/api/chat/access-review/8/conversations/10/messages/") return json({ messages: [{ id: 1, sender: amal, body: "inside the period", created_at: "2026-10-20T09:00:00Z" }] });
    return json({ detail: "unexpected" }, 404);
  }) as typeof fetch;
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
  window.confirm = nativeConfirm;
});

test("the approver approves a pending request after confirming", async () => {
  requests = [{ ...base, id: 7, status: "pending", status_label: "Waiting for approval", can_decide: true }];
  await act(async () => root.render(<ChatAccessReview csrfToken="token" canRequest={false} canApprove />));
  await flush();
  assert.match(container.textContent ?? "", /Waiting for approval/);
  assert.equal(button("Ask to review chats"), undefined);
  assert.equal(button("Reject").disabled, true);
  await click(button("Approve"));
  assert.deepEqual(calls.find((call) => call.path.endsWith("/decision/"))?.body, { decision: "approve", note: "" });
  assert.match(container.textContent ?? "", /Approved/);
  assert.match(container.textContent ?? "", /Reading allowed until/);
});

test("the requester opens an approved review, read-only, with a recorded-access banner", async () => {
  requests = [{ ...base, id: 8, status: "approved", status_label: "Approved", can_read: true, decided_by: owner, decided_at: "2026-10-31T11:00:00Z", access_until: "2026-11-03T11:00:00Z" }];
  await act(async () => root.render(<ChatAccessReview csrfToken="token" canRequest canApprove={false} />));
  await flush();
  await click(button("Open review"));
  assert.match(container.querySelector(".chat-review-banner")?.textContent ?? "", /Every chat you open is recorded/);
  await click([...container.querySelectorAll(".chat-review-list button")][0] as HTMLButtonElement);
  assert.match(container.textContent ?? "", /inside the period/);
  assert.equal(container.querySelector("textarea"), null);
});
