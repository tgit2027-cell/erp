// Actual file input + Chromium + Django session/CSRF/APIs + disposable PG16.15.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { browserHarness } from "./browser-harness.mjs";
import { openFunction, isVisible } from "./workspace-browser-navigation.mjs";
import { zipArchive } from "./synthetic-xlsx-fixture.mjs";

assert.ok(process.env.ACCEPTANCE_ROOT?.startsWith("/tmp/tivoli-procurement-acceptance."));
assert.equal(process.env.REAL_BACKEND_ORIGIN, "http://127.0.0.1:18062");
const root = process.env.ACCEPTANCE_ROOT;
const fixture = JSON.parse(await readFile(`${root}/browser-private.json`, "utf8"));
const output = "generated-artifacts/procurement-real-browser";
await mkdir(output, { recursive: true });
const header = ["Supplier part", "Description", "Quantity", "Unit", "Reference"];
const csv = `${header.join(",")}\nTVF-002-LIN,TEST ONLY linen chair,4,EA,CSV-A\nTVF-002-LIN-DEMO2,TEST ONLY alternate chair,2,EA,CSV-B\n`;
await writeFile(`${root}/TEST-ONLY-partial.csv`, csv);
await writeFile(`${output}/TEST-ONLY-partial.csv`, csv);
const ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
const rel = "http://schemas.openxmlformats.org/package/2006/relationships";
const sheets = [
  { name: "TEST ONLY Cover - do not receive", rows: [["Information"], ["Select TEST ONLY Receiving explicitly"]] },
  { name: "TEST ONLY Receiving", rows: [header,
    ["TVF-002-LIN", "TEST ONLY linen chair", "3", "EA", "XLSX-A"],
    ["TVF-002-LIN-DEMO2", "TEST ONLY alternate chair", "1", "EA", "XLSX-B"]] },
];
const escape = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;");
const entries = {
  "[Content_Types].xml": `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>${sheets.map((_, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join("")}</Types>`,
  "_rels/.rels": `<Relationships xmlns="${rel}"><Relationship Id="rId1" Type="${rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>`,
  "xl/workbook.xml": `<workbook xmlns="${ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${sheets.map((s, i) => `<sheet name="${s.name}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join("")}</sheets></workbook>`,
  "xl/_rels/workbook.xml.rels": `<Relationships xmlns="${rel}">${sheets.map((_, i) => `<Relationship Id="rId${i + 1}" Type="${rel}/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join("")}</Relationships>`,
};
sheets.forEach((sheet, i) => {
  entries[`xl/worksheets/sheet${i + 1}.xml`] = `<worksheet xmlns="${ns}"><sheetData>${sheet.rows.map((row, r) => `<row r="${r + 1}">${row.map((v, c) => `<c r="${String.fromCharCode(65 + c)}${r + 1}" t="inlineStr"><is><t>${escape(v)}</t></is></c>`).join("")}</row>`).join("")}</sheetData></worksheet>`;
});
const workbook = zipArchive(entries, 8);
await writeFile(`${root}/TEST-ONLY-partial.xlsx`, workbook);
await writeFile(`${output}/TEST-ONLY-partial.xlsx`, workbook);

