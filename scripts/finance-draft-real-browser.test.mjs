import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { test } from "node:test";
import { browserHarness } from "./browser-harness.mjs";

const origin = process.env.FINANCE_LAB_ORIGIN;
if (origin !== "http://127.0.0.1:18069" || !process.env.EXPECTED_DISPOSABLE_DATA?.startsWith("/tmp/tivoli-finance-lab.")) {
  throw new Error("Use scripts/verify-finance-draft-workflow.sh; never shared preview.");
}
const selector = (label) => `[aria-label=${JSON.stringify(label)}]`;
const title = "Draft journal title English";
const writes = ["POST /lab/session/", "POST /lab/accounts/", "POST /lab/dimensions/",
  "POST /lab/dimension-values/", "POST /lab/journals/"];

async function browser(width) {
  return browserHarness({
    width, height: 950, realBackendOrigin: origin, apiPrefix: "/lab/", entryPath: "/lab-ui/",
    assetDirectory: "generated-artifacts/finance-draft-prototype",
    readyExpression: 'document.querySelector("h1")?.textContent.includes("Disposable Finance")',
    // Only draft writes; quote/stock/ledger/permission endpoints are never allowed.
    allowedRealWrites: writes,
  });
}
async function fill(b, label, value) {
  await b.click(selector(label));
  await b.evaluate(`document.querySelector(${JSON.stringify(selector(label))}).select()`);
  await b.text(value);
}
async function choose(b, label, values) {
  await b.evaluate(`(() => {
    const element = document.querySelector(${JSON.stringify(selector(label))});
    for (const option of element.options) option.selected = ${JSON.stringify([values].flat())}.includes(option.value);
    element.dispatchEvent(new Event("change", { bubbles: true }));
  })()`);
}
async function button(b, prefix) {
  await b.wait(`Array.from(document.querySelectorAll("button")).some(b=>b.textContent.startsWith(${JSON.stringify(prefix)}) && !b.matches(":disabled"))`);
  const index = await b.evaluate(`Array.from(document.querySelectorAll("button")).findIndex(b=>b.textContent.startsWith(${JSON.stringify(prefix)}))`);
  assert.ok(index >= 0, `Missing button ${prefix}`);
  // Locate by a transient test-only DOM marker; normal click still exercises the rendered handler.
  await b.evaluate(`document.querySelectorAll("button")[${index}].setAttribute("data-lab-target", "current")`);
  try { await b.click('[data-lab-target="current"]'); }
  catch (error) { console.error("Click failed:", prefix); throw error; }
  await b.evaluate('document.querySelector("[data-lab-target]")?.removeAttribute("data-lab-target")');
}
async function ready(b) {
  await button(b, "Open isolated synthetic test session");
  await b.wait('!!document.querySelector(".finance-lab-editor") && !document.querySelector(".finance-lab-editor").disabled');
}
async function settled(b) {
  await b.wait('!document.querySelector(".finance-lab-editor")?.disabled');
}
async function confirmClick(b, prefix, accept) {
  const count = b.dialogs.length;
  const clicked = button(b, prefix);
  const deadline = Date.now() + 5000;
  while (b.dialogs.length === count && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(b.dialogs.at(-1)?.type, "confirm", "Actual unsaved-edit confirmation opens");
  await b.handleDialog(accept);
  await clicked;
}
function activate(active) {
  execFileSync("python", ["manage.py", "shell", "--no-imports", "-c",
    `from scripts.finance_lab_fixture import set_actor_active; set_actor_active(${active ? "True" : "False"})`],
  { stdio: ["ignore", "pipe", "pipe"] });
}

for (const width of [1280, 390]) {
  test(`real disposable Finance UI/backend round trip, reload, revisions, conflict and recovery at ${width}px`, async () => {
    const b = await browser(width);
    let second;
    try {
      assert.equal(await b.evaluate('fetch("/lab/context/").then(r=>r.status)'), 403);
      await ready(b);
      const accountCode = `TEST-DRAFT-ACCT-B${width}`;
      await fill(b, "Draft account code", accountCode);
      await fill(b, "Account name English", "Synthetic proposal");
      await fill(b, "Account name Arabic (optional)", "حساب تجريبي");
      await button(b, "Add draft account"); await settled(b);
      await b.wait(`!!Array.from(document.querySelectorAll("option")).find(o=>o.value===${JSON.stringify(accountCode)})`);
      await fill(b, "Draft dimension code", `TEST-DRAFT-DIM-B${width}`);
      await fill(b, "Dimension label English", "Synthetic analysis");
      await button(b, "Add draft dimension"); await settled(b);
      await b.wait(`document.querySelector(${JSON.stringify(selector("Dimension for value"))})?.value === "TEST-DRAFT-DIM-B${width}"`);
      for (const suffix of ["A", "B"]) {
        await fill(b, "Draft dimension value code", `TEST-DRAFT-VAL-${width}${suffix}`);
        await fill(b, "Dimension value English", `Synthetic value ${suffix}`);
        await button(b, "Add draft dimension value"); await settled(b);
        await b.wait(`document.querySelector('[aria-label="Local draft dimension values"]').textContent.includes("TEST-DRAFT-VAL-${width}${suffix}")`);
      }
      await fill(b, title, `Synthetic browser draft ${width}`);
      await fill(b, "Draft journal title Arabic (optional)", "مسودة محفوظة");
      await button(b, "Add draft line");
      for (const n of [1, 2, 3]) {
        await choose(b, `Draft account line ${n}`, accountCode);
        await choose(b, `Business unit line ${n}`, "TEST-BU-A");
        await fill(b, `Debit line ${n}`, n === 1 ? "0.30" : "0.00");
        await fill(b, `Credit line ${n}`, n === 2 ? "0.10" : n === 3 ? "0.20" : "0.00");
      }
      await choose(b, "Dimension value line 1", [`TEST-DRAFT-VAL-${width}A`, `TEST-DRAFT-VAL-${width}B`]);
      await button(b, "Review draft balance");
      await button(b, "Save draft to disposable database");
      await b.wait('Array.from(document.querySelectorAll(\'[role="status"]\')).some(e=>e.textContent.includes("Saved draft"))');
      const row = await b.evaluate(`fetch("/lab/journals/").then(r=>r.json()).then(d=>d.drafts.find(r=>r.title_en==="Synthetic browser draft ${width}"))`);
      const id = row.id;
      // This exact row is the only update destination permitted in this browser.
      writes.push(`PUT /lab/journals/${id}/`);
      await b.reload();
      await b.wait('!!document.querySelector(\'[aria-label="Persisted synthetic draft register"]\')');
      await button(b, `Open draft ${id}:`); await settled(b);
      await b.wait(`document.querySelector(${JSON.stringify(selector(title))})?.value==="Synthetic browser draft ${width}"`);
      assert.equal(await b.evaluate(`document.querySelector(${JSON.stringify(selector("Debit line 1"))}).value`), "0.30");
      assert.equal(await b.evaluate(`document.querySelector(${JSON.stringify(selector("Dimension value line 1"))}).selectedOptions.length`), 2);
      second = await browser(width);
      await ready(second); await button(second, `Open draft ${id}:`); await settled(second);
      await second.wait(`document.querySelector(${JSON.stringify(selector(title))})?.value==="Synthetic browser draft ${width}"`);
      await fill(b, title, `Winner ${width}`);
      await button(b, "Review draft balance"); await button(b, "Save draft to disposable database");
      await b.wait('document.querySelector(".finance-lab-editor legend").textContent.includes("revision 2")');
      await fill(second, title, `Unsaved loser ${width}`);
      await button(second, "Review draft balance"); await button(second, "Save draft to disposable database");
      await second.wait('document.body.textContent.includes("Conflicting edit:")');
      assert.equal(await second.evaluate(`document.querySelector(${JSON.stringify(selector(title))}).value`), `Unsaved loser ${width}`);
      assert.equal(await b.evaluate(`fetch("/lab/journals/${id}/").then(r=>r.json()).then(d=>d.history.length)`), 2);
      await second.evaluate("window.confirm = () => true");
      await button(second, "Reload saved version"); await settled(second);
      await second.wait(`document.querySelector(${JSON.stringify(selector(title))}).value==="Winner ${width}"`);
      assert.equal(await second.evaluate('document.querySelectorAll(\'[aria-label="Preserved draft revision history"] details\').length'), 2);
      // A meaningful invalid amount is retained and prevents a request.
      const before = second.calls.filter(c => c.method === "PUT").length;
      await fill(second, "Debit line 1", "0.001");
      await button(second, "Review draft balance");
      assert.equal(await second.evaluate('document.querySelector(".finance-draft-actions button:last-child").disabled'), true);
      assert.equal(second.calls.filter(c => c.method === "PUT").length, before);
      await fill(second, "Debit line 1", "0.30");
      await button(second, "Review draft balance"); await button(second, "Save draft to disposable database");
      await second.wait('document.querySelector(".finance-lab-editor legend").textContent.includes("revision 3")');
      assert.equal(await second.evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
      await second.screenshot(`persisted-${width}`, "finance-draft-real");
      // Recurring proposals use the real persisted editor, never a scheduler/posting endpoint.
      await button(second, "New recurring template");
      async function dates(browser, start, end, text) {
        for (const [label, value] of [["Proposal range start", start], ["Proposal range end", end]]) {
          await browser.evaluate(`(() => {
            const input = document.querySelector(${JSON.stringify(selector(label))});
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, ${JSON.stringify(value)});
            input.dispatchEvent(new Event("change", { bubbles: true }));
          })()`);
        }
        await fill(browser, "Explicit occurrence dates", text);
      }
      await dates(second, "2026-12-01", "2027-01-31", "2026-12-31\n2027-01-02");
      await fill(second, title, `Recurring proposal ${width}`);
      await fill(second, "Draft journal title Arabic (optional)", "قالب مقترح دون ترحيل");
      for (const n of [1, 2]) {
        await choose(second, `Draft account line ${n}`, accountCode);
        await choose(second, `Business unit line ${n}`, "TEST-BU-A");
        await fill(second, `Debit line ${n}`, n === 1 ? "0.30" : "0.00");
        await fill(second, `Credit line ${n}`, n === 2 ? "0.30" : "0.00");
      }
      await choose(second, "Dimension value line 1", [`TEST-DRAFT-VAL-${width}A`]);
      await button(second, "Review draft balance"); await button(second, "Save draft to disposable database");
      await second.wait('document.querySelector(".finance-lab-editor legend").textContent.includes("Editing template")');
      const template = await second.evaluate(`fetch("/lab/journals/").then(r=>r.json()).then(d=>d.drafts.find(r=>r.title_en==="Recurring proposal ${width}"))`);
      writes.push(`PUT /lab/journals/${template.id}/`);
      assert.equal(template.kind, "recurring_template");
      // Wait for the post-commit list refresh too: pending operations legitimately warn on unload.
      await settled(second);
      await second.reload();
      await button(second, `Open template ${template.id}:`); await settled(second);
      assert.equal(await second.evaluate(`document.querySelector(${JSON.stringify(selector("Explicit occurrence dates"))}).value`), "2026-12-31\n2027-01-02");
      await button(second, "Preview saved proposed occurrences");
      await second.wait('!!document.getElementById("finance-recurring-preview-title")');
      assert.equal(await second.evaluate('document.querySelector(\'[aria-label="Exact proposed totals"]\').textContent.includes("0.60")'), true);
      await button(b, "Refresh saved list");
      await button(b, `Open template ${template.id}:`); await settled(b);
      await dates(b, "2026-12-01", "2027-01-31", "2026-12-31\n2027-01-02\n2027-01-03");
      await button(b, "Review draft balance"); await button(b, "Save draft to disposable database");
      await b.wait('document.querySelector(".finance-lab-editor legend").textContent.includes("revision 2")');
      await dates(second, "2026-12-01", "2027-01-31", "2026-12-31\n2027-01-04");
      assert.equal(await second.evaluate('!!document.getElementById("finance-recurring-preview-title")'), false);
      await button(second, "Review draft balance"); await button(second, "Save draft to disposable database");
      await second.wait('document.body.textContent.includes("Conflicting edit:")');
      assert.equal(await second.evaluate(`document.querySelector(${JSON.stringify(selector("Explicit occurrence dates"))}).value`), "2026-12-31\n2027-01-04");
      await confirmClick(second, "Reload saved version", false);
      assert.equal(await second.evaluate(`document.querySelector(${JSON.stringify(selector("Explicit occurrence dates"))}).value`), "2026-12-31\n2027-01-04");
      await confirmClick(second, "Reload saved version", true); await settled(second);
      await second.wait(`document.querySelector(${JSON.stringify(selector("Explicit occurrence dates"))})?.value.endsWith("2027-01-03")`);
      const beforePreview = await second.evaluate(`fetch("/lab/journals/${template.id}/").then(r=>r.json())`);
      await button(second, "Preview saved proposed occurrences");
      await second.wait('!!document.getElementById("finance-recurring-preview-title")');
      const result = await second.evaluate(`fetch("/lab/journals/${template.id}/preview/").then(r=>r.json())`);
      assert.equal(result.occurrence_count, 3);
      assert.equal(result.total_proposed_debits, "0.90");
      assert.equal(result.scheduled, false); assert.equal(result.posted, false);
      assert.equal(result.entry_lines[0].dimensions.length, 1);
      assert.deepEqual(await second.evaluate(`fetch("/lab/journals/${template.id}/").then(r=>r.json())`), beforePreview);
      assert.equal(await second.evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
      await second.screenshot(`recurring-${width}`, "finance-draft-real");
      // Access loss is the explicit lab session only, not an invented Finance grant.
      activate(false);
      await button(second, "Refresh saved list");
      await second.wait('!document.querySelector(".finance-lab-editor") && !document.querySelector(\'[aria-label="Preserved draft revision history"]\')');
      assert.equal(await second.evaluate('document.body.textContent.includes("Synthetic proposal")'), false);
      assert.ok(b.calls.some(c => c.path === `/lab/journals/${id}/` && c.status === 200));
      assert.ok(second.calls.some(c => c.method === "PUT" && c.status === 409));
      b.assertSafe(); second.assertSafe();
    } finally {
      activate(true);
      if (second) await second.close();
      await b.close();
    }
  });
}

for (const [width, kind, name] of [
  [1280, "accrual_adjustment", "accrual"],
  [390, "prepayment_adjustment", "prepayment"],
]) {
  test(`real ${name} proposal persists explicit inputs, revisions and conflict recovery at ${width}px`, async () => {
    const b = await browser(width);
    let second;
    async function proposedDate(target, value) {
      await target.evaluate(`(() => {
        const input = document.querySelector('[aria-label="Proposed adjustment date"]');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, ${JSON.stringify(value)});
        input.dispatchEvent(new Event("change", {bubbles:true}));
      })()`);
    }
    try {
      await ready(b);
      const account = `TEST-DRAFT-ACCT-AD${width}`;
      const dimension = `TEST-DRAFT-DIM-AD${width}`;
      const value = `TEST-DRAFT-VAL-AD${width}`;
      await fill(b, "Draft account code", account);
      await fill(b, "Account name English", "Explicit synthetic adjustment account");
      await button(b, "Add draft account"); await settled(b);
      await fill(b, "Draft dimension code", dimension);
      await fill(b, "Dimension label English", "Explicit synthetic analysis");
      await button(b, "Add draft dimension"); await settled(b);
      await choose(b, "Dimension for value", dimension);
      await fill(b, "Draft dimension value code", value);
      await fill(b, "Dimension value English", "Synthetic analysis A");
      await button(b, "Add draft dimension value"); await settled(b);
      await confirmClick(b, `New ${name} proposal`, true);
      await fill(b, title, `Explicit ${name} ${width}`);
      await proposedDate(b, "2028-02-29");
      await fill(b, "Adjustment explanation", "User-entered proposal only — توضيح مقدم");
      for (const n of [1, 2]) {
        await choose(b, `Draft account line ${n}`, account);
        await choose(b, `Business unit line ${n}`, "TEST-BU-A");
        await fill(b, `Debit line ${n}`, n === 1 ? "0.30" : "0.00");
        await fill(b, `Credit line ${n}`, n === 2 ? "0.30" : "0.00");
      }
      await choose(b, "Dimension value line 1", [value]);
      await button(b, "Review draft balance");
      assert.equal(await b.evaluate('document.body.textContent.includes("Difference (debit − credit): 0.00")'), true);
      await button(b, "Save draft to disposable database");
      await b.wait(`document.querySelector(".finance-lab-editor legend").textContent.includes("Editing ${name} adjustment proposal")`);
      await settled(b);
      const row = await b.evaluate(`fetch("/lab/journals/").then(r=>r.json()).then(d=>d.drafts.find(r=>r.title_en==="Explicit ${name} ${width}"))`);
      assert.equal(row.kind, kind);
      writes.push(`PUT /lab/journals/${row.id}/`);
      const saved = await b.evaluate(`fetch("/lab/journals/${row.id}/").then(r=>r.json())`);
      assert.deepEqual(saved.adjustment_proposal, {date: "2028-02-29", explanation: "User-entered proposal only — توضيح مقدم"});
      assert.equal(saved.review.debit_total, "0.30");
      assert.equal(saved.review.difference, "0.00");
      assert.equal(saved.review.posted, false);
      assert.equal(saved.lines[0].dimensions[0].code, value);
      assert.equal(saved.lines[0].business_unit_code, "TEST-BU-A");
      await b.reload();
      await button(b, `Open ${name} proposal ${row.id}:`); await settled(b);
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Proposed adjustment date"]\').value'), "2028-02-29");
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Adjustment explanation"]\').value'), saved.adjustment_proposal.explanation);
      second = await browser(width);
      await ready(second);
      await button(second, `Open ${name} proposal ${row.id}:`); await settled(second);
      // A validation error retains both metadata and amounts and never sends a save.
      const beforeInvalid = b.calls.filter(call => call.method === "PUT").length;
      await fill(b, "Debit line 1", "0.001");
      await button(b, "Review draft balance");
      assert.equal(await b.evaluate('document.querySelector(".finance-draft-actions button:last-child").disabled'), true);
      assert.equal(b.calls.filter(call => call.method === "PUT").length, beforeInvalid);
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Adjustment explanation"]\').value'), saved.adjustment_proposal.explanation);
      await fill(b, "Debit line 1", "0.30");
      await proposedDate(b, "2028-03-01");
      await fill(b, "Adjustment explanation", "Explicit revised date and explanation");
      await button(b, "Review draft balance"); await button(b, "Save draft to disposable database");
      await b.wait('document.querySelector(".finance-lab-editor legend").textContent.includes("revision 2")');
      await proposedDate(second, "2028-03-02");
      await fill(second, "Adjustment explanation", "Retained conflicting explanation");
      await button(second, "Review draft balance"); await button(second, "Save draft to disposable database");
      await second.wait('document.body.textContent.includes("Conflicting edit:")');
      assert.equal(await second.evaluate('document.querySelector(\'[aria-label="Proposed adjustment date"]\').value'), "2028-03-02");
      assert.equal(await second.evaluate('document.querySelector(\'[aria-label="Adjustment explanation"]\').value'), "Retained conflicting explanation");
      await confirmClick(second, "Reload saved version", true); await settled(second);
      assert.equal(await second.evaluate('document.querySelector(\'[aria-label="Proposed adjustment date"]\').value'), "2028-03-01");
      assert.equal(await second.evaluate('document.querySelector(\'[aria-label="Adjustment explanation"]\').value'), "Explicit revised date and explanation");
      const updated = await second.evaluate(`fetch("/lab/journals/${row.id}/").then(r=>r.json())`);
      assert.deepEqual(updated.history.map(h => h.snapshot.adjustment_proposal.date), ["2028-02-29", "2028-03-01"]);
      assert.equal(updated.review.posted, false);
      assert.equal(await second.evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
      await second.evaluate('document.querySelector(\'[aria-label="Proposed adjustment date"]\').scrollIntoView({block:"center"})');
      await second.screenshot(`adjustment-${name}-${width}`, "finance-draft-real");
      b.assertSafe(); second.assertSafe();
    } finally {
      if (second) await second.close();
      await b.close();
    }
  });
}

for (const width of [1280, 390]) {
  test(`real opening-balance worksheet persists, reopens and rejects conflicting edits at ${width}px`, async () => {
    const b = await browser(width);
    let second;
    async function asOf(target, value) {
      await target.evaluate(`(() => {
        const input = document.querySelector('[aria-label="Opening balance as-of date"]');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, ${JSON.stringify(value)});
        input.dispatchEvent(new Event("change", {bubbles:true}));
      })()`);
    }
    try {
      await ready(b);
      const account = `TEST-DRAFT-ACCT-OP${width}`;
      await fill(b, "Draft account code", account);
      await fill(b, "Account name English", "Synthetic opening proposal");
      await button(b, "Add draft account"); await settled(b);
      await confirmClick(b, "New opening-balance worksheet", true);
      await asOf(b, "2028-02-29");
      await fill(b, title, `Opening worksheet ${width}`);
      for (const n of [1, 2]) {
        await choose(b, `Draft account line ${n}`, account);
        await choose(b, `Business unit line ${n}`, "TEST-BU-A");
        await fill(b, `Debit line ${n}`, n === 1 ? "0.30" : "0.00");
        await fill(b, `Credit line ${n}`, n === 2 ? "0.30" : "0.00");
      }
      await button(b, "Review draft balance");
      assert.equal(await b.evaluate('document.body.textContent.includes("Difference (debit − credit): 0.00")'), true);
      await button(b, "Save draft to disposable database");
      await b.wait('document.querySelector(".finance-lab-editor legend").textContent.includes("Editing opening-balance worksheet")');
      await settled(b);
      const row = await b.evaluate(`fetch("/lab/journals/").then(r=>r.json()).then(d=>d.drafts.find(r=>r.title_en==="Opening worksheet ${width}"))`);
      assert.equal(row.kind, "opening_balance");
      assert.equal(row.as_of_date, "2028-02-29");
      writes.push(`PUT /lab/journals/${row.id}/`);
      const saved = await b.evaluate(`fetch("/lab/journals/${row.id}/").then(r=>r.json())`);
      assert.equal(saved.review.debit_total, "0.30");
      assert.equal(saved.review.difference, "0.00");
      assert.equal(saved.review.posted, false);
      await b.reload();
      await button(b, `Open worksheet ${row.id}:`); await settled(b);
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Opening balance as-of date"]\').value'), "2028-02-29");
      second = await browser(width);
      await ready(second);
      await button(second, `Open worksheet ${row.id}:`); await settled(second);
      await asOf(b, "2028-03-01");
      await button(b, "Review draft balance"); await button(b, "Save draft to disposable database");
      await b.wait('document.querySelector(".finance-lab-editor legend").textContent.includes("revision 2")');
      await asOf(second, "2028-03-02");
      await button(second, "Review draft balance"); await button(second, "Save draft to disposable database");
      await second.wait('document.body.textContent.includes("Conflicting edit:")');
      assert.equal(await second.evaluate('document.querySelector(\'[aria-label="Opening balance as-of date"]\').value'), "2028-03-02");
      await confirmClick(second, "Reload saved version", true); await settled(second);
      assert.equal(await second.evaluate('document.querySelector(\'[aria-label="Opening balance as-of date"]\').value'), "2028-03-01");
      const updated = await second.evaluate(`fetch("/lab/journals/${row.id}/").then(r=>r.json())`);
      assert.deepEqual(updated.history.map(h => h.snapshot.as_of_date), ["2028-02-29", "2028-03-01"]);
      assert.equal(updated.review.posted, false);
      assert.equal(await second.evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
      await second.screenshot(`opening-${width}`, "finance-draft-real");
      b.assertSafe(); second.assertSafe();
    } finally {
      if (second) await second.close();
      await b.close();
    }
  });
}
