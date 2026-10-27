import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, Profiler } from "react";
import { createRoot } from "react-dom/client";
import PackingListPreview from "../src/PackingListPreview";

// All HTTP terminates here; no file, receipt or measurement is persisted.
const nativeFetch = globalThis.fetch;
let root, host, calls, reviewed, commits, responder, progress, invalidations;
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const candidate = (id = 10, code = "ITEM1", sku = "TEST-CHAIR") => ({
  variant_id: id, item_code: code, variant_sku: sku,
  variant_description: `TEST ONLY variant ${id}`, item_description: "TEST ONLY chair", brand_name: "TEST ONLY",
});
async function flush() { await act(async () => { await new Promise(setImmediate); }); }
async function render(unit = 1, enabled = true, generation = "current") {
  await act(async () => root.render(
    <Profiler id="preparation" onRender={() => commits.push(host.querySelector(".packing-preparation")?.textContent ?? "")}>
      <PackingListPreview selectedUnitId={unit} selectedUnitName={`TEST ONLY BU ${unit}`}
        preparation={{
          contextKey: `TEST_USER:${unit}:${generation}`, enabled, reason: "Existing receipt access is missing",
          commitments: progress,
          locations: [{ id: unit * 11, code: `LOC${unit}`, name: `TEST ONLY location ${unit}`, parent_id: null }],
          onReviewed: (draft) => reviewed.push(draft),
          onSourceChange: () => invalidations++,
        }} />
    </Profiler>,
  ));
  await flush();
}
const input = (label) => host.querySelector(`[aria-label="${label}"]`);
async function setValue(element, value) {
  assert.ok(element);
  const prototype = element.tagName === "SELECT" ? window.HTMLSelectElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, String(value));
    element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
  await flush();
}
async function upload(content = "Code,Description,Qty,Unit,Ref\nITEM1,TEST ONLY chair,2,EA,TEST-REF\n") {
  const bytes = new TextEncoder().encode(content);
  const file = { name: "TEST-ONLY.csv", size: bytes.length, arrayBuffer: async () => bytes.buffer };
  Object.defineProperty(input("Choose supplier packing list"), "files", { configurable: true, value: [file] });
  await act(async () => input("Choose supplier packing list").dispatchEvent(new Event("change", { bubbles: true })));
  await flush();
  for (const [label, column] of [
    ["Item description", 1], ["Quantity", 2], ["Quantity unit", 3],
    ["Supplier line reference", 4], ["Supplier / item code", 0],
  ]) await setValue(input(`Map ${label}`), column);
}
async function choose(row = 2, variant = 10, location = 11, quantity = "2.00") {
  await setValue(input(`Select authorized variant for source row ${row}`), variant);
  await setValue(input(`Existing receiving location for source row ${row}`), location);
  await setValue(input(`Warehouse-confirmed quantity for source row ${row}`), quantity);
}
function handoff() {
  return [...host.querySelectorAll("button")].find((button) => button.textContent === "Use reviewed lines in receipt draft");
}
async function acknowledge() {
  await act(async () => host.querySelector('.packing-preparation input[type="checkbox"]').click());
}
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host); calls = []; reviewed = []; commits = [];
  progress = undefined; invalidations = 0;
  responder = (url) => json({ candidates: url.searchParams.getAll("code").includes("ITEM1") ? [candidate()] : [] });
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, options });
    assert.equal(options.method ?? "GET", "GET");
    assert.equal(options.body, undefined, "No file, draft, measurement or costs are uploaded");
    if (url.pathname === "/api/stock-catalog-matches/") return json({ matched_codes: ["ITEM1"] });
    assert.equal(url.pathname, "/api/stock-receipt-candidates/");
    return responder(url);
  };
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); globalThis.fetch = nativeFetch;
});

