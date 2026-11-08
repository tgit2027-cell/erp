import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";
import "./staff-loans.css";
import "./factory.css";
import "./factory-planning.css";
import { DashboardView, PlanningView } from "./FactoryPlanning";
import { LearningView, MachinesView, TeamView } from "./FactoryOps";

type Hours = Record<string, string>;
type Purchase = {
  id: number; description: string; quantity: string; estimated_cost: string; supplier_name: string; reason: string;
  status: "requested" | "approved" | "bought" | "rejected" | "cancelled"; status_label: string; requested_by: string;
  decided_by: string; decision_note: string; bought_by: string; bought_on: string; actual_cost: string; paid_from: string;
  receipt_reference: string; can_decide: boolean; can_buy: boolean; can_cancel: boolean;
};
type Slip = {
  id: number; number: string; status: "manager" | "warehouse" | "approved" | "delivered" | "rejected"; status_label: string;
  job: string; project: string; store: string; deliver_to: string; note: string; rejection_reason: string;
  lines: { sku: string; name: string; unit: string; quantity: string; beyond_plan: boolean }[];
  approvals: { step: string; name: string; at: string }[];
  transfer_id: number | null; transfer_status: string; can_decide: boolean; can_print: boolean; printed_count: number;
};
type PrintedSlip = Slip & { company: string; unit: string; printed_at: string; printed_by: string };
type StockCheck = { variant_id: number; sku: string; name: string; unit: string; needed: string; free: string; other_jobs: string; to_order: string; short: boolean };
type ToOrder = { variant_id: number; sku: string; name: string; unit: string; needed: string; free: string; to_order: string; jobs: { job: string; job_id: number; due_date: string; short: string }[] };
type Job = {
  id: number; number: string; status: string; status_label: string; purpose: string; purpose_label: string;
  quote: { id: number; customer: string } | null; project_name: string; design_reference: string;
  product: { variant_id: number; sku: string; name: string; quantity: string } | null;
  due_date: string; late: boolean; destination: string; destination_label: string;
  finish_location: { id: number; code: string; name: string } | null; note: string; decision_note: string;
  boq: BoqRow[];
  planned_hours: Hours;
  materials: { variant_id: number; sku: string; name: string; quantity: string; unit_cost: string; taken: string }[];
  estimate: { materials: string; labour: string; overhead: string; total: string; limit: string; overhead_percent: string; overrun_percent: string };
  actual: { materials: string; purchases: string; labour: string; overhead: string; total: string };
  issues: { id: number; sku: string; name: string; location: string; quantity: string; value: string; beyond_plan: boolean; status: string; status_label: string; note: string; requested_by: string; decided_by: string; can_decide: boolean; deliver_to: string; request_id: number | null; can_receive: boolean }[];
  material_requests: Slip[];
  short_materials?: { sku: string; name: string; short: string }[];
  labour: { id: number; line: string; workshop_label: string; employee: string; work_date: string; hours: string; overtime: boolean; rate: string; cost: string }[];
  purchases: Purchase[];
  created_by: string; approved_by: string; finished_by: string; cost_approved_by: string; received_by: string; received_on: string;
  can_edit: boolean; can_submit: boolean; can_approve: boolean; can_work: boolean; can_finish: boolean; can_approve_cost: boolean;
  can_send: boolean; can_receive: boolean; can_cancel: boolean;
};
type RawMaterial = {
  variant_id: number; code: string; item_code: string; description: string; size: string; category: string; category_label: string;
  unit: string; unit_price: string; list_price: string; list_currency: string; supplier: string; on_hand: string; free: string;
  places: { location_id: number; location: string; on_hand: string; free: string }[];
};
type Option = { id: string; label: string };
type Overview = {
  unit: { id: number; name: string }; today: string;
  settings: { is_live: boolean; overhead_percent: string; overrun_percent: string; overtime_percent: string; rates: Hours; asset_threshold: string; machine_life_months: number; order_lead_days: number; stages_per_person: number; updated_by: string; updated_at: string };
  workshops: Option[]; purposes: Option[]; categories: Option[];
  tiles: { in_production: number; waiting: number; late: number; to_site: number; work_in_progress: string; to_order: number };
  orders: Job[]; raw_materials: RawMaterial[]; to_order: ToOrder[];
  items: { variant_id: number; sku: string; name: string }[];
  sales_orders: { id: number; customer: string; status_label: string; total: string }[];
  people: { id: number; name: string; job_title: string; department: string; section: string; section_label: string }[];
  accounts: { id: number; name: string; kind_label: string }[];
  locations: { id: number; code: string; name: string; kind: string }[];
  can_run: boolean; can_approve: boolean; can_approve_cost: boolean; can_buy: boolean; can_keep_list: boolean; is_master_admin: boolean;
  policy: string;
};
export type FactoryView = "main" | "planning" | "dashboard" | "team" | "machines" | "learning" | "materials" | "setup";

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The request could not be completed.");
  return data as T;
}
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const qar = (value: string | number) => Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = (value: string) => Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 });
const headers = (csrfToken: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken });
const TONE: Record<string, string> = {
  draft: "done", waiting: "waiting", in_progress: "approved", cost_check: "waiting", stored: "approved", on_the_way: "approved",
  delivered: "done", in_stock: "done", cancelled: "done", requested: "waiting", approved: "approved", bought: "done", rejected: "rejected", issued: "done", warehouse: "waiting", manager: "waiting",
};
const errorText = (caught: unknown, fallback: string) => (caught instanceof Error ? show(caught.message) : fallback);

function useAction(csrfToken: string, unitId: number, onDone: (message: string) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const send = async <T,>(path: string, method: string, body: unknown, message: (result: T) => string) => {
    setBusy(true); setError("");
    try {
      const result = await call<T>(`${path}?unit=${unitId}`, { method, headers: headers(csrfToken), body: JSON.stringify(body) });
      onDone(message(result));
      return true;
    } catch (caught) {
      setError(errorText(caught, "That could not be done."));
      return false;
    } finally { setBusy(false); }
  };
  return { busy, error, send };
}

type BoqRow = { id?: number; materials?: { variant_id: number; sku: string; quantity: string }[]; code: string; description: string; quantity: string; unit: string; details?: Record<string, string>; area?: string; floor?: string; zone?: string; tags?: (number | string)[] };
type BoqUpload = { file_name: string; lines: BoqRow[]; sheets: { name: string; items: number }[]; problems: string[]; more_problems: number;
  materials: { variant_id: number; sku: string; name: string; quantity: string }[]; stock_check: StockCheck[] };
const detailText = (details?: Record<string, string>) => Object.entries(details ?? {}).map(([heading, text]) => `${heading}: ${show(text)}`).join(" · ");
type MaterialRow = { variant_id: string; quantity: string };

