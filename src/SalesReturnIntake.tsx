import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

type SaleLine = {
  quote_line_id: number;
  variant_id: number;
  item_id: string;
  description: string;
  brand: string;
  quantity: string | number;
};
type SaleDetail = {
  quote_id: number;
  delivered_at: string;
  customer: { id: number; name: string };
  business_unit: { id: number; code: string; name: string };
  lines: SaleLine[];
  locations: { id: number; code: string; name: string }[];
  delivery_window: DeliveryWindow;
};
type DeliveryWindow = {
  date_basis: string;
  calendar_days: number;
  delivery_date: string | null;
  deadline: string | null;
  request_date: string;
  eligible: boolean | null;
  message: string;
};
type ReturnLine = {
  quote_line_id: number;
  quantity: string | number;
  description: string;
  item_id: string;
  brand: string;
  condition: "uninspected" | "good" | "not_good";
  inspection_notes: string;
};
type SalesReturn = {
  id: number;
  quote_id: number;
  request_date: string;
  customer: { id: number; name: string };
  business_unit: { id: number; code: string; name: string };
  status: "awaiting_inspection" | "finance_pending";
  receiving_mode: "showroom" | "warehouse_collection";
  location: { id: number; code: string; name: string };
  receipt_collection_details: string;
  proposed_refund_amount: string;
  currency: "QAR";
  original_payment_method: "cash" | "card";
  requested_refund_method: "cash" | "card";
  refund_preference: "usually_original" | "cash" | "card";
  original_method_source: "user_recorded_not_verified";
  lines: ReturnLine[];
  events: { kind: string; occurred_at: string }[];
  policy?: ReturnPolicy;
  delivery_window?: Partial<DeliveryWindow> | null;
};
type ReturnPolicy = {
  provisional_calendar_days?: number;
  calendar_days?: number;
  date_basis: string;
  source: string;
  status: string;
};
type PageResponse = {
  rows: SalesReturn[];
  policy: ReturnPolicy;
  pagination: {
    page: number;
    page_size: number;
    total_count: number;
    total_pages: number;
    previous_page: number | null;
    next_page: number | null;
  };
};
type Result<T> = { context: string; data: T | null; state: "idle" | "loading" | "ready" | "error" | "denied"; error: string };
type WindowResult = { context: string; requestDate: string; data: DeliveryWindow | null; state: "idle" | "loading" | "ready" | "error" | "denied"; error: string };
type DraftLine = { quantity: string };
type CreateDraft = {
  quoteId: string;
  requestDate: string;
  receivingMode: "showroom" | "warehouse_collection";
  locationId: string;
  details: string;
  amount: string;
  originalMethod: "" | "cash" | "card";
  preference: "usually_original" | "cash" | "card";
  lines: Record<number, DraftLine>;
};
type InspectionDraft = Record<number, { condition: "" | "good" | "not_good"; notes: string }>;
type Props = { selectedUnitId: number; username: string; csrfToken: string };

