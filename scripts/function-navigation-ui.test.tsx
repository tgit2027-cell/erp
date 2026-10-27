import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import App from "../src/App";
import { navigationContent } from "./navigation-content-contract.mjs";
import { coreSections, sidebarSections } from "../src/sidebar-destinations";
import { parseWorkspaceHash, routeIsAvailable } from "../src/workspace-hash";
import FinanceStatusPage, { financeReviewDestinations } from "../src/FinanceStatusPage";

const originalFetch = globalThis.fetch;
const unit = { id: 5, code: "TEST", name: "TEST ONLY unit", theme_palette: "tivoli" };
let root: ReturnType<typeof createRoot>;
let host: HTMLDivElement;
let calls: { path: string; method: string; body?: string }[];
let saved: { destination: string; label: string }[];
let profile: { is_staff: boolean; is_superuser: boolean; can_manage_report_panels: boolean };
let allowed: { destination: string; label: string }[];
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { "Content-Type": "application/json" },
});
const flush = async () => act(async () => { await new Promise(setImmediate); });
const click = async (element: Element | null) => {
  assert.ok(element, "Expected a rendered control");
  await act(async () => element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })));
  await flush();
};
const keyFor = (section: string, child: string) => `${section}.${child}`;

test("Finance status explains shared versus isolated availability without lab links, forms or requests", async () => {
  window.history.replaceState(null, "", `${window.location.pathname}#/finance/main`);
  await act(async () => root.render(<App />)); await flush(); await flush();
  const page = host.querySelector('[data-workspace-view="finance/main"]')!;
  assert.ok(page);
  assert.match(page.textContent ?? "", /STATUS ONLY.*NOT OPERATIONAL FINANCE/);
  assert.match(page.textContent ?? "", /Implemented only in the isolated Finance lab/);
  assert.match(page.textContent ?? "", /Schema readiness is not permission/);
  assert.equal(page.querySelectorAll("form,input,select,textarea").length, 0);
  assert.equal(page.querySelectorAll('a[href*="lab"],a[href*="finance-draft"]').length, 0);
  assert.equal(page.querySelector('a[href="#/governance/report-panels"]'), null);
  assert.ok(calls.every(call => call.method === "GET"));
  assert.ok(calls.every(call => !/lab|finance/.test(call.path)));
  assert.equal(host.querySelector(".planned-panel"), null);
});

test("Finance contextual destinations all use existing ordinary-user routes and preserve denied states", async () => {
  const access = { isStaff: false, isSuperuser: false, canManageReportPanels: false };
  for (const destination of financeReviewDestinations) {
    assert.equal(routeIsAvailable(destination, access), true);
  }
  window.history.replaceState(null, "", `${window.location.pathname}#/finance/main`);
  await act(async () => root.render(<App />)); await flush(); await flush();
  await click(host.querySelector('[data-workspace-view="finance/main"] a[href="#/retail/collections"]'));
  assert.equal(window.location.hash, "#/retail/collections");
  assert.equal(host.querySelector(".page-heading h1")?.textContent, "Collection follow-up");
  assert.ok(calls.every(call => call.method === "GET"));
  assert.match(host.textContent ?? "", /denied|unavailable/i);
  assert.doesNotMatch(host.textContent ?? "", /TEST_ONLY_PRIVATE_COST/);
});

test("existing panel-manager authority gets a contextual link without needing staff or gaining data rights", async () => {
  profile.can_manage_report_panels = true;
  window.history.replaceState(null, "", `${window.location.pathname}#/finance/main`);
  await act(async () => root.render(<App />)); await flush(); await flush();
  assert.equal(profile.is_staff, false);
  const page = host.querySelector('[data-workspace-view="finance/main"]')!;
  assert.ok(page.querySelector('a[href="#/governance/report-panels"]'));
  assert.match(page.textContent ?? "", /panel visibility only, not Finance or report-data authority/);
  await click(page.querySelector('a[href="#/governance/report-panels"]'));
  assert.equal(host.querySelector(".page-heading h1")?.textContent, "Manage report panels");
  assert.ok(calls.every(call => call.method === "GET"));
});