function JobForm({ overview, csrfToken, onDone, job, onClose }: { overview: Overview; csrfToken: string; onDone: (message: string) => void; job?: Job; onClose?: () => void }) {
  const [purpose, setPurpose] = useState(job?.purpose ?? "fitout");
  const [quoteId, setQuoteId] = useState(job?.quote ? String(job.quote.id) : "");
  const [projectName, setProjectName] = useState(job?.project_name ?? "");
  const [designRef, setDesignRef] = useState(job?.design_reference ?? "");
  const [variantId, setVariantId] = useState(job?.product ? String(job.product.variant_id) : "");
  const [quantity, setQuantity] = useState(job?.product?.quantity ?? "1");
  const [due, setDue] = useState(job?.due_date ?? "");
  const [note, setNote] = useState(job?.note ?? "");
  const [boq, setBoq] = useState<BoqRow[]>(job?.boq.length ? job.boq : [{ code: "JD-00", description: "", quantity: "1", unit: "piece" }]);
  const [materials, setMaterials] = useState<MaterialRow[]>(job?.materials.map((line) => ({ variant_id: String(line.variant_id), quantity: line.quantity })) ?? []);
  const [hours, setHours] = useState<Hours>(job?.planned_hours ?? {});
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  const [upload, setUpload] = useState<{ busy: boolean; error: string; result: BoqUpload | null }>({ busy: false, error: "", result: null });
  const readSheet = async (file: File | undefined) => {
    if (!file) return;
    setUpload({ busy: true, error: "", result: null });
    const form = new FormData();
    form.append("file", file);
    try {
      const result = await call<BoqUpload>(`/api/factory/boq-upload/?unit=${overview.unit.id}`, { method: "POST", headers: { "X-CSRFToken": csrfToken }, body: form });
      setBoq(result.lines);
      if (result.materials.length) setMaterials(result.materials.map((row) => ({ variant_id: String(row.variant_id), quantity: row.quantity })));
      setUpload({ busy: false, error: "", result });
    } catch (caught) {
      setUpload({ busy: false, error: errorText(caught, "The BOQ could not be read."), result: null });
    }
  };
  const showroom = purpose === "showroom";
  const nextCode = () => `JD-${String(boq.length).padStart(2, "0")}`;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const body = {
      purpose, due_date: due, note, project_name: projectName, design_reference: designRef,
      ...(showroom ? { variant_id: Number(variantId), quantity } : { quote_id: Number(quoteId) }),
      boq: boq.filter((row) => row.description.trim()),
      materials: materials.filter((row) => row.variant_id && row.quantity).map((row) => ({ variant_id: Number(row.variant_id), quantity: row.quantity })),
      planned_hours: hours,
    };
    const ok = await send<Job>(job ? `/api/factory/jobs/${job.id}/` : "/api/factory/jobs/", job ? "PUT" : "POST", body,
      (row) => job ? `${row.number} is updated.` : `${row.number} is opened. Send it to the Factory Manager when the materials and hours are listed.`);
    if (ok) onClose?.();
  };
  const materialChoices = [...overview.raw_materials.map((row) => ({ id: row.variant_id, label: `${row.code} · ${show(row.description)}${row.size ? ` · ${row.size}` : ""}` })),
    ...overview.items.map((row) => ({ id: row.variant_id, label: `${row.sku} · ${show(row.name)}` }))];
  return <form className="sp-form fac-form" onSubmit={(event) => void submit(event)} aria-label={job ? `Change ${job.number}` : "Open a job order"}>
    <h3>{job ? `Change ${job.number}` : "Open a job order"}</h3>
    <div className="sp-fields fac-grid">
      <label>For<select value={purpose} onChange={(event) => setPurpose(event.target.value)}>
        {overview.purposes.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
      </select></label>
      {showroom ? <>
        <label>Piece to make<select value={variantId} onChange={(event) => setVariantId(event.target.value)} required>
          <option value="">Choose…</option>
          {overview.items.map((row) => <option key={row.variant_id} value={row.variant_id}>{row.sku} · {show(row.name)}</option>)}
        </select></label>
        <label>How many<input value={quantity} inputMode="decimal" onChange={(event) => setQuantity(event.target.value)} required /></label>
      </> : <label className="fac-wide">Signed sales order<select value={quoteId} onChange={(event) => setQuoteId(event.target.value)} required>
        <option value="">{overview.sales_orders.length ? "Choose…" : "No approved and paid sales orders in this unit"}</option>
        {overview.sales_orders.map((row) => <option key={row.id} value={row.id}>Order {row.id} · {show(row.customer)} · QAR {qar(row.total)} · {row.status_label}</option>)}
      </select></label>}
      <label>Project name<input value={projectName} maxLength={120} onChange={(event) => setProjectName(event.target.value)} placeholder="e.g. Villa 12 fit-out" /></label>
      <label>Design package<input value={designRef} maxLength={120} onChange={(event) => setDesignRef(event.target.value)} placeholder="e.g. DP-12 rev B" /></label>
      <label>Needed by<input type="date" value={due} min={overview.today} onChange={(event) => setDue(event.target.value)} required /></label>
      <label className="fac-wide">Note<input value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} /></label>
    </div>
    <fieldset className="fac-lines"><legend>Works (BOQ items){showroom ? " · optional" : ""}</legend>
      <div className="fac-upload">
        <label className="button button-outline fac-file">{upload.busy ? "Reading the BOQ…" : "Upload the BOQ from Excel"}
          <input type="file" accept=".xlsx,.xlsm" aria-label="BOQ Excel file" disabled={upload.busy} onChange={(event) => { void readSheet(event.target.files?.[0]); event.target.value = ""; }} /></label>
        <span>Each JD-00, JD-01… row is read with its description, quantity and unit. Other columns (size, finish, area, remarks) are kept with the item. A sheet named "Materials" (JD, Material, Qty, Unit) fills each JD's materials and is checked against stock. Uploading replaces the lists below.</span>
      </div>
      {upload.error && <div className="alert" role="alert">{upload.error}</div>}
      {upload.result && <p className="fac-uploaded" role="status">Read {upload.result.lines.length} BOQ items from {upload.result.file_name} ({upload.result.sheets.map((sheet) => `${sheet.name}: ${sheet.items}`).join(", ")}).
        {upload.result.problems.length > 0 && <> {upload.result.problems.length + upload.result.more_problems} rows were skipped. Fix them in Excel and upload again, or add them by hand:</>}</p>}
      {upload.result && upload.result.problems.length > 0 && <ul className="fac-problems">{upload.result.problems.map((text) => <li key={text}>{show(text)}</li>)}</ul>}
      {upload.result && upload.result.stock_check.length > 0 && <StockCheckTable rows={upload.result.stock_check} />}
      <div className={boq.length > 12 ? "fac-boq-scroll" : undefined}>
      {boq.map((row, index) => <div className="fac-line" key={index}>
        <input aria-label={`BOQ code ${index + 1}`} value={row.code} onChange={(event) => setBoq(boq.map((line, at) => at === index ? { ...line, code: event.target.value.toUpperCase() } : line))} className="fac-code" />
        <input aria-label={`BOQ description ${index + 1}`} value={row.description} placeholder="e.g. Oak veneer door 90 x 210" onChange={(event) => setBoq(boq.map((line, at) => at === index ? { ...line, description: event.target.value } : line))} />
        <input aria-label={`BOQ quantity ${index + 1}`} value={row.quantity} inputMode="decimal" className="fac-qty" onChange={(event) => setBoq(boq.map((line, at) => at === index ? { ...line, quantity: event.target.value } : line))} />
        <input aria-label={`BOQ unit ${index + 1}`} value={row.unit} className="fac-unit" onChange={(event) => setBoq(boq.map((line, at) => at === index ? { ...line, unit: event.target.value } : line))} />
        <button type="button" className="button button-quiet" onClick={() => setBoq(boq.filter((_, at) => at !== index))} aria-label={`Remove ${row.code}`}>Remove</button>
        {((row.details && Object.keys(row.details).length > 0) || (row.materials && row.materials.length > 0)) && <small className="fac-line-details">{[detailText(row.details), row.materials?.length ? `Materials: ${row.materials.map((item) => `${num(item.quantity)} × ${item.sku}`).join(", ")}` : ""].filter(Boolean).join(" · ")}</small>}
      </div>)}
      </div>
      <button type="button" className="button button-outline" onClick={() => setBoq([...boq, { code: nextCode(), description: "", quantity: "1", unit: "piece" }])}>Add a BOQ item</button>
    </fieldset>
    <fieldset className="fac-lines"><legend>Materials needed (from the production drawings)</legend>
      {materials.map((row, index) => <div className="fac-line" key={index}>
        <select aria-label={`Material ${index + 1}`} value={row.variant_id} onChange={(event) => setMaterials(materials.map((line, at) => at === index ? { ...line, variant_id: event.target.value } : line))}>
          <option value="">Choose…</option>
          {materialChoices.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
        </select>
        <input aria-label={`Material quantity ${index + 1}`} value={row.quantity} inputMode="decimal" className="fac-qty" onChange={(event) => setMaterials(materials.map((line, at) => at === index ? { ...line, quantity: event.target.value } : line))} />
        <button type="button" className="button button-quiet" onClick={() => setMaterials(materials.filter((_, at) => at !== index))}>Remove</button>
      </div>)}
      <button type="button" className="button button-outline" onClick={() => setMaterials([...materials, { variant_id: "", quantity: "1" }])}>Add a material</button>
    </fieldset>
    <fieldset className="fac-lines"><legend>Workshop hours planned</legend>
      <div className="sp-fields fac-hours">{overview.workshops.map((workshop) => <label key={workshop.id}>{workshop.label}
        <input value={hours[workshop.id] ?? ""} inputMode="decimal" placeholder="0" onChange={(event) => setHours({ ...hours, [workshop.id]: event.target.value })} /></label>)}</div>
    </fieldset>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy}>{job ? "Save changes" : "Open the job"}</button>
      {onClose && <button className="button button-quiet" type="button" onClick={onClose}>Back</button>}
    </div>
  </form>;
}

