import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, Profiler } from "react";
import { createRoot } from "react-dom/client";
import PurchasingWorkbench from "../src/PurchasingWorkbench";
import { sidebarSections } from "../src/sidebar-destinations";

let root, host, calls, commits, opened, respond;
const nativeFetch = globalThis.fetch;
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
function quote(id = 101, unit = 1) {
  return {
    quote_id: id, business_unit_id: unit, status: "ready", kind: "stock",
    lines: ["1.250", "900719925474.75"].map((quantity, index) => ({
      quote_line_id: id * 10 + index, variant_id: index + 7, quantity,
      specifications: { finish: `Synthetic linen ${index}` },
      preparation_identity: index ? null : {
        item_code: "TEST-CHAIR", description: "Synthetic chair", sku: "TEST-LINEN",
        brand: "Synthetic brand", location_code: "TEST-WH", location_name: "Synthetic warehouse",
      },
    })),
    delivery_progress: { ready_at: "2031-04-03T10:00:00Z", delivered_at: null,
      lines: [0, 1].map(index => ({ quote_line_id: id * 10 + index, variant_id: index + 7,
        ordered_quantity: index ? "900719925474.75" : "1.250",
        delivered_quantity: "0.00", remaining_quantity: index ? "900719925474.75" : "1.250",
        reserved_to_date_quantity: "SECRET-HISTORICAL-COVERAGE",
      })),
    },
    unit_cost: "SECRET-COST",
  };
}
async function flush() { await act(async () => { await new Promise(setImmediate); }); }
async function render(unit = 1, focus = "preparation") {
  await act(async () => root.render(
    <Profiler id="preparation" onRender={() => commits.push({ unit, text: host.textContent })}>
      <PurchasingWorkbench mode="delivery" selectedUnitId={unit} selectedUnitName={`Synthetic Unit ${unit}`}
        isStaff={false} username="synthetic" csrfToken="synthetic" locations={[]}
        onOpenReports={() => {}} focusedView={focus} onNavigateFunction={child => opened.push(child)} />
    </Profiler>,
  ));
  await flush();
}
async function lookup(id) {
  await act(async () => {
    const input = host.querySelector("#pw-quote-id");
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, String(id));
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => host.querySelector(".pw-quote-form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();
}
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  calls = []; commits = []; opened = []; respond = (url) => json(quote(Number(url.pathname.split("/")[4]), Number(url.searchParams.get("unit"))));
  globalThis.fetch = (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, options });
    assert.equal(options.method ?? "GET", "GET", "Review and marks must never submit transactions");
    if (url.pathname.endsWith("/preparation/")) return Promise.resolve(respond(url));
    return Promise.resolve(json({ rows: [], results: [], pagination: { total_count: 0 } }));
  };
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); globalThis.fetch = nativeFetch; });

test("multiline review preserves exact strings and specs, masks other fields, and marks make no request", async () => {
  await render(); await lookup(101);
  assert.equal(host.querySelectorAll(".dp-line").length, 2);
  assert.match(host.textContent, /900719925474\.75/);
  assert.match(host.textContent, /Synthetic linen 1/);
  assert.match(host.textContent, /TEST-WH/);
  assert.match(host.textContent, /details unavailable under current access/);
  assert.doesNotMatch(host.textContent, /SECRET-COST|SECRET-HISTORICAL/);
  const before = calls.length;
  await act(async () => host.querySelector('.dp-review-check input').click());
  assert.equal(host.querySelector(".dp-review-check input").checked, true);
  assert.equal(calls.length, before);
  assert.match(host.textContent, /does not confirm physical picking/);
  assert.match(host.textContent, /Stored reservation coverage is historical/);
  await act(async () => host.querySelector(".dp-footer button").click());
  assert.deepEqual(opened, ["handover"]);
});

test("quote selection and same-BU fresh lookup reset review marks", async () => {
  await render(); await lookup(101);
  await act(async () => host.querySelector(".dp-review-check input").click());
  await lookup(303);
  assert.equal(host.querySelector(".dp-review-check input").checked, false);
  await act(async () => host.querySelector(".dp-review-check input").click());
  await lookup(303);
  assert.equal(host.querySelector(".dp-review-check input").checked, false);
});

test("first committed BU switch clears old identities and marks; delayed old success cannot restore them", async () => {
  await render(); await lookup(101);
  await act(async () => host.querySelector(".dp-review-check input").click());
  let finish;
  respond = () => new Promise(resolve => { finish = resolve; });
  await lookup(102);
  const start = commits.length;
  await render(2);
  assert.ok(commits.slice(start).every(commit => !commit.text.includes("TEST-CHAIR") && !commit.text.includes("Quote #101")));
  await act(async () => finish(json(quote(102, 1)))); await flush();
  assert.equal(host.querySelectorAll(".dp-line").length, 0);
  respond = () => json(quote(303, 2)); await lookup(303);
  assert.equal(host.querySelector(".dp-review-check input").checked, false);
  assert.match(host.textContent, /Business unit 2/);
});

test("current denial and revoked identity clear previous content without changing grants", async () => {
  await render(); await lookup(101);
  respond = () => json({ detail: "Current access denied" }, 403); await lookup(101);
  assert.match(host.querySelector('[role="alert"]').textContent, /Current access denied/);
  assert.equal(host.querySelectorAll(".dp-line").length, 0);
  respond = () => { const data = quote(); data.lines.forEach(line => { line.preparation_identity = null; }); return json(data); };
  await lookup(101);
  assert.doesNotMatch(host.textContent, /TEST-WH|TEST-CHAIR/);
  assert.equal(host.querySelectorAll(".dp-unavailable").length, 2);
});

test("obsolete denial cannot replace newer quote and new context uses explicit matching unit", async () => {
  await render();
  let finish;
  respond = () => new Promise(resolve => { finish = resolve; }); await lookup(101);
  respond = () => json(quote(303)); await lookup(303);
  await act(async () => finish(json({ detail: "Obsolete denial" }, 403))); await flush();
  assert.match(host.textContent, /Quote #303/);
  assert.doesNotMatch(host.textContent, /Obsolete denial/);
  const request = calls.filter(call => call.url.pathname.endsWith("/preparation/")).at(-1);
  assert.equal(request.url.searchParams.get("unit"), request.url.searchParams.get("business_unit_id"));
});

test("preparation has a distinct sidebar destination and empty state", async () => {
  assert.ok(sidebarSections({ isStaff: false, isSuperuser: false, canManageReportPanels: false })
    .find(section => section.id === "delivery").children.some(child => child.id === "preparation"));
  await render();
  assert.match(host.querySelector("h1").textContent, /Picking preparation/);
  assert.match(host.textContent, /No authorized quote in view/);
  assert.equal(calls.filter(call => call.url.pathname.endsWith("/preparation/")).length, 0);
});
