import assert from "node:assert/strict";
import { test } from "node:test";
import { browserHarness } from "./browser-harness.mjs";

const selector = (label) => `[aria-label=${JSON.stringify(label)}]`;

for (const [name, width, height] of [["desktop", 1280, 900], ["mobile", 390, 844]]) {
  test(`standalone Finance draft ${name}: keyboard, English review, safe local save and no API`, async () => {
    const browser = await browserHarness({
      width, height, assetDirectory: "generated-artifacts/finance-draft-prototype",
      readyExpression: '!!document.querySelector("#finance-draft-title")',
    });
    try {
      await browser.key("Tab");
      assert.equal(await browser.evaluate(`document.activeElement === document.querySelector(${JSON.stringify(selector("Draft account code"))})`), true);
      assert.equal(await browser.evaluate('getComputedStyle(document.activeElement).outlineStyle === "solid" && parseFloat(getComputedStyle(document.activeElement).outlineWidth) >= 3'), true);
      await browser.text("TEST-DRAFT-ACCT-A");
      await browser.key("Tab"); await browser.text("Synthetic proposed account");
      await browser.key("Tab"); await browser.text("حساب تجريبي مقترح");
      await browser.key("Tab"); await browser.key("Enter");
      await browser.wait('!!document.querySelector(\'[aria-label="Local draft accounts"]\')');
      async function fill(label, text) {
        await browser.click(selector(label)); await browser.text(text);
      }
      async function choose(label, value) {
        await browser.evaluate(`(() => {
          const node = document.querySelector(${JSON.stringify(selector(label))});
          Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(node, ${JSON.stringify(value)});
          node.dispatchEvent(new Event("change", {bubbles:true}));
        })()`);
      }
      assert.equal(await browser.evaluate('document.querySelector(\'[aria-label="Account name Arabic (optional)"]\')?.getAttribute("aria-label")'), "Account name Arabic (optional)");
      await fill("Draft journal title English", "Synthetic browser draft");
      await fill("Draft journal title Arabic (optional)", "مسودة تجريبية للمتصفح");
      for (const index of [1, 2]) {
        await choose(`Draft account line ${index}`, "TEST-DRAFT-ACCT-A");
        await choose(`Business unit line ${index}`, "TEST-BU-A");
      }
      await fill("Debit line 1", "12.34");
      await fill("Credit line 1", "0.00");
      await fill("Debit line 2", "0.00");
      await fill("Credit line 2", "12.34");
      await browser.click('.finance-draft-journal button[type="submit"]');
      await browser.wait('document.querySelector(\'[role="status"]\')?.textContent.includes("Balanced draft — not approved or posted.")');
      assert.equal(await browser.evaluate('document.querySelectorAll(".finance-draft-table-scroll tbody tr").length'), 2);
      assert.equal(await browser.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true, "No page-level horizontal overflow");
      assert.equal(await browser.evaluate('!!document.querySelector(\'[lang="ar"][dir="rtl"]\')'), true);
      await browser.click(".finance-draft-actions button:last-child");
      await browser.wait('!!document.querySelector(\'[aria-label="Session-only synthetic drafts"]\')');
      assert.equal(browser.calls.length, 0, "No API requests or backend authentication");
      await browser.screenshot(`finance-draft-${name}`, "finance-draft");
      browser.assertSafe();
    } finally {
      await browser.close();
    }
  });
}
