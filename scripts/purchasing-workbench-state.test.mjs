import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  getPurchasingConfigurationReadiness,
  getPurchasingActionAvailability,
  getPurchasingPagerState,
  getPurchasingReadsideState,
  getPurchasingReadsideTabs,
  getPurchasingWorkbenchCapabilities,
  getQuoteLookupState,
  isPurchasingReadsideDeniedStatus,
  shouldRequestPurchasingCounterparties,
  shouldLoadPurchasingConfiguration,
} from "../src/purchasing-workbench-state.mjs";

test("ordinary users never load the staff-only purchasing configuration endpoint", () => {
  assert.equal(shouldLoadPurchasingConfiguration(false), false);
  assert.equal(shouldLoadPurchasingConfiguration(undefined), false);
  assert.equal(shouldLoadPurchasingConfiguration(true), true);
});

test("empty demo configuration is shown as missing or intentionally unconfigured", () => {
  const readiness = getPurchasingConfigurationReadiness({
    role_assignments: [],
    current_prices: [],
    work_calendar: { is_configured: true },
    storage_exception_approver_roles: [],
    special_order_value_bands: [],
  });

  assert.deepEqual(
    Object.fromEntries(readiness.map((item) => [item.key, item.status])),
    {
      "approval-roles": "not-configured",
      "approved-prices": "not-configured",
      "work-calendar": "configured",
      counterparties: "staff-only",
      "special-order-value-bands": "unconfigured",
      "storage-exception-approvers": "not-configured",
    },
  );
});

test("readiness uses only active bands and the API's configured flags", () => {
  const readiness = getPurchasingConfigurationReadiness({
    role_assignments: [{ role: "manager" }],
    current_prices: [{ amount: "20.00" }],
    work_calendar: { is_configured: false },
    special_order_value_bands: [
      { is_active: false },
      { is_active: true },
    ],
    storage_exception_approver_roles: ["bu_gm"],
  });
  const statuses = Object.fromEntries(readiness.map((item) => [item.key, item.status]));

  assert.equal(statuses["approval-roles"], "configured");
  assert.equal(statuses["approved-prices"], "configured");
  assert.equal(statuses["work-calendar"], "not-configured");
  assert.equal(statuses["special-order-value-bands"], "present-not-used");
  assert.equal(statuses["storage-exception-approvers"], "configured");
});

test("quote lookup states distinguish idle, loading, denied/error and returned data", () => {
  assert.equal(getQuoteLookupState({ submitted: false }), "idle");
  assert.equal(getQuoteLookupState({ submitted: true, loading: true }), "loading");
  assert.equal(getQuoteLookupState({ submitted: true, error: "Access denied" }), "error");
  assert.equal(getQuoteLookupState({ submitted: true, quote: { quote_id: 7 } }), "ready");
  assert.equal(getQuoteLookupState({ submitted: true }), "empty");
});

test("workbench exposes the existing scoped read capabilities and gates writes by server-confirmed access", () => {
  assert.deepEqual(getPurchasingWorkbenchCapabilities(), {
    configurationRead: "staff-only",
    quoteRead: "server-authorized",
    receiptHistoryRead: "purchase-history-grant",
    storageHistoryRead: "create-order-grant",
    quoteListRead: "create-order-grant",
    reservationRead: "inventory-report-grant",
    deliveryRead: "sales-report-grant",
    counterpartiesRead: "staff-only",
    writesEnabled: true,
  });
});

