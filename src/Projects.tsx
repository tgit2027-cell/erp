import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";
import "./staff-loans.css";
import "./factory.css";
import "./projects.css";
import { BondsView, TendersView } from "./Tenders";
import { call, errorText, num, qar, short, show, TONE, useAction, type Bond, type Person } from "./projects-shared";

type Step = { step: string; label: string; planned_on: string; done_on: string; not_needed: boolean; late: boolean; automatic: boolean; note: string };
type Line = {
  id: number; position: number; code: string; section: string; area: string; floor: string; work_type: string; price_group: string; category: string;
  description: string; quantity: string; unit: string; rate: string; cost_rate: string; value: string; budget: string; used: string; left: string; over: boolean;
  supplier: string; remarks: string; rfq_date: string; source: string; source_label: string; done_percent: string; stage: string; late: boolean; steps: Step[];
  factory_job: { id: number; number: string; status_label: string } | null;
};
type Order = {
  id: number; number: string; kind: string; kind_label: string; supplier: string; description: string; amount: string; lines: { id: number; code: string; area: string }[];
  budget_left: string; over_budget: string; over_reason: string; status: string; status_label: string; reference: string; payment_due: string; paid: string; to_pay: string;
  payments: { id: number; amount: string; paid_on: string; reference: string; account: string }[];
  requested_by: string; manager_by: string; finance_by: string; decision_note: string; finance_question: string; details_answer: string;
  delivery: string; expected_delivery: string;
  instalments: { label: string; percent: string; event: string; event_label: string; amount: string; paid: string; due_on: string }[];
  can_manager: boolean; can_finance: boolean; can_answer: boolean; can_pay: boolean; can_cancel: boolean;
};
type Claim = {
  id: number; number: string; kind: string; kind_label: string; period_end: string; status: string; status_label: string; work_to_date: string; previous: string;
  gross: string; retention: string; advance_recovery: string; retention_released: string; net: string; received: string; invoice_number: string; due_on: string;
  lines: { code: string; area: string; description: string; amount: string; done_percent: string; value: string }[];
  requested_gross: string; submitted_on: string; certified_on: string; consultant_reference: string; can_submit: boolean; can_certify: boolean;
  prepared_by: string; approved_by: string; decision_note: string; can_decide: boolean; can_withdraw: boolean; can_invoice: boolean; can_receive: boolean;
};
type Variation = { id: number; number: string; description: string; value: string; cost_estimate: string; status: string; status_label: string; client_reference: string; done_percent: string; decision_note: string; requested_by: string; can_decide: boolean; can_sign: boolean };
type Week = { week: string; in_actual: string; out_actual: string; in_planned: string; out_planned: string; net: string; running: string; past: boolean; current: boolean; items: { kind: string; text: string; amount: string; on: string }[] };
type Cash = { weeks: Week[]; opening: string; overdue_in: string; overdue_out: string };
type Totals = Record<string, string> & { over_budget: boolean };
type Project = {
  id: number; number: string; name: string; status: string; status_label: string; customer: { id: number; name: string; project_number: string };
  tender: { id: number; number: string } | null; site: string; quote_reference: string; boq_file_name: string; project_manager: Person | null;
  start_date: string; handover_due: string; handed_over_on: string; defects_end: string; late: boolean; contract_reference: string; signed_on: string;
  advance_percent: string; retention_percent: string; retention_release: string; warranty_days: number; created_by: string; approved_by: string; totals: Totals; items: number; late_items: number; over_items: number;
  factory_items: number; waiting: number; bonds_expiring: number;
  can_edit: boolean; can_discount: boolean; can_approve: boolean; can_order: boolean; can_schedule: boolean; can_progress: boolean; can_vary: boolean;
  can_claim: boolean; can_release: boolean; can_handover: boolean; can_close: boolean; can_cancel: boolean; can_receive: boolean; can_send_to_factory: boolean; can_bond: boolean;
  boq?: Line[]; orders?: Order[]; claims?: Claim[]; variations?: Variation[]; cash?: Cash; bonds?: Bond[];
  receipts?: { id: number; kind_label: string; claim: string; amount: string; received_on: string; reference: string; account: string }[];
  factory_jobs?: { id: number; number: string; status_label: string; due_date: string; cost: string; estimate: string; lines: string[]; slips: { id: number; number: string; status_label: string; store: string; lines: number }[] }[];
};
type Approval = { type: string; project: string; project_id: number; id: number; number: string; description: string; amount: string; status_label: string; supplier?: string; over_budget?: string; over_reason?: string; net?: string };
type Overview = {
  unit: { id: number; name: string }; today: string;
  settings: { is_live: boolean; advance_percent: string; retention_percent: string; retention_release: string; warranty_days: number; bond_reminder_days: number; claim_due_days: number; updated_by: string };
  tiles: { running: number; contract_value: string; budget_left: string; to_bill: string; to_collect: string; retention_held: string; waiting: number; late_items: number; over_budget: number };
  projects: Project[]; approvals: Approval[]; cash: Cash; tenders: { id: number; number: string; client_name: string; project_name: string; total: string; customer_id: number | null; quote_reference: string }[];
  customers: { id: number; name: string; number: string }[]; people: Person[]; accounts: { id: number; name: string; kind_label: string }[];
  sources: { id: string; label: string }[]; steps: { id: string; label: string }[]; order_kinds: { id: string; label: string }[];
  can_run: boolean; can_approve: boolean; can_finance: boolean; is_master_admin: boolean; policy: string;
};
type BoqPreview = { file_name: string; sheet: string; lines: Record<string, string>[]; problems: string[]; more_problems: number;
  totals: { items: number; value: string; discount_percent: string; discount: string; contract_value: string; cost: string; margin: string; factory_items: number; sheet_total: string } };
type Done = (message: string) => void;
export type ProjectsView = "main" | "tenders" | "bonds" | "cash" | "setup";

const block = (title: string, children: ReactNode) => <section className="fac-block"><h4>{title}</h4>{children}</section>;

