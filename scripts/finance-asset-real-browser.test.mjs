import test from "node:test";
import assert from "node:assert/strict";
import { browserHarness } from "./browser-harness.mjs";

const origin = process.env.FINANCE_LAB_ORIGIN;
if (origin !== "http://127.0.0.1:18069" ||
    !process.env.EXPECTED_DISPOSABLE_DATA?.startsWith("/tmp/tivoli-finance-lab.")) {
  throw new Error("Use only the verified disposable Finance runner, never the shared preview.");
}
console.log("Synthetic fixed-asset browser facility ready (owned backend only).");
const selector = label => `[aria-label=${JSON.stringify(label)}]`;
const writes = ["POST /lab/session/", "POST /lab/accounts/", "POST /lab/dimensions/",
  "POST /lab/dimension-values/", "POST /lab/assets/"];
async function button(b, prefix) {
  const match = `(b.getAttribute("aria-label")||b.textContent).startsWith(${JSON.stringify(prefix)})`;
  await b.wait(`Array.from(document.querySelectorAll("button")).some(b=>${match}&&!b.matches(":disabled"))`);
  await b.evaluate(`Array.from(document.querySelectorAll("button")).find(b=>${match}).setAttribute("data-asset-click","yes")`);
  await b.click('[data-asset-click="yes"]');
  await b.evaluate('document.querySelector("[data-asset-click]")?.removeAttribute("data-asset-click")');
}
const settled = b => b.wait('!!document.querySelector("[data-testid=asset-editor]")&&!document.querySelector("[data-testid=asset-editor]").disabled');
async function browser(width) {
  const b = await browserHarness({
    width, height: 950, realBackendOrigin: origin, apiPrefix: "/lab/",
    entryPath: "/lab-ui/", assetDirectory: "generated-artifacts/finance-draft-prototype",
    documentPaths: ["/lab-assets-ui/"], allowedRealWrites: writes,
    readyExpression: 'document.querySelector("h1")?.textContent.includes("Disposable Finance")',
  });
  try {
    await button(b, "Open isolated synthetic test session");
    await b.wait('!!document.querySelector(".finance-lab-editor")&&!document.querySelector(".finance-lab-editor").disabled');
    await b.click('a[href="/lab-assets-ui/"]');
    await settled(b);
    assert.equal(await b.evaluate('document.querySelector("h1").textContent.toLowerCase().includes("asset")'), true);
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
    const input=document.querySelector('[aria-label="Acquisition date"]');
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
  test(`real isolated fixed-asset draft persists exact cost, references and conflict recovery at ${width}px`, async () => {
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
        const account=await post("accounts",{code:"TEST-DRAFT-ACCT-ASSET${width}",name_en:"Explicit synthetic draft account"});
        const dimension=await post("dimensions",{code:"TEST-DRAFT-DIM-ASSET${width}",label_en:"Explicit asset analysis"});
        const value=await post("dimension-values",{dimension_id:dimension.id,code:"TEST-DRAFT-VAL-ASSET${width}",label_en:"Synthetic analysis value"});
        const context=await(await fetch("/lab/context/")).json();
        return {account:account.id,value:value.id,unit:context.business_units.find(u=>u.code==="TEST-BU-A").id};
      })()`);
      await b.reload(); await settled(b);
      assert.equal(await field(b, "Acquisition date"), "");
      assert.equal(await field(b, "Acquisition cost"), "");
      assert.equal(await b.evaluate('document.querySelector("input[type=file]")===null'), true);
      await fill(b, "Asset identifier", ` TEST-ASSET-${width} `);
      await fill(b, "Asset name", `Synthetic asset ${width}`);
      await fill(b, "Asset description", " Explicit synthetic description — وصف ");
      await date(b, "2028-02-29");
      await fill(b, "Acquisition cost", "0.101");
      await choose(b, "Asset account", String(setup.account));
      await choose(b, "Asset business unit", String(setup.unit));
      await choose(b, "Asset dimensions", String(setup.value));
      await fill(b, "Asset reference", " TEST-REFERENCE-01 ");
      await fill(b, "Asset location details", " Proposed location text — موقع ");
      await button(b, "Review asset draft");
      assert.equal(await b.evaluate('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Save asset draft").disabled'), true);
      assert.equal(b.calls.filter(c=>c.method==="POST"&&c.path==="/lab/assets/").length, 0);
      assert.equal(await field(b, "Asset description"), " Explicit synthetic description — وصف ");
      await fill(b, "Acquisition cost", "0.10");
      await button(b, "Review asset draft");
      assert.equal(await field(b, "Acquisition cost"), "0.10");
      assert.equal(await b.evaluate('document.querySelector(".asset-cost strong").textContent.includes("0.10")'), true);
      await button(b, "Save asset draft"); await settled(b);
      await b.wait('document.querySelector(".asset-editor>legend").textContent.includes("revision 1")');
      const row = await b.evaluate(`fetch("/lab/assets/").then(r=>r.json()).then(d=>d.assets.find(p=>p.name==="Synthetic asset ${width}"))`);
      writes.push(`PUT /lab/assets/${row.id}/`);
      const saved = await b.evaluate(`fetch("/lab/assets/${row.id}/").then(r=>r.json())`);
      assert.equal(saved.review.proposed_cost, "0.10");
      assert.deepEqual([saved.review.approved,saved.review.capitalized,saved.review.posted], [false,false,false]);
      assert.equal(saved.asset_identifier, ` TEST-ASSET-${width} `);
      assert.equal(saved.description, " Explicit synthetic description — وصف ");
      assert.equal(saved.reference, " TEST-REFERENCE-01 ");
      assert.equal(saved.location_details, " Proposed location text — موقع ");
      assert.deepEqual(saved.dimension_value_ids, [setup.value]);
      await b.reload(); await settled(b);
      await button(b, `Open asset draft ${row.id}:`); await settled(b);
      assert.equal(await field(b, "Acquisition cost"), "0.10");
      assert.equal(await field(b, "Asset location details"), saved.location_details);
      other = await browser(width);
      await button(other, `Open asset draft ${row.id}:`); await settled(other);
      await fill(b, "Acquisition cost", "1.01");
      await button(b, "Review asset draft");
      await button(b, "Save asset revision"); await settled(b);
      await b.wait('document.querySelector(".asset-editor>legend").textContent.includes("revision 2")');
      await fill(other, "Acquisition cost", "2.00");
      await fill(other, "Asset description", "Retain my conflicting asset draft");
      await button(other, "Review asset draft");
      await button(other, "Save asset revision"); await settled(other);
      await other.wait('document.body.textContent.includes("Saved revision changed")');
      assert.equal(await field(other, "Acquisition cost"), "2.00");
      assert.equal(await field(other, "Asset description"), "Retain my conflicting asset draft");
      const dialogs = other.dialogs.length;
      const click = button(other, "Reload saved asset draft");
      const deadline = Date.now() + 5000;
      while (other.dialogs.length === dialogs && Date.now() < deadline) await new Promise(resolve=>setTimeout(resolve,20));
      assert.equal(other.dialogs.at(-1)?.type, "confirm");
      await other.handleDialog(true); await click; await settled(other);
      assert.equal(await field(other, "Acquisition cost"), "1.01");
      const updated = await other.evaluate(`fetch("/lab/assets/${row.id}/").then(r=>r.json())`);
      assert.equal(updated.revision, 2);
      assert.deepEqual(updated.history.map(h=>h.snapshot.review.proposed_cost), ["0.10","1.01"]);
      assert.equal(updated.reference, saved.reference);
      assert.equal(updated.location_details, saved.location_details);
      assert.deepEqual([updated.review.approved,updated.review.capitalized,updated.review.posted], [false,false,false]);
      const geometry = await other.evaluate(`({
        client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,
        minimumField:Math.min(...[...document.querySelectorAll('.asset-editor input,.asset-editor select,.asset-editor textarea')].map(el=>el.getBoundingClientRect().width))
      })`);
      assert.ok(geometry.scroll <= geometry.client, JSON.stringify(geometry));
      assert.ok(geometry.minimumField >= 160, JSON.stringify(geometry));
      console.log(`Synthetic asset ${width}px geometry: ${JSON.stringify(geometry)}`);
      await other.evaluate('document.querySelector(\'[aria-label="Acquisition cost"]\').scrollIntoView({block:"center"})');
      await other.screenshot(`asset-${width}`, "finance-asset-real");
      b.assertSafe(); other.assertSafe();
      assert.equal(b.calls.filter(c=>c.method==="POST"&&c.path==="/lab/assets/").length, 1);
      assert.equal(b.calls.filter(c=>c.method==="PUT").length, 1);
      assert.equal(other.calls.filter(c=>c.method==="PUT").length, 1);
    } finally { if (other) await other.close(); await b.close(); }
  });
}