test("explicit two-line variant mapping and quantity review prepare a local draft without posting", async () => {
  responder = (url) => json({ candidates: [
    candidate(), candidate(20, "ITEM1", "TEST-SECOND"),
    ...(url.searchParams.getAll("code").includes("CAT2") ? [candidate(30, "CAT2", "TEST-THIRD")] : []),
  ] });
  await render();
  await upload("Code,Description,Qty,Unit,Ref\nITEM1,TEST ONLY chair,2,EA,R1\nSUPPLIER-UNKNOWN,TEST ONLY table,3,EA,R2\n");
  assert.match(host.textContent, /Ambiguous/);
  assert.match(host.textContent, /No authorized variant found/);
  assert.equal(input("Select authorized variant for source row 2").value, "", "Never auto-select the first variant");
  assert.equal(handoff().disabled, true);
  await setValue(input("Exact catalog code or variant SKU for source row 3"), "CAT2");
  await choose(2, 20, 11, "1.25");
  await choose(3, 30, 11, "3.00");
  assert.equal(handoff().disabled, true, "Valid choices alone are not a review");
  await acknowledge();
  assert.equal(handoff().disabled, false);
  await act(async () => handoff().click());
  assert.equal(reviewed.length, 1);
  assert.deepEqual(reviewed[0].lines.map((line) => ({
    row: line.sourceRow, variant: line.variantId, location: line.locationId,
    quantity: line.quantity, unit: line.sourceUnit, ref: line.lineReference,
  })), [
    { row: 2, variant: 20, location: 11, quantity: "1.25", unit: "EA", ref: "R1" },
    { row: 3, variant: 30, location: 11, quantity: "3.00", unit: "EA", ref: "R2" },
  ]);
  assert.equal("unit_cost" in reviewed[0].lines[0], false);
  assert.ok(calls.every((call) => (call.options.method ?? "GET") === "GET"));
});

test("invalid rows, receipt precision and changed enrichment block or invalidate review", async () => {
  await render(); await upload(); await choose(2, 10, 11, "0");
  assert.equal(handoff().disabled, true);
  await setValue(input("Warehouse-confirmed quantity for source row 2"), "2.001");
  assert.equal(handoff().disabled, true);
  await setValue(input("Warehouse-confirmed quantity for source row 2"), "2.00");
  await acknowledge(); assert.equal(handoff().disabled, false);
  await setValue(input("Warehouse-confirmed quantity for source row 2"), "1.00");
  assert.equal(handoff().disabled, true, "Changed quantity needs a new explicit review");
  await setValue(input("Warehouse-confirmed volume in m³ for source row 2"), "-1");
  assert.equal(handoff().disabled, true);
  assert.match(host.textContent, /non-negative decimal in m³/);
  await upload("Code,Description,Qty,Unit,Ref\nITEM1,TEST ONLY chair,0,EA,R1\n");
  assert.match(host.textContent, /Quantity must be a positive decimal/);
  assert.equal(handoff().disabled, true);
});

test("a fresh denial clears candidate choices and never renders extra cost fields", async () => {
  await render(); await upload();
  responder = () => json({ detail: "TEST ONLY current access denied" }, 403);
  await render(1, true, "refresh");
  assert.match(host.textContent, /TEST ONLY current access denied/);
  assert.equal(input("Select authorized variant for source row 2").options.length, 1);
  assert.equal(handoff().disabled, true);
  responder = () => json({ candidates: [{ ...candidate(), supplier_cost: "SECRET-COST", landed_cost: "SECRET-LANDED" }] });
  await render(1, true, "next-refresh");
  assert.equal(host.textContent.includes("SECRET-COST"), false);
  assert.equal(host.textContent.includes("SECRET-LANDED"), false);
});

test("BU switch masks old mappings on the first commit and ignores a late former-unit lookup", async () => {
  await render(); await upload(); await choose(); await acknowledge();
  let resolveOld;
  responder = (url) => url.searchParams.get("unit") === "1"
    ? new Promise((resolve) => { resolveOld = resolve; })
    : json({ detail: "TEST ONLY unit B denied" }, 403);
  await setValue(input("Exact catalog code or variant SKU for source row 2"), "OLD-PENDING");
  const before = commits.length;
  await render(2);
  assert.equal(commits[before].includes("TEST-CHAIR"), false);
  assert.equal(commits[before].includes("LOC1"), false);
  await act(async () => resolveOld(json({ candidates: [candidate(99, "OLD-PENDING", "STALE-A")] })));
  await flush();
  assert.match(host.textContent, /unit B denied/);
  assert.equal(host.textContent.includes("STALE-A"), false);
  assert.equal(handoff().disabled, true);
  assert.equal(reviewed.length, 0);
});