function OpenProject({ overview, csrfToken, onDone, onClose }: { overview: Overview; csrfToken: string; onDone: Done; onClose: () => void }) {
  const [preview, setPreview] = useState<BoqPreview | null>(null);
  const [reading, setReading] = useState({ busy: false, error: "" });
  const [form, setForm] = useState({ tender_id: "", customer_id: "", name: "", site: "", contract_reference: "", signed_on: overview.today, project_manager_id: "", handover_due: "", discount_percent: "0", quote_reference: "",
    advance_percent: overview.settings.advance_percent, retention_percent: overview.settings.retention_percent, retention_release: overview.settings.retention_release, warranty_days: String(overview.settings.warranty_days) });
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  const read = async (file: File | undefined) => {
    if (!file) return;
    setReading({ busy: true, error: "" });
    const body = new FormData();
    body.append("file", file);
    try {
      const result = await call<BoqPreview>(`/api/projects/boq-upload/?unit=${overview.unit.id}`, { method: "POST", headers: { "X-CSRFToken": csrfToken }, body });
      setPreview(result);
      setForm((current) => ({ ...current, discount_percent: result.totals.discount_percent }));
      setReading({ busy: false, error: "" });
    } catch (caught) { setReading({ busy: false, error: errorText(caught, "The BOQ could not be read.") }); }
  };
  const gross = Number(preview?.totals.value ?? 0);
  const final = gross * (1 - Number(form.discount_percent || 0) / 100);
  const chooseTender = (id: string) => {
    const tender = overview.tenders.find((row) => String(row.id) === id);
    setForm({ ...form, tender_id: id, customer_id: tender?.customer_id ? String(tender.customer_id) : form.customer_id, name: tender ? show(tender.project_name || tender.client_name) : form.name, quote_reference: tender?.quote_reference ?? form.quote_reference });
  };
  return <form className="sp-form fac-form" aria-label="Open a signed project" onSubmit={(event) => {
    event.preventDefault();
    if (!preview) return;
    void send<Project>("/api/projects/", "POST", { ...form, tender_id: form.tender_id ? Number(form.tender_id) : null, customer_id: Number(form.customer_id), project_manager_id: form.project_manager_id ? Number(form.project_manager_id) : null, warranty_days: Number(form.warranty_days) || 0, boq: preview.lines, boq_file_name: preview.file_name },
      (row) => `${row.number} is open with ${row.items} items. Its budget now waits for the Construction Manager.`);
  }}>
    <h3>Open a signed project</h3>
    <div className="fac-upload">
      <label className="button button-dark fac-file">{reading.busy ? "Reading…" : preview ? "Upload a different BOQ" : "Upload the signed BOQ (Excel)"}<input type="file" accept=".xlsx,.xlsm" aria-label="Signed BOQ Excel file" onChange={(event) => void read(event.target.files?.[0])} /></label>
      <span>Tivoli's BOQ template: items with their area, floor, category, selling price, cost (supplier + installation) and supplier. Joinery items go to the factory.</span>
    </div>
    {reading.error && <div className="alert" role="alert">{reading.error}</div>}
    {preview && <>
      <p className="fac-uploaded"><strong>{preview.file_name}</strong> · {preview.totals.items} items · {preview.totals.factory_items} joinery items for the factory{preview.totals.sheet_total && ` · sheet total QAR ${qar(preview.totals.sheet_total)}`}</p>
      {preview.problems.length > 0 && <ul className="fac-problems">{preview.problems.map((problem) => <li key={problem}>{show(problem)}</li>)}</ul>}
      <dl className="sp-totals prj-price" aria-label="Contract price">
        <div><dt>Selling price (BOQ)</dt><dd>QAR {qar(preview.totals.value)}</dd></div>
        <div><dt>Discount</dt><dd>{num(form.discount_percent || "0")}% · QAR {qar(gross - final)}</dd></div>
        <div><dt>Final price</dt><dd>QAR {qar(final)}</dd></div>
        <div><dt>Approved cost (budget)</dt><dd>QAR {qar(preview.totals.cost)}</dd></div>
        <div><dt>Margin</dt><dd>QAR {qar(final - Number(preview.totals.cost))} · {final ? Math.round((final - Number(preview.totals.cost)) * 100 / final) : 0}%</dd></div>
      </dl>
    </>}
    <div className="sp-fields fac-grid">
      {overview.tenders.length > 0 && <label>From a won tender<select value={form.tender_id} onChange={(event) => chooseTender(event.target.value)}><option value="">Not from a tender</option>{overview.tenders.map((row) => <option key={row.id} value={row.id}>{row.number} · {show(row.project_name || row.client_name)}</option>)}</select></label>}
      <label>Client<select required value={form.customer_id} onChange={(event) => setForm({ ...form, customer_id: event.target.value })}><option value="">Choose…</option>{overview.customers.map((row) => <option key={row.id} value={row.id}>{show(row.name)}{row.number && ` · ${row.number}`}</option>)}</select></label>
      <label>Project name<input required value={form.name} maxLength={160} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
      <label>Site<input value={form.site} maxLength={160} onChange={(event) => setForm({ ...form, site: event.target.value })} /></label>
      <label>Contract or LPO reference<input required value={form.contract_reference} maxLength={60} onChange={(event) => setForm({ ...form, contract_reference: event.target.value })} /></label>
      <label>Date signed<input required type="date" max={overview.today} value={form.signed_on} onChange={(event) => setForm({ ...form, signed_on: event.target.value })} /></label>
      <label>Discount (%)<input inputMode="decimal" value={form.discount_percent} onChange={(event) => setForm({ ...form, discount_percent: event.target.value })} /></label>
      <label>Project Manager<select value={form.project_manager_id} onChange={(event) => setForm({ ...form, project_manager_id: event.target.value })}><option value="">Choose…</option>{overview.people.map((row) => <option key={row.id} value={row.id}>{show(row.name)}</option>)}</select></label>
      <label>Handover due<input type="date" value={form.handover_due} onChange={(event) => setForm({ ...form, handover_due: event.target.value })} /></label>
      <label>Down payment (%, usually 20 to 50)<input inputMode="decimal" value={form.advance_percent} onChange={(event) => setForm({ ...form, advance_percent: event.target.value })} /></label>
      <label>Retention (%)<input inputMode="decimal" value={form.retention_percent} onChange={(event) => setForm({ ...form, retention_percent: event.target.value })} /></label>
      <label>Retention is paid<select value={form.retention_release} onChange={(event) => setForm({ ...form, retention_release: event.target.value })}><option value="handover">At handover</option><option value="warranty">After the warranty period</option></select></label>
      {form.retention_release === "warranty" && <label>Warranty period (days)<input inputMode="numeric" value={form.warranty_days} onChange={(event) => setForm({ ...form, warranty_days: event.target.value })} /></label>}
    </div>
    <p className="sp-hint">Progress payments follow the site's progress: the site team requests each one, the Construction Manager approves it, the consultant certifies it, then Finance invoices it.</p>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy || !preview}>Open the project</button><button className="button button-quiet" type="button" onClick={onClose}>Cancel</button></div>
  </form>;
}

function Approvals({ overview, csrfToken, onDone, onOpen }: { overview: Overview; csrfToken: string; onDone: Done; onOpen: (id: number) => void }) {
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  if (!overview.approvals.length) return null;
  return block(`Waiting for you · ${overview.approvals.length}`, <>
    <ul className="fac-list prj-approvals">{overview.approvals.map((row) => <li key={`${row.type}-${row.id}`}>
      <span className="prj-kind">{row.type === "budget" ? "Budget" : row.type === "order" ? (Number(row.over_budget) > 0 ? "Over budget" : "Order") : row.type === "variation" ? "Variation" : "Claim"}</span>
      <button type="button" className="fac-toggle" onClick={() => onOpen(row.project_id)}>{row.project}</button> {row.number} · {show(row.description || row.supplier || "")} · QAR {qar(row.net ?? row.amount)}
      {row.over_budget && Number(row.over_budget) > 0 && <small>QAR {qar(row.over_budget)} over budget: {show(row.over_reason ?? "")}</small>}
      <small>{row.status_label}</small>
      <span className="lv-actions">
        {row.type === "budget" && <button className="button button-dark" type="button" disabled={busy} onClick={() => void send<Project>(`/api/projects/${row.id}/`, "POST", { action: "approve" }, (project) => `${project.number}'s budget is approved; the project is in progress.`)}>Approve budget</button>}
        {row.type === "variation" && <button className="button button-dark" type="button" disabled={busy} onClick={() => void send<Project>(`/api/projects/variations/${row.id}/`, "POST", { action: "approve" }, () => `${row.number} is approved.`)}>Approve</button>}
        {row.type === "claim" && <button className="button button-outline" type="button" onClick={() => onOpen(row.project_id)}>Open the claim</button>}
        {row.type === "order" && <button className="button button-outline" type="button" onClick={() => onOpen(row.project_id)}>Open the order</button>}
      </span>
    </li>)}</ul>
    {error && <div className="alert" role="alert">{error}</div>}
  </>);
}

function ProjectList({ overview, csrfToken, onDone }: { overview: Overview; csrfToken: string; onDone: Done }) {
  const [opening, setOpening] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const done = (message: string) => { setOpening(false); onDone(message); };
  if (openId !== null) return <ProjectDetail projectId={openId} overview={overview} csrfToken={csrfToken} onDone={onDone} onBack={() => setOpenId(null)} onSwitch={setOpenId} />;
  return <>
    <dl className="sp-totals" aria-label="Projects at a glance">
      <div><dt>Projects in progress</dt><dd>{overview.tiles.running}</dd></div>
      <div><dt>Contract value</dt><dd>QAR {short(overview.tiles.contract_value)}</dd></div>
      <div><dt>Budget left</dt><dd>QAR {short(overview.tiles.budget_left)}</dd></div>
      <div><dt>Work done, not yet billed</dt><dd>QAR {short(overview.tiles.to_bill)}</dd></div>
      <div><dt>To collect from clients</dt><dd>QAR {short(overview.tiles.to_collect)}</dd></div>
      <div><dt>Waiting for a decision</dt><dd className={overview.tiles.waiting ? "fac-late" : undefined}>{overview.tiles.waiting}</dd></div>
      <div><dt>Late items</dt><dd className={overview.tiles.late_items ? "fac-late" : undefined}>{overview.tiles.late_items}</dd></div>
      <div><dt>Over budget</dt><dd className={overview.tiles.over_budget ? "fac-late" : undefined}>{overview.tiles.over_budget}</dd></div>
    </dl>
    <Approvals overview={overview} csrfToken={csrfToken} onDone={onDone} onOpen={setOpenId} />
    {overview.can_run && (opening ? <OpenProject overview={overview} csrfToken={csrfToken} onDone={done} onClose={() => setOpening(false)} />
      : <div className="sp-actions"><button className="button button-dark" type="button" onClick={() => setOpening(true)}>Open a signed project</button></div>)}
    {overview.projects.length === 0 ? <p className="sp-empty">No projects in {overview.unit.name} yet. Open one from its signed BOQ.</p>
      : <div className="sp-table-wrap"><table className="sp-table bc-table sl-table fac-table fac-jobs" aria-label="Projects">
        <thead><tr><th scope="col">Project</th><th scope="col">Client</th><th scope="col" className="bc-num">Final price</th><th scope="col" className="bc-num">Budget used</th><th scope="col" className="bc-num">Done</th><th scope="col">Handover</th><th scope="col">Status</th></tr></thead>
        <tbody>{overview.projects.map((row) => <tr key={row.id}>
          <th scope="row"><button type="button" className="fac-toggle" onClick={() => setOpenId(row.id)}>{row.number}</button><small>{show(row.name)}</small></th>
          <td>{show(row.customer.name)}<small>{row.customer.project_number}{row.tender && ` · ${row.tender.number}`}</small></td>
          <td className="bc-num">{qar(row.totals.contract)}{Number(row.totals.discount) > 0 && <small>after {num(row.totals.discount_percent)}% discount</small>}</td>
          <td className="bc-num">{qar(row.totals.committed)}<small className={row.totals.over_budget || row.over_items ? "fac-late" : undefined}>of {qar(row.totals.budget)}{row.over_items > 0 && ` · ${row.over_items} over`}</small></td>
          <td className="bc-num"><Bar percent={Number(row.totals.done_percent)} />{num(row.totals.done_percent)}%</td>
          <td>{row.handed_over_on || row.handover_due || "–"}{row.late && <small className="fac-late">late</small>}{row.late_items > 0 && <small className="fac-late">{row.late_items} items late</small>}</td>
          <td><span className={`sp-status sp-status-${TONE[row.status]}`}>{row.status_label}</span>{row.waiting > 0 && <small>{row.waiting} waiting</small>}{row.bonds_expiring > 0 && <small className="fac-late">{row.bonds_expiring} bond{row.bonds_expiring > 1 ? "s" : ""} expiring</small>}</td>
        </tr>)}</tbody>
      </table></div>}
  </>;
}

