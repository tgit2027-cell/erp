import assert from "node:assert/strict";
import { test } from "node:test";
import { browserHarness } from "./browser-harness.mjs";

const inventory = '[title="Inventory & Warehouses"].nav-section-toggle';
const purchasing = '[title="Purchasing Workbench"].nav-section-toggle';
const child = (name) => `.section-navigation-children [aria-label="${name}"]`;
const active = (selector) => `document.activeElement === document.querySelector(${JSON.stringify(selector)})`;
const expanded = (selector) => `document.querySelector(${JSON.stringify(selector)})?.getAttribute("aria-expanded") === "true"`;
const focusContrast = `(() => {
  const luminance = color => {
    const rgb = color.match(/[\\d.]+/g).slice(0,3).map(Number).map(v => {
      const c=v/255; return c<=0.04045 ? c/12.92 : ((c+0.055)/1.055)**2.4;
    });
    return rgb[0]*0.2126+rgb[1]*0.7152+rgb[2]*0.0722;
  };
  const a=luminance(getComputedStyle(document.activeElement).outlineColor);
  const b=luminance(getComputedStyle(document.querySelector(".sidebar")).backgroundColor);
  return (Math.max(a,b)+0.05)/(Math.min(a,b)+0.05);
})()`;

async function run(options, callback) {
  const browser = await browserHarness(options);
  try { await callback(browser); browser.assertSafe(); }
  finally { await browser.close(); }
}

test("native Tab Enter and Space preserve focus order disclosure and hidden-child exclusion", async () => {
  await run({}, async (b) => {
    for (const selector of [
      '.sidebar .nav-row > .nav-item', '[title="Retail & Sales"].nav-section-toggle',
      '.section-navigation-group:nth-child(1) .section-navigation-favorite .favorite-toggle',
      '[title="Procurement & Imports"].nav-section-toggle',
      '.section-navigation-group:nth-child(2) .section-navigation-favorite .favorite-toggle', inventory,
    ]) {
      await b.key("Tab"); assert.equal(await b.evaluate(active(selector)), true, selector);
      assert.equal(await b.evaluate(`getComputedStyle(document.activeElement).outlineStyle !== "none" &&
        parseFloat(getComputedStyle(document.activeElement).outlineWidth) >= 2`), true, "Visible native keyboard focus");
    }
    const before = b.calls.length;
    await b.key("Enter"); await b.wait(expanded(inventory));
    assert.equal(await b.evaluate(active(inventory)), true);
    const inventoryChildren = [
      ["Inventory quantities", "inventory/main"], ["Permitted locations", "inventory/locations"],
      ["Prepare imported-goods receipt", "inventory/receipt"], ["Purchase commitments", "inventory/commitments"],
      ["Commitment receipt progress", "inventory/progress"], ["Packing-list preview", "inventory/packing"],
      ["Inventory & transfer registers", "inventory/registers"], ["Stock transfer requests", "inventory/transfers"], ["Warehouse capacity", "inventory/capacity"],
    ];
    await b.key("Tab");
    assert.equal(await b.evaluate(active('.section-navigation-group:nth-child(3) .section-navigation-favorite .favorite-toggle')), true);
    let traversed = 1;
    for (const [label, route] of inventoryChildren) {
      await b.key("Tab");
      traversed += 1;
      assert.equal(await b.evaluate(active(`[data-workspace-link="${route}"]`)), true, label);
      if (!route.endsWith("/main")) {
        await b.key("Tab");
        traversed += 1;
        assert.equal(await b.evaluate(active(`[data-workspace-link="${route}"] + .favorite-toggle`)), true,
          `${label}: separate subsection Favorite control`);
      }
    }
    for (let i = 0; i < traversed; i++) await b.key("Tab", true);
    assert.equal(await b.evaluate(active(inventory)), true);
    await b.key(" "); await b.wait(`!(${expanded(inventory)})`);
    await b.key("Tab");
    assert.equal(await b.evaluate(active('.section-navigation-group:nth-child(3) .section-navigation-favorite .favorite-toggle')), true);
    await b.key("Tab");
    assert.equal(await b.evaluate(active('[title="Delivery & After-Sales"].nav-section-toggle')), true,
      "Collapsed inventory children are excluded from native sequential focus");
    for (let i = 0; i < 20 && !await b.evaluate(active(purchasing)); i++) await b.key("Tab");
    assert.equal(await b.evaluate(active(purchasing)), true);
    await b.key("Tab");
    assert.equal(await b.evaluate(active('.section-navigation-group:nth-child(9) .section-navigation-favorite .favorite-toggle')), true);
    assert.ok(await b.evaluate(focusContrast) >= 3, "Actual favorite focus ring must contrast with sidebar");
    assert.equal(b.calls.length, before, "Disclosure/focus never requests data");
    await b.screenshot("desktop-keyboard");
  });
});

