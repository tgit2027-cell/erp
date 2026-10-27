import test from "node:test";
import assert from "node:assert/strict";
import { browserHarness } from "./browser-harness.mjs";

const origin = process.env.FINANCE_LAB_ORIGIN;
if (origin !== "http://127.0.0.1:18069" ||
    !process.env.EXPECTED_DISPOSABLE_DATA?.startsWith("/tmp/tivoli-finance-lab.")) {
  throw new Error("Use only the owned disposable Finance facility.");
}
const selector = label => `[aria-label=${JSON.stringify(label)}]`;
async function button(b, text) {
  const predicate = `(b.getAttribute("aria-label")||b.textContent).startsWith(${JSON.stringify(text)})`;
  await b.wait(`Array.from(document.querySelectorAll("button")).some(b=>${predicate}&&!b.matches(":disabled"))`);
  await b.evaluate(`Array.from(document.querySelectorAll("button")).find(b=>${predicate}).setAttribute("data-test-click","yes")`);
  await b.click('[data-test-click="yes"]');
  await b.evaluate('document.querySelector("[data-test-click]")?.removeAttribute("data-test-click")');
}
async function fill(b, label, value) {
  await b.click(selector(label));
  await b.evaluate(`document.querySelector(${JSON.stringify(selector(label))}).select()`);
  await b.text(value);
}
async function choose(b, label, values) {
  await b.evaluate(`(()=>{
    const select=document.querySelector(${JSON.stringify(selector(label))});
    for(const option of select.options) option.selected=${JSON.stringify([values].flat())}.includes(option.value);
    select.dispatchEvent(new Event("change",{bubbles:true}));
  })()`);
}
async function date(b, value) {
  await b.evaluate(`(()=>{
    const input=document.querySelector('[aria-label="Proposed date"]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,${JSON.stringify(value)});
    input.dispatchEvent(new Event("change",{bubbles:true}));
  })()`);
}
async function confirmAction(b, action, ready) {
  const count = b.dialogs.length;
  let finished = false, failure;
  const promise = action().then(() => { finished = true; }, error => { finished = true; failure = error; });
  // A renderer evaluation can itself block behind beforeunload/confirm.
  // Observe CDP dialog events in Node until handled, rather than polling DOM.
  for (let index=0; index<120; index++) {
    if (b.dialogs.length>count) { await b.handleDialog(true); break; }
    if (finished && index >= 10) break;
    await new Promise(resolve=>setTimeout(resolve,25));
  }
  await promise;
  if (failure) throw failure;
  await b.wait(ready);
}
const settled = b => b.wait('!!document.querySelector("[data-testid=reversal-editor-fields]")&&!document.querySelector("[data-testid=reversal-editor-fields]").disabled');
const value = (b, label) => b.evaluate(`document.querySelector(${JSON.stringify(selector(label))}).value`);

