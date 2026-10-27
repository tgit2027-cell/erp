import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import App from "../src/App";

const destination = { destination: "purchasing", label: "Purchasing Workbench" };
const unit = { id: 2, code: "RBB", name: "TEST ONLY Roche Bobois", theme_palette: "roche" };
let root;
let container;
let calls;
let activeUser;
let favorites;
let advertisePurchasing;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
async function mount() {
  await act(async () => root.render(<App />));
  await flush();
}
async function click(element) {
  assert.ok(element, "Expected navigation control to be rendered");
  await act(async () => element.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await flush();
}
function star(action) {
  return container.querySelector(`[aria-label="${action} Purchasing Workbench ${action === "Add" ? "to" : "from"} favorites"]`);
}
async function remount() {
  await act(async () => root.unmount());
  root = createRoot(container);
  await mount();
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  activeUser = "demo_roche";
  favorites = new Map([["demo_roche", false], ["demo_multi", false]]);
  advertisePurchasing = true;
  calls = [];
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://synthetic.invalid");
    calls.push({ url, ...options });
    if (url.pathname === "/api/auth/csrf/") return json({ csrfToken: "synthetic-csrf-placeholder" });
    if (url.pathname === "/api/auth/me/") return json({ authenticated: true, username: activeUser, is_staff: false });
    if (url.pathname === "/api/organization/") return json({
      company: { name: "TEST ONLY company", code: "TEST" },
      selected_unit: unit, units: [unit], locations: [],
    });
    if (url.pathname === "/api/favorites/" && options.method === "POST") {
      assert.deepEqual(JSON.parse(options.body), { destination: "purchasing" });
      favorites.set(activeUser, true);
      return json({ favorite: destination }, 201);
    }
    if (url.pathname === "/api/favorites/purchasing/" && options.method === "DELETE") {
      favorites.set(activeUser, false);
      return new Response(null, { status: 204 });
    }
    if (url.pathname === "/api/favorites/") return json({
      available_destinations: advertisePurchasing ? [destination] : [],
      favorites: favorites.get(activeUser) ? [destination] : [],
    });
    if (url.pathname === "/api/purchasing/quotes/") {
      return json({ detail: "Existing order authorization is missing." }, 404);
    }
    return json({ detail: "Existing authorization is missing." }, 403);
  };
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

test("sidebar pin and My Page open the current purchasing route without changing BU or enabling denied transactions", async () => {
  await mount();
  assert.ok(star("Add"));
  await click(star("Add"));
  const pin = calls.find((item) => item.method === "POST");
  assert.equal(pin.url.pathname, "/api/favorites/");
  assert.equal(pin.headers["X-CSRFToken"], "synthetic-csrf-placeholder");
  await click(container.querySelector(".my-page-link"));
  assert.match(container.querySelector(".my-page-view").textContent, /Purchasing Workbench/);
  await click(container.querySelector(".my-favorite-open"));
  assert.equal(container.querySelector("#purchasing-workbench-title").textContent, "Purchasing workbench");
  assert.ok(container.querySelector(".pw-overview-launcher"));
  await click([...container.querySelectorAll(".pw-overview-launcher button")].find((node) => /Transaction workbench/.test(node.textContent)));
  assert.match(container.querySelector(".pw-transactions").textContent, /Transactions unavailable/);
  const accessChecks = calls.filter((item) => item.url.pathname === "/api/purchasing/quotes/");
  assert.ok(accessChecks.length > 0);
  assert.ok(accessChecks.every((item) => item.url.searchParams.get("business_unit_id") === "2"));
  assert.equal(calls.filter((item) => item.url.pathname.startsWith("/api/units/")).length, 0);
  assert.equal(calls.filter((item) => item.url.pathname.startsWith("/api/purchasing/") && item.method === "POST").length, 0);
  await click(container.querySelector(".my-page-link"));
  await click(container.querySelector('.my-page-view [aria-label="Remove Purchasing Workbench from favorites"]'));
  assert.match(container.querySelector(".my-page-view").textContent, /No favorites yet/);
  const removal = calls.find((item) => item.method === "DELETE");
  assert.equal(removal.url.pathname, "/api/favorites/purchasing/");
  assert.equal(removal.headers["X-CSRFToken"], "synthetic-csrf-placeholder");
});

test("server-persisted purchasing favorite survives remount and does not leak to another signed-in user", async () => {
  await mount();
  await click(star("Add"));
  await remount();
  assert.ok(star("Remove"));
  await click(container.querySelector(".favorite-shortcut .nav-item"));
  assert.ok(container.querySelector("#purchasing-workbench-title"));
  activeUser = "demo_multi";
  await remount();
  await click(container.querySelector(".my-page-link"));
  assert.match(container.querySelector(".my-page-view").textContent, /No favorites yet/);
  assert.equal(container.querySelector(".favorite-shortcut"), null);
  assert.equal(favorites.get("demo_roche"), true);
});

test("a destination absent from the server allowlist cannot expose a purchasing star or stale My Page shortcut", async () => {
  favorites.set(activeUser, true);
  advertisePurchasing = false;
  await mount();
  assert.equal(star("Add"), null);
  assert.equal(star("Remove"), null);
  await click(container.querySelector(".my-page-link"));
  assert.equal(container.querySelector(".my-favorite-open"), null);
  assert.equal(calls.filter((item) => item.method === "POST").length, 0);
});