test("native Favorites and My Page navigation reveals active parent and preserves denied workbench", async () => {
  await run({}, async (b) => {
    await b.click(".my-page-link");
    await b.wait('document.querySelector("h1")?.textContent === "My Page"');
    await b.click(".my-favorite-open");
    await b.wait(expanded(purchasing));
    await b.wait('!!document.querySelector(".pw-overview-launcher")');
    assert.equal(await b.evaluate(`document.querySelector(${JSON.stringify(child("Purchasing Workbench"))})?.getAttribute("aria-current")`), "page");
    await b.click(".pw-overview-launcher button:nth-child(2)");
    await b.wait('document.querySelector(".purchasing-workbench")?.textContent.includes("Transactions unavailable")');
    await b.click(purchasing);
    await b.wait(`!(${expanded(purchasing)})`);
    await b.click(".favorite-shortcut .nav-item");
    await b.wait(expanded(purchasing));
    assert.equal(await b.evaluate('document.querySelector("h1").textContent'), "Purchasing Workbench");
    assert.equal(await b.evaluate('!!document.querySelector(".palette-admin, .capacity-audit-register, .report-panel-manager")'), false);
    assert.ok(b.calls.every(({ method }) => method === "GET"));
    await b.screenshot("desktop-favorites");
  });
});

test("native BU selection retains active inventory child and uses only current scoped rows and theme", async () => {
  await run({}, async (b) => {
    await b.click(inventory); await b.wait(expanded(inventory));
    await b.click(child("Inventory quantities"));
    await b.wait('document.querySelector(".inventory-view tbody")?.textContent.includes("TEST-LEDGER-1")');
    const colors = [];
    for (const [id, theme] of [[1, "tivoli"], [2, "roche"], [3, "natuzzi"]]) {
      if (id !== 1) {
        // Focus is explicit; selection itself uses actual Chromium keyboard default behavior.
        await b.evaluate('document.querySelector(\'[aria-label="Business unit"]\').focus()');
        await b.key("ArrowDown"); await b.key("Enter");
      }
      await b.wait(`document.documentElement.dataset.unitTheme === "${theme}"`);
      await b.wait(`document.querySelector(".inventory-view tbody")?.textContent.includes("TEST-LEDGER-${id}")`);
      assert.equal(await b.evaluate('document.querySelector(\'[aria-label="Business unit"]\').value'), String(id));
      assert.equal(await b.evaluate(expanded(inventory)), true);
      assert.equal(await b.evaluate(`document.querySelector(${JSON.stringify(child("Inventory quantities"))}).getAttribute("aria-current")`), "page");
      colors.push(await b.evaluate('getComputedStyle(document.querySelector(".sidebar")).backgroundColor'));
      assert.equal(new URL(`http://fixture.invalid${b.calls.filter(c => c.path === "/api/stock-balances/").at(-1).query}`).searchParams.get("unit"), String(id));
    }
    assert.equal(new Set(colors).size, 3, "All three actual BU sidebar themes remain distinct");
    assert.deepEqual(b.calls.filter(c => c.method !== "GET").map(c => c.path),
      ["/api/units/2/select/", "/api/units/3/select/"]);
    await b.screenshot("desktop-natuzzi");
  });
});

