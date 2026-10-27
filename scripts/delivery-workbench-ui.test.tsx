import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, Profiler } from "react";
import { createRoot } from "react-dom/client";
import PurchasingWorkbench from "../src/PurchasingWorkbench";
import { sidebarSections } from "../src/sidebar-destinations";

const nativeFetch = globalThis.fetch;
let root, host, calls, posts, quoteSnapshots, quotePaymentAmounts, deniedQuoteUnits, deniedGovernance, deliveryRole, warnings, originalError, originalWarn, commits;
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const page = (rows = []) => ({ rows, pagination: {
  page: 1, page_size: 20, total_count: rows.length, total_pages: 1, previous_page: null, next_page: null,
} });
function quote(unit) {
  const id = unit === 1 ? 101 : 202;
  const status = quoteSnapshots.get(id) ?? "confirmed";
  return {
    quote_id: id, business_unit_id: unit, kind: "stock", status, customer_id: unit * 10,
    discount_percent: "0.00", total_list_value: "87.50", total_discount: "0.00",
    total_quote_value: "87.50", payment_plan: "full", downpayment_percent: null,
    special_order_bands_configured: false,
    lines: [{
      quote_line_id: id * 10, variant_id: unit * 100, quantity: "2.000",
      selling_price_snapshot: "43.75", price_revision_snapshot: 1,
      discount_percent: "0.00", discount_amount: "0.00", net_unit_price: "43.75",
      net_total: "87.50", specifications: null,
    }],
    payments: [
      ...(status === "delivered" ? [{ payment_id: 1, kind: "deposit", amount: "66.00",
        non_returnable_amount: "0.00", simulated_reference: "TEST_ONLY_DEPOSIT", created_at: "2031-04-01T10:00:00Z" }] : []),
      ...(quotePaymentAmounts.get(id) ? [{ payment_id: 2, kind: "balance", amount: quotePaymentAmounts.get(id),
        non_returnable_amount: "0.00", simulated_reference: "TEST_ONLY_BALANCE", created_at: "2031-04-06T12:00:00Z" }] : []),
    ],
    approvals: status === "delivered" ? [{
      approval_id: 9, kind: "post_handover", approver_id: 77, role: "ceo",
      reason: "TEST ONLY approved deferred balance", details: null, approved_at: "2031-04-05T08:00:00Z",
    }] : [],
    holds: [], events: [],
    delivery_progress: {
      ready_at: status === "confirmed" ? null : "2031-04-05T09:07:06Z",
      delivered_at: status === "delivered" ? "2031-04-06T11:12:13Z" : null,
      lines: [{
        quote_line_id: id * 10, variant_id: unit * 100,
        ordered_quantity: "2.0000", reserved_to_date_quantity: "1.2500",
        delivered_quantity: status === "delivered" ? "1.0000" : "0.0000",
        remaining_quantity: status === "delivered" ? "1.0000" : "2.0000",
      }],
    },
  };
}
async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}
async function renderUnit(unit, isStaff = true) {
  await act(async () => root.render(
    <Profiler id="delivery" onRender={() => commits.push({ unit, text: host.textContent ?? "" })}>
      <PurchasingWorkbench
        mode="delivery"
        selectedUnitId={unit}
        selectedUnitName={`TEST ONLY Unit ${unit}`}
        isStaff={isStaff}
        username="TEST_ONLY_delivery_operator"
        csrfToken="TEST_ONLY_CSRF"
        locations={[]}
        onOpenReports={() => {}}
      />
    </Profiler>,
  ));
  await flush();
}
async function lookup(id) {
  const input = host.querySelector("#pw-quote-id");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, String(id));
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => host.querySelector(".pw-quote-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  ));
  await flush();
}
async function click(button) {
  assert.ok(button, "Expected an available rendered action");
  assert.equal(button.disabled, false);
  await act(async () => button.click());
  await flush();
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  calls = []; posts = []; quoteSnapshots = new Map(); quotePaymentAmounts = new Map();
  deniedQuoteUnits = new Set(); deniedGovernance = new Set();
  deliveryRole = "ceo"; commits = []; warnings = [];
  originalError = console.error; originalWarn = console.warn;
  console.error = console.warn = (...args) => warnings.push(args.map(String).join(" "));
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "https://delivery.test.invalid");
    calls.push({ url, ...options });
    assert.equal(options.credentials, "same-origin");
    if (options.method === "POST") {
      const post = { url, body: JSON.parse(options.body), options };
      posts.push(post);
      const id = Number(url.pathname.match(/\/quotes\/(\d+)\//)?.[1]);
      if (url.pathname.endsWith("/payments/")) {
        quotePaymentAmounts.set(id, post.body.amount);
      } else {
        quoteSnapshots.set(id, url.pathname.endsWith("/ready/") ? "ready" : "delivered");
      }
      return json({ success: true });
    }
    assert.equal(options.method, "GET");
    if (url.pathname === "/api/purchasing/quotes/") {
      const unit = Number(url.searchParams.get("business_unit_id"));
      if (url.searchParams.get("page_size") === "1") return json(page());
      if (unit === 2) return json(page());
      return json(page([{ quote_id: unit === 1 ? 101 : 202, kind: "stock", status: quote(unit).status,
        customer_id: unit * 10, total_quote_value: "87.50", currency: "QAR", created_at: "2031-04-01T10:00:00Z" }]));
    }
    if (/^\/api\/purchasing\/quotes\/\d+\/$/.test(url.pathname)) {
      const unit = Number(url.searchParams.get("business_unit_id"));
      if (deniedQuoteUnits.has(unit)) return json({ detail: "TEST ONLY quote detail denied." }, 403);
      return json(quote(unit));
    }
    if (url.pathname === "/api/purchasing/storage-charges/") return json(page());
    if (/^\/api\/governance\/purchasing\/\d+\/$/.test(url.pathname)) {
      const unit = Number(url.pathname.split("/").at(-2));
      if (deniedGovernance.has(unit)) return json({ detail: "TEST ONLY configuration denied." }, 403);
      return json({
        test_only_label: "TEST ONLY synthetic configuration",
        business_unit: { id: unit, code: `TEST${unit}`, name: `TEST ONLY Unit ${unit}` },
        role_assignments: [{ id: 1, user_id: 1, username: "TEST_ONLY_delivery_operator", role: deliveryRole }],
        current_prices: [], work_calendar: { is_configured: false, closed_weekdays: [], closed_dates: [] },
        storage_exception_approver_roles: [], special_order_value_bands: [],
        special_order_bands_used_for_approval: false, special_order_bands_note: "TEST ONLY",
      });
    }
    if (["/api/reports/inventory/reservations/", "/api/reports/sales/deliveries/"].includes(url.pathname)) {
      return json(page());
    }
    throw new Error(`Unexpected HTTP mock: ${options.method} ${url.pathname}`);
  };
});