test("status does not infer manager rights and removes its link when the existing flag changes", async () => {
  await act(async () => root.render(<FinanceStatusPage canManageReportPanels onNavigate={() => {}} />));
  assert.ok(host.querySelector('a[href="#/governance/report-panels"]'));
  await act(async () => root.render(<FinanceStatusPage canManageReportPanels={false} onNavigate={() => {}} />));
  assert.equal(host.querySelector('a[href="#/governance/report-panels"]'), null);
  assert.equal(calls.length, 0);
});

test("status links delegate ordinary navigation to the existing safety callback rather than changing location", async () => {
  window.history.replaceState(null, "", `${window.location.pathname}#/finance/main`);
  const navigated: string[] = [];
  await act(async () => root.render(<FinanceStatusPage canManageReportPanels={false}
    onNavigate={(section, child) => navigated.push(`${section}/${child}`)} />));
  await click(host.querySelector('a[href="#/retail/quote"]'));
  assert.deepEqual(navigated, ["retail/quote"]);
  assert.equal(window.location.hash, "#/finance/main", "A refusing navigation guard can keep the current screen.");
  assert.equal(calls.length, 0);
});

test("unavailable organization retains the existing workspace block without introducing Finance reads", async () => {
  const ordinaryFetch = globalThis.fetch;
  globalThis.fetch = async (input, options) => String(input).includes("/api/organization/")
    ? response({ detail: "TEST_ONLY scope denied", unit_cost: "TEST_ONLY_PRIVATE_COST" }, 403)
    : ordinaryFetch(input, options);
  window.history.replaceState(null, "", `${window.location.pathname}#/finance/main`);
  await act(async () => root.render(<App />)); await flush(); await flush();
  assert.equal(host.querySelector("#finance-status-title"), null);
  assert.match(host.textContent ?? "", /denied|unavailable|context/i);
  assert.doesNotMatch(host.textContent ?? "", /TEST_ONLY_PRIVATE_COST/);
  assert.ok(calls.every(call => call.method === "GET"));
});

beforeEach(() => {
  window.history.replaceState(null, "", window.location.pathname);
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  calls = []; saved = [];
  profile = { is_staff: false, is_superuser: false, can_manage_report_panels: false };
  allowed = [
    ...coreSections.map(({ id, name }) => ({ destination: id, label: name })),
    ...sidebarSections(profile).flatMap((section) => section.children.filter((child) => child.id !== "main" && child.favoriteable !== false).map((child) => ({
      destination: keyFor(section.id, child.id), label: child.label,
    }))),
  ];
  globalThis.fetch = async (input, options = {}) => {
    const url = new URL(String(input), "https://synthetic.invalid");
    const method = options.method ?? "GET";
    calls.push({ path: url.pathname, method, body: options.body as string | undefined });
    if (url.pathname === "/api/auth/csrf/") return response({ csrfToken: "TEST_ONLY" });
    if (url.pathname === "/api/auth/me/") return response({
      authenticated: true, username: "TEST_ONLY_FUNCTIONS", ...profile,
      is_staff: profile.is_staff, is_superuser: profile.is_superuser,
      can_manage_report_panels: profile.can_manage_report_panels,
    });
    if (url.pathname === "/api/organization/") return response({
      company: { name: "TEST ONLY company", code: "TEST" }, units: [unit], selected_unit: unit, locations: [],
    });
    if (url.pathname === "/api/favorites/" && method === "POST") {
      const { destination } = JSON.parse(String(options.body));
      const item = allowed.find((choice) => choice.destination === destination);
      assert.ok(item, `Favorite write must use an advertised key: ${destination}`);
      saved = [...saved.filter((entry) => entry.destination !== destination), item];
      return response({ favorite: item }, 201);
    }
    if (url.pathname.startsWith("/api/favorites/") && method === "DELETE") {
      const destination = decodeURIComponent(url.pathname.split("/").at(-2) ?? "");
      saved = saved.filter((item) => item.destination !== destination);
      return new Response(null, { status: 204 });
    }
    if (url.pathname === "/api/favorites/") return response({ available_destinations: allowed, favorites: saved });
    if (url.pathname === "/api/stock-balances/") return response({
      results: [], pagination: { page: 1, page_size: 50, total_count: 0, total_pages: 1, previous_page: null, next_page: null },
    });
    return response({ detail: "TEST_ONLY unavailable" }, 403);
  };
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove(); globalThis.fetch = originalFetch;
  window.history.replaceState(null, "", window.location.pathname);
});

