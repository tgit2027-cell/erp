import { useCallback, useEffect, useState } from "react";
import { call, day, errorText, num, show, useSender } from "./FactoryPlanning";

type PlanStage = {
  id: number; kind: string; label: string; section: string; status: string; status_label: string; kept: boolean; changed: boolean; by_hand: boolean;
  now: { start: string; end: string; lead: string }; plan: { start: string; end: string; lead_id: number | null; lead: string }; reasons: string[];
};
type PlanLine = { id: number; code: string; description: string; floor: string; tags: string[]; learned_from: number; stages: PlanStage[] };
type Brief = { start_on: string; floor_order: string[]; first_codes: string[]; finish_by: string; notes: string; given_by: string; given_at: string };
type Waiting = { sku: string; name: string; ready_on: string; source: string };
type PlanJob = {
  id: number; number: string; project: string; status: string; status_label: string; due_date: string; finish_by: string; finishes_on: string;
  late_by: number; materials_ready_on: string; waiting_for: Waiting[]; not_ordered: { sku: string; name: string; quantity: string }[];
  planned: boolean; brief: Brief | null; lines: PlanLine[];
  questions?: { floors: string[]; codes: string[]; suggested: { start_on: string; start_why: string; floor_order: string[]; finish_by: string; first_codes: string[] } };
};
type Coming = { kind: string; order_id: number | null; supplier: string; status: string; status_label: string; ordered_on: string; expected_on: string; expected_given: boolean; overdue: boolean; payment: string; payment_label: string; quantity: string };
type PlanMaterial = { variant_id: number; sku: string; name: string; unit: string; needed: string; free: string; not_ordered: string; covered_by_stock: boolean; coming: Coming[]; jobs: { job: string; ready_on: string }[] };
type Move = { job: string; code: string; stage: string; from: string; to: string; lead: string; why: string };
type Run = { id: number; ran_on: string; ran_at: string; by: string; reason: string; changes: number; moves: Move[] };
type Plan = {
  today: string; jobs: PlanJob[]; materials: PlanMaterial[]; runs: Run[]; can_plan: boolean;
  summary: { jobs: number; to_brief: number; changes: number; late_jobs: { number: string; late_by: number }[]; waiting_materials: number; not_ordered: number };
  rules: { order_lead_days: number; stages_per_person: number; ask_days: number };
};

