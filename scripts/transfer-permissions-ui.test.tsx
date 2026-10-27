import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import TransferPermissions from "../src/TransferPermissions";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let posts: unknown[];
let status = 200;
let actions: string[];

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}

async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  posts = [];
  status = 200;
  actions = ["approve_transfer"];
  globalThis.fetch = (async (input: RequestInfo | URL, options: RequestInit = {}) => {
    if (options.method === "POST") {
      const body = JSON.parse(String(options.body));
      posts.push({ body, headers: options.headers });
      actions = body.enabled ? [...actions, body.action] : actions.filter((item) => item !== body.action);
      return json({ changed: true });
    }
    if (status !== 200) return json({ detail: "Only an ERP/system administrator may change transfer permissions." }, status);
    return json({
      unit: { id: 1, code: "TVL", name: "TEST ONLY Tivoli" },
      actions: [
        { action: "request_transfer", label: "Request stock transfer" },
        { action: "approve_transfer", label: "Approve stock transfer" },
        { action: "execute_transfer", label: "Schedule and carry out stock transfer" },
      ],
      users: [{ id: 5, username: "test_manager", name: "TEST ONLY Manager", actions }],
      history: [],
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
  act(() => root.render(<TransferPermissions selectedUnitId={1} csrfToken="TEST-ONLY-CSRF" />));
  await flush();
}

test("ticking a box grants the permission for the selected unit", async () => {
  await mount();
  const box = container.querySelector<HTMLInputElement>('input[aria-label="Request (showroom admin) for test_manager"]');
  assert.ok(box);
  assert.equal(box.checked, false);
  await act(async () => box.click());
  await flush();
  assert.deepEqual((posts[0] as { body: unknown }).body, { unit_id: 1, user_id: 5, action: "request_transfer", enabled: true });
  assert.equal((posts[0] as { headers: Record<string, string> }).headers["X-CSRFToken"], "TEST-ONLY-CSRF");
  assert.equal(container.querySelector<HTMLInputElement>('input[aria-label="Request (showroom admin) for test_manager"]')?.checked, true);
});

test("a refused account sees the server's explanation and no controls", async () => {
  status = 403;
  await mount();
  assert.match(container.textContent ?? "", /Only an ERP\/system administrator/);
  assert.equal(container.querySelector("input"), null);
});
