function asArray(value) {
  return Array.isArray(value) ? value : [];
}

export function shouldLoadPurchasingConfiguration(isStaff) {
  return isStaff === true;
}

export function getPurchasingConfigurationReadiness(configuration) {
  const roles = asArray(configuration?.role_assignments);
  const prices = asArray(configuration?.current_prices);
  const activeBands = asArray(configuration?.special_order_value_bands).filter(
    (band) => band?.is_active === true,
  );
  const bandsUsedForApproval = configuration?.special_order_bands_used_for_approval === true;
  const storageRoles = asArray(configuration?.storage_exception_approver_roles);

  return [
    {
      key: "approval-roles",
      label: "Approval-role assignments",
      status: roles.length ? "configured" : "not-configured",
      count: roles.length,
    },
    {
      key: "approved-prices",
      label: "Approved selling prices",
      status: prices.length ? "configured" : "not-configured",
      count: prices.length,
    },
    {
      key: "work-calendar",
      label: "Work calendar",
      status: configuration?.work_calendar?.is_configured === true ? "configured" : "not-configured",
      count: null,
    },
    {
      key: "counterparties",
      label: "Supplier and customer directory",
      status: "staff-only",
      count: null,
    },
    {
      key: "special-order-value-bands",
      label: "Special-order value bands",
      status: activeBands.length ? (bandsUsedForApproval ? "configured" : "present-not-used") : "unconfigured",
      count: activeBands.length,
    },
    {
      key: "storage-exception-approvers",
      label: "Storage-exception approvers",
      status: storageRoles.length ? "configured" : "not-configured",
      count: storageRoles.length,
    },
  ];
}

export function getQuoteLookupState({ submitted, loading, error, quote }) {
  if (!submitted) return "idle";
  if (loading) return "loading";
  if (error) return "error";
  return quote ? "ready" : "empty";
}

export function getPurchasingWorkbenchCapabilities() {
  return {
    configurationRead: "staff-only",
    quoteRead: "server-authorized",
    receiptHistoryRead: "purchase-history-grant",
    storageHistoryRead: "create-order-grant",
    quoteListRead: "create-order-grant",
    reservationRead: "inventory-report-grant",
    deliveryRead: "sales-report-grant",
    counterpartiesRead: "staff-only",
    writesEnabled: true,
  };
}

const DISCOUNT_CEILINGS = {
  salesperson: 10,
  manager: 20,
  commercial_gm: 30,
  ceo: 50,
};

function actionState(enabled, reason = "") {
  return { enabled, reason: enabled ? "" : reason };
}

function quoteBalanceCents(quote) {
  if (!Array.isArray(quote?.payments)) return null;
  const totalMatch = String(quote?.total_quote_value ?? "").trim().match(/^(\d+)(?:\.(\d{1,2}))?$/);
  if (!totalMatch) return null;
  const total = BigInt(totalMatch[1]) * 100n + BigInt((totalMatch[2] ?? "").padEnd(2, "0"));
  let paid = 0n;
  for (const payment of quote.payments) {
    if (!["deposit", "balance"].includes(payment?.kind)) continue;
    const match = String(payment?.amount ?? "").trim().match(/^(\d+)(?:\.(\d{1,2}))?$/);
    if (!match) return null;
    paid += BigInt(match[1]) * 100n + BigInt((match[2] ?? "").padEnd(2, "0"));
  }
  return total > paid ? total - paid : 0n;
}