test("transaction actions fail closed when order access, CSRF, configuration or counterparties are missing", () => {
  const denied = getPurchasingActionAvailability({
    orderAccess: "denied",
    isStaff: true,
    username: "demo_manager",
    csrfToken: "token",
    locations: [{ id: 1 }],
    counterparties: [{ party_type: "supplier" }, { party_type: "customer" }],
    counterpartyState: "ready",
    configuration: {
      role_assignments: [{ username: "demo_manager", role: "manager" }],
      current_prices: [{ variant_id: 1 }],
      work_calendar: { is_configured: true },
    },
    configurationState: "ready",
  });
  assert.equal(denied.receipt.enabled, false);
  assert.equal(denied.quote.enabled, false);
  assert.match(denied.receipt.reason, /did not confirm an active create_order grant/);

  const noConfiguration = getPurchasingActionAvailability({
    orderAccess: "ready",
    isStaff: false,
    username: "demo_sales",
    csrfToken: "token",
    locations: [{ id: 1 }],
    counterparties: [],
    counterpartyState: "idle",
  });
  assert.equal(noConfiguration.receipt.enabled, false);
  assert.match(noConfiguration.receipt.reason, /staff-only/);
  assert.equal(noConfiguration.quote.enabled, false);
  assert.match(noConfiguration.quote.reason, /quotation choices are not available/);
  assert.equal(noConfiguration.hold.enabled, false);
  assert.equal(noConfiguration.postHandover.enabled, false);

  const noCsrf = getPurchasingActionAvailability({
    orderAccess: "ready",
    isStaff: true,
    username: "demo_manager",
    csrfToken: "",
    locations: [{ id: 1 }],
    counterparties: [{ party_type: "supplier" }, { party_type: "customer" }],
    counterpartyState: "ready",
    configuration: {
      role_assignments: [{ username: "demo_manager", role: "manager" }],
      current_prices: [{ variant_id: 1 }],
      work_calendar: { is_configured: true },
    },
    configurationState: "ready",
  });
  assert.equal(noCsrf.receipt.enabled, false);
  assert.match(noCsrf.receipt.reason, /CSRF token/);
});

test("transaction actions use assigned roles and state, without changing access grants", () => {
  const configuration = {
    role_assignments: [{ username: "demo_manager", role: "manager" }],
    current_prices: [{ variant_id: 11, amount: "1000.00" }],
    work_calendar: { is_configured: true },
  };
  const quotationConfiguration = {
    role_assignments: [{ username: "demo_manager", role: "manager" }],
    current_prices: [{ variant_id: 11, amount: "1000.00", currency: "QAR" }],
    customers: [{ counterparty_id: 2, party_type: "customer" }],
    discount_ceiling: "20",
  };
  const counterparties = [
    { party_type: "supplier", counterparty_id: 1 },
    { party_type: "customer", counterparty_id: 2 },
  ];
  const common = {
    orderAccess: "ready",
    isStaff: true,
    username: "demo_manager",
    csrfToken: "session-csrf",
    locations: [{ id: 3 }],
    counterparties,
    counterpartyState: "ready",
    configuration,
    configurationState: "ready",
    quotationConfiguration,
    quotationConfigurationState: "ready",
  };

  const approved = getPurchasingActionAvailability({
    ...common,
    quote: { status: "approved", holds: [], approvals: [] },
  });
  assert.equal(approved.receipt.enabled, true);
  assert.equal(approved.quote.enabled, true);
  assert.equal(approved.hold.enabled, true);
  assert.equal(approved.payment.enabled, true);
  assert.equal(approved.discountApproval.enabled, false);
  assert.equal(approved.postHandover.enabled, false);

  const pendingDiscount = getPurchasingActionAvailability({
    ...common,
    quote: {
      status: "pending_approval",
      discount_percent: "20.00",
      holds: [],
      approvals: [],
    },
  });
  assert.equal(pendingDiscount.discountApproval.enabled, true);
  assert.equal(pendingDiscount.payment.enabled, false);
  assert.equal(pendingDiscount.delivery.enabled, false);

  const notReady = getPurchasingActionAvailability({
    ...common,
    quote: { status: "confirmed", holds: [], approvals: [] },
  });
  assert.equal(notReady.readiness.enabled, true);
  assert.equal(notReady.delivery.enabled, false);
  assert.equal(notReady.storageAccrual.enabled, false);

  const ready = getPurchasingActionAvailability({
    ...common,
    quote: { status: "ready", holds: [], approvals: [] },
    storageOutstandingAmount: 32,
  });
  assert.equal(ready.delivery.enabled, true);
  assert.equal(ready.storageAccrual.enabled, true);
  assert.equal(ready.storagePayment.enabled, true);
  assert.equal(ready.quoteBalancePayment.enabled, true);

  const delivered = getPurchasingActionAvailability({
    ...common,
    quote: {
      status: "delivered",
      holds: [],
      approvals: [{ kind: "post_handover" }],
      total_quote_value: "4000.00",
      payments: [{ kind: "deposit", amount: "3600.00" }],
    },
    storageOutstandingAmount: 32,
  });
  assert.equal(delivered.payment.enabled, true);
  assert.equal(delivered.quoteBalancePayment.enabled, true);
  assert.equal(delivered.storagePayment.enabled, true);

  const deliveredWithoutApproval = getPurchasingActionAvailability({
    ...common,
    quote: {
      status: "delivered",
      holds: [],
      approvals: [],
      total_quote_value: "4000.00",
      payments: [{ kind: "deposit", amount: "3600.00" }],
    },
  });
  assert.equal(deliveredWithoutApproval.quoteBalancePayment.enabled, false);
  assert.match(deliveredWithoutApproval.quoteBalancePayment.reason, /post-handover approval is required/);

  const deliveredPaid = getPurchasingActionAvailability({
    ...common,
    quote: {
      status: "delivered",
      holds: [],
      approvals: [{ kind: "post_handover" }],
      total_quote_value: "4000.00",
      payments: [
        { kind: "deposit", amount: "3600.00" },
        { kind: "balance", amount: "400.00" },
      ],
    },
  });
  assert.equal(deliveredPaid.quoteBalancePayment.enabled, false);
  assert.match(deliveredPaid.quoteBalancePayment.reason, /No unpaid quote balance remains/);

  const deferred = getPurchasingActionAvailability({
    ...common,
    configuration: {
      ...configuration,
      role_assignments: [{ username: "demo_manager", role: "ceo" }],
    },
    quote: { status: "confirmed", holds: [], approvals: [] },
  });
  assert.equal(deferred.postHandover.enabled, true);

  const ordinaryStaff = getPurchasingActionAvailability({
    ...common,
    username: "different_user",
    quote: { status: "approved", holds: [], approvals: [] },
  });
  assert.equal(ordinaryStaff.quote.enabled, false);
  assert.match(ordinaryStaff.quote.reason, /No current role assignment/);
});

