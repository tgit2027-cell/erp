import test from "node:test";
import assert from "node:assert/strict";
import { browserHarness } from "./browser-harness.mjs";

const origin = process.env.FINANCE_LAB_ORIGIN;
if (origin !== "http://127.0.0.1:18069" ||
    !process.env.EXPECTED_DISPOSABLE_DATA?.startsWith("/tmp/tivoli-finance-lab.")) {
  throw new Error("Use only the verified disposable Finance runner, never the shared preview.");
}
const selector = label => `[aria-label=${JSON.stringify(label)}]`;
const writes = ["POST /lab/session/", "POST /lab/accounts/", "POST /lab/dimensions/",
  "POST /lab/dimension-values/", "POST /lab/expenses/"];
async function button(b, prefix) {
  await b.wait(`Array.from(document.querySelectorAll("button")).some(b=>(b.getAttribute("aria-label")||b.textContent).startsWith(${JSON.stringify(prefix)})&&!b.matches(":disabled"))`);
  await b.evaluate(`Array.from(document.querySelectorAll("button")).find(b=>(b.getAttribute("aria-label")||b.textContent).startsWith(${JSON.stringify(prefix)})).setAttribute("data-expense-click","yes")`);
  await b.click('[data-expense-click="yes"]');
  await b.evaluate('document.querySelector("[data-expense-click]")?.removeAttribute("data-expense-click")');
}
const settled = b => b.wait('!!document.querySelector("[data-testid=expense-editor]")&&!document.querySelector("[data-testid=expense-editor]").disabled');
async function browser(width) {
  const b = await browserHarness({
    width, height: 950, realBackendOrigin: origin, apiPrefix: "/lab/",
    entryPath: "/lab-ui/", assetDirectory: "generated-artifacts/finance-draft-prototype",
    documentPaths: ["/lab-expense-ui/"], allowedRealWrites: writes,
    readyExpression: 'document.querySelector("h1")?.textContent.includes("Disposable Finance")',
  });
  try {
    await button(b, "Open isolated synthetic test session");
    await b.wait('!!document.querySelector(".finance-lab-editor")&&!document.querySelector(".finance-lab-editor").disabled');
    await b.click('a[href="/lab-expense-ui/"]'); await settled(b);
    assert.equal(await b.evaluate('document.querySelector("h1").textContent.includes("Expense")'), true);
    return b;
  } catch (error) {
    console.error("Synthetic expense setup", await b.evaluate(`({
      path:location.pathname,title:document.querySelector("h1")?.textContent,
      alerts:[...document.querySelectorAll('[role="alert"]')].map(el=>el.textContent)
    })`));
    await b.close(); throw error;
  }
}
async function fill(b, label, value) {
  await b.click(selector(label));
  await b.evaluate(`document.querySelector(${JSON.stringify(selector(label))}).select()`);
  await b.text(value);
}
async function date(b, label, value) {
  await b.evaluate(`(()=>{
    const input=document.querySelector(${JSON.stringify(selector(label))});
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,${JSON.stringify(value)});
    input.dispatchEvent(new Event("change",{bubbles:true}));
  })()`);
}
async function choose(b, label, values) {
  await b.evaluate(`(()=>{
    const input=document.querySelector(${JSON.stringify(selector(label))});
    for(const option of input.options) option.selected=${JSON.stringify([values].flat())}.includes(option.value);
    input.dispatchEvent(new Event("change",{bubbles:true}));
  })()`);
}
const field = (b, label) => b.evaluate(`document.querySelector(${JSON.stringify(selector(label))}).value`);
for (const width of [1280, 390]) {
  const kind = width === 1280 ? "expense" : "petty_cash";
  test(`real isolated ${kind} proposal persists precise rows, reopen and conflict recovery at ${width}px`, async () => {
    const b = await browser(width);
    let other;
    try {
      const setup = await b.evaluate(`(async()=>{
        const token=(await(await fetch("/lab/session/")).json()).csrf_token;
        async function post(path,body) {
          const response=await fetch("/lab/"+path+"/",{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":token},body:JSON.stringify(body)});
          if(!response.ok) throw new Error("Synthetic setup rejected "+response.status);
          return response.json();
        }
        const account=await post("accounts",{code:"TEST-DRAFT-ACCT-EXP${width}",name_en:"Explicit synthetic proposed account"});
        const dimension=await post("dimensions",{code:"TEST-DRAFT-DIM-EXP${width}",label_en:"Explicit expense analysis"});
        const value=await post("dimension-values",{dimension_id:dimension.id,code:"TEST-DRAFT-VAL-EXP${width}",label_en:"Synthetic analysis value"});
        const context=await(await fetch("/lab/context/")).json();
        return {account:account.id,value:value.id,unit:context.business_units.find(u=>u.code==="TEST-BU-A").id};
      })()`);
      await b.reload(); await settled(b);
      assert.equal(await field(b, "Proposal type"), "");
      assert.equal(await b.evaluate('document.querySelector("input[type=file]")===null'), true);
      await fill(b, "Expense proposal name", `Synthetic ${kind} ${width}`);
      await choose(b, "Proposal type", kind);
      await fill(b, "Proposal explanation", " Explicit synthetic review — شرح ");
      await button(b, "Add proposal row");
      for (const n of [1, 2]) {
        await date(b, `Expense date ${n}`, n === 1 ? "2028-02-29" : "2028-03-01");
        await fill(b, `Claimant/custodian reference ${n}`, ` TEST-PARTY-${n} `);
        await fill(b, `Expense description ${n}`, ` Explicit description ${n} — وصف `);
        await fill(b, `Expense amount ${n}`, n === 1 ? "0.10" : "0.20");
        await choose(b, `Expense account ${n}`, String(setup.account));
        await choose(b, `Expense business unit ${n}`, n === 1 ? String(setup.unit) : "");
        await choose(b, `Expense dimensions ${n}`, String(setup.value));
        if (n === 1) await fill(b, `Synthetic receipt reference ${n}`, " TEST-RECEIPT-1 ");
      }
      // Bad precision stays local; a valid first row must not partly create a proposal.
      await fill(b, "Expense amount 2", "0.201");
      await button(b, "Review proposal");
      assert.equal(await b.evaluate('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Save expense proposal").disabled'), true);
      assert.equal(b.calls.filter(c=>c.method==="POST"&&c.path==="/lab/expenses/").length, 0);
      assert.equal(await field(b, "Expense description 1"), " Explicit description 1 — وصف ");
      await fill(b, "Expense amount 2", "0.20");
      await button(b, "Review proposal");
      assert.equal(await b.evaluate('document.querySelector("[data-testid=expense-proposed-total]").textContent.includes("0.30")'), true);
      await button(b, "Save expense proposal"); await settled(b);
      await b.wait('document.querySelector(".expense-editor>legend").textContent.includes("revision 1")');
      const row = await b.evaluate(`fetch("/lab/expenses/").then(r=>r.json()).then(d=>d.proposals.find(p=>p.name==="Synthetic ${kind} ${width}"))`);
      writes.push(`PUT /lab/expenses/${row.id}/`);
      const saved = await b.evaluate(`fetch("/lab/expenses/${row.id}/").then(r=>r.json())`);
      assert.equal(saved.proposal_type, kind);
      assert.equal(saved.review.proposed_total, "0.30");
      assert.deepEqual([saved.review.approved,saved.review.paid,saved.review.posted], [false,false,false]);
      assert.deepEqual(saved.review.missing_receipt_rows, [2]);
      assert.deepEqual(saved.review.unproposed_business_unit_rows, [2]);
      assert.equal(saved.entries[0].description, " Explicit description 1 — وصف ");
      assert.equal(saved.entries[0].receipt_reference, " TEST-RECEIPT-1 ");
      assert.deepEqual(saved.entries.map(e=>e.dimension_value_ids), [[setup.value],[setup.value]]);
      await b.reload(); await settled(b);
      await button(b, `Open expense proposal ${row.id}:`); await settled(b);
      assert.equal(await field(b, "Expense amount 2"), "0.20");
      assert.equal(await field(b, "Proposal type"), kind);
      other = await browser(width);
      await button(other, `Open expense proposal ${row.id}:`); await settled(other);
      await fill(b, "Expense amount 1", "1.01");
      await button(b, "Review proposal");
      await button(b, "Save proposal revision"); await settled(b);
      await b.wait('document.querySelector(".expense-editor>legend").textContent.includes("revision 2")');
      await fill(other, "Expense amount 1", "2.00");
      await fill(other, "Expense description 1", "Retain my conflicting proposal");
      await button(other, "Review proposal");
      await button(other, "Save proposal revision"); await settled(other);
      await other.wait('document.body.textContent.includes("Saved revision changed")');
      assert.equal(await field(other, "Expense amount 1"), "2.00");
      assert.equal(await field(other, "Expense description 1"), "Retain my conflicting proposal");
      const dialogs = other.dialogs.length;
      const click = button(other, "Reload saved proposal");
      const deadline = Date.now() + 5000;
      while (other.dialogs.length === dialogs && Date.now() < deadline) await new Promise(resolve=>setTimeout(resolve,20));
      assert.equal(other.dialogs.at(-1)?.type, "confirm");
      await other.handleDialog(true); await click; await settled(other);
      assert.equal(await field(other, "Expense amount 1"), "1.01");
      const updated = await other.evaluate(`fetch("/lab/expenses/${row.id}/").then(r=>r.json())`);
      assert.equal(updated.revision, 2);
      assert.deepEqual(updated.history.map(h=>h.snapshot.review.proposed_total), ["0.30","1.21"]);
      assert.deepEqual(updated.entries[1], saved.entries[1]);
      assert.deepEqual([updated.review.approved,updated.review.paid,updated.review.posted], [false,false,false]);
      const geometry = await other.evaluate(`({
        client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,
        minimumField:Math.min(...[...document.querySelectorAll('.expense-editor input,.expense-editor select,.expense-editor textarea')].map(el=>el.getBoundingClientRect().width))
      })`);
      assert.ok(geometry.scroll <= geometry.client, JSON.stringify(geometry));
      assert.ok(geometry.minimumField >= 160, JSON.stringify(geometry));
      console.log(`Synthetic ${kind} ${width}px geometry: ${JSON.stringify(geometry)}`);
      await other.evaluate('document.querySelector(\'[aria-label="Expense amount 1"]\').scrollIntoView({block:"center"})');
      await other.screenshot(`expense-${width}`, "finance-expense-real");
      b.assertSafe(); other.assertSafe();
      assert.equal(b.calls.filter(c=>c.method==="POST"&&c.path==="/lab/expenses/").length, 1);
      assert.equal(b.calls.filter(c=>c.method==="PUT").length, 1);
      assert.equal(other.calls.filter(c=>c.method==="PUT").length, 1);
    } finally { if (other) await other.close(); await b.close(); }
  });
}
