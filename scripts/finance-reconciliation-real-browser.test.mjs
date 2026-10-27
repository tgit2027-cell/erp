import test from "node:test";
import assert from "node:assert/strict";
import { browserHarness } from "./browser-harness.mjs";

const origin = process.env.FINANCE_LAB_ORIGIN;
if (origin !== "http://127.0.0.1:18069" ||
    !process.env.EXPECTED_DISPOSABLE_DATA?.startsWith("/tmp/tivoli-finance-lab.")) {
  throw new Error("Use only the verified disposable Finance runner.");
}
const selector = label => `[aria-label=${JSON.stringify(label)}]`;
const writes = ["POST /lab/session/", "POST /lab/reconciliations/"];
if (process.env.FINANCE_LAB_RECONCILIATION_WIDTH &&
    !["1280", "390"].includes(process.env.FINANCE_LAB_RECONCILIATION_WIDTH)) {
  throw new Error("Select only an existing 1280px or 390px browser case; do not silently run zero cases.");
}
async function button(b, prefix) {
  await b.wait(`Array.from(document.querySelectorAll("button")).some(b=>b.textContent.startsWith(${JSON.stringify(prefix)})&&!b.matches(":disabled"))`);
  await b.evaluate(`Array.from(document.querySelectorAll("button")).find(b=>b.textContent.startsWith(${JSON.stringify(prefix)})).setAttribute("data-rec-click","yes")`);
  await b.click('[data-rec-click="yes"]');
  await b.evaluate('document.querySelector("[data-rec-click]")?.removeAttribute("data-rec-click")');
}
const settled = b => b.wait('!!document.querySelector("[data-testid=reconciliation-editor]")&&!document.querySelector("[data-testid=reconciliation-editor]").disabled');
async function browser(width) {
  const b = await browserHarness({
    width, height: 950, realBackendOrigin: origin, apiPrefix: "/lab/",
    entryPath: "/lab-ui/", assetDirectory: "generated-artifacts/finance-draft-prototype",
    documentPaths: ["/lab-reconciliation-ui/"], allowedRealWrites: writes,
    readyExpression: 'document.querySelector("h1")?.textContent.includes("Disposable Finance")',
  });
  try {
    await button(b, "Open isolated synthetic test session");
    await b.wait('!!document.querySelector(".finance-lab-editor")&&!document.querySelector(".finance-lab-editor").disabled');
    await b.click('a[href="/lab-reconciliation-ui/"]');
    await settled(b);
    assert.equal(await b.evaluate('document.querySelector("h1").textContent'), "Reconciliation proposals");
    return b;
  } catch (error) {
    console.error("Synthetic reconciliation setup diagnostic", await b.evaluate(`({
      path:location.pathname,title:document.querySelector("h1")?.textContent,
      alerts:Array.from(document.querySelectorAll('[role="alert"]')).map(el=>el.textContent),
      link:(()=>{const a=document.querySelector('a[href="/lab-reconciliation-ui/"]');if(!a)return null;
        const r=a.getBoundingClientRect();return {rects:[...a.getClientRects()].map(r=>({x:r.x,y:r.y,width:r.width,height:r.height})),
        centerTag:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.tagName};})()
    })`));
    console.error("Synthetic setup dialog types", b.dialogs.map(dialog => dialog.type));
    await b.screenshot(`setup-failure-${width}`, "finance-reconciliation-real");
    await b.close(); throw error;
  }
}
async function fill(b, label, value) {
  await b.click(selector(label));
  await b.evaluate(`document.querySelector(${JSON.stringify(selector(label))}).select()`);
  await b.text(value);
}
async function date(b, label, value) {
  await b.evaluate(`(() => {
    const input=document.querySelector(${JSON.stringify(selector(label))});
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,${JSON.stringify(value)});
    input.dispatchEvent(new Event("change",{bubbles:true}));
  })()`);
}
async function choose(b, label, value) {
  await b.evaluate(`(() => {
    const input=document.querySelector(${JSON.stringify(selector(label))});
    input.value=${JSON.stringify(value)};
    input.dispatchEvent(new Event("change",{bubbles:true}));
  })()`);
}
async function source(b, side, index, id, amount, direction) {
  await button(b, `Add ${side.toLowerCase()} row`);
  assert.equal(await b.evaluate(`document.querySelector(${JSON.stringify(selector(`${side} row direction ${index}`))}).value`), "");
  await fill(b, `${side} row ID ${index}`, id);
  await date(b, `${side} row date ${index}`, side === "Statement" ? "2028-02-29" : "2028-03-01");
  await fill(b, `${side} row amount ${index}`, amount);
  await choose(b, `${side} row direction ${index}`, direction);
  await fill(b, `${side} row reference ${index}`, ` Synthetic ${id} `);
  await fill(b, `${side} row explanation ${index}`, ` Explicit ${id} — بيانات مقدمة `);
}
async function match(b, left, right, explanation) {
  await choose(b, "Match statement row", left);
  await choose(b, "Match book row", right);
  await fill(b, "Match explanation", explanation);
  await button(b, "Add explicit match");
}
const field = (b, label) => b.evaluate(`document.querySelector(${JSON.stringify(selector(label))}).value`);
const formula = (b, key) => b.evaluate(`document.querySelector('[data-testid="reconciliation-formula-${key}"]').textContent`);