const Bar = ({ percent }: { percent: number }) => <span className="prj-bar" aria-hidden="true"><span style={{ width: `${Math.max(0, Math.min(100, percent))}%` }} /></span>;

const TABS = [["summary", "Summary"], ["boq", "BOQ & budget"], ["orders", "Orders & payments"], ["schedule", "Schedule"], ["cash", "Cash in & out"], ["variations", "Variations"], ["claims", "Claims"], ["factory", "Factory"], ["bonds", "Bonds"]] as const;
type Tab = (typeof TABS)[number][0];

function ProjectDetail({ projectId, overview, csrfToken, onDone, onBack, onSwitch }: { projectId: number; overview: Overview; csrfToken: string; onDone: Done; onBack: () => void; onSwitch: (id: number) => void }) {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("summary");
  const load = useCallback(async () => {
    try { setProject(await call<Project>(`/api/projects/${projectId}/?unit=${overview.unit.id}`)); setError(""); }
    catch (caught) { setError(errorText(caught, "The project could not be loaded.")); }
  }, [projectId, overview.unit.id]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { onDone(message); void load(); };
  if (!project) return <>{error ? <div className="alert" role="alert">{error}</div> : <p className="sp-empty" role="status">Loading…</p>}</>;
  const t = project.totals;
  const props = { project, overview, csrfToken, onDone: done };
  const count = (name: Tab) => name === "orders" ? project.orders?.filter((row) => row.can_manager || row.can_finance || row.can_answer || row.can_pay).length
    : name === "claims" ? project.claims?.filter((row) => row.can_decide || row.can_submit || row.can_certify || row.can_invoice || row.can_receive).length
    : name === "schedule" ? project.late_items : name === "bonds" ? project.bonds_expiring : 0;
  return <div className="prj-detail">
    <div className="prj-head">
      <div className="prj-switch">
        <button className="button button-quiet" type="button" onClick={onBack}>← All projects</button>
        {overview.projects.length > 1 && <label>Switch project
          <select value={projectId} onChange={(event) => onSwitch(Number(event.target.value))}>
            {overview.projects.map((row) => <option key={row.id} value={row.id}>{row.number} · {show(row.name)}</option>)}
          </select></label>}
      </div>
      <div><h3>{project.number} · {show(project.name)} <span className={`sp-status sp-status-${TONE[project.status]}`}>{project.status_label}</span></h3>
        <p className="fac-meta">{show(project.customer.name)} {project.customer.project_number} · contract {show(project.contract_reference)} signed {project.signed_on}{project.site && ` · ${show(project.site)}`}{project.project_manager && ` · PM ${show(project.project_manager.name)}`}{project.tender && ` · tender ${project.tender.number}`}{project.boq_file_name && ` · ${project.boq_file_name}`}</p></div>
    </div>
    <dl className="sp-totals prj-price" aria-label="Price and budget">
      <div><dt>Selling price (BOQ)</dt><dd>QAR {qar(t.boq_value)}</dd></div>
      <div><dt>Discount {num(t.discount_percent)}%</dt><dd>− QAR {qar(t.discount)}</dd></div>
      <div><dt>Final price{Number(t.variations_signed) ? " + variations" : ""}</dt><dd>QAR {qar(t.contract)}</dd></div>
      <div><dt>Budget (approved cost)</dt><dd>QAR {qar(t.budget)}</dd></div>
      <div><dt>Ordered + factory</dt><dd className={t.over_budget ? "fac-late" : undefined}>QAR {qar(t.committed)}</dd></div>
      <div><dt>Budget left</dt><dd className={Number(t.budget_left) < 0 ? "fac-late" : undefined}>QAR {qar(t.budget_left)}</dd></div>
      <div><dt>Expected margin</dt><dd>QAR {qar(t.expected_margin)} · {num(t.margin_percent)}%</dd></div>
      <div><dt>Work done</dt><dd>{num(t.done_percent)}% · QAR {qar(t.work_to_date)}</dd></div>
    </dl>
    <div className="an-tabs prj-tabs" role="tablist" aria-label="Project">
      {TABS.map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={tab === id} className={`an-tab${tab === id ? " an-tab-on" : ""}`} onClick={() => setTab(id)}>{label}{Boolean(count(id)) && <span className="an-count">{count(id)}</span>}</button>)}
    </div>
    {tab === "summary" && <Summary {...props} />}
    {tab === "boq" && <BoqTab {...props} />}
    {tab === "orders" && <OrdersTab {...props} />}
    {tab === "schedule" && <ScheduleTab {...props} />}
    {tab === "cash" && <CashTab project={project} overview={overview} csrfToken={csrfToken} onDone={done} />}
    {tab === "variations" && <VariationsTab {...props} />}
    {tab === "claims" && <ClaimsTab {...props} />}
    {tab === "factory" && <FactoryTab {...props} />}
    {tab === "bonds" && <ProjectBonds {...props} />}
  </div>;
}

type TabProps = { project: Project; overview: Overview; csrfToken: string; onDone: Done };

