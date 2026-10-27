import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import WarehouseCapacityAuditRegister from "../src/WarehouseCapacityAuditRegister";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; method: string | undefined }[];

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}

function pageData(page: number) {
  const event = page === 1
    ? {
        id: 9,
        kind: "capacity_edited",
        label: "Warehouse capacity edited",
        location: { id: 41, code: "TEST-WH-41", name: "TEST ONLY Warehouse" },
        actor: "TEST ONLY capacity_editor",
        target_user: null,
        capability: null,
        created_at: "2026-10-18T12:00:00Z",
      }
    : {
        id: 8,
        kind: "assigned",
        label: "Capacity permission assigned",
        location: { id: 41, code: "TEST-WH-41", name: "TEST ONLY Warehouse" },
        actor: "System administrator",
        target_user: "TEST ONLY capacity_manager",
        capability: "view_capacity",
        created_at: "2026-10-18T11:00:00Z",
      };
  return {
    results: [event],
    pagination: {
      page,
      page_size: 20,
      total_count: 2,
      total_pages: 2,
      previous_page: page === 1 ? null : 1,
      next_page: page === 1 ? 2 : null,
    },
  };
}

async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}

function mount(isSuperuser = true) {
  root = createRoot(container);
  act(() => root.render(<WarehouseCapacityAuditRegister isSuperuser={isSuperuser} />));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  calls = [];
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, method: options.method });
    const page = Number(url.searchParams.get("page"));
    return json(pageData(page));
  };
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("non-superuser does not see or request the administrator-only audit register", async () => {
  mount(false);
  await flush();
  assert.equal(container.textContent, "");
  assert.equal(calls.length, 0);
});

test("superuser sees an explicit empty audit state", async () => {
  globalThis.fetch = async () => json({
    results: [],
    pagination: {
      page: 1, page_size: 20, total_count: 0, total_pages: 1,
      previous_page: null, next_page: null,
    },
  });
  mount();
  await flush();
  assert.match(container.textContent ?? "", /Warehouse capacity audit/);
  assert.match(container.textContent ?? "", /No warehouse-capacity audit events are available/);
  assert.equal(container.querySelector("table"), null);
});

test("superuser sees metadata-only events and accessible pagination without a write request", async () => {
  mount();
  await flush();
  assert.match(container.textContent ?? "", /Warehouse capacity edited/);
  assert.match(container.textContent ?? "", /TEST-WH-41 · TEST ONLY Warehouse/);
  assert.match(container.textContent ?? "", /TEST ONLY capacity_editor/);
  assert.match(container.textContent ?? "", /2 events · page 1 of 2/);
  assert.equal(container.querySelectorAll("tbody tr").length, 1);
  const next = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Next page");
  assert.equal((next as HTMLButtonElement | undefined)?.disabled, false);
  assert.ok(calls.every(({ method }) => method === undefined || method === "GET"));

  assert.ok(next);
  await act(async () => next.click());
  await flush();
  assert.match(container.textContent ?? "", /Capacity permission assigned/);
  assert.match(container.textContent ?? "", /TEST ONLY capacity_manager/);
  assert.match(container.textContent ?? "", /view_capacity/);
  assert.match(container.textContent ?? "", /2 events · page 2 of 2/);
  const nextAfterLastPage = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Next page");
  assert.equal((nextAfterLastPage as HTMLButtonElement | undefined)?.disabled, true);
  assert.equal(calls.length, 2);
  assert.deepEqual(
    calls.map(({ url }) => [url.pathname, url.searchParams.get("page"), url.searchParams.get("page_size")]),
    [
      ["/api/governance/warehouse-capacity/audit/", "1", "20"],
      ["/api/governance/warehouse-capacity/audit/", "2", "20"],
    ],
  );
});
