import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import App from "../src/App";

const units = [
  { id: 1, code: "TVL", name: "TEST ONLY unit A", theme_palette: "tivoli" },
  { id: 2, code: "RBB", name: "TEST ONLY unit B", theme_palette: "roche" },
];
// Deliberately not alphabetic/ID order, and children can precede parents.
const firstLocations = [
  { id: 30, code: "test-child-z", name: "TEST ONLY Z child", parent_id: 20 },
  { id: 20, code: "test-root-z", name: "TEST ONLY Z root", parent_id: null },
  { id: 41, code: "test-grandchild", name: "TEST ONLY grandchild", parent_id: 30 },
  { id: 22, code: "test-leaf-a", name: "TEST ONLY A leaf", parent_id: 20 },
  { id: 10, code: "test-root-a", name: "TEST ONLY A root", parent_id: null },
];
const firstLabels = [
  "TEST ONLY Z root", "TEST ONLY Z child", "TEST ONLY grandchild",
  "TEST ONLY A leaf", "TEST ONLY A root",
];
const secondLocations = [
  { id: 120, code: "test-b-child", name: "TEST ONLY B child", parent_id: 110 },
  { id: 110, code: "test-b-root", name: "TEST ONLY B root", parent_id: null },
];
let root;
let container;
let style;
let selectedUnit;
let locationsByUnit;
let calls;
let errors;
let warnings;
let originalError;
let originalWarn;

function json(data) {
  return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
}
async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
async function mount() {
  await act(async () => root.render(<App />));
  await flush();
}
function tree() {
  return container.querySelector(".locations-card .location-tree");
}
function labels() {
  return Array.from(container.querySelectorAll(".locations-card .location-node > span:last-child"),
    (element) => element.textContent);
}
async function switchUnit(id) {
  const select = container.querySelector('select[aria-label="Business unit"]');
  assert.ok(select);
  await act(async () => {
    select.value = String(id);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
}

function assertContext(id) {
  assert.equal(container.querySelector('select[aria-label="Business unit"]').value, String(id));
  assert.equal(document.documentElement.dataset.unitTheme, units.find((unit) => unit.id === id).theme_palette);
  assert.ok(container.querySelector(".page-heading").textContent.includes(units.find((unit) => unit.id === id).name));
  assert.deepEqual(labels(), id === 1 ? firstLabels : ["TEST ONLY B root", "TEST ONLY B child"]);
}

function defer() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

async function clickButton(text) {
  const button = Array.from(container.querySelectorAll("button")).find((node) => node.textContent === text);
  assert.ok(button, `Button "${text}" is available`);
  await act(async () => button.click());
  await flush();
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  // Use the actual tree list rules; the shared test loader otherwise omits CSS.
  const css = readFileSync(new URL("../src/style.css", import.meta.url), "utf8");
  style = document.createElement("style");
  style.textContent = (css.match(/\.location-tree(?: ul)?\s*\{[^}]*\}/g) ?? []).join("\n");
  document.head.append(style);
  selectedUnit = 1;
  locationsByUnit = new Map([[1, firstLocations], [2, secondLocations]]);
  calls = [];
  errors = [];
  warnings = [];
  originalError = console.error;
  originalWarn = console.warn;
  console.error = (...args) => errors.push(args.map(String).join(" "));
  console.warn = (...args) => warnings.push(args.map(String).join(" "));
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, ...options });
    if (url.pathname === "/api/auth/csrf/") return json({ csrfToken: "synthetic-csrf-placeholder" });
    if (url.pathname === "/api/auth/me/") return json({
      authenticated: true, username: "synthetic_location_viewer", is_staff: false,
    });
    if (url.pathname === "/api/organization/") return json({
      company: { name: "TEST ONLY company", code: "TEST" },
      units, selected_unit: units.find((unit) => unit.id === selectedUnit),
      locations: locationsByUnit.get(selectedUnit),
    });
    if (url.pathname === "/api/favorites/") return json({ available_destinations: [], favorites: [] });
    if (url.pathname === "/api/stock-balances/") return json({
      results: [],
      pagination: { page: 1, page_size: 50, total_count: 0, total_pages: 1, previous_page: null, next_page: null },
    });
    if (options.method === "POST" && /^\/api\/units\/[12]\/select\/$/.test(url.pathname)) {
      selectedUnit = Number(url.pathname.split("/")[3]);
      return json({ selected_unit: units.find((unit) => unit.id === selectedUnit) });
    }
    throw new Error(`Unexpected API request: ${options.method ?? "GET"} ${url.pathname}`);
  };
});

afterEach(async () => {
  try {
    await act(async () => root.unmount());
  } finally {
    container.remove();
    style.remove();
    console.error = originalError;
    console.warn = originalWarn;
  }
  assert.deepEqual(errors, [], "No React nesting or other console errors");
  assert.deepEqual(warnings, [], "No React or other console warnings");
});

