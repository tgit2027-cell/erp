import test from "node:test";
import assert from "node:assert/strict";
import { browserHarness } from "./browser-harness.mjs";

const origin = process.env.FINANCE_LAB_ORIGIN;
if (origin !== "http://127.0.0.1:18069" || !process.env.EXPECTED_DISPOSABLE_DATA?.startsWith("/tmp/tivoli-finance-lab.")) {
  throw new Error("Use the disposable Finance runner, never the shared preview.");
}
const selector = label => `[aria-label=${JSON.stringify(label)}]`;
const writes = ["POST /lab/session/", "POST /lab/accounts/", "POST /lab/dimensions/",
  "POST /lab/dimension-values/", "POST /lab/budgets/"];
async function button(b, prefix) {
  await b.wait(`Array.from(document.querySelectorAll("button")).some(b => b.textContent.startsWith(${JSON.stringify(prefix)}) && !b.matches(":disabled"))`);
  await b.evaluate(`Array.from(document.querySelectorAll("button")).find(b=>b.textContent.startsWith(${JSON.stringify(prefix)})).setAttribute("data-budget-click","yes")`);
  await b.click('[data-budget-click="yes"]');
  await b.evaluate('document.querySelector("[data-budget-click]")?.removeAttribute("data-budget-click")');
}
async function settled(b) {
  await b.wait('!!document.querySelector("[data-testid=budget-editor]") && !document.querySelector("[data-testid=budget-editor]").disabled');
}
async function browser(width) {
  const b = await browserHarness({
    width, height: 950, realBackendOrigin: origin, apiPrefix: "/lab/",
    entryPath: "/lab-ui/", assetDirectory: "generated-artifacts/finance-draft-prototype",
    documentPaths: ["/lab-budget-ui/"],
    readyExpression: 'document.querySelector("h1")?.textContent.includes("Disposable Finance")',
    allowedRealWrites: writes,
  });
  try {
    await button(b, "Open isolated synthetic test session");
    await b.wait('!!document.querySelector(".finance-lab-editor") && !document.querySelector(".finance-lab-editor").disabled');
    await b.click('a[href="/lab-budget-ui/"]');
    await settled(b);
    assert.equal(await b.evaluate('document.querySelector("h1").textContent'), "Budget scenarios");
    return b;
  } catch (error) { await b.close(); throw error; }
}
async function fill(b, label, value) {
  await b.click(selector(label));
  await b.evaluate(`document.querySelector(${JSON.stringify(selector(label))}).select()`);
  await b.text(value);
}
async function date(b, label, value) {
  await b.evaluate(`(() => {
    const input = document.querySelector(${JSON.stringify(selector(label))});
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,${JSON.stringify(value)});
    input.dispatchEvent(new Event("change",{bubbles:true}));
  })()`);
}
async function choose(b, label, ids) {
  await b.evaluate(`(() => {
    const input = document.querySelector(${JSON.stringify(selector(label))});
    for (const option of input.options) option.selected = ${JSON.stringify([ids].flat())}.includes(option.value);
    input.dispatchEvent(new Event("change",{bubbles:true}));
  })()`);
}