function Summary({ project, overview, csrfToken, onDone }: TabProps) {
  const [form, setForm] = useState({ discount_percent: project.totals.discount_percent, date: overview.today, note: "" });
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  const t = project.totals;
  const act = (action: string, message: string, extra: Record<string, unknown> = {}) => void send<Project>(`/api/projects/${project.id}/`, "POST", { action, ...extra }, () => message);
  const rows: [string, string][] = [
    ["Down payment from the client", `${qar(t.advance_received)} of ${qar(t.advance_expected)} received · ${qar(t.advance_recovered)} taken back in claims`],
    ["Billed to the client", `${qar(t.billed)} · ${qar(t.to_bill)} done and not billed yet`],
    ["Collected", `${qar(t.collected)} · ${qar(t.to_collect)} still to collect`],
    ["Retention held by the client", qar(t.retention_held)],
    ["Paid to suppliers and subcontractors", qar(t.paid_out)],
    ["Cash position (in − out)", qar(t.cash_position)],
    ["Over budget, approved / waiting", `${qar(t.over_budget_approved)} / ${qar(t.over_budget_waiting)}`],
    ["Variations signed / not signed yet", `${qar(t.variations_signed)} / ${qar(t.variations_at_risk)}`],
  ];
  return <>
    <table className="sp-table bc-table prj-kv" aria-label="Money"><tbody>{rows.map(([label, value]) => <tr key={label}><th scope="row">{label}</th><td className="bc-num">{value}</td></tr>)}</tbody></table>
    <p className="fac-meta">Opened by {project.created_by}{project.approved_by && `, budget approved by ${project.approved_by}`}. Client terms: {num(project.advance_percent)}% down payment, progress payments certified by the consultant, {num(project.retention_percent)}% retention paid {project.retention_release === "handover" ? "at handover" : `${project.warranty_days} days after handover`}{project.defects_end && project.retention_release === "warranty" && ` (${project.defects_end})`}.</p>
    {project.can_approve && <div className="fac-inline"><span className="fac-hint">The budget is the BOQ's approved cost: QAR {qar(t.budget)} for {project.items} items.</span><button className="button button-dark" type="button" disabled={busy} onClick={() => act("approve", "The budget is approved; the project is in progress.")}>Approve the budget</button></div>}
    {project.can_discount && <div className="fac-inline" aria-label="Discount">
      <span className="fac-hint">Discount on the signed total. {project.status === "setup" ? "The Project Manager can change it until the budget is approved; then only the Construction Manager." : "Only the Construction Manager changes it now."}</span>
      <input aria-label="Discount percent" className="fac-qty" inputMode="decimal" value={form.discount_percent} onChange={(event) => setForm({ ...form, discount_percent: event.target.value })} />
      <button className="button button-outline" type="button" disabled={busy} onClick={() => act("discount", "The discount is changed and the final price updated.", { discount_percent: form.discount_percent })}>Change the discount</button>
    </div>}
    {project.can_handover && <div className="fac-inline"><input aria-label="Handover date" type="date" max={overview.today} value={form.date} onChange={(event) => setForm({ ...form, date: event.target.value })} /><button className="button button-outline" type="button" disabled={busy} onClick={() => act("handover", "The project is handed over; the defects period has started.", { date: form.date })}>Record the handover</button></div>}
    {project.can_close && <div className="fac-inline"><button className="button button-outline" type="button" disabled={busy} onClick={() => act("close", "The project is closed.")}>Close the project</button></div>}
    {project.can_cancel && <div className="fac-inline"><input aria-label="Reason to cancel" placeholder="Reason, to cancel" value={form.note} onChange={(event) => setForm({ ...form, note: event.target.value })} /><button className="button button-quiet" type="button" disabled={busy || !form.note.trim()} onClick={() => act("cancel", "The project is cancelled.", { note: form.note })}>Cancel the project</button></div>}
    {error && <div className="alert" role="alert">{error}</div>}
  </>;
}

function BoqTab({ project }: TabProps) {
  const [filter, setFilter] = useState("");
  const lines = project.boq ?? [];
  const text = filter.trim().toLowerCase();
  const shown = text ? lines.filter((line) => `${line.code} ${line.area} ${line.floor} ${line.description} ${line.supplier} ${line.price_group} ${line.category}`.toLowerCase().includes(text)) : lines;
  return <>
    <div className="fac-inline"><input aria-label="Find an item" placeholder="Find by code, area, supplier or description" value={filter} onChange={(event) => setFilter(event.target.value)} /></div>
    <div className="sp-table-wrap"><table className="sp-table bc-table fac-table" aria-label="BOQ and budget">
      <thead><tr><th scope="col">Item</th><th scope="col">Area · floor</th><th scope="col" className="bc-num">Qty</th><th scope="col" className="bc-num">Selling price</th><th scope="col" className="bc-num">Budget (cost)</th><th scope="col" className="bc-num">Used</th><th scope="col" className="bc-num">Left</th><th scope="col">Supplier</th></tr></thead>
      <tbody>{shown.map((line) => <tr key={line.id}>
        <th scope="row" className="pw-mono">{line.code}<small>{[line.price_group, line.category, line.work_type].filter(Boolean).join(" · ")}</small><small title={show(line.description)}>{show(line.description).split("\n")[0].slice(0, 70)}</small></th>
        <td>{show(line.area) || "–"}<small>{show(line.floor)}</small></td>
        <td className="bc-num">{num(line.quantity)}<small>{line.unit}</small></td>
        <td className="bc-num">{qar(line.value)}<small>{qar(line.rate)} each</small></td>
        <td className="bc-num">{qar(line.budget)}<small>{qar(line.cost_rate)} each</small></td>
        <td className="bc-num">{qar(line.used)}</td>
        <td className={`bc-num${line.over ? " fac-late" : ""}`}>{qar(line.left)}{line.over && <small className="fac-late">over budget</small>}</td>
        <td>{show(line.supplier) || "–"}<small>{line.source_label}{line.factory_job && ` · ${line.factory_job.number}`}</small>{line.remarks && <small>{show(line.remarks)}</small>}</td>
      </tr>)}</tbody>
      <tfoot><tr className="fac-total"><th scope="row" colSpan={3}>Total ({lines.length} items)</th><td className="bc-num">{qar(project.totals.boq_value)}</td><td className="bc-num">{qar(project.totals.boq_budget)}</td><td colSpan={3} /></tr></tfoot>
    </table></div>
  </>;
}

function OrdersTab({ project, overview, csrfToken, onDone }: TabProps) {
  const blank = { kind: "supply", supplier: "", description: "", amount: "", reference: "", payment_due: "", over_reason: "", delivery: "immediate", expected_delivery: "" };
  const [form, setForm] = useState(blank);
  const [terms, setTerms] = useState<Term[]>([{ percent: "100", event: "delivered", due_on: "" }]);
  const [picked, setPicked] = useState<number[]>([]);
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  const lines = project.boq ?? [];
  const left = picked.reduce((sum, id) => sum + Number(lines.find((line) => line.id === id)?.left ?? 0), 0);
  const over = Number(form.amount || 0) - Math.max(left, 0);
  return <>
    {(project.orders ?? []).length === 0 ? <p className="sp-empty">No orders yet.</p>
      : <ul className="fac-list prj-orders">{(project.orders ?? []).map((order) => <OrderRow key={`${order.id}-${order.status}-${order.paid}`} order={order} overview={overview} csrfToken={csrfToken} onDone={onDone} />)}</ul>}
    {project.can_order && <form className="sp-form fac-form" aria-label="New order" onSubmit={(event) => {
      event.preventDefault();
      void send<Project>(`/api/projects/${project.id}/orders/`, "POST", { ...form, line_ids: picked, terms },
        (row) => { const last = row.orders?.[row.orders.length - 1]; return last?.status === "approved" ? `${last.number} is within budget and approved.` : `${last?.number} is over budget and waits for the Construction Manager, then Finance.`; })
        .then((ok) => { if (ok) { setForm(blank); setPicked([]); setTerms([{ percent: "100", event: "delivered", due_on: "" }]); } });
    }}>
      <h3>New order to a supplier or subcontractor</h3>
      <fieldset className="fac-lines"><legend>BOQ items it is for ({picked.length} chosen · QAR {qar(left)} budget left)</legend>
        <div className="fac-boq-scroll prj-pick">{lines.filter((line) => line.source !== "factory").map((line) => <label key={line.id} className="fac-check">
          <input type="checkbox" checked={picked.includes(line.id)} onChange={(event) => setPicked(event.target.checked ? [...picked, line.id] : picked.filter((id) => id !== line.id))} />
          <span className="pw-mono">{line.code}</span> {show(line.area)} · {show(line.description).split("\n")[0].slice(0, 50)} <small>left QAR {qar(line.left)}</small></label>)}</div>
      </fieldset>
      <div className="sp-fields fac-grid">
        <label>Kind<select value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}>{overview.order_kinds.map((kind) => <option key={kind.id} value={kind.id}>{kind.label}</option>)}</select></label>
        <label>Supplier or subcontractor<input required value={form.supplier} onChange={(event) => setForm({ ...form, supplier: event.target.value })} /></label>
        <label>What is ordered<input required value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
        <label>Amount (QAR)<input required inputMode="decimal" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} /></label>
        <label>Quotation or LPO number<input value={form.reference} onChange={(event) => setForm({ ...form, reference: event.target.value })} /></label>
        <label>Delivery<select value={form.delivery} onChange={(event) => setForm({ ...form, delivery: event.target.value })}><option value="immediate">Delivered at once (from stock)</option><option value="production">Made to order, then shipped</option></select></label>
        <label>Expected delivery to site<input type="date" value={form.expected_delivery} onChange={(event) => setForm({ ...form, expected_delivery: event.target.value })} /></label>
        {over > 0 && form.amount && <label className="fac-wide">Why it goes QAR {qar(over)} over budget (Construction Manager, then Finance approve)<input required value={form.over_reason} onChange={(event) => setForm({ ...form, over_reason: event.target.value })} /></label>}
      </div>
      <TermsEditor terms={terms} onChange={setTerms} amount={Number(form.amount || 0)} />
      {error && <div className="alert" role="alert">{error}</div>}
      <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy}>{over > 0 && form.amount ? "Send for approval" : "Place the order"}</button></div>
    </form>}
  </>;
}

type Term = { percent: string; event: string; due_on: string };
const EVENTS: [string, string][] = [["order", "On order"], ["production", "Before production"], ["shipped", "On shipping"], ["delivered", "On delivery"], ["installed", "On installation"], ["handed_over", "At handover"], ["date", "On a date"]];
const PRESETS: [string, Term[]][] = [
  ["100% on delivery", [{ percent: "100", event: "delivered", due_on: "" }]],
  ["100% with the order", [{ percent: "100", event: "order", due_on: "" }]],
  ["50% advance, 50% on delivery", [{ percent: "50", event: "order", due_on: "" }, { percent: "50", event: "delivered", due_on: "" }]],
  ["30% advance, 60% on shipping, 10% on installation", [{ percent: "30", event: "production", due_on: "" }, { percent: "60", event: "shipped", due_on: "" }, { percent: "10", event: "installed", due_on: "" }]],
  ["Subcontract: 20% advance, 70% on progress, 10% at handover", [{ percent: "20", event: "order", due_on: "" }, { percent: "70", event: "installed", due_on: "" }, { percent: "10", event: "handed_over", due_on: "" }]],
];

