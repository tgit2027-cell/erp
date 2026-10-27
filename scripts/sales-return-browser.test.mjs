import assert from "node:assert/strict";
import { test } from "node:test";
import { browserHarness } from "./browser-harness.mjs";
import { openFunction } from "./workspace-browser-navigation.mjs";

const area = ".pw-sales-return";
const inspection = ".pw-return-inspection";
const intake = `${area} .pw-return-block form`;
const localDate = () => new Date().toLocaleDateString("en-CA");
const windowFor = (date) => ({
  date_basis: "delivery", calendar_days: 14, delivery_date: date,
  deadline: new Date(Date.parse(`${date}T00:00:00Z`) + 14 * 86400000).toISOString().slice(0, 10),
  request_date: date, eligible: true, message: "TEST ONLY eligible assessment",
});
const line = {
  quote_line_id: 501, variant_id: 91, item_id: "TEST-CHAIR", description: "TEST ONLY chair",
  brand: "TEST ONLY brand", quantity: "1.000", condition: "uninspected", inspection_notes: "",
};
const customer = { id: 28, name: "TEST ONLY synthetic customer" };
const location = { id: 71, code: "TEST-SH", name: "TEST ONLY showroom" };
const policy = { calendar_days: 14, date_basis: "delivery", status: "owner_confirmed_pending_legal_validation" };

function fixture() {
  const posts = [];
  let inspected = false;
  let rejectInspection = true;
  const record = () => ({
    id: 19, quote_id: 431, customer, request_date: localDate(),
    business_unit: { id: 1, code: "TVL", name: "TEST ONLY Tivoli" }, location,
    status: inspected ? "finance_pending" : "awaiting_inspection",
    receiving_mode: "showroom", proposed_refund_amount: "10.00", currency: "QAR",
    lines: [{ ...line, condition: inspected ? "good" : "uninspected" }],
    delivery_window: windowFor(localDate()), policy, events: [],
  });
  return {
    posts, allowInspection() { rejectInspection = false; },
    async mockApi(req, url) {
      if (!url.pathname.startsWith("/api/purchasing/returns/")) return null;
      if (req.method === "GET" && url.searchParams.get("business_unit_id") !== "1") {
        return { status: 403, data: { detail: "TEST ONLY scope denied" } };
      }
      if (req.method === "GET" && url.pathname === "/api/purchasing/returns/") {
        return { data: { rows: [record()], policy, pagination: {
          page: 1, page_size: 10, total_count: 1, total_pages: 1, next_page: null, previous_page: null,
        } } };
      }
      if (req.method === "GET" && url.pathname === "/api/purchasing/returns/sales/431/") {
        return { data: { quote_id: 431, customer, lines: [line], locations: [location],
          delivery_window: windowFor(url.searchParams.get("request_date") || localDate()) } };
      }
      if (req.method === "POST" && ["/api/purchasing/returns/", "/api/purchasing/returns/19/inspection/"].includes(url.pathname)) {
        let raw = "";
        for await (const chunk of req) raw += chunk;
        posts.push({ path: url.pathname, body: JSON.parse(raw), key: req.headers["idempotency-key"] });
        if (url.pathname.endsWith("/inspection/") && !rejectInspection) {
          inspected = true;
          return { data: record() };
        }
        return { status: 400, data: { detail: "TEST ONLY validation feedback; nothing posted" } };
      }
      // Fall through to the harness's unexpected-request rejection, never proxy.
      return null;
    },
  };
}

async function run(options, callback) {
  const f = fixture();
  const b = await browserHarness({ ...options, mockApi: f.mockApi,
    mockPosts: ["/api/purchasing/returns/", "/api/purchasing/returns/19/inspection/"] });
  try {
    await openFunction(b, "delivery", "returns");
    if ((options.width ?? 1280) < 760) {
      await b.click('[title="Delivery & After-Sales"].nav-section-toggle');
    }
    await b.wait('!!document.querySelector(".pw-return-table")');
    await callback(b, f);
    b.assertSafe();
  } finally { await b.close(); }
}