export function getPurchasingActionAvailability({
  orderAccess = "idle",
  isStaff = false,
  username = "",
  csrfToken = "",
  locations = [],
  counterparties = [],
  counterpartyState = "idle",
  configuration = null,
  configurationState = "idle",
  quotationConfiguration = null,
  quotationConfigurationState = "idle",
  quote = null,
  storageOutstandingAmount = 0,
} = {}) {
  const hasOrderAccess = orderAccess === "ready";
  const hasCsrf = typeof csrfToken === "string" && csrfToken.trim().length > 0;
  const canStart = hasOrderAccess && hasCsrf;
  const assignments = asArray(configuration?.role_assignments);
  const currentRoles = assignments
    .filter((assignment) => assignment?.username === username)
    .map((assignment) => assignment?.role)
    .filter((role) => typeof role === "string");
  const discountCeiling = Math.max(0, ...currentRoles.map((role) => DISCOUNT_CEILINGS[role] || 0));
  const quoteConfiguration = quotationConfiguration;
  const quotePrices = asArray(quoteConfiguration?.current_prices);
  const quoteCustomers = asArray(quoteConfiguration?.customers);
  const quoteAssignments = asArray(quoteConfiguration?.role_assignments);
  const quoteHasRole = quoteAssignments.some((assignment) => assignment?.username === username);
  const quoteConfigReady = quotationConfigurationState === "ready" && quoteConfiguration !== null;
  const suppliers = asArray(counterparties).filter((party) => party?.party_type === "supplier");
  const customers = asArray(counterparties).filter((party) => party?.party_type === "customer");
  const configVisible = isStaff === true && configurationState === "ready" && configuration !== null;
  const quoteIsActive = quote && !["pending_approval", "cancelled"].includes(quote.status);
  const quoteCanReceivePayment = quoteIsActive && hasOrderAccess;
  const hasPostHandoverApproval = asArray(quote?.approvals)
    .some((approval) => approval?.kind === "post_handover");
  const remainingQuoteBalanceCents = quote?.status === "delivered"
    ? quoteBalanceCents(quote)
    : null;
  const postHandoverApprovedRole = currentRoles.some((role) => role === "bu_gm" || role === "ceo");
  const quoteStatusAllowsActions = quote && hasOrderAccess;
  const calendarConfigured = configuration?.work_calendar?.is_configured === true;

  const noOrderAccess = orderAccess === "loading"
    ? "Checking this business unit's existing order access."
    : orderAccess === "error"
      ? "Order access could not be verified; all write actions remain unavailable."
      : "The server did not confirm an active create_order grant and approval-role assignment for this business unit.";
  const csrfReason = "A valid session CSRF token is required before a write action can be submitted.";
  const configurationReason = isStaff
    ? configurationState === "loading"
      ? "Staff configuration is still loading; dependent actions remain unavailable."
      : configurationState === "error"
        ? "Staff configuration could not be verified; dependent actions remain unavailable."
        : "Staff configuration is required before this action can be enabled."
    : "This account cannot read staff-only price and work-calendar configuration; dependent actions remain unavailable.";
  const directoryReason = !isStaff
    ? "The synthetic supplier/customer directory is staff-only, so this action is unavailable to this account."
    : counterpartyState === "loading"
      ? "The staff-only synthetic counterparty directory is loading."
      : counterpartyState === "error"
        ? "The synthetic counterparty directory could not be verified."
        : "No matching synthetic counterparty is configured.";
  const roleReason = configVisible && currentRoles.length === 0
    ? "No active approval-role assignment was returned for this user in this business unit."
    : configurationReason;

  let receiptReason = "";
  if (!hasOrderAccess) receiptReason = noOrderAccess;
  else if (!hasCsrf) receiptReason = csrfReason;
  else if (!asArray(locations).length) receiptReason = "No authorized receiving locations are available for this business unit.";
  else if (counterpartyState !== "ready" || suppliers.length === 0) receiptReason = directoryReason;

  let quoteReason = "";
  if (!hasOrderAccess) quoteReason = noOrderAccess;
  else if (!hasCsrf) quoteReason = csrfReason;
  else if (!quoteConfigReady) quoteReason = quotationConfigurationState === "loading"
    ? "Current quotation choices are loading for this business unit."
    : quotationConfigurationState === "error" || quotationConfigurationState === "denied"
      ? "Current quotation choices could not be authorized; retry after verifying this business unit."
      : "Current quotation choices are not available for this business unit.";
  else if (!quoteHasRole) quoteReason = "No current role assignment was returned for this salesperson in this business unit.";
  else if (!quotePrices.length) quoteReason = "No current approved selling-price snapshots are configured for this unit.";
  else if (!quoteCustomers.length) quoteReason = "No synthetic customers are available for this business unit.";

  let holdReason = "";
  if (!hasOrderAccess) holdReason = noOrderAccess;
  else if (!hasCsrf) holdReason = csrfReason;
  else if (!configVisible) holdReason = configurationReason;
  else if (!currentRoles.length) holdReason = roleReason;
  else if (!calendarConfigured) holdReason = "The business-unit work calendar is not configured; holds remain unavailable.";
  else if (quote && !["approved", "held"].includes(quote.status)) holdReason = "Only an approved quote can be held.";
  else if (asArray(quote?.holds).some((hold) => hold?.status === "active")) holdReason = "This quote already has an active hold.";
  else if (!quote) holdReason = "Load an authorized quote before requesting a hold.";

  const discount = Number(quote?.discount_percent);
  const eligibleForDiscountApproval = currentRoles.some(
    (role) => (DISCOUNT_CEILINGS[role] || 0) >= discount && DISCOUNT_CEILINGS[role],
  );
  let discountReason = "";
  if (!hasOrderAccess) discountReason = noOrderAccess;
  else if (!hasCsrf) discountReason = csrfReason;
  else if (!configVisible) discountReason = configurationReason;
  else if (!quote) discountReason = "Load an authorized quote before reviewing its price decision.";
  else if (quote.status !== "pending_approval") discountReason = "This quote is not awaiting a price decision.";
  else if (!eligibleForDiscountApproval) discountReason = "No active assigned role can approve this discount ceiling.";

  const paymentState = !hasOrderAccess
    ? noOrderAccess
    : !hasCsrf
      ? csrfReason
      : !quote
        ? "Load an authorized quote before recording a simulated payment."
        : !quoteCanReceivePayment
          ? "Payments are unavailable until the quote is approved and active."
          : "";
  const quoteBalanceAllowed = Boolean(
    quoteCanReceivePayment
      && (quote.status !== "delivered"
        || (hasPostHandoverApproval
          && remainingQuoteBalanceCents !== null
          && remainingQuoteBalanceCents > 0n)),
  );
  const storagePaymentAllowed = Boolean(
    quoteCanReceivePayment
      && ["ready", "delivered"].includes(quote.status)
      && Number.isFinite(Number(storageOutstandingAmount))
      && Number(storageOutstandingAmount) > 0,
  );

  let postHandoverReason = "";
  if (!hasOrderAccess) postHandoverReason = noOrderAccess;
  else if (!hasCsrf) postHandoverReason = csrfReason;
  else if (!configVisible) postHandoverReason = configurationReason;
  else if (!quote) postHandoverReason = "Load an authorized quote before requesting a post-handover approval.";
  else if (!["approved", "confirmed", "ready"].includes(quote.status)) postHandoverReason = "The quote is not in an active approval state.";
  else if (asArray(quote.approvals).some((approval) => approval?.kind === "post_handover")) postHandoverReason = "A post-handover approval already exists for this quote.";
  else if (!postHandoverApprovedRole) postHandoverReason = "Only an assigned BU GM or CEO can approve post-handover terms.";

  const stageAction = (statuses, prompt) => {
    if (!hasOrderAccess) return actionState(false, noOrderAccess);
    if (!hasCsrf) return actionState(false, csrfReason);
    if (!quote) return actionState(false, "Load an authorized quote first.");
    if (!statuses.includes(quote.status)) return actionState(false, prompt);
    return actionState(true);
  };

  return {
    orderAccess: actionState(hasOrderAccess, noOrderAccess),
    currentRoles,
    discountCeiling,
    quoteDiscountCeiling: Number.isFinite(Number(quoteConfiguration?.discount_ceiling))
      ? Math.max(0, Number(quoteConfiguration.discount_ceiling))
      : 0,
    receipt: actionState(!receiptReason, receiptReason),
    quote: actionState(!quoteReason, quoteReason),
    hold: actionState(!holdReason, holdReason),
    discountApproval: actionState(!discountReason, discountReason),
    payment: actionState(!paymentState, paymentState),
    quoteBalancePayment: actionState(
      quoteBalanceAllowed,
      quoteBalanceAllowed
        ? ""
        : quote?.status === "delivered" && !hasPostHandoverApproval
          ? "A BU GM or CEO post-handover approval is required before collecting a delivered quote balance."
          : quote?.status === "delivered" && remainingQuoteBalanceCents === null
            ? "The delivered quote balance could not be verified; refresh the authorized quote details."
            : quote?.status === "delivered" && remainingQuoteBalanceCents === 0n
              ? "No unpaid quote balance remains."
          : "Load an approved quote before recording a quote-balance payment.",
    ),
    storagePayment: actionState(
      storagePaymentAllowed,
      storagePaymentAllowed
        ? ""
        : !quote || !["ready", "delivered"].includes(quote.status)
          ? "Storage-charge payments are available only after readiness."
          : !Number.isFinite(Number(storageOutstandingAmount)) || Number(storageOutstandingAmount) < 0
            ? "The storage-charge balance could not be verified."
          : Number(storageOutstandingAmount) > 0
              ? "The storage-charge balance was returned; refresh and retry if the amount changed."
              : "No outstanding synthetic storage charge was returned.",
    ),
    postHandover: actionState(!postHandoverReason, postHandoverReason),
    readiness: stageAction(["confirmed"], "The quote must have fully reserved stock before readiness can be recorded."),
    delivery: stageAction(["ready"], "Record readiness before delivery."),
    storageAccrual: stageAction(["ready", "delivered"], "Storage can accrue only after readiness is recorded."),
    canStart,
  };
}