function TermsEditor({ terms, onChange, amount }: { terms: Term[]; onChange: (terms: Term[]) => void; amount: number }) {
  const total = terms.reduce((sum, term) => sum + Number(term.percent || 0), 0);
  const set = (index: number, change: Partial<Term>) => onChange(terms.map((term, at) => at === index ? { ...term, ...change } : term));
  return <fieldset className="fac-lines" aria-label="Payment terms"><legend>Payment terms with this supplier ({total}% of 100%)</legend>
    <div className="fac-inline"><select aria-label="Usual terms" value="" onChange={(event) => { const found = PRESETS.find(([label]) => label === event.target.value); if (found) onChange(found[1].map((term) => ({ ...term }))); }}>
      <option value="">Use usual terms…</option>{PRESETS.map(([label]) => <option key={label} value={label}>{label}</option>)}</select></div>
    {terms.map((term, index) => <div key={index} className="fac-inline">
      <input aria-label={`Instalment ${index + 1} percent`} className="fac-qty" inputMode="decimal" value={term.percent} onChange={(event) => set(index, { percent: event.target.value })} /><span>%</span>
      <select aria-label={`Instalment ${index + 1} paid`} value={term.event} onChange={(event) => set(index, { event: event.target.value })}>{EVENTS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>
      {term.event === "date" && <input aria-label={`Instalment ${index + 1} date`} type="date" value={term.due_on} onChange={(event) => set(index, { due_on: event.target.value })} />}
      <span className="fac-hint">QAR {qar(amount * Number(term.percent || 0) / 100)}</span>
      {terms.length > 1 && <button className="button button-quiet" type="button" onClick={() => onChange(terms.filter((_, at) => at !== index))}>Remove</button>}
    </div>)}
    {terms.length < 8 && <button className="button button-quiet" type="button" onClick={() => onChange([...terms, { percent: String(Math.max(0, 100 - total)), event: "delivered", due_on: "" }])}>Add an instalment</button>}
  </fieldset>;
}

function OrderRow({ order, overview, csrfToken, onDone }: { order: Order; overview: Overview; csrfToken: string; onDone: Done }) {
  const [note, setNote] = useState("");
  const [pay, setPay] = useState({ amount: order.to_pay, date: overview.today, reference: "", account_id: "" });
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  const act = (action: string, message: string, extra: Record<string, unknown> = {}) => void send<Project>(`/api/projects/orders/${order.id}/`, "POST", { action, note, ...extra }, () => message);
  return <li>
    <span className={`sp-status sp-status-${TONE[order.status]}`}>{order.status_label}</span> <strong>{order.number}</strong> {show(order.supplier)} · {show(order.description)} · QAR {qar(order.amount)}
    {order.lines.length > 0 && <small>For {order.lines.map((line) => line.code).join(", ")} · budget left when ordered QAR {qar(order.budget_left)}</small>}
    {Number(order.over_budget) > 0 && <small className="fac-late">QAR {qar(order.over_budget)} over budget: {show(order.over_reason)}</small>}
    {order.finance_question && <small>Finance asked: {show(order.finance_question)}{order.details_answer && ` · answer: ${show(order.details_answer)}`}</small>}
    <small>{order.delivery === "production" ? "Made to order, then shipped" : "Delivered at once"}{order.expected_delivery && ` · expected on site ${order.expected_delivery}`} · {order.instalments.map((row) => `${num(row.percent)}% ${row.label.toLowerCase()} (${row.due_on}${Number(row.paid) >= Number(row.amount) ? ", paid" : Number(row.paid) > 0 ? `, ${qar(row.paid)} paid` : ""})`).join(" · ")}</small>
    <small>Asked by {order.requested_by}{order.manager_by && `, Construction Manager ${order.manager_by}`}{order.finance_by && `, Finance ${order.finance_by}`}{order.payment_due && ` · due ${order.payment_due}`} · paid QAR {qar(order.paid)}{order.payments.map((payment) => ` · ${payment.paid_on} ${qar(payment.amount)} (${payment.reference})`).join("")}</small>
    <span className="lv-actions">
      {(order.can_manager || order.can_finance) && <>
        <button className="button button-dark" type="button" disabled={busy} onClick={() => act("approve", order.can_manager ? `${order.number} goes to Finance.` : `${order.number} is approved.`)}>Approve</button>
        {order.can_finance && <button className="button button-outline" type="button" disabled={busy || !note.trim()} onClick={() => act("ask", `The Project Manager is asked for more details on ${order.number}.`)}>Ask for details</button>}
        <button className="button button-outline" type="button" disabled={busy || !note.trim()} onClick={() => act("reject", `${order.number} is rejected.`)}>Reject</button>
      </>}
      {order.can_answer && <button className="button button-dark" type="button" disabled={busy || !note.trim()} onClick={() => act("answer", `Your answer goes back to Finance.`)}>Send the details</button>}
      {(order.can_manager || order.can_finance || order.can_answer || order.can_cancel) && <input aria-label="Note" value={note} placeholder={order.can_answer ? "Details for Finance" : "Note, question or reason"} onChange={(event) => setNote(event.target.value)} />}
      {order.can_cancel && !order.can_manager && !order.can_finance && <button className="button button-quiet" type="button" disabled={busy || !note.trim()} onClick={() => act("cancel", `${order.number} is cancelled.`)}>Cancel</button>}
    </span>
    {order.can_pay && <span className="fac-inline" aria-label={`Pay ${order.number}`}>
      <select aria-label="Paid from" value={pay.account_id} onChange={(event) => setPay({ ...pay, account_id: event.target.value })}><option value="">Paid from…</option>{overview.accounts.map((account) => <option key={account.id} value={account.id}>{show(account.name)} · {account.kind_label}</option>)}</select>
      <input aria-label="Date paid" type="date" max={overview.today} value={pay.date} onChange={(event) => setPay({ ...pay, date: event.target.value })} />
      <input aria-label="Amount paid" className="fac-qty" inputMode="decimal" value={pay.amount} onChange={(event) => setPay({ ...pay, amount: event.target.value })} />
      <input aria-label="Payment reference" placeholder="Cheque or transfer number" value={pay.reference} onChange={(event) => setPay({ ...pay, reference: event.target.value })} />
      <button className="button button-dark" type="button" disabled={busy || !pay.account_id || !pay.reference} onClick={() => act("pay", `Payment recorded on ${order.number}.`, { ...pay, account_id: Number(pay.account_id) })}>Pay (up to QAR {qar(order.to_pay)})</button>
    </span>}
    {error && <div className="alert" role="alert">{error}</div>}
  </li>;
}

function ScheduleTab({ project, overview, csrfToken, onDone }: TabProps) {
  const [picked, setPicked] = useState<number[]>([]);
  const [form, setForm] = useState({ step: "delivered", planned_on: "", done_on: "" });
  const [progress, setProgress] = useState<Record<string, string>>({});
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  const lines = project.boq ?? [];
  const cell = (step: Step) => step.not_needed ? <span className="prj-step is-skip">n/a</span>
    : <span className={`prj-step${step.done_on ? " is-done" : step.late ? " is-late" : step.planned_on ? " is-planned" : ""}`} title={`${step.label}${step.planned_on ? ` · planned ${step.planned_on}` : ""}${step.done_on ? ` · done ${step.done_on}` : ""}${step.automatic ? " (from the factory)" : ""}`}>{step.done_on ? `✓ ${step.done_on.slice(5)}` : step.planned_on ? step.planned_on.slice(5) : "·"}</span>;
  return <>
    <p className="sp-hint">Each item from order to handover. Dates are month-day; ✓ is done, red is late. Orders mark "ordered" and "paid" themselves, and the factory fills in its own items. Marking an item installed counts it 100% done for the next claim.</p>
    <div className="sp-table-wrap"><table className="sp-table bc-table fac-table prj-schedule" aria-label="Schedule">
      <thead><tr>{project.can_schedule && <th scope="col"><span className="sr-only">Choose</span></th>}<th scope="col">Item</th>{overview.steps.map((step) => <th key={step.id} scope="col">{step.label}</th>)}<th scope="col" className="bc-num">Done %</th></tr></thead>
      <tbody>{lines.map((line) => <tr key={line.id} className={line.late ? "prj-late-row" : undefined}>
        {project.can_schedule && <td><input type="checkbox" aria-label={`Choose ${line.code}`} checked={picked.includes(line.id)} onChange={(event) => setPicked(event.target.checked ? [...picked, line.id] : picked.filter((id) => id !== line.id))} /></td>}
        <th scope="row" className="pw-mono">{line.code}<small>{show(line.area)} · {line.stage}</small></th>
        {line.steps.map((step) => <td key={step.step}>{cell(step)}</td>)}
        <td className="bc-num">{project.can_progress ? <input aria-label={`Done % of ${line.code}`} className="prj-pct" inputMode="decimal" value={progress[line.id] ?? num(line.done_percent)} onChange={(event) => setProgress({ ...progress, [line.id]: event.target.value })} /> : `${num(line.done_percent)}%`}</td>
      </tr>)}</tbody>
    </table></div>
    {project.can_schedule && <div className="fac-inline" aria-label="Update the schedule">
      <span className="fac-hint">{picked.length} items chosen.</span>
      <select aria-label="Step" value={form.step} onChange={(event) => setForm({ ...form, step: event.target.value })}>{overview.steps.map((step) => <option key={step.id} value={step.id}>{step.label}</option>)}</select>
      <label className="fac-check">Planned <input type="date" value={form.planned_on} onChange={(event) => setForm({ ...form, planned_on: event.target.value })} /></label>
      <label className="fac-check">Done <input type="date" max={overview.today} value={form.done_on} onChange={(event) => setForm({ ...form, done_on: event.target.value })} /></label>
      <button className="button button-dark" type="button" disabled={busy || !picked.length || (!form.planned_on && !form.done_on)} onClick={() => void send<Project>(`/api/projects/${project.id}/schedule/`, "POST",
        { steps: [{ line_ids: picked, step: form.step, ...(form.planned_on ? { planned_on: form.planned_on } : {}), ...(form.done_on ? { done_on: form.done_on } : {}) }] }, () => "The schedule is updated.").then((ok) => ok && setPicked([]))}>Save dates</button>
      {project.can_progress && Object.keys(progress).length > 0 && <button className="button button-outline" type="button" disabled={busy} onClick={() => void send<Project>(`/api/projects/${project.id}/schedule/`, "POST", { progress }, () => "Progress is saved.").then((ok) => ok && setProgress({}))}>Save progress %</button>}
    </div>}
    {error && <div className="alert" role="alert">{error}</div>}
  </>;
}

export function CashTable({ cash, label }: { cash: Cash; label: string }) {
  const max = Math.max(1, ...cash.weeks.map((week) => Math.max(Number(week.in_actual) + Number(week.in_planned), Number(week.out_actual) + Number(week.out_planned))));
  return <>
    {(Number(cash.overdue_in) > 0 || Number(cash.overdue_out) > 0) && <p className="fac-alert-line" role="note">Overdue: QAR {qar(cash.overdue_in)} expected from clients, QAR {qar(cash.overdue_out)} due to suppliers (shown in this week).</p>}
    <div className="sp-table-wrap"><table className="sp-table bc-table fac-table prj-cash" aria-label={label}>
      <thead><tr><th scope="col">Week of</th><th scope="col" className="bc-num">Cash in</th><th scope="col" className="bc-num">Cash out</th><th scope="col">In / out</th><th scope="col" className="bc-num">Net</th><th scope="col" className="bc-num">Running</th></tr></thead>
      <tbody><tr className="prj-past"><th scope="row">Before {cash.weeks[0]?.week}<small>paid and received earlier</small></th><td colSpan={4} /><td className={`bc-num${Number(cash.opening) < 0 ? " fac-late" : ""}`}>{qar(cash.opening)}</td></tr>
        {cash.weeks.map((week) => {
        const cashIn = Number(week.in_actual) + Number(week.in_planned);
        const cashOut = Number(week.out_actual) + Number(week.out_planned);
        return <tr key={week.week} className={week.current ? "prj-now" : week.past ? "prj-past" : undefined}>
          <th scope="row">{week.week}{week.current && <small>this week</small>}</th>
          <td className="bc-num">{cashIn ? qar(cashIn) : "–"}{Number(week.in_planned) > 0 && <small>{qar(week.in_planned)} expected</small>}</td>
          <td className="bc-num">{cashOut ? qar(cashOut) : "–"}{Number(week.out_planned) > 0 && <small>{qar(week.out_planned)} due</small>}</td>
          <td><span className="prj-flow"><span className="prj-in" style={{ width: `${cashIn * 100 / max}%` }} /></span><span className="prj-flow"><span className="prj-out" style={{ width: `${cashOut * 100 / max}%` }} /></span>
            {week.items.length > 0 && <small>{week.items.slice(0, 3).map((item) => `${item.kind === "in" ? "+" : "−"} ${show(item.text)} ${qar(item.amount)}`).join(" · ")}{week.items.length > 3 && ` · +${week.items.length - 3} more`}</small>}</td>
          <td className={`bc-num${Number(week.net) < 0 ? " fac-late" : ""}`}>{qar(week.net)}</td>
          <td className={`bc-num${Number(week.running) < 0 ? " fac-late" : ""}`}>{qar(week.running)}</td>
        </tr>;
      })}</tbody>
    </table></div>
  </>;
}

function CashTab({ project, overview, csrfToken, onDone }: TabProps) {
  const [form, setForm] = useState({ kind: "advance", claim_id: "", amount: "", date: overview.today, reference: "", account_id: "" });
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  const claims = (project.claims ?? []).filter((claim) => claim.can_receive);
  return <>
    {project.cash && <CashTable cash={project.cash} label="Cash in and out by week" />}
    {(project.receipts ?? []).length > 0 && block("Received from the client", <ul className="fac-list">{(project.receipts ?? []).map((row) => <li key={row.id}>{row.received_on} · {row.kind_label}{row.claim && ` ${row.claim}`} · QAR {qar(row.amount)} <small>{row.reference} · {show(row.account)}</small></li>)}</ul>)}
    {project.can_receive && <div className="fac-inline" aria-label="Record money received">
      <select aria-label="Received for" value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}><option value="advance">Advance</option><option value="claim">Claim payment</option></select>
      {form.kind === "claim" && <select aria-label="Claim" value={form.claim_id} onChange={(event) => setForm({ ...form, claim_id: event.target.value })}><option value="">Which invoice…</option>{claims.map((claim) => <option key={claim.id} value={claim.id}>{claim.invoice_number} · {qar(Number(claim.net) - Number(claim.received))} left</option>)}</select>}
      <select aria-label="Received into" value={form.account_id} onChange={(event) => setForm({ ...form, account_id: event.target.value })}><option value="">Into…</option>{overview.accounts.map((account) => <option key={account.id} value={account.id}>{show(account.name)}</option>)}</select>
      <input aria-label="Date received" type="date" max={overview.today} value={form.date} onChange={(event) => setForm({ ...form, date: event.target.value })} />
      <input aria-label="Amount received" className="fac-qty" inputMode="decimal" placeholder="QAR" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} />
      <input aria-label="Receipt reference" placeholder="Cheque or transfer number" value={form.reference} onChange={(event) => setForm({ ...form, reference: event.target.value })} />
      <button className="button button-dark" type="button" disabled={busy || !form.account_id || !form.amount || !form.reference} onClick={() => void send<Project>(`/api/projects/${project.id}/receipts/`, "POST", { ...form, account_id: Number(form.account_id), claim_id: form.claim_id ? Number(form.claim_id) : null }, () => "Money received is recorded.").then((ok) => ok && setForm({ ...form, amount: "", reference: "" }))}>Record money received</button>
    </div>}
    {error && <div className="alert" role="alert">{error}</div>}
  </>;
}

