import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Chat from "../src/Chat";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { method: string; path: string; body?: unknown }[];
let unreadReports: number[];

const amal = { id: 1, username: "amal", name: "Amal Haddad" };
const badr = { id: 2, username: "badr", name: "Badr Saleh" };
const carla = { id: 3, username: "carla", name: "Carla Mendes" };
const direct = {
  id: 10, kind: "direct", title: "Badr Saleh", members: [amal, badr], started_by: 1, can_remove_members: false, unread: 2,
  last_message: { sender: "Badr Saleh", text: "Container cleared", created_at: "2026-10-31T09:00:00Z" },
};
let conversations: unknown[];

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 6; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (label: string) => [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(label)) as HTMLButtonElement;
async function click(element: HTMLElement) { await act(async () => { element.click(); }); await flush(); }
async function type(element: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = element instanceof window.HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  unreadReports = [];
  conversations = [direct];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = init?.method ?? "GET";
    const raw = init?.body;
    const body = raw instanceof FormData
      ? { body: raw.get("body"), files: raw.getAll("files").map((file) => (file as File).name) }
      : raw ? JSON.parse(String(raw)) : undefined;
    calls.push({ method, path: url.pathname + url.search, body });
    if (url.pathname === "/api/chat/conversations/" && method === "GET") return json({ conversations });
    if (url.pathname === "/api/chat/conversations/10/messages/" && method === "GET") {
      if (url.searchParams.get("after")) return json({ messages: [], has_more: false, last_read_message_id: 0 });
      return json({ messages: [
        { id: 100, sender: badr, body: "Has the container cleared?", created_at: "2026-10-31T08:59:00Z" },
        { id: 101, sender: badr, body: "Container cleared", created_at: "2026-10-31T09:00:00Z" },
      ], has_more: false, last_read_message_id: 0 });
    }
    if (url.pathname === "/api/chat/conversations/10/read/") return json({ last_read_message_id: 101, unread: { total: 0, conversations: 0 } });
    if (url.pathname === "/api/chat/conversations/10/messages/" && method === "POST") {
      return json({ id: 102, sender: amal, body: body.body, created_at: "2026-10-31T09:01:00Z",
        attachments: body.files.map((name: string, index: number) => ({ id: 500 + index, name, size: 2048, content_type: "application/pdf", is_picture: false })) }, 201);
    }
    if (url.pathname === "/api/chat/people/") return json({ people: [badr, carla] });
    if (url.pathname === "/api/chat/conversations/" && method === "POST") {
      const group = { id: 11, kind: "group", title: body.title, members: [amal, badr, carla], started_by: 1, can_remove_members: true, unread: 0, last_message: null };
      conversations = [group, direct];
      return json(group, 201);
    }
    if (url.pathname === "/api/chat/conversations/11/messages/") return json({ messages: [], has_more: false, last_read_message_id: 0 });
    return json({ detail: "unexpected" }, 404);
  }) as typeof fetch;
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("chat list shows unread counts and opening a chat marks it read", async () => {
  await act(async () => root.render(<Chat csrfToken="token" username="amal" onUnreadChange={(summary) => unreadReports.push(summary.total)} />));
  await flush();
  assert.match(container.textContent ?? "", /Badr Saleh/);
  assert.equal(container.querySelector(".chat-badge")?.textContent, "2");
  assert.equal(unreadReports.at(-1), 2);

  await click(button("Badr Saleh"));
  assert.match(container.textContent ?? "", /Has the container cleared\?/);
  const read = calls.find((call) => call.path === "/api/chat/conversations/10/read/");
  assert.deepEqual(read?.body, { message_id: 101 });
  assert.equal(unreadReports.at(-1), 0);
  assert.equal(container.querySelector(".chat-badge"), null);
});

test("sending a message posts it and shows it as mine", async () => {
  await act(async () => root.render(<Chat csrfToken="token" username="amal" />));
  await flush();
  await click(button("Badr Saleh"));
  const box = container.querySelector("textarea") as HTMLTextAreaElement;
  await type(box, "  Thanks, booking delivery  ");
  await click(button("Send"));
  const sent = calls.find((call) => call.method === "POST" && call.path === "/api/chat/conversations/10/messages/");
  assert.deepEqual(sent?.body, { body: "Thanks, booking delivery", files: [] });
  const mine = container.querySelector(".chat-message.is-mine .chat-bubble");
  assert.equal(mine?.textContent, "Thanks, booking delivery");
  assert.equal(box.value, "");
});

test("a new group needs a name and people, then opens", async () => {
  await act(async () => root.render(<Chat csrfToken="token" username="amal" />));
  await flush();
  await click(button("New group"));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 250)); });
  await flush();
  const create = button("Create group");
  assert.equal(create.disabled, true);
  await type(container.querySelector('input[placeholder="For example: Warehouse team"]') as HTMLInputElement, "Warehouse team");
  await click(button("Badr Saleh"));
  await click(button("Carla Mendes"));
  assert.equal(button("Create group").disabled, false);
  await click(button("Create group"));
  const created = calls.find((call) => call.method === "POST" && call.path === "/api/chat/conversations/");
  assert.deepEqual(created?.body, { kind: "group", title: "Warehouse team", member_ids: [2, 3] });
  assert.match(container.querySelector(".chat-thread-head")?.textContent ?? "", /Warehouse team/);
  assert.match(container.querySelector(".chat-thread-head")?.textContent ?? "", /You, Badr Saleh, Carla Mendes/);
});

test("files can be attached, removed, and are sent with the message", async () => {
  await act(async () => root.render(<Chat csrfToken="token" username="amal" />));
  await flush();
  await click(button("Badr Saleh"));
  const picker = container.querySelector('input[type="file"]') as HTMLInputElement;
  const choose = async (files: File[]) => {
    Object.defineProperty(picker, "files", { configurable: true, value: files });
    await act(async () => { picker.dispatchEvent(new Event("change", { bubbles: true })); });
  };
  await choose([new File(["%PDF-1.4"], "Quote 114.pdf", { type: "application/pdf" }), new File(["x"], "extra.txt")]);
  assert.match(container.querySelector(".chat-chosen-files")?.textContent ?? "", /Quote 114\.pdf/);
  await click(container.querySelector('button[aria-label="Remove extra.txt"]') as HTMLButtonElement);
  assert.equal(button("Send").disabled, false);
  await click(button("Send"));
  const sent = calls.find((call) => call.method === "POST" && call.path === "/api/chat/conversations/10/messages/");
  assert.deepEqual(sent?.body, { body: "", files: ["Quote 114.pdf"] });
  const link = container.querySelector(".chat-message.is-mine a.chat-file") as HTMLAnchorElement;
  assert.equal(link.getAttribute("href"), "/api/chat/attachments/500/");
  assert.match(link.textContent ?? "", /Quote 114\.pdf/);
  assert.equal(container.querySelector(".chat-chosen-files"), null);

  await choose(Array.from({ length: 6 }, (_, index) => new File(["x"], `f${index}.txt`)));
  assert.match(container.querySelector('[role="alert"]')?.textContent ?? "", /up to 5 files/);
});