const time = (value: string) => new Date(value).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** The planner's questions before it plans a job: start date, floor order, JDs first, finish-by date, notes. */
function BriefForm({ job, unitId, csrfToken, onDone, onCancel }: { job: PlanJob; unitId: number; csrfToken: string; onDone: (message: string) => void; onCancel?: () => void }) {
  const floors = job.questions?.floors ?? job.brief?.floor_order ?? [];
  const codes = job.questions?.codes ?? job.lines.map((line) => line.code);
  const suggested = job.questions?.suggested;
  const [form, setForm] = useState({
    start_on: job.brief?.start_on ?? suggested?.start_on ?? "",
    floor_order: job.brief?.floor_order.length ? job.brief.floor_order : (suggested?.floor_order ?? floors),
    first_codes: job.brief?.first_codes ?? [],
    finish_by: job.brief?.finish_by ?? suggested?.finish_by ?? job.due_date,
    notes: job.brief?.notes ?? "",
  });
  const { busy, error, send } = useSender(unitId, csrfToken, onDone);
  const move = (index: number, by: number) => {
    const order = [...form.floor_order];
    const [found] = order.splice(index, 1);
    order.splice(index + by, 0, found);
    setForm({ ...form, floor_order: order });
  };
  const toggle = (code: string) => setForm({ ...form, first_codes: form.first_codes.includes(code) ? form.first_codes.filter((found) => found !== code) : [...form.first_codes, code] });
  return <form className="fpl-brief" onSubmit={(event) => { event.preventDefault(); void send(`/api/factory/orders/${job.id}/plan-brief/`, form, `${job.number} is planned.`); }}>
    <p className="fpl-ask">Before I plan <strong>{job.number}</strong>{job.project && <> · {show(job.project)}</>}, a few questions:</p>
    <label>1. When should it start?<input type="date" aria-label={`${job.number} start date`} value={form.start_on} min={new Date().toISOString().slice(0, 10)} required onChange={(event) => setForm({ ...form, start_on: event.target.value })} />
      {suggested && <small>Suggested {day(suggested.start_on)}: {suggested.start_why}</small>}</label>
    <div className="fpl-field"><span>2. Which floor first?</span>
      {floors.length ? <ol className="fpl-floors">{form.floor_order.map((floor, index) => <li key={floor}><span>{floor}</span>
        <button type="button" className="button button-quiet" aria-label={`Move ${floor} up`} disabled={index === 0} onClick={() => move(index, -1)}>↑</button>
        <button type="button" className="button button-quiet" aria-label={`Move ${floor} down`} disabled={index === form.floor_order.length - 1} onClick={() => move(index, 1)}>↓</button></li>)}</ol>
        : <small>The BOQ gives no floors, so the JDs go in BOQ order.</small>}</div>
    <div className="fpl-field"><span>3. Any JDs to do before the others?</span>
      <div className="fpl-codes">{codes.map((code) => <label key={code} className="fp-inline fp-check"><input type="checkbox" checked={form.first_codes.includes(code)} onChange={() => toggle(code)} /> {code}</label>)}</div></div>
    <label>4. Must finish by<input type="date" aria-label={`${job.number} finish by`} value={form.finish_by} onChange={(event) => setForm({ ...form, finish_by: event.target.value })} />
      <small>Due date {day(job.due_date)}.</small></label>
    <label>5. Anything else the planner should know?<textarea aria-label={`${job.number} notes`} rows={2} maxLength={500} value={form.notes} placeholder="e.g. the lobby doors are needed for the site visit" onChange={(event) => setForm({ ...form, notes: event.target.value })} /></label>
    <div className="fpl-buttons"><button type="submit" className="button button-dark" disabled={busy || !form.start_on}>{job.brief ? "Plan again with these answers" : "Plan this job"}</button>
      {onCancel && <button type="button" className="button button-quiet" onClick={onCancel}>Back</button>}</div>
    {error && <small className="fp-error" role="alert">{error}</small>}
  </form>;
}

function PlannedJob({ job, canPlan, unitId, csrfToken, onDone }: { job: PlanJob; canPlan: boolean; unitId: number; csrfToken: string; onDone: (message: string) => void }) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  return <li className="fpl-job">
    <div className="fpl-job-head">
      <button type="button" className="fp-toggle" aria-expanded={open} onClick={() => setOpen(!open)}><strong>{job.number}</strong> {show(job.project)}</button>
      <span className={job.late_by ? "fpl-late" : "fpl-ok"}>{job.finishes_on ? `Finishes ${day(job.finishes_on)}` : "Not planned"}{job.late_by ? `, ${job.late_by} days after ${day(job.finish_by)}` : ` · finish by ${day(job.finish_by)}`}</span>
      {job.waiting_for.length > 0 && <small>Materials by {day(job.materials_ready_on)}</small>}
      {canPlan && !editing && <button type="button" className="button button-quiet" onClick={() => setEditing(true)}>Change answers</button>}
    </div>
    {job.brief && <small className="fpl-brief-note">Starts {day(job.brief.start_on)}{job.brief.floor_order.length > 0 && <> · floors: {job.brief.floor_order.join(", ")}</>}{job.brief.first_codes.length > 0 && <> · first: {job.brief.first_codes.join(", ")}</>}{job.brief.notes && <> · “{job.brief.notes}”</>} · answered by {show(job.brief.given_by)}</small>}
    {job.not_ordered.length > 0 && <small className="fpl-warn">Not ordered yet: {job.not_ordered.map((row) => `${show(row.name)} (${num(row.quantity)})`).join(", ")}</small>}
    {editing && <BriefForm job={job} unitId={unitId} csrfToken={csrfToken} onDone={(message) => { setEditing(false); onDone(message); }} onCancel={() => setEditing(false)} />}
    {open && <div className="sp-table-wrap fpl-why"><table className="sp-table bc-table fp-mini fpl-table"><thead><tr><th>JD</th><th>Stage</th><th>Dates</th><th>Lead</th><th>Why</th></tr></thead><tbody>
      {job.lines.flatMap((line) => line.stages.map((stage, index) => <tr key={stage.id}>
        <td>{index === 0 && <><strong>{line.code}</strong>{line.floor && <small>{line.floor}</small>}</>}</td>
        <td>{stage.label}{stage.kept && <small>{stage.by_hand ? "set by hand" : stage.status_label.toLowerCase()}</small>}</td>
        <td>{day(stage.plan.start)} – {day(stage.plan.end)}{stage.changed && stage.now.start && <small>was {day(stage.now.start)}</small>}</td>
        <td>{show(stage.plan.lead) || "–"}</td>
        <td><small>{stage.reasons.map(show).join(" ")}</small></td>
      </tr>))}</tbody></table></div>}
  </li>;
}

