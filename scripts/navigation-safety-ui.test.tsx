import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { useWorkspaceNavigationSafety } from "../src/useWorkspaceNavigationSafety";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let decisions: boolean[];
let confirmations: string[];
let answer = false;
const originalConfirm = window.confirm;
function Probe({ pending = 0, section = "retail" }: { pending?: number; section?: string }) {
  const { canLeaveSection, navigationWarning } = useWorkspaceNavigationSafety(section);
  return <><div className="pw-transactions" data-pending-operation-count={pending}><form><input aria-label="Draft" /></form></div>
    <input aria-label="Unrelated search" />
    <button onClick={() => decisions.push(canLeaveSection("inventory"))}>Leave</button>
    <button onClick={() => decisions.push(canLeaveSection(section))}>Same section</button>
    <div role="alert">{navigationWarning}</div></>;
}
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host); decisions = []; confirmations = []; answer = false;
  window.confirm = (message) => { confirmations.push(String(message)); return answer; };
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); window.confirm = originalConfirm; });
async function render(pending = 0) { await act(async () => root.render(<Probe pending={pending} />)); }
async function click(index = 0) { await act(async () => host.querySelectorAll<HTMLButtonElement>("button")[index].click()); }
async function edit(selector = '[aria-label="Draft"]') {
  await act(async () => {
    const input = host.querySelector<HTMLInputElement>(selector)!;
    input.value = "TEST ONLY local draft";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
}
test("clean navigation and unrelated search need no draft confirmation", async () => {
  await render(); await edit('[aria-label="Unrelated search"]'); await click();
  assert.deepEqual(decisions, [true]); assert.deepEqual(confirmations, []);
});
test("same-section navigation retains a draft without an unnecessary discard prompt", async () => {
  await render(); await edit(); await click(1);
  assert.deepEqual(decisions, [true]); assert.deepEqual(confirmations, []);
  assert.equal(host.querySelector<HTMLInputElement>('[aria-label="Draft"]')!.value, "TEST ONLY local draft");
});
test("cross-section navigation requires explicit discard and cancel keeps the exact draft", async () => {
  await render(); await edit(); await click();
  assert.deepEqual(decisions, [false]); assert.match(confirmations[0], /draft.*retry context/i);
  assert.equal(host.querySelector<HTMLInputElement>('[aria-label="Draft"]')!.value, "TEST ONLY local draft");
  answer = true; await click(); await click();
  assert.deepEqual(decisions, [false, true, true]);
  assert.equal(confirmations.length, 2, "An explicitly discarded draft does not keep prompting");
});
test("pending submission blocks leaving without cancel/retry and remains blocked until settled", async () => {
  await render(1); answer = true; await click(); await click();
  assert.deepEqual(decisions, [false, false]); assert.deepEqual(confirmations, []);
  assert.match(host.querySelector('[role="alert"]')!.textContent!, /pending.*retry identity/i);
  await render(0); await click();
  assert.deepEqual(decisions, [false, false, true]);
  assert.equal(host.querySelector('[role="alert"]')!.textContent, "");
});