test("destination catalog has unique <=32-character flattened keys and exactly mirrors staff/superuser gates", () => {
  for (const access of [
    { isStaff: false, isSuperuser: false, canManageReportPanels: false },
    { isStaff: true, isSuperuser: false, canManageReportPanels: false },
    { isStaff: false, isSuperuser: false, canManageReportPanels: true },
    { isStaff: true, isSuperuser: true, canManageReportPanels: true },
  ]) {
    const sections = sidebarSections(access);
    const keys = sections.flatMap((section) => [
      section.id,
      ...section.children.filter((child) => child.id !== "main").map((child) => keyFor(section.id, child.id)),
    ]);
    assert.equal(new Set(keys).size, keys.length, "Favorites API catalog keys are unique");
    assert.ok(keys.every((key) => key.length <= 32), "Favorite keys fit the backend max_length=32");
    assert.equal(sections.find((section) => section.id === "procurement")?.children.some((item) => item.id === "suppliers"), access.isStaff);
    assert.equal(sections.find((section) => section.id === "governance")?.children.some((item) => item.id === "report-panels"), access.canManageReportPanels);
    assert.equal(sections.find((section) => section.id === "governance")?.children.some((item) => item.id === "capacity-admin"), access.isSuperuser);
    assert.equal(sections.find((section) => section.id === "governance")?.children.some((item) => item.id === "authority"), false);
    assert.equal(sections.find((section) => section.id === "reports")?.children.some((item) => item.id === "panels"), false);
  }
});

test("all 45 permission-dependent children render their focused function or explicit unavailable state, with matching titles", async () => {
  profile = { is_staff: true, is_superuser: true, can_manage_report_panels: true };
  await act(async () => root.render(<App />)); await flush();
  const entries = sidebarSections({ isStaff: true, isSuperuser: true, canManageReportPanels: true })
    .flatMap((section) => section.children.map((child) => ({ route: `${section.id}/${child.id}`, label: child.label })));
  assert.equal(entries.length, 49);
  assert.deepEqual(entries.map(({ route }) => route).sort(), Object.keys(navigationContent).sort());
  const shown = (node: Element) => {
    for (let current: Element | null = node; current; current = current.parentElement) {
      if (current.hasAttribute("hidden")) return false;
      if (current.tagName === "DETAILS" && !current.hasAttribute("open")
        && !current.querySelector(":scope > summary")?.contains(node)) return false;
    }
    return true;
  };
  for (const { route, label } of entries) {
    window.history.pushState(null, "", `${window.location.pathname}#/${route}`);
    await act(async () => window.dispatchEvent(new window.PopStateEvent("popstate")));
    await flush(); await flush();
    assert.equal(host.querySelector(".page-heading h1")?.textContent, label, route);
    assert.equal(host.querySelector(`[data-workspace-link="${route}"]`)?.getAttribute("aria-current"), "page", route);
    const witness = navigationContent[route as keyof typeof navigationContent];
    assert.ok([...host.querySelectorAll(witness)].some(shown), `${route}: focused content ${witness}`);
    if (route !== "purchasing/transactions") {
      for (const exclusive of ["#pw-receipt-title", "#pw-commitment-create-title",
        "#pw-commitment-progress-title", "#pw-create-quote-title", "#pw-quote-actions-title", ".packing-preview"]) {
        assert.equal([...host.querySelectorAll(exclusive)].some(shown), witness === exclusive,
          `${route}: unrelated receiving/action heading or form ${exclusive}`);
      }
    }
    const innerHeading = host.querySelector("#purchasing-workbench-title");
    if (!route.endsWith("/main") && innerHeading && shown(innerHeading)) {
      assert.equal(innerHeading.textContent, label, route);
    }
    if (route === "finance/main") {
      assert.match(host.querySelector('[data-workspace-view="finance/main"]')?.textContent ?? "", /STATUS ONLY.*NOT OPERATIONAL FINANCE/);
    }
    if (route === "hr/main") {
      assert.match(host.querySelector(".planned-panel")?.textContent ?? "", /PLANNED.*NOT IMPLEMENTED/);
    }
  }
  assert.ok(calls.every((call) => call.method === "GET"), "Navigation never submits a transaction or grant");
});