test("read-side views distinguish idle, denied, empty, and populated responses", () => {
  assert.equal(getPurchasingReadsideState("idle"), "idle");
  assert.equal(getPurchasingReadsideState("loading"), "loading");
  assert.equal(getPurchasingReadsideState("unauthorized"), "unauthorized");
  assert.equal(getPurchasingReadsideState("error"), "error");
  assert.equal(getPurchasingReadsideState("ready", []), "empty");
  assert.equal(getPurchasingReadsideState("ready", [{ id: 1 }]), "populated");
  assert.equal(isPurchasingReadsideDeniedStatus(401), true);
  assert.equal(isPurchasingReadsideDeniedStatus(403), true);
  assert.equal(isPurchasingReadsideDeniedStatus(404), true);
  assert.equal(isPurchasingReadsideDeniedStatus(500), false);
  assert.equal(shouldRequestPurchasingCounterparties(false), false);
  assert.equal(shouldRequestPurchasingCounterparties(undefined), false);
  assert.equal(shouldRequestPurchasingCounterparties(true), true);
});

test("register navigation excludes staff-only destinations for ordinary users", () => {
  assert.deepEqual(
    getPurchasingReadsideTabs(false).map(({ id }) => id),
    ["receipts", "quotes", "storage", "reservations", "deliveries"],
  );
  assert.deepEqual(
    getPurchasingReadsideTabs(true).map(({ id }) => id),
    ["receipts", "quotes", "storage", "reservations", "deliveries", "counterparties"],
  );
});

test("empty and populated registers expose correct previous/next page controls", () => {
  assert.deepEqual(
    getPurchasingPagerState({
      total_count: 0,
      total_pages: 1,
      previous_page: null,
      next_page: null,
    }),
    {
      isEmpty: true,
      hasMultiplePages: false,
      canGoPrevious: false,
      canGoNext: false,
    },
  );
  assert.deepEqual(
    getPurchasingPagerState({
      total_count: 5,
      total_pages: 3,
      previous_page: null,
      next_page: 2,
    }),
    {
      isEmpty: false,
      hasMultiplePages: true,
      canGoPrevious: false,
      canGoNext: true,
    },
  );
  assert.deepEqual(
    getPurchasingPagerState({
      total_count: 5,
      total_pages: 3,
      previous_page: 2,
      next_page: 4,
    }),
    {
      isEmpty: false,
      hasMultiplePages: true,
      canGoPrevious: true,
      canGoNext: true,
    },
  );
  assert.deepEqual(
    getPurchasingPagerState({
      total_count: 5,
      total_pages: 3,
      previous_page: 3,
      next_page: null,
    }),
    {
      isEmpty: false,
      hasMultiplePages: true,
      canGoPrevious: true,
      canGoNext: false,
    },
  );
});

