import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  getPurchasingConfigurationReadiness,
  getQuoteLookupState,
  shouldLoadPurchasingConfiguration,
} from "./purchasing-workbench-state.mjs";
import PurchasingReadside from "./PurchasingReadside";
import RetailCollections from "./RetailCollections";
import RetailInvoices from "./RetailInvoices";
import DeliveryPreparation from "./DeliveryPreparation";
import PurchasingTransactions from "./PurchasingTransactions";
import SalesReturnIntake from "./SalesReturnIntake";
import { sidebarSections } from "./sidebar-destinations";
import { workspaceHash } from "./workspace-hash";
import "./purchasing-workbench.css";

export type PurchasingWorkbenchProps = {
  mode?: "purchasing" | "delivery" | "retail" | "warehouse" | "procurement";
  selectedUnitId: number;
  selectedUnitName: string;
  isStaff: boolean;
  username: string;
  csrfToken: string;
  locations: { id: number; code: string; name: string; parent_id: number | null }[];
  onOpenReports: () => void;
  onOpenDelivery?: () => void;
  onOpenInventoryReceipt?: () => void;
  onOpenInventoryPacking?: () => void;
  onOpenProcurementReceipt?: () => void;
  onOpenProcurementPacking?: () => void;
  onOpenRetailQuote?: () => void;
  onNavigateFunction?: (child: string) => void;
  onReceiptSuccess?: () => void;
  focusedView?: string;
};

export type GovernanceResponse = {
  test_only_label: string;
  business_unit: { id: number; code: string; name: string };
  role_assignments: { id: number; user_id: number; username: string; role: string }[];
  current_prices: {
    id: number;
    variant_id: number;
    sku: string;
    amount: string | number;
    currency: string;
    revision: number;
    approved_by: number | string | null;
  }[];
  work_calendar: { is_configured: boolean; closed_weekdays: number[]; closed_dates: string[] };
  storage_exception_approver_roles: string[];
  special_order_value_bands: {
    id: number;
    minimum_value: string | number;
    maximum_value: string | number | null;
    is_active: boolean;
    approval_reason: string;
  }[];
  special_order_bands_used_for_approval: boolean;
  special_order_bands_note: string;
};

export type Quote = {
  quote_id: number;
  business_unit_id: number;
  kind: string;
  status: string;
  customer_id: number;
  discount_percent: string | number;
  total_list_value: string | number;
  total_discount: string | number;
  total_quote_value: string | number;
  payment_plan: string;
  downpayment_percent: string | number | null;
  lines: {
    quote_line_id: number;
    variant_id: number;
    quantity: string | number;
    selling_price_snapshot: string | number;
    price_revision_snapshot: number;
    discount_percent: string | number;
    discount_amount: string | number;
    net_unit_price: string | number;
    net_total: string | number;
    specifications: Record<string, unknown> | string | null;
    preparation_identity?: {
      item_code: string; description: string; sku: string; brand: string;
      location_code: string; location_name: string;
    } | null;
  }[];
  special_order_value_bands_configured: boolean;
  payments: {
    payment_id: number;
    kind: string;
    amount: string | number;
    non_returnable_amount: string | number;
    simulated_reference: string;
    created_at: string;
  }[];
  approvals: {
    approval_id: number;
    kind: string;
    approver_id: number;
    role: string;
    reason: string;
    details: unknown;
    approved_at: string;
  }[];
  holds: {
    hold_id: number;
    status: string;
    quantity: string | number;
    working_days: number;
    expires_at: string;
  }[];
  events: {
    event_id: number;
    kind: string;
    message: string;
    details: unknown;
    occurred_at: string;
  }[];
  delivery_progress?: {
    ready_at: string | null;
    delivered_at: string | null;
    lines: {
      quote_line_id: number;
      variant_id: number;
      ordered_quantity: string;
      reserved_to_date_quantity: string;
      delivered_quantity: string;
      remaining_quantity: string;
    }[];
  };
  presentation_document?: {
    company?: {
      name_en?: string; name_ar?: string; trademark_en?: string; contact_en?: string; contact_ar?: string;
      identity_status?: string;
    };
    quote_date?: string;
    valid_until?: string;
    client?: {
      account?: string;
      name_en?: string;
      name_ar?: string;
      contact_en?: string;
      contact_ar?: string;
      contact_person_en?: string;
      contact_person_ar?: string;
      project_en?: string;
      project_ar?: string;
      address_en?: string;
      address_ar?: string;
      phone?: string;
      mobile?: string;
      email?: string;
    };
    notes_en?: string;
    notes_ar?: string;
    salesperson?: string;
    business_unit?: { code?: string; name?: string };
    department_en?: string;
    department_ar?: string;
    payment_terms_en?: string;
    payment_terms_ar?: string;
    delivery_time_en?: string;
    delivery_time_ar?: string;
    terms_status?: string;
    currency?: string;
    total_list_value?: string | number;
    total_discount?: string | number;
    total_quote_value?: string | number;
    lines?: Array<{
      variant_id: number; variant_sku?: string; item_code?: string; name_en?: string; name_ar?: string;
      category_en?: string; category_ar?: string; description_en?: string; description_ar?: string;
      image_path?: string | null; quantity?: string | number; unit_selling_price?: string | number;
      discount_percent?: string | number; discount_amount?: string | number; net_total?: string | number;
      unit_en?: string; unit_ar?: string; specifications?: unknown;
    }>;
  } | null;
};

function safeSameOriginImage(path: string | null | undefined) {
  if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//") || /[\u0000-\u001f]/.test(path)) return "";
  try {
    return typeof window === "undefined" || new URL(path, window.location.origin).origin === window.location.origin
      ? path : "";
  } catch {
    return "";
  }
}

type RequestState<T> =
  | { status: "idle" | "loading"; data: null; error: "" }
  | { status: "ready"; data: T; error: "" }
  | { status: "error"; data: null; error: string };

const idle = <T,>(): RequestState<T> => ({ status: "idle", data: null, error: "" });

class QuoteReadProblem extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    method: "GET",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body?.detail === "string"
      ? body.detail
      : typeof body?.message === "string"
        ? body.message
        : "";
    throw new QuoteReadProblem(detail || `Request unavailable (${response.status}).`, response.status);
  }
  return body as T;
}

function showValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function dateValue(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function Field({ label, value, mono = false }: { label: string; value: unknown; mono?: boolean }) {
  return (
    <div className="pw-field">
      <span>{label}</span>
      <strong className={mono ? "pw-mono" : ""}>{showValue(value)}</strong>
    </div>
  );
}

function ArabicField({ english, arabic, value, missing }: {
  english: string; arabic: string; value: string | undefined; missing: string;
}) {
  return (
    <div className="pw-field">
      <span>{english} <span lang="ar" dir="rtl">{arabic}</span></span>
      <strong lang="ar" dir="rtl">{value || "غير متوفر"}</strong>
      {!value && <small className="pw-missing-content">{missing}</small>}
    </div>
  );
}

function BilingualField({ english, arabic, value, mono = false }: {
  english: string; arabic: string; value: unknown; mono?: boolean;
}) {
  return (
    <div className="pw-field">
      <span>{english} <span lang="ar" dir="rtl">{arabic}</span></span>
      <strong className={mono ? "pw-mono" : ""}>{showValue(value)}</strong>
    </div>
  );
}

function SectionTitle({ index, title, count }: { index: string; title: string; count?: number }) {
  return (
    <div className="pw-section-title">
      <span>{index}</span>
      <h3>{title}</h3>
      {count !== undefined && <small>{count} returned</small>}
    </div>
  );
}

function DataTable({ headers, children }: { headers: string[]; children: ReactNode }) {
  return (
    <div className="pw-table-wrap">
      <table className="pw-table">
        <thead><tr>{headers.map((header) => <th scope="col" key={header}>{header}</th>)}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function GovernancePanel({
  state,
  isStaff,
  unitName,
  retry,
}: {
  state: RequestState<GovernanceResponse>;
  isStaff: boolean;
  unitName: string;
  retry: () => void;
}) {
  if (!isStaff) {
    return (
      <div className="pw-notice">
        <strong>Staff access required</strong>
        <p>Purchasing governance configuration is staff-only. No configuration request was made.</p>
      </div>
    );
  }
  if (state.status === "loading") {
    return <div className="pw-skeleton" role="status" aria-label="Loading unit configuration"><i /><i /><i /></div>;
  }
  if (state.status === "error") {
    return (
      <div className="pw-notice pw-error" role="alert">
        <strong>Configuration was not returned</strong>
        <p>{state.error}</p>
        <button className="pw-button pw-button-muted" type="button" onClick={retry}>Retry configuration</button>
      </div>
    );
  }
  if (state.status !== "ready") {
    return <div className="pw-empty"><span>01</span><strong>Configuration not requested</strong></div>;
  }

  const config = state.data;
  const readiness = getPurchasingConfigurationReadiness(config);
  const explanations: Record<string, string> = {
    "approval-roles": "Required for purchasing operations; this screen does not assign roles.",
    "approved-prices": "Quotes require an approved selling-price snapshot.",
    "work-calendar": "Required before the API can create a temporary hold.",
    counterparties: "Staff may review the synthetic directory in the read-only Counterparties tab.",
    "special-order-value-bands": "Bands are not treated as approval rules unless the API explicitly says they are used.",
    "storage-exception-approvers": "No exception approver role is returned unless explicitly configured.",
  };

  return (
    <div className="pw-governance">
      <div className="pw-caveat">
        <strong>{config.test_only_label || "Synthetic prototype configuration"}</strong>
        <span>For review only; this does not represent approved company policy.</span>
      </div>
      <div className="pw-unit-summary">
        <div><span className="pw-kicker">BUSINESS UNIT</span><h3>{config.business_unit?.name || unitName}</h3></div>
        <span className="pw-unit-code">{config.business_unit?.code ?? "—"} <i>·</i> ID {showValue(config.business_unit?.id)}</span>
      </div>
      <section className="pw-section">
        <SectionTitle index="0" title="Configuration readiness" />
        <ul className="pw-readiness-list">
          {readiness.map((item) => (
            <li key={item.key}>
              <span><strong>{item.label}</strong><small>{explanations[item.key]}</small></span>
              <b className={`pw-status ${item.status === "configured" ? "is-on" : "is-off"}`}>
                {item.status.replaceAll("-", " ")}
              </b>
            </li>
          ))}
        </ul>
        <p className="pw-readonly-note">
          Staff configuration is not a substitute for transaction grants. Write controls require a successful
          unit-scoped API access check and the relevant configuration; the server rechecks every submission.
        </p>
      </section>
      <section className="pw-section">
        <SectionTitle index="A" title="Role assignments" count={config.role_assignments?.length ?? 0} />
        {config.role_assignments?.length
          ? <DataTable headers={["User", "User ID", "Assigned role"]}>{config.role_assignments.map((row) => <tr key={row.id}><td><strong>{row.username}</strong></td><td className="pw-mono">{row.user_id}</td><td>{row.role}</td></tr>)}</DataTable>
          : <p className="pw-empty-inline">No role assignments are configured for this unit.</p>}
      </section>
      <section className="pw-section">
        <SectionTitle index="B" title="Current price snapshots" count={config.current_prices?.length ?? 0} />
        {config.current_prices?.length
          ? <DataTable headers={["SKU", "Variant", "Amount", "Currency", "Revision", "Approved by"]}>{config.current_prices.map((row) => <tr key={row.id}><td className="pw-mono"><strong>{row.sku}</strong></td><td>{row.variant_id}</td><td className="pw-number">{showValue(row.amount)}</td><td>{row.currency}</td><td className="pw-mono">{row.revision}</td><td>{showValue(row.approved_by)}</td></tr>)}</DataTable>
          : <p className="pw-empty-inline">No approved price snapshots are configured for this unit.</p>}
      </section>
      <section className="pw-section">
        <SectionTitle index="C" title="Work calendar" />
        <div className="pw-field-grid">
          <Field label="Configured" value={config.work_calendar?.is_configured ? "Yes" : "No"} />
          <Field label="Closed weekdays" value={config.work_calendar?.closed_weekdays?.join(", ") || "None returned"} />
          <Field label="Closed dates" value={config.work_calendar?.closed_dates?.join(", ") || "None returned"} />
        </div>
      </section>
      <section className="pw-section">
        <SectionTitle index="D" title="Storage exception approver roles" count={config.storage_exception_approver_roles?.length ?? 0} />
        {config.storage_exception_approver_roles?.length
          ? <div className="pw-role-list">{config.storage_exception_approver_roles.map((role, index) => <span key={`${role}-${index}`}>{role}</span>)}</div>
          : <p className="pw-empty-inline">No storage exception approver roles are configured.</p>}
      </section>
      <section className="pw-section">
        <SectionTitle index="E" title="Special-order value bands" count={config.special_order_value_bands?.length ?? 0} />
        {config.special_order_value_bands?.length
          ? <DataTable headers={["Minimum", "Maximum", "Active", "Approval reason"]}>{config.special_order_value_bands.map((row) => <tr key={row.id}><td className="pw-number">{showValue(row.minimum_value)}</td><td className="pw-number">{showValue(row.maximum_value)}</td><td><span className={`pw-status ${row.is_active ? "is-on" : "is-off"}`}>{row.is_active ? "Active" : "Inactive"}</span></td><td>{row.approval_reason}</td></tr>)}</DataTable>
          : <p className="pw-empty-inline">Special-order value bands remain unconfigured.</p>}
        <div className="pw-band-note">
          <Field label="Used for approval" value={config.special_order_bands_used_for_approval ? "Yes" : "No"} />
          <p>{config.special_order_bands_note || "No note returned."}</p>
        </div>
      </section>
    </div>
  );
}

export function PurchasingGovernanceReview({
  selectedUnitId,
  selectedUnitName,
  isStaff,
}: {
  selectedUnitId: number | null;
  selectedUnitName: string;
  isStaff: boolean;
}) {
  const [context, setContext] = useState({ unitId: selectedUnitId, isStaff, retryRevision: 0, state: idle<GovernanceResponse>() });
  const requestVersion = useRef(0);
  const [retryRevision, setRetryRevision] = useState(0);
  const current = context.unitId === selectedUnitId && context.isStaff === isStaff && context.retryRevision === retryRevision
    ? context.state
    : selectedUnitId !== null && isStaff
      ? { status: "loading" as const, data: null, error: "" as const }
      : idle<GovernanceResponse>();

  useEffect(() => {
    const version = ++requestVersion.current;
    if (!isStaff || selectedUnitId === null) {
      setContext({ unitId: selectedUnitId, isStaff, retryRevision, state: idle() });
      return;
    }
    let active = true;
    setContext({ unitId: selectedUnitId, isStaff, retryRevision, state: { status: "loading", data: null, error: "" } });
    getJson<GovernanceResponse>(`/api/governance/purchasing/${encodeURIComponent(String(selectedUnitId))}/`)
      .then((data) => {
        if (active && requestVersion.current === version) {
          setContext({ unitId: selectedUnitId, isStaff, retryRevision, state: { status: "ready", data, error: "" } });
        }
      })
      .catch((reason: unknown) => {
        if (active && requestVersion.current === version) {
          setContext({
            unitId: selectedUnitId,
            isStaff,
            retryRevision,
            state: { status: "error", data: null, error: reason instanceof Error ? reason.message : "Configuration request unavailable." },
          });
        }
      });
    return () => { active = false; };
  }, [isStaff, selectedUnitId, retryRevision]);

  const retry = () => setRetryRevision((value) => value + 1);
  return (
    <section className="content-card governance-purchasing-review" aria-labelledby="governance-purchasing-title">
      <div className="card-heading">
        <div>
          <span className="eyebrow">STAFF · SELECTED BUSINESS UNIT · READ ONLY</span>
          <h2 id="governance-purchasing-title">Purchasing configuration review</h2>
          <p>Configuration returned for the selected unit. This view does not change roles, prices, calendars or approval settings.</p>
        </div>
        <span className="pw-readonly-mark">VIEW ONLY</span>
      </div>
      {!isStaff ? (
        <div className="pw-notice"><strong>Staff access required</strong><p>No purchasing configuration request was made.</p></div>
      ) : selectedUnitId === null ? (
        <div className="governance-context-state" role="status">
          <strong>Business-unit context unavailable</strong>
          <p>Purchasing configuration cannot be reviewed until the selected unit is verified. Other independently authorized administration remains available.</p>
        </div>
      ) : (
        <GovernancePanel state={current} isStaff={isStaff} unitName={selectedUnitName} retry={retry} />
      )}
    </section>
  );
}

function QuotationSpecifications({ value }: { value: unknown }) {
  const fields = value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
  const english = fields && typeof fields.en === "string" ? fields.en : "";
  const arabic = fields && typeof fields.ar === "string" ? fields.ar : "";
  return <div className="pw-preview-specifications">
    <strong>Specifications <span lang="ar" dir="rtl">المواصفات</span></strong>
    <p>{english || (fields && ("en" in fields || "ar" in fields) ? "English specifications not supplied"
      : fields && Object.keys(fields).length === 0 ? "Not supplied" : showValue(value))}</p>
    {arabic ? <p lang="ar" dir="rtl">{arabic}</p> : <small>Arabic specifications not supplied</small>}
  </div>;
}

export function QuotePanel({ quote }: { quote: Quote }) {
  const document = quote.presentation_document;
  const presentationLines = document?.lines ?? [];
  const pageSize = 4;
  const previewPages = Array.from(
    { length: Math.max(1, Math.ceil(presentationLines.length / pageSize)) },
    (_, index) => presentationLines.slice(index * pageSize, (index + 1) * pageSize),
  );
  return (
    <div className="pw-quote">
      <div className="pw-quote-banner">
        <div><span className="pw-kicker">AUTHORIZED QUOTE RESPONSE</span><h3>Quote <span className="pw-mono">#{quote.quote_id}</span></h3></div>
        <span className="pw-status">{quote.status}</span>
      </div>
      <div className="pw-quote-meta">
        <Field label="Business unit ID" value={quote.business_unit_id} mono />
        <Field label="Kind" value={quote.kind} />
        <Field label="Customer ID" value={quote.customer_id} mono />
        <Field label="Payment plan" value={quote.payment_plan} />
        <Field label="Discount" value={`${showValue(quote.discount_percent)}%`} />
        <Field label="Downpayment" value={`${showValue(quote.downpayment_percent)}%`} />
        <Field label="Special-order bands configured" value={quote.special_order_value_bands_configured ? "Yes" : "No"} />
      </div>
      <div className="pw-totals">
        <div><span>List value</span><strong>{showValue(quote.total_list_value)}</strong></div>
        <div><span>Discount</span><strong>{showValue(quote.total_discount)}</strong></div>
        <div><span>Quote value</span><strong>{showValue(quote.total_quote_value)}</strong></div>
      </div>
      {document && (
        <section className="pw-presentation-document" aria-labelledby="pw-presentation-title">
          {previewPages.map((pageLines, pageIndex) => {
            const finalPage = pageIndex === previewPages.length - 1;
            return <article className="pw-preview-page" key={pageIndex} aria-label={`Quotation preview page ${pageIndex + 1} of ${previewPages.length}`}>
              <header className="pw-preview-header">
                <div className="pw-company-identity">
                  <span className="pw-kicker">ON-SCREEN PREVIEW · REVIEW ONLY</span>
                  <span className="pw-kicker" lang="ar" dir="rtl">معاينة عرض السعر على الشاشة · للمراجعة فقط</span>
                  <h3 id={pageIndex === 0 ? "pw-presentation-title" : undefined}>
                    {document.company?.trademark_en || document.company?.name_en || "Company legal name unavailable"}
                    {document.company?.name_ar && <span lang="ar" dir="rtl">{document.company.name_ar}</span>}
                  </h3>
                  {document.company?.trademark_en && <p>Registered company <span lang="ar" dir="rtl">الشركة المسجلة</span>: <strong>{document.company.name_en}</strong></p>}
                  {!document.company?.name_ar && <small className="pw-missing-content">Arabic company name not supplied; legal name is not translated.</small>}
                  <p>Company contact <span lang="ar" dir="rtl">بيانات اتصال الشركة</span>: {document.company?.contact_en || "Not supplied"} <span lang="ar" dir="rtl">{document.company?.contact_ar || "غير متوفر"}</span></p>
                  {!document.company?.contact_ar && <small className="pw-missing-content">Arabic company contact not supplied.</small>}
                  <p className="pw-company-identity-status">
                    {document.company?.identity_status === "owner_confirmed_registered_name"
                      ? <>Owner-confirmed registered name <span lang="ar" dir="rtl">الاسم المسجل مؤكد من المالك</span></>
                      : <>Historical identity snapshot · not rewritten <span lang="ar" dir="rtl">بيانات الهوية المحفوظة سابقاً دون تعديل</span></>}
                  </p>
                </div>
                <div className="pw-preview-reference">
                  <span>Quotation <span lang="ar" dir="rtl">عرض سعر</span></span>
                  <strong className="pw-mono">#{quote.quote_id}</strong>
                  <small>{document.business_unit?.code || "Business unit"} · {document.business_unit?.name || "Unit name unavailable"}</small>
                </div>
              </header>
              <div className="pw-presentation-meta">
                <BilingualField english="Quote date" arabic="تاريخ العرض" value={document.quote_date || "Unavailable"} />
                <BilingualField english="Salesperson" arabic="مسؤول المبيعات" value={document.salesperson || "Unavailable"} />
                <BilingualField english="Customer account" arabic="حساب العميل" value={document.client?.account || "Unavailable"} mono />
                <BilingualField english="Client" arabic="العميل" value={document.client?.name_en || "Client name unavailable"} />
                <ArabicField english="Client name" arabic="اسم العميل" value={document.client?.name_ar}
                  missing="Arabic client name not supplied." />
                <BilingualField english="Customer contact" arabic="بيانات اتصال العميل" value={document.client?.contact_en || "Not supplied"} />
                <ArabicField english="Customer contact" arabic="بيانات اتصال العميل" value={document.client?.contact_ar}
                  missing="Arabic client contact not supplied." />
                <BilingualField english="Contact person" arabic="جهة الاتصال" value={document.client?.contact_person_en || "Not supplied"} />
                <ArabicField english="Contact person" arabic="جهة الاتصال" value={document.client?.contact_person_ar}
                  missing="Arabic contact person not supplied." />
                <BilingualField english="Project" arabic="المشروع" value={document.client?.project_en || "Not supplied"} />
                <ArabicField english="Project" arabic="المشروع" value={document.client?.project_ar} missing="Arabic project not supplied." />
                <BilingualField english="Address" arabic="العنوان" value={document.client?.address_en || "Not supplied"} />
                <ArabicField english="Address" arabic="العنوان" value={document.client?.address_ar} missing="Arabic address not supplied." />
                <BilingualField english="Phone" arabic="الهاتف" value={document.client?.phone || "Not supplied"} />
                <BilingualField english="Mobile" arabic="الجوال" value={document.client?.mobile || "Not supplied"} />
                <BilingualField english="Email" arabic="البريد الإلكتروني" value={document.client?.email || "Not supplied"} />
                <BilingualField english="Business unit" arabic="وحدة الأعمال" value={document.business_unit?.name || "Unit name unavailable"} />
                <ArabicField english="Business unit name" arabic="اسم وحدة الأعمال" value={undefined}
                  missing="Arabic business-unit name was not supplied; the English name above is not translated." />
                <BilingualField english="Department" arabic="القسم" value={document.department_en || "Not specified"} />
                <ArabicField english="Department" arabic="القسم" value={document.department_ar} missing="Arabic department not supplied." />
                <BilingualField english="Valid until" arabic="صالح حتى" value={document.valid_until || "Not supplied"} />
              </div>
              <section className="pw-presentation-lines" aria-label={`Quotation items page ${pageIndex + 1}`}>
                <h4>Items <span lang="ar" dir="rtl">الأصناف</span></h4>
                {pageLines.length ? pageLines.map((line, lineOffset) => {
                  const index = pageIndex * pageSize + lineOffset;
                  const safeImage = safeSameOriginImage(line.image_path);
                  return <article key={`${line.variant_id}-${index}`} className="pw-presentation-line">
                    {safeImage
                      ? <img src={safeImage} alt={line.name_en || line.item_code || "Quotation item"} loading="lazy" />
                      : <div className="pw-image-missing" role="img" aria-label="Product image unavailable">Image unavailable</div>}
                    <div>
                      <strong>{line.name_en || line.item_code || "Item name unavailable"}</strong>
                      <span lang="ar" dir="rtl">{line.name_ar || "Arabic name unavailable"}</span>
                      {!line.name_ar && <small>Arabic item name not supplied</small>}
                      <span>Item ID {line.item_code || line.variant_sku || "Unavailable"} · Variant {line.variant_sku || line.variant_id} · {line.category_en || "Category unavailable"} / <span lang="ar" dir="rtl">{line.category_ar || "الفئة غير متوفرة"}</span></span>
                      {!line.category_ar && <small>Arabic category not supplied</small>}
                      {line.description_en && <p>{line.description_en}</p>}
                      {line.description_ar && <p lang="ar" dir="rtl">{line.description_ar}</p>}
                      {!line.description_en && <small>English description unavailable</small>}
                      {!line.description_ar && <small>Arabic description not supplied</small>}
                      {line.specifications !== undefined && <QuotationSpecifications value={line.specifications} />}
                      <div className="pw-presentation-line-values">
                        <span>Qty <span lang="ar" dir="rtl">الكمية</span>: <b>{showValue(line.quantity)}</b></span>
                        <span>Unit <span lang="ar" dir="rtl">الوحدة</span>: <b>{line.unit_en || "Not supplied"} · <span lang="ar" dir="rtl">{line.unit_ar || "غير متوفر"}</span></b></span>
                        {!line.unit_en && !line.unit_ar && <small>Display unit not supplied; quantity is unchanged.</small>}
                        {!line.unit_ar && <small>Arabic unit not supplied</small>}
                        <span>Unit price <span lang="ar" dir="rtl">سعر الوحدة</span>: <b>{showValue(line.unit_selling_price)} {document.currency || ""}</b></span>
                        <span>Discount <span lang="ar" dir="rtl">الخصم</span>: <b>{showValue(line.discount_percent)}% · {showValue(line.discount_amount)}</b></span>
                        <span>Line total <span lang="ar" dir="rtl">الإجمالي</span>: <b>{showValue(line.net_total)} {document.currency || ""}</b></span>
                      </div>
                    </div>
                  </article>;
                }) : <p className="pw-empty-inline">No presentation lines were returned.</p>}
              </section>
              {finalPage && <>
                <div className="pw-presentation-terms">
                  <div><strong>Payment terms <span lang="ar" dir="rtl">شروط الدفع</span></strong><p>{document.payment_terms_en || "Not supplied"}<span lang="ar" dir="rtl">{document.payment_terms_ar || "غير محدد"}</span></p>{!document.payment_terms_ar && <small className="pw-missing-content">Arabic payment terms not supplied</small>}</div>
                  <div><strong>Delivery estimate <span lang="ar" dir="rtl">مدة التسليم</span></strong><p>{document.delivery_time_en || "Not supplied"}<span lang="ar" dir="rtl">{document.delivery_time_ar || "غير محدد"}</span></p>{!document.delivery_time_ar && <small className="pw-missing-content">Arabic delivery estimate not supplied</small>}</div>
                  <div><strong>Department <span lang="ar" dir="rtl">القسم</span></strong><p>{document.department_en || "Not supplied"}<span lang="ar" dir="rtl">{document.department_ar || "غير محدد"}</span></p>{!document.department_ar && <small className="pw-missing-content">Arabic department not supplied</small>}</div>
                  <div><strong>Notes <span lang="ar" dir="rtl">ملاحظات</span></strong><p>{document.notes_en || "Not supplied"}<span lang="ar" dir="rtl">{document.notes_ar || "غير محدد"}</span></p>{!document.notes_ar && <small className="pw-missing-content">Arabic notes not supplied</small>}</div>
                  <div className="pw-presentation-totals">
                    <span>List value <span lang="ar" dir="rtl">قيمة القائمة</span> <b>{showValue(document.total_list_value)} {document.currency || ""}</b></span>
                    <span>Discount <span lang="ar" dir="rtl">الخصم</span> <b>{showValue(document.total_discount)} {document.currency || ""}</b></span>
                    <span>Quotation total <span lang="ar" dir="rtl">إجمالي العرض</span> <b>{showValue(document.total_quote_value)} {document.currency || ""}</b></span>
                  </div>
                </div>
                <aside className="pw-pending-terms" role="note">
                  <strong>Terms and conditions <span lang="ar" dir="rtl">الشروط والأحكام</span></strong>
                  <span>Pending owner text · no legal terms supplied.</span>
                  <span lang="ar" dir="rtl">بانتظار النص المعتمد من المالك · لم تُضف شروط قانونية.</span>
                </aside>
                <div className="pw-preview-signatures">
                  <div><strong>Customer signature <span lang="ar" dir="rtl">توقيع العميل</span></strong><i aria-hidden="true" /><span>Name / date <span lang="ar" dir="rtl">الاسم / التاريخ</span></span></div>
                  <div><strong>Salesperson signature <span lang="ar" dir="rtl">توقيع مسؤول المبيعات</span></strong><i aria-hidden="true" /><span>Name / date <span lang="ar" dir="rtl">الاسم / التاريخ</span></span></div>
                </div>
              </>}
              <footer className="pw-preview-page-number">Page {pageIndex + 1} of {previewPages.length} <span lang="ar" dir="rtl">الصفحة {pageIndex + 1} من {previewPages.length}</span></footer>
            </article>;
          })}
        </section>
      )}
      <section className="pw-section"><SectionTitle index="01" title="Quote lines" count={quote.lines?.length ?? 0} />
        {quote.lines?.length
          ? <DataTable headers={["Line / variant", "Qty", "Selling snapshot", "Revision", "Discount", "Discount amount", "Net unit", "Net total", "Specifications"]}>{quote.lines.map((line) => <tr key={line.quote_line_id}><td><strong>#{line.quote_line_id}</strong><small>Variant {line.variant_id}</small></td><td className="pw-number">{line.quantity}</td><td className="pw-number">{showValue(line.selling_price_snapshot)}</td><td className="pw-mono">{line.price_revision_snapshot}</td><td>{showValue(line.discount_percent)}%</td><td className="pw-number">{showValue(line.discount_amount)}</td><td className="pw-number">{showValue(line.net_unit_price)}</td><td className="pw-number"><strong>{showValue(line.net_total)}</strong></td><td><pre className="pw-json">{showValue(line.specifications)}</pre></td></tr>)}</DataTable>
          : <p className="pw-empty-inline">No lines returned for this quote.</p>}
      </section>
      {quote.delivery_progress && (
        <section className="pw-section" aria-label="Read-only delivery progress">
          <SectionTitle index="06" title="Delivery progress · read only" count={quote.delivery_progress.lines?.length ?? 0} />
          <div className="pw-field-grid">
            <Field label="Ready at" value={quote.delivery_progress.ready_at} />
            <Field label="Delivered at" value={quote.delivery_progress.delivered_at} />
          </div>
          <p className="pw-readonly-note">Remaining is the server-returned ordered minus delivered quantity. Reservation coverage is historical and does not represent active reserved stock.</p>
          {quote.delivery_progress.lines?.length ? (
            <DataTable headers={["Quote line / variant", "Ordered", "Reserved to date", "Delivered", "Remaining (ordered minus delivered)"]}>
              {quote.delivery_progress.lines.map((line) => (
                <tr key={line.quote_line_id}>
                  <td><strong>#{line.quote_line_id}</strong><small>Variant {line.variant_id}</small></td>
                  <td className="pw-number">{line.ordered_quantity}</td>
                  <td className="pw-number">{line.reserved_to_date_quantity}</td>
                  <td className="pw-number">{line.delivered_quantity}</td>
                  <td className="pw-number">{line.remaining_quantity}</td>
                </tr>
              ))}
            </DataTable>
          ) : <p className="pw-empty-inline">No delivery progress lines were returned for this quote.</p>}
        </section>
      )}
      <section className="pw-section"><SectionTitle index="02" title="Simulated payments" count={quote.payments?.length ?? 0} />
        {quote.payments?.length
          ? <DataTable headers={["Payment", "Kind", "Amount", "Non-returnable", "Simulated reference", "Created"]}>{quote.payments.map((payment) => <tr key={payment.payment_id}><td className="pw-mono">{payment.payment_id}</td><td>{payment.kind}</td><td className="pw-number">{showValue(payment.amount)}</td><td className="pw-number">{showValue(payment.non_returnable_amount)}</td><td className="pw-mono">{payment.simulated_reference}</td><td>{dateValue(payment.created_at)}</td></tr>)}</DataTable>
          : <p className="pw-empty-inline">No payment records were returned for this quote.</p>}
      </section>
      <section className="pw-section"><SectionTitle index="03" title="Approvals" count={quote.approvals?.length ?? 0} />
        {quote.approvals?.length
          ? <DataTable headers={["Approval", "Kind", "Approver", "Role", "Reason", "Details", "Approved"]}>{quote.approvals.map((approval) => <tr key={approval.approval_id}><td className="pw-mono">{approval.approval_id}</td><td>{approval.kind}</td><td className="pw-mono">{approval.approver_id}</td><td>{approval.role}</td><td>{approval.reason}</td><td><pre className="pw-json">{showValue(approval.details)}</pre></td><td>{dateValue(approval.approved_at)}</td></tr>)}</DataTable>
          : <p className="pw-empty-inline">No approvals were returned for this quote.</p>}
      </section>
      <section className="pw-section"><SectionTitle index="04" title="Holds" count={quote.holds?.length ?? 0} />
        {quote.holds?.length
          ? <DataTable headers={["Hold", "Status", "Quantity", "Working days", "Expires"]}>{quote.holds.map((hold) => <tr key={hold.hold_id}><td className="pw-mono">{hold.hold_id}</td><td>{hold.status}</td><td className="pw-number">{hold.quantity}</td><td className="pw-number">{hold.working_days}</td><td>{dateValue(hold.expires_at)}</td></tr>)}</DataTable>
          : <p className="pw-empty-inline">No holds were returned for this quote.</p>}
      </section>
      <section className="pw-section"><SectionTitle index="05" title="Workflow events" count={quote.events?.length ?? 0} />
        {quote.events?.length
          ? <div className="pw-event-list">{quote.events.map((event) => <article className="pw-event" key={event.event_id}><span className="pw-event-time">{dateValue(event.occurred_at)}</span><div><strong>{event.kind}</strong><p>{event.message}</p>{event.details !== null && event.details !== undefined && <pre className="pw-json">{showValue(event.details)}</pre>}</div></article>)}</div>
          : <p className="pw-empty-inline">No workflow events were returned for this quote.</p>}
        <p className="pw-api-limit">Delivery progress and readiness/handover timestamps are read from the authorized quote response. Stored reservation coverage is not active stock, and this view does not enable partial delivery.</p>
      </section>
    </div>
  );
}

export default function PurchasingWorkbench({
  mode = "purchasing",
  selectedUnitId,
  selectedUnitName,
  isStaff,
  username,
  csrfToken,
  locations,
  onOpenReports,
  onOpenDelivery,
  onOpenInventoryReceipt,
  onOpenInventoryPacking,
  onOpenProcurementReceipt,
  onOpenProcurementPacking,
  onOpenRetailQuote,
  onNavigateFunction,
  onReceiptSuccess,
  focusedView,
}: PurchasingWorkbenchProps) {
  const deliveryMode = mode === "delivery";
  const retailMode = mode === "retail";
  const warehouseMode = mode === "warehouse";
  const procurementMode = mode === "procurement";
  const workspaceSection = retailMode ? "retail" : deliveryMode ? "delivery" : procurementMode ? "procurement" : warehouseMode ? "inventory" : "purchasing";
  const focus = focusedView;
  const isOverview = focus === "main";
  const overviewFunctions = sidebarSections({
    isStaff, isSuperuser: false, canManageReportPanels: false,
  }).find((item) => item.id === workspaceSection)?.children.filter((child) => child.id !== "main") ?? [];
  const openFunction = (child: string) => {
    if (onNavigateFunction) onNavigateFunction(child);
    else window.location.hash = workspaceHash(workspaceSection, child);
  };
  const transactionEnabled = focus === undefined
    || (!deliveryMode && !retailMode && !procurementMode && !warehouseMode && focus === "transactions")
    || (procurementMode && ["commitments", "progress", "receipt", "packing", "receiving"].includes(focus))
    || (retailMode && ["quote", "actions"].includes(focus))
    || (deliveryMode && ["handover", "returns"].includes(focus))
    || (warehouseMode && ["receipt", "commitments", "progress", "receiving", "packing"].includes(focus));
  const [returnIntakeOpened, setReturnIntakeOpened] = useState(false);
  useEffect(() => {
    if (focus === "returns") setReturnIntakeOpened(true);
  }, [focus]);
  const [governance, setGovernance] = useState<RequestState<GovernanceResponse>>(idle);
  const [governanceAttempt, setGovernanceAttempt] = useState(0);
  const [governanceContext, setGovernanceContext] = useState({ unitId: selectedUnitId, isStaff });
  const [quoteId, setQuoteId] = useState("");
  const [quote, setQuote] = useState<RequestState<Quote>>(idle);
  const [quoteUnitId, setQuoteUnitId] = useState(selectedUnitId);
  const [readsideRefresh, setReadsideRefresh] = useState(0);
  const quoteRequestId = useRef(0);
  const quoteNavigationVersion = useRef(0);
  const quoteAccessRequestId = useRef(0);
  const [quoteAccessMessage, setQuoteAccessMessage] = useState("");
  const currentQuoteContext = useRef({ selectedUnitId, username, id: quote.data?.quote_id });
  currentQuoteContext.current = { selectedUnitId, username, id: quote.data?.quote_id };
  useEffect(() => () => { quoteAccessRequestId.current += 1; }, []);
  // Reset local lookup state before children commit a different BU, without
  // remounting the workbench or its transaction forms.
  if (quoteUnitId !== selectedUnitId) {
    setQuoteUnitId(selectedUnitId);
    setQuoteId("");
    setQuote(idle());
    setQuoteAccessMessage("");
  }
  // Do not commit a prior unit/staff context's configuration to either child.
  // The existing effect still owns fetching, retries and late-response cleanup.
  if (governanceContext.unitId !== selectedUnitId || governanceContext.isStaff !== isStaff) {
    setGovernanceContext({ unitId: selectedUnitId, isStaff });
    setGovernance(idle());
  }
  const quoteLookupState = getQuoteLookupState({
    submitted: quote.status !== "idle",
    loading: quote.status === "loading",
    error: quote.status === "error" ? quote.error : "",
    quote: quote.status === "ready" ? quote.data : null,
  });
  useEffect(() => {
    if (!transactionEnabled) {
      setGovernance(idle());
      return;
    }
    if (!shouldLoadPurchasingConfiguration(isStaff)) {
      setGovernance(idle());
      return;
    }
    let active = true;
    setGovernance({ status: "loading", data: null, error: "" });
    getJson<GovernanceResponse>(`/api/governance/purchasing/${encodeURIComponent(String(selectedUnitId))}/`)
      .then((data) => { if (active) setGovernance({ status: "ready", data, error: "" }); })
      .catch((error: unknown) => {
        if (active) {
          setGovernance({
            status: "error",
            data: null,
            error: error instanceof Error ? error.message : "Configuration request unavailable.",
          });
        }
      });
    return () => { active = false; };
  }, [transactionEnabled, deliveryMode, isStaff, selectedUnitId, governanceAttempt]);

  useEffect(() => {
    quoteRequestId.current += 1;
  }, [selectedUnitId]);

  async function loadQuoteById(value: string) {
    setQuoteAccessMessage("");
    const id = value.trim();
    if (!/^\d+$/.test(id) || Number(id) <= 0) {
      quoteRequestId.current += 1;
      setQuote({ status: "error", data: null, error: "Enter a positive numeric quote ID." });
      return;
    }
    const requestId = ++quoteRequestId.current;
    setQuote({ status: "loading", data: null, error: "" });
    try {
      const data = await getJson<Quote>(
        deliveryMode && focus === "preparation"
          ? `/api/purchasing/quotes/${encodeURIComponent(id)}/preparation/?business_unit_id=${selectedUnitId}&unit=${selectedUnitId}`
          : `/api/purchasing/quotes/${encodeURIComponent(id)}/?business_unit_id=${encodeURIComponent(String(selectedUnitId))}`,
      );
      if (quoteRequestId.current === requestId) setQuote({ status: "ready", data, error: "" });
    } catch (error) {
      if (quoteRequestId.current === requestId) {
        setQuote({
          status: "error",
          data: null,
          error: error instanceof Error ? error.message : "Quote request unavailable.",
        });
      }
    }
  }

  async function submitQuote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    quoteNavigationVersion.current += 1;
    await loadQuoteById(quoteId);
  }

  async function recheckCurrentQuoteAccess() {
    const context = currentQuoteContext.current;
    if (!context.id) return; // A newer explicit lookup may already be pending.
    const version = ++quoteAccessRequestId.current;
    const lookupVersion = quoteRequestId.current;
    const navigationVersion = quoteNavigationVersion.current;
    const isCurrent = () => quoteAccessRequestId.current === version
      && quoteRequestId.current === lookupVersion
      && quoteNavigationVersion.current === navigationVersion
      && currentQuoteContext.current.selectedUnitId === context.selectedUnitId
      && currentQuoteContext.current.username === context.username
      && currentQuoteContext.current.id === context.id;
    try {
      const path = deliveryMode && focus === "preparation"
        ? `/api/purchasing/quotes/${context.id}/preparation/?business_unit_id=${context.selectedUnitId}&unit=${context.selectedUnitId}`
        : `/api/purchasing/quotes/${context.id}/?business_unit_id=${context.selectedUnitId}`;
      const data = await getJson<Quote>(path);
      if (!isCurrent()) return;
      setQuoteAccessMessage("");
      setQuote({ status: "ready", data, error: "" });
    } catch (error) {
      if (!isCurrent()) return;
      if (error instanceof QuoteReadProblem && (error.status === 403 || error.status === 404)) {
        quoteRequestId.current += 1;
        setQuoteAccessMessage("");
        setQuote({ status: "error", data: null, error: error.message });
      } else {
        // Neither a transport failure nor a 5xx proves access was revoked.
        setQuoteAccessMessage("Quote access could not be rechecked. Previously loaded details are retained; retry the quote lookup.");
      }
    }
  }

  // Returning to preparation always rechecks access, rather than trusting a
  // retained quote or its earlier independently authorized stock identities.
  useEffect(() => {
    if (deliveryMode && focus === "preparation" && /^\d+$/.test(quoteId)) {
      void loadQuoteById(quoteId);
    }
  }, [focus]);

  function refreshAfterMutation(updatedQuoteId?: number) {
    setReadsideRefresh((value) => value + 1);
    if (updatedQuoteId) {
      setQuoteId(String(updatedQuoteId));
      void loadQuoteById(String(updatedQuoteId));
    }
  }

  return (
    <section className="purchasing-workbench" aria-labelledby="purchasing-workbench-title" data-workspace-view={focusedView === undefined || warehouseMode ? undefined : `${workspaceSection}/${focus}`}>
      <header className="pw-header">
        <div className="pw-heading-copy">
          <div className="pw-kicker">{deliveryMode ? "DELIVERY & AFTER-SALES · REVIEW WORKSPACE" : retailMode ? "RETAIL & SALES · REVIEW WORKSPACE" : procurementMode ? "PROCUREMENT & IMPORTS · RECEIVING WORKSPACE" : warehouseMode ? "INVENTORY · RECEIVING WORKSPACE" : "PURCHASING · REVIEW WORKSPACE"}</div>
          <h1 id="purchasing-workbench-title">{focus === "preparation" ? "Picking preparation" : focus && focus !== "main"
            ? overviewFunctions.find((child) => child.id === focus)?.label
              ?? ({ quote: deliveryMode ? "Authorized quote lookup" : "Quote lookup", actions: "Quote collection & holds", register: "Authorized quote register", collections: "Collection follow-up", invoices: "Customer invoices", transactions: "Transaction workbench", registers: "Purchasing records", commitments: "Purchase commitments", progress: "Commitment receipt progress", receipt: "Imported-goods receipt", packing: "Packing-list review", suppliers: "Staff counterparty directory", handover: "Readiness & handover", returns: "Sales-return intake", quotes: "Quote register", storage: "Storage-charge register", reservations: "Reservation register", deliveries: "Delivery register" } as Record<string, string>)[focus] ?? focus
            : deliveryMode ? "Delivery & after-sales" : retailMode ? "Retail & Sales" : procurementMode ? "Procurement & Imports" : warehouseMode ? "Inventory receiving" : "Purchasing workbench"}</h1>
          <p>{deliveryMode
            ? "Follow authorized quotes from readiness to handover within the selected business unit. Existing access checks remain authoritative."
            : retailMode
              ? "Create synthetic stock or special-order quotes from approved snapshots, then follow only the actions this account is authorized to take."
              : procurementMode
                ? "Prepare supplier commitments, review partial receipts and inspect imported-goods records. Every submission uses existing unit-scoped server checks."
                : warehouseMode
                  ? "Prepare and record receipts using the existing unit-scoped gates. A commitment is not received stock; the server rechecks every submission."
                : "Review authorized purchasing records and use only transaction actions enabled by this account’s existing access and configuration."}</p>
        </div>
        <div className="pw-stamp"><span>TEST ONLY</span><strong>SYNTHETIC<br />DATA</strong></div>
      </header>
      {isOverview && <div className="pw-caution"><span className="pw-caution-mark" aria-hidden="true">i</span><p>Returned values are for synthetic demonstration and review. They do not imply approved company policy or authorize a transaction.</p></div>}
      {(isOverview || focus === "quote" || (retailMode && focus === undefined)) && retailMode && onOpenDelivery && <div className="pw-retail-delivery-link">
        <p>Delivery progress is read here; readiness and handover actions stay in the Delivery workspace.</p>
        <button className="pw-button pw-button-muted" type="button" onClick={onOpenDelivery}>Open Delivery &amp; After-Sales</button>
      </div>}
      {(isOverview && !warehouseMode) && (
        <section className="pw-overview-launcher" aria-label={`${workspaceSection} functions`}>
          <div><span className="pw-kicker">FUNCTIONS</span><h2>Choose a focused workspace</h2><p>Each function keeps existing account and business-unit checks unchanged.</p></div>
          <div className="pw-overview-launcher-list">
            {overviewFunctions.map((child) => <button type="button" key={child.id} onClick={() => openFunction(child.id)}>
              <span><strong>{child.label}</strong><small>{retailMode ? "Existing quote and collection checks"
                : procurementMode ? child.id === "suppliers" ? "Staff-only read-side directory" : "Existing receipt and commitment checks"
                  : deliveryMode ? "Authorized read-side or action view"
                    : child.id === "quote" ? "Authorized detail request"
                      : child.id === "transactions" ? "Existing action gates" : "Read-side registers"}</small></span>
              <span aria-hidden="true">→</span>
            </button>)}
          </div>
        </section>
      )}
      {procurementMode && onOpenInventoryReceipt && <div className="pw-contextual-link">
        <span>Inventory receiving uses the same existing receipt checks.</span>
        <div><button className="pw-button pw-button-muted" type="button" onClick={onOpenInventoryReceipt}>Inventory receipt</button>
        {onOpenInventoryPacking && <button className="pw-button pw-button-muted" type="button" onClick={onOpenInventoryPacking}>Inventory packing preview</button>}</div>
      </div>}
      {warehouseMode && <div className="pw-contextual-link">
        <span>Procurement preparation and inventory receipt review share no new authority.</span>
        <div>{onOpenProcurementReceipt && <button className="pw-button pw-button-muted" type="button" onClick={onOpenProcurementReceipt}>Procurement receipt</button>}
        {onOpenProcurementPacking && <button className="pw-button pw-button-muted" type="button" onClick={onOpenProcurementPacking}>Procurement packing review</button>}</div>
      </div>}
      {deliveryMode && focus === "quote" && onOpenRetailQuote && <div className="pw-contextual-link">
        <span>Retail quote creation stays within its existing account checks.</span>
        <button className="pw-button pw-button-muted" type="button" onClick={onOpenRetailQuote}>Open Retail quote workspace</button>
      </div>}
      {isOverview && <div className="pw-overview-strip">
        <div><span>ACTIVE UNIT</span><strong>{selectedUnitName}</strong></div>
        <div><span>UNIT ID</span><strong className="pw-mono">{selectedUnitId}</strong></div>
        <div><span>WORKSPACE MODE</span><strong>Authorization gated</strong></div>
      </div>}

      {retailMode && focus === "invoices" && <RetailInvoices
        selectedUnitId={selectedUnitId}
        selectedUnitName={selectedUnitName}
        username={username}
        refreshCounter={readsideRefresh}
      />}

      {retailMode && focus === "collections" && <RetailCollections
        selectedUnitId={selectedUnitId}
        selectedUnitName={selectedUnitName}
        username={username}
        refreshCounter={readsideRefresh}
        onOpenQuoteActions={(id) => {
          setQuoteId(String(id));
          quoteNavigationVersion.current += 1;
          void loadQuoteById(String(id));
          openFunction("actions");
        }}
      />}

        {!deliveryMode && !warehouseMode && !procurementMode && focus === undefined && <section className="pw-card pw-governance-card">
        <div className="pw-card-heading">
          <div><span className="pw-kicker">STAFF CONFIGURATION REVIEW</span><h2>Unit purchasing configuration</h2><p>Server-returned assignments and configuration. No controls here change purchasing setup.</p></div>
          <span className={`pw-access-chip ${isStaff ? "is-authorized" : ""}`}>{isStaff ? "Staff view" : "Staff only"}</span>
        </div>
        <GovernancePanel
          state={governance}
          isStaff={isStaff}
          unitName={selectedUnitName}
          retry={() => setGovernanceAttempt((value) => value + 1)}
        />
      </section>}

      {!warehouseMode && !procurementMode && <section className="pw-card pw-quote-card" hidden={focus !== undefined && (focus === "quote" && retailMode || !["quote", "preparation", "actions", "handover", "transactions"].includes(focus))}>
        <div className="pw-card-heading">
          <div><span className="pw-kicker">AUTHORIZED DETAIL LOOKUP</span><h2>{focus === "transactions" ? "Select a quote for this transaction" : "Inspect a quote"}</h2><p>Enter a quote ID to request its detail. The server determines whether this signed-in actor may view it.</p></div>
          <span className="pw-readonly-mark">GET ONLY</span>
        </div>
        <form className="pw-quote-form" onSubmit={(event) => void submitQuote(event)}>
          <label htmlFor="pw-quote-id">Quote ID</label>
          <div className="pw-quote-input-row">
            <input
              id="pw-quote-id"
              inputMode="numeric"
              pattern="[0-9]*"
              value={quoteId}
              onChange={(event) => {
                setQuoteId(event.target.value);
                setQuoteAccessMessage("");
                quoteRequestId.current += 1;
                setQuote(idle());
              }}
              placeholder="Enter numeric quote ID"
              aria-describedby="pw-quote-help"
            />
            <button className="pw-button pw-button-primary" type="submit" disabled={quote.status === "loading"}>
              {quote.status === "loading" ? "Requesting…" : "Request quote"}
            </button>
          </div>
          <small id="pw-quote-help">The quote endpoint enforces business-unit authorization. A denied or unavailable request displays no quote data.</small>
        </form>
        {quoteLookupState === "loading" && <div className="pw-skeleton pw-quote-skeleton" role="status" aria-label="Loading quote details"><i /><i /><i /></div>}
        {quoteLookupState === "error" && <div className="pw-notice pw-error" role="alert"><strong>Quote data was not returned</strong><p>{quote.error}</p></div>}
        {quoteLookupState === "ready" && quote.status === "ready" && focus !== "preparation" && <QuotePanel quote={quote.data} />}
        {quoteLookupState === "idle" && <div className="pw-empty pw-quote-empty"><span>Q</span><strong>No quote requested</strong><p>{focus === "transactions"
          ? "Load an authorized quote here to make its eligible transaction actions available in this view."
          : focus !== undefined
            ? "Use the Quote register in section navigation to browse authorized records, or enter a quote ID."
            : "Use the Quotes register below to browse your authorized list, or enter a quote ID."}</p></div>}
        {quoteLookupState === "empty" && <div className="pw-empty"><span>—</span><strong>No authorized response data</strong></div>}
      </section>}
      {quoteAccessMessage && <p className="pw-notice" role="status">{quoteAccessMessage}</p>}
      {deliveryMode && focus === "preparation" && <DeliveryPreparation
        quote={quote.status === "ready" ? quote.data : null}
        selectedUnitId={selectedUnitId}
        onOpenHandover={() => openFunction("handover")}
      />}

      <PurchasingTransactions
        selectedUnitId={selectedUnitId}
        selectedUnitName={selectedUnitName}
        username={username}
        isStaff={isStaff}
        csrfToken={csrfToken}
        locations={locations}
        configuration={governance.status === "ready" ? governance.data : null}
        configurationState={governance.status}
        quote={quote.status === "ready" ? quote.data : null}
        mode={mode}
        transactionEnabled={transactionEnabled}
        quoteLoading={quote.status === "loading"}
        quoteNavigationVersion={quoteNavigationVersion}
        refreshKey={readsideRefresh}
        focusedView={focus}
        onQuoteCreated={(id) => refreshAfterMutation(id)}
        onMutation={(id) => refreshAfterMutation(id)}
        onQuoteAccessRecheck={() => { void recheckCurrentQuoteAccess(); }}
        onReceiptSuccess={onReceiptSuccess}
      />

      {!retailMode && !warehouseMode && !procurementMode && <details className="pw-panel" hidden={focus !== undefined && focus !== "returns"} open={focus === "returns" || undefined} onToggle={(event) => {
        if (event.currentTarget.open) setReturnIntakeOpened(true);
      }}>
        <summary id="pw-sales-return-summary">Sales-return intake — synthetic, non-posting</summary>
        {returnIntakeOpened && <SalesReturnIntake
          selectedUnitId={selectedUnitId}
          username={username}
          csrfToken={csrfToken}
        />}
      </details>}

      <div hidden={focus !== undefined && !["register", "registers", "suppliers", "quotes", "storage", "reservations", "deliveries"].includes(focus)}>
        <PurchasingReadside
          key={`${selectedUnitId}:${readsideRefresh}`}
          selectedUnitId={selectedUnitId}
          selectedUnitName={selectedUnitName}
          isStaff={isStaff}
          csrfToken={csrfToken}
          mode={mode}
          onOpenReports={onOpenReports}
          enabled={focus === undefined || ["register", "registers", "suppliers", "quotes", "storage", "reservations", "deliveries"].includes(focus)}
          focusedTab={["register", "quotes"].includes(focus ?? "") ? "quotes" : focus === "storage" || focus === "reservations" || focus === "deliveries" ? focus : focus === "suppliers" ? "counterparties" : undefined}
        />
      </div>
      <footer className="pw-footer"><span>TIVOLI ERP · {deliveryMode ? "DELIVERY REVIEW" : procurementMode ? "PROCUREMENT & IMPORTS" : warehouseMode ? "INVENTORY RECEIVING" : "PURCHASING REVIEW"}</span><span>Prototype only · existing server-authorized transitions</span></footer>
    </section>
  );
}
