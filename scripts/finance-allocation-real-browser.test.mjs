import test from "node:test";
import assert from "node:assert/strict";
import { browserHarness } from "./browser-harness.mjs";

const origin = process.env.FINANCE_LAB_ORIGIN;
if (origin !== "http://127.0.0.1:18069" || !process.env.EXPECTED_DISPOSABLE_DATA?.startsWith("/tmp/tivoli-finance-lab.")) {
  throw new Error("Use the disposable Finance runner, not the shared preview.");
}
const selector = label => `[aria-label=${JSON.stringify(label)}]`;
const writes = ["POST /lab/session/", "POST /lab/accounts/", "POST /lab/dimensions/",
  "POST /lab/dimension-values/", "POST /lab/allocations/"];
async function browser(width) {
  const b = await browserHarness({
    width, height: 950, realBackendOrigin: origin, apiPrefix: "/lab/",
    entryPath: "/lab-ui/", assetDirectory: "generated-artifacts/finance-draft-prototype",
    documentPaths: ["/lab-allocation-ui/"],
    readyExpression: 'document.querySelector("h1")?.textContent.includes("Disposable Finance")',
    allowedRealWrites: writes,
  });
  try {
    // The allocation page deliberately reuses the existing session setup facility.
    await button(b, "Open isolated synthetic test session");
    await b.wait('!!document.querySelector(".finance-lab-editor") && !document.querySelector(".finance-lab-editor").disabled');
    await b.click('a[href="/lab-allocation-ui/"]');
    await b.wait('!!document.querySelector(".allocation-editor") && !document.querySelector(".allocation-editor").disabled');
    return b;
  } catch (error) {
    await b.close();
    throw error;
  }
}
async function fill(b, label, value) {
  await b.click(selector(label));
  await b.evaluate(`document.querySelector(${JSON.stringify(selector(label))}).select()`);
  await b.text(value);
}
async function choose(b, label, ids) {
  await b.evaluate(`(() => {
    const input = document.querySelector(${JSON.stringify(selector(label))});
    for (const option of input.options) option.selected = ${JSON.stringify([ids].flat())}.includes(option.value);
    input.dispatchEvent(new Event("change", {bubbles:true}));
  })()`);
}
async function button(b, prefix) {
  await b.wait(`Array.from(document.querySelectorAll("button")).some(b => b.textContent.startsWith(${JSON.stringify(prefix)}) && !b.matches(":disabled"))`);
  await b.evaluate(`Array.from(document.querySelectorAll("button")).find(b => b.textContent.startsWith(${JSON.stringify(prefix)})).setAttribute("data-allocation-click","yes")`);
  await b.click('[data-allocation-click="yes"]');
  await b.evaluate('document.querySelector("[data-allocation-click]")?.removeAttribute("data-allocation-click")');
}
async function ready(b) {
  await b.wait('!!document.querySelector(".allocation-editor") || Array.from(document.querySelectorAll("button")).some(b => b.textContent.startsWith("Open isolated"))');
  if (await b.evaluate('!!Array.from(document.querySelectorAll("button")).find(b => b.textContent.startsWith("Open isolated"))')) {
    await button(b, "Open isolated synthetic test session");
  }
  await b.wait('!!document.querySelector(".allocation-editor") && !document.querySelector(".allocation-editor").disabled');
}
async function settled(b) {
  await b.wait('!!document.querySelector(".allocation-editor") && !document.querySelector(".allocation-editor").disabled');
}

