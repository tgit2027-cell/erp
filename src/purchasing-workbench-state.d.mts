export type PurchasingReadinessStatus =
  | "configured"
  | "not-configured"
  | "staff-only"
  | "present-not-used"
  | "unconfigured";

export type PurchasingReadinessItem = {
  key: string;
  label: string;
  status: PurchasingReadinessStatus;
  count: number | null;
};

export function shouldLoadPurchasingConfiguration(isStaff: boolean): boolean;

export function getPurchasingConfigurationReadiness(configuration: {
  role_assignments?: unknown[];
  current_prices?: unknown[];
  work_calendar?: { is_configured?: boolean } | null;
  special_order_value_bands?: { is_active?: boolean }[];
  special_order_bands_used_for_approval?: boolean;
  storage_exception_approver_roles?: unknown[];
} | null | undefined): PurchasingReadinessItem[];

export function getQuoteLookupState(input: {
  submitted: boolean;
  loading?: boolean;
  error?: string;
  quote?: unknown;
}): "idle" | "loading" | "error" | "ready" | "empty";

export function getPurchasingWorkbenchCapabilities(): {
  configurationRead: "staff-only";
  quoteRead: "server-authorized";
  receiptHistoryRead: "purchase-history-grant";
  storageHistoryRead: "create-order-grant";
  quoteListRead: "create-order-grant";
  reservationRead: "inventory-report-grant";
  deliveryRead: "sales-report-grant";
  counterpartiesRead: "staff-only";
  writesEnabled: true;
};
export type PurchasingActionState = { enabled: boolean; reason: string };
export type PurchasingActionAvailability = {
  orderAccess: PurchasingActionState;
  currentRoles: string[];
  discountCeiling: number;
  quoteDiscountCeiling: number;
  receipt: PurchasingActionState;
  quote: PurchasingActionState;
  hold: PurchasingActionState;
  discountApproval: PurchasingActionState;
  payment: PurchasingActionState;
  quoteBalancePayment: PurchasingActionState;
  storagePayment: PurchasingActionState;
  postHandover: PurchasingActionState;
  readiness: PurchasingActionState;
  delivery: PurchasingActionState;
  storageAccrual: PurchasingActionState;
  canStart: boolean;
};
export function getPurchasingActionAvailability(input?: {
  orderAccess?: "idle" | "loading" | "ready" | "denied" | "error";
  isStaff?: boolean;
  username?: string;
  csrfToken?: string;
  locations?: unknown[];
  counterparties?: unknown[];
  counterpartyState?: "idle" | "loading" | "ready" | "error";
  configuration?: {
    role_assignments?: { username?: string; role?: string }[];
    current_prices?: unknown[];
    work_calendar?: { is_configured?: boolean } | null;
  } | null;
  configurationState?: "idle" | "loading" | "ready" | "error";
  quotationConfigurationState?: "idle" | "loading" | "ready" | "denied" | "error";
  quotationConfiguration?: {
    current_prices?: unknown[];
    customers?: { party_type?: string }[];
    role_assignments?: { username?: string; role?: string }[];
    discount_ceiling?: string | number;
  } | null;
  storageOutstandingAmount?: number;
  quote?: {
    status?: string;
    discount_percent?: string | number;
    holds?: { status?: string }[];
    approvals?: { kind?: string }[];
  } | null;
}): PurchasingActionAvailability;
export function shouldRequestPurchasingCounterparties(isStaff: unknown): boolean;
export function isPurchasingReadsideDeniedStatus(status: unknown): boolean;
export function getPurchasingReadsideState(
  status: string,
  rows?: unknown,
): "idle" | "loading" | "unauthorized" | "error" | "empty" | "populated";
export function getPurchasingReadsideTabs(isStaff: unknown): {
  id: "receipts" | "quotes" | "storage" | "reservations" | "deliveries" | "counterparties";
  label: string;
  staffOnly?: boolean;
}[];
export function getPurchasingPagerState(pagination: {
  total_count?: number;
  total_pages?: number;
  previous_page?: number | null;
  next_page?: number | null;
} | null | undefined): {
  isEmpty: boolean;
  hasMultiplePages: boolean;
  canGoPrevious: boolean;
  canGoNext: boolean;
};
