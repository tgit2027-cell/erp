import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { browserHarness } from "./browser-harness.mjs";

const origin = process.env.FINANCE_LAB_ORIGIN;
if (origin !== "http://127.0.0.1:18069" ||
    !process.env.EXPECTED_DISPOSABLE_DATA?.startsWith("/tmp/tivoli-finance-lab.")) {
  throw new Error("Use only the verified disposable Finance facility.");
}
const assetDirectory = process.env.FINANCE_LOADING_ASSETS || "generated-artifacts/finance-editor-load/after";
const evidenceDirectory = process.env.FINANCE_LOADING_EVIDENCE || "finance-editor-load";
const manifest = JSON.parse(await readFile(`${assetDirectory}/.vite/manifest.json`, "utf8"));
const routes = [
  ["/lab-review-ui/", "FinanceLabReview", "Saved work,"],
  ["/lab-ui/", "FinanceDraftLab", "Disposable Finance draft lab"],
  ["/lab-allocation-ui/", "FinanceAllocationLab", "Allocation proposals"],
  ["/lab-budget-ui/", "FinanceBudgetLab", "Budget scenarios"],
  ["/lab-reconciliation-ui/", "FinanceReconciliationLab", "Reconciliation proposals"],
  ["/lab-expense-ui/", "FinanceExpenseLab", "Expense proposals"],
  ["/lab-assets-ui/", "FinanceAssetLab", "Fixed-asset records"],
  ["/lab-forecasts-ui/", "FinanceForecastLab", "Cash-flow scenarios"],
  ["/lab-invoices-ui/", "FinanceInvoiceLab", "Supplier invoices"],
  ["/lab-customer-invoices-ui/", "FinanceCustomerInvoiceLab", "Customer invoices"],
  ["/lab-adjustments-ui/", "FinanceAdjustmentLab", "Supplier adjustments"],
  ["/lab-customer-credits-ui/", "FinanceCustomerCreditLab", "Customer credit proposals"],
  ["/lab-reversals-ui/", "FinanceReversalLab", "Journal reversal proposals"],
];
const documents = [...routes.map(([path]) => path), "/lab-supplier-advances-ui/"];
function allowedFiles(key, seen = new Set(), files = new Set()) {
  if (seen.has(key)) return files;
  seen.add(key);
  const chunk = manifest[key];
  assert.ok(chunk, key);
  files.add(`/${chunk.file}`);
  for (const css of chunk.css ?? []) files.add(`/${css}`);
  for (const dependency of chunk.imports ?? []) allowedFiles(dependency, seen, files);
  return files;
}
async function assertSelectedAssets(b, module) {
  const key = `../../src/${module}.tsx`;
  const permitted = allowedFiles(key);
  const observed = await b.evaluate(`performance.getEntriesByType("resource").map(r=>new URL(r.name).pathname).filter(p=>p.startsWith("/assets/")&&(p.endsWith(".js")||p.endsWith(".css")))`);
  assert.ok(observed.includes(`/${manifest[key].file}`), `Selected module fetched: ${module}`);
  assert.ok(observed.every((file) => permitted.has(file)), JSON.stringify({ module, observed, allowed: [...permitted] }));
}
for (const width of [1280, 390]) {
  test(`selected Finance modules, delayed failure/retry and real saved reopening at ${width}px`, async () => {
    let armed = false, blocked = false, release;
    const b = await browserHarness({
      width, assetDirectory,
      realBackendOrigin: origin, apiPrefix: "/lab/", entryPath: "/lab-review-ui/",
      documentPaths: documents,
      readyExpression: '!!document.querySelector(\'[aria-label="Finance lab navigation"]\')',
      allowedRealWrites: ["POST /lab/session/", "POST /lab/accounts/", "POST /lab/supplier-advances/"],
      beforeAssetResponse: async (path) => {
        if (armed && path === `/${manifest["../../src/FinanceSupplierAdvanceLab.tsx"].file}`) {
          armed = false; blocked = true;
          return new Promise((resolve) => { release = () => resolve({ status: 503 }); });
        }
      },
    });
    try {
      // Finish anonymous boot responses before fixture login/CSRF rotation.
      await b.wait('!!document.querySelector(".lab-review-message.is-forbidden")');
      const saved = await b.evaluate(`(async()=>{
        let session=await(await fetch("/lab/session/")).json();
        const post=async(path,body,id)=>{
          const r=await fetch("/lab/"+path+"/",{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":session.csrf_token,...(id?{"X-Finance-Request-ID":id}:{})},body:JSON.stringify(body)});
          if(!r.ok)throw new Error("Synthetic setup rejected "+r.status);
          return r.json();
        };
        await post("session",{});
        // Django login rotates CSRF; use the new session's token.
        session=await(await fetch("/lab/session/")).json();
        const account=await post("accounts",{code:"TEST-DRAFT-ACCT-LOAD${width}",name_en:"Explicit load-test account"});
        const body={supplier_reference:"TEST-SUPPLIER-LOAD${width}",document_reference:"TEST-LOAD${width}",proposed_date:"2028-02-29",currency:"QAR",amount:"0.30",purpose:"Explicit synthetic review",account_id:account.id,business_unit_id:null,dimension_value_ids:[]};
        const identity=crypto.randomUUID(), saved=await post("supplier-advances",body,identity);
        const canonical=value=>Array.isArray(value)?"["+value.map(canonical).join(",")+"]":value&&typeof value==="object"?"{"+Object.keys(value).sort().map(k=>JSON.stringify(k)+":"+canonical(value[k])).join(",")+"}":JSON.stringify(value);
        const bytes=new TextEncoder().encode("supplier-advances\\n"+canonical(body));
        const fingerprint=[...new Uint8Array(await crypto.subtle.digest("SHA-256",bytes))].map(v=>v.toString(16).padStart(2,"0")).join("");
        return {...saved,requestIdentity:identity,recoveryScope:session.recovery_scope,fingerprint};
      })()`);
      for (const [path, module, heading] of routes) {
        await b.click(`nav a[href="${path}"]`);
        await b.wait(`document.querySelector('h1')?.textContent.startsWith(${JSON.stringify(heading)})`);
        assert.equal(await b.evaluate(`document.querySelectorAll('nav[aria-label="Finance lab navigation"]').length`), 1);
        assert.equal(await b.evaluate(`document.querySelector('nav a[aria-current="page"]')?.getAttribute("href")`), path);
        await assertSelectedAssets(b, module);
      }
      // The register's real saved link retains its ?reopen= query through
      // delayed/failing module transport and subsequent explicit recovery.
      await b.click('nav a[href="/lab-review-ui/"]');
      await b.wait(`!!document.querySelector('a[href="/lab-supplier-advances-ui/?reopen=${saved.id}"]')`);
      const beforeFilterRequests = b.calls.length;
      await b.click('[aria-label="Search saved drafts"]');
      await b.text("NO-SYNTHETIC-MATCH");
      await b.wait('document.body.textContent.includes("No saved drafts match these filters")');
      assert.equal(await b.evaluate('document.querySelectorAll(".lab-review-reopen").length'), 0);
      await b.click(".lab-review-filters button");
      await b.click('[aria-label="Search saved drafts"]');
      await b.text(`#${saved.id}`);
      await b.wait(`document.querySelector('a[href="/lab-supplier-advances-ui/?reopen=${saved.id}"]')!==null`);
      await b.evaluate(`const select=document.querySelector('[aria-label="Draft type"]');select.value="supplier-advances";select.dispatchEvent(new Event("change",{bubbles:true}));`);
      await b.wait('document.querySelectorAll(".lab-review-group").length===1');
      assert.equal(await b.evaluate('document.querySelectorAll(".lab-review-reopen").length'), 1);
      await b.click('[aria-label="Search saved drafts"]');
      assert.ok(parseFloat(await b.evaluate('getComputedStyle(document.querySelector(\'[aria-label="Search saved drafts"]\')).outlineWidth')) >= 2);
      assert.equal(b.calls.length, beforeFilterRequests, "Local filters make no API request.");
      const filterGeometry = await b.evaluate('({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})');
      assert.ok(filterGeometry.scroll <= filterGeometry.client);
      await b.screenshot(`review-finder-${width}`, evidenceDirectory);
      await b.key("Tab");
      assert.equal(await b.evaluate('document.activeElement===document.querySelector(".lab-review-filters button")'), true);
      await b.key("Enter");
      await b.wait('document.querySelector(\'[aria-label="Search saved drafts"]\').value===""');
      // Browser-only metadata fixture for the real committed request above.
      // No payload is persisted, no acknowledgement or business API is mocked.
      const metadataKey = `tivoli-finance-create:${saved.recoveryScope}`;
      const metadata = JSON.stringify([{ id: saved.requestIdentity, path: "supplier-advances", fingerprint: saved.fingerprint }]);
      await b.evaluate(`sessionStorage.setItem(${JSON.stringify(metadataKey)},${JSON.stringify(metadata)})`);
      armed = true;
      await b.click(`a[href="/lab-supplier-advances-ui/?reopen=${saved.id}"]`);
      await b.wait('document.querySelector(\'[role="status"][aria-busy="true"]\')?.textContent.includes("supplier advance editor")');
      assert.equal(blocked, true);
      assert.ok(await b.evaluate(`!!document.querySelector('nav a[href="/lab-review-ui/"]')`));
      release();
      await b.wait('document.querySelector(\'[role="alert"]\')?.textContent.includes("could not be loaded")');
      assert.equal(await b.evaluate(`sessionStorage.getItem(${JSON.stringify(metadataKey)})`), metadata);
      assert.equal(b.calls.filter((call) => call.method === "POST").length, 3);
      await b.evaluate('[...document.querySelectorAll("button")].find(b=>b.textContent==="Retry loading view").click()');
      await b.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
      let retry = "in-place import retry";
      if (await b.evaluate('!!document.querySelector(\'[role="alert"]\')')) {
        retry = "explicit page reload after browser-cached module failure";
        await b.evaluate('[...document.querySelectorAll("button")].find(b=>b.textContent==="Reload this page").click()');
      }
      await b.wait(`document.querySelector('[aria-label="Document reference"]')?.value==="TEST-LOAD${width}"`);
      await b.wait('document.querySelector(".lab-recovery")?.textContent.includes("Outcome unknown")');
      assert.equal(await b.evaluate(`sessionStorage.getItem(${JSON.stringify(metadataKey)})`), metadata);
      assert.equal(await b.evaluate('[...document.querySelectorAll(".lab-recovery button")].find(b=>b.textContent==="Retry original request").disabled'), true);
      await b.evaluate('[...document.querySelectorAll(".lab-recovery button")].find(b=>b.textContent==="Check request status").click()');
      await b.wait('document.querySelector(".lab-recovery")?.textContent.includes("Save acknowledged")');
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Amount"]\').value'), "0.30");
      await assertSelectedAssets(b, "FinanceSupplierAdvanceLab");
      await b.reload();
      await b.wait(`document.querySelector('[aria-label="Document reference"]')?.value==="TEST-LOAD${width}"`);
      const denied = await b.evaluate(`fetch("/lab/supplier-advances/${saved.id}/",{credentials:"omit"}).then(async r=>({status:r.status,body:await r.json()}))`);
      assert.equal(denied.status, 403);
      assert.deepEqual(Object.keys(denied.body), ["detail"]);
      const geometry = await b.evaluate('({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})');
      assert.ok(geometry.scroll <= geometry.client, JSON.stringify(geometry));
      assert.equal(b.calls.filter((call) => call.method === "POST").length, 3);
      console.log(`${width}px:14 selected destinations; local type/exact-ID/no-match/clear filters with focus, no network or overflow; ${retry}; original saved query/value and recovery metadata retained, actual status lookup; geometry=${JSON.stringify(geometry)}; exactly3 setup POSTs, no UI financial POSTs.`);
      await b.screenshot(`selected-editor-${width}`, evidenceDirectory);
      b.assertSafe();
    } finally { release?.(); await b.close(); }
  });
}