for (const width of [1280, 390]) {
  test(`real isolated allocation UI persists exact inputs, reopens and recovers conflicting revisions at ${width}px`, async () => {
    const b = await browser(width);
    let other;
    try {
      await ready(b);
      const accountCode = `TEST-DRAFT-ACCT-AL${width}`;
      // Explicit synthetic setup uses existing guarded APIs; no shared account,
      // grant, ledger account or operational setup is created.
      const setup = await b.evaluate(`(async () => {
        const token = (await (await fetch("/lab/session/")).json()).csrf_token;
        async function post(path, body) {
          const response = await fetch("/lab/"+path+"/", {method:"POST",
            headers:{"Content-Type":"application/json","X-CSRFToken":token},
            body:JSON.stringify(body)});
          if (!response.ok) throw new Error("Synthetic allocation setup rejected: "+response.status);
          return response.json();
        }
        const account = await post("accounts",{code:${JSON.stringify(accountCode)},name_en:"Explicit synthetic allocation account"});
        const dimension = await post("dimensions",{code:"TEST-DRAFT-DIM-AL${width}",label_en:"Explicit analysis"});
        const value = await post("dimension-values",{dimension_id:dimension.id,code:"TEST-DRAFT-VAL-AL${width}",label_en:"Synthetic allocation destination"});
        return {account:account.id,value:value.id};
      })()`);
      await b.reload(); await ready(b);
      await fill(b, "Allocation title", `Synthetic allocation ${width}`);
      await fill(b, "Allocation source amount", "0.30");
      await fill(b, "Allocation currency", "QAR");
      await fill(b, "Allocation explanation", "Explicit synthetic amounts — بيانات مقدمة");
      await b.evaluate(`(() => {
        const input = document.querySelector('[aria-label="Allocation proposed date"]');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,"2028-02-29");
        input.dispatchEvent(new Event("change",{bubbles:true}));
      })()`);
      await button(b, "Add destination");
      const choices = await b.evaluate('fetch("/lab/context/").then(r=>r.json())');
      const unitId = choices.business_units.find(unit => unit.code === "TEST-BU-A").id;
      for (const n of [1, 2]) {
        await fill(b, `Destination amount ${n}`, n === 1 ? "0.10" : "0.20");
        await choose(b, `Destination account ${n}`, String(setup.account));
        await choose(b, `Destination business unit ${n}`, String(unitId));
        await choose(b, `Destination dimensions ${n}`, String(setup.value));
        await choose(b, `Allocation journal account ${n}`, String(setup.account));
        await choose(b, `Allocation journal business unit ${n}`, String(unitId));
        await choose(b, `Allocation journal dimensions ${n}`, String(setup.value));
        await fill(b, `Allocation debit ${n}`, n === 1 ? "0.30" : "0.00");
        await fill(b, `Allocation credit ${n}`, n === 2 ? "0.30" : "0.00");
      }
      const beforeInvalid = b.calls.filter(call => call.method === "POST" && call.path === "/lab/allocations/").length;
      await fill(b, "Allocation source amount", "0.20");
      await button(b, "Review allocation proposal");
      assert.equal(await b.evaluate('document.body.textContent.includes("Overallocated by")'), true);
      assert.equal(await b.evaluate('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Save allocation proposal").disabled'), true);
      assert.equal(b.calls.filter(call => call.method === "POST" && call.path === "/lab/allocations/").length, beforeInvalid);
      await fill(b, "Allocation source amount", "0.30");
      await button(b, "Review allocation proposal");
      await button(b, "Save allocation proposal"); await settled(b);
      await b.wait('document.querySelector(".allocation-editor legend").textContent.includes("revision 1")');
      const row = await b.evaluate(`fetch("/lab/allocations/").then(r=>r.json()).then(d=>d.proposals.find(p=>p.title_en==="Synthetic allocation ${width}"))`);
      writes.push(`PUT /lab/allocations/${row.id}/`);
      const saved = await b.evaluate(`fetch("/lab/allocations/${row.id}/").then(r=>r.json())`);
      assert.equal(saved.review.allocated_total, "0.30");
      assert.equal(saved.review.difference, "0.00");
      assert.equal(saved.review.journal.balanced, true);
      assert.equal(saved.review.journal.posted, false);
      assert.deepEqual(saved.allocation_proposal.destinations.map(d => d.dimension_value_ids), [[setup.value], [setup.value]]);
      assert.equal(saved.allocation_proposal.explanation, "Explicit synthetic amounts — بيانات مقدمة");
      await b.reload(); await ready(b);
      await button(b, `Open allocation proposal ${row.id}:`); await settled(b);
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Destination amount 2"]\').value'), "0.20");
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Allocation proposed date"]\').value'), "2028-02-29");
      other = await browser(width); await ready(other);
      await button(other, `Open allocation proposal ${row.id}:`); await settled(other);
      await fill(b, "Allocation source amount", "1.00");
      await fill(b, "Allocation explanation", "Explicit revised proposal");
      await button(b, "Review allocation proposal");
      await button(b, "Save allocation proposal"); await settled(b);
      await b.wait('document.querySelector(".allocation-editor legend").textContent.includes("revision 2")');
      await fill(other, "Allocation source amount", "2.00");
      await fill(other, "Allocation explanation", "Keep this conflicting local work");
      await button(other, "Review allocation proposal");
      await button(other, "Save allocation proposal"); await settled(other);
      await other.wait('document.body.textContent.includes("Conflicting edit:")');
      assert.equal(await other.evaluate('document.querySelector(\'[aria-label="Allocation source amount"]\').value'), "2.00");
      assert.equal(await other.evaluate('document.querySelector(\'[aria-label="Allocation explanation"]\').value'), "Keep this conflicting local work");
      const dialogCount = other.dialogs.length;
      const click = button(other, "Reload saved allocation");
      const deadline = Date.now() + 5000;
      while (other.dialogs.length === dialogCount && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 20));
      assert.equal(other.dialogs.at(-1)?.type, "confirm");
      await other.handleDialog(true); await click; await settled(other);
      assert.equal(await other.evaluate('document.querySelector(\'[aria-label="Allocation source amount"]\').value'), "1.00");
      const updated = await other.evaluate(`fetch("/lab/allocations/${row.id}/").then(r=>r.json())`);
      assert.deepEqual(updated.history.map(h => h.snapshot.allocation_proposal.source_amount), ["0.30", "1.00"]);
      assert.equal(updated.review.difference, "0.70");
      assert.equal(updated.review.state, "remaining");
      assert.equal(updated.revision, 2);
      assert.equal(await other.evaluate('document.documentElement.scrollWidth <= innerWidth'), true);
      await other.evaluate('document.querySelector(\'[aria-label="Allocation source amount"]\').scrollIntoView({block:"center"})');
      await other.screenshot(`allocation-${width}`, "finance-allocation-real");
      b.assertSafe(); other.assertSafe();
    } finally {
      if (other) await other.close();
      await b.close();
    }
  });
}