test("parent child and grandchild render valid nested lists without added indentation or React warnings", async () => {
  await mount();
  const list = tree();
  const roots = list.querySelectorAll(":scope > li");
  assert.equal(roots.length, 2);
  const children = roots[0].querySelectorAll(":scope > ul > li");
  assert.equal(children.length, 2);
  assert.equal(children[0].querySelectorAll(":scope > ul > li").length, 1);
  assert.equal(list.querySelectorAll("ul").length, 2);
  for (const item of list.querySelectorAll("li")) {
    assert.equal(item.parentElement.tagName, "UL");
    assert.equal(item.querySelector(":scope > li"), null);
  }
  assert.deepEqual(Array.from(list.querySelectorAll(".location-node"),
    (node) => node.style.paddingLeft), ["0px", "18px", "36px", "18px", "0px"]);
  assert.equal(window.getComputedStyle(list).marginTop, "10px");
  for (const nested of list.querySelectorAll("ul")) {
    assert.equal(window.getComputedStyle(nested).marginTop, "0px");
    assert.equal(window.getComputedStyle(nested).paddingLeft, "0px");
    assert.equal(window.getComputedStyle(nested).listStyle, "none");
  }
});

test("API labels sibling order and leaves stay unchanged with no inferred location types or extra records", async () => {
  await mount();
  assert.deepEqual(labels(), firstLabels);
  const nodes = Array.from(tree().querySelectorAll("li"));
  assert.equal(nodes.length, firstLocations.length);
  for (const label of ["TEST ONLY grandchild", "TEST ONLY A leaf", "TEST ONLY A root"]) {
    const leaf = nodes.find((node) => node.querySelector(":scope > .location-node > span:last-child").textContent === label);
    assert.ok(leaf);
    assert.equal(leaf.querySelector(":scope > ul"), null);
  }
  assert.equal(calls.filter((call) => call.method && call.method !== "GET").length, 0);
});

test("empty authorized locations retain the existing empty state without a fallback tree", async () => {
  locationsByUnit.set(1, []);
  await mount();
  assert.equal(container.querySelector(".locations-card .empty-state").textContent, "No permitted locations.");
  assert.equal(tree(), null);
  assert.deepEqual(labels(), []);
  assert.equal(calls.filter((call) => call.url.pathname === "/api/organization/").length, 1);
});

test("missing parents are not fetched inferred or inserted into the existing rooted hierarchy", async () => {
  locationsByUnit.set(1, [
    ...firstLocations,
    { id: 900, code: "test-disconnected", name: "TEST ONLY disconnected node", parent_id: 999 },
  ]);
  await mount();
  assert.deepEqual(labels(), firstLabels);
  assert.equal(tree().querySelectorAll("li").length, firstLocations.length);
  assert.deepEqual(new Set(calls.map((call) => call.url.pathname)), new Set([
    "/api/auth/csrf/", "/api/auth/me/", "/api/organization/", "/api/favorites/", "/api/stock-balances/",
  ]));
});

test("BU switching replaces the authorized tree and switching back restores only that unit's locations", async () => {
  await mount();
  assert.deepEqual(labels(), firstLabels);
  await switchUnit(2);
  assert.deepEqual(labels(), ["TEST ONLY B root", "TEST ONLY B child"]);
  assert.equal(container.querySelector('select[aria-label="Business unit"]').value, "2");
  assert.ok(firstLabels.every((label) => !tree().textContent.includes(label)));
  assert.equal(calls.filter((call) => call.url.pathname === "/api/favorites/").at(-1).url.searchParams.get("unit"), "2");
  await switchUnit(1);
  assert.deepEqual(labels(), firstLabels);
  assert.ok(secondLocations.every((location) => !tree().textContent.includes(location.name)));
  assert.equal(container.querySelector('select[aria-label="Business unit"]').value, "1");
  const writes = calls.filter((call) => call.method && call.method !== "GET");
  assert.deepEqual(writes.map((call) => call.url.pathname), ["/api/units/2/select/", "/api/units/1/select/"]);
  assert.ok(writes.every((call) => call.headers["X-CSRFToken"] === "synthetic-csrf-placeholder"));
  assert.equal(calls.filter((call) => call.url.pathname === "/api/organization/").length, 3);
  assert.deepEqual(calls.filter((call) => call.url.pathname === "/api/stock-balances/").map((call) => call.url.searchParams.get("unit")), ["1", "2", "1"]);
});