test("workbench retains accessible form, async feedback, table and motion controls", () => {
  const component = readFileSync(new URL("../src/PurchasingWorkbench.tsx", import.meta.url), "utf8");
  const transactions = readFileSync(new URL("../src/PurchasingTransactions.tsx", import.meta.url), "utf8");
  const readside = readFileSync(new URL("../src/PurchasingReadside.tsx", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../src/purchasing-workbench.css", import.meta.url), "utf8");
  const readsideStyles = readFileSync(new URL("../src/purchasing-readside.css", import.meta.url), "utf8");

  assert.match(component, /<label htmlFor="pw-quote-id">/);
  assert.match(component, /aria-describedby="pw-quote-help"/);
  assert.match(component, /role="alert"/);
  assert.match(component, /aria-label="Loading quote details"/);
  assert.match(component, /scope="col"/);
  assert.match(component, /shouldLoadPurchasingConfiguration\(isStaff\)/);
  assert.match(component, /method:\s*"GET"/);
  assert.doesNotMatch(component, /method:\s*"(POST|PUT|PATCH|DELETE)"/i);
  assert.match(component, /<PurchasingTransactions/);
  assert.match(transactions, /"X-CSRFToken": csrfToken/);
  assert.match(transactions, /"Idempotency-Key": key/);
  assert.match(transactions, /JSON\.stringify\(\[username, selectedUnitId, name, path, payload\]\)/);
  assert.match(transactions, /credentials:\s*"same-origin"/);
  assert.match(transactions, /\/api\/purchasing\/receipts\//);
  assert.match(transactions, /\/api\/purchasing\/quotes\//);
  for (const endpoint of [
    "/discount-approval/",
    "/holds/",
    "/payments/",
    "/post-handover-approval/",
    "/ready/",
    "/deliver/",
    "/storage-accrual/",
  ]) assert.ok(transactions.includes(endpoint), `Expected supported endpoint ${endpoint}`);
  assert.match(transactions, /availability\.receipt\.enabled/);
  assert.match(transactions, /availability\.quote\.enabled/);
  assert.match(transactions, /availability\.hold\.enabled/);
  assert.match(transactions, /availability\.postHandover\.enabled/);
  assert.match(transactions, /simulated_cash/);
  assert.match(transactions, /simulated_card/);
  assert.match(transactions, /simulated_bank/);
  assert.doesNotMatch(transactions, /@stripe\/|@paypal\//i);
  assert.doesNotMatch(transactions, /assign_approval_role|UserActionGrant|create_synthetic_counterparty/i);
  assert.match(transactions, /role="alert"/);
  assert.match(transactions, /role="status"/);
  assert.match(readside, /No synthetic records returned/);
  assert.match(readside, /The server did not authorize this/);
  assert.match(readside, /shouldRequestPurchasingCounterparties\(isStaff\)/);
  assert.match(readside, /aria-current=\{renderedTab === tab\.id \? "page" : undefined\}/);
  assert.match(readside, /getPurchasingReadsideTabs\(isStaff\)/);
  assert.match(readside, /getPurchasingPagerState\(pagination\)/);
  assert.match(readside, /aria-label="Previous page"/);
  assert.match(readside, /aria-label="Next page"/);
  assert.match(readside, /method:\s*"GET"/);
  assert.doesNotMatch(readside, /method:\s*"(POST|PUT|PATCH|DELETE)"/i);
  for (const path of [
    "/api/reports/purchase_history/receipts/",
    "/api/purchasing/quotes/",
    "/api/purchasing/storage-charges/",
    "/api/reports/inventory/reservations/",
    "/api/reports/sales/deliveries/",
    "/api/governance/purchasing/counterparties/",
  ]) {
    assert.ok(readside.includes(path), `Expected read-only endpoint path ${path}`);
  }
  assert.match(styles, /button:focus-visible/);
  assert.match(styles, /prefers-reduced-motion:\s*reduce/);
  assert.match(readsideStyles, /button:focus-visible/);
  assert.match(readsideStyles, /prefers-reduced-motion:\s*reduce/);
});
