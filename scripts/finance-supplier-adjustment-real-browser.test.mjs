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
  "POST /lab/dimension-values/", "POST /lab/invoices/", "POST /lab/adjustments/"];
async function button(b, prefix) {
  const match = `(b.getAttribute("aria-label")||b.textContent).startsWith(${JSON.stringify(prefix)})`;
  await b.wait(`Array.from(document.querySelectorAll("button")).some(b=>${match}&&!b.matches(":disabled"))`);
  await b.evaluate(`Array.from(document.querySelectorAll("button")).find(b=>${match}).setAttribute("data-adjustment-click","yes")`);
  await b.click('[data-adjustment-click="yes"]');
  await b.evaluate('document.querySelector("[data-adjustment-click]")?.removeAttribute("data-adjustment-click")');
}
const settled = b => b.wait('!!document.querySelector("[data-testid=adjustment-editor]")&&!document.querySelector("[data-testid=adjustment-editor]").disabled');
async function browser(width) {
  const b = await browserHarness({
    width, height: 950, realBackendOrigin: origin, apiPrefix: "/lab/",
    entryPath: "/lab-ui/", assetDirectory: "generated-artifacts/finance-draft-prototype",
    documentPaths: ["/lab-adjustments-ui/"], allowedRealWrites: writes,
    readyExpression: 'document.querySelector("h1")?.textContent.includes("Disposable Finance")',
  });
  try {
    await button(b, "Open isolated synthetic test session");
    await b.wait('!!document.querySelector(".finance-lab-editor")&&!document.querySelector(".finance-lab-editor").disabled');
    await b.click('a[href="/lab-adjustments-ui/"]');
    await settled(b);
    return b;
  } catch (error) { await b.close(); throw error; }
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
  test(`real isolated supplier credit/debit proposals preserve invoice, amounts and conflict recovery at ${width}px`, async () => {
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
        const account=await post("accounts",{code:"TEST-DRAFT-ACCT-ADJ${width}",name_en:"Explicit adjustment draft account"});
        const dimension=await post("dimensions",{code:"TEST-DRAFT-DIM-ADJ${width}",label_en:"Explicit adjustment analysis"});
        const value=await post("dimension-values",{dimension_id:dimension.id,code:"TEST-DRAFT-VAL-ADJ${width}",label_en:"Synthetic adjustment value"});
        const context=await(await fetch("/lab/context/")).json();
        const unit=context.business_units.find(u=>u.code==="TEST-BU-A").id;
        const invoice=await post("invoices",{
          supplier_reference:"TEST-SUPPLIER-ADJ${width}",invoice_reference:"TEST-INVOICE-ADJ${width}",
          invoice_date:"2028-02-29",due_date:null,currency:"QAR",lines:[{
            description:"Existing synthetic invoice proposal",amount:"10.00",account_id:account.id,
            business_unit_id:unit,dimension_value_ids:[value.id]
          }]
        });
        return {account:account.id,value:value.id,unit,invoice};
      })()`);
      await b.reload(); await settled(b);
      assert.equal(await field(b, "Proposal type"), "");
      assert.equal(await field(b, "Proposal date"), "");
      assert.equal(await field(b, "Linked invoice"), "");
      assert.equal(await field(b, "Line amount 1"), "");
      await choose(b, "Proposal type", "credit");
      await fill(b, "Supplier reference", "TEST-MISMATCH");
      await fill(b, "Document reference", ` TEST-CREDIT-${width} — مرجع `);
      await date(b, "Proposal date", "2028-02-29");
      await fill(b, "Reason", " Explicit synthetic proposal reason — نص ");
      await choose(b, "Linked invoice", String(setup.invoice.id));
      await button(b, "Add proposal line");
      for (const [number, amount] of [[1, "0.10"], [2, "0.20"]]) {
        await fill(b, `Line description ${number}`, ` Explicit proposal ${number} — نص `);
        await fill(b, `Line amount ${number}`, amount);
        await choose(b, `Line account ${number}`, String(setup.account));
      }
      await choose(b, "Line business unit 1", String(setup.unit));
      await choose(b, "Line dimensions 1", String(setup.value));
      await button(b, "Review adjustment proposal");
      assert.equal(await b.evaluate('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Save adjustment proposal").disabled'), true);
      assert.equal(await b.evaluate('document.querySelector("[role=alert]").textContent.includes("exactly match")'), true);
      // Surrounding ASCII whitespace is preserved, but both UI/server agree it
      // is ignored only for the exact link-reference comparison.
      await fill(b, "Supplier reference", ` TEST-SUPPLIER-ADJ${width} `);
      await fill(b, "Line amount 2", "-0.20");
      await button(b, "Review adjustment proposal");
      assert.equal(b.calls.filter(c=>c.method==="POST"&&c.path==="/lab/adjustments/").length, 0);
      assert.equal(await field(b, "Reason"), " Explicit synthetic proposal reason — نص ");
      await fill(b, "Line amount 2", "0.20");
      await button(b, "Review adjustment proposal");
      assert.equal(await b.evaluate('document.querySelector(".invoice-total strong").textContent'), "0.30");
      await button(b, "Save adjustment proposal"); await settled(b);
      await b.wait('document.querySelector(".invoice-editor>legend").textContent.includes("revision 1")');
      const row = await b.evaluate(`fetch("/lab/adjustments/").then(r=>r.json()).then(d=>d.adjustments.find(p=>p.document_reference===" TEST-CREDIT-${width} — مرجع "))`);
      writes.push(`PUT /lab/adjustments/${row.id}/`);
      const saved = await b.evaluate(`fetch("/lab/adjustments/${row.id}/").then(r=>r.json())`);
      assert.equal(saved.review.total, "0.30");
      assert.equal(saved.proposal_type, "credit");
      assert.equal(saved.linked_invoice.revision, 1);
      assert.equal(saved.linked_invoice.id, setup.invoice.id);
      assert.equal(saved.lines[0].description, " Explicit proposal 1 — نص ");
      assert.deepEqual(saved.lines[0].dimension_value_ids, [setup.value]);
      assert.equal(saved.lines[1].business_unit_id, null);
      assert.equal(saved.review.amount_basis, "explicit_non_negative_magnitudes");
      assert.deepEqual([saved.review.issued_credit_note,saved.review.settlement,
        saved.review.operational_payable_adjustment,saved.review.approved,saved.review.posted],
        [false,false,false,false,false]);
      assert.deepEqual(await b.evaluate(`fetch("/lab/invoices/${setup.invoice.id}/").then(r=>r.json())`), setup.invoice);
      await b.reload(); await settled(b);
      await button(b, `Open adjustment proposal ${row.id}:`); await settled(b);
      assert.equal(await field(b, "Line amount 2"), "0.20");
      assert.equal(await field(b, "Linked invoice"), String(setup.invoice.id));
      other = await browser(width);
      await button(other, `Open adjustment proposal ${row.id}:`); await settled(other);
      await choose(b, "Proposal type", "debit");
      await fill(b, "Line amount 1", "1.01");
      await fill(b, "Reason", "Explicit revised debit classification");
      await button(b, "Review adjustment proposal");
      await button(b, "Save adjustment revision"); await settled(b);
      await b.wait('document.querySelector(".invoice-editor>legend").textContent.includes("revision 2")');
      await fill(other, "Line amount 1", "2.00");
      await fill(other, "Reason", "Preserve conflicting local reason");
      await button(other, "Review adjustment proposal");
      await button(other, "Save adjustment revision"); await settled(other);
      await other.wait('document.body.textContent.includes("Saved revision changed")');
      assert.equal(await field(other, "Line amount 1"), "2.00");
      assert.equal(await field(other, "Reason"), "Preserve conflicting local reason");
      const dialogs = other.dialogs.length;
      const click = button(other, "Reload saved adjustment");
      const deadline = Date.now()+5000;
      while(other.dialogs.length===dialogs&&Date.now()<deadline) await new Promise(resolve=>setTimeout(resolve,20));
      assert.equal(other.dialogs.at(-1)?.type, "confirm");
      await other.handleDialog(true); await click; await settled(other);
      assert.equal(await field(other, "Line amount 1"), "1.01");
      assert.equal(await field(other, "Proposal type"), "debit");
      const updated = await other.evaluate(`fetch("/lab/adjustments/${row.id}/").then(r=>r.json())`);
      assert.equal(updated.revision, 2);
      assert.deepEqual(updated.history.map(h=>h.snapshot.review.total), ["0.30", "1.21"]);
      assert.deepEqual(updated.history.map(h=>h.snapshot.proposal_type), ["credit", "debit"]);
      assert.equal(updated.lines[1].amount, "0.20");
      assert.deepEqual(await other.evaluate(`fetch("/lab/invoices/${setup.invoice.id}/").then(r=>r.json())`), setup.invoice);
      const geometry = await other.evaluate(`({
        client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,
        minimumField:Math.min(...[...document.querySelectorAll('.invoice-editor input,.invoice-editor select,.invoice-editor textarea')].map(el=>el.getBoundingClientRect().width))
      })`);
      assert.ok(geometry.scroll<=geometry.client, JSON.stringify(geometry));
      assert.ok(geometry.minimumField>=160, JSON.stringify(geometry));
      console.log(`Synthetic supplier adjustment ${width}px geometry: ${JSON.stringify(geometry)}`);
      await other.evaluate('document.querySelector(\'[aria-label="Proposal type"]\').scrollIntoView({block:"center"})');
      await other.screenshot(`adjustment-${width}`, "finance-supplier-adjustment-real");
      b.assertSafe(); other.assertSafe();
      assert.equal(b.calls.filter(c=>c.method==="POST"&&c.path==="/lab/adjustments/").length, 1);
      assert.equal(b.calls.filter(c=>c.method==="PUT").length, 1);
      assert.equal(other.calls.filter(c=>c.method==="PUT").length, 1);
    } finally { if(other) await other.close(); await b.close(); }
  });
}