function StockCheckTable({ rows }: { rows: StockCheck[] }) {
  const short = rows.filter((row) => row.short);
  return <div className="fac-stock-check">
    <p className={short.length ? "fac-alert-line" : "fac-ok-line"} role="status">{short.length
      ? `${short.length} of ${rows.length} materials are not in stock in full. Order them before production starts:`
      : `All ${rows.length} materials in the BOQ are in stock, after what other open jobs still need.`}</p>
    <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label="BOQ materials against stock">
      <thead><tr><th scope="col">Material</th><th scope="col" className="bc-num">BOQ needs</th><th scope="col" className="bc-num">Free in stock</th><th scope="col" className="bc-num">Other open jobs</th><th scope="col" className="bc-num">To order</th></tr></thead>
      <tbody>{rows.map((row) => <tr key={row.variant_id} className={row.short ? "fac-short" : undefined}><th scope="row">{row.sku}<small>{show(row.name)}</small></th>
        <td className="bc-num">{num(row.needed)}</td><td className="bc-num">{num(row.free)}</td><td className="bc-num">{num(row.other_jobs)}</td>
        <td className="bc-num">{row.short ? <strong>{num(row.to_order)} {row.unit.toLowerCase()}</strong> : "–"}</td></tr>)}</tbody>
    </table></div>
  </div>;
}