const active = (selector) => `document.activeElement === document.querySelector(${JSON.stringify(selector)})`;
async function focus(b, selector) {
  await b.evaluate(`document.querySelector(${JSON.stringify(selector)}).focus()`);
}
async function set(b, selector, value) {
  await focus(b, selector);
  await b.text(value);
}
async function selectFirst(b, selector) {
  await focus(b, selector); await b.key(" "); await b.key("ArrowDown"); await b.key("Enter");
}
async function loadSale(b) {
  await set(b, "#pw-return-quote", "431");
  await b.wait(`!!document.querySelector("#pw-return-qty-501")`);
  await b.wait(`${JSON.stringify("Within delivery window.")} && document.querySelector(${JSON.stringify(area)})?.textContent.includes("Within delivery window.")`);
}
async function assertFocus(b, selector) {
  assert.equal(await b.evaluate(active(selector)), true, `Native tab order: ${selector}; actual ${await b.evaluate('document.activeElement.outerHTML.slice(0,400)')}`);
  assert.equal(await b.evaluate(`(() => {
    const s=getComputedStyle(document.activeElement);
    return document.activeElement.matches(":focus-visible") && s.outlineStyle !== "none" &&
      parseFloat(s.outlineWidth)>=2;
  })()`), true, `Visible keyboard focus: ${selector}`);
  assert.ok(await b.evaluate(`(() => {
    const lum=c=>{
      const rgb=c.match(/[\\d.]+/g).slice(0,3).map(Number).map(v=>{
        const n=v/255;return n<=.04045?n/12.92:((n+.055)/1.055)**2.4;
      });return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;
    };
    let p=document.activeElement.parentElement;
    while(p.parentElement && getComputedStyle(p).backgroundColor==="rgba(0, 0, 0, 0)") p=p.parentElement;
    const a=lum(getComputedStyle(document.activeElement).outlineColor),c=lum(getComputedStyle(p).backgroundColor);
    return (Math.max(a,c)+.05)/(Math.min(a,c)+.05);
  })()`) >= 3, `Visible focus contrast >= 3:1: ${selector}`);
}
async function assertLabels(b, selector) {
  const controls = await b.evaluate(`[...document.querySelectorAll(${JSON.stringify(selector + " input," + selector + " select," + selector + " textarea")})].map(n=>({
    name:n.getAttribute("aria-label") || [...n.labels || []].map(l=>l.textContent.trim()).join(" "),
    id:n.id
  }))`);
  assert.ok(controls.length > 0);
  assert.ok(controls.every(c => c.name.length > 0), JSON.stringify(controls));
  const tree = await b.accessibility();
  for (const name of ["Delivered quote ID", "Returned quantity", "Request date", "Receiving mode", "Receiving location",
    "Proposed refund amount (QAR)", "Refund preference", "Requested refund method", "Receipt / collection details"]) {
    assert.ok(tree.some(n => !n.ignored && n.name?.value === name), `Chromium accessible name: ${name}`);
  }
}
async function prepare(b) {
  await set(b, "#pw-return-qty-501", "1");
  await selectFirst(b, `${intake} select[required]`);
  await set(b, `${intake} input[placeholder="0.00"]`, "10.00");
  await selectFirst(b, `${intake} select[required]:has(option[value="cash"])`);
  await set(b, `${intake} textarea`, "TEST ONLY received at showroom");
}

test("return intake native tab order, accessible names and client/server validation announcements", async () => {
  await run({}, async (b, f) => {
    await loadSale(b);
    await assertLabels(b, intake);
    await focus(b, "#pw-return-quote");
    const controls = [
      "#pw-return-qty-501", `${intake} input[type=date]`, `${intake} select:not([required])`,
      `${intake} select[required]`, `${intake} input[placeholder="0.00"]`,
      `${intake} select[required]:has(option[value="cash"])`,
      `${intake} select:has(option[value="usually_original"])`,
      `${intake} input[readonly]`, `${intake} textarea`,
    ];
    for (const selector of controls) {
      // Chromium's native date editor has multiple segment tab stops.
      for (let i = 0; i < 5; i++) {
        await b.key("Tab");
        if (await b.evaluate(active(selector))) break;
      }
      await assertFocus(b, selector);
    }
    await prepare(b);
    await set(b, "#pw-return-qty-501", "0");
    await focus(b, `${intake} textarea`);
    await b.key("Tab"); await assertFocus(b, `${intake} button[type=submit]`);
    await b.key("Enter");
    await b.wait(`${JSON.stringify("Enter a positive returned quantity")} && document.querySelector('${area} [role=alert]')?.textContent.includes("Enter a positive returned quantity")`);
    // Test client-side error before a request, not a posting.
    assert.equal(f.posts.length, 0);
    await focus(b, "#pw-return-qty-501");
    await b.evaluate(`document.querySelector("#pw-return-qty-501").select()`);
    await b.text("1");
    await focus(b, `${intake} button[type=submit]`); await b.key("Enter");
    await b.wait(`document.querySelector("${area} [role=alert]")?.textContent.includes("TEST ONLY validation feedback")`);
    assert.equal(f.posts.length, 1);
    assert.equal(f.posts[0].path, "/api/purchasing/returns/");
    assert.ok((await b.accessibility()).some(n => !n.ignored && n.role?.value === "alert"));
  });
});

