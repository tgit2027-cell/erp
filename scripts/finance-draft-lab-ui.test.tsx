import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FinanceDraftLab } from "../src/FinanceDraftLab";

let host, root, requests, handler, saved, blockSave;
const nativeFetch = globalThis.fetch;
const choices = {
  legal_entity_label: "TEST ONLY",
  business_units: [{ id: 1, code: "TEST-BU-A", name: "Synthetic A" }],
  accounts: [{ id: 1, code: "TEST-DRAFT-ACCT-A", name_en: "Synthetic account", name_ar: "حساب تجريبي" }],
  dimensions: [], dimension_values: [],
};
function exactTestTotal(lines, field) {
  const cents = lines.reduce((sum, line) => {
    const [whole, fraction = ""] = String(line[field]).split(".");
    return sum + BigInt(whole || "0") * 100n + BigInt((fraction + "00").slice(0, 2));
  }, 0n);
  return `${cents / 100n}.${String(cents % 100n).padStart(2, "0")}`;
}
function document(revision = 1, title = "Saved synthetic draft") {
  const base = {
    id: 5, revision, title_en: title, title_ar: "مسودة تجريبية",
    lines: [
      { account_code: "TEST-DRAFT-ACCT-A", business_unit_code: "TEST-BU-A", dimensions: [], debit: "0.30", credit: "0.00", memo_en: "Memo", memo_ar: "" },
      { account_code: "TEST-DRAFT-ACCT-A", business_unit_code: "", dimensions: [], debit: "0.00", credit: "0.30", memo_en: "", memo_ar: "" },
    ],
    review: { debit_total: "0.30", credit_total: "0.30", balanced: true },
  };
  return { ...base, history: [{ revision, created_at: "synthetic observation", snapshot: base }] };
}
beforeEach(async () => {
  host = globalThis.document.createElement("div"); globalThis.document.body.append(host);
  root = createRoot(host); requests = []; saved = document(); blockSave = null;
  handler = null;
  globalThis.fetch = async (url, options = {}) => {
    const call = { path: String(url), method: options.method ?? "GET", body: options.body ? JSON.parse(options.body) : undefined };
    requests.push(call);
    const custom = await handler?.(call);
    if (custom) return new Response(JSON.stringify(custom.data), { status: custom.status });
    if (call.path === "/lab/session/") return new Response(JSON.stringify({ authenticated: true, csrf_token: "SYNTHETIC" }));
    if (call.path === "/lab/context/") return new Response(JSON.stringify(choices));
    if (call.path === "/lab/journals/" && call.method === "GET") return new Response(JSON.stringify({ drafts: [saved] }));
    if (call.method === "PUT" || call.method === "POST") {
      if (blockSave) await blockSave;
      saved = document(call.method === "POST" ? 1 : saved.revision + 1, call.body.title_en);
      if (call.method === "POST") saved.id = 6;
      saved.kind = call.body.kind;
      saved.as_of_date = call.body.as_of_date ?? null;
      saved.recurrence_proposal = call.body.recurrence_proposal;
      saved.adjustment_proposal = call.body.adjustment_proposal;
      saved.title_ar = call.body.title_ar;
      saved.lines = call.body.lines.map((line, index) => ({
        ...(saved.lines[index] ?? {}),
        account_code: line.account_code ?? choices.accounts.find((account) => account.id === line.account_id)?.code,
        business_unit_code: line.business_unit_code ?? choices.business_units.find((unit) => unit.id === line.business_unit_id)?.code ?? "",
        dimensions: line.dimensions ?? [],
        debit: line.debit, credit: line.credit, memo_en: line.memo_en, memo_ar: line.memo_ar,
      }));
      saved.history = [{ revision: saved.revision, created_at: "synthetic observation", snapshot: {
        title_en: saved.title_en, title_ar: saved.title_ar, lines: saved.lines,
         review: { debit_total: exactTestTotal(call.body.lines, "debit"),
           credit_total: exactTestTotal(call.body.lines, "credit") },
         as_of_date: saved.as_of_date,
         adjustment_proposal: saved.adjustment_proposal,
      }}];
    }
    return new Response(JSON.stringify(saved));
  };
  await act(async () => { root.render(<FinanceDraftLab />); });
  await click("Open draft 5:");
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); globalThis.fetch = nativeFetch;
});
function button(prefix) {
  const row = [...host.querySelectorAll("button")].find((node) => node.textContent.startsWith(prefix));
  assert.ok(row, prefix); return row;
}
async function click(prefix) { await act(async () => button(prefix).click()); }
function titleInput() { return host.querySelector('[aria-label="Draft journal title English"]'); }
async function fillTitle(value) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(titleInput(), value);
    titleInput().dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function fillLabeled(label, value) {
  const input = host.querySelector(`[aria-label="${label}"]`);
  assert.ok(input, `Missing English accessible label: ${label}`);
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value").set.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function setSelect(label, value) {
  const select = host.querySelector(`[aria-label="${label}"]`);
  assert.ok(select, `Missing select: ${label}`);
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set.call(select, value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}
async function submitReview() {
  await act(async () => {
    host.querySelector(".finance-draft-journal").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
}

test("Finance lab controls are English while optional Arabic business content survives save and reopen", async () => {
  assert.equal(host.querySelector('nav a[href="/lab-invoices-ui/"]')?.textContent, "Supplier invoices");
  const arabicTitle = host.querySelector('[aria-label="Draft journal title Arabic (optional)"]');
  assert.ok(arabicTitle);
  assert.equal(arabicTitle.value, "مسودة تجريبية");
  assert.ok(host.querySelector('[aria-label="Line memo Arabic (optional) 1"]'));
  assert.equal(button("Save draft to disposable database").textContent, "Save draft to disposable database");
  assert.equal(button("Refresh saved list").textContent, "Refresh saved list");
  assert.equal(host.querySelector("h1").textContent, "Disposable Finance draft lab");
  assert.doesNotMatch([...host.querySelectorAll("button")].map((node) => node.textContent).join(" "), /[\u0600-\u06FF]/);

  const savedArabicTitle = "عنوان عربي محفوظ";
  const savedArabicMemo = "وصف عربي محفوظ";
  await fillLabeled("Draft journal title Arabic (optional)", savedArabicTitle);
  await fillLabeled("Line memo Arabic (optional) 1", savedArabicMemo);
  await click("Review draft balance");
  await click("Save draft to disposable database");
  const write = requests.filter((call) => call.method === "PUT").at(-1);
  assert.equal(write.body.title_ar, savedArabicTitle);
  assert.equal(write.body.lines[0].memo_ar, savedArabicMemo);
  await click("Open draft 5:");
  assert.equal(host.querySelector('[aria-label="Draft journal title Arabic (optional)"]').value, savedArabicTitle);
  assert.equal(host.querySelector('[aria-label="Line memo Arabic (optional) 1"]').value, savedArabicMemo);
  assert.ok(host.textContent.includes(savedArabicTitle));
  await click("Review draft balance");
  assert.ok(host.textContent.includes(savedArabicMemo));
});

test("database save error retains draft and explicit retry; committed save is not misreported after list refresh failure", async () => {
  await fillTitle("Preserved local edits"); await click("Review draft balance");
  let attempt = 0;
  handler = (call) => {
    if (call.method === "PUT" && ++attempt === 1) return { status: 400, data: { detail: "Synthetic validation failure" } };
  };
  await click("Save draft to disposable database");
  assert.equal(titleInput().value, "Preserved local edits");
  assert.ok(host.textContent.includes("Synthetic validation failure"));
  handler = (call) => call.path === "/lab/journals/" ? { status: 503, data: { detail: "List unavailable" } } : null;
  await click("Save draft to disposable database");
  assert.ok(host.textContent.includes("Saved draft 5, revision 2"));
  assert.ok(host.textContent.includes("Draft saved, but list refresh failed"));
  const writes = requests.filter((call) => call.method === "PUT");
  assert.equal(writes.length, 2);
  assert.deepEqual(writes[0].body, writes[1].body);
  assert.equal(requests.filter((call) => call.method === "POST").length, 0);
});

test("409 keeps editor inputs and requires explicit reload confirmation without automatic retry", async () => {
  await fillTitle("Uncommitted local title"); await click("Review draft balance");
  saved = document(2, "Other editor winner");
  handler = (call) => call.method === "PUT" ? { status: 409, data: { detail: "Stale revision", current_revision: 2 } } : null;
  await click("Save draft to disposable database");
  assert.equal(titleInput().value, "Uncommitted local title");
  assert.ok(host.textContent.includes("Conflicting edit:"));
  window.confirm = () => false;
  await click("Reload saved version");
  assert.equal(titleInput().value, "Uncommitted local title");
  window.confirm = () => true;
  await click("Reload saved version");
  assert.equal(titleInput().value, "Other editor winner");
  assert.equal(requests.filter((call) => call.method === "PUT").length, 1);
});

test("transient refresh failure preserves authorized editor; current synthetic session denial clears all protected content", async () => {
  await fillTitle("Keep authorized draft");
  handler = (call) => call.path === "/lab/context/" ? { status: 503, data: { detail: "Transient fixture failure" } } : null;
  await click("Refresh saved list");
  assert.equal(titleInput().value, "Keep authorized draft");
  assert.ok(host.querySelector('[aria-label="Preserved draft revision history"]'));
  handler = () => ({ status: 403, data: { detail: "Synthetic session denied" } });
  await click("Refresh saved list");
  assert.equal(titleInput(), null);
  assert.equal(host.querySelector('[aria-label="Preserved draft revision history"]'), null);
  assert.equal(host.textContent.includes("Synthetic account"), false);
});

test("pending draft save disables navigation and duplicates, preserves the editor and warns on unload", async () => {
  let release;
  blockSave = new Promise((resolve) => { release = resolve; });
  await fillTitle("Pending title"); await click("Review draft balance");
  const editor = host.querySelector(".finance-lab-editor");
  await click("Save draft to disposable database");
  assert.equal(editor.disabled, true);
  assert.equal(button("New draft").disabled, true);
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  assert.equal(event.defaultPrevented, true);
  await click("Save draft to disposable database");
  assert.equal(requests.filter((call) => call.method === "PUT").length, 1);
  await act(async () => release());
  assert.equal(host.querySelector(".finance-lab-editor"), editor);
  assert.equal(titleInput().value, "Pending title");
  assert.equal(editor.disabled, false);
  window.confirm = () => false;
  await click("Add draft line");
  await click("New draft");
  assert.equal(host.querySelector(".finance-lab-editor"), editor);
  assert.equal(host.querySelectorAll(".finance-draft-line").length, 3);
});

test("lost save response is not represented as failure or retried automatically; explicit reopening recovers the committed version", async () => {
  await fillTitle("Response lost"); await click("Review draft balance");
  handler = (call) => {
    if (call.method === "PUT") {
      saved = document(2, call.body.title_en);
      throw new TypeError("Synthetic connection interruption after commit");
    }
  };
  await click("Save draft to disposable database");
  assert.equal(titleInput().value, "Response lost");
  assert.ok(host.textContent.includes("the database may have committed"));
  assert.equal(button("Save draft to disposable database").disabled, true);
  assert.equal(requests.filter((call) => call.method === "PUT").length, 1);
  handler = null;
  await click("Refresh saved list");
  window.confirm = () => true;
  await click("Open draft 5:");
  assert.equal(titleInput().value, "Response lost");
  assert.ok(host.querySelector(".finance-lab-editor legend").textContent.includes("revision 2"));
  await click("Review draft balance");
  assert.equal(button("Save draft to disposable database").disabled, false);
});

function recurringSaved() {
  return { ...document(), kind: "recurring_template", recurrence_proposal: {
    start: "2026-12-01", end: "2027-01-31", dates: ["2026-12-31", "2027-01-02"],
  }};
}
async function fillRecurringDates(value) {
  const input = host.querySelector('[aria-label="Explicit occurrence dates"]');
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
test("recurring templates reopen explicit dates separately; GET preview is labeled and invalidated by local edits", async () => {
  saved = recurringSaved();
  await click("Refresh saved list"); await click("Open template 5:");
  const dates = host.querySelector('[aria-label="Explicit occurrence dates"]');
  assert.equal(dates.value, "2026-12-31\n2027-01-02");
  assert.ok(host.textContent.includes("not scheduled jobs or posted journals"));
  assert.equal([...host.querySelectorAll("button")].some((b) => b.textContent.startsWith("Open draft 5:")), false);
  handler = (call) => call.path.endsWith("/preview/") ? { status: 200, data: {
    template_id: 5, revision: 1, occurrence_count: 2,
    occurrences: saved.recurrence_proposal.dates.map(date => ({ date, debit_total: "0.30", credit_total: "0.30", balanced: true })),
    entry_lines: saved.lines, total_proposed_debits: "0.60", total_proposed_credits: "0.60",
  } } : null;
  await click("Preview saved proposed occurrences");
  assert.ok(host.querySelector("#finance-recurring-preview-title"));
  assert.ok(host.textContent.includes("0.60"));
  await fillRecurringDates("2027-01-03");
  assert.equal(host.querySelector("#finance-recurring-preview-title"), null);
  const count = requests.filter(r => r.path.endsWith("/preview/")).length;
  await click("Preview saved proposed occurrences");
  assert.ok(host.textContent.includes("Save current balanced changes before preview"));
  assert.equal(requests.filter(r => r.path.endsWith("/preview/")).length, count);
  assert.equal(requests.filter(r => ["PUT", "POST"].includes(r.method)).length, 0);
});

test("recurring date validation and conflict retain exact local inputs; explicit reopening replaces them", async () => {
  saved = recurringSaved();
  await click("Refresh saved list"); await click("Open template 5:");
  await fillRecurringDates("01/02/2027"); await click("Review draft balance");
  handler = (call) => call.method === "PUT" ? { status: 400, data: { detail: "Use unambiguous YYYY-MM-DD dates only." } } : null;
  await click("Save draft to disposable database");
  assert.ok(host.textContent.includes("Use unambiguous YYYY-MM-DD"));
  assert.equal(host.querySelector("textarea").value, "01/02/2027");
  await fillRecurringDates("2027-01-03");
  handler = (call) => call.method === "PUT" ? { status: 409, data: { detail: "Synthetic conflicting edit" } } : null;
  await click("Save draft to disposable database");
  assert.ok(host.textContent.includes("Conflicting edit:"));
  assert.equal(host.querySelector("textarea").value, "2027-01-03");
  assert.equal(requests.filter(r => r.method === "PUT").at(-1).body.kind, "recurring_template");
  assert.deepEqual(requests.filter(r => r.method === "PUT").at(-1).body.recurrence_proposal.dates, ["2027-01-03"]);
  handler = null; window.confirm = () => true;
  await click("Reload saved version");
  assert.equal(host.querySelector("textarea").value, "2026-12-31\n2027-01-02");
});

test("current preview access denial clears recurring metadata and entry controls without a write or automatic retry", async () => {
  saved = recurringSaved();
  await click("Refresh saved list"); await click("Open template 5:");
  handler = (call) => call.path.endsWith("/preview/") ? { status: 403, data: { detail: "Synthetic lab access revoked" } } : null;
  await click("Preview saved proposed occurrences");
  assert.equal(host.querySelector(".finance-lab-editor"), null);
  assert.equal(host.querySelector("textarea"), null);
  assert.equal(host.querySelector('[aria-label="Preserved draft revision history"]'), null);
  assert.equal(requests.filter(r => r.path.endsWith("/preview/")).length, 1);
  assert.equal(requests.filter(r => ["PUT", "POST"].includes(r.method)).length, 0);
});

test("uncertain recurring save blocks duplicate retry and reopens the exact committed proposal", async () => {
  saved = recurringSaved();
  await click("Refresh saved list"); await click("Open template 5:");
  await fillRecurringDates("2027-01-03"); await click("Review draft balance");
  handler = (call) => {
    if (call.method === "PUT") {
      saved = { ...recurringSaved(), revision: 2, recurrence_proposal: call.body.recurrence_proposal };
      throw new TypeError("Synthetic lost response after template commit");
    }
  };
  await click("Save draft to disposable database");
  assert.equal(button("Save draft to disposable database").disabled, true);
  assert.equal(host.querySelector("textarea").value, "2027-01-03");
  assert.ok(host.textContent.includes("the database may have committed"));
  assert.equal(requests.filter(r => r.method === "PUT").length, 1);
  handler = null; window.confirm = () => true;
  await click("Refresh saved list"); await click("Open template 5:");
  assert.equal(host.querySelector("textarea").value, "2027-01-03");
  assert.ok(host.querySelector(".finance-lab-editor legend").textContent.includes("revision 2"));
  await click("Review draft balance");
  assert.equal(button("Save draft to disposable database").disabled, false);
});

test("opening-balance worksheet requires and persists its date; reopening preserves the date and synthetic boundary", async () => {
  await click("New opening-balance worksheet");
  assert.ok(host.textContent.includes("not an import"));
  assert.equal(host.querySelector('[aria-label="Opening balance as-of date"]').value, "");
  assert.equal(host.querySelector('[aria-label="Opening balance as-of date"]').checkValidity(), false);

  await fillLabeled("Opening balance as-of date", "2026-03-31");
  await fillTitle("Opening position");
  const setSelect = async (label, value) => {
    const select = host.querySelector(`[aria-label="${label}"]`);
    await act(async () => {
      Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set.call(select, value);
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
  };
  await setSelect("Draft account line 1", "TEST-DRAFT-ACCT-A");
  await setSelect("Draft account line 2", "TEST-DRAFT-ACCT-A");
  await fillLabeled("Debit line 1", "0.30");
  await fillLabeled("Credit line 1", "0");
  await fillLabeled("Debit line 2", "0");
  await fillLabeled("Credit line 2", "0.30");
  await click("Review draft balance");
  assert.ok(host.textContent.includes("Difference (debit − credit):"));
  assert.ok(host.textContent.includes("0.00"));
  assert.equal(button("Save draft to disposable database").disabled, false, host.textContent);
  await click("Save draft to disposable database");
  const post = requests.find((call) => call.method === "POST");
  assert.equal(post.body.kind, "opening_balance");
  assert.equal(post.body.as_of_date, "2026-03-31");
  assert.ok(button("Open worksheet 6:"));

  await click("Open worksheet 6:");
  assert.equal(host.querySelector('[aria-label="Opening balance as-of date"]').value, "2026-03-31");
  await fillLabeled("Opening balance as-of date", "2026-04-01");
  await click("Review draft balance");
  assert.equal(button("Save draft to disposable database").disabled, false, host.textContent);
  await click("Save draft to disposable database");
  const put = requests.filter((call) => call.method === "PUT").at(-1);
  assert.equal(put.body.kind, "opening_balance");
  assert.equal(put.body.as_of_date, "2026-04-01");
  assert.ok(host.textContent.includes("as of 2026-04-01"));
  // Subtract exact minor units, not binary floating point at the field limit.
  await fillLabeled("Debit line 1", "999999999999.99");
  await fillLabeled("Credit line 2", "999999999999.98");
  await click("Review draft balance");
  assert.ok(host.textContent.includes("Difference (debit − credit): 0.01 QAR"));
  assert.equal(button("Save draft to disposable database").disabled, true);
  await fillLabeled("Debit line 1", "999999999999.98");
  await fillLabeled("Credit line 2", "999999999999.99");
  await click("Review draft balance");
  assert.ok(host.textContent.includes("Difference (debit − credit): -0.01 QAR"));
  assert.equal(button("Save draft to disposable database").disabled, true);
});

test("opening-balance date validation and conflicts preserve local date until explicit reload", async () => {
  saved = { ...document(), kind: "opening_balance", as_of_date: "2026-02-28" };
  await click("Refresh saved list");
  await click("Open worksheet 5:");
  await fillLabeled("Opening balance as-of date", "2026-03-31");
  await click("Review draft balance");
  handler = (call) => call.method === "PUT"
    ? { status: 400, data: { detail: "Invalid synthetic as-of date" } } : null;
  await click("Save draft to disposable database");
  assert.equal(host.querySelector('[aria-label="Opening balance as-of date"]').value, "2026-03-31");
  assert.ok(host.textContent.includes("Invalid synthetic as-of date"));

  handler = (call) => call.method === "PUT"
    ? { status: 409, data: { detail: "Synthetic conflicting edit", current_revision: 2 } } : null;
  await click("Save draft to disposable database");
  assert.ok(host.textContent.includes("Conflicting edit:"));
  assert.equal(host.querySelector('[aria-label="Opening balance as-of date"]').value, "2026-03-31");
  handler = null; window.confirm = () => true;
  await click("Reload saved version");
  assert.equal(host.querySelector('[aria-label="Opening balance as-of date"]').value, "2026-02-28");
});

for (const [kind, actionLabel, proposalLabel] of [
  ["accrual_adjustment", "New accrual proposal", "accrual"],
  ["prepayment_adjustment", "New prepayment proposal", "prepayment"],
]) {
  test(`${proposalLabel} adjustment proposal requires explicit metadata and preserves it through POST, PUT, reopen, history and exact balance review`, async () => {
    await click(actionLabel);
    assert.ok(host.textContent.includes("explicitly dated and explained proposal for review only"));
    assert.equal(host.querySelector('[aria-label="Proposed adjustment date"]').value, "");
    assert.equal(host.querySelector('[aria-label="Adjustment explanation"]').value, "");
    assert.equal(host.querySelector('[aria-label="Proposed adjustment date"]').checkValidity(), false);
    assert.equal(host.querySelector('[aria-label="Adjustment explanation"]').checkValidity(), false);
    await fillTitle(`${proposalLabel} proposal title`);
    await submitReview();
    assert.ok(host.textContent.includes("valid proposed date"));
    assert.equal(requests.filter((call) => ["POST", "PUT"].includes(call.method)).length, 0);
    await fillLabeled("Proposed adjustment date", "2026-06-30");
    await submitReview();
    assert.ok(host.textContent.includes("required explicit explanation"));
    await fillLabeled("Adjustment explanation", "Explicit synthetic proposal rationale.");
    await setSelect("Draft account line 1", "TEST-DRAFT-ACCT-A");
    await setSelect("Draft account line 2", "TEST-DRAFT-ACCT-A");
    await fillLabeled("Debit line 1", "0.30");
    await fillLabeled("Credit line 1", "0");
    await fillLabeled("Debit line 2", "0");
    await fillLabeled("Credit line 2", "0.30");
    await click("Review draft balance");
    assert.ok(host.textContent.includes("Difference (debit − credit): 0.00 QAR"));
    assert.equal(button("Save draft to disposable database").disabled, false);
    await click("Save draft to disposable database");
    const post = requests.filter((call) => call.method === "POST").at(-1);
    assert.equal(post.body.kind, kind);
    assert.deepEqual(post.body.adjustment_proposal, {
      date: "2026-06-30", explanation: "Explicit synthetic proposal rationale.",
    });
    assert.ok(button(`Open ${proposalLabel} proposal 6:`));
    await click(`Open ${proposalLabel} proposal 6:`);
    assert.equal(host.querySelector('[aria-label="Proposed adjustment date"]').value, "2026-06-30");
    assert.equal(host.querySelector('[aria-label="Adjustment explanation"]').value, "Explicit synthetic proposal rationale.");
    assert.ok(host.textContent.includes("Explicit explanation: Explicit synthetic proposal rationale."));
    assert.ok(host.querySelector('[aria-label="Preserved adjustment proposal metadata"]'));

    await fillLabeled("Adjustment explanation", "Updated explicit proposal rationale.");
    await click("Review draft balance");
    await click("Save draft to disposable database");
    const put = requests.filter((call) => call.method === "PUT").at(-1);
    assert.equal(put.body.kind, kind);
    assert.deepEqual(put.body.adjustment_proposal, {
      date: "2026-06-30", explanation: "Updated explicit proposal rationale.",
    });
    assert.equal(put.body.expected_revision, 1);
    assert.equal(saved.adjustment_proposal.explanation, "Updated explicit proposal rationale.");
    assert.equal(saved.history[0].snapshot.adjustment_proposal.date, "2026-06-30");
  });
}

test("adjustment explanation is capped at 2,000 characters; conflict and unknown save retain entered proposal metadata", async () => {
  saved = { ...document(), kind: "accrual_adjustment", adjustment_proposal: {
    date: "2026-07-01", explanation: "Existing saved proposal.",
  }};
  await click("Refresh saved list");
  await click("Open accrual proposal 5:");
  const area = host.querySelector('[aria-label="Adjustment explanation"]');
  assert.equal(area.maxLength, 2000);
  await fillTitle("Retained proposal");
  await fillLabeled("Proposed adjustment date", "2026-07-31");
  await fillLabeled("Adjustment explanation", "x".repeat(2001));
  await submitReview();
  assert.ok(host.textContent.includes("2,000 characters or fewer"));
  assert.equal(requests.filter((call) => ["POST", "PUT"].includes(call.method)).length, 0);
  await fillLabeled("Adjustment explanation", "Retain these explicit inputs.");
  await click("Review draft balance");
  handler = (call) => call.method === "PUT"
    ? { status: 409, data: { detail: "Synthetic proposal conflict" } } : null;
  await click("Save draft to disposable database");
  assert.ok(host.textContent.includes("Conflicting edit:"));
  assert.equal(host.querySelector('[aria-label="Proposed adjustment date"]').value, "2026-07-31");
  assert.equal(host.querySelector('[aria-label="Adjustment explanation"]').value, "Retain these explicit inputs.");
  assert.equal(requests.filter((call) => call.method === "PUT").at(-1).body.adjustment_proposal.explanation, "Retain these explicit inputs.");
  assert.equal(requests.filter((call) => call.method === "POST").length, 0);

  handler = (call) => {
    if (call.method === "POST") {
      saved = { ...document(1, call.body.title_en), id: 6, kind: call.body.kind, adjustment_proposal: call.body.adjustment_proposal };
      throw new TypeError("Synthetic connection interruption after commit");
    }
    return null;
  };
  window.confirm = () => true;
  await click("New accrual proposal");
  await fillTitle("Uncertain proposal");
  await fillLabeled("Proposed adjustment date", "2026-08-31");
  await fillLabeled("Adjustment explanation", "May have committed.");
  await setSelect("Draft account line 1", "TEST-DRAFT-ACCT-A");
  await setSelect("Draft account line 2", "TEST-DRAFT-ACCT-A");
  await fillLabeled("Debit line 1", "0.30");
  await fillLabeled("Credit line 1", "0");
  await fillLabeled("Debit line 2", "0");
  await fillLabeled("Credit line 2", "0.30");
  await click("Review draft balance");
  await click("Save draft to disposable database");
  assert.ok(host.textContent.includes("the database may have committed"));
  assert.equal(host.querySelector('[aria-label="Proposed adjustment date"]').value, "2026-08-31");
  assert.equal(host.querySelector('[aria-label="Adjustment explanation"]').value, "May have committed.");
  assert.equal(button("Save draft to disposable database").disabled, true);
  assert.equal(requests.filter((call) => call.method === "POST").length, 1);
});