function SlipCard({ slip, overview, csrfToken, onDone, onPrint }: { slip: Slip; overview: Overview; csrfToken: string; onDone: (message: string) => void; onPrint: (slip: PrintedSlip) => void }) {
  const [reason, setReason] = useState("");
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  const [printError, setPrintError] = useState("");
  const print = async () => {
    setPrintError("");
    try {
      onPrint(await call<PrintedSlip>(`/api/factory/material-requests/${slip.id}/?unit=${overview.unit.id}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ action: "print" }) }));
    } catch (caught) { setPrintError(errorText(caught, "The slip could not be printed.")); }
  };
  return <li className="fac-slip">
    <div className="fac-slip-head"><strong className="pw-mono">{slip.number}</strong> <span className={`sp-status sp-status-${TONE[slip.status] ?? "waiting"}`}>{slip.status_label}</span>
      <small>{slip.store} → {slip.deliver_to}{slip.printed_count > 0 && ` · printed ${slip.printed_count}×`}</small></div>
    <ul className="fac-slip-lines">{slip.lines.map((line) => <li key={line.sku}>{num(line.quantity)} {line.unit.toLowerCase()} × <span className="pw-mono">{line.sku}</span> {show(line.name)}{line.beyond_plan && <em> · more than the job listed</em>}</li>)}</ul>
    {slip.note && <small>Note: {show(slip.note)}</small>}
    <ol className="fac-approvals">{slip.approvals.map((step) => <li key={step.step} className={step.name ? "is-done" : undefined}>{step.step}: {step.name ? <>{show(step.name)} <small>{new Date(step.at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}</small></> : <span className="fp-muted">waiting</span>}</li>)}</ol>
    {slip.rejection_reason && <small className="fac-late">Rejected · {show(slip.rejection_reason)}</small>}
    {slip.status === "warehouse" && <small>The warehouse admin approves it on Inventory › Stock transfer requests.</small>}
    <span className="lv-actions">
      {slip.can_decide && <>
        <button className="button button-dark" type="button" disabled={busy} onClick={() => void send<Job>(`/api/factory/material-requests/${slip.id}/`, "POST", { action: "approve" }, () => `${slip.number} is approved and sent to the warehouse admin.`)}>Approve</button>
        <input aria-label={`Reason to reject ${slip.number}`} value={reason} placeholder="Reason, to reject" onChange={(event) => setReason(event.target.value)} />
        <button className="button button-outline" type="button" disabled={busy || !reason.trim()} onClick={() => void send<Job>(`/api/factory/material-requests/${slip.id}/`, "POST", { action: "reject", note: reason }, () => `${slip.number} is rejected.`)}>Reject</button>
      </>}
      {slip.can_print && <button className="button button-outline" type="button" onClick={() => void print()}>Print the slip</button>}
    </span>
    {(error || printError) && <div className="alert" role="alert">{error || printError}</div>}
  </li>;
}

/** The printable material request: what is asked for, and every approval, for the warehouse team and the file. */
function PrintSlip({ slip, onClose }: { slip: PrintedSlip; onClose: () => void }) {
  useEffect(() => {
    const timer = window.setTimeout(() => window.print(), 300);
    return () => window.clearTimeout(timer);
  }, []);
  const when = (value: string) => value ? new Date(value).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "";
  return <div className="fac-print-backdrop" role="dialog" aria-modal="true" aria-label={`Material request ${slip.number}`}>
    <article className="fac-print">
      <header><div><strong>{show(slip.company)}</strong><span>{show(slip.unit)} · Factory</span></div>
        <div className="fac-print-title"><h2>Material request</h2><span className="pw-mono">{slip.number}</span></div></header>
      <dl className="fac-print-meta">
        <div><dt>Job order</dt><dd>{slip.job}</dd></div><div><dt>Project</dt><dd>{show(slip.project) || "–"}</dd></div>
        <div><dt>From</dt><dd>{show(slip.store)}</dd></div><div><dt>Deliver to</dt><dd>{show(slip.deliver_to)}</dd></div>
      </dl>
      <table><thead><tr><th>#</th><th>Code</th><th>Material</th><th>Unit</th><th className="bc-num">Quantity</th><th>Issued</th></tr></thead>
        <tbody>{slip.lines.map((line, index) => <tr key={line.sku}><td>{index + 1}</td><td className="pw-mono">{line.sku}</td><td>{show(line.name)}{line.beyond_plan && " *"}</td><td>{line.unit}</td><td className="bc-num">{num(line.quantity)}</td><td className="fac-print-blank" /></tr>)}</tbody></table>
      {slip.lines.some((line) => line.beyond_plan) && <p className="fac-print-note">* More than the job listed{slip.note && `: ${show(slip.note)}`}</p>}
      <section className="fac-print-signs">{slip.approvals.map((step) => <div key={step.step}><span>{step.step}</span><strong>{show(step.name)}</strong><small>{when(step.at)}</small><i>Signature</i></div>)}
        <div><span>Handed over by (warehouse)</span><strong>&nbsp;</strong><small>Date</small><i>Signature</i></div>
        <div><span>Received by (factory)</span><strong>&nbsp;</strong><small>Date</small><i>Signature</i></div></section>
      <footer>Printed {when(slip.printed_at)} by {show(slip.printed_by)} · copy {slip.printed_count} · file with the job order {slip.job}</footer>
    </article>
    <div className="fac-print-tools"><button className="button button-dark" type="button" onClick={() => window.print()}>Print</button><button className="button button-quiet" type="button" onClick={onClose}>Close</button></div>
  </div>;
}

function CostTable({ job }: { job: Job }) {
  const estimated = Number(job.estimate.total) > 0;
  return <table className="sp-table bc-table fac-cost" aria-label={`${job.number} cost`}>
    <thead><tr><th scope="col">Cost</th><th scope="col" className="bc-num">Estimate</th><th scope="col" className="bc-num">So far</th></tr></thead>
    <tbody>
      <tr><th scope="row">Materials from the warehouse</th><td className="bc-num">{estimated ? qar(job.estimate.materials) : "–"}</td><td className="bc-num">{qar(job.actual.materials)}</td></tr>
      <tr><th scope="row">Special purchases</th><td className="bc-num">–</td><td className="bc-num">{qar(job.actual.purchases)}</td></tr>
      <tr><th scope="row">Labour</th><td className="bc-num">{estimated ? qar(job.estimate.labour) : "–"}</td><td className="bc-num">{qar(job.actual.labour)}</td></tr>
      <tr><th scope="row">Factory overhead{estimated ? ` (${num(job.estimate.overhead_percent)}%)` : ""}</th><td className="bc-num">{estimated ? qar(job.estimate.overhead) : "–"}</td><td className="bc-num">{qar(job.actual.overhead)}</td></tr>
      <tr className="fac-total"><th scope="row">Total</th><td className="bc-num">{estimated ? qar(job.estimate.total) : "–"}</td><td className="bc-num">{qar(job.actual.total)}</td></tr>
    </tbody>
    {estimated && <tfoot><tr><td colSpan={3}>Above QAR {qar(job.estimate.limit)} ({num(job.estimate.overrun_percent)}% over the estimate) Finance must approve the cost before it leaves production.</td></tr></tfoot>}
  </table>;
}

function JobDetail({ job, overview, csrfToken, onDone }: { job: Job; overview: Overview; csrfToken: string; onDone: (message: string) => void }) {
  const [asking, setAsking] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const factoryPlaces = overview.locations.filter((location) => location.kind === "factory");
  const defaultFactory = String((factoryPlaces.find((location) => location.code.endsWith("-FAC")) ?? factoryPlaces[0])?.id ?? "");
  const blankTake = { variant_id: "", location_id: "", deliver_to_id: defaultFactory, quantity: "", note: "" };
  const [take, setTake] = useState(blankTake);
  const [work, setWork] = useState({ workshop: overview.workshops[0]?.id ?? "joinery", employee_id: "", line_id: "", work_date: overview.today, hours: "", overtime: false });
  const [buyRequest, setBuyRequest] = useState({ description: "", quantity: "1", estimated_cost: "", supplier_name: "", reason: "" });
  const [finish, setFinish] = useState({ destination: job.purpose === "showroom" ? "warehouse" : "", location_id: "" });
  const [received, setReceived] = useState(overview.today);
  const [printing, setPrinting] = useState<PrintedSlip | null>(null);
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  const act = (action: string, extra: Record<string, unknown> = {}, message = "") =>
    void send<Job>(`/api/factory/jobs/${job.id}/`, "POST", { action, note, ...extra }, (row) => message || `${row.number}: ${row.status_label.toLowerCase()}.`).then((ok) => ok && setAsking(null));
  const material = overview.raw_materials.find((row) => String(row.variant_id) === take.variant_id);
  const planned = job.materials.find((row) => String(row.variant_id) === take.variant_id);
  const beyond = take.variant_id !== "" && take.quantity !== "" && (!planned || Number(planned.taken) + Number(take.quantity) > Number(planned.quantity));
  const takeChoices = [...job.materials.map((row) => ({ id: row.variant_id, label: `${row.sku} · ${show(row.name)} (listed ${num(row.quantity)}, taken ${num(row.taken)})` })),
    ...overview.raw_materials.filter((row) => !job.materials.some((line) => line.variant_id === row.variant_id)).map((row) => ({ id: row.variant_id, label: `${row.code} · ${show(row.description)} (not on the job's list)` }))];
  const kindOf = (id: number | string) => overview.locations.find((location) => String(location.id) === String(id))?.kind;
  const usable = overview.locations.filter((location) => location.kind === "warehouse" || location.kind === "factory");
  const places = material?.places.length ? material.places.filter((place) => kindOf(place.location_id) === "warehouse" || kindOf(place.location_id) === "factory")
    .map((place) => ({ id: place.location_id, label: `${place.location} · ${num(place.free)} free${kindOf(place.location_id) === "factory" ? " (already at the factory)" : ""}` }))
    : usable.map((location) => ({ id: location.id, label: `${location.code} · ${show(location.name)}` }));
  const fromFactory = take.location_id !== "" && kindOf(take.location_id) === "factory";
  const stillNeeded = job.materials.filter((row) => Number(row.quantity) > Number(row.taken));
  const store = overview.locations.find((location) => location.kind === "warehouse" && /raw materials/i.test(location.name)) ?? overview.locations.find((location) => location.kind === "warehouse");
  if (asking === "edit") return <JobForm overview={overview} csrfToken={csrfToken} onDone={onDone} job={job} onClose={() => setAsking(null)} />;
  const block = (title: string, children: ReactNode) => <section className="fac-block"><h4>{title}</h4>{children}</section>;
  return <div className="fac-detail">
    <p className="fac-meta">
      {job.quote ? <>Sales order {job.quote.id} · {show(job.quote.customer)}</> : job.product ? <>{num(job.product.quantity)} × {job.product.sku} {show(job.product.name)}</> : null}
      {job.project_name && <> · {show(job.project_name)}</>}{job.design_reference && <> · design package {show(job.design_reference)}</>}
      <> · opened by {job.created_by}</>{job.approved_by && <>, approved by {job.approved_by}</>}{job.finished_by && <>, finished by {job.finished_by}</>}
      {job.cost_approved_by && <>, cost approved by {job.cost_approved_by}</>}{job.received_by && <>, received at site {job.received_on}</>}
      {job.finish_location && <> · {job.status === "on_the_way" || job.status === "delivered" ? `held at ${show(job.finish_location.name)} (${job.finish_location.code})` : `stored at ${job.finish_location.code}`}</>}
      {job.note && <> · {show(job.note)}</>}{job.decision_note && <> · <strong>{show(job.decision_note)}</strong></>}
    </p>
    <div className="fac-columns">
      {block(`Works (BOQ)${job.boq.length > 1 ? ` · ${job.boq.length} items` : ""}`, job.boq.length ? <ul className={`fac-list${job.boq.length > 12 ? " fac-boq-scroll" : ""}`}>{job.boq.map((line) => <li key={line.code}><span className="pw-mono">{line.code}</span> {show(line.description)} · {num(line.quantity)} {line.unit}
        {line.details && Object.keys(line.details).length > 0 && <small>{detailText(line.details)}</small>}
        {line.materials && line.materials.length > 0 && <small>Materials: {line.materials.map((item) => `${num(item.quantity)} × ${item.sku}`).join(", ")}</small>}</li>)}</ul> : <p className="sp-empty">No BOQ items.</p>)}
      {block("Cost", <CostTable job={job} />)}
    </div>
    {printing && <PrintSlip slip={printing} onClose={() => setPrinting(null)} />}
    {job.short_materials && job.short_materials.length > 0 && <p className="fac-alert-line" role="alert">Not enough in stock for this job: {job.short_materials.map((row) => `${num(row.short)} × ${row.sku}`).join(", ")}. Order them (see Raw materials › To order).</p>}
    {block("Materials", <>
      {job.materials.length > 0 && <table className="sp-table bc-table" aria-label={`${job.number} materials`}><thead><tr><th scope="col">Material</th><th scope="col" className="bc-num">Listed</th><th scope="col" className="bc-num">Taken</th><th scope="col" className="bc-num">Unit cost</th></tr></thead>
        <tbody>{job.materials.map((line) => <tr key={line.variant_id}><th scope="row">{line.sku}<small>{show(line.name)}</small></th><td className="bc-num">{num(line.quantity)}</td><td className="bc-num">{num(line.taken)}</td><td className="bc-num">{qar(line.unit_cost)}</td></tr>)}</tbody></table>}
      {job.material_requests.length > 0 && <ul className="fac-list fac-slips" aria-label={`${job.number} material requests`}>{job.material_requests.map((slip) => <SlipCard key={`${slip.id}-${slip.status}`} slip={slip} overview={overview} csrfToken={csrfToken} onDone={onDone} onPrint={setPrinting} />)}</ul>}
      {job.issues.length > 0 && <ul className="fac-list">{job.issues.map((issue) => <li key={issue.id}>
        <span className={`sp-status sp-status-${TONE[issue.status]}`}>{issue.status_label}</span> {num(issue.quantity)} × {issue.sku} from {issue.location}{issue.deliver_to && <> to {issue.deliver_to}</>}{issue.status === "issued" && <> · QAR {qar(issue.value)}</>}
        {issue.beyond_plan && " · beyond the list"}{issue.note && ` · ${show(issue.note)}`} <small>by {issue.requested_by}{issue.decided_by && `, decided by ${issue.decided_by}`}</small>
        {issue.can_receive && issue.request_id && <span className="lv-actions">
          <button className="button button-dark" type="button" disabled={busy} onClick={() => void send<Job>(`/api/factory/deliveries/${issue.request_id}/`, "POST", {}, () => `The warehouse's delivery is received at ${issue.deliver_to} and booked to ${job.number}.`)}>It arrived at the factory</button>
        </span>}
        {issue.can_decide && <span className="lv-actions">
          <button className="button button-dark" type="button" disabled={busy} onClick={() => void send<Job>(`/api/factory/materials/${issue.id}/`, "POST", { action: "approve" }, () => issue.deliver_to ? `${issue.sku} is approved and sent to the warehouse to prepare.` : `${issue.sku} is used from factory stock for ${job.number}.`)}>Approve</button>
          <button className="button button-outline" type="button" disabled={busy || !note.trim()} onClick={() => void send<Job>(`/api/factory/materials/${issue.id}/`, "POST", { action: "reject", note }, () => `The extra ${issue.sku} is rejected.`)}>Reject</button>
          <input aria-label="Reason to reject" value={note} placeholder="Reason, to reject" onChange={(event) => setNote(event.target.value)} />
        </span>}
      </li>)}</ul>}
      {job.can_work && <div className="mj-inline sl-inline fac-inline" aria-label="Take materials">
        <select aria-label="Material to take" value={take.variant_id} onChange={(event) => setTake({ ...take, variant_id: event.target.value, location_id: "" })}>
          <option value="">Ask for a material…</option>{takeChoices.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
        </select>
        <select aria-label="Taken from" value={take.location_id} onChange={(event) => setTake({ ...take, location_id: event.target.value })}>
          <option value="">From…</option>{places.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
        </select>
        {!fromFactory && <select aria-label="Deliver to" value={take.deliver_to_id} onChange={(event) => setTake({ ...take, deliver_to_id: event.target.value })}>
          <option value="">Deliver to…</option>{factoryPlaces.map((location) => <option key={location.id} value={location.id}>to {location.code} · {show(location.name)}</option>)}
        </select>}
        <input aria-label="Quantity to take" value={take.quantity} inputMode="decimal" placeholder="Quantity" className="fac-qty" onChange={(event) => setTake({ ...take, quantity: event.target.value })} />
        {beyond && <input aria-label="Why more than listed" value={take.note} placeholder="Why more than listed (Factory Manager approves)" onChange={(event) => setTake({ ...take, note: event.target.value })} />}
        <button className="button button-dark" type="button" disabled={busy || !take.variant_id || !take.location_id || !take.quantity || (!fromFactory && !take.deliver_to_id)}
          onClick={() => void send<Job>(`/api/factory/jobs/${job.id}/materials/`, "POST", { ...take, variant_id: Number(take.variant_id), location_id: Number(take.location_id), deliver_to_id: fromFactory ? null : Number(take.deliver_to_id) },
            () => fromFactory ? (beyond ? "Sent to the Factory Manager, as it is more than the job listed." : "Used from factory stock and booked to the job.") : "Material request made. The Factory Manager approves it, then the warehouse admin; then print the slip for the warehouse team.").then((ok) => ok && setTake(blankTake))}>{fromFactory ? (beyond ? "Ask for approval" : "Use from factory stock") : "Send for approval"}</button>
      </div>}
      {job.can_work && stillNeeded.length > 0 && store && <div className="mj-inline sl-inline fac-inline" aria-label="Ask for everything still listed">
        <span className="fac-hint">Or make one request to {store.code} for everything still listed ({stillNeeded.map((row) => `${num(String(Number(row.quantity) - Number(row.taken)))} × ${row.sku}`).join(", ")}), delivered to the factory location above.</span>
        <button className="button button-outline" type="button" disabled={busy || !take.deliver_to_id}
          onClick={() => void send<Job>(`/api/factory/jobs/${job.id}/materials/`, "POST", { location_id: store.id, deliver_to_id: Number(take.deliver_to_id), lines: stillNeeded.map((row) => ({ variant_id: row.variant_id, quantity: String(Number(row.quantity) - Number(row.taken)) })) },
            () => "One material request made for everything listed. The Factory Manager approves it, then the warehouse admin.")}>Ask for all of it</button>
      </div>}
    </>)}
    {block("Hours", <>
      {job.labour.length > 0 && <table className="sp-table bc-table" aria-label={`${job.number} hours`}><thead><tr><th scope="col">Date</th><th scope="col">Who</th><th scope="col">Workshop</th><th scope="col" className="bc-num">Hours</th><th scope="col" className="bc-num">Cost</th></tr></thead>
        <tbody>{job.labour.map((row) => <tr key={row.id}><td>{row.work_date}</td><td>{show(row.employee)}{row.line && <small>{row.line}</small>}</td><td>{row.workshop_label}{row.overtime && <small>overtime</small>}</td><td className="bc-num">{num(row.hours)}</td><td className="bc-num">{qar(row.cost)}<small>at {qar(row.rate)}/h</small></td></tr>)}</tbody></table>}
      {job.can_work && <div className="mj-inline sl-inline fac-inline" aria-label="Record hours">
        <select aria-label="Workshop" value={work.workshop} onChange={(event) => setWork({ ...work, workshop: event.target.value })}>{overview.workshops.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select>
        <select aria-label="Who worked" value={work.employee_id} onChange={(event) => setWork({ ...work, employee_id: event.target.value })}>
          <option value="">Who…</option>{overview.people.map((person) => <option key={person.id} value={person.id}>{show(person.name)} · {person.section_label || person.job_title}</option>)}
        </select>
        {job.boq.length > 0 && <select aria-label="On which JD" value={work.line_id} onChange={(event) => setWork({ ...work, line_id: event.target.value })}>
          <option value="">Which JD…</option>{job.boq.map((line) => <option key={line.code} value={line.id}>{line.code} · {show(line.description).slice(0, 40)}</option>)}
        </select>}
        <input aria-label="Date worked" type="date" value={work.work_date} max={overview.today} onChange={(event) => setWork({ ...work, work_date: event.target.value })} />
        <input aria-label="Hours worked" value={work.hours} inputMode="decimal" placeholder="Hours" className="fac-qty" onChange={(event) => setWork({ ...work, hours: event.target.value })} />
        <label className="fac-check"><input type="checkbox" checked={work.overtime} onChange={(event) => setWork({ ...work, overtime: event.target.checked })} /> Overtime</label>
        <button className="button button-dark" type="button" disabled={busy || !work.employee_id || !work.hours}
          onClick={() => void send<Job>(`/api/factory/jobs/${job.id}/hours/`, "POST", { ...work, employee_id: Number(work.employee_id), line_id: work.line_id ? Number(work.line_id) : null }, () => "Hours recorded on the job.").then((ok) => ok && setWork({ ...work, hours: "", overtime: false }))}>Record hours</button>
      </div>}
    </>)}
    {(job.purchases.length > 0 || job.can_work) && block("Special purchases", <>
      {job.purchases.length > 0 && <ul className="fac-list">{job.purchases.map((purchase) => <PurchaseRow key={`${purchase.id}-${purchase.status}`} purchase={purchase} overview={overview} csrfToken={csrfToken} onDone={onDone} />)}</ul>}
      {job.can_work && <div className="mj-inline sl-inline fac-inline" aria-label="Ask for a special purchase">
        <input aria-label="What is needed" value={buyRequest.description} placeholder="What is needed" onChange={(event) => setBuyRequest({ ...buyRequest, description: event.target.value })} />
        <input aria-label="Purchase quantity" value={buyRequest.quantity} inputMode="decimal" className="fac-qty" onChange={(event) => setBuyRequest({ ...buyRequest, quantity: event.target.value })} />
        <input aria-label="Estimated cost" value={buyRequest.estimated_cost} inputMode="decimal" placeholder="Estimated QAR" className="fac-qty" onChange={(event) => setBuyRequest({ ...buyRequest, estimated_cost: event.target.value })} />
        <input aria-label="Supplier" value={buyRequest.supplier_name} placeholder="Supplier (optional)" onChange={(event) => setBuyRequest({ ...buyRequest, supplier_name: event.target.value })} />
        <input aria-label="Why not from the warehouse" value={buyRequest.reason} placeholder="Why not from the warehouse" onChange={(event) => setBuyRequest({ ...buyRequest, reason: event.target.value })} />
        <button className="button button-outline" type="button" disabled={busy || !buyRequest.description || !buyRequest.estimated_cost || !buyRequest.reason}
          onClick={() => void send<Job>(`/api/factory/jobs/${job.id}/purchases/`, "POST", buyRequest, () => "Special purchase requested. The Factory Manager approves it, then the factory purchaser buys it.").then((ok) => ok && setBuyRequest({ description: "", quantity: "1", estimated_cost: "", supplier_name: "", reason: "" }))}>Request purchase</button>
      </div>}
    </>)}
    <div className="lv-actions fac-actions">
      {job.can_edit && asking === null && <button className="button button-outline" type="button" onClick={() => setAsking("edit")}>Change</button>}
      {job.can_submit && <button className="button button-dark" type="button" disabled={busy} onClick={() => act("submit", {}, `${job.number} is sent to the Factory Manager.`)}>Send for approval</button>}
      {job.can_approve && <button className="button button-dark" type="button" disabled={busy} onClick={() => act("approve", {}, `${job.number} is approved and in production.`)}>Approve the job</button>}
      {job.can_approve_cost && <button className="button button-dark" type="button" disabled={busy} onClick={() => act("approve_cost", {}, `${job.number}'s cost is approved.`)}>Approve the cost</button>}
      {job.can_send && <button className="button button-dark" type="button" disabled={busy} onClick={() => act("send", {}, `${job.number} is on its way to the client's site.`)}>Send to site</button>}
      {job.can_finish && asking === null && <button className="button button-dark" type="button" onClick={() => setAsking("finish")}>Finish the job</button>}
      {job.can_receive && asking === null && <button className="button button-dark" type="button" onClick={() => setAsking("received")}>Client received it</button>}
      {(job.can_approve || job.can_approve_cost || job.can_cancel) && asking === null && <>
        {job.can_approve && <button className="button button-outline" type="button" onClick={() => { setAsking("reject"); setNote(""); }}>Send back</button>}
        {job.can_approve_cost && <button className="button button-outline" type="button" onClick={() => { setAsking("reject_cost"); setNote(""); }}>Send back to production</button>}
        {job.can_cancel && <button className="button button-quiet" type="button" onClick={() => { setAsking("cancel"); setNote(""); }}>Cancel the job</button>}
      </>}
    </div>
    {asking === "finish" && <div className="mj-inline sl-inline fac-inline" aria-label="Finish the job">
      {job.purpose !== "showroom" && <select aria-label="Where it goes" value={finish.destination} onChange={(event) => setFinish({ ...finish, destination: event.target.value })}>
        <option value="">Where does it go?</option><option value="warehouse">Store in the warehouse</option><option value="site">Straight to the client's site (held in the project's Vsite warehouse until received)</option>
      </select>}
      {finish.destination === "warehouse" && <select aria-label="Stored at" value={finish.location_id} onChange={(event) => setFinish({ ...finish, location_id: event.target.value })}>
        <option value="">Stored at…</option>{overview.locations.filter((location) => location.kind !== "factory").map((location) => <option key={location.id} value={location.id}>{location.code} · {show(location.name)}</option>)}
      </select>}
      <button className="button button-dark" type="button" disabled={busy || !finish.destination || (finish.destination === "warehouse" && !finish.location_id)}
        onClick={() => act("finish", { destination: finish.destination, location_id: finish.location_id ? Number(finish.location_id) : null })}>Confirm finished</button>
      <button className="button button-quiet" type="button" onClick={() => setAsking(null)}>Back</button>
    </div>}
    {asking === "received" && <div className="mj-inline sl-inline fac-inline">
      <input aria-label="Date received" type="date" value={received} max={overview.today} onChange={(event) => setReceived(event.target.value)} />
      <button className="button button-dark" type="button" disabled={busy} onClick={() => act("received", { date: received }, `${job.number} is received at the client's site.`)}>Confirm received</button>
      <button className="button button-quiet" type="button" onClick={() => setAsking(null)}>Back</button>
    </div>}
    {(asking === "reject" || asking === "reject_cost" || asking === "cancel") && <div className="mj-inline sl-inline fac-inline">
      <input aria-label="Reason" value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} placeholder="Why" />
      <button className="button button-dark" type="button" disabled={busy || !note.trim()} onClick={() => act(asking)}>Confirm</button>
      <button className="button button-quiet" type="button" onClick={() => setAsking(null)}>Back</button>
    </div>}
    {error && <div className="alert" role="alert">{error}</div>}
  </div>;
}

function PurchaseRow({ purchase, overview, csrfToken, onDone }: { purchase: Purchase; overview: Overview; csrfToken: string; onDone: (message: string) => void }) {
  const [buying, setBuying] = useState(false);
  const [form, setForm] = useState({ account_id: "", date: overview.today, actual_cost: purchase.estimated_cost, receipt_reference: "", note: "" });
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  const act = (action: string, extra: Record<string, unknown> = {}) =>
    void send<Job>(`/api/factory/purchases/${purchase.id}/`, "POST", { action, note: form.note, ...extra }, () => `${show(purchase.description)}: ${action === "buy" ? "bought and booked to the job" : action + "d"}.`);
  return <li>
    <span className={`sp-status sp-status-${TONE[purchase.status]}`}>{purchase.status_label}</span> {num(purchase.quantity)} × {show(purchase.description)} · estimated QAR {qar(purchase.estimated_cost)}
    {purchase.supplier_name && ` · ${show(purchase.supplier_name)}`}{purchase.actual_cost && ` · paid QAR ${qar(purchase.actual_cost)} from ${show(purchase.paid_from)} on ${purchase.bought_on}, receipt ${purchase.receipt_reference}`}
    <small>{show(purchase.reason)} · asked by {purchase.requested_by}{purchase.decided_by && `, decided by ${purchase.decided_by}`}{purchase.decision_note && ` · ${show(purchase.decision_note)}`}</small>
    <span className="lv-actions">
      {purchase.can_decide && <button className="button button-dark" type="button" disabled={busy} onClick={() => act("approve")}>Approve</button>}
      {purchase.can_buy && !buying && <button className="button button-dark" type="button" onClick={() => setBuying(true)}>Record the purchase</button>}
      {(purchase.can_decide || purchase.can_cancel) && <>
        <input aria-label="Reason" value={form.note} placeholder="Reason, to reject or cancel" onChange={(event) => setForm({ ...form, note: event.target.value })} />
        {purchase.can_decide && <button className="button button-outline" type="button" disabled={busy || !form.note.trim()} onClick={() => act("reject")}>Reject</button>}
        {purchase.can_cancel && <button className="button button-quiet" type="button" disabled={busy || !form.note.trim()} onClick={() => act("cancel")}>Cancel</button>}
      </>}
    </span>
    {buying && <span className="mj-inline sl-inline fac-inline">
      <select aria-label="Paid from" value={form.account_id} onChange={(event) => setForm({ ...form, account_id: event.target.value })}>
        <option value="">Paid by (cash box or card)…</option>{overview.accounts.map((account) => <option key={account.id} value={account.id}>{show(account.name)} · {account.kind_label}</option>)}
      </select>
      <input aria-label="Date bought" type="date" value={form.date} max={overview.today} onChange={(event) => setForm({ ...form, date: event.target.value })} />
      <input aria-label="Amount paid" value={form.actual_cost} inputMode="decimal" className="fac-qty" onChange={(event) => setForm({ ...form, actual_cost: event.target.value })} />
      <input aria-label="Receipt number" value={form.receipt_reference} placeholder="Receipt number" onChange={(event) => setForm({ ...form, receipt_reference: event.target.value })} />
      <button className="button button-dark" type="button" disabled={busy || !form.account_id || !form.receipt_reference} onClick={() => act("buy", { account_id: Number(form.account_id), date: form.date, actual_cost: form.actual_cost, receipt_reference: form.receipt_reference })}>Confirm purchase</button>
      <button className="button button-quiet" type="button" onClick={() => setBuying(false)}>Back</button>
    </span>}
    {error && <div className="alert" role="alert">{error}</div>}
  </li>;
}

function Jobs({ overview, csrfToken, onDone }: { overview: Overview; csrfToken: string; onDone: (message: string) => void }) {
  const [opening, setOpening] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const done = (message: string) => { setOpening(false); onDone(message); };
  return <>
    <dl className="sp-totals" aria-label="Factory at a glance">
      <div><dt>In production</dt><dd>{overview.tiles.in_production}</dd></div>
      <div><dt>Waiting for a decision</dt><dd>{overview.tiles.waiting}</dd></div>
      <div><dt>Late</dt><dd>{overview.tiles.late}</dd></div>
      <div><dt>Finished, not yet at site</dt><dd>{overview.tiles.to_site}</dd></div>
      <div><dt>Cost in production</dt><dd>QAR {qar(overview.tiles.work_in_progress)}</dd></div>
      <div><dt>Materials to order</dt><dd className={overview.tiles.to_order ? "fac-late" : undefined}>{overview.tiles.to_order}</dd></div>
    </dl>
    {overview.can_run && (opening
      ? <JobForm overview={overview} csrfToken={csrfToken} onDone={done} onClose={() => setOpening(false)} />
      : <div className="sp-actions"><button className="button button-dark" type="button" onClick={() => setOpening(true)}>Open a job order</button></div>)}
    {overview.orders.length === 0
      ? <p className="sp-empty">No job orders in {overview.unit.name} yet.</p>
      : <div className="sp-table-wrap"><table className="sp-table bc-table sl-table fac-table fac-jobs" aria-label="Job orders">
        <thead><tr><th scope="col">Job</th><th scope="col">For</th><th scope="col">Needed by</th><th scope="col" className="bc-num">Cost so far</th><th scope="col">Status</th></tr></thead>
        <tbody>{overview.orders.map((job) => <JobRows key={job.id} job={job} open={openId === job.id} onToggle={() => setOpenId(openId === job.id ? null : job.id)} overview={overview} csrfToken={csrfToken} onDone={onDone} />)}</tbody>
      </table></div>}
  </>;
}

function JobRows({ job, open, onToggle, overview, csrfToken, onDone }: { job: Job; open: boolean; onToggle: () => void; overview: Overview; csrfToken: string; onDone: (message: string) => void }) {
  const waiting = job.issues.filter((issue) => issue.can_decide).length + job.material_requests.filter((slip) => slip.can_decide).length + job.purchases.filter((purchase) => purchase.can_decide || purchase.can_buy).length;
  return <>
    <tr className={open ? "fac-open" : undefined}>
      <th scope="row"><button type="button" className="fac-toggle" aria-expanded={open} onClick={onToggle}>{job.number}</button>
        <small>{job.boq.length ? `${job.boq.length} BOQ ${job.boq.length === 1 ? "item" : "items"}` : job.product ? `${num(job.product.quantity)} × ${job.product.sku}` : ""}</small></th>
      <td>{job.purpose_label}<small>{job.quote ? show(job.quote.customer) : ""}{job.project_name ? `${job.quote ? " · " : ""}${show(job.project_name)}` : ""}</small></td>
      <td>{job.due_date}{job.late && <small className="fac-late">late</small>}{job.short_materials && job.short_materials.length > 0 && <small className="fac-late">{job.short_materials.length} material{job.short_materials.length === 1 ? "" : "s"} to order</small>}</td>
      <td className="bc-num">{qar(job.actual.total)}{Number(job.estimate.total) > 0 && <small>of {qar(job.estimate.total)}</small>}</td>
      <td><span className={`sp-status sp-status-${TONE[job.status]}`}>{job.status_label}</span>{waiting > 0 && <small>{waiting} waiting for you</small>}</td>
    </tr>
    {open && <tr className="fac-detail-row"><td colSpan={5}><JobDetail key={`${job.id}-${job.status}`} job={job} overview={overview} csrfToken={csrfToken} onDone={onDone} /></td></tr>}
  </>;
}

function RawMaterials({ overview, csrfToken, onDone }: { overview: Overview; csrfToken: string; onDone: (message: string) => void }) {
  const [filter, setFilter] = useState("");
  const [category, setCategory] = useState("");
  const [form, setForm] = useState({ variant_id: "", category: "boards", size: "" });
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  const text = filter.trim().toLowerCase();
  const rows = overview.raw_materials.filter((row) => (!category || row.category === category)
    && (!text || `${row.code} ${row.item_code} ${row.description} ${row.size} ${row.supplier}`.toLowerCase().includes(text)));
  return <>
    {overview.to_order.length > 0 && <section className="fac-to-order" aria-label="Materials to order">
      <h3>To order</h3>
      <p className="sp-hint">What open jobs still need, against free stock in the stores and at the factory, earliest job first.</p>
      <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label="Materials to order">
        <thead><tr><th scope="col">Material</th><th scope="col" className="bc-num">Jobs need</th><th scope="col" className="bc-num">Free</th><th scope="col" className="bc-num">To order</th><th scope="col">Short on</th></tr></thead>
        <tbody>{overview.to_order.map((row) => <tr key={row.variant_id} className="fac-short"><th scope="row">{row.sku}<small>{show(row.name)}</small></th>
          <td className="bc-num">{num(row.needed)}</td><td className="bc-num">{num(row.free)}</td><td className="bc-num"><strong>{num(row.to_order)}</strong> <small>{row.unit.toLowerCase()}</small></td>
          <td>{row.jobs.map((job) => `${job.job} ${num(job.short)} (due ${job.due_date})`).join(" · ")}</td></tr>)}</tbody>
      </table></div>
    </section>}
    <p className="fac-intro">The materials the factory usually uses. Stock and prices come from the warehouse and finance records; the factory takes them against a job order on the Job orders page.</p>
    <div className="mj-inline sl-inline fac-inline">
      <input aria-label="Search raw materials" value={filter} placeholder="Search code, description, size or supplier" onChange={(event) => setFilter(event.target.value)} />
      <select aria-label="Category" value={category} onChange={(event) => setCategory(event.target.value)}><option value="">All categories</option>{overview.categories.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select>
    </div>
    {rows.length === 0 ? <p className="sp-empty">{overview.raw_materials.length ? "Nothing matches." : "No raw materials are on the list yet."}</p>
      : <div className="sp-table-wrap"><table className="sp-table bc-table sl-table fac-table" aria-label="Raw materials">
        <thead><tr><th scope="col">Code</th><th scope="col">Description</th><th scope="col">Size</th><th scope="col" className="bc-num">Unit price</th><th scope="col">Supplier</th><th scope="col" className="bc-num">In stock</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.variant_id}>
          <th scope="row" className="pw-mono">{row.code}<small>{row.category_label}</small></th>
          <td>{show(row.description)}<small>per {row.unit.toLowerCase()}</small></td>
          <td>{row.size || "–"}</td>
          <td className="bc-num">{row.unit_price ? `QAR ${qar(row.unit_price)}` : "–"}{row.list_price && <small>list {row.list_currency} {qar(row.list_price)}</small>}</td>
          <td>{row.supplier ? show(row.supplier) : "–"}</td>
          <td className="bc-num">{num(row.on_hand)}{row.free !== row.on_hand && <small>{num(row.free)} free</small>}<small>{row.places.map((place) => `${place.location} ${num(place.on_hand)}`).join(" · ")}</small></td>
        </tr>)}</tbody>
      </table></div>}
    {overview.can_keep_list && <form className="sp-form fac-form" aria-label="Add to the raw materials list" onSubmit={(event) => {
      event.preventDefault();
      void send<{ raw_materials: RawMaterial[] }>("/api/factory/raw-materials/", "PUT", { ...form, variant_id: Number(form.variant_id) }, () => "The raw materials list is updated.")
        .then((ok) => ok && setForm({ variant_id: "", category: form.category, size: "" }));
    }}>
      <h3>Add a stock item to the list</h3>
      <div className="sp-fields fac-grid">
        <label className="fac-wide">Stock item<select value={form.variant_id} onChange={(event) => setForm({ ...form, variant_id: event.target.value })} required>
          <option value="">Choose…</option>{overview.items.map((row) => <option key={row.variant_id} value={row.variant_id}>{row.sku} · {show(row.name)}</option>)}
        </select></label>
        <label>Category<select value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })}>{overview.categories.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>
        <label>Size<input value={form.size} maxLength={80} placeholder="e.g. 2440 x 1220 x 18 mm" onChange={(event) => setForm({ ...form, size: event.target.value })} /></label>
      </div>
      <p className="sp-hint">New items are created on Inventory › Stock items first, with their code, description and supplier.</p>
      {error && <div className="alert" role="alert">{error}</div>}
      <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy}>Add to the list</button></div>
    </form>}
  </>;
}

function Setup({ overview, csrfToken, onDone }: { overview: Overview; csrfToken: string; onDone: (message: string) => void }) {
  const [form, setForm] = useState({ ...overview.settings, rates: { ...overview.settings.rates } });
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  if (!overview.is_master_admin) return <p className="sp-empty">Only the Master Admin changes the factory's setup.</p>;
  const save = (extra: Record<string, unknown> = {}) => void send("/api/factory/settings/", "PUT",
    { overhead_percent: form.overhead_percent, overrun_percent: form.overrun_percent, overtime_percent: form.overtime_percent, rates: form.rates, asset_threshold: form.asset_threshold, machine_life_months: form.machine_life_months, order_lead_days: form.order_lead_days, stages_per_person: form.stages_per_person, ...extra },
    () => !("is_live" in extra) ? "Factory setup is saved."
      : extra.is_live ? "The Factory section is live for everyone with a factory permission." : "The Factory section is hidden again; only you can see it.");
  return <>
    <section className={`fac-switch ${overview.settings.is_live ? "is-live" : ""}`} aria-label="Go-live switch">
      <div><strong>{overview.settings.is_live ? "The Factory section is live." : "The Factory section is not live yet."}</strong>
        <p>{overview.settings.is_live ? "Everyone with a factory permission sees it." : "Only you (Master Admin) can see it. Turn it on when the factory team is ready."}</p></div>
      <button className={`button ${overview.settings.is_live ? "button-outline" : "button-dark"}`} type="button" disabled={busy} onClick={() => save({ is_live: !overview.settings.is_live })}>{overview.settings.is_live ? "Hide it again" : "Make it live"}</button>
    </section>
    <form className="sp-form fac-form" aria-label="Factory rates" onSubmit={(event) => { event.preventDefault(); save(); }}>
      <h3>Hourly rates and job costing</h3>
      <div className="sp-fields fac-grid">
        {overview.workshops.map((workshop) => <label key={workshop.id}>{workshop.label} (QAR per hour)<input value={form.rates[workshop.id] ?? ""} inputMode="decimal" onChange={(event) => setForm({ ...form, rates: { ...form.rates, [workshop.id]: event.target.value } })} /></label>)}
        <label>Overtime (% of the rate)<input value={form.overtime_percent} inputMode="decimal" onChange={(event) => setForm({ ...form, overtime_percent: event.target.value })} /></label>
        <label>Factory overhead (%)<input value={form.overhead_percent} inputMode="decimal" onChange={(event) => setForm({ ...form, overhead_percent: event.target.value })} /></label>
        <label>Finance approves above (% over estimate)<input value={form.overrun_percent} inputMode="decimal" onChange={(event) => setForm({ ...form, overrun_percent: event.target.value })} /></label>
        <label>A machine is a fixed asset from (QAR)<input value={form.asset_threshold} inputMode="decimal" onChange={(event) => setForm({ ...form, asset_threshold: event.target.value })} /></label>
        <label>Machine useful life (months)<input value={String(form.machine_life_months)} inputMode="numeric" onChange={(event) => setForm({ ...form, machine_life_months: Number(event.target.value) || 0 })} /></label>
        <label>Days from ordering to delivery (planner)<input value={String(form.order_lead_days)} inputMode="numeric" onChange={(event) => setForm({ ...form, order_lead_days: Number(event.target.value) || 0 })} /></label>
        <label>Stages one person works on at once (planner)<input value={String(form.stages_per_person)} inputMode="numeric" onChange={(event) => setForm({ ...form, stages_per_person: Number(event.target.value) || 0 })} /></label>
      </div>
      <p className="sp-hint">New rates apply to hours recorded from now on; a job keeps the overhead and limit it was approved with.{overview.settings.updated_by && ` Last changed by ${overview.settings.updated_by}.`}</p>
      {error && <div className="alert" role="alert">{error}</div>}
      <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy}>Save</button></div>
    </form>
  </>;
}

