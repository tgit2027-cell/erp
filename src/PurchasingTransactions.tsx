import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PurchaseCurrencyFields, { ChargeCurrencyFields, chargeExchangePayload, exchangePayload, exchangeProblem, qarExchange } from "./PurchaseCurrencyFields";
import type { ExchangeDraft } from "./PurchaseCurrencyFields";
import type { FormEvent } from "react";
import { getPurchasingActionAvailability } from "./purchasing-workbench-state.mjs";
import type { GovernanceResponse, Quote } from "./PurchasingWorkbench";
import PackingListPreview from "./PackingListPreview";
import type { ReviewedPackingReceipt } from "./packing-receipt-types";
import PurchasingQuoteLineFields from "./PurchasingQuoteLineFields";
import {
  formatQarCents,
  presentationLineUnits,
  validateQuoteLineEstimates,
} from "./purchasing-quote-lines";
import type { QuoteLineInput } from "./purchasing-quote-lines";

type LocationOption = { id: number; code: string; name: string; parent_id: number | null };
type Counterparty = { counterparty_id: number; party_type: "supplier" | "customer"; code: string; name: string };
type QuotationChoices = {
  business_unit: { id: number; code: string; name: string };
  current_prices: Array<{
    id: number; variant_id: number; sku: string; amount: string; currency: string; revision: number;
    approved_by: null;
    item: { item_code: string; name_en: string; name_ar: string; category_en: string; category_ar: string; description_en: string; description_ar: string; image_path: string | null };
    stock: Array<{ location_id: number; quantity: string }> | null;
  }>;
  current_price_count?: number;
  customers: Array<{ counterparty_id: number; code: string; display_name: string; party_type: "customer"; contact_en: string; contact_ar: string; name_ar: string }>;
  customer_count?: number;
  role_assignments: Array<{ id: number; user_id: number; username: string; role: string }>;
  discount_ceiling: string;
  work_calendar: { is_configured: boolean; closed_weekdays: number[]; closed_dates: string[] };
  test_only_label: string;
};
type QuotePriceRow = QuotationChoices["current_prices"][number];
type QuoteCustomerRow = QuotationChoices["customers"][number];
type StorageChargeRow = {
  amount: string;
  exception?: { amount_waived?: string } | null;
};
type PurchaseCommitmentLine = {
  commitment_id: number;
  commitment_line_id: number;
  supplier_id: number;
  variant_id: number;
  variant_sku: string;
  item_description: string;
  ordered_quantity: string;
  received_to_date: string;
  remaining_quantity: string;
  currency?: string;
};
type CommitmentSnapshot = {
  unitId: number;
  username: string;
  state: ReadState;
  rows: PurchaseCommitmentLine[];
  error: string;
};
type ConfigurationState = "idle" | "loading" | "ready" | "error";
type ReadState = "idle" | "loading" | "ready" | "error";
type AccessState = "idle" | "loading" | "ready" | "denied" | "error";
type ActionOutcome = { quoteId: number | undefined; kind: "success" | "error"; message: string };
const ACTION_LABELS: Record<string, string> = {
  commitment: "Purchase commitment",
  receipt: "Receipt creation",
  quote: "Quote creation",
  payment: "Simulated payment",
  ready: "Readiness notification",
  hold: "Temporary hold",
  "discount-approval": "Discount approval",
  "post-handover": "Post-handover approval",
  delivery: "Delivery",
  "storage-accrual": "Storage accrual",
};
type Props = {
  mode?: "purchasing" | "delivery" | "retail" | "warehouse" | "procurement";
  selectedUnitId: number;
  selectedUnitName: string;
  username: string;
  isStaff: boolean;
  csrfToken: string;
  locations: LocationOption[];
  configuration: GovernanceResponse | null;
  configurationState: ConfigurationState;
  quote: Quote | null;
  quoteLoading: boolean;
  quoteNavigationVersion: { readonly current: number };
  refreshKey: number;
  onQuoteCreated: (quoteId: number) => void;
  onMutation: (quoteId?: number) => void;
  onQuoteAccessRecheck?: () => void;
  onReceiptSuccess?: () => void;
  focusedView?: string;
  transactionEnabled?: boolean;
};

type ReceiptLineDraft = {
  variantId: string;
  locationId: string;
  quantity: string;
  unitCost: string;
  quoteLineId: string;
};
type PurchaseCommitmentDraftLine = {
  variantId: string;
  orderedQuantity: string;
  unitCost: string;
};
type CommitmentReceiptDraftLine = {
  commitmentLineId: string;
  locationId: string;
  quantity: string;
};
type ChargeDraft = { category: "shipping" | "clearance" | "other"; amount: string; exchange: ExchangeDraft };
type AdditionalQuoteLineDraft = QuoteLineInput & { key: number };

function moneyCents(value: string | number | null | undefined) {
  const match = String(value ?? "").trim().match(/^(\d+)(?:\.(\d{1,2}))?$/);
  if (!match) return 0n;
  return BigInt(match[1]) * 100n + BigInt((match[2] ?? "").padEnd(2, "0"));
}

function outstandingStorageCents(charges: StorageChargeRow[], payments: Quote["payments"] = []) {
  const assessed = charges.reduce((sum, charge) => {
    const amount = moneyCents(charge.amount);
    const waived = moneyCents(charge.exception?.amount_waived);
    return sum + (amount > waived ? amount - waived : 0n);
  }, 0n);
  const paid = payments
    .filter((payment) => payment.kind === "storage")
    .reduce((sum, payment) => sum + moneyCents(payment.amount), 0n);
  return assessed > paid ? assessed - paid : 0n;
}