for (const width of [1280, 390].filter(width => !process.env.FINANCE_LAB_RECONCILIATION_WIDTH ||
    String(width) === process.env.FINANCE_LAB_RECONCILIATION_WIDTH)) {
  test(`real isolated reconciliation persists manual matches, arithmetic and conflict recovery at ${width}px`, async () => {
    const b = await browser(width);
    let other;
    try {
      await fill(b, "Reconciliation proposal name", `Synthetic reconciliation ${width}`);
      await date(b, "Statement balance date", "2028-02-29");
      await date(b, "Book balance date", "2028-03-01");
      await fill(b, "Statement balance", "100.30");
      await fill(b, "Book balance", "100.10");
      await fill(b, "Reconciliation explanation", " Explicit arithmetic review — شرح ");
      await source(b, "Statement", 1, "S1", "0.10", "increase");
      await source(b, "Statement", 2, "S2", "0.20", "increase");
      await source(b, "Statement", 3, "S3", "0.05", "decrease");
      await source(b, "Book", 1, "B1", "0.10", "increase");
      await source(b, "Book", 2, "B2", "0.02", "decrease");
      assert.equal(await b.evaluate('document.body.textContent.includes("No rows are matched. Nothing is inferred.")'), true);
      // Invalid proposals remain local and identifiable; neither is silently dropped.
      await match(b, "S1", "B2", "Incompatible explicit proposal");
      await button(b, "Review proposal");
      assert.equal(await b.evaluate('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Save reconciliation proposal").disabled'), true);
      assert.equal(b.calls.filter(c => c.method === "POST" && c.path === "/lab/reconciliations/").length, 0);
      await button(b, "Unmatch pair 1");
      await match(b, "S1", "B1", " Explicit full-row match ");
      await match(b, "S1", "B1", "Duplicate must be corrected");
      await button(b, "Review proposal");
      assert.equal(await b.evaluate('document.body.textContent.includes("matched more than once")'), true);
      assert.equal(b.calls.filter(c => c.method === "POST" && c.path === "/lab/reconciliations/").length, 0);
      await button(b, "Unmatch pair 2");
      assert.equal(await b.evaluate('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Remove statement row 1").disabled'), true);
      assert.equal(await formula(b, "balance-difference"), "0.20");
      assert.equal(await formula(b, "unmatched-statement-ids"), "S2, S3");
      assert.equal(await formula(b, "unmatched-book-ids"), "B2");
      assert.equal(await formula(b, "unmatched-row-difference"), "0.17");
      assert.equal(await formula(b, "comparison-residual"), "0.03");
      await button(b, "Review proposal");
      await button(b, "Save reconciliation proposal"); await settled(b);
      await b.wait('document.querySelector(".rec-editor > legend").textContent.includes("revision 1")');
      const row = await b.evaluate(`fetch("/lab/reconciliations/").then(r=>r.json()).then(d=>d.proposals.find(p=>p.name==="Synthetic reconciliation ${width}"))`);
      writes.push(`PUT /lab/reconciliations/${row.id}/`);
      const saved = await b.evaluate(`fetch("/lab/reconciliations/${row.id}/").then(r=>r.json())`);
      assert.equal(saved.review.comparison_residual, "0.03");
      assert.equal(saved.review.operational_reconciled, false);
      assert.equal(saved.review.posted, false);
      assert.equal(saved.review.warnings[0].code, "balance_dates_differ");
      assert.equal(saved.statement_rows[0].explanation, " Explicit S1 — بيانات مقدمة ");
      assert.equal(saved.statement_rows[0].reference, " Synthetic S1 ");
      await b.reload(); await settled(b);
      await button(b, `Open proposal ${row.id}:`); await settled(b);
      assert.equal(await field(b, "Statement row amount 2"), "0.20");
      assert.equal(await field(b, "Book row date 1"), "2028-03-01");
      assert.equal(await formula(b, "unmatched-statement-ids"), "S2, S3");
      other = await browser(width);
      await button(other, `Open proposal ${row.id}:`); await settled(other);
      await fill(b, "Statement balance", "101.30");
      await button(b, "Review proposal");
      await button(b, "Save proposal revision"); await settled(b);
      await b.wait('document.querySelector(".rec-editor > legend").textContent.includes("revision 2")');
      await fill(other, "Statement balance", "777.77");
      await fill(other, "Reconciliation explanation", "Retain my conflicting review");
      await button(other, "Review proposal");
      await button(other, "Save proposal revision"); await settled(other);
      await other.wait('document.body.textContent.includes("Saved revision changed")');
      assert.equal(await field(other, "Statement balance"), "777.77");
      assert.equal(await field(other, "Reconciliation explanation"), "Retain my conflicting review");
      const dialogs = other.dialogs.length;
      const click = button(other, "Reload saved proposal");
      const deadline = Date.now() + 5000;
      while (other.dialogs.length === dialogs && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 20));
      assert.equal(other.dialogs.at(-1)?.type, "confirm");
      await other.handleDialog(true); await click; await settled(other);
      assert.equal(await field(other, "Statement balance"), "101.30");
      const updated = await other.evaluate(`fetch("/lab/reconciliations/${row.id}/").then(r=>r.json())`);
      assert.equal(updated.revision, 2);
      assert.deepEqual(updated.statement_rows, saved.statement_rows);
      assert.deepEqual(updated.book_rows, saved.book_rows);
      assert.deepEqual(updated.matches, saved.matches);
      assert.deepEqual(updated.history.map(h=>h.snapshot.review.comparison_residual), ["0.03", "1.03"]);
      // A direct invalid edit cannot bypass validation or create another revision.
      const rejected = await other.evaluate(`(async()=>{
        const current=await(await fetch("/lab/reconciliations/${row.id}/")).json();
        const keys=["name","currency","statement_date","statement_balance","book_date","book_balance","explanation","statement_rows","book_rows","matches"];
        const payload=Object.fromEntries(keys.map(key=>[key,current[key]]));
        payload.expected_revision=current.revision; payload.matches.push({...payload.matches[0]});
        const token=(await(await fetch("/lab/session/")).json()).csrf_token;
        const response=await fetch("/lab/reconciliations/${row.id}/",{method:"PUT",headers:{"Content-Type":"application/json","X-CSRFToken":token},body:JSON.stringify(payload)});
        return {status:response.status,body:await response.json()};
      })()`);
      assert.equal(rejected.status, 400);
      assert.match(rejected.body.detail, /matched twice/);
      assert.deepEqual(await other.evaluate(`fetch("/lab/reconciliations/${row.id}/").then(r=>r.json())`), updated);
      const geometry = await other.evaluate(`({
        client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,
        minimumField:Math.min(...[...document.querySelectorAll('.rec-editor input,.rec-editor select,.rec-editor textarea')]
          .map(el=>el.getBoundingClientRect().width))
      })`);
      assert.ok(geometry.scroll <= geometry.client, JSON.stringify(geometry));
      assert.ok(geometry.minimumField >= 160, JSON.stringify(geometry));
      console.log(`Synthetic reconciliation geometry at ${width}px: ${JSON.stringify(geometry)}`);
      await other.evaluate('document.querySelector(\'[aria-label="Statement balance"]\').scrollIntoView({block:"center"})');
      await other.screenshot(`reconciliation-${width}`, "finance-reconciliation-real");
      b.assertSafe(); other.assertSafe();
      assert.equal(b.calls.filter(c=>c.method==="POST"&&c.path==="/lab/reconciliations/").length, 1);
      assert.equal(b.calls.filter(c=>c.method==="PUT").length, 1);
      assert.equal(other.calls.filter(c=>c.method==="PUT").length, 2);
    } finally { if (other) await other.close(); await b.close(); }
  });
}