afterEach(async () => {
  try { await act(async () => root.unmount()); }
  finally {
    host.remove();
    globalThis.fetch = nativeFetch;
    console.error = originalError; console.warn = originalWarn;
  }
  assert.deepEqual(warnings, []);
});

test("delivery mode presents exact read-only progress and existing readiness/handover without procurement creation controls", async () => {
  await renderUnit(1);
  assert.ok(host.querySelector("#purchasing-workbench-title").textContent.includes("Delivery"));
  assert.equal(host.querySelector("#pw-create-quote-title"), null);
  assert.equal(host.querySelector("#pw-receipt-title"), null);
  const returnLink = sidebarSections({ isStaff: true, isSuperuser: false, canManageReportPanels: false })
    .find((section) => section.id === "delivery").children.find((child) => child.id === "returns");
  assert.equal(returnLink.id, "returns", "After-sales uses a focused route rather than a scroll selector.");
  assert.equal(host.querySelector("#pw-sales-return-summary")?.tagName, "SUMMARY");
  assert.equal(host.textContent.includes("Record test payment"), false);
  assert.deepEqual([...host.querySelectorAll(".pr-tabs > button")].map((button) => button.textContent), [
    "Quotes", "Storage charges", "Reservations", "Deliveries",
  ]);
  assert.equal(host.querySelector(".pr-tabs button[aria-current='page']").textContent, "Quotes");
  assert.equal(calls.some(({ url }) => /commitments|counterparties\/?$|receipts/.test(url.pathname)), false);

  await lookup(101);
  const progress = host.querySelector('[aria-label="Read-only delivery progress"]');
  assert.ok(progress);
  assert.match(progress.textContent, /Ready at/);
  assert.match(progress.textContent, /Delivered at/);
  assert.match(progress.textContent, /Remaining \(ordered minus delivered\)/);
  assert.match(progress.textContent, /Ready at—/);
  assert.match(progress.textContent, /2\.0000/);
  assert.match(progress.textContent, /1\.2500/);
  assert.match(progress.textContent, /0\.0000/);
  assert.match(progress.textContent, /2\.0000/);
  assert.doesNotMatch(progress.textContent, /QAR|cost|price/i);
  assert.ok([...host.querySelectorAll(".pw-quote-action-grid form")]
    .some((form) => form.textContent.includes("Exceptional post-handover balance")));

  await click([...host.querySelectorAll("button")].find((button) => button.textContent === "Record readiness"));
  assert.equal(posts.at(-1).url.pathname, "/api/purchasing/quotes/101/ready/");
  assert.equal(posts.at(-1).body.business_unit_id, undefined);
  assert.ok(host.querySelector(".pw-quote").textContent.includes("ready"));
  assert.match(host.querySelector('[aria-label="Read-only delivery progress"]').textContent, /2031-04-05T09:07:06Z/);
  await click([...host.querySelectorAll("button")].find((button) => button.textContent === "Record delivery"));
  assert.equal(posts.at(-1).url.pathname, "/api/purchasing/quotes/101/deliver/");
  assert.ok(host.querySelector(".pw-quote").textContent.includes("delivered"));
  assert.match(host.querySelector('[aria-label="Read-only delivery progress"]').textContent, /2031-04-06T11:12:13Z/);
  assert.match(host.querySelector('[aria-label="Read-only delivery progress"]').textContent, /1\.0000/);
  assert.equal(posts.length, 2);
  assert.equal(calls.some(({ url }) => /commitments|counterparties|receipts|payments|post-handover/.test(url.pathname)), false);
});