const today = () => {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
};
const emptyDraft = (): CreateDraft => ({
  quoteId: "", requestDate: today(), receivingMode: "showroom", locationId: "",
  details: "", amount: "", originalMethod: "", preference: "usually_original", lines: {},
});
function requestKey() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `synthetic-return-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
async function readJson<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail
      : typeof body?.message === "string" ? body.message : "";
    throw Object.assign(new Error(detail || `Request unavailable (${response.status}).`), { status: response.status });
  }
  return body as T;
}
function formatDate(value: string | null) {
  if (!value) return "Not recorded";
  // Server calendar dates must not shift with the viewer's browser timezone.
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleDateString();
}
function money(value: string | number, currency = "QAR") {
  return `${currency} ${value}`;
}

export default function SalesReturnIntake({ selectedUnitId, username, csrfToken }: Props) {
  const context = JSON.stringify([selectedUnitId, username, csrfToken]);
  const contextRef = useRef(context);
  const listSequence = useRef(0);
  const detailSequence = useRef(0);
  const mutationSequence = useRef(0);
  const idempotencyKeys = useRef(new Map<string, string>());
  const [listResult, setListResult] = useState<Result<PageResponse>>({
    context, data: null, state: "loading", error: "",
  });
  const [detailResult, setDetailResult] = useState<Result<SaleDetail>>({
    context, data: null, state: "idle", error: "",
  });
  const [windowResult, setWindowResult] = useState<WindowResult>({
    context, requestDate: "", data: null, state: "idle", error: "",
  });
  const [draftState, setDraftState] = useState<{ context: string; draft: CreateDraft }>({
    context, draft: emptyDraft(),
  });
  const [inspectionState, setInspectionState] = useState<{ context: string; selectedId: number | null; draft: InspectionDraft }>({
    context, selectedId: null, draft: {},
  });
  const [page, setPage] = useState(1);
  const [reload, setReload] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [outcome, setOutcome] = useState<{ context: string; operation: "intake" | "inspection"; kind: "success" | "error"; message: string } | null>(null);

  if (contextRef.current !== context) {
    contextRef.current = context;
    listSequence.current += 1;
    detailSequence.current += 1;
    setWindowResult({ context, requestDate: "", data: null, state: "idle", error: "" });
    mutationSequence.current += 1;
    setListResult({ context, data: null, state: "loading", error: "" });
    setDetailResult({ context, data: null, state: "idle", error: "" });
    setDraftState({ context, draft: emptyDraft() });
    setInspectionState({ context, selectedId: null, draft: {} });
    setPage(1);
    setSubmitting(false);
    setOutcome(null);
  }
  const list = listResult.context === context ? listResult : { context, data: null, state: "loading" as const, error: "" };
  const detail = detailResult.context === context ? detailResult : { context, data: null, state: "idle" as const, error: "" };
  const deliveryAssessment = windowResult.context === context && windowResult.requestDate === draftState.draft.requestDate
    ? windowResult : { context, requestDate: draftState.draft.requestDate, data: null, state: "idle" as const, error: "" };
  const draft = draftState.context === context ? draftState.draft : emptyDraft();
  const selectedInspectionId = inspectionState.context === context ? inspectionState.selectedId : null;
  const inspection = inspectionState.context === context ? inspectionState.draft : {};
  const selectedReturn = list.data?.rows.find((row) => row.id === selectedInspectionId) ?? null;
  const policy = list.data?.policy ?? selectedReturn?.policy;
  const requestedMethod = draft.preference === "usually_original" ? draft.originalMethod : draft.preference;
  const setDraft = (update: Partial<CreateDraft>) => setDraftState((current) => ({
    context,
    draft: { ...(current.context === context ? current.draft : emptyDraft()), ...update },
  }));
  const updateDraftLine = (lineId: number, quantity: string) => {
    const current = draftState.context === context ? draftState.draft : emptyDraft();
    setDraftState({ context, draft: { ...current, lines: { ...current.lines, [lineId]: { quantity } } } });
  };

  useEffect(() => {
    const sequence = ++listSequence.current;
    const activeContext = context;
    setListResult({ context: activeContext, data: null, state: "loading", error: "" });
    const params = new URLSearchParams({
      business_unit_id: String(selectedUnitId), page: String(page), page_size: "10",
    });
    fetch(`/api/purchasing/returns/?${params}`, {
      method: "GET", credentials: "same-origin", headers: { Accept: "application/json" },
    }).then((response) => readJson<PageResponse>(response)).then((data) => {
      if (contextRef.current === activeContext && listSequence.current === sequence) {
        setListResult({ context: activeContext, data, state: "ready", error: "" });
      }
    }).catch((error: unknown) => {
      if (contextRef.current === activeContext && listSequence.current === sequence) {
        const status = Number((error as { status?: number })?.status);
        setListResult({
          context: activeContext, data: null, state: status === 403 || status === 404 ? "denied" : "error",
          error: error instanceof Error ? error.message : "The return register could not be loaded.",
        });
      }
    });
  }, [context, selectedUnitId, page, reload]);

  useEffect(() => {
    const quoteId = draft.quoteId.trim();
    const sequence = ++detailSequence.current;
    const activeContext = context;
    if (!/^\d+$/.test(quoteId) || Number(quoteId) <= 0) {
      setDetailResult({ context: activeContext, data: null, state: "idle", error: "" });
      return;
    }
    const existingSale = detailResult.context === activeContext && detailResult.data?.quote_id === Number(quoteId);
    if (!existingSale) setDetailResult({ context: activeContext, data: null, state: "loading", error: "" });
    setWindowResult({ context: activeContext, requestDate: draft.requestDate, data: null, state: "loading", error: "" });
    const params = new URLSearchParams({ business_unit_id: String(selectedUnitId), request_date: draft.requestDate });
    fetch(`/api/purchasing/returns/sales/${encodeURIComponent(quoteId)}/?${params}`, {
      method: "GET", credentials: "same-origin", headers: { Accept: "application/json" },
    }).then((response) => readJson<SaleDetail>(response)).then((data) => {
      if (contextRef.current === activeContext && detailSequence.current === sequence) {
        if (data.delivery_window?.request_date === draft.requestDate) {
          setDetailResult({ context: activeContext, data, state: "ready", error: "" });
          setWindowResult({ context: activeContext, requestDate: draft.requestDate, data: data.delivery_window, state: "ready", error: "" });
          if (!existingSale) setDraftState((current) => current.context === activeContext
            ? { ...current, draft: { ...current.draft, locationId: "" } }
            : current);
        } else {
          setWindowResult({ context: activeContext, requestDate: draft.requestDate, data: null, state: "error", error: "The delivery-date assessment did not match the selected request date. Reassess before continuing." });
          if (!existingSale) setDetailResult({ context: activeContext, data: null, state: "error", error: "Sale response did not include the requested delivery assessment." });
        }
      }
    }).catch((error: unknown) => {
      if (contextRef.current === activeContext && detailSequence.current === sequence) {
        const status = Number((error as { status?: number })?.status);
        const message = error instanceof Error ? error.message : "Sale detail could not be loaded.";
        setWindowResult({ context: activeContext, requestDate: draft.requestDate, data: null, state: status === 403 || status === 404 ? "denied" : "error", error: message });
        if (!existingSale) setDetailResult({
          context: activeContext, data: null, state: status === 403 || status === 404 ? "denied" : "error", error: message,
        });
      }
    });
  }, [context, selectedUnitId, draft.quoteId, draft.requestDate]);

  async function createReturn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const sale = detail.data;
    if (!sale || !draft.locationId || !requestedMethod || !draft.originalMethod || !csrfToken
      || deliveryAssessment.state !== "ready" || deliveryAssessment.data?.eligible !== true) return;
    const lines = sale.lines.map((line) => ({
      quote_line_id: line.quote_line_id,
      quantity: (draft.lines[line.quote_line_id]?.quantity ?? "").trim(),
    })).filter((line) => line.quantity !== "");
    if (!lines.length || lines.some((line) => {
      const sold = sale.lines.find((item) => item.quote_line_id === line.quote_line_id)?.quantity ?? 0;
      return !/^\d+(?:\.\d+)?$/.test(line.quantity) || Number(line.quantity) <= 0 || Number(line.quantity) > Number(sold);
    })) {
      setOutcome({ context, operation: "intake", kind: "error", message: "Enter a positive returned quantity no greater than the sold quantity for each selected line." });
      return;
    }
    if (!/^\d+(?:\.\d{1,2})?$/.test(draft.amount) || Number(draft.amount) < 0) {
      setOutcome({ context, operation: "intake", kind: "error", message: "Enter a proposed refund amount with up to two decimal places." });
      return;
    }
    const payload = {
      business_unit_id: selectedUnitId,
      quote_id: sale.quote_id,
      request_date: draft.requestDate,
      receiving_mode: draft.receivingMode,
      location_id: Number(draft.locationId),
      receipt_collection_details: draft.details.trim(),
      proposed_refund_amount: draft.amount,
      original_payment_method: draft.originalMethod,
      requested_refund_method: requestedMethod,
      refund_preference: draft.preference,
      lines,
    };
    const identity = JSON.stringify([context, payload]);
    const key = idempotencyKeys.current.get(identity) ?? requestKey();
    idempotencyKeys.current.set(identity, key);
    const sequence = ++mutationSequence.current;
    setSubmitting(true);
    setOutcome(null);
    try {
      const response = await fetch("/api/purchasing/returns/", {
        method: "POST", credentials: "same-origin",
        headers: {
          Accept: "application/json", "Content-Type": "application/json",
          "X-CSRFToken": csrfToken, "Idempotency-Key": key,
        },
        body: JSON.stringify(payload),
      });
      const created = await readJson<SalesReturn>(response);
      if (contextRef.current !== context || mutationSequence.current !== sequence) return;
      setOutcome({ context, operation: "intake", kind: "success", message: `Synthetic return request ${created.id} recorded for authorized review.` });
      setDraftState({ context, draft: emptyDraft() });
      setDetailResult({ context, data: null, state: "idle", error: "" });
      setPage(1);
      setReload((value) => value + 1);
    } catch (error) {
      if (contextRef.current === context && mutationSequence.current === sequence) {
        setOutcome({ context, operation: "intake", kind: "error", message: error instanceof Error ? error.message : "The synthetic intake could not be recorded." });
      }
    } finally {
      if (contextRef.current === context && mutationSequence.current === sequence) setSubmitting(false);
    }
  }

  async function submitInspection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedReturn || selectedReturn.status !== "awaiting_inspection"
      || selectedReturn.delivery_window?.eligible !== true || !csrfToken) return;
    const lines = selectedReturn.lines.map((line) => ({
      quote_line_id: line.quote_line_id,
      condition: inspection[line.quote_line_id]?.condition,
      inspection_notes: (inspection[line.quote_line_id]?.notes ?? "").trim(),
    }));
    if (lines.some((line) => line.condition !== "good" && line.condition !== "not_good")) return;
    const payload = { business_unit_id: selectedUnitId, lines };
    const identity = JSON.stringify([context, selectedReturn.id, payload]);
    const key = idempotencyKeys.current.get(identity) ?? requestKey();
    idempotencyKeys.current.set(identity, key);
    const sequence = ++mutationSequence.current;
    setSubmitting(true);
    setOutcome(null);
    try {
      const response = await fetch(`/api/purchasing/returns/${selectedReturn.id}/inspection/`, {
        method: "POST", credentials: "same-origin",
        headers: {
          Accept: "application/json", "Content-Type": "application/json",
          "X-CSRFToken": csrfToken, "Idempotency-Key": key,
        },
        body: JSON.stringify(payload),
      });
      const updated = await readJson<SalesReturn>(response);
      if (contextRef.current !== context || mutationSequence.current !== sequence) return;
      setOutcome({
        context, operation: "inspection", kind: "success",
        message: `Return ${updated.id} is pending authorized Finance review. No refund, credit, inventory or disposition action was taken.`,
      });
      setInspectionState({ context, selectedId: updated.id, draft: {} });
      setReload((value) => value + 1);
    } catch (error) {
      if (contextRef.current === context && mutationSequence.current === sequence) {
        setOutcome({ context, operation: "inspection", kind: "error", message: error instanceof Error ? error.message : "Inspection could not be recorded." });
      }
    } finally {
      if (contextRef.current === context && mutationSequence.current === sequence) setSubmitting(false);
    }
  }

  function selectInspection(row: SalesReturn) {
    setInspectionState({
      context, selectedId: row.id,
      draft: Object.fromEntries(row.lines.map((line) => [line.quote_line_id, {
        condition: line.condition === "uninspected" ? "" : line.condition, notes: line.inspection_notes ?? "",
      }])),
    });
  }

  return (
    <section className="pw-card pw-sales-return" data-pending-operation-count={submitting ? 1 : 0} aria-labelledby="pw-return-title">
      <div className="pw-card-heading pw-return-heading">
        <div>
          <span className="pw-kicker">SALES RETURNS · SYNTHETIC INTAKE</span>
          <h2 id="pw-return-title">Return request register</h2>
          <p>Record a non-posting request for review. Customer and item identity come from an authorized delivered sale.</p>
        </div>
        <span className="pw-readonly-mark">NON-POSTING</span>
      </div>
      <div className="pw-return-banner">
        <span className="pw-return-index">SR</span>
        <div><strong>TEST ONLY · SYNTHETIC SALES-RETURN INTAKE</strong>
          <p>No refund, credit, stock movement or accounting entry is created here. The server-provided delivery-window assessment does not approve a refund.</p></div>
        <span className="pw-status is-off">Finance review required</span>
      </div>
      <div className="pw-return-layout">
        <div className="pw-return-main">
          <section className="pw-return-block" aria-labelledby="pw-return-new-heading">
            <div className="pw-return-block-heading">
              <span className="pw-step">01</span><div><h3 id="pw-return-new-heading">New intake</h3><p>Start with the delivered quote ID. Access-denied details remain unavailable.</p></div>
            </div>
            <form className="pw-action-form" onSubmit={(event) => void createReturn(event)}>
              <label htmlFor="pw-return-quote">Delivered quote ID
                <input id="pw-return-quote" inputMode="numeric" pattern="[0-9]*" value={draft.quoteId}
                  onChange={(event) => setDraft({ quoteId: event.target.value, lines: {}, locationId: "" })}
                  placeholder="Enter an authorized quote ID" aria-describedby="pw-return-quote-help" />
              </label>
              <small id="pw-return-quote-help" className="pw-action-note">Sale details are requested only after an ID is entered and are scoped to the active business unit.</small>
              {detail.state === "loading" && !detail.data && <div className="pw-skeleton pw-return-skeleton" role="status" aria-label="Loading delivered sale"><i /><i /></div>}
              {detail.state === "denied" && <div className="pw-notice pw-error" role="alert"><strong>Sale details unavailable</strong><p>{detail.error} Intake is disabled until an authorized sale response is available.</p></div>}
              {detail.state === "error" && <div className="pw-notice pw-error" role="alert"><strong>Sale lookup could not be completed</strong><p>{detail.error}</p></div>}
              {detail.data && (detail.state === "ready" || detail.state === "loading") && <>
                <div className="pw-return-sale-summary">
                  <span>AUTHORIZED DELIVERY · QUOTE <b className="pw-mono">#{detail.data.quote_id}</b></span>
                  <strong>{detail.data.customer.name}</strong>
                  <small>Customer {detail.data.customer.id} · Delivered {formatDate(detail.data.delivery_window?.delivery_date ?? null)}</small>
                </div>
                <div className="pw-return-lines" aria-label="Returned sale items">
                  {detail.data.lines.map((line) => (
                    <div className="pw-return-line" key={line.quote_line_id}>
                      <div><span className="pw-mono">LINE {line.quote_line_id} · ITEM {line.item_id}</span>
                        <strong>{line.description}</strong><small>{line.brand} · Sold quantity {line.quantity}</small></div>
                      <label htmlFor={`pw-return-qty-${line.quote_line_id}`}>Returned quantity
                        <input id={`pw-return-qty-${line.quote_line_id}`} inputMode="decimal" value={draft.lines[line.quote_line_id]?.quantity ?? ""}
                          onChange={(event) => updateDraftLine(line.quote_line_id, event.target.value)} placeholder="0" />
                      </label>
                    </div>
                  ))}
                </div>
                <div className="pw-form-grid">
                  <label>Request date<input type="date" value={draft.requestDate} onChange={(event) => setDraft({ requestDate: event.target.value })} /></label>
                  <label>Receiving mode<select value={draft.receivingMode} onChange={(event) => setDraft({ receivingMode: event.target.value as CreateDraft["receivingMode"] })}>
                    <option value="showroom">Showroom drop-off</option><option value="warehouse_collection">Warehouse collection</option>
                  </select></label>
                  <label>Receiving location<select value={draft.locationId} onChange={(event) => setDraft({ locationId: event.target.value })} required>
                    <option value="">Select location</option>
                    {detail.data.locations.map((location) => <option key={location.id} value={location.id}>{location.code} · {location.name}</option>)}
                  </select></label>
                  <label>Proposed refund amount (QAR)<input inputMode="decimal" value={draft.amount} onChange={(event) => setDraft({ amount: event.target.value })} placeholder="0.00" required /></label>
                  <label>Original payment method <span className="pw-return-label-note">User recorded; not verified</span>
                    <select value={draft.originalMethod} onChange={(event) => setDraft({ originalMethod: event.target.value as CreateDraft["originalMethod"] })} required>
                      <option value="">Select recorded method</option><option value="cash">Cash</option><option value="card">Card</option>
                    </select>
                  </label>
                  <label>Refund preference
                    <select value={draft.preference} onChange={(event) => setDraft({ preference: event.target.value as CreateDraft["preference"] })}>
                      <option value="usually_original">Usually original method</option><option value="cash">Cash</option><option value="card">Card</option>
                    </select>
                  </label>
                  <label>Requested refund method
                    <input aria-label="Requested refund method" readOnly value={requestedMethod || "Select original method"} />
                  </label>
                  <label className="pw-return-details">Receipt / collection details
                    <textarea value={draft.details} onChange={(event) => setDraft({ details: event.target.value })} maxLength={1000} required placeholder="Record synthetic receipt or collection details" />
                  </label>
                </div>
                <div className="pw-return-rule" role="note">
                  <strong>Owner-confirmed company rule · legal validation pending</strong>
                  <p>{policy?.calendar_days ?? policy?.provisional_calendar_days ?? 14} calendar days from actual delivery · date basis: <b>{policy?.date_basis ?? "delivery"}</b>.</p>
                  <p>This company rule is not a verified statement of Qatar law. Eligibility does not approve a refund; good-condition inspection and authorized Finance review remain required.</p>
                  {deliveryAssessment.state === "loading" && <p role="status">Assessing the selected request date…</p>}
                  {deliveryAssessment.state === "error" || deliveryAssessment.state === "denied"
                    ? <p className="pw-disabled-reason" role="alert">{deliveryAssessment.error || "Delivery eligibility could not be assessed. Intake remains disabled."}</p>
                    : deliveryAssessment.state === "ready" && deliveryAssessment.data && <p className={deliveryAssessment.data.eligible ? "" : "pw-disabled-reason"} role={deliveryAssessment.data.eligible ? "status" : "alert"}>
                      Delivered {formatDate(deliveryAssessment.data.delivery_date)} · Deadline {formatDate(deliveryAssessment.data.deadline)} · {deliveryAssessment.data.eligible ? "Within delivery window." : deliveryAssessment.data.message}
                    </p>}
                </div>
                <button className="pw-button pw-button-primary" type="submit"
                  disabled={submitting || !requestedMethod || !detail.data.locations.length || !csrfToken
                    || deliveryAssessment.state !== "ready" || deliveryAssessment.data?.eligible !== true}>
                  {submitting ? "Recording intake…" : "Record synthetic intake"}
                </button>
              </>}
            </form>
          </section>
          {outcome?.context === context && <div className={`pw-notice ${outcome.kind === "error" ? "pw-error" : "pw-success"}`} role={outcome.kind === "error" ? "alert" : "status"}>
            <strong>{outcome.operation === "inspection"
              ? outcome.kind === "error" ? "Inspection not recorded" : "Synthetic inspection recorded"
              : outcome.kind === "error" ? "Intake not recorded" : "Synthetic intake recorded"}</strong><p>{outcome.message}</p>
          </div>}
          <section className="pw-return-block pw-return-register" aria-labelledby="pw-return-register-heading">
            <div className="pw-return-block-heading">
              <span className="pw-step">02</span><div><h3 id="pw-return-register-heading">Return register</h3><p>Unit-scoped requests and inspection status. Selecting an awaiting-inspection row opens its inspection form.</p></div>
              <button className="pw-button pw-button-muted" type="button" onClick={() => setReload((value) => value + 1)}>Refresh</button>
            </div>
            {list.state === "loading" && <div className="pw-skeleton" role="status" aria-label="Loading return register"><i /><i /><i /></div>}
            {list.state === "denied" && <div className="pw-empty"><span>—</span><strong>Return register unavailable</strong><p>{list.error} Intake and register states remain separate; sale details can still be requested above.</p></div>}
            {list.state === "error" && <div className="pw-notice pw-error" role="alert"><strong>Register could not be loaded</strong><p>{list.error}</p><button className="pw-button pw-button-muted" type="button" onClick={() => setReload((value) => value + 1)}>Retry register</button></div>}
            {list.state === "ready" && list.data && (list.data.rows.length ? <>
              <div className="pw-table-wrap"><table className="pw-table pw-return-table">
                <thead><tr><th scope="col">Request</th><th scope="col">Sale / customer</th><th scope="col">Requested</th><th scope="col">Proposed amount</th><th scope="col">Status</th><th scope="col">Inspection</th></tr></thead>
                <tbody>{list.data.rows.map((row) => <tr key={row.id}>
                  <td className="pw-mono">SR-{row.id}</td>
                  <td><strong>Quote #{row.quote_id}</strong><small>{row.customer.name}</small></td>
                  <td>{formatDate(row.request_date)}</td>
                  <td>{money(row.proposed_refund_amount, row.currency)}</td>
                  <td><span className={`pw-status ${row.status === "finance_pending" ? "is-off" : ""}`}>{row.status.replaceAll("_", " ")}</span></td>
                  <td><button className="pw-button pw-button-outline" type="button" onClick={() => selectInspection(row)}>{row.status === "awaiting_inspection" ? "Inspect return" : "Review record"}</button></td>
                </tr>)}</tbody>
              </table></div>
              <div className="pw-return-pagination"><span>{list.data.pagination.total_count} synthetic requests · page {list.data.pagination.page} of {Math.max(1, list.data.pagination.total_pages)}</span>
                <div><button className="pw-button pw-button-muted" type="button" disabled={list.data.pagination.previous_page === null} onClick={() => setPage(list.data!.pagination.previous_page ?? 1)}>Previous</button>
                  <button className="pw-button pw-button-muted" type="button" disabled={list.data.pagination.next_page === null} onClick={() => setPage(list.data!.pagination.next_page ?? list.data!.pagination.page)}>Next</button></div>
              </div>
            </> : <div className="pw-empty"><span>SR</span><strong>No synthetic return requests</strong><p>Authorized return requests for this business unit will appear here. No transactions are implied by an empty register.</p></div>)}
          </section>
        </div>
        <aside className="pw-return-aside" aria-label="Return controls and policy notes">
          <div className="pw-return-aside-card"><span className="pw-kicker">WORKFLOW GUARDRAILS</span>
            <h3>Intake is not an outcome.</h3>
            <ol><li>Authorized delivered sale details</li><li>Proposed returned quantities and refund preference</li><li>Every line condition inspected</li><li>Pending authorized Finance review</li></ol>
            <p>No approval, refund, credit, stock adjustment or disposition is available in this screen.</p>
          </div>
          <div className="pw-return-aside-card pw-return-policy-card">
            <span className="pw-kicker">DATE-BASIS STATUS</span><strong>{policy?.date_basis ?? "delivery"}</strong>
            <p>{policy?.calendar_days ?? policy?.provisional_calendar_days ?? 14} configurable calendar days counted from actual delivery. Legal validation is pending; this is not a verified statement of Qatar law.</p>
            <span className="pw-status is-off">Finance review still required</span>
          </div>
        </aside>
      </div>
      {selectedReturn && <div className="pw-return-inspection-overlay">
        <section className="pw-return-inspection" aria-labelledby="pw-inspection-title">
          <div className="pw-return-block-heading"><span className="pw-step">03</span><div>
            <h3 id="pw-inspection-title">Inspect return SR-{selectedReturn.id}</h3>
            <p>{selectedReturn.customer.name} · Quote #{selectedReturn.quote_id} · Inspection records condition only.</p>
          </div><button className="pw-button pw-button-muted" type="button" onClick={() => setInspectionState({ context, selectedId: null, draft: {} })}>Close</button></div>
          {selectedReturn.delivery_window && <div className={`pw-return-rule ${selectedReturn.delivery_window.eligible === true ? "" : "pw-disabled-reason"}`} role="note">
            <strong>Stored delivery-window assessment</strong>
            <p>Delivered {selectedReturn.delivery_window.delivery_date ? formatDate(selectedReturn.delivery_window.delivery_date) : "not recorded"} · Deadline {selectedReturn.delivery_window.deadline ? formatDate(selectedReturn.delivery_window.deadline) : "not recorded"} · Requested {selectedReturn.delivery_window.request_date ? formatDate(selectedReturn.delivery_window.request_date) : formatDate(selectedReturn.request_date)} · {selectedReturn.delivery_window.eligible === true ? "Eligible under stored assessment." : selectedReturn.delivery_window.message || "Eligibility unavailable; record is fail-closed."}</p>
          </div>}
          {selectedReturn.status === "finance_pending" && <div className="pw-notice pw-success"><strong>Pending authorized Finance review</strong><p>No refund, credit, disposition or stock action is authorized or performed here.</p></div>}
          {selectedReturn.status === "awaiting_inspection" && selectedReturn.delivery_window?.eligible !== true && <div className="pw-disabled-reason" role="alert">Inspection is unavailable because this stored return is not confirmed eligible. Eligibility alone does not approve a refund; inspection and Finance review remain required.</div>}
          <form className="pw-action-form" onSubmit={(event) => void submitInspection(event)}>
            {selectedReturn.lines.map((line) => <div className="pw-return-inspection-line" key={line.quote_line_id}>
              <div><span className="pw-mono">LINE {line.quote_line_id}</span><strong>{line.description}</strong><small>{line.brand} · Returned quantity {line.quantity}</small></div>
              {selectedReturn.status === "awaiting_inspection" && selectedReturn.delivery_window?.eligible === true ? <>
                <label>Condition for line {line.quote_line_id}<select aria-label={`Condition for line ${line.quote_line_id}`}
                  required value={inspection[line.quote_line_id]?.condition ?? ""}
                  onChange={(event) => setInspectionState((current) => ({
                    context, selectedId: selectedReturn.id,
                    draft: { ...(current.context === context ? current.draft : {}), [line.quote_line_id]: {
                      condition: event.target.value as "" | "good" | "not_good",
                      notes: current.context === context ? current.draft[line.quote_line_id]?.notes ?? "" : "",
                    } },
                  }))}>
                  <option value="">Select inspection condition</option><option value="good">Good</option><option value="not_good">Not good</option>
                </select></label>
                <label>Inspection notes for line {line.quote_line_id}<textarea value={inspection[line.quote_line_id]?.notes ?? ""}
                  onChange={(event) => setInspectionState((current) => ({
                    context, selectedId: selectedReturn.id,
                    draft: { ...(current.context === context ? current.draft : {}), [line.quote_line_id]: {
                      condition: current.context === context ? current.draft[line.quote_line_id]?.condition ?? "" : "",
                      notes: event.target.value,
                    } },
                  }))} placeholder="Record observations; no disposition is inferred" /></label>
                {inspection[line.quote_line_id]?.condition === "not_good" && <p className="pw-disabled-reason">Not good is an inspection finding only. No exclusion, disposition or stock decision is inferred.</p>}
              </> : <div className="pw-return-condition"><span>Recorded condition</span><strong>{line.condition.replaceAll("_", " ")}</strong>
                {line.inspection_notes && <small>{line.inspection_notes}</small>}</div>}
            </div>)}
            {selectedReturn.status === "awaiting_inspection" && selectedReturn.delivery_window?.eligible === true && <>
              <p className="pw-action-note">Every returned line must have a condition recorded before inspection can be submitted.</p>
              <button className="pw-button pw-button-primary" type="submit" disabled={submitting || selectedReturn.lines.some((line) => !["good", "not_good"].includes(inspection[line.quote_line_id]?.condition ?? ""))}>
                {submitting ? "Saving inspection…" : "Submit inspection for Finance review"}
              </button>
            </>}
          </form>
        </section>
      </div>}
    </section>
  );
}
