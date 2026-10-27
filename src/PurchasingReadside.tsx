import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  getPurchasingPagerState,
  getPurchasingReadsideState,
  getPurchasingReadsideTabs,
  isPurchasingReadsideDeniedStatus,
  shouldRequestPurchasingCounterparties,
} from "./purchasing-workbench-state.mjs";
import "./purchasing-readside.css";

type Pagination = {
  page: number;
  page_size: number;
  total_count: number;
  total_pages: number;
  previous_page: number | null;
  next_page: number | null;
};
type PageResult<T> = { rows: T[]; pagination: Pagination };
type ReadState<T> =
  | { status: "idle" | "loading"; data: null; error: "" }
  | { status: "ready"; data: T; error: "" }
  | { status: "unauthorized" | "error"; data: null; error: string };

type ReceiptLine = {
  item_id: string;
  description: string;
  brand_name: string;
  warehouse_location: string;
  quantity: string;
  supplier_unit_cost?: string;
  purchase_value?: string;
  landed_cost_allocation?: string;
};
type Receipt = {
  receipt_id: number;
  created_at: string;
  business_unit: { id: number; code: string; name: string };
  supplier: { code: string; name: string };
  currency: string;
  fields: string[];
  lines: ReceiptLine[];
  purchase_value?: string;
  foreign_purchase_value?: string;
  exchange_rate?: string;
  rate_source?: string;
  rate_date?: string;
  eligible_landed_charges?: string;
  charges?: { category: string; amount: string; eligible: boolean; currency?: string; foreign_amount?: string; exchange_rate?: string }[];
  landed_cost_rate_percent?: string;
  inventory_value?: string;
};
type Quote = {
  quote_id: number;
  kind: string;
  status: string;
  customer_id: number;
  total_quote_value: string;
  currency: string;
  created_at: string;
};
type QuoteDetail = Quote & {
  business_unit_id: number;
  discount_percent: string;
  total_list_value: string;
  total_discount: string;
  payment_plan: string;
  downpayment_percent: string | null;
  lines: {
    quote_line_id: number;
    variant_id: number;
    quantity: string;
    selling_price_snapshot: string;
    price_revision_snapshot?: number;
    discount_percent: string;
    discount_amount: string;
    net_unit_price: string;
    net_total: string;
    specifications: unknown;
  }[];
  payments: { payment_id: number; kind: string; amount: string; created_at: string }[];
  approvals: { approval_id: number; kind: string; approver_id: number; role: string; reason: string; approved_at: string }[];
  holds: { hold_id: number; status: string; quantity: string; working_days: number; expires_at: string }[];
  events: { event_id: number; kind: string; message: string; occurred_at: string }[];
};
type StorageCharge = {
  charge_id: number;
  quote_id: number;
  period_start: string;
  period_end: string;
  base_quote_value: string;
  amount: string;
  currency: string;
  exception: {
    approver_id: number;
    approver_role: string;
    amount_waived: string;
    reason: string;
    approved_at: string;
  } | null;
};
type Reservation = {
  reservation_id: number;
  status: string;
  created_at: string;
  item_id: string;
  description: string;
  brand_name: string;
  warehouse_location: string;
  quantity: string;
};
type Delivery = {
  delivery_line_id: number;
  delivered_at: string;
  selling_price: string;
  quantity: string;
  description: string;
  item_id: string;
  warehouse_location: string;
  brand_name: string;
};
type Counterparty = {
  counterparty_id: number;
  party_type: string;
  code: string;
  name: string;
};
type TabId = "receipts" | "quotes" | "storage" | "reservations" | "deliveries" | "counterparties";
type Column<T> = { label: string; render: (row: T) => ReactNode };
type ReadError = Error & { status?: number };

const idle = <T,>(): ReadState<T> => ({ status: "idle", data: null, error: "" });

function queryUrl(path: string, params: Record<string, string | number>): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => query.set(key, String(value)));
  return `${path}?${query.toString()}`;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    method: "GET",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : `Request unavailable (${response.status}).`;
    const error = new Error(detail) as ReadError;
    error.status = response.status;
    throw error;
  }
  return body as T;
}

function useReadPage<T>(url: string | null, page: number, refreshKey = 0): ReadState<PageResult<T>> {
  const [state, setState] = useState<ReadState<PageResult<T>>>(idle);
  useEffect(() => {
    if (!url) {
      setState(idle());
      return;
    }
    let active = true;
    setState({ status: "loading", data: null, error: "" });
    const separator = url.includes("?") ? "&" : "?";
    getJson<PageResult<T>>(`${url}${separator}page=${page}&page_size=20`)
      .then((data) => { if (active) setState({ status: "ready", data, error: "" }); })
      .catch((reason: unknown) => {
        if (!active) return;
        const error = reason as ReadError;
        const denied = isPurchasingReadsideDeniedStatus(error.status);
        setState({
          status: denied ? "unauthorized" : "error",
          data: null,
          error: error.message || "The read request did not return data.",
        });
      });
    return () => { active = false; };
  }, [url, page, refreshKey]);
  return state;
}

