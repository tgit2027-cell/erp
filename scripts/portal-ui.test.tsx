import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Portal from "../src/Portal";
import PortalAdmin from "../src/PortalAdmin";
import { sidebarSections } from "../src/sidebar-destinations";
import { routeIsAvailable } from "../src/workspace-hash";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const post = (id: number, extra = {}) => ({
  id, kind: "procedure", kind_label: "Procedure", title: `TEST ONLY Procedure ${id}`, body: "Ask two weeks before.", unit: { id: 7, name: "Tivoli" },
  for_label: "Tivoli", must_read: true, pinned: false, read_by_me: false, event_on: null, event_time: "", event_place: "", file: null,
  status: "published", status_label: "On the portal", posted_by: "Hana", posted_at: "2026-11-05T10:00:00Z", withdrawn_by: "", withdrawn_at: null,
  withdrawn_reason: "", can_manage: false, photos: [], ...extra,
});
const overview = (section: string) => ({
  section, me: { name: "TEST ONLY Aisha Rahman", unit: "Tivoli" },
  posts: section === "manage" ? [post(1, { can_manage: true, reads: { done: 1, total: 3 } })] : [post(2, { kind: "announcement", kind_label: "Announcement", must_read: false })],
  to_read: section === "home" ? [post(1)] : [], upcoming: [post(3, { kind: "event", kind_label: "Event", event_on: "2026-12-18", title: "TEST ONLY National Day" })],
  latest_album: section === "home" ? post(4, { kind: "event", kind_label: "Event", event_on: "2026-01-20", title: "TEST ONLY Kick-off 2026", photos: [{ id: 9, caption: "" }] }) : null,
  post_units: section === "manage" ? [{ id: 7, name: "Tivoli" }] : [], can_post_group: false,
  kinds: [{ value: "announcement", label: "Announcement" }, { value: "event", label: "Event" }], policy: "TEST ONLY rules.",
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
const field = (label: string) => [...container.querySelectorAll("label")].find((node) => node.textContent?.trim().startsWith(label))?.querySelector("input, select, textarea") as HTMLInputElement;
function setValue(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
  input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
}
const writes = () => calls.filter((call) => call.init?.method && call.init.method !== "GET");

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/portal/posts/" && (!init?.method || init.method === "GET")) return json(overview(url.searchParams.get("section") ?? "home"));
    if (url.pathname === "/api/portal/suggestions/" && (!init?.method || init.method === "GET")) {
      return json({ units: [{ id: 7, name: "Tivoli" }], default_unit: 7, topics: [{ value: "idea", label: "An idea" }], mine: [], can_read: true,
        inbox: [{ id: 5, unit: "Tivoli", topic: "idea", topic_label: "An idea", text: "TEST ONLY More shade", sent_at: "2026-11-05T10:00:00Z",
          status: "new", status_label: "New", reply: "", replied_by: "", replied_at: null, sender: "TEST ONLY Aisha Rahman" }] });
    }
    return json({}, 201);
  }) as typeof fetch;
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the portal is in the menu only when switched on, with HR's screens in the HR section", () => {
  const base = { isStaff: false, isSuperuser: false, canManageReportPanels: false };
  assert.equal(sidebarSections(base).some((section) => section.id === "portal"), false);
  assert.equal(routeIsAvailable({ section: "portal", child: "main" }, base), false);
  const on = sidebarSections({ ...base, hrPortalEnabled: true });
  assert.equal(on[0].id, "portal");
  assert.deepEqual(on[0].children.map((child) => child.id),
    ["main", "library", "news", "photos", "leave", "attendance", "overtime", "letters", "suggestions", "password"]);
  assert.ok(on.find((section) => section.id === "hr")?.children.some((child) => child.id === "portal-posts"));
  assert.equal(sidebarSections(base).find((section) => section.id === "hr")?.children.some((child) => child.id === "portal-posts"), false);
});

test("home shows the tiles, what to read, the latest album and upcoming events; tiles open screens", async () => {
  const opened: string[] = [];
  await act(async () => root.render(<Portal view="main" csrfToken="t" onOpen={(child) => opened.push(child)} />));
  await flush();
  assert.match(container.textContent ?? "", /All your HR resources, in one place/);
  assert.match(container.textContent ?? "", /Please read \(1\)/);
  assert.match(container.textContent ?? "", /Kick-off 2026/);
  assert.match(container.textContent ?? "", /National Day/);
  assert.ok(!(container.textContent ?? "").includes("TEST ONLY"));
  await act(async () => button("Leave Request")!.click());
  assert.deepEqual(opened, ["leave"]);
  await act(async () => button("Driver Request")!.click());
  assert.match(container.textContent ?? "", /Driver Request is not in the new portal yet/);
  await act(async () => button("I have read this")!.click());
  await flush();
  const write = writes()[0];
  assert.equal(write.url.pathname, "/api/portal/posts/1/");
  assert.deepEqual(JSON.parse(String(write.init?.body)), { action: "read" });
});

test("staff send a suggestion with their name", async () => {
  await act(async () => root.render(<Portal view="suggestions" csrfToken="t" onOpen={() => undefined} />));
  await flush();
  assert.match(container.textContent ?? "", /Your name is sent with it/);
  await act(async () => setValue(field("Your suggestion"), "TEST ONLY Water cooler"));
  await act(async () => button("Send to HR")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(writes()[0].init?.body)), { unit_id: 7, topic: "idea", text: "TEST ONLY Water cooler" });
});

test("HR posts from the HR section and sees who has read", async () => {
  await act(async () => root.render(<PortalAdmin view="posts" csrfToken="t" />));
  await flush();
  assert.ok(button("Read by 1 of 3"));
  await act(async () => button("New post")!.click());
  await act(async () => setValue(field("Title"), "TEST ONLY Eid holiday"));
  await act(async () => setValue(field("Text"), "Closed on Sunday."));
  await act(async () => button("Post")!.click());
  await flush();
  const body = JSON.parse(String(writes()[0].init?.body));
  assert.equal(body.kind, "announcement");
  assert.equal(body.unit_id, 7);
});

test("HR answers a staff suggestion", async () => {
  await act(async () => root.render(<PortalAdmin view="suggestions" csrfToken="t" />));
  await flush();
  assert.match(container.textContent ?? "", /Aisha Rahman/);
  await act(async () => button("Answer")!.click());
  await act(async () => setValue(container.querySelector("input[aria-label='Answer']") as HTMLInputElement, "Thanks"));
  await act(async () => button("Send answer")!.click());
  await flush();
  assert.equal(writes()[0].url.pathname, "/api/portal/suggestions/5/");
});
