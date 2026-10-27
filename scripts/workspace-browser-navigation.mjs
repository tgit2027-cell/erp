import assert from "node:assert/strict";

// Browser input only: setup focus, then activate the actual navigation control.
export async function openFunction(b, section, child) {
  const link = `[data-workspace-link="${section}/${child}"]`;
  await b.wait(`!!document.querySelector(${JSON.stringify(link)})`);
  await b.evaluate(`(() => {
    const node = document.querySelector(${JSON.stringify(link)});
    node.closest(".section-navigation-group").querySelector(".nav-section-toggle").focus();
  })()`);
  const open = await b.evaluate(`document.querySelector(${JSON.stringify(link)})
    .closest(".section-navigation-group").querySelector(".nav-section-toggle").getAttribute("aria-expanded")`);
  if (open !== "true") await b.key("Enter");
  await b.evaluate(`document.querySelector(${JSON.stringify(link)}).focus()`);
  await b.key("Enter");
  await assertFunction(b, section, child);
}

export async function assertFunction(b, section, child) {
  const route = `${section}/${child}`;
  await b.wait(`[...document.querySelectorAll("[data-workspace-view]")]
    .some(node => node.dataset.workspaceView === ${JSON.stringify(route)} && node.getClientRects().length)`);
  assert.equal(await b.evaluate("location.hash"), `#/${route}`);
  assert.equal(await b.evaluate(`document.querySelector('[data-workspace-link="${route}"]')
    ?.getAttribute("aria-current")`), "page");
  assert.equal(await b.evaluate(`document.querySelector('[data-workspace-link="${route}"]')
    ?.closest(".section-navigation-group").querySelector(".nav-section-toggle").getAttribute("aria-expanded")`), "true");
}

export async function isVisible(b, selector) {
  return b.evaluate(`[...document.querySelectorAll(${JSON.stringify(selector)})].some(node => !!node.getClientRects().length)`);
}