function VariationsTab({ project, overview, csrfToken, onDone }: TabProps) {
  const [form, setForm] = useState({ description: "", value: "", cost_estimate: "" });
  const [sign, setSign] = useState({ client_reference: "", client_signed_on: overview.today, note: "" });
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  const act = (id: number, action: string, message: string) => void send<Project>(`/api/projects/variations/${id}/`, "POST", { action, ...sign }, () => message);
  return <>
    {(project.variations ?? []).length === 0 ? <p className="sp-empty">No variations.</p> : <ul className="fac-list">{(project.variations ?? []).map((row) => <li key={row.id}>
      <span className={`sp-status sp-status-${TONE[row.status]}`}>{row.status_label}</span> <strong>{row.number}</strong> {show(row.description)} · QAR {qar(row.value)} <small>cost estimate QAR {qar(row.cost_estimate)} · asked by {row.requested_by}{row.client_reference && ` · client ref ${row.client_reference}`}{row.decision_note && ` · ${show(row.decision_note)}`}</small>
      <span className="lv-actions">
        {row.can_decide && <><button className="button button-dark" type="button" disabled={busy} onClick={() => act(row.id, "approve", `${row.number} is approved.`)}>Approve</button><button className="button button-outline" type="button" disabled={busy || !sign.note.trim()} onClick={() => act(row.id, "reject", `${row.number} is rejected.`)}>Reject</button></>}
        {row.can_sign && <button className="button button-outline" type="button" disabled={busy || !sign.client_reference} onClick={() => act(row.id, "client_signed", `${row.number} is signed by the client.`)}>Client signed</button>}
      </span>
    </li>)}</ul>}
    {(project.variations ?? []).some((row) => row.can_decide || row.can_sign) && <div className="fac-inline">
      <input aria-label="Client's signed reference" placeholder="Client's signed reference" value={sign.client_reference} onChange={(event) => setSign({ ...sign, client_reference: event.target.value })} />
      <input aria-label="Date the client signed" type="date" max={overview.today} value={sign.client_signed_on} onChange={(event) => setSign({ ...sign, client_signed_on: event.target.value })} />
      <input aria-label="Reason, to reject" placeholder="Reason, to reject" value={sign.note} onChange={(event) => setSign({ ...sign, note: event.target.value })} />
    </div>}
    {project.can_vary && <div className="fac-inline" aria-label="New variation">
      <input aria-label="Variation description" placeholder="What changes" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
      <input aria-label="Variation value" className="fac-qty" inputMode="decimal" placeholder="Value (− to omit)" value={form.value} onChange={(event) => setForm({ ...form, value: event.target.value })} />
      <input aria-label="Variation cost estimate" className="fac-qty" inputMode="decimal" placeholder="Cost" value={form.cost_estimate} onChange={(event) => setForm({ ...form, cost_estimate: event.target.value })} />
      <button className="button button-dark" type="button" disabled={busy || !form.description || !form.value} onClick={() => void send<Project>(`/api/projects/${project.id}/variations/`, "POST", form, () => "The variation waits for the Construction Manager.").then((ok) => ok && setForm({ description: "", value: "", cost_estimate: "" }))}>Add variation</button>
    </div>}
    {error && <div className="alert" role="alert">{error}</div>}
  </>;
}

