import test from "node:test";
import assert from "node:assert/strict";
import { browserHarness } from "./browser-harness.mjs";

const origin = process.env.FINANCE_LAB_ORIGIN;
if (origin !== "http://127.0.0.1:18069" ||
    !process.env.EXPECTED_DISPOSABLE_DATA?.startsWith("/tmp/tivoli-finance-lab.")) {
  throw new Error("Use only the verified disposable Finance facility.");
}
async function button(b, prefix) {
  const match = `(b.getAttribute("aria-label")||b.textContent).startsWith(${JSON.stringify(prefix)})`;
  await b.wait(`Array.from(document.querySelectorAll("button")).some(b=>${match}&&!b.matches(":disabled"))`);
  await b.evaluate(`Array.from(document.querySelectorAll("button")).find(b=>${match}).setAttribute("data-recovery-click","yes")`);
  await b.click('[data-recovery-click="yes"]');
  await b.evaluate('document.querySelector("[data-recovery-click]")?.removeAttribute("data-recovery-click")');
}
async function fill(b, label, value) {
  const selector = `[aria-label=${JSON.stringify(label)}]`;
  await b.click(selector);
  await b.evaluate(`document.querySelector(${JSON.stringify(selector)}).select()`);
  await b.text(value);
}
const settled = b => b.wait('!!document.querySelector("[data-testid=supplier-advance-editor-fields]")&&!document.querySelector("[data-testid=supplier-advance-editor-fields]").disabled');
async function confirmNavigation(b, action, ready) {
  const count = b.dialogs.length;
  const promise = action();
  for (let index=0; index<100; index++) {
    if (b.dialogs.length>count) { await b.handleDialog(true); break; }
    if (await b.evaluate(ready).catch(()=>false)) break;
    await new Promise(resolve=>setTimeout(resolve,30));
  }
  await promise;
  await b.wait(ready);
}
for (const width of [1280, 390]) {
  test(`committed response-loss recovery, exact replay and cross-type reopen at ${width}px`, async () => {
    let dropNext = true;
    let dropped = 0;
    const writes = ["POST /lab/session/", "POST /lab/accounts/", "POST /lab/invoices/", "POST /lab/supplier-advances/"];
    const b = await browserHarness({
      width, height: 950, realBackendOrigin: origin, apiPrefix: "/lab/",
      entryPath: "/lab-ui/", assetDirectory: "generated-artifacts/finance-draft-prototype",
      documentPaths: ["/lab-supplier-advances-ui/", "/lab-invoices-ui/", "/lab-review-ui/"],
      allowedRealWrites: writes,
      readyExpression: 'document.querySelector("h1")?.textContent.includes("Disposable Finance")',
      beforeRealResponse: async call => {
        if (dropNext && call.path === "/lab/supplier-advances/" && call.method === "POST" && call.status === 201) {
          dropNext = false; dropped++;
          // Fault only AFTER upstream Django finished/committed. Proxy returns
          // 502 instead of delivering the original acknowledgement; no mocked
          // business API/data or pre-commit exception substitutes for the server.
          throw new Error("Synthetic post-commit acknowledgement delivery loss");
        }
      },
    });
    try {
      await button(b, "Open isolated synthetic test session");
      await b.wait('!!document.querySelector(".finance-lab-editor")&&!document.querySelector(".finance-lab-editor").disabled');
      await b.click('a[href="/lab-supplier-advances-ui/"]'); await settled(b);
      // Read-only observer of actual fetch headers/body; never changes responses.
      await b.evaluate(`(()=>{
        window.__observedCreates=[];
        const original=window.fetch;
        window.fetch=function(url,options) {
          if(url==="/lab/supplier-advances/"&&options?.method==="POST")
            window.__observedCreates.push({body:options.body,id:options.headers["X-Finance-Request-ID"]});
          return original.apply(this,arguments);
        };
      })()`);
      const reference = `TEST-RECOVER-${width}`;
      await fill(b, "Supplier reference", `TEST-SUPPLIER-${width}`);
      await fill(b, "Document reference", reference);
      await fill(b, "Purpose", "Explicit isolated recovery review — نص");
      await b.evaluate(`(()=>{
        const input=document.querySelector('[aria-label="Proposed date"]');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,"2028-02-29");
        input.dispatchEvent(new Event("change",{bubbles:true}));
      })()`);
      await fill(b, "Amount", "0.10");
      await button(b, "Review supplier-advance proposal");
      await button(b, "Save supplier-advance proposal"); await settled(b);
      await b.wait('document.body.textContent.includes("Outcome unknown")');
      assert.equal(dropped, 1);
      const committed = await b.evaluate(`fetch("/lab/supplier-advances/").then(r=>r.json()).then(d=>d.advances.filter(p=>p.document_reference===${JSON.stringify(reference)}))`);
      assert.equal(committed.length, 1);
      assert.equal(committed[0].revision, 1);
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Amount"]\').value'), "0.10");
      await button(b, "Retry original request");
      await b.wait('document.body.textContent.includes("Save acknowledged")');
      const observed = await b.evaluate("window.__observedCreates");
      assert.equal(observed.length, 2);
      assert.match(observed[0].id, /^[0-9a-f-]{36}$/);
      assert.deepEqual(observed[0], observed[1]);
      const afterRetry = await b.evaluate(`fetch("/lab/supplier-advances/").then(r=>r.json()).then(d=>d.advances.filter(p=>p.document_reference===${JSON.stringify(reference)}))`);
      assert.equal(afterRetry.length, 1);
      assert.equal(afterRetry[0].id, committed[0].id);
      const requestId = observed[0].id;
      const unauthorized = await b.evaluate(`fetch("/lab/requests/${requestId}/",{credentials:"omit"}).then(async r=>({status:r.status,body:await r.json()}))`);
      assert.equal(unauthorized.status, 403);
      assert.deepEqual(Object.keys(unauthorized.body), ["detail"]);
      await confirmNavigation(b, ()=>b.click('.lab-recovery a[href*="?reopen="]'),
        `document.querySelector(".invoice-editor>legend")?.textContent.includes("proposal ${committed[0].id}, revision 1")`);
      await settled(b);
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Amount"]\').value'), "0.10");
      await b.wait('!document.querySelector(".lab-recovery")');

      // Second committed loss: navigate away/reload, retaining only identity
      // metadata. No payload is persisted or automatic POST is issued.
      await button(b, "New supplier-advance proposal"); await settled(b);
      await fill(b, "Supplier reference", `TEST-SUPPLIER-${width}`);
      await fill(b, "Document reference", `${reference}-RELOAD`);
      await fill(b, "Purpose", "Reload recovery scenario");
      await b.evaluate(`(()=>{
        const input=document.querySelector('[aria-label="Proposed date"]');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,"2028-03-01");
        input.dispatchEvent(new Event("change",{bubbles:true}));
      })()`);
      await fill(b, "Amount", "0.20");
      dropNext = true;
      await button(b, "Review supplier-advance proposal");
      await button(b, "Save supplier-advance proposal"); await settled(b);
      await b.wait('document.body.textContent.includes("Outcome unknown")');
      await confirmNavigation(b, ()=>b.click('a[href="/lab-review-ui/"]'),
        'location.pathname==="/lab-review-ui/"&&!!document.querySelector(".lab-review-register")');
      await b.wait('document.querySelector(".lab-recovery")?.textContent.includes("Outcome unknown")');
      assert.equal(await b.evaluate('[...document.querySelectorAll(".lab-recovery button")].find(b=>b.textContent.startsWith("Retry original request")).disabled'), true);
      await button(b, "Check request status");
      await b.wait('document.body.textContent.includes("Save acknowledged")');
      await b.click('.lab-recovery a[href*="?reopen="]'); await settled(b);
      await b.wait('document.querySelector(\'[aria-label="Document reference"]\').value.endsWith("-RELOAD")');
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Amount"]\').value'), "0.20");
      const count = b.calls.filter(call=>call.path==="/lab/supplier-advances/"&&call.method==="POST").length;
      assert.equal(count, 3); // two creates, one same-request replay, no auto retry

      // Existing second type uses the same create protocol, original editor,
      // and selected revision. No synthetic supplier master or payment exists.
      const invoice = await b.evaluate(`(async()=>{
        const token=(await(await fetch("/lab/session/")).json()).csrf_token;
        const post=async(path,body,id)=>{
          const response=await fetch("/lab/"+path+"/",{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":token,...(id?{"X-Finance-Request-ID":id}:{})},body:JSON.stringify(body)});
          if(!response.ok) throw new Error("Synthetic setup rejected "+response.status);
          return response.json();
        };
        const account=await post("accounts",{code:"TEST-DRAFT-ACCT-REC${width}",name_en:"Recovery review account"});
        return post("invoices",{supplier_reference:"TEST-SUPPLIER-REC${width}",invoice_reference:"TEST-INVOICE-REC${width}",invoice_date:"2028-02-29",due_date:null,currency:"QAR",
          lines:[{description:"Explicit synthetic line",amount:"0.30",account_id:account.id,business_unit_id:null,dimension_value_ids:[]}]},crypto.randomUUID());
      })()`);
      await b.click('a[href="/lab-review-ui/"]');
      await b.wait(`!!document.querySelector('a[href="/lab-invoices-ui/?reopen=${invoice.id}"]')`);
      const geometry = await b.evaluate(`({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})`);
      assert.ok(geometry.scroll<=geometry.client, JSON.stringify(geometry));
      console.log(`Review index ${width}px: ${JSON.stringify(geometry)}, committed acknowledgement losses=${dropped}`);
      await b.screenshot(`review-${width}`, "finance-consolidation-real");
      await b.click(`a[href="/lab-invoices-ui/?reopen=${invoice.id}"]`);
      await b.wait('document.querySelector(\'[aria-label="Invoice reference"]\')?.value==="TEST-INVOICE-REC'+width+'"');
      await b.reload();
      await b.wait('document.querySelector(\'[aria-label="Invoice reference"]\')?.value==="TEST-INVOICE-REC'+width+'"');
      assert.equal(dropped, 2);
      b.assertSafe();
    } finally { await b.close(); }
  });
}