function ArrivalInput({ coming, unitId, csrfToken, onDone }: { coming: Coming; unitId: number; csrfToken: string; onDone: (message: string) => void }) {
  const { busy, error, send } = useSender(unitId, csrfToken, onDone);
  return <><input type="date" aria-label={`Expected arrival of order ${coming.order_id}`} value={coming.expected_on} disabled={busy}
    onChange={(event) => void send("/api/factory/arrivals/", { order_id: coming.order_id, expected_on: event.target.value }, "Expected arrival saved; the plan follows it.")} />
    {error && <small className="fp-error" role="alert">{error}</small>}</>;
}

export function PlannerPanel({ unitId, csrfToken, onChanged }: { unitId: number; csrfToken: string; onChanged: () => void }) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState<"jobs" | "moves" | "materials">("jobs");
  const load = useCallback(async () => {
    try { setPlan(await call<Plan>(`/api/factory/plan/?unit=${unitId}`)); setError(""); }
    catch (caught) { setError(errorText(caught, "The planner could not be loaded.")); }
  }, [unitId]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); void load(); onChanged(); };
  const { busy, send } = useSender(unitId, csrfToken, done);
  if (!plan) return error ? <div className="alert" role="alert">{error}</div> : null;
  const waiting = plan.jobs.filter((job) => !job.planned);
  const planned = plan.jobs.filter((job) => job.planned);
  const last = plan.runs[0];
  const moves = plan.runs.flatMap((run) => run.moves.map((move) => ({ ...move, run })));
  const watched = plan.materials.filter((row) => !row.covered_by_stock);
  return <section className="fpl" aria-label="Factory planner">
    <div className="fpl-head">
      <button type="button" className="fp-toggle" aria-expanded={open} onClick={() => setOpen(!open)}><strong>Factory planner</strong></button>
      <span>{planned.length} job{planned.length === 1 ? "" : "s"} planned{waiting.length > 0 && <>, <strong>{waiting.length} waiting for your answers</strong></>}
        {plan.summary.late_jobs.length > 0 && <>, <span className="fpl-late">{plan.summary.late_jobs.length} would finish late</span></>}</span>
      {last && <small>Last planned {time(last.ran_at)} by {show(last.by)}{last.changes ? ` · ${last.changes} stage${last.changes === 1 ? "" : "s"} planned or moved` : " · nothing moved"}</small>}
      {plan.can_plan && planned.length > 0 && <button type="button" className="button button-outline" disabled={busy} onClick={() => void send("/api/factory/plan/", {}, "Planned again.")}>Plan again now</button>}
    </div>
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {error && <div className="alert" role="alert">{error}</div>}
    {open && <>
      <p className="fpl-rules">It re-plans by itself every morning from past work, stock, purchase orders and each person's load. Dates you change by hand stay where they are. Rules: up to {plan.rules.stages_per_person} stages per person at once; goods arrive {plan.rules.order_lead_days} days after ordering unless purchasing gives a date.</p>
      {waiting.length > 0 && <div className="fpl-waiting">{waiting.map((job) => plan.can_plan
        ? <BriefForm key={job.id} job={job} unitId={unitId} csrfToken={csrfToken} onDone={done} />
        : <p key={job.id} className="fpl-ask">{job.number} is waiting for a factory admin to answer the planner's questions.</p>)}</div>}
      <div className="fp-views" role="tablist" aria-label="Planner view">
        <button type="button" role="tab" aria-selected={tab === "jobs"} className={tab === "jobs" ? "is-on" : undefined} onClick={() => setTab("jobs")}>Planned jobs</button>
        <button type="button" role="tab" aria-selected={tab === "moves"} className={tab === "moves" ? "is-on" : undefined} onClick={() => setTab("moves")}>What moved ({moves.length})</button>
        <button type="button" role="tab" aria-selected={tab === "materials"} className={tab === "materials" ? "is-on" : undefined} onClick={() => setTab("materials")}>Materials on the way ({watched.length})</button>
      </div>
      {tab === "jobs" && (planned.length ? <ul className="fpl-jobs">{planned.map((job) => <PlannedJob key={job.id} job={job} canPlan={plan.can_plan} unitId={unitId} csrfToken={csrfToken} onDone={done} />)}</ul>
        : <p className="sp-empty">No job is planned yet. Answer the questions above and the planner does the rest.</p>)}
      {tab === "moves" && (moves.length ? <div className="sp-table-wrap"><table className="sp-table bc-table fp-mini fpl-table"><thead><tr><th>When</th><th>JD</th><th>Stage</th><th>Was</th><th>Now</th><th>Why</th></tr></thead><tbody>
        {moves.slice(0, 60).map((move, index) => <tr key={`${move.run.id}-${index}`}><td><small>{time(move.run.ran_at)}<br />{show(move.run.by)}</small></td>
          <td>{move.job} {move.code}</td><td>{move.stage}</td><td>{move.from}</td><td><strong>{move.to}</strong>{move.lead && <small>{show(move.lead)}</small>}</td><td><small>{show(move.why)}</small></td></tr>)}
      </tbody></table></div> : <p className="sp-empty">Nothing has moved since the jobs were planned.</p>)}
      {tab === "materials" && (watched.length ? <div className="sp-table-wrap"><table className="sp-table bc-table fp-mini fpl-table"><thead><tr><th>Material</th><th>Needed</th><th>Free</th><th>On the way</th><th>Not ordered</th></tr></thead><tbody>
        {watched.map((row) => <tr key={row.variant_id}><td><strong>{show(row.name)}</strong><small>{row.sku} · {row.jobs.map((found) => `${found.job} by ${day(found.ready_on)}`).join(", ")}</small></td>
          <td>{num(row.needed)} {row.unit}</td><td>{num(row.free)}</td>
          <td>{row.coming.length ? row.coming.map((coming, index) => <div key={index} className="fpl-coming">
            <span>{num(coming.quantity)} · {coming.status_label}{coming.supplier && ` from ${show(coming.supplier)}`}{coming.ordered_on && ` on ${day(coming.ordered_on)}`}</span>
            {coming.payment_label && <small className={`fpl-pay fpl-pay-${coming.payment}`}>{coming.payment_label}</small>}
            {coming.kind === "order" && plan.can_plan ? <ArrivalInput coming={coming} unitId={unitId} csrfToken={csrfToken} onDone={done} /> : <small>expected {day(coming.expected_on)}</small>}
            {coming.overdue ? <small className="fpl-late">overdue</small> : !coming.expected_given && <small>estimate</small>}
          </div>) : "–"}</td>
          <td className={row.not_ordered !== "0.00" ? "fpl-late" : undefined}>{row.not_ordered !== "0.00" ? num(row.not_ordered) : "–"}</td></tr>)}
      </tbody></table></div> : <p className="sp-empty">Every open job's materials are free in stock.</p>)}
    </>}
  </section>;
}
