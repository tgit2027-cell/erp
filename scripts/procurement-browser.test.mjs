// Built App + native Chromium; mocked synthetic APIs, no Django/DB/transactions.
import assert from "node:assert/strict";
import { test } from "node:test";
import { browserHarness } from "./browser-harness.mjs";
import { openFunction } from "./workspace-browser-navigation.mjs";

const favorite = { destination: "procurement", label: "Procurement & Imports" };
const page = { rows: [], pagination: {
  page: 1, page_size: 20, total_count: 0, total_pages: 1, next_page: null, previous_page: null,
} };
const group = '.section-navigation-group:has([title="Procurement & Imports"])';
const parent = '[title="Procurement & Imports"].nav-section-toggle';
const firstChild = `${group} .section-navigation-children button`;

async function mockApi(req, url) {
  if (req.method !== "GET") return null;
  if (url.pathname === "/api/favorites/") {
    return { data: { available_destinations: [favorite], favorites: [favorite] } };
  }
  if (url.pathname === "/api/purchasing/quotes/" || url.pathname === "/api/purchasing/commitments/") {
    return { data: page };
  }
  if (url.pathname === "/api/stock-receipt-candidates/") {
    return { status: 403, data: { detail: "TEST ONLY preparation catalog unavailable" } };
  }
  return null;
}

for (const width of [1280, 390, 320]) {
  test(`Procurement native keyboard, Favorites and non-posting preparation fit ${width}px`, async () => {
    const b = await browserHarness({ width, height: 900, mockApi });
    try {
      await b.wait(`!!document.querySelector(${JSON.stringify(`${group} .favorite-toggle`)})`);
      await b.evaluate(`document.querySelector(${JSON.stringify(parent)}).focus()`);
      await b.key("Enter");
      assert.equal(await b.evaluate(`document.querySelector(${JSON.stringify(parent)}).getAttribute("aria-expanded")`), "true");
      assert.equal(await b.evaluate('getComputedStyle(document.activeElement).outlineStyle !== "none"'), true);
      await b.key(" ");
      assert.equal(await b.evaluate(`document.querySelector(${JSON.stringify(parent)}).getAttribute("aria-expanded")`), "false");
      await b.key("Tab"); // Existing per-user pin control, not a transaction.
      assert.equal(await b.evaluate(`document.activeElement === document.querySelector(${JSON.stringify(`${group} .favorite-toggle`)})`), true);
      await b.key("Tab");
      assert.equal(await b.evaluate(`!!document.activeElement.closest(${JSON.stringify(`${group} .section-navigation-children`)})`), false);
      await b.key("Tab", true); await b.key("Tab", true);
      assert.equal(await b.evaluate(`document.activeElement === document.querySelector(${JSON.stringify(parent)})`), true);
      await b.key("Enter"); await b.key("Tab"); await b.key("Tab");
      assert.equal(await b.evaluate(`document.activeElement === document.querySelector(${JSON.stringify(firstChild)})`), true);
      await b.key("Enter");
      await b.wait('document.querySelector("#purchasing-workbench-title")?.textContent === "Procurement & Imports"');
      await openFunction(b, "procurement", "receipt");
      assert.equal(await b.evaluate('!!document.querySelector(".planned-panel")'), false);
      assert.equal(await b.evaluate('!!document.querySelector("#pw-receipt-title")'), true);
      assert.equal(await b.evaluate('!!document.querySelector("#pw-create-quote-title, .pw-quote-form, #pw-sales-return-summary")'), false);

      await b.click(".my-page-link");
      await b.wait('!!document.querySelector(".my-favorite-open")');
      await b.click(".my-favorite-open");
      await b.wait('document.querySelector("#purchasing-workbench-title")?.textContent === "Procurement & Imports"');
      await openFunction(b, "procurement", "receipt");
      assert.equal(await b.evaluate(`document.querySelector(${JSON.stringify(parent)}).getAttribute("aria-expanded")`), "true");
      await openFunction(b, "procurement", "packing");
      if (width < 760) await b.click(parent); // Explicitly dismiss existing active-parent flyout.

      // Section navigation deliberately focuses its target on the next frame.
      // Let that finish before establishing the keyboard interaction's focus.
      await b.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
      await b.evaluate('document.querySelector(".pw-action-form > summary").focus()');
      await b.wait('document.activeElement === document.querySelector(".pw-action-form > summary")');
      const wasOpen = await b.evaluate('document.querySelector(".pw-action-form").open');
      await b.key(" ");
      assert.equal(await b.evaluate('document.querySelector(".pw-action-form").open'), !wasOpen,
        "Space toggles the actual current disclosure state");
      if (wasOpen) await b.key("Enter");
      await b.wait('document.querySelector(".pw-action-form")?.open === true');
      await b.wait('!!document.querySelector(".packing-preview input[type=file]")');
      assert.equal(await b.evaluate('document.querySelector(".packing-preview input[type=file]").labels.length > 0'), true);
      await b.key("Tab");
      assert.equal(await b.evaluate('!!document.activeElement.closest(".packing-preview")'), true);
      assert.equal(await b.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), true);
      await b.screenshot(`procurement-${width}`, "procurement-browser");
      assert.equal(b.calls.some(call => call.path.includes("/counterparties/")), false,
        "Ordinary user does not gain the staff-only supplier directory");
      assert.deepEqual(b.calls.filter(call => call.method !== "GET"), [],
        "Navigation/preparation must not submit any transaction or pin write");
      b.assertSafe();
    } finally {
      await b.close();
    }
  });
}