const TITLES: Record<FactoryView, [string, string]> = {
  main: ["Job orders", "Factory work for fit-out projects, studio clients and the showrooms: the works (BOQ items), the materials taken from the warehouse, special purchases, workshop hours and what each job costs."],
  planning: ["Production schedule", "Every BOQ item (JD) through its stages, from drawings and materials to saw/CNC, joinery, paint, metal and ready for delivery: who works on it, in which section, and when. List or Gantt view."],
  dashboard: ["Factory dashboard", "Items in production, completed and stuck, late work and where it waits, the team's load, cost against estimate, client payments, and work done by tag over the years."],
  team: ["Factory team", "The factory's people by section (joinery, paint, CNC, metal…), what each person is working on, their hours on each JD, and who has room for more work."],
  machines: ["Machines and spare parts", "Every machine with its condition and maintenance, alerts when service is due, the spare parts and consumables on hand, and how Finance treats each purchase."],
  learning: ["What the factory learned", "From every finished JD: the usual days per stage, materials and accessories per unit, hours and team, by tag. Used to fill new JDs that are made the same way."],
  materials: ["Raw materials", "Boards, veneer, paint, hardware and metal the factory usually uses, with their size, price, supplier and stock."],
  setup: ["Factory setup", "Hourly rates, overtime, overhead, the Finance cost check, and the switch that makes this section live."],
};