function useReadDetail<T>(url: string | null): ReadState<T> {
  const [state, setState] = useState<ReadState<T>>(idle);
  useEffect(() => {
    if (!url) {
      setState(idle());
      return;
    }
    let active = true;
    setState({ status: "loading", data: null, error: "" });
    getJson<T>(url)
      .then((data) => { if (active) setState({ status: "ready", data, error: "" }); })
      .catch((reason: unknown) => {
        if (!active) return;
        const error = reason as ReadError;
        const denied = isPurchasingReadsideDeniedStatus(error.status);
        setState({
          status: denied ? "unauthorized" : "error",
          data: null,
          error: error.message || "The detail request did not return data.",
        });
      });
    return () => { active = false; };
  }, [url]);
  return state;
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
function formatDate(value: string | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}
function money(value: string | undefined, currency?: string): string {
  return value === undefined ? "—" : `${currency ? `${currency} ` : ""}${value}`;
}

function DataTable<T extends object>({
  label,
  rows,
  columns,
  getId,
  selectedId,
  onSelect,
}: {
  label: string;
  rows: T[];
  columns: Column<T>[];
  getId: (row: T) => string;
  selectedId: string | undefined;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="pr-table-scroll">
      <table className="pr-table" aria-label={label}>
        <thead><tr><th scope="col">Record</th>{columns.map((column) => <th scope="col" key={column.label}>{column.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((row) => {
            const id = getId(row);
            return (
              <tr key={id} className={selectedId === id ? "is-selected" : undefined}>
                <td><button type="button" className="pr-row-link" aria-label={`Open ${label} record ${id}`} onClick={() => onSelect(id)}>{id}</button></td>
                {columns.map((column) => <td key={column.label}>{column.render(row)}</td>)}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Pager({ pagination, onPage }: { pagination: Pagination; onPage: (page: number) => void }) {
  const controls = getPurchasingPagerState(pagination);
  return (
    <nav className="pr-pager" aria-label="Purchasing list pages">
      <span>Page <strong>{pagination.page}</strong> of <strong>{pagination.total_pages}</strong> · {pagination.total_count} records</span>
      <div>
        <button type="button" onClick={() => pagination.previous_page && onPage(pagination.previous_page)} disabled={!controls.canGoPrevious} aria-label="Previous page">Previous</button>
        <button type="button" onClick={() => pagination.next_page && onPage(pagination.next_page)} disabled={!controls.canGoNext} aria-label="Next page">Next</button>
      </div>
    </nav>
  );
}

function DetailState({ state, children, label }: { state: ReadState<unknown>; children?: ReactNode; label: string }) {
  if (state.status === "idle") return <p className="pr-muted">Select a record to request its authorized detail.</p>;
  if (state.status === "loading") return <p className="pr-muted" role="status">Loading {label} detail…</p>;
  if (state.status === "unauthorized") return <p className="pr-notice" role="status">This detail is not available in the current access scope.</p>;
  if (state.status === "error") return <p className="pr-error" role="alert">{state.error}</p>;
  return <div className="pr-detail-content">{children}</div>;
}

function ListState<T>({
  state,
  label,
  emptyMessage,
  children,
}: {
  state: ReadState<PageResult<T>>;
  label: string;
  emptyMessage: string;
  children: (data: PageResult<T>) => ReactNode;
}) {
  const viewState = getPurchasingReadsideState(
    state.status,
    state.status === "ready" ? state.data.rows : undefined,
  );
  if (viewState === "idle") return <p className="pr-notice">This read view is not available for the current selection.</p>;
  if (viewState === "loading") return <p className="pr-muted" role="status">Loading {label}…</p>;
  if (viewState === "unauthorized") return <p className="pr-notice" role="status">The server did not authorize this {label} view. No records are displayed.</p>;
  if (viewState === "error") return <p className="pr-error" role="alert">{state.status === "error" ? state.error : "The read request failed."}</p>;
  if (state.status !== "ready") return <p className="pr-notice">This read view is not available.</p>;
  if (viewState === "empty") return <div className="pr-empty"><strong>No synthetic records returned</strong><p>{emptyMessage}</p></div>;
  return <>{children(state.data)}</>;
}

function DetailFields({ fields }: { fields: [string, unknown][] }) {
  return <dl className="pr-detail-fields">{fields.filter(([, value]) => value !== undefined && value !== null).map(([label, value]) => (
    <div key={label}><dt>{label}</dt><dd>{display(value)}</dd></div>
  ))}</dl>;
}

function ReceiptDetail({ receipt }: { receipt: Receipt }) {
  const permits = (field: string) => receipt.fields.includes(field);
  return (
    <>
      <DetailFields fields={[
        ["Receipt", receipt.receipt_id],
        ["Date", formatDate(receipt.created_at)],
        ["Business unit", receipt.business_unit.name],
        ["Supplier", receipt.supplier.name],
        ["Supplier currency", receipt.currency],
        ["Exchange rate", permits("exchange_rate") ? `${receipt.exchange_rate} QAR per ${receipt.currency} (${receipt.rate_source}, ${receipt.rate_date})` : undefined],
        ["Supplier total", permits("foreign_purchase_value") ? money(receipt.foreign_purchase_value, receipt.currency) : undefined],
        ["Purchase value", permits("purchase_value") ? money(receipt.purchase_value, "QAR") : undefined],
        ["Eligible landed charges", permits("eligible_landed_charges") ? money(receipt.eligible_landed_charges, "QAR") : undefined],
        ["Landed-cost rate", permits("landed_cost_rate_percent") ? receipt.landed_cost_rate_percent : undefined],
        ["Inventory value", permits("inventory_value") ? money(receipt.inventory_value, "QAR") : undefined],
      ]} />
      <h4>Receipt lines</h4>
      {receipt.lines.length ? receipt.lines.map((line, index) => (
        <article className="pr-line" key={`${line.item_id}-${index}`}>
          <strong>{line.item_id} · {line.description}</strong>
          <DetailFields fields={[
            ["Brand", line.brand_name],
            ["Warehouse", line.warehouse_location],
            ["Quantity", line.quantity],
            ["Unit cost", permits("lines.supplier_unit_cost") ? money(line.supplier_unit_cost, "QAR") : undefined],
            ["Purchase value", permits("lines.purchase_value") ? money(line.purchase_value, "QAR") : undefined],
            ["Landed allocation", permits("lines.landed_cost_allocation") ? money(line.landed_cost_allocation, "QAR") : undefined],
          ]} />
        </article>
      )) : <p className="pr-muted">No line detail is available for this selection.</p>}
      {receipt.charges && permits("charges.amount") && <>
        <h4>Eligible and recorded landed charges</h4>
        <ul className="pr-charge-list">
          {receipt.charges.map((charge, index) => (
            <li key={`${charge.category}-${index}`}>{charge.category} · {money(charge.amount, "QAR")}{charge.currency && charge.foreign_amount ? ` (${money(charge.foreign_amount, charge.currency)} at ${charge.exchange_rate})` : ""}{charge.eligible ? "" : " · not allocated"}</li>
          ))}
        </ul>
      </>}
    </>
  );
}

function QuoteDetailView({ quote }: { quote: QuoteDetail }) {
  return (
    <>
      <DetailFields fields={[
        ["Status", quote.status],
        ["Type", quote.kind],
        ["Customer ID", quote.customer_id],
        ["Quote total", money(quote.total_quote_value, quote.currency)],
        ["Payment plan", quote.payment_plan],
        ["Downpayment", quote.downpayment_percent ? `${quote.downpayment_percent}%` : "Not specified"],
        ["Created", formatDate(quote.created_at)],
      ]} />
      <h4>Quoted lines and specifications</h4>
      {quote.lines.map((line) => (
        <article className="pr-line" key={line.quote_line_id}>
          <strong>Line {line.quote_line_id} · variant {line.variant_id}</strong>
          <DetailFields fields={[
            ["Quantity", line.quantity],
            ["Selling-price snapshot", money(line.selling_price_snapshot, quote.currency)],
            ["Approved price revision", line.price_revision_snapshot],
            ["Discount", `${line.discount_percent}%`],
            ["Discount amount", money(line.discount_amount, quote.currency)],
            ["Net unit", money(line.net_unit_price, quote.currency)],
            ["Line total", money(line.net_total, quote.currency)],
            ["Specifications", line.specifications],
          ]} />
        </article>
      ))}
      <WorkflowRows title="Simulated payments" rows={quote.payments.map((row) => ({ ...row, summary: money(row.amount, quote.currency), date: row.created_at }))} />
      <WorkflowRows title="Approvals" rows={quote.approvals.map((row) => ({ ...row, summary: `${row.role} · ${row.reason}`, date: row.approved_at }))} />
      <WorkflowRows title="Temporary holds" rows={quote.holds.map((row) => ({ ...row, summary: `${row.status} · ${row.quantity} units · ${row.working_days} working days`, date: row.expires_at }))} />
      <WorkflowRows title="Workflow events" rows={quote.events.map((row) => ({ ...row, summary: row.message, date: row.occurred_at }))} />
    </>
  );
}

function WorkflowRows({ title, rows }: { title: string; rows: { summary: string; date: string; kind?: string }[] }) {
  return <section className="pr-workflow"><h4>{title}</h4>{rows.length
    ? <ul>{rows.map((row, index) => <li key={`${row.kind ?? title}-${index}`}><strong>{row.kind ?? title}</strong><span>{row.summary}</span><time>{formatDate(row.date)}</time></li>)}</ul>
    : <p className="pr-muted">No {title.toLowerCase()} were returned.</p>}</section>;
}

function StorageDetail({ charge }: { charge: StorageCharge }) {
  return (
    <>
      <DetailFields fields={[
        ["Charge", charge.charge_id],
        ["Quote", charge.quote_id],
        ["Period", `${charge.period_start} – ${charge.period_end}`],
        ["Base quote value", money(charge.base_quote_value, charge.currency)],
        ["Charge amount", money(charge.amount, charge.currency)],
      ]} />
      {charge.exception && <>
        <h4>Approved exception</h4>
        <DetailFields fields={[
          ["Approver", charge.exception.approver_id],
          ["Approver role", charge.exception.approver_role],
          ["Amount waived", money(charge.exception.amount_waived, charge.currency)],
          ["Reason", charge.exception.reason],
          ["Approved", formatDate(charge.exception.approved_at)],
        ]} />
      </>}
    </>
  );
}

function PurchasingCollection<T extends object>({
  title,
  subtitle,
  state,
  emptyMessage,
  label,
  columns,
  getId,
  selectedId,
  onSelect,
  onPage,
  detailState,
  renderDetail,
}: {
  title: string;
  subtitle: string;
  state: ReadState<PageResult<T>>;
  emptyMessage: string;
  label: string;
  columns: Column<T>[];
  getId: (row: T) => string;
  selectedId?: string;
  onSelect: (id: string) => void;
  onPage: (page: number) => void;
  detailState: ReadState<unknown>;
  renderDetail: (data: unknown) => ReactNode;
}) {
  return (
    <section className="pr-collection" aria-labelledby={`pr-${label}-title`}>
      <header className="pr-section-head">
        <div><span className="pr-kicker">{subtitle}</span><h2 id={`pr-${label}-title`}>{title}</h2></div>
        <span className="pr-readonly">READ ONLY</span>
      </header>
      <ListState state={state} label={title.toLowerCase()} emptyMessage={emptyMessage}>
        {(data) => (
          <>
            <DataTable
              label={title}
              rows={data.rows}
              columns={columns}
              getId={getId}
              selectedId={selectedId}
              onSelect={onSelect}
            />
            <Pager pagination={data.pagination} onPage={onPage} />
          </>
        )}
      </ListState>
      {selectedId && <aside className="pr-detail" aria-label={`${title} selected-record detail`}>
        <div className="pr-detail-heading"><span>SELECTED RECORD</span><strong>{selectedId}</strong></div>
        <DetailState state={detailState} label={title.toLowerCase()}>{detailState.status === "ready" ? renderDetail(detailState.data) : null}</DetailState>
      </aside>}
    </section>
  );
}

export default function PurchasingReadside({
  mode = "purchasing",
  selectedUnitId,
  selectedUnitName,
  isStaff,
  csrfToken,
  onOpenReports,
  focusedTab,
  enabled = true,
}: {
  mode?: "purchasing" | "delivery" | "retail" | "warehouse" | "procurement";
  selectedUnitId: number;
  selectedUnitName: string;
  isStaff: boolean;
  csrfToken: string;
  onOpenReports: () => void;
  focusedTab?: TabId;
  enabled?: boolean;
}) {
  const deliveryMode = mode === "delivery";
  const retailMode = mode === "retail";
  const warehouseMode = mode === "warehouse";
  const procurementMode = mode === "procurement";
  const [activeTab, setActiveTab] = useState<TabId>(deliveryMode || retailMode ? "quotes" : "receipts");
  const [pages, setPages] = useState<Record<TabId, number>>({
    receipts: 1,
    quotes: 1,
    storage: 1,
    reservations: 1,
    deliveries: 1,
    counterparties: 1,
  });
  const [selected, setSelected] = useState<Partial<Record<TabId, string>>>({});
  const [counterpartyRefreshKey, setCounterpartyRefreshKey] = useState(0);
  const [customerCode, setCustomerCode] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [customerSaving, setCustomerSaving] = useState(false);
  const [customerMessage, setCustomerMessage] = useState("");
  const [customerError, setCustomerError] = useState("");
  useEffect(() => {
    setPages({ receipts: 1, quotes: 1, storage: 1, reservations: 1, deliveries: 1, counterparties: 1 });
    setSelected({});
  }, [selectedUnitId]);
  useEffect(() => {
    setActiveTab(focusedTab ?? (deliveryMode || retailMode ? "quotes" : "receipts"));
  }, [deliveryMode, retailMode, procurementMode, focusedTab]);
  const pageFor = (tab: TabId) => pages[tab];
  const changePage = (tab: TabId, page: number) => setPages((current) => ({ ...current, [tab]: page }));
  const select = (tab: TabId, id: string) => setSelected((current) => ({ ...current, [tab]: current[tab] === id ? undefined : id }));
  const selectUnit = { unit: selectedUnitId };

  const tabEnabled = (tab: TabId) => !focusedTab || tab === focusedTab;
  const receipts = useReadPage<Receipt>(!enabled || !tabEnabled("receipts") || deliveryMode || retailMode ? null : queryUrl("/api/reports/purchase_history/receipts/", selectUnit), pageFor("receipts"));
  const quotes = useReadPage<Quote>(!enabled || !tabEnabled("quotes") || warehouseMode || procurementMode ? null : queryUrl("/api/purchasing/quotes/", { business_unit_id: selectedUnitId }), pageFor("quotes"));
  const storage = useReadPage<StorageCharge>(!enabled || !tabEnabled("storage") || retailMode || warehouseMode || procurementMode ? null : queryUrl("/api/purchasing/storage-charges/", { business_unit_id: selectedUnitId }), pageFor("storage"));
  const reservations = useReadPage<Reservation>(!enabled || !tabEnabled("reservations") || retailMode || warehouseMode || procurementMode ? null : queryUrl("/api/reports/inventory/reservations/", selectUnit), pageFor("reservations"));
  const deliveries = useReadPage<Delivery>(!enabled || !tabEnabled("deliveries") || retailMode || warehouseMode || procurementMode ? null : queryUrl("/api/reports/sales/deliveries/", selectUnit), pageFor("deliveries"));
  const counterparties = useReadPage<Counterparty>(
    !enabled || !tabEnabled("counterparties") || deliveryMode || retailMode || warehouseMode || !shouldRequestPurchasingCounterparties(isStaff) ? null : "/api/governance/purchasing/counterparties/",
    pageFor("counterparties"),
    counterpartyRefreshKey,
  );

  const detailUrl = (path: string, id: string | undefined, params: Record<string, string | number> = {}) =>
    id ? queryUrl(`${path}${encodeURIComponent(id)}/`, params) : null;
  const receiptDetail = useReadDetail<{ receipt: Receipt }>(
    enabled && tabEnabled("receipts") && !deliveryMode && !retailMode ? detailUrl("/api/reports/purchase_history/receipts/", selected.receipts, selectUnit) : null,
  );
  const quoteDetail = useReadDetail<QuoteDetail>(
    enabled && tabEnabled("quotes") && !warehouseMode && !procurementMode ? detailUrl("/api/purchasing/quotes/", selected.quotes, { business_unit_id: selectedUnitId }) : null,
  );
  const storageDetail = useReadDetail<{ charge: StorageCharge }>(
    enabled && tabEnabled("storage") && !retailMode && !warehouseMode && !procurementMode ? detailUrl("/api/purchasing/storage-charges/", selected.storage, { business_unit_id: selectedUnitId }) : null,
  );
  const reservationDetail = useReadDetail<{ reservation: Reservation }>(
    enabled && tabEnabled("reservations") && !retailMode && !warehouseMode && !procurementMode ? detailUrl("/api/reports/inventory/reservations/", selected.reservations, selectUnit) : null,
  );
  const deliveryDetail = useReadDetail<{ delivery: Delivery }>(
    enabled && tabEnabled("deliveries") && !retailMode && !warehouseMode && !procurementMode ? detailUrl("/api/reports/sales/deliveries/", selected.deliveries, selectUnit) : null,
  );
  const createCustomer = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const code = customerCode.trim().toUpperCase();
    const name = customerName.trim();
    if (!code.startsWith("TEST-CUST-") || !name.startsWith("TEST ONLY")) {
      setCustomerError("Use a customer code beginning TEST-CUST- and a name beginning TEST ONLY. Only synthetic demo customers are allowed.");
      setCustomerMessage("");
      return;
    }
    setCustomerSaving(true);
    setCustomerError("");
    setCustomerMessage("");
    try {
      const response = await fetch("/api/governance/purchasing/counterparties/", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ party_type: "customer", code, name }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof body?.detail === "string" ? body.detail : `Customer creation failed (${response.status}).`);
      }
      setCustomerMessage(`Synthetic customer ${body.code ?? code} created.`);
      setCustomerCode("");
      setCustomerName("");
      setPages((current) => ({ ...current, counterparties: 1 }));
      setSelected((current) => ({ ...current, counterparties: undefined }));
      setCounterpartyRefreshKey((current) => current + 1);
    } catch (reason) {
      setCustomerError(reason instanceof Error ? reason.message : "Customer could not be created.");
    } finally {
      setCustomerSaving(false);
    }
  };

  const counterpartyDetail = useReadDetail<Counterparty>(
    enabled && tabEnabled("counterparties") && !deliveryMode && !retailMode && !warehouseMode && shouldRequestPurchasingCounterparties(isStaff)
      ? detailUrl("/api/governance/purchasing/counterparties/", selected.counterparties)
      : null,
  );

  const allTabs: { id: TabId; label: string; staffOnly?: boolean }[] = getPurchasingReadsideTabs(isStaff);
  const tabs = warehouseMode
    ? allTabs.filter((tab) => tab.id === "receipts")
    : procurementMode
      ? allTabs.filter((tab) => tab.id === "receipts" || (isStaff && tab.id === "counterparties"))
    : deliveryMode
    ? allTabs.filter((tab) => ["quotes", "storage", "reservations", "deliveries"].includes(tab.id))
    : retailMode ? allTabs.filter((tab) => tab.id === "quotes") : allTabs;
  const visibleTabs = focusedTab ? tabs.filter((tab) => tab.id === focusedTab) : tabs;
  const renderedTab = focusedTab ?? activeTab;
  const receiptColumns: Column<Receipt>[] = [
    { label: "Date", render: (row) => formatDate(row.created_at) },
    { label: "Supplier", render: (row) => row.supplier.name },
    { label: "Unit", render: (row) => row.business_unit.code },
    { label: "Lines", render: (row) => row.lines.length },
    ...(receipts.status === "ready" && receipts.data.rows.some((row) => row.fields.includes("purchase_value"))
      ? [{ label: "Purchase value", render: (row: Receipt) => money(row.purchase_value, "QAR") }]
      : []),
    ...(receipts.status === "ready" && receipts.data.rows.some((row) => row.fields.includes("eligible_landed_charges"))
      ? [{ label: "Landed charges", render: (row: Receipt) => money(row.eligible_landed_charges, "QAR") }]
      : []),
  ];

  return (
    <section className="purchasing-readside" aria-labelledby="pr-title">
      <header className="pr-header">
      <div><span className="pr-kicker">{deliveryMode ? "DELIVERY & AFTER-SALES · READ-SIDE" : retailMode ? "RETAIL & SALES · READ-SIDE" : procurementMode ? "PROCUREMENT & IMPORTS · READ-SIDE" : warehouseMode ? "INVENTORY · RECEIPT REGISTER" : "PURCHASING · READ-SIDE WORKBENCH"}</span><h2 id="pr-title">{focusedTab === "quotes" ? "Quote register" : focusedTab === "storage" ? "Storage-charge register" : focusedTab === "reservations" ? "Reservation register" : focusedTab === "deliveries" ? "Delivery register" : deliveryMode ? "Delivery registers" : retailMode ? "Quote register" : procurementMode || warehouseMode ? "Receipt & landed-charge registers" : "Workflow records"}</h2>
          <p>Read-only views for <strong>{selectedUnitName}</strong>. All records shown here are synthetic; permissions are checked by each API.</p></div>
        <span className="pr-synthetic-label">TEST ONLY<br />GET READS</span>
      </header>
      <div className="pr-scope">
        <span><small>BUSINESS UNIT</small><strong>{selectedUnitName}</strong></span>
        <span><small>UNIT ID</small><strong>{selectedUnitId}</strong></span>
        <span><small>MODE</small><strong>{deliveryMode ? "Delivery scope" : retailMode ? "Retail sales" : procurementMode ? "Procurement scope" : warehouseMode ? "Receiving review" : "Read only"}</strong></span>
      </div>
      <p className="pr-period-note">{procurementMode
        ? "Receipt and embedded eligible landed-charge details use the existing purchase-history grant and server-masked fields. No quote, inventory, sales, reservation or storage requests are made."
        : warehouseMode
        ? "Only the existing receipt register is shown. Its report scope and sensitive fields remain server-authorized; no quote, sales, reservation or storage reads are requested."
        : deliveryMode
        ? "Quotes and storage charges are scoped to the selected business unit. Reservations and delivery lines use the report API’s authorized scope; denied views remain unavailable."
        : retailMode
          ? "Quote rows come from the existing business-unit-scoped list GET. Access denials remain unavailable; no report grant or alternate write route is substituted."
          : "Receipt, reservation, and delivery lists inherit the report API’s configured local date window. Quotes and storage charges are listed by selected business unit."}</p>
      <div className="pr-workspace">
        <nav id="pr-tabs" className="pr-tabs" aria-label={deliveryMode ? "Delivery registers" : retailMode ? "Retail quote register" : procurementMode ? "Procurement registers" : warehouseMode ? "Receipt register" : "Purchasing records"}>
          {visibleTabs.map((tab) => <button key={tab.id} id={`pr-${tab.id}-tab`} type="button" data-register-tab={tab.id} aria-current={renderedTab === tab.id ? "page" : undefined}
            onClick={() => setActiveTab(tab.id)}>
            {tab.label}{tab.staffOnly && <span>STAFF</span>}
          </button>)}
          {!warehouseMode && !focusedTab && <div className="pr-report-link"><span>Broader authorized views</span><button type="button" onClick={onOpenReports}>Open Reports</button></div>}
        </nav>
        <div className="pr-content" role="region" aria-label={`${visibleTabs.find((tab) => tab.id === activeTab)?.label ?? visibleTabs[0]?.label ?? "Unavailable"} list`}>
          {renderedTab === "receipts" && <PurchasingCollection title="Receipts and landed charges" subtitle="PURCHASE HISTORY" label="receipts"
            state={receipts} emptyMessage="No receipts match this business-unit and report date scope; records outside it are not exposed."
            columns={receiptColumns} getId={(row) => String(row.receipt_id)} selectedId={selected.receipts} onSelect={(id) => select("receipts", id)}
            onPage={(page) => changePage("receipts", page)} detailState={receiptDetail}
            renderDetail={(data) => <ReceiptDetail receipt={(data as { receipt: Receipt }).receipt} />} />}
          {renderedTab === "quotes" && <PurchasingCollection title="Quotes and orders" subtitle="AUTHORIZED ORDER SCOPE" label="quotes"
            state={quotes} emptyMessage="No synthetic quotes or orders were returned for this unit."
            columns={[
              { label: "Status", render: (row) => row.status },
              { label: "Type", render: (row) => row.kind },
              { label: "Total", render: (row) => money(row.total_quote_value, row.currency) },
              { label: "Created", render: (row) => formatDate(row.created_at) },
            ]} getId={(row) => String(row.quote_id)} selectedId={selected.quotes} onSelect={(id) => select("quotes", id)}
            onPage={(page) => changePage("quotes", page)} detailState={quoteDetail}
            renderDetail={(data) => <QuoteDetailView quote={data as QuoteDetail} />} />}
          {renderedTab === "storage" && <PurchasingCollection title="Storage charges" subtitle="AUTHORIZED ORDER SCOPE" label="storage"
            state={storage} emptyMessage="No synthetic storage-charge records were returned for this unit."
            columns={[
              { label: "Quote", render: (row) => row.quote_id },
              { label: "Period", render: (row) => `${row.period_start} – ${row.period_end}` },
              { label: "Base quote value", render: (row) => money(row.base_quote_value, row.currency) },
              { label: "Charge", render: (row) => money(row.amount, row.currency) },
              { label: "Exception", render: (row) => row.exception ? "Approved exception" : "—" },
            ]} getId={(row) => String(row.charge_id)} selectedId={selected.storage} onSelect={(id) => select("storage", id)}
            onPage={(page) => changePage("storage", page)} detailState={storageDetail}
            renderDetail={(data) => <StorageDetail charge={(data as { charge: StorageCharge }).charge} />} />}
          {renderedTab === "reservations" && <PurchasingCollection title="Reservations" subtitle="INVENTORY DATA SCOPE" label="reservations"
            state={reservations} emptyMessage="No synthetic reservation records were returned under the current inventory scope."
            columns={[
              { label: "Item", render: (row) => row.item_id },
              { label: "Brand", render: (row) => row.brand_name },
              { label: "Location", render: (row) => row.warehouse_location },
              { label: "Quantity", render: (row) => row.quantity },
              { label: "Status", render: (row) => row.status },
              { label: "Created", render: (row) => formatDate(row.created_at) },
            ]} getId={(row) => String(row.reservation_id)} selectedId={selected.reservations} onSelect={(id) => select("reservations", id)}
            onPage={(page) => changePage("reservations", page)} detailState={reservationDetail}
            renderDetail={(data) => {
              const record = (data as { reservation: Reservation }).reservation;
              return <DetailFields fields={[
                ["Item", record.item_id],
                ["Description", record.description],
                ["Brand", record.brand_name],
                ["Location", record.warehouse_location],
                ["Quantity", record.quantity],
                ["Status", record.status],
                ["Created", formatDate(record.created_at)],
              ]} />;
            }} />}
          {renderedTab === "deliveries" && <PurchasingCollection title="Delivered sales" subtitle="ORDINARY SALES FIELDS" label="deliveries"
            state={deliveries} emptyMessage="No synthetic delivery lines were returned under the current sales scope."
            columns={[
              { label: "Date", render: (row) => formatDate(row.delivered_at) },
              { label: "Item", render: (row) => row.item_id },
              { label: "Brand", render: (row) => row.brand_name },
              { label: "Location", render: (row) => row.warehouse_location },
              { label: "Quantity", render: (row) => row.quantity },
              { label: "Selling price", render: (row) => money(row.selling_price, "QAR") },
            ]} getId={(row) => String(row.delivery_line_id)} selectedId={selected.deliveries} onSelect={(id) => select("deliveries", id)}
            onPage={(page) => changePage("deliveries", page)} detailState={deliveryDetail}
            renderDetail={(data) => {
              const record = (data as { delivery: Delivery }).delivery;
              return <DetailFields fields={[
                ["Delivered", formatDate(record.delivered_at)],
                ["Selling price", money(record.selling_price, "QAR")],
                ["Quantity", record.quantity],
                ["Description", record.description],
                ["Item ID", record.item_id],
                ["Warehouse location", record.warehouse_location],
                ["Brand", record.brand_name],
              ]} />;
            }} />}
          {renderedTab === "counterparties" && (isStaff
            ? <>
              <section className="pr-customer-create" aria-labelledby="pr-customer-create-title">
                <div><span className="pr-kicker">STAFF-ONLY · SYNTHETIC DATA</span><h2 id="pr-customer-create-title">Create customer</h2>
                  <p>Use fictional demo records only. Customer code must start with TEST-CUST- and name with TEST ONLY.</p></div>
                <form onSubmit={(event) => void createCustomer(event)}>
                  <label htmlFor="pr-new-customer-code">Customer code<input id="pr-new-customer-code" value={customerCode} onChange={(event) => setCustomerCode(event.target.value)} placeholder="TEST-CUST-ACME" maxLength={40} required disabled={customerSaving} /></label>
                  <label htmlFor="pr-new-customer-name">Customer name<input id="pr-new-customer-name" value={customerName} onChange={(event) => setCustomerName(event.target.value)} placeholder="TEST ONLY Acme Customer" maxLength={120} required disabled={customerSaving} /></label>
                  <button type="submit" disabled={customerSaving}>{customerSaving ? "Creating…" : "Create synthetic customer"}</button>
                </form>
                {customerError && <p className="pr-error" role="alert">{customerError}</p>}
                {customerMessage && <p className="pr-notice" role="status">{customerMessage} The directory is refreshing.</p>}
              </section>
              <PurchasingCollection title="Synthetic counterparties" subtitle="STAFF-ONLY DIRECTORY" label="counterparties"
              state={counterparties} emptyMessage="The staff-scoped synthetic directory returned no counterparties."
              columns={[
                { label: "Party type", render: (row) => row.party_type },
                { label: "Name", render: (row) => row.name },
              ]} getId={(row) => String(row.counterparty_id)} selectedId={selected.counterparties} onSelect={(id) => select("counterparties", id)}
              onPage={(page) => changePage("counterparties", page)} detailState={counterpartyDetail}
              renderDetail={(data) => {
                const row = data as Counterparty;
                return <DetailFields fields={[["Type", row.party_type], ["Code", row.code], ["Name", row.name]]} />;
              }} />
              </>
            : <section className="pr-collection"><header className="pr-section-head"><div><span className="pr-kicker">STAFF-ONLY DIRECTORY</span><h2>Counterparties</h2></div><span className="pr-readonly">NO REQUEST</span></header><p className="pr-notice">Staff access is required. This account did not request the counterparty directory.</p></section>
          )}
        </div>
      </div>
    </section>
  );
}