function ClaimsTab({ project, overview, csrfToken, onDone }: TabProps) {
  const [form, setForm] = useState({ period_end: overview.today, date: overview.today, note: "", consultant_reference: "", certified: "" });
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  const act = (id: number, action: string, message: string) => void send<Project>(`/api/projects/claims/${id}/`, "POST", { action, date: form.date, note: form.note, consultant_reference: form.consultant_reference, certified: form.certified }, () => message);
  const following = (project.claims ?? []).some((claim) => claim.can_decide || claim.can_invoice || claim.can_submit || claim.can_certify);
  return <>
    {(project.claims ?? []).length === 0 ? <p className="sp-empty">No claims yet. Record progress on the Schedule, then prepare the month's claim.</p> : <ul className="fac-list">{(project.claims ?? []).map((claim) => <li key={claim.id}>
      <span className={`sp-status sp-status-${TONE[claim.status]}`}>{claim.status_label}</span> <strong>{claim.number}</strong> {claim.kind_label} to {claim.period_end}{claim.invoice_number && ` · ${claim.invoice_number} due ${claim.due_on}`}
      {claim.submitted_on && <small>Sent to the consultant {claim.submitted_on}{claim.certified_on && `, certified ${claim.certified_on}`}{claim.consultant_reference && ` · ${claim.consultant_reference}`}{claim.requested_gross !== claim.gross && ` · asked QAR ${qar(claim.requested_gross)}, certified QAR ${qar(claim.gross)}`}</small>}
      <small>{claim.kind === "progress" ? `Work this claim QAR ${qar(claim.gross)} − retention ${qar(claim.retention)} − advance ${qar(claim.advance_recovery)} = ` : "Retention released = "}<strong>QAR {qar(claim.net)}</strong> · received {qar(claim.received)} · prepared by {claim.prepared_by}{claim.approved_by && `, approved by ${claim.approved_by}`}</small>
      <span className="lv-actions">
        {claim.can_decide && <><button className="button button-dark" type="button" disabled={busy} onClick={() => act(claim.id, "approve", `${claim.number} is approved.`)}>Approve</button><button className="button button-outline" type="button" disabled={busy || !form.note.trim()} onClick={() => act(claim.id, "reject", `${claim.number} is rejected.`)}>Reject</button></>}
        {claim.can_submit && <button className="button button-dark" type="button" disabled={busy} onClick={() => act(claim.id, "submit", `${claim.number} is with the consultant.`)}>Sent to the consultant</button>}
        {claim.can_certify && <><button className="button button-dark" type="button" disabled={busy || !form.consultant_reference} onClick={() => act(claim.id, "certify", `${claim.number} is certified; Finance can invoice it.`)}>Consultant certified</button><button className="button button-outline" type="button" disabled={busy || !form.note.trim()} onClick={() => act(claim.id, "returned", `${claim.number} was returned by the consultant.`)}>Consultant returned it</button></>}
        {claim.can_invoice && <button className="button button-dark" type="button" disabled={busy} onClick={() => act(claim.id, "invoice", `${claim.number} is invoiced.`)}>Invoice it</button>}
        {claim.can_withdraw && <button className="button button-quiet" type="button" disabled={busy} onClick={() => act(claim.id, "withdraw", `${claim.number} is withdrawn.`)}>Withdraw</button>}
      </span>
    </li>)}</ul>}
    <div className="fac-inline">
      {(project.can_claim || project.can_release) && <input aria-label="Claim date" type="date" value={form.period_end} onChange={(event) => setForm({ ...form, period_end: event.target.value })} />}
      {project.can_claim && <button className="button button-dark" type="button" disabled={busy} onClick={() => void send<Project>(`/api/projects/${project.id}/claims/`, "POST", { kind: "progress", period_end: form.period_end }, () => "The progress claim waits for the Construction Manager.")}>Prepare progress claim</button>}
      {project.can_release && <button className="button button-outline" type="button" disabled={busy} onClick={() => void send<Project>(`/api/projects/${project.id}/claims/`, "POST", { kind: "retention", period_end: form.period_end }, () => "The retention release waits for the Construction Manager.")}>Release retention</button>}
      {following && <><input aria-label="Date" type="date" max={overview.today} value={form.date} onChange={(event) => setForm({ ...form, date: event.target.value })} />
        {(project.claims ?? []).some((claim) => claim.can_submit || claim.can_certify) && <><input aria-label="Consultant reference" placeholder="Submittal or certificate number" value={form.consultant_reference} onChange={(event) => setForm({ ...form, consultant_reference: event.target.value })} />
          <input aria-label="Amount certified" className="fac-qty" inputMode="decimal" placeholder="Certified QAR (if less)" value={form.certified} onChange={(event) => setForm({ ...form, certified: event.target.value })} /></>}
        <input aria-label="Reason" placeholder="Reason, to reject or return" value={form.note} onChange={(event) => setForm({ ...form, note: event.target.value })} /></>}
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
  </>;
}

function FactoryTab({ project, overview, csrfToken, onDone }: TabProps) {
  const joinery = (project.boq ?? []).filter((line) => line.source === "factory");
  const waiting = joinery.filter((line) => !line.factory_job);
  const [picked, setPicked] = useState<number[]>(waiting.map((line) => line.id));
  const [due, setDue] = useState(project.handover_due);
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  return <>
    <p className="sp-hint">Items in the Joinery category are made in our factory. Send them as one job order; the factory plans it, takes the materials from the warehouse and records its cost here.</p>
    {(project.factory_jobs ?? []).length > 0 && <ul className="fac-list">{(project.factory_jobs ?? []).map((job) => <li key={job.id}><strong>{job.number}</strong> {job.status_label} · due {job.due_date} · {job.lines.join(", ")}<small>cost so far QAR {qar(job.cost)}{Number(job.estimate) > 0 && ` of ${qar(job.estimate)} estimated`}{job.slips.length > 0 && ` · material slips ${job.slips.map((slip) => `${slip.number} (${slip.status_label})`).join(", ")}`}</small></li>)}</ul>}
    {joinery.length === 0 ? <p className="sp-empty">No joinery items in this BOQ.</p> : waiting.length === 0 ? <p className="sp-empty">All joinery items are with the factory.</p>
      : project.can_send_to_factory && <div className="fac-form">
        <fieldset className="fac-lines"><legend>Joinery items not yet with the factory</legend>
          {waiting.map((line) => <label key={line.id} className="fac-check"><input type="checkbox" checked={picked.includes(line.id)} onChange={(event) => setPicked(event.target.checked ? [...picked, line.id] : picked.filter((id) => id !== line.id))} /> <span className="pw-mono">{line.code}</span> {show(line.area)} · {show(line.description).split("\n")[0].slice(0, 60)} · {num(line.quantity)} {line.unit}</label>)}
        </fieldset>
        <div className="fac-inline"><label className="fac-check">Factory must finish by <input type="date" min={overview.today} value={due} onChange={(event) => setDue(event.target.value)} /></label>
          <button className="button button-dark" type="button" disabled={busy || !picked.length || !due} onClick={() => void send<Project>(`/api/projects/${project.id}/factory/`, "POST", { line_ids: picked, due_date: due }, () => "A factory job order is open for these items.")}>Send to the factory</button></div>
      </div>}
    {error && <div className="alert" role="alert">{error}</div>}
  </>;
}