function snapshot() {
  return JSON.parse(execFileSync("python", ["manage.py", "shell", "--no-imports", "-c",
    "import json; from scripts.procurement_acceptance_fixture import probe; print(json.dumps(probe()))"],
  { encoding: "utf8" }).trim());
}
async function login(b, user) {
  await b.click("#username"); await b.text(user);
  await b.click("#password"); await b.text(fixture.credentials[user]);
  await b.click("form button[type=submit]");
  await b.wait('!!document.querySelector(".sidebar") && !!document.querySelector(\'[aria-label="Business unit"]\')?.value');
  assert.equal(b.calls.find(c => c.path === "/api/auth/login/")?.status, 200);
}
async function choose(b, selector, value) {
  const index = await b.evaluate(`[...document.querySelector(${JSON.stringify(selector)}).options].findIndex(n=>n.value===${JSON.stringify(String(value))})`);
  assert.ok(index >= 0, `Option ${value} in ${selector}`);
  await b.click(selector); await b.key("Home");
  for (let n = 0; n < index; n++) await b.key("ArrowDown");
  await b.key("Enter");
  await b.wait(`document.querySelector(${JSON.stringify(selector)}).value===${JSON.stringify(String(value))}`);
}
const label = text => `[aria-label="${text}"]`;
async function api(b, path, payload, csrf = true) {
  return b.evaluate(`(async()=>{
    const token = (await (await fetch("/api/auth/csrf/")).json()).csrfToken;
    const headers={"Content-Type":"application/json"};
    if (${csrf}) headers["X-CSRFToken"]=token;
    const payload=${JSON.stringify(payload ?? null)};
    const response = await fetch(${JSON.stringify(path)}, payload
      ? {method:"POST",headers,body:JSON.stringify(payload)} : {});
    return {status:response.status,body:await response.json()};
  })()`);
}
async function reviewFile(b, extension, blocked = false) {
  await openFunction(b, "procurement", "packing");
  const before = snapshot();
  await b.uploadFile(label("Choose supplier packing list"), `${root}/TEST-ONLY-partial.${extension}`);
  await b.wait('!!document.querySelector(\'[aria-label="Map Quantity"]\')');
  if (extension === "xlsx") await choose(b, label("Packing list worksheet"), "1");
  for (const [name, col] of [
    ["Supplier / item code", 0], ["Item description", 1], ["Quantity", 2], ["Quantity unit", 3],
    ["Supplier line reference", 4],
  ]) await choose(b, label(`Map ${name}`), col);
  await choose(b, label("Receiving mode"), "commitment");
  await b.wait(`!!document.querySelector(${JSON.stringify(label("Existing authorized purchase commitment"))})?.querySelector('option[value="${fixture.commitment}"]')`);
  await choose(b, label("Existing authorized purchase commitment"), fixture.commitment);
  for (const [index, id] of fixture.lines.entries()) {
    await choose(b, label(`Select commitment line for source row ${index + 2}`), id);
    await choose(b, label(`Existing receiving location for source row ${index + 2}`), fixture.location);
    await b.click(label(`Warehouse-confirmed quantity for source row ${index + 2}`));
    await b.evaluate("document.activeElement.select()");
    await b.text((extension === "csv" ? ["4.00", "2.00"] : ["3.00", "1.00"])[index]);
  }
  const button = ".packing-preparation button.button-dark";
  if (blocked) {
    assert.equal(await b.evaluate(`document.querySelector(${JSON.stringify(button)}).disabled`), true);
    assert.match(await b.evaluate('document.querySelector(".packing-preparation").textContent'), /cannot exceed.*remaining/i);
    assert.deepEqual(snapshot(), before, "Over-receipt blocked during review without any writes");
    return before;
  }
  await b.click('.packing-preparation input[type=checkbox]');
  await b.wait(`!document.querySelector(${JSON.stringify(button)}).disabled`);
  await b.click(button);
  assert.deepEqual(snapshot(), before, "Actual file upload/mapping/review/draft preparation writes no business facts");
  await openFunction(b, "procurement", "receipt");
  const draft = '[aria-label="Reviewed packing-list receipt draft"]';
  await b.wait(`!!document.querySelector(${JSON.stringify(draft)})`);
  assert.match(await b.evaluate(`document.querySelector(${JSON.stringify(draft)}).textContent`), /Source:.*EA/s);
  assert.equal(await b.evaluate(`document.querySelectorAll(${JSON.stringify(`${draft} input[type=number]`)}).length`), 0,
    "Immutable commitment values used without exposing/editing supplier cost");
  return before;
}
const receiptPath = "/api/purchasing/receipts/";
const sensitive = /unit_cost|purchase_value|eligible_landed_charges|landed_cost_allocation|unit_landed_cost/;
async function verifyProgressScreen(b, expected) {
  await openFunction(b, "procurement", "progress");
  const rows = `[...document.querySelectorAll('[aria-labelledby="pw-commitment-progress-title"] tbody tr')]
    .map(row=>[...row.querySelectorAll("td")].slice(2).map(cell=>cell.textContent))`;
  await b.wait(`JSON.stringify(${rows})===${JSON.stringify(JSON.stringify(expected))}`);
  assert.deepEqual(await b.evaluate(rows), expected, "Actual workbench shows each line's ordered/received/remaining quantities");
  assert.equal(await isVisible(b, '[aria-labelledby="pw-commitment-create-title"]'), false,
    "Progress must not expose the hidden commitment-creation form");
  assert.equal(await isVisible(b, 'form:has(#pw-receipt-supplier)'), false,
    "Progress must not expose the hidden receipt-submission form");
  assert.equal(await b.evaluate(`(() => {
    const grid=document.querySelector(".pw-transaction-grid").getBoundingClientRect();
    const panel=document.querySelector('[aria-labelledby="pw-commitment-progress-title"]')
      .closest(".pw-transaction-panel").getBoundingClientRect();
    return panel.width >= grid.width * .9;
  })()`), true, "A focused progress page uses the available desktop width");
}