test("disabled receiving handoff cannot fetch choices or retain a prior acknowledgement", async () => {
  await render(); await upload(); await choose(); await acknowledge();
  const before = calls.length;
  await render(1, false);
  assert.equal(calls.length, before);
  assert.match(host.textContent, /Existing receipt access is missing/);
  assert.equal(handoff().disabled, true);
  assert.equal(input("Select authorized variant for source row 2").value, "");
  await render(1, true);
  assert.equal(input("Select authorized variant for source row 2").value, "");
  assert.equal(handoff().disabled, true);
  assert.equal(reviewed.length, 0);
});

function commitmentLine(id = 501, commitment = 77, overrides = {}) {
  return {
    commitment_id: commitment, commitment_line_id: id, supplier_id: 1,
    variant_id: id === 501 ? 10 : 20, variant_sku: `TEST-COMMIT-${id}`,
    item_description: `TEST ONLY commitment item ${id}`,
    ordered_quantity: "5.00", received_to_date: "2.00", remaining_quantity: "3.00",
    ...overrides,
  };
}
async function commitmentMode(id = 77) {
  await setValue(input("Receiving mode"), "commitment");
  assert.equal(input("Existing authorized purchase commitment").value, "");
  if (id !== null) await setValue(input("Existing authorized purchase commitment"), id);
}
async function chooseCommitment(row = 2, id = 501, quantity = "2.00") {
  await setValue(input(`Select commitment line for source row ${row}`), id);
  await setValue(input(`Existing receiving location for source row ${row}`), 11);
  await setValue(input(`Warehouse-confirmed quantity for source row ${row}`), quantity);
}

test("explicit commitment and two line mappings preserve source units without lookup or cost exposure", async () => {
  progress = { state: "ready", error: "", lines: [
    { ...commitmentLine(), unit_cost: "SECRET-COST" }, commitmentLine(502),
    commitmentLine(601, 88),
  ] };
  await render();
  await upload("Code,Description,Qty,Unit,Ref\nSUPPLIER-X,TEST ONLY X,20,BOX,X\nSUPPLIER-Y,TEST ONLY Y,30,EA,Y\n");
  await commitmentMode();
  const before = calls.length;
  assert.equal(input("Select commitment line for source row 2").value, "", "No inferred line even when source code matches");
  await chooseCommitment(2, 501, "1.00");
  await chooseCommitment(3, 502, "2.50");
  assert.match(host.textContent, /Ordered 5.00 · Received 2.00 · Remaining 3.00/);
  assert.equal(input("Select commitment line for source row 2").querySelector('option[value="601"]'), null);
  assert.equal(host.textContent.includes("SECRET-COST"), false);
  assert.equal(handoff().disabled, true);
  await acknowledge(); await act(async () => handoff().click());
  assert.equal(calls.length, before, "Commitment matching only uses existing supplied progress, not the ledger/global catalog");
  assert.equal(reviewed.length, 1);
  assert.equal(reviewed[0].commitmentId, 77);
  assert.equal(reviewed[0].supplierId, 1);
  assert.deepEqual(reviewed[0].lines.map((line) => [
    line.commitmentLineId, line.variantId, line.quantity, line.sourceQuantity, line.sourceUnit,
  ]), [[501, 10, "1.00", "20", "BOX"], [502, 20, "2.50", "30", "EA"]]);
  assert.equal(JSON.stringify(reviewed[0]).includes("unit_cost"), false);
});

test("duplicate line mappings and over-remaining decimal quantities block the whole draft", async () => {
  progress = { state: "ready", error: "", lines: [commitmentLine(501), commitmentLine(502, 77, {
    ordered_quantity: "0.30", received_to_date: "0.00", remaining_quantity: "0.30",
  })] };
  await render(); await upload("Code,Description,Qty,Unit,Ref\nITEM1,X,2,EA,X\nITEM1,Y,3,EA,Y\n");
  await commitmentMode();
  await chooseCommitment(2, 501, "1.00");
  await chooseCommitment(3, 501, "1.00");
  await acknowledge();
  assert.equal(handoff().disabled, true);
  assert.match(host.textContent, /already mapped/);
  await chooseCommitment(3, 502, "0.31");
  await acknowledge();
  assert.equal(handoff().disabled, true);
  assert.match(host.textContent, /cannot exceed/);
  await setValue(input("Warehouse-confirmed quantity for source row 3"), "0.30");
  await acknowledge();
  assert.equal(handoff().disabled, false, "Exact decimal boundary accepted");
  assert.equal(reviewed.length, 0);
});