function ProjectBonds({ project, overview, csrfToken, onDone }: TabProps) {
  const blank = { kind: "performance", reference: "", issuer: "", amount: "", issued_on: "", expires_on: "" };
  const [form, setForm] = useState(blank);
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  return <>
    {(project.bonds ?? []).length === 0 ? <p className="sp-empty">No bonds or guarantees for this project.</p> : <ul className="fac-list">{(project.bonds ?? []).map((bond) => <li key={bond.id}>
      <span className={`sp-status sp-status-${TONE[bond.status]}`}>{bond.status_label}</span> {bond.kind_label} {bond.reference} · QAR {qar(bond.amount)}{bond.expires_on && ` · valid to ${bond.expires_on}`}
      {bond.expiring && <small className="fac-late">expires in {bond.days_left} days</small>}{bond.expired && <small className="fac-late">past its date and not returned</small>}<small>{show(bond.issuer)}</small></li>)}</ul>}
    {project.can_bond && <div className="fac-inline" aria-label="Add a bond">
      <select aria-label="Kind" value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}>
        <option value="performance">Performance bond</option><option value="advance">Advance payment guarantee</option><option value="retention">Retention guarantee</option><option value="cheque">Guarantee cheque</option><option value="warranty">Warranty</option></select>
      <input aria-label="Bond number" placeholder="Bond or cheque number" value={form.reference} onChange={(event) => setForm({ ...form, reference: event.target.value })} />
      <input aria-label="Bank" placeholder="Bank or issuer" value={form.issuer} onChange={(event) => setForm({ ...form, issuer: event.target.value })} />
      <input aria-label="Amount" className="fac-qty" inputMode="decimal" placeholder="QAR" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} />
      <label className="fac-check">Valid to <input type="date" value={form.expires_on} onChange={(event) => setForm({ ...form, expires_on: event.target.value })} /></label>
      <button className="button button-dark" type="button" disabled={busy || !form.reference} onClick={() => void send("/api/projects/bonds/", "POST", { ...form, project_id: project.id }, () => "The bond is recorded.").then((ok) => ok && setForm(blank))}>Add</button>
    </div>}
    {error && <div className="alert" role="alert">{error}</div>}
  </>;
}

function Setup({ overview, csrfToken, onDone }: { overview: Overview; csrfToken: string; onDone: Done }) {
  const [form, setForm] = useState(overview.settings);
  const { busy, error, send } = useAction(csrfToken, overview.unit.id, onDone);
  if (!overview.is_master_admin) return <p className="sp-empty">Only the Master Admin changes the project setup.</p>;
  const save = (extra: Record<string, unknown> = {}) => void send("/api/projects/settings/", "PUT",
    { advance_percent: form.advance_percent, retention_percent: form.retention_percent, retention_release: form.retention_release, warranty_days: form.warranty_days, bond_reminder_days: form.bond_reminder_days, claim_due_days: form.claim_due_days, ...extra },
    () => !("is_live" in extra) ? "Project setup is saved." : extra.is_live ? "Projects & Fit-Out is live for everyone with a project permission." : "Projects & Fit-Out is hidden again; only you can see it.");
  return <>
    <section className={`fac-switch ${overview.settings.is_live ? "is-live" : ""}`} aria-label="Go-live switch">
      <div><strong>{overview.settings.is_live ? "Projects & Fit-Out is live." : "Projects & Fit-Out is not live yet."}</strong>
        <p>{overview.settings.is_live ? "Everyone with a project permission sees it." : "Only you (Master Admin) can see it. Turn it on when the projects team is ready."}</p></div>
      <button className={`button ${overview.settings.is_live ? "button-outline" : "button-dark"}`} type="button" disabled={busy} onClick={() => save({ is_live: !overview.settings.is_live })}>{overview.settings.is_live ? "Hide it again" : "Make it live"}</button>
    </section>
    <form className="sp-form fac-form" aria-label="Client terms" onSubmit={(event) => { event.preventDefault(); save(); }}>
      <h3>Default client terms for new projects</h3>
      <div className="sp-fields fac-grid">
        <label>Down payment (% of the contract)<input inputMode="decimal" value={form.advance_percent} onChange={(event) => setForm({ ...form, advance_percent: event.target.value })} /></label>
        <label>Retention (%)<input inputMode="decimal" value={form.retention_percent} onChange={(event) => setForm({ ...form, retention_percent: event.target.value })} /></label>
        <label>Retention is paid<select value={form.retention_release} onChange={(event) => setForm({ ...form, retention_release: event.target.value })}><option value="handover">At handover</option><option value="warranty">After the warranty period</option></select></label>
        <label>Warranty period (days)<input inputMode="numeric" value={String(form.warranty_days)} onChange={(event) => setForm({ ...form, warranty_days: Number(event.target.value) || 0 })} /></label>
        <label>Days a client has to pay a claim<input inputMode="numeric" value={String(form.claim_due_days)} onChange={(event) => setForm({ ...form, claim_due_days: Number(event.target.value) || 0 })} /></label>
        <label>Remind about bonds (days before expiry)<input inputMode="numeric" value={String(form.bond_reminder_days)} onChange={(event) => setForm({ ...form, bond_reminder_days: Number(event.target.value) || 0 })} /></label>
      </div>
      {error && <div className="alert" role="alert">{error}</div>}
      <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy}>Save</button></div>
    </form>
  </>;
}

const TITLES: Record<ProjectsView, [string, string]> = {
  main: ["Projects", "Signed fit-out projects opened from their BOQ: the budget item by item, orders and approvals, the schedule from order to handover, cash in and out, variations, claims and the factory's joinery."],
  tenders: ["Tenders", "Every tender sent to a client, by status: submitted, won, lost or cancelled, with the client's contact, quotation, value and the latest remarks."],
  bonds: ["Bonds and guarantees", "Bid bonds, performance bonds, advance payment guarantees, guarantee cheques and warranties, with their amount and validity. Expiring ones are flagged ahead of time."],
  cash: ["Projects cash flow", "Cash in from clients and cash out to suppliers and subcontractors across all open projects, week by week: what was paid and what is expected."],
  setup: ["Project setup", "Default client terms, the bond reminder, and the switch that makes this section live."],
};

export default function Projects({ selectedUnitId, csrfToken, view = "main" }: { selectedUnitId: number | null; csrfToken: string; view?: ProjectsView }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [revision, setRevision] = useState(0);
  const load = useCallback(async () => {
    if (selectedUnitId === null) { setError("Choose a business unit to see its projects."); return; }
    try { setOverview(await call<Overview>(`/api/projects/?unit=${selectedUnitId}`)); setError(""); }
    catch (caught) { setOverview(null); setError(errorText(caught, "Projects could not be loaded.")); }
  }, [selectedUnitId]);
  useEffect(() => { void load(); }, [load]);
  const done = useMemo(() => (message: string) => { setNotice(message); setRevision((value) => value + 1); void load(); }, [load]);
  const [title, intro] = TITLES[view];
  return <section className="content-card supplier-payments bank-cash staff-loans-page factory-page projects-page" aria-labelledby="prj-title">
    <header className="sp-head">
      <span className="eyebrow">PROJECTS & FIT-OUT · {title.toUpperCase()} · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="prj-title">{title}</h2>
      <p>{intro}</p>
    </header>
    {overview && !overview.settings.is_live && <p className="fac-preview" role="note">Preview: this section is not live yet, so only the Master Admin can see it. The Phase 1 teams are not affected.</p>}
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{show(notice)}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading…</p>}
    {overview && view === "main" && <ProjectList key={revision > 0 ? "list" : "first"} overview={overview} csrfToken={csrfToken} onDone={done} />}
    {overview && view === "tenders" && <TendersView unitId={overview.unit.id} csrfToken={csrfToken} customers={overview.customers} />}
    {overview && view === "bonds" && <BondsView unitId={overview.unit.id} csrfToken={csrfToken} />}
    {overview && view === "cash" && <CashTable cash={overview.cash} label="Projects cash flow by week" />}
    {overview && view === "setup" && <Setup key={revision} overview={overview} csrfToken={csrfToken} onDone={done} />}
    {overview && view === "main" && <p className="sp-hint">{overview.policy}</p>}
  </section>;
}