for (const width of [1280, 390]) {
  test(`source-linked reversal persisted review, lost acknowledgement and competing edits at ${width}px`, async () => {
    let drop = true, losses = 0;
    const writes = [
      "POST /lab/session/", "POST /lab/accounts/", "POST /lab/dimensions/", "POST /lab/dimension-values/",
      "POST /lab/journals/", "POST /lab/reversals/",
    ];
    const b = await browserHarness({
      width, height: 950, realBackendOrigin: origin, apiPrefix: "/lab/",
      entryPath: "/lab-ui/", assetDirectory: "generated-artifacts/finance-draft-prototype",
      documentPaths: ["/lab-reversals-ui/", "/lab-review-ui/"],
      allowedRealWrites: writes,
      readyExpression: 'document.querySelector("h1")?.textContent.includes("Disposable Finance")',
      beforeRealResponse: async call => {
        if (drop && call.path === "/lab/reversals/" && call.method === "POST" && call.status === 201) {
          drop = false; losses++;
          throw new Error("Synthetic response delivery loss AFTER actual committed reversal proposal");
        }
      },
    });
    try {
      await button(b, "Open isolated synthetic test session");
      await b.wait('!!document.querySelector(".finance-lab-editor")&&!document.querySelector(".finance-lab-editor").disabled');
      const setup = await b.evaluate(`(async()=>{
        const token=(await(await fetch("/lab/session/")).json()).csrf_token;
        async function post(path,body,method="POST") {
          const r=await fetch("/lab/"+path+"/",{method,headers:{"Content-Type":"application/json","X-CSRFToken":token},body:JSON.stringify(body)});
          if(!r.ok) throw new Error("Synthetic setup rejected "+r.status); return r.json();
        }
        const a=await post("accounts",{code:"TEST-DRAFT-ACCT-REV-A${width}",name_en:"Synthetic source A"});
        const c=await post("accounts",{code:"TEST-DRAFT-ACCT-REV-B${width}",name_en:"Synthetic source B"});
        const d=await post("dimensions",{code:"TEST-DRAFT-DIM-REV${width}",label_en:"Synthetic analysis"});
        const v=await post("dimension-values",{dimension_id:d.id,code:"TEST-DRAFT-VAL-REV${width}",label_en:"Synthetic source dimension"});
        const context=await(await fetch("/lab/context/")).json();
        const unit=context.business_units.find(u=>u.code==="TEST-BU-A").id;
        const body={title_en:"TEST source revision ${width}",lines:[
          {account_id:a.id,business_unit_id:unit,dimension_value_ids:[v.id],debit:"0.30",credit:"0.00"},
          {account_id:c.id,business_unit_id:null,dimension_value_ids:[],debit:"0.00",credit:"0.30"}
        ]};
        const source=await post("journals",body);
        const latest=await post("journals/"+source.id,{...body,expected_revision:1,
          lines:body.lines.map(l=>({...l,debit:l.debit==="0.30"?"0.40":l.debit,credit:l.credit==="0.30"?"0.40":l.credit}))
        },"PUT");
        return {account:a.id,contra:c.id,value:v.id,unit,source,latest};
      })()`);
      writes.push(`PUT /lab/journals/${setup.source.id}/`);
      await b.click('a[href="/lab-reversals-ui/"]'); await settled(b);
      assert.equal(await value(b,"Source journal"),"");
      assert.equal(await value(b,"Source revision"),"");
      assert.equal(await value(b,"Proposed date"),"");
      assert.equal(await b.evaluate('document.querySelectorAll(".invoice-line").length'),0);
      await choose(b,"Source journal",String(setup.source.id));
      await b.wait('!document.querySelector(\'[aria-label="Source revision"]\').disabled');
      await choose(b,"Source revision","1");
      await b.wait(`document.querySelector('[aria-label="Read-only source journal snapshot"]').textContent.includes("Journal ${setup.source.id} · revision 1")`);
      assert.match(await b.evaluate(`document.querySelector('[aria-label="Read-only source journal snapshot"]').textContent`),/debit 0\.30/);
      assert.equal(await value(b,"Proposed date"),"");
      await date(b,"2028-02-29");
      await fill(b,"Explicit explanation",`Explicit source review ${width}`);
      for (let index=1; index<=2; index++) {
        await button(b,"Add explicit line");
        assert.equal(await value(b,`Debit line ${index}`),"");
        assert.equal(await value(b,`Credit line ${index}`),"");
        await choose(b,`Account line ${index}`,String(index===1?setup.account:setup.contra));
        if(index===1) {
          await choose(b,"Business unit line 1",String(setup.unit));
          await choose(b,"Dimensions line 1",String(setup.value));
        }
        await fill(b,`Debit line ${index}`,index===1?"0.00":"0.30");
        await fill(b,`Credit line ${index}`,index===1?"0.30":"0.00");
      }
      await b.evaluate(`(()=>{
        window.__reversalCreates=[]; const original=window.fetch;
        window.fetch=function(url,options) {
          if(url==="/lab/reversals/"&&options?.method==="POST")
            window.__reversalCreates.push({body:options.body,key:options.headers["X-Finance-Request-ID"]});
          return original.apply(this,arguments);
        };
      })()`);
      await button(b,"Save reversal proposal"); await settled(b);
      await b.wait('document.body.textContent.includes("Save outcome unknown")');
      assert.equal(losses,1);
      const committed = await b.evaluate(`fetch("/lab/reversals/").then(r=>r.json()).then(d=>d.reversals.filter(r=>r.explanation===${JSON.stringify(`Explicit source review ${width}`)}))`);
      assert.equal(committed.length,1);
      writes.push(`PUT /lab/reversals/${committed[0].id}/`);
      assert.equal(await value(b,"Credit line 1"),"0.30");
      await button(b,"Retry original request");
      await b.wait('document.body.textContent.includes("Save acknowledged")');
      const observed = await b.evaluate("window.__reversalCreates");
      assert.equal(observed.length,2);
      assert.deepEqual(observed[0],observed[1]);
      assert.match(observed[0].key,/^[0-9a-f-]{36}$/);
      await confirmAction(b,()=>b.click('.lab-recovery a[href*="?reopen="]'),
        `document.querySelector(".invoice-editor>legend")?.textContent.includes("proposal ${committed[0].id}, revision 1")`);
      await settled(b);
      await b.wait('!document.querySelector(".lab-recovery")');
      assert.match(await b.evaluate(`document.querySelector('[aria-label="Saved server reversal review"]').textContent`),/Balanced: yes · offsets source: yes/);
      assert.equal(await value(b,"Source revision"),"1");
      const id=committed[0].id;
      await fill(b,"Explicit explanation","Local competing draft preserved");
      assert.match(await b.evaluate(`document.querySelector('[aria-label="Saved server reversal review"]').textContent`),/Unsaved/);
      const competing = await b.evaluate(`(async()=>{
        const token=(await(await fetch("/lab/session/")).json()).csrf_token;
        const r=await(await fetch("/lab/reversals/${id}/")).json();
        const body={source_journal_id:r.source_journal_id,source_revision:r.source_revision,proposed_date:r.proposed_date,
          explanation:"Other editor saved",lines:r.lines,expected_revision:r.revision};
        return fetch("/lab/reversals/${id}/",{method:"PUT",headers:{"Content-Type":"application/json","X-CSRFToken":token},body:JSON.stringify(body)}).then(r=>r.status);
      })()`);
      assert.equal(competing,200);
      await button(b,"Save reversal revision"); await settled(b);
      await b.wait('document.body.textContent.includes("Saved revision changed")');
      assert.equal(await value(b,"Explicit explanation"),"Local competing draft preserved");
      await confirmAction(b,()=>button(b,"Reload saved reversal"),
        `document.querySelector(".invoice-editor>legend")?.textContent.includes("proposal ${id}, revision 2")`);
      await settled(b);
      assert.equal(await value(b,"Explicit explanation"),"Other editor saved");
      await fill(b,"Explicit explanation",`Final explicit review ${width}`);
      await button(b,"Save reversal revision"); await settled(b);
      await b.wait(`document.querySelector(".invoice-editor>legend").textContent.includes("revision 3")`);
      await b.reload(); await settled(b);
      await b.wait(`document.querySelector(".invoice-editor>legend").textContent.includes("revision 3")`);
      assert.equal(await value(b,"Explicit explanation"),`Final explicit review ${width}`);
      const records = await b.evaluate(`Promise.all([
        fetch("/lab/journals/${setup.source.id}/").then(r=>r.json()),
        fetch("/lab/reversals/${id}/").then(r=>r.json()),
        fetch("/lab/reversal-sources/${setup.source.id}/1/",{credentials:"omit"}).then(async r=>({status:r.status,body:await r.json()}))
      ])`);
      assert.deepEqual(records[0],setup.latest,"Source and all source history unchanged");
      assert.equal(records[1].source_revision,1);
      assert.equal(records[1].history.length,3);
      assert.deepEqual(records[1].history.map(h=>h.snapshot.source_revision),[1,1,1]);
      assert.equal(records[1].review.posted,false);
      assert.equal(records[1].review.offsets_source,true);
      assert.equal(records[2].status,403);
      assert.deepEqual(Object.keys(records[2].body),["detail"]);
      await b.click('a[href="/lab-review-ui/"]');
      await b.wait(`!!document.querySelector('a[href="/lab-reversals-ui/?reopen=${id}"]')`);
      await b.click(`a[href="/lab-reversals-ui/?reopen=${id}"]`); await settled(b);
      await b.wait(`document.querySelector(".invoice-editor>legend").textContent.includes("revision 3")`);
      const geometry = await b.evaluate(`({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})`);
      assert.ok(geometry.scroll<=geometry.client+1,JSON.stringify(geometry));
      await b.evaluate('document.activeElement?.blur()');
      await b.key("Tab");
      const focus = await b.evaluate(`({tag:document.activeElement.tagName,outline:getComputedStyle(document.activeElement).outlineStyle})`);
      assert.notEqual(focus.tag,"BODY");
      assert.notEqual(focus.outline,"none");
      console.log(`Reversal ${width}px geometry ${JSON.stringify(geometry)}, one committed acknowledgement lost; exact replay; source revision preserved.`);
      await b.screenshot(`reversal-${width}`,"finance-reversal-real");
      b.assertSafe();
    } finally {
      await b.close();
    }
  });
}
