// @ts-nocheck
// Node harness follows repository test convention; application modules remain typechecked.
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { labRequest } from "./finance-draft-lab-api";
import {
  setRecoverySession, protectedCreate, recoveryAttempts, checkRecovery,
  retryRecovery, acknowledgeReopen,
} from "./finance-lab-recovery";

const originalFetch = globalThis.fetch;
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
let stored: Map<string, string>;
const ok = (data: unknown, status = 200) => new Response(JSON.stringify(data), {status});
const lost = () => Promise.reject(new Error("Synthetic lost acknowledgement"));
beforeEach(() => {
  setRecoverySession("", "");
  stored = new Map();
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true, value: {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, value),
    },
  });
  setRecoverySession("cluster-actor-entity-A", "synthetic-csrf");
});
afterEach(() => {
  setRecoverySession("", "");
  globalThis.fetch = originalFetch;
  if (originalStorage) Object.defineProperty(globalThis, "sessionStorage", originalStorage);
  else Reflect.deleteProperty(globalThis, "sessionStorage");
});

test("same canonical request reuses identity after uncertainty; conflicting inputs have distinct identities", async () => {
  const ids: string[] = [];
  const send = (id: string) => { ids.push(id); return lost(); };
  await assert.rejects(protectedCreate("supplier-advances", {purpose: "PRIVATE CONTENT", amount: "0.10"}, send));
  await assert.rejects(protectedCreate("supplier-advances", {amount: "0.10", purpose: "PRIVATE CONTENT"}, send));
  await assert.rejects(protectedCreate("supplier-advances", {amount: "0.11", purpose: "PRIVATE CONTENT"}, send));
  assert.equal(ids[0], ids[1]);
  assert.notEqual(ids[0], ids[2]);
  assert.equal(recoveryAttempts().length, 2);
  assert.equal([...stored.values()].some(text=>text.includes("PRIVATE CONTENT") || text.includes("0.10")), false);
});

test("reload retains only scoped identity metadata, not payload or an executable retry", async () => {
  await assert.rejects(protectedCreate("supplier-advances", {purpose: "PRIVATE"}, lost));
  const id = recoveryAttempts()[0].id;
  setRecoverySession("different-actor", "other");
  assert.deepEqual(recoveryAttempts(), []);
  setRecoverySession("cluster-actor-entity-A", "synthetic");
  assert.equal(recoveryAttempts()[0].id, id);
  assert.equal(recoveryAttempts()[0].canRetry, false);
  await assert.rejects(retryRecovery(id), /Original input is not held/);
});

test("404 remains unknown; saved status identifies original outcome and successful reopen clears attention", async () => {
  await assert.rejects(protectedCreate("supplier-advances", {amount: "0.10"}, lost));
  const id = recoveryAttempts()[0].id;
  globalThis.fetch = async () => ok({detail: "May still be running"}, 404);
  await checkRecovery(id);
  assert.equal(recoveryAttempts()[0].state, "unknown");
  globalThis.fetch = async () => ok({state: "saved", resource: "supplier-advances",
    editor_path: "/lab-supplier-advances-ui/", draft_id: 18, original_revision: 1});
  await checkRecovery(id);
  assert.equal(recoveryAttempts()[0].draftId, 18);
  assert.equal(recoveryAttempts()[0].canRetry, false);
  // Full editor navigation recreates the module: retain only metadata and
  // require the actual authorized detail GET before removing attention.
  setRecoverySession("", "");
  setRecoverySession("cluster-actor-entity-A", "synthetic");
  assert.equal(recoveryAttempts()[0].state, "unknown");
  acknowledgeReopen("supplier-advances/17");
  assert.equal(recoveryAttempts().length, 1);
  acknowledgeReopen("supplier-advances/18");
  assert.deepEqual(recoveryAttempts(), []);
});

test("late status or retry from a previous scope cannot restore its protected metadata", async () => {
  await assert.rejects(protectedCreate("supplier-advances", {amount: "0.10"}, lost));
  const id = recoveryAttempts()[0].id;
  let resolve!: (value: Response) => void;
  globalThis.fetch = () => new Promise<Response>(done => { resolve = done; });
  const pending = checkRecovery(id);
  setRecoverySession("new-actor", "new");
  resolve(ok({state: "saved", resource: "supplier-advances",
    editor_path: "/lab-supplier-advances-ui/", draft_id: 18, original_revision: 1}));
  await pending;
  assert.deepEqual(recoveryAttempts(), []);
  setRecoverySession("cluster-actor-entity-A", "old");
  let complete!: (value: unknown) => void;
  let calls = 0;
  await assert.rejects(protectedCreate("supplier-advances", {amount: "0.10"}, () => {
    if (++calls === 1) return lost();
    return new Promise(done => { complete = done; });
  }));
  const retry = retryRecovery(id);
  setRecoverySession("new-actor", "new");
  complete({id: 18});
  await assert.rejects(retry, /Synthetic session changed/);
  assert.deepEqual(recoveryAttempts(), []);
});

test("403 clears recovery display and status never accepts another resource or arbitrary destination", async () => {
  await assert.rejects(protectedCreate("supplier-advances", {amount: "0.10"}, lost));
  const id = recoveryAttempts()[0].id;
  globalThis.fetch = async () => ok({state: "saved", resource: "invoices",
    editor_path: "https://untrusted.invalid/", draft_id: 1});
  await assert.rejects(checkRecovery(id), /Invalid recorded outcome/);
  globalThis.fetch = async () => ok({detail: "Denied"}, 403);
  await assert.rejects(checkRecovery(id), /access is unavailable/);
  assert.deepEqual(recoveryAttempts(), []);
});

test("transport freezes original input for explicit retry and only reopens acknowledged saved record", async () => {
  const body = {amount: "0.10", purpose: "Original private purpose"};
  const calls: {body: string; id: string}[] = [];
  let first = true;
  globalThis.fetch = async (_url, options) => {
    if (!options?.method) return ok({state: "saved", resource: "supplier-advances",
      editor_path: "/lab-supplier-advances-ui/", draft_id: 22, original_revision: 1});
    calls.push({body: String(options.body),
      id: (options.headers as Record<string, string>)["X-Finance-Request-ID"]});
    if (first) { first = false; throw new Error("Commit acknowledgement lost"); }
    return ok({id: 22, revision: 1}, 201);
  };
  await assert.rejects(labRequest("supplier-advances", "POST", body));
  body.amount = "9.99";
  const id = recoveryAttempts()[0].id;
  await retryRecovery(id);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0], calls[1]);
  assert.equal(JSON.parse(calls[1].body).amount, "0.10");
  assert.equal(recoveryAttempts()[0].state, "saved");
});

test("unreadable successful create response stays recoverable; normal validation error does not bind a request", async () => {
  globalThis.fetch = async () => new Response("lost body", {status: 201});
  await assert.rejects(labRequest("supplier-advances", "POST", {amount: "0.10"}), /acknowledgement unreadable/);
  assert.equal(recoveryAttempts().length, 1);
  globalThis.fetch = async () => ok({detail: "Explicit validation error"}, 400);
  await assert.rejects(labRequest("supplier-advances", "POST", {amount: "invalid"}), /validation error/);
  assert.equal(recoveryAttempts().length, 1);
});