test("delivery quote lookup denial is composed and a BU switch clears previous quote progress before new content", async () => {
  await renderUnit(1);
  await lookup(101);
  assert.ok(host.querySelector(".pw-quote").textContent.includes("#101"));
  await renderUnit(2);
  assert.equal(host.querySelector("#pw-quote-id").value, "");
  assert.equal(host.querySelector(".pw-quote"), null);
  assert.ok(commits.some((commit) => commit.unit === 2 && !commit.text.includes("#101")));
  await lookup(202);
  assert.ok(host.querySelector(".pw-quote").textContent.includes("#202"));
  assert.equal(host.querySelector(".pw-quote").textContent.includes("#101"), false);
  assert.ok(host.querySelector(".pr-empty"));
});

test("delivered quote with existing post-handover approval can record quote balance and refresh without reopening delivery", async () => {
  quoteSnapshots.set(101, "delivered");
  await renderUnit(1);
  await lookup(101);
  const quoteProgress = () => host.querySelector('[aria-label="Read-only delivery progress"]').textContent;
  const before = quoteProgress();
  assert.match(before, /Delivered at2031-04-06T11:12:13Z/);
  assert.match(before, /1\.0000/);
  assert.equal([...host.querySelectorAll("button")].some((button) => button.textContent === "Record delivery"), false);
  assert.ok(calls.some(({ url }) => url.pathname === "/api/purchasing/storage-charges/"
    && url.searchParams.get("quote_id") === "101"));

  const paymentForm = [...host.querySelectorAll(".pw-quote-action-grid form")]
    .find((form) => form.textContent.includes("Record simulated payment"));
  assert.ok(paymentForm);
  const amount = paymentForm.querySelector('input[type="number"]');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(amount, "21.50");
    amount.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await flush();
  const submit = [...paymentForm.querySelectorAll("button")].find((button) => button.textContent === "Record test payment");
  assert.equal(submit.disabled, false, "Existing quote balance is enabled by the authorized post-handover approval");
  await act(async () => paymentForm.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();

  assert.equal(posts.at(-1).url.pathname, "/api/purchasing/quotes/101/payments/");
  assert.deepEqual(posts.at(-1).body, { amount: "21.50", method: "simulated_cash", purpose: "quote" });
  const refreshed = host.querySelector(".pw-quote");
  assert.ok(refreshed.textContent.includes("delivered"));
  assert.match(quoteProgress(), /Delivered at2031-04-06T11:12:13Z/);
  assert.match(quoteProgress(), /1\.0000/);
  assert.equal([...host.querySelectorAll("button")].some((button) => button.textContent === "Record delivery"), false);
  assert.equal(posts.length, 1);
});

test("denied delivery quote lookup shows unavailable state without exposing progress", async () => {
  deniedQuoteUnits.add(2);
  await renderUnit(2);
  await lookup(202);
  assert.equal(host.querySelector(".pw-quote"), null);
  assert.match(host.querySelector(".pw-quote-card [role='alert']").textContent, /TEST ONLY quote detail denied/);
  assert.equal(host.querySelector('[aria-label="Read-only delivery progress"]'), null);
});

test("delivery post-handover approval retains the existing BU GM/CEO role gate", async () => {
  deliveryRole = "salesperson";
  await renderUnit(1);
  await lookup(101);
  const approvalForm = [...host.querySelectorAll(".pw-quote-action-grid form")]
    .find((form) => form.textContent.includes("Exceptional post-handover balance"));
  assert.ok(approvalForm);
  assert.equal([...approvalForm.querySelectorAll("button")].at(-1).disabled, true);
  assert.match(approvalForm.textContent, /Only an assigned BU GM or CEO/);
  assert.equal(posts.length, 0);
});

test("nonstaff delivery does not request staff governance configuration and preserves unavailable post-handover gating", async () => {
  await renderUnit(1, false);
  await lookup(101);
  assert.equal(calls.some(({ url }) => url.pathname.startsWith("/api/governance/")), false);
  const approvalForm = [...host.querySelectorAll(".pw-quote-action-grid form")]
    .find((form) => form.textContent.includes("Exceptional post-handover balance"));
  assert.ok(approvalForm);
  assert.equal([...approvalForm.querySelectorAll("button")].at(-1).disabled, true);
  assert.match(approvalForm.textContent, /cannot read staff-only price and work-calendar configuration/);
  assert.equal(posts.length, 0);
});

test("denied delivery configuration preserves the existing post-handover unavailable reason", async () => {
  deniedGovernance.add(1);
  await renderUnit(1);
  await lookup(101);
  const approvalForm = [...host.querySelectorAll(".pw-quote-action-grid form")]
    .find((form) => form.textContent.includes("Exceptional post-handover balance"));
  assert.ok(approvalForm);
  assert.equal([...approvalForm.querySelectorAll("button")].at(-1).disabled, true);
  assert.match(approvalForm.textContent, /Staff configuration could not be verified/);
  assert.equal(posts.length, 0);
});