test("changing commitment or mode invalidates review and never defaults a missing line to direct", async () => {
  progress = { state: "ready", error: "", lines: [commitmentLine(), commitmentLine(601, 88)] };
  await render(); await upload(); await commitmentMode(); await chooseCommitment();
  await acknowledge(); await act(async () => handoff().click());
  const before = invalidations;
  await setValue(input("Existing authorized purchase commitment"), 88);
  assert.ok(invalidations > before);
  assert.equal(input("Select commitment line for source row 2").value, "");
  assert.equal(handoff().disabled, true);
  assert.equal(input("Exact catalog code or variant SKU for source row 2"), null);
  await setValue(input("Receiving mode"), "direct");
  assert.equal(input("Select authorized variant for source row 2").value, "");
  assert.equal(handoff().disabled, true);
  assert.equal(reviewed.length, 1);
});

test("progress loading denial empty and refresh require fresh explicit commitment review", async () => {
  progress = { state: "ready", error: "", lines: [commitmentLine()] };
  await render(); await upload(); await commitmentMode(); await chooseCommitment(); await acknowledge();
  for (const next of [
    { state: "loading", error: "", lines: [] },
    { state: "error", error: "TEST ONLY progress access denied", lines: [] },
    { state: "ready", error: "", lines: [] },
    { state: "ready", error: "", lines: [commitmentLine(501, 77, { remaining_quantity: "1.00", received_to_date: "4.00" })] },
  ]) {
    progress = next; await render(); await commitmentMode(null);
    assert.equal(handoff().disabled, true);
    assert.equal(input("Existing authorized purchase commitment").value, "");
    if (next.state === "error") assert.match(host.textContent, /progress access denied/);
    assert.equal(reviewed.length, 0);
  }
  await setValue(input("Existing authorized purchase commitment"), 77);
  await chooseCommitment(2, 501, "1.00"); await acknowledge();
  assert.equal(handoff().disabled, false);
});

test("commitment options and mappings do not survive the first render of another BU or disabled context", async () => {
  progress = { state: "ready", error: "", lines: [commitmentLine()] };
  await render(); await upload(); await commitmentMode(); await chooseCommitment(); await acknowledge();
  progress = { state: "error", error: "TEST ONLY B denied", lines: [] };
  const before = commits.length;
  await render(2);
  assert.equal(commits[before].includes("TEST-COMMIT-501"), false);
  await commitmentMode(null);
  assert.match(host.textContent, /B denied/);
  assert.equal(handoff().disabled, true);
  progress = { state: "ready", error: "", lines: [commitmentLine()] };
  await render(1, false);
  assert.equal(handoff().disabled, true);
  assert.equal(reviewed.length, 0);
});

test("a delayed direct lookup cannot replace an explicitly reviewed commitment mapping", async () => {
  progress = { state: "ready", error: "", lines: [commitmentLine()] };
  let resolveOld;
  responder = () => new Promise((resolve) => { resolveOld = resolve; });
  await render(); await upload();
  await commitmentMode(); await chooseCommitment(); await acknowledge();
  assert.equal(handoff().disabled, false);
  const oldResponse = resolveOld;
  await act(async () => oldResponse(json({ candidates: [candidate(99, "ITEM1", "STALE-DIRECT")] })));
  await flush();
  assert.equal(host.querySelector(".packing-preparation").textContent.includes("STALE-DIRECT"), false);
  assert.equal(input("Select commitment line for source row 2").value, "501");
  assert.equal(handoff().disabled, false);
  await act(async () => handoff().click());
  assert.equal(reviewed[0].lines[0].variantId, 10);
  assert.equal(reviewed[0].lines[0].commitmentLineId, 501);
});