test("focused quote and procurement headings identify the selected function, not a different shared-workbench function", async () => {
  await act(async () => root.render(<App />)); await flush();
  for (const [route, title] of [
    ["retail/quote", "Create a quote"],
    ["procurement/receipt", "Prepare imported-goods receipt"],
    ["procurement/commitments", "Create purchase commitment"],
    ["inventory/packing", "Packing-list preview"],
  ]) {
    window.history.pushState(null, "", `${window.location.pathname}#/${route}`);
    await act(async () => window.dispatchEvent(new window.PopStateEvent("popstate"))); await flush();
    assert.equal(host.querySelector(".page-heading h1")?.textContent, title);
    assert.equal(host.querySelector("#purchasing-workbench-title")?.textContent, title,
      "Visible shared-component title must describe this child");
  }
});

test("legacy URLs normalize to focused canonical pages and preserve history function selection", async () => {
  assert.deepEqual(parseWorkspaceHash("#/reports/panels"), { section: "governance", child: "report-panels" });
  assert.deepEqual(parseWorkspaceHash("#/governance/authority"), { section: "governance", child: "main" });
  assert.deepEqual(parseWorkspaceHash("#/inventory/receiving"), { section: "inventory", child: "receipt" });
  assert.deepEqual(parseWorkspaceHash("#/delivery/registers"), { section: "delivery", child: "quotes" });
  window.history.replaceState(null, "", `${window.location.pathname}#/inventory/receiving`);
  await act(async () => root.render(<App />)); await flush();
  assert.equal(window.location.hash, "#/inventory/receipt");
  assert.ok(host.querySelector('[data-workspace-view="inventory/receipt"]'));
  await act(async () => { window.location.hash = "#/delivery/registers"; await new Promise((resolve) => setTimeout(resolve, 10)); });
  await flush();
  assert.equal(window.location.hash, "#/delivery/quotes");
  assert.ok(host.querySelector('[data-workspace-view="delivery/quotes"]'));
  await act(async () => { window.history.back(); await new Promise((resolve) => setTimeout(resolve, 20)); }); await flush();
  assert.equal(window.location.hash, "#/inventory/receipt");
  assert.ok(host.querySelector('[data-workspace-view="inventory/receipt"]'));
});

test("main function Favorites retain legacy section keys and My Page reopens that default without a duplicate key", async () => {
  await act(async () => root.render(<App />)); await flush();
  const inventorySectionStar = host.querySelector('.section-navigation-group:has([data-workspace-link="inventory/main"]) .section-navigation-heading .favorite-toggle');
  assert.ok(inventorySectionStar, [...host.querySelectorAll(".favorite-toggle")].map((star) => star.getAttribute("aria-label")).join(" | "));
  await click(inventorySectionStar);
  assert.equal(JSON.parse(calls.find((call) => call.method === "POST")?.body ?? "{}").destination, "inventory");
  assert.deepEqual(saved.map((item) => item.destination), ["inventory"]);
  await click(host.querySelector(".my-page-link"));
  assert.ok([...host.querySelectorAll<HTMLElement>(".section-navigation-children")].every((list) => list.hidden),
    "My Page closes stale section flyouts instead of covering favorite links");
  assert.match(host.querySelector(".my-page-view")?.textContent ?? "", /Current inventory ledger snapshot/);
  await click(host.querySelector(".my-favorite-open"));
  assert.equal(window.location.hash, "#/inventory/main");
  assert.equal(host.querySelector('[data-workspace-link="inventory/main"]')?.getAttribute("aria-current"), "page");
  await click(host.querySelector(".my-page-link"));
  await click(host.querySelector(".my-page-view .my-favorite-row .favorite-toggle"));
  assert.deepEqual(saved, []);
  assert.ok(calls.some((call) => call.path === "/api/favorites/inventory/"));
});