export default function Factory({ selectedUnitId, csrfToken, view = "main" }: { selectedUnitId: number | null; csrfToken: string; view?: FactoryView }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [revision, setRevision] = useState(0);
  const load = useCallback(async () => {
    if (selectedUnitId === null) { setError("Choose a business unit to see the factory."); return; }
    try {
      setOverview(await call<Overview>(`/api/factory/?unit=${selectedUnitId}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(errorText(caught, "The factory could not be loaded."));
    }
  }, [selectedUnitId]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); setRevision((value) => value + 1); void load(); };
  const [title, intro] = TITLES[view];
  return <section className="content-card supplier-payments bank-cash staff-loans-page factory-page" aria-labelledby="fac-title">
    <header className="sp-head">
      <span className="eyebrow">FACTORY · {title.toUpperCase()} · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="fac-title">{title}</h2>
      <p>{intro}</p>
    </header>
    {overview && !overview.settings.is_live && <p className="fac-preview" role="note">Preview: this section is not live yet, so only the Master Admin can see it. The Phase 1 teams are not affected.</p>}
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading…</p>}
    {overview && view === "main" && <Jobs overview={overview} csrfToken={csrfToken} onDone={done} />}
    {overview && view === "planning" && <PlanningView unitId={overview.unit.id} csrfToken={csrfToken} />}
    {overview && view === "dashboard" && <DashboardView unitId={overview.unit.id} />}
    {overview && view === "team" && <TeamView unitId={overview.unit.id} csrfToken={csrfToken} />}
    {overview && view === "machines" && <MachinesView unitId={overview.unit.id} csrfToken={csrfToken} />}
    {overview && view === "learning" && <LearningView unitId={overview.unit.id} />}
    {overview && view === "materials" && <RawMaterials overview={overview} csrfToken={csrfToken} onDone={done} />}
    {overview && view === "setup" && <Setup key={revision} overview={overview} csrfToken={csrfToken} onDone={done} />}
    {overview && <p className="sp-hint">{overview.policy}</p>}
  </section>;
}