for (const [width, height] of [[390, 844], [320, 844], [320, 568]]) {
  test(`narrow ${width}x${height} flyout is readable hit-testable unclipped and controls do not overlap`, async () => {
    await run({ width, height }, async (b) => {
      // Verify the favorite-bearing header, not just a header without a star.
      await b.click(purchasing); await b.wait(expanded(purchasing));
      const geometry = await b.evaluate(`(() => {
        const header = document.querySelector(${JSON.stringify(purchasing)});
        const group = header.closest(".section-navigation-group");
        const list = group.querySelector("ul");
        const star = group.querySelector(".favorite-toggle");
        const r = list.getBoundingClientRect(), h = header.getBoundingClientRect(), s = star.getBoundingClientRect();
        return {
          list: {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width},
          controlsOverlap: Math.min(h.right,s.right)>Math.max(h.left,s.left) &&
            Math.min(h.bottom,s.bottom)>Math.max(h.top,s.top),
         childStars: [...list.querySelectorAll(".nav-child-row")].flatMap(row => {
           const link=row.querySelector(".nav-item"), star=row.querySelector(".favorite-toggle");
           if (!star) return [];
           const a=link.getBoundingClientRect(), s=star.getBoundingClientRect();
           return [{label:link.textContent.trim(),overlap:Math.min(a.right,s.right)>Math.max(a.left,s.left) &&
             Math.min(a.bottom,s.bottom)>Math.max(a.top,s.top),inside:s.left>=r.left && s.right<=r.right}];
         }),
         children: [...list.querySelectorAll(".nav-item[data-workspace-link]")].map(node => {
            const a=node.getBoundingClientRect(), label=node.querySelector("span"), t=label.getBoundingClientRect();
             return {label:node.textContent.trim(),font:parseFloat(getComputedStyle(node).fontSize),nodeWidth:a.width,labelWidth:t.width,labelScroll:label.scrollWidth,labelClient:label.clientWidth,labelDisplay:getComputedStyle(label).display,
              textFits:label.scrollWidth<=label.clientWidth,
              hit:node.contains(document.elementFromPoint(a.x+a.width/2,a.y+a.height/2)),
              textHit:node.contains(document.elementFromPoint(t.x+t.width/2,t.y+t.height/2))};
          }),
        };
      })()`);
      assert.equal(geometry.controlsOverlap, false, "Disclosure and favorite hit targets must not overlap");
      assert.ok(geometry.list.left >= 64 && geometry.list.right <= width, JSON.stringify(geometry));
      assert.ok(geometry.list.top >= 0 && geometry.list.bottom <= height, JSON.stringify(geometry));
      assert.ok(geometry.list.width >= 200, "Readable flyout width");
      assert.equal(geometry.children.length, 4);
       assert.equal(geometry.childStars.length, 3, "All supported non-main Purchasing destinations are available in the fixture");
       for (const star of geometry.childStars) {
         assert.equal(star.overlap, false, `Child Favorite must not overlap its navigation link: ${star.label}`);
         assert.equal(star.inside, true, `Child Favorite remains within the flyout: ${star.label}`);
       }
      for (const entry of geometry.children) {
         assert.ok(entry.font >= 11 && entry.textFits, `Readable unclipped label: ${JSON.stringify(entry)}`);
        assert.ok(entry.hit && entry.textHit, `Flyout not clipped or covered: ${entry.label}`);
      }
      await b.key("Tab");
      assert.equal(await b.evaluate(active('.section-navigation-group:nth-child(9) .section-navigation-favorite .favorite-toggle')), true);
      assert.ok(await b.evaluate(focusContrast) >= 3, "Narrow favorite focus contrast");
      await b.screenshot(`narrow-${width}x${height}`);
      await b.click(child("Quote lookup"));
      await b.wait('document.querySelector("h1")?.textContent === "Quote lookup"');
      assert.equal(await b.evaluate(active(".pw-quote-form")), true);
      assert.ok(b.calls.every(c => c.method === "GET"));
    });
  });
}
