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
  "POST /lab/dimension-values/", "POST /lab/customer-invoices/"];
async function button(b, prefix) {
  const match = `(b.getAttribute("aria-label")||b.textContent).startsWith(${JSON.stringify(prefix)})`;
  await b.wait(`Array.from(document.querySelectorAll("button")).some(b=>${match}&&!b.matches(":disabled"))`);
  await b.evaluate(`Array.from(document.querySelectorAll("button")).find(b=>${match}).setAttribute("data-ar-click","yes")`);
  await b.click('[data-ar-click="yes"]');
  await b.evaluate('document.querySelector("[data-ar-click]")?.removeAttribute("data-ar-click")');
}
const settled = b => b.wait('!!document.querySelector("[data-testid=customer-invoice-editor]")&&!document.querySelector("[data-testid=customer-invoice-editor]").disabled');
async function browser(width) {
  const b = await browserHarness({
    width, height: 950, realBackendOrigin: origin, apiPrefix: "/lab/",
    entryPath: "/lab-ui/", assetDirectory: "generated-artifacts/finance-draft-prototype",
    documentPaths: ["/lab-customer-invoices-ui/"], allowedRealWrites: writes,
    readyExpression: 'document.querySelector("h1")?.textContent.includes("Disposable Finance")',
  });
  try {
    await button(b, "Open isolated synthetic test session");
    await b.wait('!!document.querySelector(".finance-lab-editor")&&!document.querySelector(".finance-lab-editor").disabled');
    await b.click('a[href="/lab-customer-invoices-ui/"]');
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
  test(`real isolated internal customer invoice persists exact proposals, duplicate warning and conflict recovery at ${width}px`, async () => {
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
        const account=await post("accounts",{code:"TEST-DRAFT-ACCT-AR${width}",name_en:"Explicit customer invoice draft account"});
        const dimension=await post("dimensions",{code:"TEST-DRAFT-DIM-AR${width}",label_en:"Explicit customer analysis"});
        const value=await post("dimension-values",{dimension_id:dimension.id,code:"TEST-DRAFT-VAL-AR${width}",label_en:"Synthetic customer value"});
        const context=await(await fetch("/lab/context/")).json();
        return {account:account.id,value:value.id,unit:context.business_units.find(u=>u.code==="TEST-BU-A").id};
      })()`);
      await b.reload(); await settled(b);
      assert.equal(await field(b, "Invoice date"), "");
      assert.equal(await field(b, "Due date"), "");
      assert.equal(await field(b, "Customer details"), "");
      assert.equal(await field(b, "Line amount 1"), "");
      await fill(b, "Customer reference", ` TEST-CUSTOMER-${width} — عميل `);
      await fill(b, "Customer details", " Explicit synthetic customer details — بيانات ");
      await fill(b, "Invoice reference", ` TEST-AR-INVOICE-${width} — مرجع `);
      await date(b, "Invoice date", "2028-02-29");
      await button(b, "Add invoice line");
      for (const [number, value] of [[1, "0.10"], [2, "0.20"]]) {
        await fill(b, `Line description ${number}`, ` Explicit proposal ${number} — نص `);
        await fill(b, `Line amount ${number}`, value);
        await choose(b, `Line account ${number}`, String(setup.account));
      }
      await choose(b, "Line business unit 1", String(setup.unit));
      await choose(b, "Line dimensions 1", String(setup.value));
      await fill(b, "Line amount 2", "0.201");
      await button(b, "Review customer-invoice proposal");
      assert.equal(await b.evaluate('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Save customer-invoice proposal").disabled'), true);
      assert.equal(b.calls.filter(c=>c.method==="POST"&&c.path==="/lab/customer-invoices/").length, 0);
      assert.equal(await field(b, "Customer details"), " Explicit synthetic customer details — بيانات ");
      await fill(b, "Line amount 2", "0.20");
      await button(b, "Review customer-invoice proposal");
      assert.equal(await b.evaluate('document.querySelector(".invoice-total strong").textContent.includes("0.30")'), true);
      await button(b, "Save customer-invoice proposal"); await settled(b);
      await b.wait('document.querySelector(".invoice-editor>legend").textContent.includes("revision 1")');
      const row = await b.evaluate(`fetch("/lab/customer-invoices/").then(r=>r.json()).then(d=>d.customer_invoices.find(p=>p.invoice_reference===" TEST-AR-INVOICE-${width} — مرجع "))`);
      writes.push(`PUT /lab/customer-invoices/${row.id}/`);
      const saved = await b.evaluate(`fetch("/lab/customer-invoices/${row.id}/").then(r=>r.json())`);
      assert.equal(saved.review.total, "0.30");
      assert.equal(saved.due_date, null);
      assert.equal(saved.customer_details, " Explicit synthetic customer details — بيانات ");
      assert.deepEqual(saved.lines[0].dimension_value_ids, [setup.value]);
      assert.equal(saved.lines[1].business_unit_id, null);
      assert.deepEqual([saved.review.proposed,saved.review.issued_customer_invoice,
        saved.review.operational_receivable,saved.review.approved,saved.review.posted], [true,false,false,false,false]);
      const duplicate = await b.evaluate(`(async()=>{
        const token=(await(await fetch("/lab/session/")).json()).csrf_token;
        const saved=await(await fetch("/lab/customer-invoices/${row.id}/")).json();
        const payload=Object.fromEntries(["customer_reference","customer_details","invoice_reference","invoice_date","due_date","currency","lines"].map(k=>[k,saved[k]]));
        payload.customer_reference=payload.customer_reference.trim();
        const response=await fetch("/lab/customer-invoices/",{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":token},body:JSON.stringify(payload)});
        return {status:response.status,body:await response.json()};
      })()`);
      assert.equal(duplicate.status, 201);
      assert.notEqual(duplicate.body.id, row.id);
      assert.equal(duplicate.body.duplicate_warning.possible_duplicate, true);
      await b.reload(); await settled(b);
      await button(b, `Open customer invoice ${row.id}:`); await settled(b);
      assert.equal(await field(b, "Line amount 2"), "0.20");
      assert.equal(await b.evaluate('document.querySelector("[aria-label=\\"Possible duplicate warning\\"]").textContent.includes("not a uniqueness rule")'), true);
      other = await browser(width);
      await button(other, `Open customer invoice ${row.id}:`); await settled(other);
      await fill(b, "Line amount 1", "1.01");
      await fill(b, "Customer details", "Explicit revised synthetic details");
      await date(b, "Due date", "2028-03-20");
      await button(b, "Review customer-invoice proposal");
      await button(b, "Save customer-invoice revision"); await settled(b);
      await b.wait('document.querySelector(".invoice-editor>legend").textContent.includes("revision 2")');
      await fill(other, "Line amount 1", "2.00");
      await fill(other, "Customer details", "Retain conflicting local customer input");
      await button(other, "Review customer-invoice proposal");
      await button(other, "Save customer-invoice revision"); await settled(other);
      await other.wait('document.body.textContent.includes("Saved revision changed")');
      assert.equal(await field(other, "Line amount 1"), "2.00");
      assert.equal(await field(other, "Customer details"), "Retain conflicting local customer input");
      const dialogs = other.dialogs.length;
      const click = button(other, "Reload saved customer invoice");
      const deadline = Date.now()+5000;
      while (other.dialogs.length===dialogs && Date.now()<deadline) await new Promise(resolve=>setTimeout(resolve,20));
      assert.equal(other.dialogs.at(-1)?.type, "confirm");
      await other.handleDialog(true); await click; await settled(other);
      assert.equal(await field(other, "Line amount 1"), "1.01");
      assert.equal(await field(other, "Customer details"), "Explicit revised synthetic details");
      assert.equal(await field(other, "Due date"), "2028-03-20");
      const updated = await other.evaluate(`fetch("/lab/customer-invoices/${row.id}/").then(r=>r.json())`);
      assert.equal(updated.revision, 2);
      assert.deepEqual(updated.history.map(h=>h.snapshot.review.total), ["0.30","1.21"]);
      assert.equal(updated.history[0].snapshot.customer_details, saved.customer_details);
      const geometry = await other.evaluate(`({
        client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,
        minimumField:Math.min(...[...document.querySelectorAll('.invoice-editor input,.invoice-editor select,.invoice-editor textarea')].map(el=>el.getBoundingClientRect().width))
      })`);
      assert.ok(geometry.scroll<=geometry.client, JSON.stringify(geometry));
      assert.ok(geometry.minimumField>=160, JSON.stringify(geometry));
      console.log(`Synthetic customer invoice ${width}px geometry: ${JSON.stringify(geometry)}`);
      await other.evaluate('document.querySelector(\'[aria-label="Customer reference"]\').scrollIntoView({block:"center"})');
      await other.screenshot(`customer-invoice-${width}`, "finance-customer-invoice-real");
      b.assertSafe(); other.assertSafe();
      assert.equal(b.calls.filter(c=>c.method==="POST"&&c.path==="/lab/customer-invoices/").length, 2);
      assert.equal(b.calls.filter(c=>c.method==="PUT").length, 1);
      assert.equal(other.calls.filter(c=>c.method==="PUT").length, 1);
    } finally { if(other) await other.close(); await b.close(); }
  });
}
