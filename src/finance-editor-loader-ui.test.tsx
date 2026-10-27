// @ts-expect-error Node-only test API; frontend build intentionally has no Node types.
import assert from "node:assert/strict";
// @ts-expect-error Node-only test API, provided by the existing test runtime.
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceEditorLoader } from "./FinanceEditorLoader";
import type { EditorLoader } from "./FinanceEditorLoader";
import { FinanceLabNavigation } from "./FinanceLabNavigation";

let host: HTMLDivElement, root: ReturnType<typeof createRoot>;
beforeEach(() => { host = document.createElement("div"); document.body.append(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
function deferred() {
  let resolve!: (value: Awaited<ReturnType<EditorLoader>>) => void, reject!: (error: Error) => void;
  const promise = new Promise<Awaited<ReturnType<EditorLoader>>>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const A = () => <label>Explicit local draft<input aria-label="Local draft" defaultValue="Keep local work" /></label>;
const B = () => <h1>Selected B editor</h1>;
async function render(load: EditorLoader, label = "selected editor") {
  await act(async () => root.render(<><FinanceLabNavigation /><FinanceEditorLoader load={load} label={label} /></>));
}

test("deferred editor has announced loading and usable navigation without mounting a form", async () => {
  const pending = deferred();
  await render(() => pending.promise);
  assert.match(host.querySelector('[role="status"]')?.textContent ?? "", /Loading selected editor/);
  assert.equal(host.querySelector('[role="status"]')?.getAttribute("aria-busy"), "true");
  assert.ok(host.querySelector('nav a[href="/lab-review-ui/"]'));
  assert.equal(host.querySelector("input"), null);
  await act(async () => pending.resolve({ default: A }));
  assert.equal(host.querySelector('[role="status"]'), null);
  assert.ok(host.querySelector('[aria-label="Local draft"]'));
});

test("failed import has explicit user retry; retry does not invoke a financial API or reload", async () => {
  let calls = 0, requests = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { requests++; throw new Error("No API is needed"); };
  const load: EditorLoader = async () => {
    calls++; if (calls === 1) throw new Error("Synthetic chunk unavailable");
    return { default: A };
  };
  try {
    await render(load);
    assert.match(host.querySelector('[role="alert"]')?.textContent ?? "", /could not be loaded/);
    assert.match(host.textContent ?? "", /does not save or resubmit a draft/);
    assert.equal(calls, 1);
    const retry = [...host.querySelectorAll("button")].find((button) => button.textContent === "Retry loading view")!;
    await act(async () => retry.click());
    assert.equal(calls, 2);
    assert.ok(host.querySelector('[aria-label="Local draft"]'));
    assert.equal(requests, 0);
  } finally { globalThis.fetch = originalFetch; }
});

test("obsolete success cannot mount a previous editor while the current one is loading", async () => {
  const older = deferred(), newer = deferred();
  const oldLoad = () => older.promise, newLoad = () => newer.promise;
  await render(oldLoad, "A");
  await render(newLoad, "B");
  await act(async () => older.resolve({ default: A }));
  assert.equal(host.querySelector("input"), null);
  assert.match(host.querySelector('[role="status"]')?.textContent ?? "", /Loading B/);
  await act(async () => newer.resolve({ default: B }));
  assert.match(host.querySelector("h1")?.textContent ?? "", /Selected B/);
});

test("obsolete failure cannot replace the newly loaded view", async () => {
  const older = deferred();
  await render(() => older.promise);
  await render(async () => ({ default: B }), "B");
  await act(async () => older.reject(new Error("Old import failed")));
  assert.equal(host.querySelector('[role="alert"]'), null);
  assert.match(host.querySelector("h1")?.textContent ?? "", /Selected B/);
});

test("same-loader shell rerenders preserve editor identity and local inputs", async () => {
  let calls = 0;
  const load: EditorLoader = async () => { calls++; return { default: A }; };
  await render(load);
  const field = host.querySelector("input")!;
  field.value = "Unsaved exact work";
  await render(load);
  assert.equal(host.querySelector("input"), field);
  assert.equal(field.value, "Unsaved exact work");
  assert.equal(calls, 1);
});

test("unmount invalidates a pending import without mounting or requesting data", async () => {
  const pending = deferred();
  let mounts = 0;
  await render(() => pending.promise);
  await act(async () => root.render(<p>Another destination</p>));
  await act(async () => pending.resolve({ default: () => { mounts++; return <A />; } }));
  assert.equal(mounts, 0);
  assert.equal(host.textContent, "Another destination");
});