test("real CSV/XLSX upload → explicit commitment review → two partial receipts; lost-ack UI retry and atomic denials", async () => {
  let dropFirstAcknowledgement = true;
  const b = await browserHarness({
    realBackendOrigin: process.env.REAL_BACKEND_ORIGIN,
    allowedRealWrites: ["POST /api/auth/login/", `POST ${receiptPath}`, `POST /api/units/${fixture.unit}/select/`],
    beforeRealResponse: async call => {
      if (call.method === "POST" && call.path === receiptPath && call.status === 201 && dropFirstAcknowledgement) {
        dropFirstAcknowledgement = false;
        throw new Error("TEST ONLY lost acknowledgement after actual backend commit");
      }
    },
  });
  try {
    await login(b, "demo_multi");
    if (await b.evaluate('document.querySelector(\'[aria-label="Business unit"]\').value') !== String(fixture.unit)) {
      await choose(b, '[aria-label="Business unit"]', fixture.unit);
    }
    // Observe only receipt payload + idempotency identity. Never capture
    // authentication headers, CSRF, cookies or login bodies.
    await b.evaluate(`window.receiptBodies=[]; const actualFetch=window.fetch.bind(window);
      window.fetch=(url,init)=>{if(String(url)==="${receiptPath}" && init?.method==="POST") {
        const payload=JSON.parse(init.body);
        window.receiptBodies.push({...payload,idempotency_key:
          new Headers(init.headers).get("Idempotency-Key") ?? payload.idempotency_key});
      } return actualFetch(url,init);};`);
    const original = await reviewFile(b, "csv");
    assert.equal(original.receipts, 0);
    assert.equal(b.calls.filter(c => c.method === "POST" && c.path === receiptPath).length, 0);
    assert.deepEqual(original.progress, [["10.00", "0.00", "10.00"], ["5.00", "0.00", "5.00"]]);
    const submit = 'form:has(#pw-receipt-supplier) button[type=submit]';
    await b.click(submit);
    await b.wait(`document.querySelector(${JSON.stringify(submit)})?.disabled===false &&
      window.receiptBodies.length===1 && document.body.textContent.includes("502")`);
    const accepted = snapshot();
    assert.equal(accepted.receipts, 1, "Backend committed despite lost acknowledgement");
    await b.click(submit);
    await b.wait('document.body.textContent.includes("Synthetic goods receipt recorded.")');
    assert.deepEqual(snapshot(), accepted, "UI retry replays the original result without any duplicate/audit writes");
    const bodies = await b.evaluate("window.receiptBodies");
    assert.equal(bodies.length, 2);
    assert.equal(typeof bodies[0].idempotency_key, "string", "Actual Idempotency-Key header observed");
    assert.deepEqual(bodies[0], bodies[1], "Exact payload and idempotency key survive acknowledgement failure");
    assert.deepEqual(accepted.progress, [["10.00", "4.00", "6.00"], ["5.00", "2.00", "3.00"]]);
    await verifyProgressScreen(b, accepted.progress);
    await reviewFile(b, "xlsx");
    await b.click(submit);
    await b.wait('document.body.textContent.includes("Synthetic goods receipt recorded.") && !document.querySelector(\'form:has(#pw-receipt-supplier) button[type=submit]\').disabled');
    const after = snapshot();
    assert.equal(after.receipts, 2);
    assert.equal(after.movements, 4);
    assert.deepEqual(after.progress, [["10.00", "7.00", "3.00"], ["5.00", "3.00", "2.00"]]);
    assert.deepEqual(after.balances, fixture.variants.map((id, index) => [id, ["7.00", "3.00"][index], "0.00"]));
    const request = await b.evaluate("window.receiptBodies.at(-1)");
    await reviewFile(b, "csv", true); // Source asks 4 when only 3 remain.
    const over = structuredClone(request);
    over.idempotency_key = "TEST-ONLY-browser-overreceipt";
    over.lines[0].quantity = "1.00"; over.lines[1].quantity = "3.00";
    assert.equal((await api(b, receiptPath, over)).status, 400);
    assert.deepEqual(snapshot(), after, "One over-received line leaves ALL business/audit/idempotency records unchanged");
    const forbidden = structuredClone(over);
    forbidden.idempotency_key = "TEST-ONLY-browser-wrong-location";
    forbidden.lines.forEach(line => { line.quantity = "1.00"; line.location_id = fixture.forbidden_location; });
    assert.equal((await api(b, receiptPath, forbidden)).status, 400,
      "Existing receipt API rejects a location outside the selected BU as invalid input");
    assert.deepEqual(snapshot(), after);
    forbidden.idempotency_key = "TEST-ONLY-browser-wrong-unit";
    forbidden.business_unit_id = fixture.roche;
    assert.equal((await api(b, receiptPath, forbidden)).status, 404,
      "A TVL commitment is not visible under an RBB receipt context");
    assert.deepEqual(snapshot(), after);
    assert.equal((await api(b, receiptPath, over, false)).status, 403);
    assert.deepEqual(snapshot(), after);
    const conflict = structuredClone(request);
    conflict.lines[0].quantity = "1.00";
    const conflicting = await api(b, receiptPath, conflict);
    assert.equal(conflicting.status, 409, JSON.stringify(conflicting.body));
    assert.deepEqual(snapshot(), after);
    const receipts = await api(b, `/api/reports/purchase_history/receipts/?unit=${fixture.unit}`);
    assert.equal(receipts.status, 200);
    assert.equal(receipts.body.pagination.total_count, 2);
    assert.equal(sensitive.test(JSON.stringify(receipts.body)), false, "No sensitive cost grant: register costs masked");
    assert.equal((await api(b, `/api/reports/purchase_history/receipts/?unit=${fixture.unit}&location=${fixture.forbidden_location}`)).status, 403);
    assert.deepEqual(snapshot(), after, "Read registers and denied reads do not mutate business/audit data");
    await verifyProgressScreen(b, after.progress);
    await b.screenshot("partial-receipt-progress", "procurement-real-browser");
    b.assertSafe();
    await writeFile(`${output}/verified.json`, JSON.stringify({
      scope: "Actual Chromium file inputs and Django session/CSRF/APIs, isolated PostgreSQL 16.15",
      csv: [4, 2], xlsx: [3, 1], receipts: after.receipts, movements: after.movements,
      progress: after.progress, unchangedRetry: true, unchangedOverreceipt: true,
      requests: b.calls, // No bodies, cookies, credentials or CSRF tokens.
    }, null, 2));
  } catch (error) {
    await b.screenshot("acceptance-failure", "procurement-real-browser");
    console.error("Synthetic rendered failure context:", await b.evaluate("document.body.innerText.slice(-4500)"));
    throw error;
  } finally { await b.close(); }
});

