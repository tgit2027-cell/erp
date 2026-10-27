import test from "node:test";
import assert from "node:assert/strict";
import { browserHarness } from "./browser-harness.mjs";

const origin = process.env.FINANCE_LAB_ORIGIN;
if (origin !== "http://127.0.0.1:18069" ||
    !process.env.EXPECTED_DISPOSABLE_DATA?.startsWith("/tmp/tivoli-finance-lab.")) {
  throw new Error("Use only the verified disposable Finance facility.");
}
const selector = label => `[aria-label=${JSON.stringify(label)}]`;
const writes = ["POST /lab/session/", "POST /lab/accounts/", "POST /lab/dimensions/",
  "POST /lab/dimension-values/", "POST /lab/supplier-advances/"];
async function button(b, prefix) {
  const match = `(b.getAttribute("aria-label")||b.textContent).startsWith(${JSON.stringify(prefix)})`;
  await b.wait(`Array.from(document.querySelectorAll("button")).some(b=>${match}&&!b.matches(":disabled"))`);
  await b.evaluate(`Array.from(document.querySelectorAll("button")).find(b=>${match}).setAttribute("data-advance-click","yes")`);
  await b.click('[data-advance-click="yes"]');
  await b.evaluate('document.querySelector("[data-advance-click]")?.removeAttribute("data-advance-click")');
}
const settled = b => b.wait('!!document.querySelector("[data-testid=supplier-advance-editor-fields]")&&!document.querySelector("[data-testid=supplier-advance-editor-fields]").disabled');
async function browser(width) {
  const b = await browserHarness({
    width, height: 950, realBackendOrigin: origin, apiPrefix: "/lab/",
    entryPath: "/lab-ui/", assetDirectory: "generated-artifacts/finance-draft-prototype",
    documentPaths: ["/lab-supplier-advances-ui/"], allowedRealWrites: writes,
    readyExpression: 'document.querySelector("h1")?.textContent.includes("Disposable Finance")',
  });
  try {
    await button(b, "Open isolated synthetic test session");
    await b.wait('!!document.querySelector(".finance-lab-editor")&&!document.querySelector(".finance-lab-editor").disabled');
    await b.click('a[href="/lab-supplier-advances-ui/"]'); await settled(b);
    return b;
  } catch (error) { await b.close(); throw error; }
}
async function fill(b, label, value) {
  await b.click(selector(label));
  await b.evaluate(`document.querySelector(${JSON.stringify(selector(label))}).select()`);
  await b.text(value);
}
async function date(b, value) {
  await b.evaluate(`(()=>{
    const input=document.querySelector('[aria-label="Proposed date"]');
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
  test(`real isolated unpaid unapplied supplier advance persists optional choices and competing edits at ${width}px`, async () => {
    const b = await browser(width);
    let other;
    try {
      assert.equal(await field(b, "Proposed date"), "");
      assert.equal(await field(b, "Amount"), "");
      assert.equal(await field(b, "Account"), "");
      await fill(b, "Supplier reference", ` TEST-SUPPLIER-ADV${width} — مورد `);
      await fill(b, "Document reference", ` TEST-ADV${width} — مرجع `);
      await date(b, "2028-02-29");
      await fill(b, "Purpose", " Explicit synthetic purpose — نص ");
      await fill(b, "Amount", "0.001");
      await button(b, "Review supplier-advance proposal");
      assert.equal(b.calls.filter(c=>c.method==="POST"&&c.path==="/lab/supplier-advances/").length, 0);
      assert.equal(await field(b, "Purpose"), " Explicit synthetic purpose — نص ");
      await fill(b, "Amount", "0.10");
      await button(b, "Review supplier-advance proposal");
      assert.equal(await b.evaluate('document.querySelector(".invoice-total strong").textContent'), "0.10");
      await button(b, "Save supplier-advance proposal"); await settled(b);
      await b.wait('document.querySelector(".invoice-editor>legend").textContent.includes("revision 1")');
      const row = await b.evaluate(`fetch("/lab/supplier-advances/").then(r=>r.json()).then(d=>d.advances.find(p=>p.document_reference===" TEST-ADV${width} — مرجع "))`);
      writes.push(`PUT /lab/supplier-advances/${row.id}/`);
      const saved = await b.evaluate(`fetch("/lab/supplier-advances/${row.id}/").then(r=>r.json())`);
      assert.equal(saved.amount, "0.10");
      assert.equal(saved.purpose, " Explicit synthetic purpose — نص ");
      assert.equal(saved.account_id, null);
      assert.equal(saved.business_unit_id, null);
      assert.deepEqual(saved.dimension_value_ids, []);
      assert.deepEqual(saved.review, {currency:"QAR",total:"0.10",proposed:true,unpaid:true,unapplied:true,
        actual_payment:false,invoice_application:false,operational_payable_change:false,approved:false,posted:false});
      const setup = await b.evaluate(`(async()=>{
        const token=(await(await fetch("/lab/session/")).json()).csrf_token;
        async function post(path,body) {
          const response=await fetch("/lab/"+path+"/",{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":token},body:JSON.stringify(body)});
          if(!response.ok) throw new Error("Synthetic setup rejected "+response.status);
          return response.json();
        }
        const account=await post("accounts",{code:"TEST-DRAFT-ACCT-ADV${width}",name_en:"Explicit advance draft account"});
        const dimension=await post("dimensions",{code:"TEST-DRAFT-DIM-ADV${width}",label_en:"Explicit advance analysis"});
        const value=await post("dimension-values",{dimension_id:dimension.id,code:"TEST-DRAFT-VAL-ADV${width}",label_en:"Synthetic advance value"});
        const context=await(await fetch("/lab/context/")).json();
        return {account:account.id,value:value.id,unit:context.business_units.find(u=>u.code==="TEST-BU-A").id};
      })()`);
      await b.reload(); await settled(b);
      await button(b, `Open supplier-advance proposal ${row.id}:`); await settled(b);
      assert.equal(await field(b, "Amount"), "0.10");
      assert.equal(await field(b, "Account"), "");
      other = await browser(width);
      await button(other, `Open supplier-advance proposal ${row.id}:`); await settled(other);
      await fill(b, "Amount", "1.01");
      await fill(b, "Purpose", "Explicit revised unpaid proposal");
      await choose(b, "Account", String(setup.account));
      await choose(b, "Business unit", String(setup.unit));
      await choose(b, "Dimensions", String(setup.value));
      await button(b, "Review supplier-advance proposal");
      await button(b, "Save supplier-advance revision"); await settled(b);
      await b.wait('document.querySelector(".invoice-editor>legend").textContent.includes("revision 2")');
      await fill(other, "Amount", "2.00");
      await fill(other, "Purpose", "Preserve conflicting local purpose");
      await button(other, "Review supplier-advance proposal");
      await button(other, "Save supplier-advance revision"); await settled(other);
      await other.wait('document.body.textContent.includes("Saved revision changed")');
      assert.equal(await field(other, "Amount"), "2.00");
      assert.equal(await field(other, "Purpose"), "Preserve conflicting local purpose");
      const dialogs = other.dialogs.length;
      const click = button(other, "Reload saved supplier advance");
      const deadline = Date.now()+5000;
      while(other.dialogs.length===dialogs&&Date.now()<deadline) await new Promise(resolve=>setTimeout(resolve,20));
      assert.equal(other.dialogs.at(-1)?.type, "confirm");
      await other.handleDialog(true); await click; await settled(other);
      assert.equal(await field(other, "Amount"), "1.01");
      assert.equal(await field(other, "Purpose"), "Explicit revised unpaid proposal");
      assert.equal(await field(other, "Account"), String(setup.account));
      const updated = await other.evaluate(`fetch("/lab/supplier-advances/${row.id}/").then(r=>r.json())`);
      assert.equal(updated.revision, 2);
      assert.deepEqual(updated.history.map(h=>h.snapshot.amount), ["0.10","1.01"]);
      assert.equal(updated.history[0].snapshot.account_id, null);
      assert.deepEqual(updated.dimension_value_ids, [setup.value]);
      assert.equal(updated.business_unit_id, setup.unit);
      assert.equal(updated.review.unpaid, true);
      assert.equal(updated.review.unapplied, true);
      const geometry = await other.evaluate(`({
        client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,
        minimumField:Math.min(...[...document.querySelectorAll('.invoice-editor input,.invoice-editor select,.invoice-editor textarea')].map(el=>el.getBoundingClientRect().width))
      })`);
      assert.ok(geometry.scroll<=geometry.client, JSON.stringify(geometry));
      assert.ok(geometry.minimumField>=160, JSON.stringify(geometry));
      console.log(`Synthetic supplier advance ${width}px geometry: ${JSON.stringify(geometry)}`);
      await other.evaluate('document.querySelector(\'[aria-label="Supplier reference"]\').scrollIntoView({block:"center"})');
      await other.screenshot(`supplier-advance-${width}`, "finance-supplier-advance-real");
      b.assertSafe(); other.assertSafe();
      assert.equal(b.calls.filter(c=>c.method==="POST"&&c.path==="/lab/supplier-advances/").length, 1);
      assert.equal(b.calls.filter(c=>c.method==="PUT").length, 1);
      assert.equal(other.calls.filter(c=>c.method==="PUT").length, 1);
    } finally { if(other) await other.close(); await b.close(); }
  });
}