test("inspection native keyboard, error announcement, safe retry and Finance-pending status feedback", async () => {
  await run({}, async (b, f) => {
    await b.click(`${area} .pw-return-table button`);
    await b.wait(`!!document.querySelector("${inspection} select")`);
    const names = await b.accessibility();
    for (const name of ["Condition for line 501", "Inspection notes for line 501", "Close"]) {
      assert.ok(names.some(n => !n.ignored && n.name?.value === name), `Inspection accessible name: ${name}`);
    }
    await focus(b, `${inspection} button`); await b.key("Tab");
    await assertFocus(b, `${inspection} select`);
    await b.key(" "); await b.key("ArrowDown"); await b.key("Enter");
    await b.wait(`document.querySelector("${inspection} select").value === "good"`);
    await b.key("Tab");
    await assertFocus(b, `${inspection} textarea`);
    await b.text("TEST ONLY condition observed"); await b.key("Tab");
    await assertFocus(b, `${inspection} button[type=submit]`);
    await b.key("Enter");
    await b.wait(`document.querySelector("${area} [role=alert]")?.textContent.includes("TEST ONLY validation feedback")`);
    assert.equal(await b.evaluate(`document.querySelector("${area} [role=alert] strong").textContent`),
      "Inspection not recorded", "The inspection error announcement must identify inspection, not intake creation");
    assert.ok((await b.accessibility()).some(n => !n.ignored && n.role?.value === "alert"));
    assert.equal(await b.evaluate(`document.querySelector("${inspection} textarea").value`), "TEST ONLY condition observed");
    f.allowInspection();
    await focus(b, `${inspection} button[type=submit]`);
    await b.key("Enter");
    await b.wait(`document.querySelector("${area} [role=status]")?.textContent.includes("pending authorized Finance review")`);
    assert.equal(f.posts.length, 2);
    assert.deepEqual(f.posts[0], f.posts[1], "Explicit retry retains exact operation, key and payload");
    assert.equal(await b.evaluate(`document.querySelector("${area} [role=status] strong").textContent`),
      "Synthetic inspection recorded");
    assert.equal(await b.evaluate(`!!document.querySelector("${inspection} button[type=submit]")`), false);
    const ax = await b.accessibility();
    assert.ok(ax.some(n => !n.ignored && n.role?.value === "status"));
  });
});

for (const width of [1280, 390, 320]) {
  test(`return forms ${width}px: no page overflow, labeled reachable controls and locally scrollable register`, async () => {
    await run({ width, height: 844 }, async (b, f) => {
      await loadSale(b); await prepare(b);
      await b.click(`${area} .pw-return-table button`);
      await b.wait(`!!document.querySelector("${inspection} select")`);
      const geometry = await b.evaluate(`(() => {
        const nodes=[...document.querySelectorAll("${area} input,${area} select,${area} textarea,${inspection} button")];
        return {page:document.documentElement.scrollWidth, width:document.documentElement.clientWidth,
          headingWidth:document.querySelector("#pw-inspection-title").getBoundingClientRect().width,
          overflow:[...document.querySelectorAll("body *")].filter(n=>{
            if(n.closest(".pw-table-wrap,.sidebar"))return false;
            return n.getBoundingClientRect().right>document.documentElement.clientWidth;
          }).slice(0,8).map(n=>({tag:n.tagName,class:n.className,right:n.getBoundingClientRect().right})),
          controls:nodes.map(n=>{
            n.scrollIntoView({block:"center",inline:"nearest"});
            const r=n.getBoundingClientRect();
            return {label:n.getAttribute("aria-label") || [...n.labels || []].map(l=>l.textContent).join(" ") || n.textContent,
              left:r.left,right:r.right,width:r.width,client:n.clientWidth,scroll:n.scrollWidth,
              hit:n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))};
          }),
          table: getComputedStyle(document.querySelector("${area} .pw-table-wrap")).overflowX};
      })()`);
      assert.ok(geometry.page <= geometry.width, JSON.stringify(geometry));
      assert.ok(geometry.headingWidth >= 100, `Inspection heading must have readable width, not a word-by-word column: ${geometry.headingWidth}px`);
      for (const c of geometry.controls) {
        assert.ok(c.label.trim(), JSON.stringify(c));
        assert.ok(c.left >= 0 && c.right <= width && c.width > 25 && c.hit, JSON.stringify(c));
      }
      assert.equal(geometry.table, "auto");
      await focus(b, `${inspection} button`); await b.key("Tab");
      await assertFocus(b, `${inspection} select`);
      await b.key("Tab"); await assertFocus(b, `${inspection} textarea`);
      await b.key("Tab", true); await b.key("Tab", true);
      await assertFocus(b, `${inspection} button`);
      await b.screenshot(`inspection-${width}`, "sales-return-browser");
      await b.key("Enter");
      await b.wait(`!document.querySelector("${inspection}")`);
      assert.equal(f.posts.length, 0);
    });
  });
}