for (const width of [1280, 390]) {
  test(`real isolated budget route persists arithmetic, reopen and revision conflict at ${width}px`, async () => {
    const b = await browser(width);
    let other;
    try {
      const setup = await b.evaluate(`(async () => {
        const token = (await (await fetch("/lab/session/")).json()).csrf_token;
        async function post(path,body) {
          const response=await fetch("/lab/"+path+"/",{method:"POST",
            headers:{"Content-Type":"application/json","X-CSRFToken":token},body:JSON.stringify(body)});
          if(!response.ok) throw new Error("Synthetic draft setup rejected: "+response.status);
          return response.json();
        }
        const account=await post("accounts",{code:"TEST-DRAFT-ACCT-BUD${width}",name_en:"Explicit synthetic budget account"});
        const dimension=await post("dimensions",{code:"TEST-DRAFT-DIM-BUD${width}",label_en:"Budget analysis"});
        const value=await post("dimension-values",{dimension_id:dimension.id,code:"TEST-DRAFT-VAL-BUD${width}",label_en:"Synthetic proposed destination"});
        const context=await (await fetch("/lab/context/")).json();
        return {account:account.id,value:value.id,unit:context.business_units.find(u=>u.code==="TEST-BU-A").id};
      })()`);
      await b.reload(); await settled(b);
      await fill(b, "Budget scenario name", `Synthetic scenario ${width}`);
      await date(b, "Scenario start date", "2028-02-01");
      await date(b, "Scenario end date", "2028-03-31");
      await button(b, "Add scenario row");
      for (const n of [1, 2]) {
        await date(b, `Budget row start date ${n}`, n === 1 ? "2028-02-01" : "2028-03-01");
        await date(b, `Budget row end date ${n}`, n === 1 ? "2028-02-29" : "2028-03-31");
        await fill(b, `Budget row amount ${n}`, n === 1 ? "0.10" : "0.20");
        await choose(b, `Budget row account ${n}`, String(setup.account));
        await choose(b, `Budget row business unit ${n}`, String(setup.unit));
        await choose(b, `Budget row dimensions ${n}`, String(setup.value));
        await fill(b, `Budget row explanation ${n}`, `Explicit entry ${n} — بيانات مقدمة`);
      }
      // Invalid date ranges stay local; no partial scenario is submitted.
      const beforeInvalid = b.calls.filter(call => call.method === "POST" && call.path === "/lab/budgets/").length;
      await date(b, "Budget row end date 1", "2028-01-31");
      await button(b, "Review worksheet");
      assert.equal(await b.evaluate('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Save budget scenario").disabled'), true);
      assert.equal(b.calls.filter(call => call.method === "POST" && call.path === "/lab/budgets/").length, beforeInvalid);
      await date(b, "Budget row end date 1", "2028-02-29");
      await button(b, "Review worksheet");
      await button(b, "Save budget scenario"); await settled(b);
      await b.wait('document.querySelector(".budget-editor > legend").textContent.includes("revision 1")');
      const row = await b.evaluate(`fetch("/lab/budgets/").then(r=>r.json()).then(d=>d.scenarios.find(s=>s.name==="Synthetic scenario ${width}"))`);
      writes.push(`PUT /lab/budgets/${row.id}/`);
      const saved = await b.evaluate(`fetch("/lab/budgets/${row.id}/").then(r=>r.json())`);
      assert.equal(saved.review.proposed_total, "0.30");
      assert.equal(saved.review.posted, false);
      assert.deepEqual(saved.review.warnings, []);
      assert.deepEqual(saved.entries.map(e => e.dimension_value_ids), [[setup.value], [setup.value]]);
      assert.equal(saved.entries[0].explanation, "Explicit entry 1 — بيانات مقدمة");
      await b.reload(); await settled(b);
      await button(b, `Open budget scenario ${row.id}:`); await settled(b);
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Budget row amount 2"]\').value'), "0.20");
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Budget row end date 1"]\').value'), "2028-02-29");
      other = await browser(width);
      await button(other, `Open budget scenario ${row.id}:`); await settled(other);

      // Explicit overlap remains an advisory; it neither merges nor rejects.
      await date(b, "Budget row start date 2", "2028-02-29");
      await fill(b, "Budget row amount 1", "1.01");
      await button(b, "Review worksheet");
      assert.equal(await b.evaluate('document.body.textContent.includes("intersecting inclusive dates")'), true);
      await button(b, "Save budget scenario"); await settled(b);
      await b.wait('document.querySelector(".budget-editor > legend").textContent.includes("revision 2")');
      await fill(other, "Budget row amount 1", "2.00");
      await fill(other, "Budget row explanation 1", "Keep conflicting reviewer work");
      await button(other, "Review worksheet");
      await button(other, "Save budget scenario"); await settled(other);
      await other.wait('document.body.textContent.includes("Saved revision changed")');
      assert.equal(await other.evaluate('document.querySelector(\'[aria-label="Budget row amount 1"]\').value'), "2.00");
      assert.equal(await other.evaluate('document.querySelector(\'[aria-label="Budget row explanation 1"]\').value'), "Keep conflicting reviewer work");
      const dialogs = other.dialogs.length;
      const click = button(other, "Reload saved scenario");
      const deadline = Date.now() + 5000;
      while (other.dialogs.length === dialogs && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 20));
      assert.equal(other.dialogs.at(-1)?.type, "confirm");
      await other.handleDialog(true); await click; await settled(other);
      assert.equal(await other.evaluate('document.querySelector(\'[aria-label="Budget row amount 1"]\').value'), "1.01");
      const updated = await other.evaluate(`fetch("/lab/budgets/${row.id}/").then(r=>r.json())`);
      assert.equal(updated.review.proposed_total, "1.21");
      assert.equal(updated.revision, 2);
      assert.deepEqual(updated.review.warnings.map(w=>[w.code,w.rows]), [["overlapping_dates", [1,2]]]);
      assert.deepEqual(updated.history.map(h=>h.snapshot.review.proposed_total), ["0.30", "1.21"]);
      assert.equal(await other.evaluate('document.documentElement.scrollWidth <= innerWidth'), true);
      await other.evaluate('document.querySelector(\'[aria-label="Budget row amount 1"]\').scrollIntoView({block:"center"})');
      await other.screenshot(`budget-${width}`, "finance-budget-real");
      b.assertSafe(); other.assertSafe();
      assert.equal(b.calls.filter(call=>call.method==="POST" && call.path==="/lab/budgets/").length, 1);
      assert.equal(other.calls.filter(call=>call.method==="PUT").length, 1);
    } finally { if (other) await other.close(); await b.close(); }
  });
}