test("rapid selections cannot let a delayed organization response replace the latest BU context", async () => {
  await mount();
  const baseFetch = globalThis.fetch;
  const responses = [];
  globalThis.fetch = async (path, options) => {
    const response = await baseFetch(path, options);
    if (String(path) === "/api/organization/") {
      const gate = defer();
      responses.push({ response, gate });
      return gate.promise;
    }
    return response;
  };
  await switchUnit(2);
  assertContext(1); // No optimistic label/theme change before the authorized snapshot.
  assert.ok(container.textContent.includes("Updating business-unit context…"));
  await switchUnit(1);
  if (responses.length === 2) {
    // Reproduce the old race: the later snapshot returns before the older one.
    await act(async () => responses[1].gate.resolve(responses[1].response));
    await flush();
    assertContext(1);
    await act(async () => responses[0].gate.resolve(responses[0].response));
  } else {
    // Serialized selection must finish its refresh before issuing the next POST.
    assert.equal(responses.length, 1);
    assert.equal(calls.filter((call) => call.method === "POST").length, 1);
    await act(async () => responses[0].gate.resolve(responses[0].response));
    await flush();
    assertContext(2);
    assert.equal(responses.length, 2);
    await act(async () => responses[1].gate.resolve(responses[1].response));
  }
  await flush();
  assert.equal(selectedUnit, 1);
  assertContext(1);
  assert.equal(container.querySelector('[role="alert"]'), null);
  assert.ok(!container.textContent.includes("Updating business-unit context…"));
});

test("overlapping selection attempts cannot apply POST mutations out of order", async () => {
  await mount();
  const baseFetch = globalThis.fetch;
  const pending = [];
  globalThis.fetch = async (path, options) => {
    if (options?.method === "POST" && String(path).startsWith("/api/units/")) {
      const gate = defer();
      pending.push({ path, options, gate });
      await gate.promise; // The synthetic server applies the selection only on release.
    }
    return baseFetch(path, options);
  };
  await switchUnit(2);
  await switchUnit(1);
  assertContext(1);
  if (pending.length === 2) {
    await act(async () => pending[1].gate.resolve());
    await flush();
    assertContext(1);
    await act(async () => pending[0].gate.resolve());
  } else {
    assert.equal(pending.length, 1);
    await act(async () => pending[0].gate.resolve());
    await flush();
    assertContext(2);
    assert.equal(pending.length, 2);
    await act(async () => pending[1].gate.resolve());
  }
  await flush();
  assert.equal(selectedUnit, 1, "The server session and final UI must agree on the last requested BU");
  assertContext(1);
});

test("a 403-denied unit switch preserves the confirmed label theme and tree and shows the server error", async () => {
  await mount();
  const baseFetch = globalThis.fetch;
  globalThis.fetch = async (path, options) => {
    if (String(path) === "/api/units/2/select/") {
      calls.push({ url: new URL(String(path), "https://synthetic.invalid"), ...options });
      return new Response(JSON.stringify({ detail: "TEST ONLY unit switch denied." }), {
        status: 403, headers: { "Content-Type": "application/json" },
      });
    }
    return baseFetch(path, options);
  };
  await switchUnit(2);
  assert.equal(selectedUnit, 1);
  assertContext(1);
  assert.equal(container.querySelector('[role="alert"]').textContent, "TEST ONLY unit switch denied.");
  assert.equal(calls.filter((call) => call.url.pathname === "/api/organization/").length, 1);
  assert.equal(calls.filter((call) => call.url.pathname === "/api/favorites/").length, 1);
  await switchUnit(1);
  assertContext(1);
  assert.equal(container.querySelector('[role="alert"]'), null, "A later successful selection clears the error");
});

test("successful selection followed by failed organization refresh clears stale context and can safely reload", async () => {
  await mount();
  const baseFetch = globalThis.fetch;
  let failRefresh = true;
  globalThis.fetch = async (path, options) => {
    if (String(path) === "/api/organization/" && failRefresh) {
      calls.push({ url: new URL(String(path), "https://synthetic.invalid"), ...options });
      return new Response(JSON.stringify({ detail: "TEST ONLY context refresh failed." }), {
        status: 503, headers: { "Content-Type": "application/json" },
      });
    }
    return baseFetch(path, options);
  };
  await switchUnit(2);
  assert.equal(selectedUnit, 2);
  const select = container.querySelector('select[aria-label="Business unit"]');
  assert.equal(select.value, "");
  assert.equal(select.disabled, true);
  assert.equal(document.documentElement.dataset.unitTheme, "slate");
  assert.deepEqual(labels(), []);
  assert.equal(tree(), null);
  assert.ok(!container.querySelector(".page-heading").textContent.includes(units[0].name));
  assert.equal(container.querySelector('[role="alert"]').textContent, "TEST ONLY context refresh failed.");
  assert.ok(container.textContent.includes("Business-unit context unavailable"));
  assert.equal(calls.filter((call) => call.url.pathname === "/api/favorites/").length, 1);
  failRefresh = false;
  await clickButton("Reload business-unit context");
  assertContext(2);
  assert.equal(container.querySelector('[role="alert"]'), null);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1, "Recovery reads context, never repeats the selection mutation");
  assert.equal(calls.filter((call) => call.url.pathname === "/api/favorites/").at(-1).url.searchParams.get("unit"), "2");
});