test("an individual implemented child favorite persists its flattened key and reopens that function", async () => {
  await act(async () => root.render(<App />)); await flush();
  await click(host.querySelector('[aria-label="Add Permitted locations to favorites"]'));
  assert.equal(JSON.parse(calls.find((call) => call.method === "POST")?.body ?? "{}").destination, "inventory.locations");
  await click(host.querySelector(".my-page-link"));
  await click(host.querySelector(".my-favorite-open"));
  assert.equal(window.location.hash, "#/inventory/locations");
  assert.equal(host.querySelector('[data-workspace-link="inventory/locations"]')?.getAttribute("aria-current"), "page");
  assert.equal(host.querySelector(".favorite-shortcut .nav-item")?.getAttribute("aria-current"), "page",
    "A flattened Favorite shortcut marks itself current only on its exact focused child.");
});

test("every allowlisted non-main child Favorite pins, opens its exact route, marks current and unpins", async () => {
  await act(async () => root.render(<App />)); await flush();
  const choices = allowed.filter((item) => item.destination.includes("."));
  assert.ok(choices.length > 15, "The ordinary synthetic catalog exercises all workspaces");
  for (const choice of choices) {
    const [section, child] = choice.destination.split(".");
    const route = `${section}/${child}`;
    const link = host.querySelector<HTMLButtonElement>(`[data-workspace-link="${route}"]`);
    assert.ok(link, `Catalog child must be rendered: ${route}`);
    const group = link.closest(".section-navigation-group");
    const disclosure = group?.querySelector<HTMLButtonElement>(".nav-section-toggle");
    assert.ok(disclosure);
    if (disclosure.getAttribute("aria-expanded") !== "true") await click(disclosure);
    await click(link);
    const star = link.closest(".nav-child-row")?.querySelector(".favorite-toggle");
    assert.ok(star, `Available child must have a Favorite star: ${route}`);
    await click(star);
    assert.equal(JSON.parse(calls.filter((call) => call.method === "POST").at(-1)?.body ?? "{}").destination, choice.destination);
    await click(host.querySelector(".my-page-link"));
    await click(host.querySelector(".my-favorite-open"));
    assert.equal(window.location.hash, `#/${route}`);
    assert.equal(host.querySelector(`.favorite-shortcut .nav-item[aria-current="page"] > span:last-child`)?.textContent?.trim(), choice.label);
    await click(host.querySelector(".my-page-link"));
    await click(host.querySelector(".my-page-view .favorite-toggle"));
    assert.equal(saved.length, 0, `Favorite removed after round-trip: ${route}`);
  }
});

test("panel-manager denial and hidden destination gates fail closed while catalog stars remain allowlisted", async () => {
  await act(async () => root.render(<App />)); await flush();
  assert.equal(host.querySelector('[aria-label="Add Manage report panels to favorites"]'), null);
  window.history.pushState(null, "", `${window.location.pathname}#/governance/report-panels`);
  await act(async () => window.dispatchEvent(new window.PopStateEvent("popstate"))); await flush();
  assert.ok(host.querySelector("[data-workspace-unavailable]"));
  assert.equal(calls.some((call) => call.path.startsWith("/api/governance/")), false);
  assert.equal(routeIsAvailable({ section: "governance", child: "report-panels" }, profile), false);
});