export function shouldRequestPurchasingCounterparties(isStaff) {
  return isStaff === true;
}

export function isPurchasingReadsideDeniedStatus(status) {
  return [401, 403, 404].includes(Number(status));
}

export function getPurchasingReadsideState(status, rows) {
  if (status === "ready") return Array.isArray(rows) && rows.length > 0 ? "populated" : "empty";
  if (status === "loading") return "loading";
  if (status === "unauthorized") return "unauthorized";
  if (status === "error") return "error";
  return "idle";
}

const PURCHASING_READSIDE_TABS = [
  { id: "receipts", label: "Receipts" },
  { id: "quotes", label: "Quotes" },
  { id: "storage", label: "Storage charges" },
  { id: "reservations", label: "Reservations" },
  { id: "deliveries", label: "Deliveries" },
  { id: "counterparties", label: "Counterparties", staffOnly: true },
];

export function getPurchasingReadsideTabs(isStaff) {
  return PURCHASING_READSIDE_TABS.filter((tab) => !tab.staffOnly || isStaff === true);
}

export function getPurchasingPagerState(pagination) {
  const totalCount = Number(pagination?.total_count);
  const previousPage = Number(pagination?.previous_page);
  const nextPage = Number(pagination?.next_page);
  const totalPages = Number(pagination?.total_pages);
  return {
    isEmpty: Number.isFinite(totalCount) && totalCount === 0,
    hasMultiplePages: Number.isFinite(totalPages) && totalPages > 1,
    canGoPrevious: Number.isInteger(previousPage) && previousPage > 0,
    canGoNext: Number.isInteger(nextPage) && nextPage > 0,
  };
}