class ApiProblem extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function requestKey() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `pw-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function readBody(response: Response): Promise<Record<string, unknown>> {
  return response.json().catch(() => ({})) as Promise<Record<string, unknown>>;
}

function errorMessage(body: Record<string, unknown>, status: number) {
  const detail = body.detail ?? body.message;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  if (Object.keys(body).length > 0) return JSON.stringify(body);
  return `The server rejected the request (${status}).`;
}

export default function PurchasingTransactions({
  mode = "purchasing",
  selectedUnitId,
  selectedUnitName,
  username,
  isStaff,
  csrfToken,
  locations,
  configuration,
  configurationState,
  quote,
  quoteLoading,
  quoteNavigationVersion,
  refreshKey,
  onQuoteCreated,
  onQuoteAccessRecheck,
  onMutation,
  onReceiptSuccess,
  focusedView,
  transactionEnabled = true,
}: Props) {
  const deliveryMode = mode === "delivery";
  const retailMode = mode === "retail";
  const warehouseMode = mode === "warehouse";
  const procurementMode = mode === "procurement";
  const receivingMode = warehouseMode || procurementMode;
  const showFunction = (name: string) => focusedView === undefined || focusedView === name
    || (focusedView === "receiving" && ["receipt", "commitments", "progress"].includes(name))
    || (focusedView === "transactions" && ["quote", "actions", "receipt", "commitments", "progress", "packing"].includes(name));
  const transactionPaneActive = focusedView === undefined
    || (retailMode && ["quote", "actions"].includes(focusedView))
    || (deliveryMode && focusedView === "handover")
    || (procurementMode && ["commitments", "progress", "receipt", "packing"].includes(focusedView))
    || (warehouseMode && ["receipt", "commitments", "progress", "receiving", "packing"].includes(focusedView))
    || (!retailMode && !deliveryMode && !procurementMode && !warehouseMode && focusedView === "transactions");
  const [orderAccess, setOrderAccess] = useState<AccessState>("idle");
  const [accessMessage, setAccessMessage] = useState("");
  const [counterpartyState, setCounterpartyState] = useState<ReadState>("idle");
  const [counterparties, setCounterparties] = useState<Counterparty[]>([]);
  const [quotationChoices, setQuotationChoices] = useState<{
    unitId: number; username: string; state: AccessState; data: QuotationChoices | null; error: string;
  }>({ unitId: -1, username: "", state: "idle", data: null, error: "" });
  const quotationRequestVersion = useRef(0);
  // Rows found by search (or looked up for restored lines) beyond the first page of choices.
  const [pickedPrices, setPickedPrices] = useState<{ context: string; rows: Record<string, QuotePriceRow> }>({ context: "", rows: {} });
  const [pickedCustomers, setPickedCustomers] = useState<{ context: string; rows: Record<string, QuoteCustomerRow> }>({ context: "", rows: {} });
  const [customerSearch, setCustomerSearch] = useState("");
  const [customerResults, setCustomerResults] = useState<{ term: string; rows: QuoteCustomerRow[]; more: boolean } | null>(null);
  const lookedUpVariants = useRef(new Set<string>());
  const [directoryStaffContext, setDirectoryStaffContext] = useState(isStaff);
  const [directoryUnitContext, setDirectoryUnitContext] = useState(selectedUnitId);
  const [storageCharges, setStorageCharges] = useState<StorageChargeRow[]>([]);
  const [storageChargeState, setStorageChargeState] = useState<ReadState>("idle");
  const [commitmentSnapshot, setCommitmentSnapshot] = useState<CommitmentSnapshot>({
    unitId: -1, username: "", state: "idle", rows: [], error: "",
  });
  const [receiptSupplier, setReceiptSupplier] = useState("");
  const [packingReceipt, setPackingReceipt] = useState<{
    context: string;
    draft: ReviewedPackingReceipt;
    unitCosts: string[];
  } | null>(null);
  const packingContext = `${username}:${selectedUnitId}:${isStaff}:${refreshKey}`;
  const currentPackingReceipt = packingReceipt?.context === packingContext && isStaff
    ? packingReceipt : null;
  if (packingReceipt && packingReceipt.context !== packingContext) {
    setPackingReceipt(null);
  }
  const [receiptCommitmentLines, setReceiptCommitmentLines] = useState<CommitmentReceiptDraftLine[]>([]);
  const [receiptLine, setReceiptLine] = useState<ReceiptLineDraft>({
    variantId: "",
    locationId: "",
    quantity: "1",
    unitCost: "",
    quoteLineId: "",
  });
  const [commitmentLines, setCommitmentLines] = useState<PurchaseCommitmentDraftLine[]>([
    { variantId: "", orderedQuantity: "1", unitCost: "" },
  ]);
  const [charges, setCharges] = useState<ChargeDraft[]>([]);
  const [receiptExchange, setReceiptExchange] = useState<ExchangeDraft>(qarExchange);
  const [commitmentExchange, setCommitmentExchange] = useState<ExchangeDraft>(qarExchange);
  const [quoteKind, setQuoteKind] = useState<"stock" | "special_order">("stock");
  const [quoteCustomer, setQuoteCustomer] = useState("");
  const [quoteVariant, setQuoteVariant] = useState("");
  const [quoteLocation, setQuoteLocation] = useState("");
  const [quoteQuantity, setQuoteQuantity] = useState("1");
  const [quoteSpecifications, setQuoteSpecifications] = useState("{}");
  const [quoteUnitEn, setQuoteUnitEn] = useState("");
  const [quoteUnitAr, setQuoteUnitAr] = useState("");
  const [additionalQuoteLines, setAdditionalQuoteLines] = useState<AdditionalQuoteLineDraft[]>([]);
  const [quoteLineRevision, setQuoteLineRevision] = useState(0);
  const quoteLineKey = useRef(0);
  const [quoteDraftContext, setQuoteDraftContext] = useState(`${selectedUnitId}:${username}`);
  const [discountPercent, setDiscountPercent] = useState("0");
  const [discountReason, setDiscountReason] = useState("");
  const [presentation, setPresentation] = useState<Record<string, string>>({
    valid_until: "", payment_terms_en: "", payment_terms_ar: "",
    delivery_time_en: "", delivery_time_ar: "", department_en: "", department_ar: "",
  });
  const [paymentPlan, setPaymentPlan] = useState<"full" | "downpayment">("full");
  const [downpaymentPercent, setDownpaymentPercent] = useState("40");
  const [paymentTermsReason, setPaymentTermsReason] = useState("");
  const [holdDays, setHoldDays] = useState("3");
  const [approvalReason, setApprovalReason] = useState("");
  const [deferredBalancePercent, setDeferredBalancePercent] = useState("5");
  const [readyMessage, setReadyMessage] = useState("");
  const [paymentAmount, setPaymentAmount] = useState("");
  const [paymentPurpose, setPaymentPurpose] = useState<"quote" | "storage">("quote");
  const [paymentMethod, setPaymentMethod] = useState<"simulated_cash" | "simulated_card" | "simulated_bank">("simulated_cash");
  const [pendingActions, setPendingActions] = useState<Record<string, { name: string; quoteId?: number; owner: object }>>({});
  const [inFlightCount, setInFlightCount] = useState(0);
  const isPending = (name: string) => Object.values(pendingActions).some((action) =>
    action.name === name && (action.quoteId === undefined || action.quoteId === quote?.quote_id));
  const [creationOutcomes, setCreationOutcomes] = useState<Record<string, Omit<ActionOutcome, "quoteId">>>({});
  const [actionOutcomes, setActionOutcomes] = useState<Record<string, ActionOutcome>>({});
  const [outcomeNavigationVersion, setOutcomeNavigationVersion] = useState(quoteNavigationVersion.current);
  const [quoteFormError, setQuoteFormError] = useState("");
  const idempotencyKeys = useRef<Record<string, string>>({});
  const displayedQuoteId = useRef(quote?.quote_id);
  displayedQuoteId.current = quote?.quote_id;
  const quoteAccessRecheck = useRef(onQuoteAccessRecheck);
  quoteAccessRecheck.current = onQuoteAccessRecheck;
  const accessRequestVersion = useRef(0);
  const actionContext = useRef({ selectedUnitId, username });
  const commitmentContextIsCurrent = commitmentSnapshot.unitId === selectedUnitId
    && commitmentSnapshot.username === username;
  const commitmentRows = commitmentContextIsCurrent && orderAccess === "ready"
    ? commitmentSnapshot.rows
    : [];
  const commitmentReadState = commitmentContextIsCurrent
    ? commitmentSnapshot.state
    : (orderAccess === "ready" ? "loading" : "idle");
  const commitmentReadError = commitmentContextIsCurrent ? commitmentSnapshot.error : "";
  const selectedReceiptCommitments = receiptCommitmentLines.map((draft) => (
    commitmentRows.find((row) => String(row.commitment_line_id) === draft.commitmentLineId) ?? null
  ));
  const linkedReceiptCommitment = selectedReceiptCommitments.find((row) => row !== null) ?? null;
  const linkedReceiptCommitmentId = linkedReceiptCommitment?.commitment_id;
  const linkedReceiptSupplierId = currentPackingReceipt?.draft.supplierId ?? linkedReceiptCommitment?.supplier_id;
  const lockedCommitmentId = currentPackingReceipt?.draft.commitmentId ?? linkedReceiptCommitmentId;
  const receiptLockedCurrency = lockedCommitmentId
    ? commitmentRows.find((row) => row.commitment_id === lockedCommitmentId)?.currency ?? "QAR"
    : undefined;
  const commitmentOptionsForReceiptLine = (lineIndex: number) => {
    const selectedElsewhere = receiptCommitmentLines
      .filter((_, index) => index !== lineIndex)
      .map((draft) => commitmentRows.find((row) => String(row.commitment_line_id) === draft.commitmentLineId))
      .filter((row): row is PurchaseCommitmentLine => row !== undefined);
    const selectedElsewhereIds = new Set(selectedElsewhere.map((row) => row.commitment_line_id));
    const selectedElsewhereCommitmentId = selectedElsewhere[0]?.commitment_id;
    return commitmentRows.filter((row) => (
      moneyCents(row.remaining_quantity) > 0n
      && (!selectedElsewhereCommitmentId || row.commitment_id === selectedElsewhereCommitmentId)
      && !selectedElsewhereIds.has(row.commitment_line_id)
    ));
  };
  if (outcomeNavigationVersion !== quoteNavigationVersion.current) {
    setOutcomeNavigationVersion(quoteNavigationVersion.current);
    setActionOutcomes({});
  }
  if (directoryStaffContext !== isStaff) {
    setDirectoryStaffContext(isStaff);
    setCounterparties([]);
    setCounterpartyState(isStaff ? "loading" : "idle");
  }
  if (directoryUnitContext !== selectedUnitId) {
    setDirectoryUnitContext(selectedUnitId);
    setCounterparties([]);
    setCounterpartyState(isStaff && !deliveryMode && !retailMode ? "loading" : "idle");
    setReceiptCommitmentLines([]);
  }
  if (actionContext.current.selectedUnitId !== selectedUnitId) {
    setOrderAccess("loading");
    setAccessMessage("");
  }
  // Invalidate UI completions before a new BU commits, without aborting POSTs,
  // remounting forms, or discarding pending requests' retry identities.
  if (actionContext.current.selectedUnitId !== selectedUnitId || actionContext.current.username !== username) {
    actionContext.current = { selectedUnitId, username };
    setPendingActions({});
    setCreationOutcomes({});
    setQuoteFormError("");
    setActionOutcomes({});
  }
  useEffect(() => () => {
    actionContext.current = { ...actionContext.current };
  }, []);

  async function verifyOrderAccess(active: { current: boolean } = { current: true }) {
    const context = actionContext.current;
    const version = ++accessRequestVersion.current;
    const isCurrent = () => active.current && actionContext.current === context
      && accessRequestVersion.current === version;
    setOrderAccess("loading");
    setAccessMessage("");
    try {
      const params = new URLSearchParams({
        business_unit_id: String(selectedUnitId),
        page: "1",
        page_size: "1",
      });
      const response = await fetch(`/api/purchasing/quotes/?${params}`, {
        method: "GET",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const body = await readBody(response);
      if (!response.ok) {
        const message = errorMessage(body, response.status);
        if (isCurrent()) {
          setOrderAccess(response.status === 403 || response.status === 404 ? "denied" : "error");
          setAccessMessage(message);
        }
        return;
      }
      if (isCurrent()) setOrderAccess("ready");
    } catch {
      if (isCurrent()) {
        setOrderAccess("error");
        setAccessMessage("The order-access check could not reach the server.");
      }
    }
  }

  useEffect(() => {
    if (!transactionEnabled) return;
    const active = { current: true };
    setOrderAccess("loading");
    setCounterparties([]);
    setCounterpartyState(!deliveryMode && isStaff ? "loading" : "idle");
    void verifyOrderAccess(active);
    if (!deliveryMode && isStaff) {
      const fetchCounterpartyPages = async (partyType: "supplier" | "customer") => {
        const rows: Counterparty[] = [];
        let page = 1;
        let totalPages = 1;
        while (page <= totalPages) {
          const params = new URLSearchParams({
            party_type: partyType,
            page: String(page),
            page_size: "100",
          });
          const response = await fetch(`/api/governance/purchasing/counterparties/?${params}`, {
            method: "GET",
            credentials: "same-origin",
            headers: { Accept: "application/json" },
          });
          const body = await readBody(response);
          if (!response.ok) throw new ApiProblem(errorMessage(body, response.status), response.status);
          if (Array.isArray(body.rows)) rows.push(...body.rows as Counterparty[]);
          const pagination = body.pagination as { total_pages?: number } | undefined;
          totalPages = Math.max(1, Number(pagination?.total_pages ?? 1));
          page += 1;
        }
        return rows;
      };
      const directoryRequest = retailMode
        ? fetchCounterpartyPages("customer")
        : procurementMode
          ? fetchCounterpartyPages("supplier")
        : Promise.all([
          fetchCounterpartyPages("supplier"),
          fetchCounterpartyPages("customer"),
        ]).then((pages) => pages.flat());
      directoryRequest.then((rows) => {
        if (active.current) {
          setCounterparties(rows);
          setCounterpartyState("ready");
        }
      }).catch(() => {
        if (active.current) setCounterpartyState("error");
      });
    }
    return () => { active.current = false; };
  }, [transactionEnabled, deliveryMode, retailMode, procurementMode, isStaff, selectedUnitId]);

  useEffect(() => {
    const version = ++quotationRequestVersion.current;
    const eligible = transactionEnabled && !deliveryMode && !receivingMode && orderAccess === "ready";
    if (!eligible) {
      setQuotationChoices({ unitId: selectedUnitId, username, state: "idle", data: null, error: "" });
      return;
    }
    let active = true;
    setQuotationChoices({ unitId: selectedUnitId, username, state: "loading", data: null, error: "" });
    const params = new URLSearchParams({ business_unit_id: String(selectedUnitId) });
    fetch(`/api/purchasing/quotation-choices/?${params}`, {
      method: "GET", credentials: "same-origin", headers: { Accept: "application/json" },
    }).then(async (response) => {
      const body = await readBody(response);
      if (!response.ok) throw new ApiProblem(errorMessage(body, response.status), response.status);
      const data = body as unknown as QuotationChoices;
      if (Number(data.business_unit?.id) !== selectedUnitId) throw new Error("Quotation choices belong to another business unit.");
      if (active && quotationRequestVersion.current === version) {
        setQuotationChoices({ unitId: selectedUnitId, username, state: "ready", data, error: "" });
      }
    }).catch((reason: unknown) => {
      if (active && quotationRequestVersion.current === version) setQuotationChoices({
        unitId: selectedUnitId, username,
        state: reason instanceof ApiProblem && (reason.status === 403 || reason.status === 404) ? "denied" : "error",
        data: null, error: reason instanceof Error ? reason.message : "Quotation choices could not be loaded.",
      });
    });
    return () => { active = false; };
  }, [transactionEnabled, deliveryMode, receivingMode, orderAccess, selectedUnitId, username, refreshKey]);

  useEffect(() => {
    const wanted = customerSearch.trim();
    if (wanted.length < 2 || quotationChoices.state !== "ready") { setCustomerResults(null); return undefined; }
    let active = true;
    const timer = setTimeout(() => {
      const params = new URLSearchParams({ business_unit_id: String(selectedUnitId), search: wanted });
      fetch(`/api/purchasing/quotation-customers/?${params}`, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(async (response) => {
          const body = await readBody(response);
          if (!response.ok) throw new ApiProblem(errorMessage(body, response.status), response.status);
          const result = body as unknown as { rows: QuoteCustomerRow[]; more: boolean };
          if (active) setCustomerResults({ term: wanted, ...result });
        })
        .catch(() => { if (active) setCustomerResults({ term: wanted, rows: [], more: false }); });
    }, 250);
    return () => { active = false; clearTimeout(timer); };
  }, [customerSearch, selectedUnitId, quotationChoices.state]);

  useEffect(() => {
    setCustomerSearch("");
    setCustomerResults(null);
    lookedUpVariants.current = new Set();
  }, [selectedUnitId, username]);

  useEffect(() => {
    const active = { current: true };
    const context = { unitId: selectedUnitId, username };
    if (!transactionEnabled || deliveryMode || retailMode || orderAccess !== "ready") {
      setCommitmentSnapshot({ ...context, state: "idle", rows: [], error: "" });
      return () => { active.current = false; };
    }
    setCommitmentSnapshot({ ...context, state: "loading", rows: [], error: "" });
    const fetchPages = async () => {
      const rows: PurchaseCommitmentLine[] = [];
      let page = 1;
      let totalPages = 1;
      while (page <= totalPages) {
        const params = new URLSearchParams({
          business_unit_id: String(selectedUnitId),
          page: String(page),
          page_size: "100",
        });
        const response = await fetch(`/api/purchasing/commitments/?${params}`, {
          method: "GET",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        const body = await readBody(response);
        if (!response.ok) throw new ApiProblem(errorMessage(body, response.status), response.status);
        if (Array.isArray(body.rows)) rows.push(...body.rows as PurchaseCommitmentLine[]);
        const pagination = body.pagination as { total_pages?: number } | undefined;
        totalPages = Math.max(1, Number(pagination?.total_pages ?? 1));
        page += 1;
      }
      return rows;
    };
    void fetchPages().then((rows) => {
      if (active.current) setCommitmentSnapshot({ ...context, state: "ready", rows, error: "" });
    }).catch((reason: unknown) => {
      if (active.current) setCommitmentSnapshot({
        ...context,
        state: "error",
        rows: [],
        error: reason instanceof Error ? reason.message : "Commitment progress could not be loaded.",
      });
    });
    return () => { active.current = false; };
  }, [transactionEnabled, deliveryMode, retailMode, selectedUnitId, username, orderAccess, refreshKey]);

  useEffect(() => {
    const active = { current: true };
    const quoteId = quote?.quote_id;
    if (!transactionEnabled || retailMode || !quoteId || orderAccess !== "ready") {
      setStorageCharges([]);
      setStorageChargeState("idle");
      return () => { active.current = false; };
    }
    setStorageChargeState("loading");
    const fetchPages = async () => {
      const rows: StorageChargeRow[] = [];
      let page = 1;
      let totalPages = 1;
      while (page <= totalPages) {
        const params = new URLSearchParams({
          business_unit_id: String(selectedUnitId),
          quote_id: String(quoteId),
          page: String(page),
          page_size: "100",
        });
        const response = await fetch(`/api/purchasing/storage-charges/?${params}`, {
          method: "GET",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        const body = await readBody(response);
        if (!response.ok) throw new ApiProblem(errorMessage(body, response.status), response.status);
        if (Array.isArray(body.rows)) rows.push(...body.rows as StorageChargeRow[]);
        const pagination = body.pagination as { total_pages?: number } | undefined;
        totalPages = Math.max(1, Number(pagination?.total_pages ?? 1));
        page += 1;
      }
      if (active.current) {
        setStorageCharges(rows);
        setStorageChargeState("ready");
      }
    };
    void fetchPages().catch(() => {
      if (active.current) {
        setStorageCharges([]);
        setStorageChargeState("error");
      }
    });
    return () => { active.current = false; };
  }, [transactionEnabled, deliveryMode, retailMode, orderAccess, quote?.quote_id, quote?.payments?.length, refreshKey, selectedUnitId]);

  const availability = useMemo(() => getPurchasingActionAvailability({
    orderAccess,
    isStaff,
    username,
    csrfToken,
    locations,
    counterparties,
    counterpartyState,
    configuration,
    configurationState,
    quotationConfiguration: quotationChoices.unitId === selectedUnitId && quotationChoices.username === username
      ? quotationChoices.data : null,
    quotationConfigurationState: quotationChoices.unitId === selectedUnitId && quotationChoices.username === username
      ? quotationChoices.state : "loading",
    quote,
    storageOutstandingAmount: storageChargeState === "ready"
      ? (outstandingStorageCents(storageCharges, quote?.payments) > 0n ? 1 : 0)
      : -1,
  }), [
    orderAccess, isStaff, username, csrfToken, locations, counterparties,
    counterpartyState, configuration, configurationState, quotationChoices, quote,
    storageCharges, storageChargeState,
  ]);
  useEffect(() => {
    if (
      !retailMode
      &&
      paymentPurpose === "quote"
      && !availability.quoteBalancePayment.enabled
      && availability.storagePayment.enabled
    ) {
      setPaymentPurpose("storage");
    }
  }, [
    retailMode,
    paymentPurpose,
    availability.quoteBalancePayment.enabled,
    availability.storagePayment.enabled,
  ]);
  const suppliers = counterparties.filter((party) => party.party_type === "supplier");
  const currentQuotation = quotationChoices.unitId === selectedUnitId && quotationChoices.username === username
    && quotationChoices.state === "ready" && quotationChoices.data?.business_unit?.id === selectedUnitId
    ? quotationChoices.data : null;
  const choiceContext = `${selectedUnitId}:${username}`;
  const prices = useMemo(() => {
    const rows = [...(currentQuotation?.current_prices ?? [])];
    if (currentQuotation && pickedPrices.context === choiceContext) {
      for (const row of Object.values(pickedPrices.rows)) if (!rows.some((item) => item.variant_id === row.variant_id)) rows.push(row);
    }
    return rows;
  }, [currentQuotation, pickedPrices, choiceContext]);
  const customers = useMemo(() => {
    const base = customerResults && customerResults.term === customerSearch.trim() ? customerResults.rows : currentQuotation?.customers ?? [];
    const rows = [...base];
    if (currentQuotation && pickedCustomers.context === choiceContext) {
      for (const row of Object.values(pickedCustomers.rows)) if (!rows.some((item) => item.counterparty_id === row.counterparty_id)) rows.push(row);
    }
    return currentQuotation ? rows : [];
  }, [currentQuotation, customerResults, customerSearch, pickedCustomers, choiceContext]);
  const pickPrice = useCallback((row: QuotePriceRow) => {
    setPickedPrices((current) => ({
      context: choiceContext,
      rows: { ...(current.context === choiceContext ? current.rows : {}), [String(row.variant_id)]: row },
    }));
  }, [choiceContext]);
  const searchQuoteItems = useCallback(async (term: string) => {
    const params = new URLSearchParams({ business_unit_id: String(selectedUnitId), search: term });
    const response = await fetch(`/api/purchasing/quotation-items/?${params}`, { credentials: "same-origin", headers: { Accept: "application/json" } });
    const body = await readBody(response);
    if (!response.ok) throw new ApiProblem(errorMessage(body, response.status), response.status);
    return body as unknown as { rows: QuotePriceRow[]; more: boolean };
  }, [selectedUnitId]);
  const rawQuoteDiscountCeiling = Number(currentQuotation?.discount_ceiling ?? 0);
  const quoteDiscountCeiling = Number.isFinite(rawQuoteDiscountCeiling)
    ? Math.max(0, rawQuoteDiscountCeiling) : 0;
  const quoteRequestCeiling = isStaff ? 50 : quoteDiscountCeiling;
  const selectedQuoteCustomer = customers.find((customer) => String(customer.counterparty_id) === quoteCustomer);
  const quoteDraftIsStale = quoteDraftContext !== `${selectedUnitId}:${username}`;
  const quoteLineInputs: QuoteLineInput[] = [
    { variantId: quoteVariant, locationId: quoteLocation, quantity: quoteQuantity, specifications: quoteSpecifications, unit_en: quoteUnitEn, unit_ar: quoteUnitAr },
    ...additionalQuoteLines,
  ];
  const missingVariantIds = currentQuotation && !quoteDraftIsStale
    ? quoteLineInputs.map((line) => line.variantId).filter((id) => /^\d+$/.test(id)
      && !prices.some((price) => String(price.variant_id) === id) && !lookedUpVariants.current.has(id))
    : [];
  const missingVariantKey = [...new Set(missingVariantIds)].sort().join(",");
  useEffect(() => {
    if (!missingVariantKey) return undefined;
    // Lines restored or carried over may name items outside the first page of choices.
    for (const id of missingVariantKey.split(",")) lookedUpVariants.current.add(id);
    let active = true;
    const params = new URLSearchParams({ business_unit_id: String(selectedUnitId), ids: missingVariantKey });
    fetch(`/api/purchasing/quotation-items/?${params}`, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(async (response) => {
        const body = await readBody(response);
        if (!response.ok || !active) return;
        for (const row of (body as unknown as { rows: QuotePriceRow[] }).rows ?? []) pickPrice(row);
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [missingVariantKey, selectedUnitId, pickPrice]);
  const quoteEstimate = quoteDraftIsStale
    ? { ok: false as const, error: "Current-context review is required." }
    : validateQuoteLineEstimates(
      quoteLineInputs, prices, discountPercent, quoteKind, locations.map((location) => location.id),
    );
  const selectedQuoteId = quote?.quote_id;
  const actionPrefix = selectedQuoteId ? `/api/purchasing/quotes/${selectedQuoteId}` : "";

  function keyFor(name: string) {
    idempotencyKeys.current[name] ??= requestKey();
    return idempotencyKeys.current[name];
  }

  async function postAction(name: string, path: string, payload: Record<string, unknown>) {
    if (!csrfToken) throw new Error("Refresh the session CSRF token before submitting.");
    const identity = JSON.stringify([username, selectedUnitId, name, path, payload]);
    const key = keyFor(identity);
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken,
        "Idempotency-Key": key,
      },
      body: JSON.stringify(payload),
    });
    const body = await readBody(response);
    if (!response.ok) throw new ApiProblem(errorMessage(body, response.status), response.status);
    return {
      body,
      // A success unseen in the current context must remain safely replayable.
      // Retire its key only when the caller can acknowledge that success.
      acknowledge() {
        if (idempotencyKeys.current[identity] === key) delete idempotencyKeys.current[identity];
      },
    };
  }

  function recordCreationOutcome(name: "commitment" | "receipt" | "quote", kind: "success" | "error", message: string) {
    setCreationOutcomes((current) => ({ ...current, [name]: { kind, message } }));
  }

  function beginAction(name: string, quoteId?: number) {
    const context = actionContext.current;
    const owner = {};
    const pendingKey = JSON.stringify([name, quoteId ?? null]);
    const isCurrent = () => actionContext.current === context;
    setInFlightCount((count) => count + 1);
    setPendingActions((current) => ({ ...current, [pendingKey]: { name, quoteId, owner } }));
    if (name === "commitment" || name === "receipt" || name === "quote") setCreationOutcomes((current) => {
      const next = { ...current };
      delete next[name];
      return next;
    });
    setActionOutcomes((current) => {
      const next = { ...current };
      delete next[name];
      return next;
    });
    return {
      isCurrent,
      finish() {
        setInFlightCount((count) => Math.max(0, count - 1));
        if (!isCurrent()) return;
        setPendingActions((current) => {
          if (current[pendingKey]?.owner !== owner) return current;
          const next = { ...current };
          delete next[pendingKey];
          return next;
        });
      },
    };
  }

  function recheckDeniedAccess() {
    void verifyOrderAccess();
    quoteAccessRecheck.current?.();
  }

  async function runAction(
    name: string,
    path: string,
    payload: Record<string, unknown>,
    successText: string,
    refreshQuote = true,
  ) {
    const action = beginAction(name, selectedQuoteId);
    try {
      const result = await postAction(name, path, payload);
      // Keep unseen outcomes replayable; never reopen or credit another quote.
      if (!action.isCurrent() || displayedQuoteId.current !== selectedQuoteId) return;
      result.acknowledge();
      setActionOutcomes((current) => ({
        ...current, [name]: { quoteId: selectedQuoteId, kind: "success", message: successText },
      }));
      onMutation(refreshQuote ? selectedQuoteId : undefined);
    } catch (reason) {
      if (!action.isCurrent()) return;
      const message = reason instanceof Error ? reason.message : "The transaction request failed.";
      if (displayedQuoteId.current === selectedQuoteId) setActionOutcomes((current) => ({
        ...current, [name]: { quoteId: selectedQuoteId, kind: "error", message },
      }));
      // A changed quote does not invalidate a current-BU authorization check.
      if (reason instanceof ApiProblem && (reason.status === 403 || reason.status === 404)) {
        recheckDeniedAccess();
      }
    } finally {
      action.finish();
    }
  }

  async function submitReceipt(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!availability.receipt.enabled || !receiptSupplier) return;
    let lines: Record<string, unknown>[];
    let purchaseCommitmentId: number | undefined;
    let supplierId = Number(receiptSupplier);
    if (currentPackingReceipt) {
      const draft = currentPackingReceipt.draft;
      if (draft.unitId !== selectedUnitId || !draft.lines.length || draft.lines.length > 80
        || draft.lines.some((line, index) => (
          !locations.some((location) => location.id === line.locationId)
          || !Number.isSafeInteger(line.variantId) || line.variantId <= 0
          || moneyCents(line.quantity) <= 0n
          || (!draft.commitmentId && moneyCents(currentPackingReceipt.unitCosts[index]) <= 0n)
        ))) {
        recordCreationOutcome("receipt", "error", "Review every mapped line, authorized receiving location, quantity and synthetic QAR unit value before recording.");
        return;
      }
      if (draft.commitmentId) {
        const selected = draft.lines.map((line) => commitmentRows.find((row) =>
          row.commitment_id === draft.commitmentId && row.commitment_line_id === line.commitmentLineId
          && row.variant_id === line.variantId && row.supplier_id === draft.supplierId));
        if (commitmentReadState !== "ready" || selected.some((row, index) => !row
          || moneyCents(draft.lines[index].quantity) > moneyCents(row.remaining_quantity))
          || new Set(draft.lines.map((line) => line.commitmentLineId)).size !== draft.lines.length
          || Number(receiptSupplier) !== draft.supplierId) {
          recordCreationOutcome("receipt", "error", "Review the current commitment lines, supplier and remaining quantities again before recording.");
          return;
        }
        purchaseCommitmentId = draft.commitmentId;
        supplierId = draft.supplierId!;
      }
      lines = draft.lines.map((line, index) => ({
        variant_id: line.variantId,
        location_id: line.locationId,
        quantity: line.quantity,
        ...(draft.commitmentId
          ? { commitment_line_id: line.commitmentLineId }
          : { unit_cost: currentPackingReceipt.unitCosts[index] }),
      }));
    } else if (receiptCommitmentLines.length > 0) {
      const selectedRows = receiptCommitmentLines.map((draft) => (
        commitmentRows.find((row) => String(row.commitment_line_id) === draft.commitmentLineId)
      ));
      if (selectedRows.some((row) => !row)) {
        recordCreationOutcome("receipt", "error", "Select commitment lines available in the current business unit.");
        return;
      }
      const currentLines = selectedRows as PurchaseCommitmentLine[];
      const commitmentIds = new Set(currentLines.map((row) => row.commitment_id));
      const supplierIds = new Set(currentLines.map((row) => row.supplier_id));
      if (commitmentIds.size !== 1 || supplierIds.size !== 1
        || currentLines.length !== receiptCommitmentLines.length) {
        recordCreationOutcome("receipt", "error", "A committed receipt must use lines from one purchase commitment and supplier.");
        return;
      }
      purchaseCommitmentId = currentLines[0].commitment_id;
      supplierId = currentLines[0].supplier_id;
      if (Number(receiptSupplier) !== supplierId) {
        recordCreationOutcome("receipt", "error", "The selected supplier must match the purchase commitment.");
        return;
      }
      lines = receiptCommitmentLines.map((draft, index) => {
        const selected = currentLines[index];
        const locationId = Number(draft.locationId);
        return {
          commitment_line_id: selected.commitment_line_id,
          variant_id: selected.variant_id,
          location_id: locationId,
          quantity: draft.quantity,
        };
      });
      if (receiptCommitmentLines.some((draft) => !Number.isSafeInteger(Number(draft.locationId))
        || Number(draft.locationId) <= 0
        || !draft.quantity.trim()
        || !Number.isFinite(Number(draft.quantity))
        || Number(draft.quantity) <= 0)) {
        recordCreationOutcome("receipt", "error", "Each commitment line needs a receiving location and positive quantity.");
        return;
      }
    } else {
      const line = {
        variant_id: Number(receiptLine.variantId),
        location_id: Number(receiptLine.locationId),
        quantity: receiptLine.quantity,
        unit_cost: receiptLine.unitCost,
        ...(receiptLine.quoteLineId.trim() ? { quote_line_id: Number(receiptLine.quoteLineId) } : {}),
      };
      if (!Number.isSafeInteger(line.variant_id) || line.variant_id <= 0
        || !Number.isSafeInteger(line.location_id) || line.location_id <= 0
        || (line.quote_line_id !== undefined && (!Number.isSafeInteger(line.quote_line_id) || line.quote_line_id <= 0))) {
        recordCreationOutcome("receipt", "error", "Use positive catalog, location, and optional quote-line IDs.");
        return;
      }
      lines = [line];
    }
    const committedCurrency = purchaseCommitmentId
      ? commitmentRows.find((row) => row.commitment_id === purchaseCommitmentId)?.currency ?? "QAR"
      : null;
    const exchange = committedCurrency ? { ...receiptExchange, currency: committedCurrency } : receiptExchange;
    const exchangeIssue = exchangeProblem(exchange);
    if (exchangeIssue) {
      recordCreationOutcome("receipt", "error", exchangeIssue);
      return;
    }
    const chargeIssue = charges.map((charge, index) => {
      const problem = charge.amount.trim() && charge.category !== "clearance" ? exchangeProblem(charge.exchange) : null;
      return problem ? `Landed charge ${index + 1}: ${problem}` : null;
    }).find(Boolean);
    if (chargeIssue) {
      recordCreationOutcome("receipt", "error", chargeIssue);
      return;
    }
    const payload = {
      business_unit_id: selectedUnitId,
      supplier_id: supplierId,
      ...(purchaseCommitmentId ? { purchase_commitment_id: purchaseCommitmentId } : {}),
      ...exchangePayload(exchange),
      lines,
      charges: charges.filter((charge) => charge.amount.trim()).map((charge) => ({
        category: charge.category,
        amount: charge.amount,
        eligible: true,
        ...chargeExchangePayload(charge.category, charge.exchange),
      })),
    };
    const action = beginAction("receipt");
    try {
      const result = await postAction("receipt", "/api/purchasing/receipts/", payload);
      if (!action.isCurrent()) return;
      result.acknowledge();
      recordCreationOutcome("receipt", "success", "Synthetic goods receipt recorded. The receipt and landed-charge registers have been refreshed.");
      if (currentPackingReceipt) setPackingReceipt(null);
      if (purchaseCommitmentId) setReceiptCommitmentLines([]);
      onMutation();
      onReceiptSuccess?.();
    } catch (reason) {
      if (!action.isCurrent()) return;
      recordCreationOutcome("receipt", "error", reason instanceof Error ? reason.message : "Receipt request failed.");
      if (reason instanceof ApiProblem && (reason.status === 403 || reason.status === 404)) recheckDeniedAccess();
    } finally {
      action.finish();
    }
  }

  async function submitCommitment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!availability.receipt.enabled || !receiptSupplier) return;
    const preparedLines = commitmentLines.map((line) => ({
      variant_id: Number(line.variantId),
      ordered_quantity: line.orderedQuantity,
      unit_cost: line.unitCost,
    }));
    if (preparedLines.some((line) => !Number.isSafeInteger(line.variant_id) || line.variant_id <= 0)) {
      recordCreationOutcome("commitment", "error", "Enter a positive catalog variant ID on every commitment line.");
      return;
    }
    if (preparedLines.length === 0 || preparedLines.length > 80) {
      recordCreationOutcome("commitment", "error", "A commitment must contain between one and 80 lines.");
      return;
    }
    const exchangeIssue = exchangeProblem(commitmentExchange);
    if (exchangeIssue) {
      recordCreationOutcome("commitment", "error", exchangeIssue);
      return;
    }
    const payload = {
      business_unit_id: selectedUnitId,
      supplier_id: Number(receiptSupplier),
      ...exchangePayload(commitmentExchange),
      lines: preparedLines,
    };
    const action = beginAction("commitment");
    try {
      const result = await postAction("commitment", "/api/purchasing/commitments/", payload);
      if (!action.isCurrent()) return;
      result.acknowledge();
      const returnedLines = Array.isArray(result.body.lines)
        ? result.body.lines as Array<{
          commitment_line_id?: unknown;
          ordered_quantity?: unknown;
          remaining_quantity?: unknown;
        }>
        : [];
      const receiptDraftLines = returnedLines.flatMap((line) => {
        const lineId = Number(line.commitment_line_id);
        if (!Number.isSafeInteger(lineId) || lineId <= 0) return [];
        return [{
          commitmentLineId: String(lineId),
          locationId: "",
          quantity: String(line.remaining_quantity ?? line.ordered_quantity ?? "1"),
        }];
      });
      if (receiptDraftLines.length > 0) {
        setReceiptCommitmentLines(receiptDraftLines);
      }
      setCommitmentLines([{ variantId: "", orderedQuantity: "1", unitCost: "" }]);
      recordCreationOutcome("commitment", "success", `Synthetic ${commitmentExchange.currency} purchase commitment created. No stock was received or posted by this action.`);
      onMutation();
    } catch (reason) {
      if (!action.isCurrent()) return;
      recordCreationOutcome("commitment", "error", reason instanceof Error ? reason.message : "Commitment request failed.");
      if (reason instanceof ApiProblem && (reason.status === 403 || reason.status === 404)) recheckDeniedAccess();
    } finally {
      action.finish();
    }
  }

  async function submitQuote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQuoteFormError("");
    if (!availability.quote.enabled || !quoteCustomer) return;
    if (quoteDraftIsStale) {
      setQuoteFormError("Quote lines belong to a previous business-unit or staff context. Start fresh lines for the current context before submitting.");
      return;
    }
    if (!currentQuotation || currentQuotation.business_unit.id !== selectedUnitId) {
      setQuoteFormError("Current business-unit quotation choices are unavailable. Retry after verifying the current business unit.");
      return;
    }
    const requestedDiscount = Number(discountPercent);
    if (!Number.isFinite(requestedDiscount) || requestedDiscount < 0 || requestedDiscount > quoteRequestCeiling) {
      setQuoteFormError(isStaff
        ? "Staff may request discounts up to 50%. Discounts above the salesperson's own ceiling enter the existing pending-manager-approval workflow."
        : `Discount must be between 0% and your role ceiling of ${quoteDiscountCeiling}%. The server makes the final decision.`);
      return;
    }
    const quoteLines: QuoteLineInput[] = [
      { variantId: quoteVariant, locationId: quoteLocation, quantity: quoteQuantity, specifications: quoteSpecifications, unit_en: quoteUnitEn, unit_ar: quoteUnitAr },
      ...additionalQuoteLines,
    ];
    const estimates = validateQuoteLineEstimates(
      quoteLines, prices, discountPercent, quoteKind, locations.map((location) => location.id),
    );
    if (!estimates.ok) {
      setQuoteFormError(estimates.error);
      return;
    }
    if (!customers.some((customer) => String(customer.counterparty_id) === quoteCustomer)) {
      setQuoteFormError("Select a customer currently available in this business unit.");
      return;
    }
    const lines = quoteLines.map((draft) => {
      const line: Record<string, unknown> = {
        variant_id: Number(draft.variantId),
        quantity: draft.quantity,
        specifications: JSON.parse(draft.specifications) as Record<string, unknown>,
      };
      if (quoteKind === "stock") line.location_id = Number(draft.locationId);
      return line;
    });
    const presentationPayload: Record<string, unknown> = { ...presentation };
    for (const key of [
      "contact_person_en", "contact_person_ar", "project_en", "project_ar",
      "address_en", "address_ar", "phone", "mobile", "email", "notes_en", "notes_ar",
    ]) {
      const value = presentation[key]?.trim();
      if (value) presentationPayload[key] = value;
      else delete presentationPayload[key];
    }
    const lineUnits = presentationLineUnits(quoteLines);
    if (lineUnits) presentationPayload.line_units = lineUnits;
    const payload: Record<string, unknown> = {
      business_unit_id: selectedUnitId,
      customer_id: Number(quoteCustomer),
      kind: quoteKind,
      discount_percent: discountPercent,
      discount_reason: discountReason,
      payment_plan: quoteKind === "stock" ? "full" : paymentPlan,
      lines,
      presentation: presentationPayload,
    };
    if (quoteKind === "special_order") {
      payload.payment_terms_reason = paymentTermsReason;
      if (paymentPlan === "downpayment") payload.downpayment_percent = downpaymentPercent;
    }
    const action = beginAction("quote");
    const navigationVersion = quoteNavigationVersion.current;
    const ownsSelection = () => displayedQuoteId.current === selectedQuoteId
      && quoteNavigationVersion.current === navigationVersion;
    try {
      const { body, acknowledge } = await postAction("quote", "/api/purchasing/quotes/", payload);
      if (!action.isCurrent() || !ownsSelection()) return;
      const id = Number(body.quote_id);
      if (!Number.isSafeInteger(id) || id <= 0) throw new Error("The API response did not include a valid quote ID.");
      acknowledge();
      recordCreationOutcome("quote", "success", `Synthetic quote #${id} created. The server determines whether a price approval is pending.`);
      onQuoteCreated(id);
    } catch (reason) {
      if (!action.isCurrent()) return;
      if (ownsSelection()) recordCreationOutcome("quote", "error", reason instanceof Error ? reason.message : "Quote request failed.");
      if (reason instanceof ApiProblem && (reason.status === 403 || reason.status === 404)) recheckDeniedAccess();
    } finally {
      action.finish();
    }
  }

  function updateFirstQuoteLine(field: keyof QuoteLineInput, value: string) {
    if (field === "variantId") setQuoteVariant(value);
    if (field === "locationId") setQuoteLocation(value);
    if (field === "quantity") setQuoteQuantity(value);
    if (field === "specifications") setQuoteSpecifications(value);
    if (field === "unit_en") setQuoteUnitEn(value);
    if (field === "unit_ar") setQuoteUnitAr(value);
  }

  function updateAdditionalQuoteLine(key: number, field: keyof QuoteLineInput, value: string) {
    setAdditionalQuoteLines((current) => current.map((line) => line.key === key ? { ...line, [field]: value } : line));
  }

  function addQuoteLine() {
    if (1 + additionalQuoteLines.length >= 40 || quoteDraftIsStale) return;
    const key = ++quoteLineKey.current;
    setAdditionalQuoteLines((current) => [...current, {
      key, variantId: "", locationId: "", quantity: "1", specifications: "{}", unit_en: "", unit_ar: "",
    }]);
    setQuoteLineRevision((revision) => revision + 1);
  }

  function removeQuoteLine(key: number) {
    if (1 + additionalQuoteLines.length <= 1) return;
    setAdditionalQuoteLines((current) => current.filter((line) => line.key !== key));
    setQuoteLineRevision((revision) => revision + 1);
  }

  function removeFirstQuoteLine() {
    if (additionalQuoteLines.length === 0) return;
    const [promoted, ...remaining] = additionalQuoteLines;
    setQuoteVariant(promoted.variantId);
    setQuoteLocation(promoted.locationId);
    setQuoteQuantity(promoted.quantity);
    setQuoteSpecifications(promoted.specifications);
    setQuoteUnitEn(promoted.unit_en ?? "");
    setQuoteUnitAr(promoted.unit_ar ?? "");
    setAdditionalQuoteLines(remaining);
    setQuoteLineRevision((revision) => revision + 1);
  }

  function resetQuoteLinesForCurrentContext() {
    setQuoteVariant("");
    setQuoteLocation("");
    setQuoteQuantity("1");
    setQuoteSpecifications("{}");
    setQuoteUnitEn("");
    setQuoteUnitAr("");
    setAdditionalQuoteLines([]);
    setQuoteCustomer("");
    setPresentation({
      valid_until: "", payment_terms_en: "", payment_terms_ar: "",
      delivery_time_en: "", delivery_time_ar: "", department_en: "", department_ar: "",
    });
    setQuoteDraftContext(`${selectedUnitId}:${username}`);
    setQuoteLineRevision((revision) => revision + 1);
    setQuoteFormError("");
  }

  function addCharge() {
    setCharges((current) => [...current, { category: "shipping", amount: "", exchange: qarExchange }]);
  }

  function addCommitmentDraftLine() {
    if (commitmentLines.length >= 80) return;
    setCommitmentLines((current) => [
      ...current,
      { variantId: "", orderedQuantity: "1", unitCost: "" },
    ]);
  }

  function addReceiptCommitmentDraftLine() {
    const hasRemainingCommitmentLine = commitmentRows.some((row) => {
      const alreadySelected = receiptCommitmentLines.some(
        (draft) => Number(draft.commitmentLineId) === row.commitment_line_id,
      );
      return (!linkedReceiptCommitmentId || row.commitment_id === linkedReceiptCommitmentId)
        && moneyCents(row.remaining_quantity) > 0n
        && !alreadySelected;
    });
    if (!hasRemainingCommitmentLine || receiptCommitmentLines.length >= 80) return;
    setReceiptCommitmentLines((current) => [
      ...current,
      { commitmentLineId: "", locationId: "", quantity: "1" },
    ]);
  }

  function updateReceiptCommitmentDraftLine(
    lineIndex: number,
    updates: Partial<CommitmentReceiptDraftLine>,
  ) {
    setReceiptCommitmentLines((current) => current.map((line, index) => (
      index === lineIndex ? { ...line, ...updates } : line
    )));
  }

  const paymentAllowed = availability.payment.enabled
    && (paymentPurpose === "quote"
      ? availability.quoteBalancePayment.enabled
      : availability.storagePayment.enabled);
  const paymentReason = paymentPurpose === "quote"
    ? availability.quoteBalancePayment.reason
    : availability.storagePayment.reason;

  return (
    <section className="pw-card pw-transactions" data-procurement-view={procurementMode ? focusedView : undefined} data-pending-operation-count={inFlightCount} hidden={!transactionPaneActive} aria-labelledby="pw-transactions-title">
      <div className="pw-card-heading" hidden={focusedView !== undefined}>
        <div>
          <span className="pw-kicker">EXISTING AUTHENTICATED API ACTIONS</span>
          <h2 id="pw-transactions-title">{retailMode ? "Sales transaction controls" : "Transaction workbench"}</h2>
          <p>{deliveryMode
            ? "Existing readiness, delivery and storage actions remain gated by the server’s unit-scoped checks."
            : retailMode
              ? "Quote creation, collection, discount decisions and holds remain subject to existing unit-scoped access and configuration."
              : procurementMode
                ? "Commitment and receipt actions use existing unit-scoped authorization. This presentation adds no transaction authority."
                : warehouseMode
                  ? "Receipt and commitment actions use the existing unit-scoped receipt authorization and required configuration."
              : "Actions are enabled only after this server confirms unit-scoped order access and required configuration."}</p>
        </div>
        <span className={`pw-access-chip ${availability.orderAccess.enabled ? "is-authorized" : ""}`}>
          {orderAccess === "loading" ? "Checking access" : availability.orderAccess.enabled ? "Order access confirmed" : "Actions gated"}
        </span>
      </div>
      {transactionPaneActive && !availability.orderAccess.enabled && (
        <div className={`pw-notice ${orderAccess === "error" ? "pw-error" : ""}`} role="status">
          <strong>{orderAccess === "loading" ? "Checking access" : "Transactions unavailable"}</strong>
          <p>{accessMessage || availability.orderAccess.reason}</p>
        </div>
      )}
      <div className="pw-caveat pw-transaction-caveat" hidden={focusedView !== undefined}>
        <strong>TEST ONLY · SIMULATED WORKFLOW</strong>
        <span>{deliveryMode
          ? "No picking, partial delivery, transport scheduling, finance or refund actions are available here. The API rechecks every existing state transition."
          : retailMode
            ? "No payment provider is connected. Retail hides procurement, receipt, return, readiness, delivery and storage-write controls; the API rechecks every available quote action."
          : receivingMode
              ? "This view exposes only existing receipt/commitment preparation and submission. No transfer-posting action or capacity inference is available."
            : "No payment provider is connected. The API rechecks permissions, state, and policy constraints for every action."}</span>
      </div>

      <div className="pw-transaction-grid">
        {!deliveryMode && !retailMode && <section className="pw-transaction-panel" hidden={focusedView !== undefined && !["receipt", "packing", "commitments", "progress", "receiving", "transactions"].includes(focusedView)} aria-labelledby={showFunction("receipt") ? "pw-receipt-title" : undefined}>
          <div className="pw-transaction-title" hidden={!showFunction("receipt")}><span className="pw-step">01</span><div><h3 id="pw-receipt-title">Receive imported goods</h3><p>Record supplier goods and eligible purchase-value landed charges.</p></div></div>
          <details className="pw-action-form" data-workspace-function="packing" hidden={!showFunction("packing")} open={focusedView === "packing" || undefined}>
            <summary>Prepare a receipt from a synthetic packing list</summary>
            <p className="pw-action-note">
              Prepare a direct receipt or explicitly select an existing purchase commitment and its lines.
              An upload never infers a supplier, commitment, item match or unit conversion.
            </p>
            <PackingListPreview
              selectedUnitId={selectedUnitId}
              selectedUnitName={selectedUnitName}
              preparation={{
                contextKey: packingContext,
                locations,
                commitments: { state: commitmentReadState, error: commitmentReadError, lines: commitmentRows },
                enabled: availability.receipt.enabled && !isPending("receipt"),
                reason: isPending("receipt") ? "Wait for the current receipt request to finish." : availability.receipt.reason,
                onReviewed: (draft) => {
                  if (!availability.receipt.enabled || isPending("receipt")
                    || draft.unitId !== selectedUnitId) return;
                  if (draft.commitmentId) {
                    if (commitmentReadState !== "ready" || !draft.supplierId
                      || !draft.lines.every((line) => commitmentRows.some((row) =>
                        row.commitment_id === draft.commitmentId && row.commitment_line_id === line.commitmentLineId
                        && row.supplier_id === draft.supplierId && row.variant_id === line.variantId))) return;
                    setReceiptSupplier(String(draft.supplierId));
                  }
                  setReceiptCommitmentLines([]);
                  setPackingReceipt({ context: packingContext, draft, unitCosts: draft.lines.map(() => "") });
                },
                onSourceChange: () => setPackingReceipt(null),
              }}
            />
          </details>
          <form className="pw-action-form" hidden={!showFunction("receipt")} onSubmit={(event) => void submitReceipt(event)}>
            <label htmlFor="pw-receipt-supplier">Synthetic supplier</label>
            <select id="pw-receipt-supplier" value={receiptSupplier} onChange={(event) => setReceiptSupplier(event.target.value)} required disabled={!availability.receipt.enabled || Boolean(linkedReceiptSupplierId)}>
              <option value="">Select a configured supplier</option>
              {suppliers.map((party) => <option key={party.counterparty_id} value={party.counterparty_id}>{party.code} · {party.name}</option>)}
            </select>
            {linkedReceiptSupplierId && <p className="pw-action-note">Supplier is fixed by the selected purchase commitment.</p>}
            <PurchaseCurrencyFields idPrefix="pw-receipt" value={receiptExchange} onChange={setReceiptExchange}
              disabled={!availability.receipt.enabled}
              lockedCurrency={receiptLockedCurrency} />
            {currentPackingReceipt ? (
              <section aria-label="Reviewed packing-list receipt draft">
                <h4>Reviewed packing-list lines</h4>
                <p className="pw-action-note">
                  {currentPackingReceipt.draft.fileName} · {currentPackingReceipt.draft.lines.length} lines.
                  Nothing has been posted. {currentPackingReceipt.draft.commitmentId
                    ? `Commitment #${currentPackingReceipt.draft.commitmentId}: immutable line values are used without exposing costs. Review quantities and any charges,`
                    : "Enter synthetic QAR unit values below, review supplier and any charges,"}
                  then explicitly record the receipt. Source units, references and warehouse measurements remain local;
                  the existing receipt API does not store them or convert units.
                </p>
                {currentPackingReceipt.draft.lines.map((line, index) => (
                  <div key={line.sourceRow} className="pw-action-subform">
                    <p>Source row {line.sourceRow} · {line.itemCode} · {line.variantSku} · {line.itemDescription}</p>
                    <p>Confirmed quantity {line.quantity} · {line.locationCode}. Source: {line.sourceQuantity} {line.sourceUnit}.</p>
                    {line.commitmentLineId && <p>Commitment line #{line.commitmentLineId}</p>}
                    {!currentPackingReceipt.draft.commitmentId && <label>
                      Packing row {line.sourceRow} · Synthetic supplier unit value ({receiptLockedCurrency ?? receiptExchange.currency})
                      <input type="number" min="0.01" step="0.01" required
                        value={currentPackingReceipt.unitCosts[index]}
                        disabled={!availability.receipt.enabled || isPending("receipt")}
                        onChange={(event) => {
                          const value = event.target.value;
                          setPackingReceipt((current) => current && current.context === packingContext
                            ? { ...current, unitCosts: current.unitCosts.map((cost, costIndex) => costIndex === index ? value : cost) }
                            : current);
                        }} />
                    </label>}
                  </div>
                ))}
                <button type="button" className="pw-button pw-button-muted"
                  disabled={isPending("receipt")}
                  onClick={() => setPackingReceipt(null)}>Discard prepared lines; use manual receipt</button>
              </section>
            ) : receiptCommitmentLines.length === 0 ? (
              <>
                <div className="pw-charge-header">
                  <strong>Direct receipt</strong>
                  <button
                    type="button"
                    className="pw-button pw-button-muted"
                    onClick={() => setReceiptCommitmentLines([{ commitmentLineId: "", locationId: "", quantity: "1" }])}
                    disabled={!availability.receipt.enabled || commitmentReadState !== "ready"
                      || !commitmentRows.some((row) => moneyCents(row.remaining_quantity) > 0n)}
                  >
                    Add commitment lines to receipt
                  </button>
                </div>
                <p className="pw-action-note">A direct receipt is not linked to a purchase commitment.</p>
                <div className="pw-form-grid">
                  <label>Catalog variant ID<input inputMode="numeric" pattern="[0-9]*" min="1" required value={receiptLine.variantId} onChange={(event) => setReceiptLine({ ...receiptLine, variantId: event.target.value })} disabled={!availability.receipt.enabled} /></label>
                  <label>Receiving location<select value={receiptLine.locationId} onChange={(event) => setReceiptLine({ ...receiptLine, locationId: event.target.value })} required disabled={!availability.receipt.enabled}><option value="">Select location</option>{locations.map((location) => <option key={location.id} value={location.id}>{location.code} · {location.name}</option>)}</select></label>
                  <label>Quantity<input type="number" min="0.01" step="0.01" required value={receiptLine.quantity} onChange={(event) => setReceiptLine({ ...receiptLine, quantity: event.target.value })} disabled={!availability.receipt.enabled} /></label>
                  <label>Supplier unit cost ({receiptLockedCurrency ?? receiptExchange.currency})<input type="number" min="0.01" step="0.01" required value={receiptLine.unitCost} onChange={(event) => setReceiptLine({ ...receiptLine, unitCost: event.target.value })} disabled={!availability.receipt.enabled} /></label>
                </div>
                <label>Optional linked quote-line ID<input inputMode="numeric" pattern="[0-9]*" min="1" value={receiptLine.quoteLineId} onChange={(event) => setReceiptLine({ ...receiptLine, quoteLineId: event.target.value })} disabled={!availability.receipt.enabled} /></label>
              </>
            ) : (
              <section className="pw-action-form pw-action-subform" aria-labelledby="pw-commitment-receipt-lines-title">
                <h4 id="pw-commitment-receipt-lines-title">Commitment receipt lines</h4>
                <p className="pw-action-note">One receipt can cover several lines from one commitment. Each quantity is checked against that line’s remaining amount under a server-side row lock.</p>
                {receiptCommitmentLines.map((draft, lineIndex) => {
                  const selected = selectedReceiptCommitments[lineIndex];
                  const options = commitmentOptionsForReceiptLine(lineIndex);
                  const selectedIsInOptions = options.some((row) => String(row.commitment_line_id) === draft.commitmentLineId);
                  return (
                    <div className="pw-charge-row" key={`commitment-receipt-${lineIndex}`}>
                      <label>
                        Commitment line {lineIndex + 1}
                        <select
                          value={draft.commitmentLineId}
                          onChange={(event) => {
                            const nextId = event.target.value;
                            const next = commitmentRows.find((row) => String(row.commitment_line_id) === nextId);
                            updateReceiptCommitmentDraftLine(lineIndex, {
                              commitmentLineId: nextId,
                              ...(next ? { quantity: next.remaining_quantity } : {}),
                            });
                            if (next) setReceiptSupplier(String(next.supplier_id));
                          }}
                          required
                          disabled={!availability.receipt.enabled || commitmentReadState !== "ready"}
                        >
                          <option value="">Select a commitment line</option>
                          {selected && !selectedIsInOptions && (
                            <option value={selected.commitment_line_id}>
                              #{selected.commitment_id} · {selected.item_description} · {selected.remaining_quantity} remaining
                            </option>
                          )}
                          {draft.commitmentLineId && !selected && (
                            <option value={draft.commitmentLineId}>Previously selected line is unavailable in this business unit</option>
                          )}
                          {options.map((row) => (
                            <option key={row.commitment_line_id} value={row.commitment_line_id}>
                              Commitment #{row.commitment_id} · {row.item_description} · {row.variant_sku}
                              · {row.received_to_date}/{row.ordered_quantity} received · {row.remaining_quantity} remaining
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Receiving location
                        <select
                          value={draft.locationId}
                          onChange={(event) => updateReceiptCommitmentDraftLine(lineIndex, { locationId: event.target.value })}
                          required
                          disabled={!availability.receipt.enabled}
                        >
                          <option value="">Select location</option>
                          {locations.map((location) => <option key={location.id} value={location.id}>{location.code} · {location.name}</option>)}
                        </select>
                      </label>
                      <label>
                        Quantity from line
                        <input
                          type="number"
                          min="0.01"
                          step="0.01"
                          max={selected?.remaining_quantity}
                          required
                          value={draft.quantity}
                          onChange={(event) => updateReceiptCommitmentDraftLine(lineIndex, { quantity: event.target.value })}
                          disabled={!availability.receipt.enabled}
                        />
                      </label>
                      {receiptCommitmentLines.length > 1 && (
                        <button
                          type="button"
                          aria-label={`Remove receipt commitment line ${lineIndex + 1}`}
                          className="pw-button pw-button-muted"
                          onClick={() => setReceiptCommitmentLines((current) => current.filter((_, index) => index !== lineIndex))}
                          disabled={!availability.receipt.enabled}
                        >
                          Remove line
                        </button>
                      )}
                      {selected && (
                        <p className="pw-action-note">
                          Ordered {selected.ordered_quantity}; received {selected.received_to_date}; remaining {selected.remaining_quantity}.
                          Supplier unit value comes from the immutable synthetic commitment line.
                        </p>
                      )}
                    </div>
                  );
                })}
                <div className="pw-charge-header">
                  <button
                    type="button"
                    className="pw-button pw-button-muted"
                    onClick={addReceiptCommitmentDraftLine}
                    disabled={!availability.receipt.enabled || !linkedReceiptCommitmentId
                      || receiptCommitmentLines.length >= 80
                      || commitmentRows.filter((row) => row.commitment_id === linkedReceiptCommitmentId
                        && moneyCents(row.remaining_quantity) > 0n
                        && !receiptCommitmentLines.some((draft) => Number(draft.commitmentLineId) === row.commitment_line_id)).length === 0}
                  >
                    Add another commitment line
                  </button>
                  <button
                    type="button"
                    className="pw-button pw-button-muted"
                    onClick={() => setReceiptCommitmentLines([])}
                    disabled={!availability.receipt.enabled}
                  >
                    Use direct receipt instead
                  </button>
                </div>
              </section>
            )}
            <div className="pw-charge-header"><strong>Eligible landed charges</strong><button type="button" className="pw-button pw-button-muted" onClick={addCharge} disabled={!availability.receipt.enabled}>Add charge</button></div>
            {charges.map((charge, index) => (
              <div className="pw-charge-row" key={`charge-${index}`}>
                <label>Charge type<select value={charge.category} onChange={(event) => setCharges((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, category: event.target.value as ChargeDraft["category"] } : item))} disabled={!availability.receipt.enabled}><option value="shipping">Shipping</option><option value="clearance">Clearance</option><option value="other">Other eligible</option></select></label>
                <label>Amount ({charge.category === "clearance" ? "QAR" : charge.exchange.currency})<input type="number" min="0.01" step="0.01" value={charge.amount} onChange={(event) => setCharges((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, amount: event.target.value } : item))} disabled={!availability.receipt.enabled} /></label>
                <ChargeCurrencyFields idPrefix={`pw-charge-${index}`} category={charge.category} value={charge.exchange} disabled={!availability.receipt.enabled}
                  onChange={(next) => setCharges((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, exchange: next } : item))} />
                <button type="button" aria-label={`Remove landed charge ${index + 1}`} className="pw-button pw-button-muted" onClick={() => setCharges((current) => current.filter((_, itemIndex) => itemIndex !== index))} disabled={!availability.receipt.enabled}>Remove</button>
              </div>
            ))}
            <p className="pw-action-note">Shipping and other charges may be billed in USD or EUR at a rate you enter; customs clearance is always QAR. Every charge is capitalized in QAR. Only eligible synthetic charges can be submitted; ineligible charges fail closed because their accounting treatment is not configured.</p>
            {!availability.receipt.enabled && <p className="pw-disabled-reason">{availability.receipt.reason}</p>}
            <button className="pw-button pw-button-primary" type="submit" disabled={!availability.receipt.enabled || isPending("receipt")}>{isPending("receipt") ? "Recording…" : "Record synthetic receipt"}</button>
          </form>
          <div className="pw-action-form pw-action-subform" hidden={!showFunction("commitments")} aria-labelledby="pw-commitment-create-title">
            <h4 id="pw-commitment-create-title">Create a synthetic purchase commitment</h4>
            <p className="pw-action-note">Uses the supplier selected above. Add a separate catalog variant, ordered quantity, and synthetic QAR unit value for each line. This test-only action does not receive stock or define approved procurement policy.</p>
            <form onSubmit={(event) => void submitCommitment(event)}>
              <PurchaseCurrencyFields idPrefix="pw-commitment" value={commitmentExchange} onChange={setCommitmentExchange} disabled={!availability.receipt.enabled} />
              {commitmentLines.map((line, lineIndex) => (
                <div className="pw-charge-row" key={`commitment-draft-${lineIndex}`}>
                  <strong>Commitment line {lineIndex + 1}</strong>
                  <label>
                    Line {lineIndex + 1} · Catalog variant ID
                    <input inputMode="numeric" pattern="[0-9]*" min="1" required value={line.variantId} onChange={(event) => setCommitmentLines((current) => current.map((item, index) => index === lineIndex ? { ...item, variantId: event.target.value } : item))} disabled={!availability.receipt.enabled} />
                  </label>
                  <label>
                    Line {lineIndex + 1} · Ordered quantity
                    <input type="number" min="0.01" step="0.01" required value={line.orderedQuantity} onChange={(event) => setCommitmentLines((current) => current.map((item, index) => index === lineIndex ? { ...item, orderedQuantity: event.target.value } : item))} disabled={!availability.receipt.enabled} />
                  </label>
                  <label>
                    Line {lineIndex + 1} · Synthetic supplier unit value ({commitmentExchange.currency})
                    <input type="number" min="0.01" step="0.01" required value={line.unitCost} onChange={(event) => setCommitmentLines((current) => current.map((item, index) => index === lineIndex ? { ...item, unitCost: event.target.value } : item))} disabled={!availability.receipt.enabled} />
                  </label>
                  {commitmentLines.length > 1 && (
                    <button
                      type="button"
                      aria-label={`Remove commitment line ${lineIndex + 1}`}
                      className="pw-button pw-button-muted"
                      onClick={() => setCommitmentLines((current) => current.filter((_, index) => index !== lineIndex))}
                      disabled={!availability.receipt.enabled}
                    >
                      Remove line
                    </button>
                  )}
                </div>
              ))}
              <button type="button" className="pw-button pw-button-muted" onClick={addCommitmentDraftLine} disabled={!availability.receipt.enabled || commitmentLines.length >= 80}>Add another commitment line</button>
              {!availability.receipt.enabled && <p className="pw-disabled-reason">{availability.receipt.reason}</p>}
              <button className="pw-button pw-button-muted" type="submit" disabled={!availability.receipt.enabled || isPending("commitment")}>{isPending("commitment") ? "Saving…" : "Create synthetic purchase commitment"}</button>
            </form>
          </div>
          <section className="pw-action-form pw-action-subform" hidden={!showFunction("progress")} aria-labelledby="pw-commitment-progress-title">
            <h4 id="pw-commitment-progress-title">Purchase commitment receipt progress</h4>
            <p className="pw-action-note">Synthetic QAR commitment quantities only. Supplier unit values are not included in this progress view.</p>
            {commitmentReadState === "loading" && <p role="status">Loading authorized commitment progress…</p>}
            {commitmentReadState === "error" && <p role="alert">{commitmentReadError || "Commitment progress is unavailable."}</p>}
            {commitmentReadState === "ready" && commitmentRows.length === 0 && <p>No synthetic purchase commitments are available for this business unit.</p>}
            {commitmentReadState === "ready" && commitmentRows.length > 0 && (
              <div className="pw-table-wrap">
                <table className="pw-table">
                  <caption>Synthetic purchase commitment quantities for {selectedUnitName}</caption>
                  <thead><tr><th scope="col">Commitment / line</th><th scope="col">Item / variant</th><th scope="col">Ordered</th><th scope="col">Received to date</th><th scope="col">Remaining</th></tr></thead>
                  <tbody>{commitmentRows.map((row) => (
                    <tr key={row.commitment_line_id}>
                      <td>#{row.commitment_id} / #{row.commitment_line_id}</td>
                      <td>{row.item_description} · {row.variant_sku}</td>
                      <td>{row.ordered_quantity}</td>
                      <td>{row.received_to_date}</td>
                      <td>{row.remaining_quantity}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </section>
        </section>}

        {!deliveryMode && !receivingMode && <section className="pw-transaction-panel" hidden={!showFunction("quote")} aria-labelledby="pw-create-quote-title">
          <div className="pw-transaction-title"><span className="pw-step">{retailMode ? "01" : "02"}</span><div><h3 id="pw-create-quote-title">{retailMode ? "Create a retail quote" : "Create a quote / order"}</h3><p>Uses the server-approved price snapshot. Discount and payment terms require explicit reasons.</p></div></div>
          <form className="pw-action-form" onSubmit={(event) => void submitQuote(event)}>
            <input type="hidden" name="quote-line-draft-revision" value={quoteLineRevision} />
            <input type="hidden" name="quote-line-identities" value={[
              "first", ...additionalQuoteLines.map((line) => String(line.key)),
            ].join(",")} />
            <label htmlFor="pw-quote-kind">{retailMode ? "Quote type" : "Order kind"}</label>
            <select id="pw-quote-kind" value={quoteKind} onChange={(event) => setQuoteKind(event.target.value as "stock" | "special_order")} disabled={!availability.quote.enabled || quoteDraftIsStale}>
              <option value="stock">Available stock</option><option value="special_order">Special order</option>
            </select>
            <label htmlFor="pw-quote-customer-search">Find customer (code or name)</label>
            <input id="pw-quote-customer-search" type="search" maxLength={80} value={customerSearch} placeholder="Type at least 2 characters"
              onChange={(event) => setCustomerSearch(event.target.value)} disabled={!availability.quote.enabled || quoteDraftIsStale} />
            <small className="pw-item-search-status" aria-live="polite">{customerResults && customerResults.term === customerSearch.trim()
              ? `${customerResults.rows.length}${customerResults.more ? "+" : ""} matching customer${customerResults.rows.length === 1 ? "" : "s"}`
              : `Showing the first ${currentQuotation?.customers.length ?? 0} of ${currentQuotation?.customer_count ?? currentQuotation?.customers.length ?? 0} customers. Search to find any customer.`}</small>
            <label htmlFor="pw-quote-customer">Synthetic customer</label>
            <select id="pw-quote-customer" value={quoteCustomer} onChange={(event) => {
              const row = customers.find((party) => String(party.counterparty_id) === event.target.value);
              if (row) setPickedCustomers((current) => ({
                context: choiceContext,
                rows: { ...(current.context === choiceContext ? current.rows : {}), [String(row.counterparty_id)]: row },
              }));
              setQuoteCustomer(event.target.value);
            }} required disabled={!availability.quote.enabled || quoteDraftIsStale}>
              <option value="">Select a configured customer</option>{customers.map((party) => <option key={party.counterparty_id} value={party.counterparty_id}>{party.code} · {party.display_name}</option>)}
            </select>
            {selectedQuoteCustomer && (
              <div className="pw-quote-customer-preview" aria-live="polite">
                <strong>Client: {selectedQuoteCustomer.display_name}</strong>
                <span>Contact: {selectedQuoteCustomer.contact_en || "English contact not supplied"}</span>
                <span>Arabic name: {selectedQuoteCustomer.name_ar
                  ? <b lang="ar" dir="rtl">{selectedQuoteCustomer.name_ar}</b>
                  : <b>Arabic customer name unavailable</b>}</span>
                <span>Arabic contact: {selectedQuoteCustomer.contact_ar
                  ? <b lang="ar" dir="rtl">{selectedQuoteCustomer.contact_ar}</b>
                  : <b>Arabic customer contact unavailable</b>}</span>
                {!selectedQuoteCustomer.name_ar && <small>Arabic customer name not supplied</small>}
                {!selectedQuoteCustomer.contact_ar && <small>Arabic customer contact not supplied</small>}
              </div>
            )}
            {quoteDraftIsStale && (
              <div className="pw-notice pw-error" role="alert">
                <strong>Quote line context is out of date</strong>
                <p>These selections were made for another business unit or staff context. They are retained for review but cannot be included in a current submission.</p>
                <button type="button" data-quote-draft-action className="pw-button pw-button-muted" onClick={resetQuoteLinesForCurrentContext}>Start fresh quote lines for this unit</button>
              </div>
            )}
            <PurchasingQuoteLineFields
              first index={0}
              line={quoteLineInputs[0]}
              kind={quoteKind}
              prices={prices}
              locations={locations}
              searchItems={searchQuoteItems}
              onPick={(row) => pickPrice(row as QuotePriceRow)}
              disabled={!availability.quote.enabled || quoteDraftIsStale}
              onChange={updateFirstQuoteLine}
              onRemove={additionalQuoteLines.length > 0 ? removeFirstQuoteLine : undefined}
            />
            {additionalQuoteLines.map((line, index) => (
              <PurchasingQuoteLineFields
                key={line.key}
                index={index + 1}
                line={line}
                kind={quoteKind}
                prices={prices}
                locations={locations}
                searchItems={searchQuoteItems}
                onPick={(row) => pickPrice(row as QuotePriceRow)}
                disabled={!availability.quote.enabled || quoteDraftIsStale}
                onChange={(field, value) => updateAdditionalQuoteLine(line.key, field, value)}
                onRemove={() => removeQuoteLine(line.key)}
              />
            ))}
            <button type="button" id="pw-add-quote-line" data-quote-draft-action className="pw-button pw-button-muted" onClick={addQuoteLine}
              disabled={!availability.quote.enabled || quoteDraftIsStale || quoteLineInputs.length >= 40}>
              Add another quote line ({quoteLineInputs.length}/40)
            </button>
            <div className="pw-form-grid">
              <label htmlFor="pw-quote-discount">Discount (%)<input id="pw-quote-discount" type="number" min="0" max={quoteRequestCeiling} step="0.01" inputMode="decimal" required value={discountPercent} onChange={(event) => setDiscountPercent(event.target.value)} disabled={!availability.quote.enabled || quoteDraftIsStale} aria-describedby="pw-quote-discount-ceiling" /></label>
              <small id="pw-quote-discount-ceiling">{isStaff
                ? `Your assigned role ceiling is ${quoteDiscountCeiling}%; staff may request up to 50%. Above your own ceiling remains subject to pending manager approval.`
                : `Your current role ceiling: ${quoteDiscountCeiling}% · enforced again by the server.`}</small>
            </div>
            <section className="pw-quote-line-review" aria-label="Quote line review">
              <h4>Review each line</h4>
              {quoteLineInputs.map((line, index) => {
                const price = quoteDraftIsStale
                  ? undefined
                  : prices.find((candidate) => String(candidate.variant_id) === line.variantId);
                const estimate = quoteEstimate.ok ? quoteEstimate.lines[index] : null;
                return (
                  <article key={index}>
                    <strong>Line {index + 1} · {price?.sku ?? "Select a current variant"}</strong>
                    <span>Price {price ? `${price.amount} ${price.currency}` : "not available"} · Quantity {line.quantity || "—"}</span>
                    <span>Specifications: {line.specifications || "—"}</span>
                    <b>{estimate
                      ? <>Estimated line total QAR {formatQarCents(estimate.netTotalCents)}</>
                      : "Line total pending valid line details"}</b>
                  </article>
                );
              })}
              <p className="pw-estimated-quote-total" data-quote-total aria-live="polite">
                Estimated exact quote total (QAR): <strong>{quoteEstimate.ok ? formatQarCents(quoteEstimate.totalCents) : "—"}</strong>
              </p>
              <p className="pw-action-note">Indicative estimate only, calculated from the current approved price snapshots and the quote-level discount. The server price snapshot and returned quote are authoritative.</p>
            </section>
            {retailMode && <p className="pw-action-note">Your current role discount ceiling is {quoteDiscountCeiling}%. The server remains authoritative for the final price decision.</p>}
            <label>Discount decision reason<input required maxLength={240} value={discountReason} onChange={(event) => setDiscountReason(event.target.value)} disabled={!availability.quote.enabled || quoteDraftIsStale} /></label>
            {quoteKind === "special_order" && (
              <>
                <label htmlFor="pw-payment-plan">Payment plan<select id="pw-payment-plan" value={paymentPlan} onChange={(event) => setPaymentPlan(event.target.value as "full" | "downpayment")} disabled={!availability.quote.enabled || quoteDraftIsStale}><option value="full">100% payment</option><option value="downpayment">Configured per-order downpayment</option></select></label>
                {paymentPlan === "downpayment" && <label>Downpayment (%)<input type="number" min="40" max="70" step="0.01" required value={downpaymentPercent} onChange={(event) => setDownpaymentPercent(event.target.value)} disabled={!availability.quote.enabled || quoteDraftIsStale} /></label>}
                <label htmlFor="pw-payment-terms-reason">Special-order terms reason<input id="pw-payment-terms-reason" required maxLength={240} value={paymentTermsReason} onChange={(event) => setPaymentTermsReason(event.target.value)} disabled={!availability.quote.enabled || quoteDraftIsStale} /></label>
                <p className="pw-action-note">Order-value bands remain unconfigured; no thresholds are inferred. The API validates each stated plan and records the reason.</p>
              </>
            )}
            <fieldset className="pw-presentation-fields">
              <legend>Client-facing quotation details · optional display text</legend>
              <label htmlFor="pw-valid-until">Valid until<input id="pw-valid-until" type="date" value={presentation.valid_until} onChange={(event) => setPresentation((value) => ({ ...value, valid_until: event.target.value }))} disabled={!availability.quote.enabled || quoteDraftIsStale} /></label>
              {([
                ["contact_person_en", "Contact person (English)"],
                ["contact_person_ar", "Contact person (Arabic)"],
                ["project_en", "Project (English)"], ["project_ar", "Project (Arabic)"],
                ["address_en", "Address (English)"], ["address_ar", "Address (Arabic)"],
                ["notes_en", "Notes (English)"], ["notes_ar", "Notes (Arabic)"],
              ] as const).map(([key, label]) => (
                <label key={key} htmlFor={`pw-${key}`}>{label}
                  <textarea id={`pw-${key}`} rows={2} maxLength={500}
                    dir={key.endsWith("_ar") ? "rtl" : undefined} lang={key.endsWith("_ar") ? "ar" : undefined}
                    value={presentation[key] ?? ""} onChange={(event) => setPresentation((value) => ({ ...value, [key]: event.target.value }))}
                    disabled={!availability.quote.enabled || quoteDraftIsStale} />
                </label>
              ))}
              {([
                ["phone", "Phone", "tel"], ["mobile", "Mobile", "tel"], ["email", "Email", "email"],
              ] as const).map(([key, label, type]) => (
                <label key={key} htmlFor={`pw-${key}`}>{label}
                  <input id={`pw-${key}`} type={type} maxLength={254} value={presentation[key] ?? ""}
                    onChange={(event) => setPresentation((value) => ({ ...value, [key]: event.target.value }))}
                    disabled={!availability.quote.enabled || quoteDraftIsStale} />
                </label>
              ))}
              {([
                ["department_en", "Department (English)"], ["department_ar", "Department (Arabic)"],
                ["payment_terms_en", "Payment terms (English)"], ["payment_terms_ar", "Payment terms (Arabic)"],
                ["delivery_time_en", "Delivery time (English)"], ["delivery_time_ar", "Delivery time (Arabic)"],
              ] as const).map(([key, label]) => (
                <label key={key} htmlFor={`pw-${key}`}>{label}<textarea id={`pw-${key}`} rows={2} maxLength={500}
                  dir={key.endsWith("_ar") ? "rtl" : undefined} lang={key.endsWith("_ar") ? "ar" : undefined}
                  value={presentation[key]} onChange={(event) => setPresentation((value) => ({ ...value, [key]: event.target.value }))}
                  disabled={!availability.quote.enabled || quoteDraftIsStale} /></label>
              ))}
              <p>Display-only customer context; configured account and customer identity remain authoritative. A 30-day validity template is proposed, never applied automatically. Legal terms remain pending owner text.</p>
            </fieldset>
            {quoteFormError && <p className="pw-disabled-reason" role="alert">{quoteFormError}</p>}
            {!availability.quote.enabled && <p className="pw-disabled-reason">{availability.quote.reason}</p>}
            <button className="pw-button pw-button-primary" type="submit" disabled={!availability.quote.enabled || isPending("quote") || quoteDraftIsStale || !prices.some((price) => String(price.variant_id) === quoteVariant)}>{isPending("quote") ? "Creating…" : "Create synthetic quote"}</button>
          </form>
        </section>}
      </div>

      {/* Creation feedback belongs to the operation, not the selected quote's
          action pane: focused quote/receipt pages must not hide its outcome. */}
      {Object.entries(creationOutcomes).map(([name, outcome]) => (
        <div key={name} className={`pw-notice ${outcome.kind === "success" ? "pw-success" : "pw-error"}`} role={outcome.kind === "success" ? "status" : "alert"}>
          <strong>{outcome.kind === "success" ? "Completed" : "Request not completed"} · {ACTION_LABELS[name]}</strong>
          <p>{outcome.message}</p>
        </div>
      ))}

      {!receivingMode && <section className="pw-transaction-panel pw-selected-quote-actions" hidden={!showFunction("actions") && !showFunction("handover")} aria-labelledby="pw-quote-actions-title">
        <div className="pw-transaction-title"><span className="pw-step">{retailMode ? "02" : "03"}</span><div><h3 id="pw-quote-actions-title">{deliveryMode ? "Readiness & handover" : retailMode ? "Quote collection & holds" : "Continue the selected quote"}</h3><p>{quoteLoading ? "Loading quote…" : quote ? `Quote #${quote.quote_id} · ${quote.status}` : deliveryMode ? "Load an authorized quote above to check its available delivery-stage actions." : "Load a quote by ID above or create one to enable its supported actions."}</p></div></div>
        {!quote && <p className="pw-disabled-reason">No authorized quote is selected. Use the quote lookup above; inaccessible quote records remain hidden by the API.</p>}
        {quote && (
          <>
            <div className="pw-quote-action-grid">
              {!deliveryMode && quote.status === "pending_approval" && (
                <form className="pw-action-form pw-action-subform" onSubmit={(event) => { event.preventDefault(); void runAction("discount-approval", `${actionPrefix}/discount-approval/`, { reason: approvalReason }, "Discount decision recorded."); }}>
                  <h4>Approve price decision</h4>
                  <label>Approval reason<input required maxLength={240} value={approvalReason} onChange={(event) => setApprovalReason(event.target.value)} disabled={!availability.discountApproval.enabled} /></label>
                  {!availability.discountApproval.enabled && <p className="pw-disabled-reason">{availability.discountApproval.reason}</p>}
                  <button className="pw-button pw-button-primary" type="submit" disabled={!availability.discountApproval.enabled || isPending("discount-approval") || !approvalReason.trim()}>{isPending("discount-approval") ? "Submitting…" : "Record discount approval"}</button>
                </form>
              )}
              {!deliveryMode && ["approved", "held"].includes(quote.status) && (
                <form className="pw-action-form pw-action-subform" onSubmit={(event) => { event.preventDefault(); void runAction("hold", `${actionPrefix}/holds/`, { working_days: Number(holdDays) }, "Temporary hold request recorded."); }}>
                  <h4>Request temporary hold</h4>
                  <label>Working days (maximum 3)<input type="number" min="1" max="3" step="1" required value={holdDays} onChange={(event) => setHoldDays(event.target.value)} disabled={!availability.hold.enabled} /></label>
                  {!availability.hold.enabled && <p className="pw-disabled-reason">{availability.hold.reason}</p>}
                  <button className="pw-button pw-button-primary" type="submit" disabled={!availability.hold.enabled || isPending("hold")}>{isPending("hold") ? "Requesting…" : "Request hold"}</button>
                </form>
              )}
              <form className="pw-action-form pw-action-subform" onSubmit={(event) => { event.preventDefault(); void runAction("payment", `${actionPrefix}/payments/`, { amount: paymentAmount, method: paymentMethod, purpose: paymentPurpose }, "Simulated payment recorded."); }}>
                <h4>Record simulated payment</h4>
                <div className="pw-form-grid">
                  <label>Amount<input type="number" min="0.01" step="0.01" required value={paymentAmount} onChange={(event) => setPaymentAmount(event.target.value)} disabled={!availability.payment.enabled} /></label>
                  {retailMode
                    ? <span className="pw-action-note">Purpose · Quote balance only</span>
                    : <label>Purpose<select value={paymentPurpose} onChange={(event) => setPaymentPurpose(event.target.value as "quote" | "storage")} disabled={!availability.payment.enabled}><option value="quote" disabled={!availability.quoteBalancePayment.enabled}>Quote balance</option><option value="storage" disabled={!availability.storagePayment.enabled}>Storage charge</option></select></label>}
                  <label>Simulated method<select value={paymentMethod} onChange={(event) => setPaymentMethod(event.target.value as typeof paymentMethod)} disabled={!availability.payment.enabled}><option value="simulated_cash">Simulated cash</option><option value="simulated_card">Simulated card</option><option value="simulated_bank">Simulated bank</option></select></label>
                </div>
                <p className="pw-action-note">Stock reservations are created by the API only after the non-returnable deposit threshold is met; there is no separate reservation-approval endpoint.</p>
                {(!availability.payment.enabled || !paymentAllowed) && <p className="pw-disabled-reason">{availability.payment.enabled ? paymentReason : availability.payment.reason}</p>}
                {quote.status === "delivered" && !availability.quoteBalancePayment.enabled && <p className="pw-disabled-reason">{availability.quoteBalancePayment.reason}</p>}
                <button className="pw-button pw-button-primary" type="submit" disabled={!availability.payment.enabled || !paymentAllowed || isPending("payment")}>{isPending("payment") ? "Recording…" : "Record test payment"}</button>
              </form>
              {!retailMode && ["approved", "confirmed", "ready"].includes(quote.status) && (
                <form className="pw-action-form pw-action-subform" onSubmit={(event) => { event.preventDefault(); void runAction("post-handover", `${actionPrefix}/post-handover-approval/`, { deferred_balance_percent: deferredBalancePercent, reason: approvalReason }, "Post-handover approval recorded."); }}>
                  <h4>Exceptional post-handover balance</h4>
                  <div className="pw-form-grid">
                    <label>Deferred balance (%)<input type="number" min="5" max="10" step="0.01" required value={deferredBalancePercent} onChange={(event) => setDeferredBalancePercent(event.target.value)} disabled={!availability.postHandover.enabled} /></label>
                    <label>BU GM / CEO reason<input required maxLength={240} value={approvalReason} onChange={(event) => setApprovalReason(event.target.value)} disabled={!availability.postHandover.enabled} /></label>
                  </div>
                  {!availability.postHandover.enabled && <p className="pw-disabled-reason">{availability.postHandover.reason}</p>}
                  <button className="pw-button pw-button-primary" type="submit" disabled={!availability.postHandover.enabled || isPending("post-handover") || !approvalReason.trim()}>{isPending("post-handover") ? "Submitting…" : "Record explicit approval"}</button>
                </form>
              )}
              {!retailMode && quote.status === "confirmed" && (
                <form className="pw-action-form pw-action-subform" onSubmit={(event) => { event.preventDefault(); void runAction("ready", `${actionPrefix}/ready/`, { message: readyMessage }, "Readiness notification recorded."); }}>
                  <h4>Notify customer that goods are ready</h4>
                  <label>Notification note (optional)<input maxLength={240} value={readyMessage} onChange={(event) => setReadyMessage(event.target.value)} disabled={!availability.readiness.enabled} /></label>
                  {!availability.readiness.enabled && <p className="pw-disabled-reason">{availability.readiness.reason}</p>}
                  <button className="pw-button pw-button-primary" type="submit" disabled={!availability.readiness.enabled || isPending("ready")}>{isPending("ready") ? "Sending…" : "Record readiness"}</button>
                </form>
              )}
              {!retailMode && quote.status === "ready" && (
                <>
                  <div className="pw-action-form pw-action-subform">
                    <h4>Storage charges</h4>
                    <p className="pw-action-note">One free month begins at readiness. The API accrues the existing synthetic monthly charge only when due; an immediate request may return no charge.</p>
                    {!availability.storageAccrual.enabled && <p className="pw-disabled-reason">{availability.storageAccrual.reason}</p>}
                    <button className="pw-button pw-button-muted" type="button" onClick={() => void runAction("storage-accrual", `${actionPrefix}/storage-accrual/`, {}, "Storage accrual request completed; refresh the Storage register for returned rows.")} disabled={!availability.storageAccrual.enabled || isPending("storage-accrual")}>{isPending("storage-accrual") ? "Checking…" : "Check / accrue due storage"}</button>
                  </div>
                  <div className="pw-action-form pw-action-subform">
                    <h4>Record delivery / handover</h4>
                    <p className="pw-action-note">The server verifies reservation and payment conditions before marking delivery.</p>
                    {!availability.delivery.enabled && <p className="pw-disabled-reason">{availability.delivery.reason}</p>}
                    <button className="pw-button pw-button-primary" type="button" onClick={() => void runAction("delivery", `${actionPrefix}/deliver/`, {}, "Delivery recorded.")} disabled={!availability.delivery.enabled || isPending("delivery")}>{isPending("delivery") ? "Recording…" : "Record delivery"}</button>
                  </div>
                </>
              )}
              {!retailMode && quote.status === "delivered" && (
                <div className="pw-action-form pw-action-subform">
                  <h4>Storage charges after readiness</h4>
                  <p className="pw-action-note">Use the Storage register to review generated charges. The API applies the configured free period and monthly rate.</p>
                  {!availability.storageAccrual.enabled && <p className="pw-disabled-reason">{availability.storageAccrual.reason}</p>}
                  <button className="pw-button pw-button-muted" type="button" onClick={() => void runAction("storage-accrual", `${actionPrefix}/storage-accrual/`, {}, "Storage accrual request completed; refresh the Storage register for returned rows.")} disabled={!availability.storageAccrual.enabled || isPending("storage-accrual")}>{isPending("storage-accrual") ? "Checking…" : "Check / accrue due storage"}</button>
                </div>
              )}
            </div>
            <p className="pw-action-note">The quote detail and paginated registers below remain the review source. No action changes permissions, and the server is authoritative for every approval, reservation, payment, and delivery transition.</p>
          </>
        )}
        {Object.entries(actionOutcomes)
          .filter(([, outcome]) => outcome.quoteId !== undefined && (!quote || outcome.quoteId === selectedQuoteId))
          .map(([name, outcome]) => (
            <div key={name} className={`pw-notice ${outcome.kind === "success" ? "pw-success" : "pw-error"}`} role={outcome.kind === "success" ? "status" : "alert"}>
              <strong>{outcome.kind === "success" ? "Completed" : "Request not completed"} · {ACTION_LABELS[name] ?? name} · Quote #{outcome.quoteId}</strong>
              <p>{outcome.message}</p>
            </div>
          ))}
        {!deliveryMode && quote && quote.status === "confirmed" && <p className="pw-action-note">{retailMode ? "Reservation feedback is server-returned quote progress. No separate reservation-approval endpoint exists." : "The API has confirmed reservation status. A separate reservation approval action is not available in the existing API."}</p>}
      </section>}
      <p className="pw-footer-note">No write endpoint, permission, accounting rule, counterparty, price, or approver assignment is created by this workbench.</p>
      <span className="pw-sr-only" aria-live="polite">{selectedUnitName}</span>
    </section>
  );
}
