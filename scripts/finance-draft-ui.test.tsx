import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceDraftWorkspace } from "../src/FinanceDraftWorkspace";

let host, root, networkCalls;
const nativeFetch = globalThis.fetch;

function control(label) {
  const found = host.querySelector(`[aria-label="${label}"]`);
  assert.ok(found, `Missing labeled control: ${label}`);
  return found;
}

function button(text) {
  const found = [...host.querySelectorAll("button")].find((element) => element.textContent?.startsWith(text));
  assert.ok(found, `Missing button: ${text}`);
  return found;
}

async function fill(label, value) {
  const element = control(label);
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), "value").set.call(element, value);
    element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}

async function click(text) {
  await act(async () => button(text).click());
}

async function addAccount(code = "TEST-DRAFT-ACCT-A") {
  await fill("Draft account code", code);
  await fill("Account name English", "Synthetic proposed account");
  await fill("Account name Arabic (optional)", "حساب تجريبي مقترح");
  await click("Add draft account");
}

async function prepareTwoLines(debit = "0.30", credit = "0.30") {
  await addAccount();
  await fill("Draft journal title English", "Synthetic journal review");
  await fill("Draft journal title Arabic (optional)", "مراجعة قيد تجريبي");
  for (const index of [1, 2]) {
    await fill(`Draft account line ${index}`, "TEST-DRAFT-ACCT-A");
  }
  await fill("Debit line 1", debit);
  await fill("Credit line 1", "0.00");
  await fill("Debit line 2", "0.00");
  await fill("Credit line 2", credit);
}

beforeEach(async () => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  networkCalls = [];
  globalThis.fetch = (...args) => {
    networkCalls.push(args);
    throw new Error("This isolated draft prototype must not make network requests.");
  };
  await act(async () => root.render(<FinanceDraftWorkspace
    legalEntityLabel="TEST ONLY synthetic entity"
    businessUnits={[{ code: "TEST-BU-A", nameEn: "Synthetic unit A", nameAr: "وحدة تجريبية أ" }]}
  />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  globalThis.fetch = nativeFetch;
});

test("isolated English account/dimension setup and exact balanced draft review use no network or posting", async () => {
  await prepareTwoLines("0.30", "0.10");
  await fill("Draft dimension code", "TEST-DRAFT-DIM-EXAMPLE");
  await fill("Dimension label English", "Synthetic dimension");
  await fill("Dimension label Arabic (optional)", "بُعد تجريبي");
  await click("Add draft dimension");
  assert.equal(control("Dimension for value").value, "TEST-DRAFT-DIM-EXAMPLE");
  await fill("Draft dimension value code", "TEST-DRAFT-VAL-EXAMPLE");
  await fill("Dimension value English", "Synthetic value");
  await fill("Dimension value Arabic (optional)", "قيمة تجريبية");
  await click("Add draft dimension value");
  await fill("Dimension value line 1", "TEST-DRAFT-VAL-EXAMPLE");
  await fill("Business unit line 1", "TEST-BU-A");
  await click("Add draft line");
  await fill("Draft account line 3", "TEST-DRAFT-ACCT-A");
  await fill("Debit line 3", "0.00");
  await fill("Credit line 3", "0.20");
  await click("Review draft balance");

  const status = host.querySelector('[role="status"]');
  assert.ok(status);
  assert.match(status.textContent, /Balanced draft — not approved or posted/);
  assert.match(status.textContent, /TEST-BU-A · Synthetic unit A/);
  assert.deepEqual([...status.querySelectorAll("strong")].map((element) => element.textContent), ["0.30", "0.30"]);
  assert.equal(button("Save in this local session").disabled, false);
  await click("Save in this local session");
  assert.match(host.querySelector('[aria-label="Session-only synthetic drafts"]').textContent, /Synthetic journal review/);
  assert.ok(host.querySelector('[lang="ar"][dir="rtl"]'));
  assert.match(host.textContent, /NOT AVAILABLE TO SHARED USERS/);
  assert.match(host.textContent, /No Finance permission or user-facing API is configured/);
  assert.equal(networkCalls.length, 0);
  assert.equal([...host.querySelectorAll("button")].some((element) => /^Post |^Approve /.test(element.textContent)), false);
});

test("English validation explains unbalanced and invalid decimal drafts while preserving input", async () => {
  await prepareTwoLines("10.00", "9.99");
  await click("Review draft balance");
  assert.match(host.querySelector('[role="status"]').textContent, /Draft totals do not balance/);
  assert.doesNotMatch(host.querySelector(".finance-draft-review ul")?.textContent ?? "", /[\u0600-\u06FF]/);
  assert.equal(button("Save in this local session").disabled, true);
  assert.equal(control("Debit line 1").value, "10.00");
  assert.equal(control("Credit line 2").value, "9.99");

  await fill("Credit line 2", "10.001");
  await click("Review draft balance");
  assert.match(host.querySelector('[role="status"]').textContent, /at most two decimal places/);
  assert.equal(button("Save in this local session").disabled, true);
  assert.equal(networkCalls.length, 0);
});

test("changing a reviewed draft invalidates the review without clearing its account or memo", async () => {
  await prepareTwoLines();
  await fill("Line memo English 1", "Keep this draft memo");
  await click("Review draft balance");
  assert.equal(button("Save in this local session").disabled, false);
  await fill("Debit line 1", "0.31");
  assert.equal(host.querySelector('[role="status"]'), null);
  assert.equal(button("Save in this local session").disabled, true);
  assert.equal(control("Draft account line 1").value, "TEST-DRAFT-ACCT-A");
  assert.equal(control("Line memo English 1").value, "Keep this draft memo");
});

test("synthetic code validation prevents accidental operational account setup and the prototype is unmounted", async () => {
  await addAccount("OPERATING-ACCOUNT");
  assert.match(host.querySelector('[role="alert"]').textContent, /TEST-DRAFT-ACCT-/);
  assert.equal(host.querySelector('[aria-label="Local draft accounts"]'), null);
  assert.equal(networkCalls.length, 0);
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.equal(app.includes("FinanceDraftWorkspace"), false, "No unapproved Finance UI route is mounted.");
});