test("real brand-only account: no order authority, no cross-BU commitment/receipt access and no cost leak", async () => {
  const b = await browserHarness({ realBackendOrigin: process.env.REAL_BACKEND_ORIGIN,
    allowedRealWrites: ["POST /api/auth/login/", `POST ${receiptPath}`] });
  try {
    await login(b, "demo_roche");
    await openFunction(b, "procurement", "packing");
    await b.wait('document.body.textContent.includes("Transactions unavailable")');
    assert.equal(await b.evaluate('!!document.querySelector(".packing-preparation button:not(:disabled)")'), false);
    const before = snapshot();
    assert.equal((await api(b, `/api/purchasing/commitments/?business_unit_id=${fixture.unit}`)).status, 404);
    assert.equal((await api(b, `/api/reports/purchase_history/receipts/?unit=${fixture.unit}`)).status, 403);
    const denied = await api(b, receiptPath, {
      business_unit_id: fixture.unit, supplier_id: fixture.supplier, purchase_commitment_id: fixture.commitment,
      idempotency_key: "TEST-ONLY-browser-brand-denied",
      lines: [{ commitment_line_id: fixture.lines[0], variant_id: fixture.variants[0], location_id: fixture.location, quantity: "1.00" }],
      charges: [],
    });
    assert.equal(denied.status, 403);
    assert.equal(sensitive.test(JSON.stringify(denied.body)), false);
    assert.deepEqual(snapshot(), before);
    b.assertSafe();
  } finally { await b.close(); }
});
