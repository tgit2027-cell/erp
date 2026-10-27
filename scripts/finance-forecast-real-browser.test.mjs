import test from "node:test";
import assert from "node:assert/strict";
import { browserHarness } from "./browser-harness.mjs";

const origin = process.env.FINANCE_LAB_ORIGIN;
if (origin !== "http://127.0.0.1:18069" ||
    !process.env.EXPECTED_DISPOSABLE_DATA?.startsWith("/tmp/tivoli-finance-lab.")) {
  throw new Error("Use only the verified disposable Finance facility.");
}
console.log("Synthetic forecast browser facility ready (owned backend only).");
const selector = label => `[aria-label=${JSON.stringify(label)}]`;
const writes = ["POST /lab/session/", "POST /lab/dimensions/", "POST /lab/dimension-values/", "POST /lab/forecasts/"];
async function button(b, prefix) {
  const match = `(b.getAttribute("aria-label")||b.textContent).startsWith(${JSON.stringify(prefix)})`;
  await b.wait(`Array.from(document.querySelectorAll("button")).some(b=>${match}&&!b.matches(":disabled"))`);
  await b.evaluate(`Array.from(document.querySelectorAll("button")).find(b=>${match}).setAttribute("data-forecast-click","yes")`);
  await b.click('[data-forecast-click="yes"]');
  await b.evaluate('document.querySelector("[data-forecast-click]")?.removeAttribute("data-forecast-click")');
}
const settled = b => b.wait('!!document.querySelector("[data-testid=forecast-editor]")&&!document.querySelector("[data-testid=forecast-editor]").disabled');
async function browser(width) {
  const b = await browserHarness({
    width, height: 950, realBackendOrigin: origin, apiPrefix: "/lab/",
    entryPath: "/lab-ui/", assetDirectory: "generated-artifacts/finance-draft-prototype",
    documentPaths: ["/lab-forecasts-ui/"], allowedRealWrites: writes,
    readyExpression: 'document.querySelector("h1")?.textContent.includes("Disposable Finance")',
  });
  try {
    await button(b, "Open isolated synthetic test session");
    await b.wait('!!document.querySelector(".finance-lab-editor")&&!document.querySelector(".finance-lab-editor").disabled');
    await b.click('a[href="/lab-forecasts-ui/"]'); await settled(b);
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
  test(`real isolated forecast persists same-date negative balances and revision recovery at ${width}px`, async () => {
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
        const dimension=await post("dimensions",{code:"TEST-DRAFT-DIM-FOR${width}",label_en:"Explicit forecast analysis"});
        const value=await post("dimension-values",{dimension_id:dimension.id,code:"TEST-DRAFT-VAL-FOR${width}",label_en:"Synthetic forecast value"});
        const context=await(await fetch("/lab/context/")).json();
        return {value:value.id,unit:context.business_units.find(u=>u.code==="TEST-BU-A").id};
      })()`);
      await b.reload(); await settled(b);
      assert.equal(await field(b, "Opening as-of date"), "");
      assert.equal(await field(b, "Opening QAR balance"), "");
      assert.equal(await field(b, "Flow date 1"), "");
      assert.equal(await field(b, "Flow amount 1"), "");
      await fill(b, "Forecast name", ` Synthetic forecast ${width} — نموذج `);
      await date(b, "Opening as-of date", "2028-02-29");
      await fill(b, "Opening QAR balance", "-0.10");
      await button(b, "Add cash flow");
      await button(b, "Add cash flow");
      for (const [number, day, direction, value] of [
        [1, "2028-03-01", "inflow", "0.20"],
        [2, "2028-02-29", "outflow", "0.30"],
        [3, "2028-02-29", "inflow", "0.10"],
      ]) {
        await date(b, `Flow date ${number}`, day);
        await choose(b, `Flow direction ${number}`, direction);
        await fill(b, `Flow amount ${number}`, value);
        await fill(b, `Flow description ${number}`, ` Explicit synthetic flow ${number} — نص `);
      }
      await choose(b, "Flow business unit 1", String(setup.unit));
      await choose(b, "Flow dimensions 1", String(setup.value));
      await fill(b, "Flow amount 1", "0.201");
      await button(b, "Review forecast");
      assert.equal(await b.evaluate('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Save forecast draft").disabled'), true);
      assert.equal(b.calls.filter(c=>c.method==="POST"&&c.path==="/lab/forecasts/").length, 0);
      assert.equal(await field(b, "Flow description 2"), " Explicit synthetic flow 2 — نص ");
      await fill(b, "Flow amount 1", "0.20");
      await button(b, "Review forecast");
      assert.deepEqual(await b.evaluate('[...document.querySelectorAll(".review-table tbody tr")].map(row=>[row.cells[0].textContent,row.cells[5].textContent])'),
                       [["2","-0.40"],["3","-0.30"],["1","-0.10"]]);
      await button(b, "Save forecast draft"); await settled(b);
      await b.wait('document.querySelector(".forecast-editor>legend").textContent.includes("revision 1")');
      const row = await b.evaluate(`fetch("/lab/forecasts/").then(r=>r.json()).then(d=>d.forecasts.find(p=>p.name===" Synthetic forecast ${width} — نموذج "))`);
      writes.push(`PUT /lab/forecasts/${row.id}/`);
      const saved = await b.evaluate(`fetch("/lab/forecasts/${row.id}/").then(r=>r.json())`);
      assert.equal(saved.review.closing_balance, "-0.10");
      assert.deepEqual([saved.review.total_inflows,saved.review.total_outflows,saved.review.net_change], ["0.30","0.30","0.00"]);
      assert.equal(saved.review.ordering, "date_ascending_then_input_order");
      assert.deepEqual(saved.entries.map(e=>e.date), ["2028-03-01","2028-02-29","2028-02-29"]);
      assert.equal(saved.entries[0].description, " Explicit synthetic flow 1 — نص ");
      assert.deepEqual(saved.entries[0].dimension_value_ids, [setup.value]);
      assert.equal(saved.entries[1].business_unit_id, null);
      assert.deepEqual([saved.review.proposed,saved.review.actual_cash,saved.review.posted,saved.review.approved], [true,false,false,false]);
      await b.reload(); await settled(b);
      await button(b, `Open forecast draft ${row.id}:`); await settled(b);
      assert.equal(await field(b, "Opening QAR balance"), "-0.10");
      assert.equal(await field(b, "Flow date 1"), "2028-03-01");
      other = await browser(width);
      await button(other, `Open forecast draft ${row.id}:`); await settled(other);
      await fill(b, "Opening QAR balance", "-1.01");
      await button(b, "Review forecast");
      await button(b, "Save forecast revision"); await settled(b);
      await b.wait('document.querySelector(".forecast-editor>legend").textContent.includes("revision 2")');
      await fill(other, "Opening QAR balance", "-2.00");
      await fill(other, "Flow description 1", "Retain my conflicting local forecast");
      await button(other, "Review forecast");
      await button(other, "Save forecast revision"); await settled(other);
      await other.wait('document.body.textContent.includes("Saved revision changed")');
      assert.equal(await field(other, "Opening QAR balance"), "-2.00");
      assert.equal(await field(other, "Flow description 1"), "Retain my conflicting local forecast");
      const dialogs = other.dialogs.length;
      const click = button(other, "Reload saved forecast");
      const deadline = Date.now()+5000;
      while (other.dialogs.length===dialogs && Date.now()<deadline) await new Promise(resolve=>setTimeout(resolve,20));
      assert.equal(other.dialogs.at(-1)?.type, "confirm");
      await other.handleDialog(true); await click; await settled(other);
      assert.equal(await field(other, "Opening QAR balance"), "-1.01");
      const updated = await other.evaluate(`fetch("/lab/forecasts/${row.id}/").then(r=>r.json())`);
      assert.equal(updated.revision, 2);
      assert.deepEqual(updated.history.map(h=>h.snapshot.review.closing_balance), ["-0.10","-1.01"]);
      assert.equal(updated.entries[0].description, saved.entries[0].description);
      const geometry = await other.evaluate(`({
        client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,
        minimumField:Math.min(...[...document.querySelectorAll('.forecast-editor input,.forecast-editor select,.forecast-editor textarea')].map(el=>el.getBoundingClientRect().width))
      })`);
      assert.ok(geometry.scroll<=geometry.client, JSON.stringify(geometry));
      assert.ok(geometry.minimumField>=160, JSON.stringify(geometry));
      console.log(`Synthetic forecast ${width}px geometry: ${JSON.stringify(geometry)}`);
      await other.evaluate('document.querySelector(\'[aria-label="Opening QAR balance"]\').scrollIntoView({block:"center"})');
      await other.screenshot(`forecast-${width}`, "finance-forecast-real");
      b.assertSafe(); other.assertSafe();
      assert.equal(b.calls.filter(c=>c.method==="POST"&&c.path==="/lab/forecasts/").length, 1);
      assert.equal(b.calls.filter(c=>c.method==="PUT").length, 1);
      assert.equal(other.calls.filter(c=>c.method==="PUT").length, 1);
    } finally { if(other) await other.close(); await b.close(); }
  });
}
