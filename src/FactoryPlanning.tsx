import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { PlannerPanel } from "./FactoryPlanner";

type Person = { id: number; name: string; job_title?: string; section?: string; section_label?: string };
type Stage = {
  id: number; kind: string; label: string; section: string; section_id: string; status: string; status_label: string;
  planned_start: string; planned_end: string; started_on: string; finished_on: string;
  lead: Person | null; helpers: Person[]; stuck_reason: string; late: boolean; days_late: number; by_hand: boolean; can_update: boolean;
};
type Tag = { id: number; name: string; color: string; is_active?: boolean };
type Line = {
  id: number; code: string; description: string; quantity: string; unit: string; area: string; floor: string; zone: string;
  tags: Tag[]; status: string; status_label: string; current: { kind: string; label: string; section: string } | null;
  start: string; end: string; completed_on: string; late: boolean; team: Person[]; stages: Stage[];
};
type Payment = { total: string; paid: string; balance: string; percent: string };
type Job = {
  id: number; number: string; status: string; status_label: string; purpose_label: string; project_name: string; customer: string;
  product: string; due_date: string; late: boolean; payment: Payment | null; payment_issue: boolean; lines: Line[];
};
type Planning = {
  today: string; jobs: Job[]; people: (Person & { department: string })[]; tags: Tag[];
  stages: { id: string; label: string; section: string; days: number }[]; can_update: boolean; can_keep_tags: boolean;
};
type Dashboard = {
  today: string;
  items: { total: number; not_started: number; in_progress: number; stuck: number; done: number; late: number };
  jobs: { in_production: number; waiting: number; finished: number; late: number };
  stages: { kind: string; label: string; section: string; waiting: number; late: number; stuck: number; average_days: string; planned_days: string }[];
  bottleneck: string;
  late: { job: string; code: string; description: string; stage: string; section: string; planned_end: string; days_late: number; lead: string }[];
  late_count: number;
  stuck: { job: string; code: string; description: string; stage: string; reason: string }[];
  team: { name: string; active: number; planned: number; late: number }[];
  workload: { summary: { people: number; busy: number; free: number; no_section: number };
    people: { id: number; name: string; section_label: string; load: string; load_label: string; active: number; planned_soon: number; late: number; assigned: number; hours_week: string; hours_month: string }[] };
  to_order: { variant_id: number; sku: string; name: string; unit: string; needed: string; free: string; to_order: string; jobs: { job: string; due_date: string; short: string }[] }[];
  machines: { machines: number; maintenance: { code: string; name: string; label: string; overdue: boolean }[]; condition: { code: string; name: string; label: string }[]; parts: { name: string; on_hand: string; unit: string }[] };
  costs: { job: string; estimate: string; limit: string; spent: string; percent: string; over: boolean }[];
  work_in_progress: string;
  payments: (Payment & { job: string; customer: string; issue: boolean })[];
  tags: { years: number[]; rows: { id: number; name: string; color: string; in_production: number; total: number; years: { year: number; items: number; quantity: string }[] }[] };
};
type Row = Line & { job: Job };

export async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The request could not be completed.");
  return data as T;
}
export const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const qar = (value: string | number) => Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const num = (value: string) => Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 });
export const day = (value: string) => value ? new Date(`${value}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "–";
const initials = (name: string) => show(name).split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
export const errorText = (caught: unknown, fallback: string) => (caught instanceof Error ? show(caught.message) : fallback);
const STATUS_ORDER = ["stuck", "in_progress", "not_started", "done"];
const STATUS_LABEL: Record<string, string> = { stuck: "Stuck", in_progress: "In progress", not_started: "Not started", done: "Completed", skipped: "Not needed" };
export const STAGE_COLOR: Record<string, string> = {
  drawings: "#7c6fd6", materials: "#a07a3c", saw: "#3d8bd4", joinery: "#8a5a2b", paint: "#d4793d", metal: "#6b7785", ready: "#2f9e6e",
};
const MS_DAY = 86400000;
const toDay = (value: string) => Math.round(new Date(`${value}T00:00:00Z`).getTime() / MS_DAY);

function Pill({ status, label }: { status: string; label?: string }) {
  return <span className={`fp-status fp-status-${status}`}>{label ?? STATUS_LABEL[status] ?? status}</span>;
}
function TagChips({ tags }: { tags: Tag[] }) {
  return <>{tags.map((tag) => <span key={tag.id} className="fp-tag" style={{ background: tag.color }}>{tag.name}</span>)}</>;
}
function Team({ people }: { people: Person[] }) {
  if (!people.length) return <span className="fp-muted">–</span>;
  return <span className="fp-team">{people.slice(0, 4).map((person) => <span key={person.id} className="fp-avatar" title={show(person.name)}>{initials(person.name)}</span>)}
    {people.length > 4 && <span className="fp-avatar fp-more">+{people.length - 4}</span>}</span>;
}

export function useSender(unitId: number, csrfToken: string, onDone: (message: string) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const send = async (path: string, body: unknown, message: string) => {
    setBusy(true); setError("");
    try {
      await call(`${path}?unit=${unitId}`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify(body) });
      onDone(message);
      return true;
    } catch (caught) {
      setError(errorText(caught, "That could not be saved."));
      return false;
    } finally { setBusy(false); }
  };
  return { busy, error, send };
}

/** The factory's people as options, the stage's own section first, then the others by section. */
function PeopleOptions({ people, section, exclude = [] }: { people: Person[]; section: string; exclude?: number[] }) {
  const usable = people.filter((person) => !exclude.includes(person.id));
  const groups = new Map<string, Person[]>();
  for (const person of usable) {
    const key = person.section || "";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(person);
  }
  const keys = [...groups.keys()].sort((a, b) => (a === section ? -1 : b === section ? 1 : a === "" ? 1 : b === "" ? -1 : a.localeCompare(b)));
  return <>{keys.map((key) => {
    const list = groups.get(key)!;
    return <optgroup key={key || "none"} label={key ? `${list[0].section_label}${key === section ? " (this stage)" : ""}` : "No section yet"}>
      {list.map((person) => <option key={person.id} value={person.id}>{show(person.name)}</option>)}
    </optgroup>;
  })}</>;
}

function StageRow({ stage, line, planning, unitId, csrfToken, onDone }: { stage: Stage; line: Row; planning: Planning; unitId: number; csrfToken: string; onDone: (message: string) => void }) {
  const [reason, setReason] = useState<string | null>(null);
  const { busy, error, send } = useSender(unitId, csrfToken, onDone);
  const path = `/api/factory/stages/${stage.id}/`;
  const name = `${line.code} ${stage.label.toLowerCase()}`;
  const editable = stage.can_update && stage.status !== "skipped";
  const act = (action: string, extra: Record<string, unknown> = {}, message = "") => void send(path, { action, ...extra }, message || `${name}: ${action === "done" ? "completed" : action === "start" ? "started" : action}.`).then((ok) => ok && setReason(null));
  const helperIds = stage.helpers.map((person) => person.id);
  return <tr className={`fp-stage${stage.status === "skipped" ? " fp-skipped" : ""}`}>
    <th scope="row"><span className="fp-dot" style={{ background: STAGE_COLOR[stage.kind] }} />{stage.label}<small>{stage.section}</small></th>
    <td>{editable ? <select aria-label={`${name} lead`} value={stage.lead?.id ?? ""} disabled={busy}
      onChange={(event) => void send(path, { lead_id: event.target.value ? Number(event.target.value) : null }, `${name}: lead changed.`)}>
      <option value="">Lead…</option><PeopleOptions people={planning.people} section={stage.section_id} />
    </select> : stage.lead ? show(stage.lead.name) : "–"}
      <small className="fp-helpers">{stage.helpers.map((person) => <span key={person.id} className="fp-chip">{show(person.name)}
        {editable && <button type="button" aria-label={`Remove ${show(person.name)} from ${name}`} onClick={() => void send(path, { helper_ids: helperIds.filter((id) => id !== person.id) }, `${name}: team changed.`)}>×</button>}</span>)}
        {editable && <select aria-label={`${name} add helper`} value="" disabled={busy} onChange={(event) => event.target.value && void send(path, { helper_ids: [...helperIds, Number(event.target.value)] }, `${name}: team changed.`)}>
          <option value="">+ helper</option><PeopleOptions people={planning.people} section={stage.section_id} exclude={[...(stage.lead ? [stage.lead.id] : []), ...helperIds]} />
        </select>}</small></td>
    <td>{editable ? <input type="date" aria-label={`${name} start`} value={stage.planned_start} disabled={busy} onChange={(event) => void send(path, { planned_start: event.target.value || null }, `${name}: dates changed.`)} /> : day(stage.planned_start)}
      {stage.started_on && <small>started {day(stage.started_on)}</small>}
      {stage.by_hand && stage.status === "not_started" && <small className="fp-by-hand">Set by hand{editable && <button type="button" className="fp-link" disabled={busy} onClick={() => void send(path, { planned_by_hand: false }, `${name}: the planner can move it again.`)}>Let the planner move it</button>}</small>}</td>
    <td className={stage.late ? "fp-late" : undefined}>{editable ? <input type="date" aria-label={`${name} due`} value={stage.planned_end} min={stage.planned_start || undefined} disabled={busy} onChange={(event) => void send(path, { planned_end: event.target.value || null }, `${name}: dates changed.`)} /> : day(stage.planned_end)}
      {stage.finished_on ? <small>done {day(stage.finished_on)}</small> : stage.late && <small>{stage.days_late} days late</small>}</td>
    <td><Pill status={stage.status} label={stage.status_label} />{stage.stuck_reason && <small className="fp-reason">{show(stage.stuck_reason)}</small>}</td>
    <td className="fp-actions">{stage.can_update && <>
      {(stage.status === "not_started" || stage.status === "stuck") && <button className="button button-dark" type="button" disabled={busy} onClick={() => act("start")}>Start</button>}
      {["not_started", "in_progress", "stuck"].includes(stage.status) && <button className="button button-outline" type="button" disabled={busy} onClick={() => act("done")}>Done</button>}
      {["not_started", "in_progress"].includes(stage.status) && reason === null && <button className="button button-quiet" type="button" onClick={() => setReason("")}>Stuck</button>}
      {stage.status === "done" && <button className="button button-quiet" type="button" disabled={busy} onClick={() => act("reopen")}>Reopen</button>}
      {stage.status === "not_started" && <button className="button button-quiet" type="button" disabled={busy} onClick={() => act("skip", {}, `${name} is not needed for this item.`)}>Not needed</button>}
      {stage.status === "skipped" && <button className="button button-quiet" type="button" disabled={busy} onClick={() => act("needed", {}, `${name} is back in the plan.`)}>Needed</button>}
      {reason !== null && <span className="fp-stuck">
        <input aria-label={`Why is ${name} stuck`} value={reason} placeholder="Why is it stuck?" maxLength={200} onChange={(event) => setReason(event.target.value)} />
        <button className="button button-dark" type="button" disabled={busy || !reason.trim()} onClick={() => act("stuck", { reason }, `${name} is marked stuck.`)}>Mark stuck</button>
        <button className="button button-quiet" type="button" onClick={() => setReason(null)}>Back</button></span>}
    </>}{error && <small className="fp-error" role="alert">{error}</small>}</td>
  </tr>;
}

type Suggestion = {
  line_id: number; basis?: string; reason: string;
  suggestion: null | { samples: number; total_days: number; accessories_per_unit: string;
    stages: Record<string, { days: number | null; samples: number; usually_skipped: boolean }>;
    materials: { variant_id: number; sku: string; name: string; accessory: boolean; per_unit: string; quantity: string }[];
    hours: Record<string, string>; leads: Record<string, { id: number; name: string; times: number }> };
};

function LearnedHint({ line, planning, start, unitId, csrfToken, onDone }: { line: Row; planning: Planning; start: string; unitId: number; csrfToken: string; onDone: (message: string) => void }) {
  const [found, setFound] = useState<Suggestion | null>(null);
  const [error, setError] = useState("");
  const { busy, error: sendError, send } = useSender(unitId, csrfToken, onDone);
  const ask = () => call<Suggestion>(`/api/factory/boq-lines/${line.id}/suggestion/?unit=${unitId}`).then(setFound).catch((caught) => setError(errorText(caught, "No suggestion could be made.")));
  if (!found) return <span className="fp-learn"><button className="button button-outline" type="button" onClick={() => void ask()}>Suggest from past work</button>{error && <small className="fp-error">{error}</small>}</span>;
  const profile = found.suggestion;
  if (!profile) return <p className="fp-learned fp-muted" role="status">{found.reason}</p>;
  const stageText = planning.stages.filter((stage) => !profile.stages[stage.id]?.usually_skipped && profile.stages[stage.id]?.days)
    .map((stage) => `${stage.label} ${profile.stages[stage.id].days} d`).join(" · ");
  return <div className="fp-learned" role="status" aria-label={`What the factory learned for ${line.code}`}>
    <strong>Learned from {profile.samples} finished JDs with {found.basis === "same tags" ? "the same tags" : found.basis === "all these tags" ? "these tags" : "the main tag"}</strong>
    <span>{stageText || "No stage times yet"} · about {profile.total_days} working days</span>
    {Object.keys(profile.leads).length > 0 && <span>Usual team: {Object.entries(profile.leads).map(([kind, lead]) => `${planning.stages.find((stage) => stage.id === kind)?.label ?? kind}: ${show(lead.name)}`).join(" · ")}</span>}
    {profile.materials.length > 0 && <span>Materials for {num(line.quantity)} {line.unit}: {profile.materials.map((row) => `${num(row.quantity)} × ${row.sku}${row.accessory ? " (accessory)" : ""}`).join(" · ")}{Number(profile.accessories_per_unit) > 0 && ` · ${num(profile.accessories_per_unit)} accessories per unit`}</span>}
    {Object.keys(profile.hours).length > 0 && <span>Hours usually booked: {Object.entries(profile.hours).map(([shop, hours]) => `${shop} ${num(hours)} h`).join(" · ")}</span>}
    {planning.can_update && <button className="button button-dark" type="button" disabled={busy || !start} title="Plans the stages with the learned days, sets the usual leads, switches off stages usually not needed and, while the job is being prepared, adds the materials"
      onClick={() => void send(`/api/factory/boq-lines/${line.id}/suggestion/`, { start }, `${line.code} is filled from past work, from ${day(start)}.`)}>Fill this JD from past work</button>}
    {sendError && <small className="fp-error" role="alert">{sendError}</small>}
  </div>;
}

function LineEditor({ line, planning, unitId, csrfToken, onDone }: { line: Row; planning: Planning; unitId: number; csrfToken: string; onDone: (message: string) => void }) {
  const [form, setForm] = useState({ area: line.area, floor: line.floor, zone: line.zone });
  const [start, setStart] = useState(line.start || planning.today);
  const { busy, error, send } = useSender(unitId, csrfToken, onDone);
  const tagIds = line.tags.map((tag) => tag.id);
  if (!planning.can_update) return null;
  return <div className="fp-line-edit" aria-label={`${line.code} details`}>
    <label>Area<input value={form.area} maxLength={80} onChange={(event) => setForm({ ...form, area: event.target.value })} /></label>
    <label>Floor<input value={form.floor} maxLength={80} onChange={(event) => setForm({ ...form, floor: event.target.value })} /></label>
    <label>Zone<input value={form.zone} maxLength={80} onChange={(event) => setForm({ ...form, zone: event.target.value })} /></label>
    <button className="button button-outline" type="button" disabled={busy} onClick={() => void send(`/api/factory/boq-lines/${line.id}/`, form, `${line.code}: area, floor and zone saved.`)}>Save</button>
    <span className="fp-tags-edit">Tags: {line.tags.map((tag) => <span key={tag.id} className="fp-tag" style={{ background: tag.color }}>{tag.name}
      <button type="button" aria-label={`Remove tag ${tag.name}`} onClick={() => void send(`/api/factory/boq-lines/${line.id}/`, { tag_ids: tagIds.filter((id) => id !== tag.id) }, `${line.code}: tags changed.`)}>×</button></span>)}
      <select aria-label={`Add a tag to ${line.code}`} value="" disabled={busy} onChange={(event) => event.target.value && void send(`/api/factory/boq-lines/${line.id}/`, { tag_ids: [...tagIds, Number(event.target.value)] }, `${line.code}: tags changed.`)}>
        <option value="">+ tag</option>{planning.tags.filter((tag) => tag.is_active !== false && !tagIds.includes(tag.id)).map((tag) => <option key={tag.id} value={tag.id}>{tag.name}</option>)}
      </select></span>
    <span className="fp-autoplan"><input type="date" aria-label={`Plan ${line.code} from`} value={start} onChange={(event) => setStart(event.target.value)} />
      <button className="button button-outline" type="button" disabled={busy || !start} title="Lays out the stages one after another with their usual days, skipping Fridays"
        onClick={() => void send(`/api/factory/boq-lines/${line.id}/plan/`, { start }, `${line.code} is planned from ${day(start)}.`)}>Plan all stages from this date</button></span>
    <LearnedHint line={line} planning={planning} start={start} unitId={unitId} csrfToken={csrfToken} onDone={onDone} />
    {error && <small className="fp-error" role="alert">{error}</small>}
  </div>;
}

function ListView({ rows, planning, groupBy, unitId, csrfToken, onDone }: { rows: Row[]; planning: Planning; groupBy: string; unitId: number; csrfToken: string; onDone: (message: string) => void }) {
  const [open, setOpen] = useState<number[]>([]);
  const groups = useMemo(() => {
    const found = new Map<string, { key: string; title: ReactNode; rows: Row[] }>();
    const keyOf = (row: Row) => groupBy === "job" ? row.job.number : groupBy === "section" ? (row.current?.section ?? "Completed") : row.status;
    for (const row of rows) {
      const key = keyOf(row);
      if (!found.has(key)) found.set(key, { key, rows: [], title: groupBy === "status" ? <Pill status={key} /> : groupBy === "job"
        ? <span className="fp-group-job">{row.job.number} · {show(row.job.project_name || row.job.customer || row.job.product)}</span> : <span className="fp-group-job">{key}</span> });
      found.get(key)!.rows.push(row);
    }
    const list = [...found.values()];
    if (groupBy === "status") list.sort((a, b) => STATUS_ORDER.indexOf(a.key) - STATUS_ORDER.indexOf(b.key));
    return list;
  }, [rows, groupBy]);
  const toggle = (id: number) => setOpen(open.includes(id) ? open.filter((value) => value !== id) : [...open, id]);
  if (!rows.length) return <p className="sp-empty">No BOQ items match.</p>;
  return <div className="fp-list">{groups.map((group) => <section key={group.key} className="fp-group" aria-label={`${typeof group.title === "string" ? group.title : group.key} items`}>
    <h3 className="fp-group-head">{group.title}<span className="fp-count">{group.rows.length}</span></h3>
    <div className="sp-table-wrap"><table className="sp-table bc-table fp-table">
      <thead><tr><th scope="col">Item</th><th scope="col">Job</th><th scope="col" className="bc-num">Qty</th><th scope="col">Area · Floor · Zone</th><th scope="col">Stage now</th><th scope="col">Team</th><th scope="col">Start</th><th scope="col">Due</th><th scope="col">Status</th></tr></thead>
      <tbody>{group.rows.map((row) => <ItemRows key={row.id} row={row} open={open.includes(row.id)} onToggle={() => toggle(row.id)} planning={planning} unitId={unitId} csrfToken={csrfToken} onDone={onDone} />)}</tbody>
    </table></div>
  </section>)}</div>;
}

function ItemRows({ row, open, onToggle, planning, unitId, csrfToken, onDone }: { row: Row; open: boolean; onToggle: () => void; planning: Planning; unitId: number; csrfToken: string; onDone: (message: string) => void }) {
  const done = row.stages.filter((stage) => stage.status === "done").length;
  const needed = row.stages.filter((stage) => stage.status !== "skipped").length;
  return <>
    <tr className={open ? "fp-open" : undefined}>
      <th scope="row"><button type="button" className="fp-toggle" aria-expanded={open} onClick={onToggle}><span className="pw-mono">{row.code}</span> {show(row.description)}</button>
        <span className="fp-tagline"><TagChips tags={row.tags} /><span className="fp-progress" title={`${done} of ${needed} stages done`}><span style={{ width: `${needed ? (done * 100) / needed : 0}%` }} /></span></span></th>
      <td>{row.job.number}<small>{show(row.job.project_name || row.job.customer || row.job.product)}</small></td>
      <td className="bc-num">{num(row.quantity)}<small>{row.unit}</small></td>
      <td>{[row.area, row.floor, row.zone].filter(Boolean).map(show).join(" · ") || "–"}</td>
      <td>{row.current ? <><span className="fp-dot" style={{ background: STAGE_COLOR[row.current.kind] }} />{row.current.label}<small>{row.current.section}</small></> : <span className="fp-muted">All done</span>}</td>
      <td><Team people={row.team} /></td>
      <td>{day(row.start)}</td>
      <td className={row.late ? "fp-late" : undefined}>{day(row.end || row.job.due_date)}{!row.end && <small>job due</small>}</td>
      <td><Pill status={row.status} label={row.status_label} />{row.job.payment_issue && <small className="fp-late">payment due</small>}</td>
    </tr>
    {open && <tr className="fp-detail-row"><td colSpan={9}>
      <LineEditor line={row} planning={planning} unitId={unitId} csrfToken={csrfToken} onDone={onDone} />
      <div className="sp-table-wrap"><table className="sp-table bc-table fp-stages" aria-label={`${row.code} stages`}>
        <thead><tr><th scope="col">Stage · section</th><th scope="col">Team</th><th scope="col">Start</th><th scope="col">Due</th><th scope="col">Status</th><th scope="col">Update</th></tr></thead>
        <tbody>{row.stages.map((stage) => <StageRow key={`${stage.id}-${stage.status}-${stage.planned_start}-${stage.planned_end}`} stage={stage} line={row} planning={planning} unitId={unitId} csrfToken={csrfToken} onDone={onDone} />)}</tbody>
      </table></div>
    </td></tr>}
  </>;
}

function GanttView({ rows, planning }: { rows: Row[]; planning: Planning }) {
  const [zoom, setZoom] = useState<"day" | "week">("day");
  const [open, setOpen] = useState<number[]>([]);
  const today = toDay(planning.today);
  const dates = rows.flatMap((row) => row.stages.flatMap((stage) => [stage.planned_start, stage.planned_end, stage.started_on, stage.finished_on]).filter(Boolean).map(toDay));
  const first = Math.min(today - 3, ...dates);
  const last = Math.max(today + 30, ...dates) + 2;
  const width = zoom === "day" ? 30 : 9;
  const span = last - first + 1;
  const days = Array.from({ length: span }, (_, index) => first + index);
  const months: { label: string; start: number; length: number }[] = [];
  for (const value of days) {
    const label = new Date(value * MS_DAY).toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });
    if (months.length && months[months.length - 1].label === label) months[months.length - 1].length += 1;
    else months.push({ label, start: value, length: 1 });
  }
  const bar = (start: string, end: string) => start && end ? { left: (toDay(start) - first) * width, width: (toDay(end) - toDay(start) + 1) * width } : null;
  const toggle = (id: number) => setOpen(open.includes(id) ? open.filter((value) => value !== id) : [...open, id]);
  if (!rows.length) return <p className="sp-empty">No BOQ items match.</p>;
  return <div className="fp-gantt-wrap">
    <div className="fp-gantt-tools"><span>Zoom</span>
      <button type="button" className={`button ${zoom === "day" ? "button-dark" : "button-outline"}`} onClick={() => setZoom("day")}>Days</button>
      <button type="button" className={`button ${zoom === "week" ? "button-dark" : "button-outline"}`} onClick={() => setZoom("week")}>Weeks</button>
      <span className="fp-legend">{planning.stages.map((stage) => <span key={stage.id}><span className="fp-dot" style={{ background: STAGE_COLOR[stage.id] }} />{stage.label}</span>)}</span>
    </div>
    <div className="fp-gantt" role="table" aria-label="Production Gantt">
      <div className="fp-g-names">
        <div className="fp-g-head" role="columnheader">Item</div>
        {rows.map((row) => <div key={row.id} role="rowgroup">
          <div className="fp-g-name" role="row"><button type="button" className="fp-toggle" aria-expanded={open.includes(row.id)} onClick={() => toggle(row.id)}><span className="pw-mono">{row.code}</span> {show(row.description)}</button><small>{row.job.number}</small></div>
          {open.includes(row.id) && row.stages.filter((stage) => stage.status !== "skipped").map((stage) => <div key={stage.id} className="fp-g-name fp-g-sub" role="row">{stage.label}<small>{stage.lead ? show(stage.lead.name) : stage.section}</small></div>)}
        </div>)}
      </div>
      <div className="fp-g-scroll">
        <div className="fp-g-canvas" style={{ width: span * width }}>
          <div className="fp-g-head fp-g-axis">
            <div className="fp-g-months">{months.map((month) => <span key={month.label} style={{ left: (month.start - first) * width, width: month.length * width }}>{month.label}</span>)}</div>
            <div className="fp-g-days">{days.map((value) => {
              const date = new Date(value * MS_DAY);
              const label = zoom === "day" ? String(date.getUTCDate()) : date.getUTCDay() === 6 ? String(date.getUTCDate()) : "";
              return <span key={value} className={date.getUTCDay() === 5 ? "fp-friday" : undefined} style={{ left: (value - first) * width, width }}>{label}</span>;
            })}</div>
          </div>
          <div className="fp-g-today" style={{ left: (today - first) * width + width / 2 }} title={`Today ${planning.today}`} />
          {rows.map((row) => <div key={row.id}>
            <div className="fp-g-row">
              {row.stages.filter((stage) => stage.status !== "skipped").map((stage) => {
                const place = bar(stage.planned_start, stage.planned_end);
                return place && <span key={stage.id} className={`fp-g-bar fp-g-${stage.status}${stage.late ? " fp-g-late" : ""}`} style={{ ...place, background: STAGE_COLOR[stage.kind] }}
                  title={`${row.code} ${stage.label}: ${day(stage.planned_start)} – ${day(stage.planned_end)} · ${stage.status_label}${stage.lead ? ` · ${show(stage.lead.name)}` : ""}`} />;
              })}
              {!row.start && <span className="fp-g-none">Not planned yet</span>}
            </div>
            {open.includes(row.id) && row.stages.filter((stage) => stage.status !== "skipped").map((stage) => {
              const place = bar(stage.planned_start, stage.planned_end);
              const actual = stage.started_on ? bar(stage.started_on, stage.finished_on || (stage.status === "done" ? stage.started_on : planning.today)) : null;
              return <div key={stage.id} className="fp-g-row fp-g-sub">
                {place && <span className={`fp-g-bar fp-g-${stage.status}${stage.late ? " fp-g-late" : ""}`} style={{ ...place, background: STAGE_COLOR[stage.kind] }} title={`${stage.label}: planned ${day(stage.planned_start)} – ${day(stage.planned_end)}`}>{zoom === "day" && place.width > 70 ? stage.status_label : ""}</span>}
                {actual && <span className="fp-g-actual" style={actual} title={`Actual: ${day(stage.started_on)} – ${stage.finished_on ? day(stage.finished_on) : "now"}`} />}
                {!place && <span className="fp-g-none">No dates</span>}
              </div>;
            })}
          </div>)}
        </div>
      </div>
    </div>
    <p className="sp-hint">Bars are the planned dates; the thin line under a stage is when it actually ran. Red outline: late. Fridays are shaded.</p>
  </div>;
}

function TagManager({ planning, unitId, csrfToken, onDone }: { planning: Planning; unitId: number; csrfToken: string; onDone: (message: string) => void }) {
  const [form, setForm] = useState({ name: "", color: "#8a5a2b" });
  const { busy, error, send } = useSender(unitId, csrfToken, onDone);
  return <section className="fp-tags-panel" aria-label="Manage tags">
    <h3>Tags</h3>
    <p className="sp-hint">Kinds of work (door, cabinet, cladding, glass…). Put them on each JD to count the work over the years on the Factory dashboard.</p>
    <div className="fp-tag-list">{planning.tags.map((tag) => <span key={tag.id} className={`fp-tag${tag.is_active === false ? " fp-tag-off" : ""}`} style={{ background: tag.color }}>{tag.name}
      <button type="button" aria-label={tag.is_active === false ? `Use ${tag.name} again` : `Stop using ${tag.name}`} onClick={() => void send("/api/factory/tags/", { id: tag.id, name: tag.name, color: tag.color, is_active: tag.is_active === false }, `${tag.name} is ${tag.is_active === false ? "back in use" : "no longer offered"}.`)}>{tag.is_active === false ? "↺" : "×"}</button></span>)}
      {!planning.tags.length && <span className="fp-muted">No tags yet.</span>}</div>
    <form className="fp-tag-form" onSubmit={(event) => { event.preventDefault(); void send("/api/factory/tags/", form, `The ${form.name} tag is added.`).then((ok) => ok && setForm({ ...form, name: "" })); }}>
      <input aria-label="New tag" value={form.name} maxLength={40} placeholder="e.g. Door" onChange={(event) => setForm({ ...form, name: event.target.value })} />
      <input aria-label="Tag colour" type="color" value={form.color} onChange={(event) => setForm({ ...form, color: event.target.value })} />
      <button className="button button-outline" type="submit" disabled={busy || !form.name.trim()}>Add tag</button>
    </form>
    {error && <div className="alert" role="alert">{error}</div>}
  </section>;
}

export function PlanningView({ unitId, csrfToken }: { unitId: number; csrfToken: string }) {
  const [planning, setPlanning] = useState<Planning | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [mode, setMode] = useState<"list" | "gantt">("list");
  const [groupBy, setGroupBy] = useState("status");
  const [filter, setFilter] = useState({ job: "", tag: "", section: "", text: "", late: false });
  const [tagsOpen, setTagsOpen] = useState(false);
  const load = useCallback(async () => {
    try { setPlanning(await call<Planning>(`/api/factory/planning/?unit=${unitId}`)); setError(""); }
    catch (caught) { setError(errorText(caught, "The schedule could not be loaded.")); }
  }, [unitId]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); void load(); };
  const rows = useMemo<Row[]>(() => {
    if (!planning) return [];
    const text = filter.text.trim().toLowerCase();
    return planning.jobs.flatMap((job) => job.lines.map((line) => ({ ...line, job })))
      .filter((row) => (!filter.job || String(row.job.id) === filter.job)
        && (!filter.tag || row.tags.some((tag) => String(tag.id) === filter.tag))
        && (!filter.section || row.current?.kind === filter.section)
        && (!filter.late || row.late)
        && (!text || `${row.code} ${row.description} ${row.area} ${row.floor} ${row.zone} ${row.job.number} ${row.job.project_name} ${row.team.map((person) => person.name).join(" ")}`.toLowerCase().includes(text)));
  }, [planning, filter]);
  if (!planning) return <>{error ? <div className="alert" role="alert">{error}</div> : <p className="sp-empty" role="status">Loading…</p>}</>;
  return <div className="fp-planning">
    <PlannerPanel unitId={unitId} csrfToken={csrfToken} onChanged={() => void load()} />
    <div className="fp-toolbar">
      <div className="fp-views" role="tablist" aria-label="View">
        <button type="button" role="tab" aria-selected={mode === "list"} className={mode === "list" ? "is-on" : undefined} onClick={() => setMode("list")}>List</button>
        <button type="button" role="tab" aria-selected={mode === "gantt"} className={mode === "gantt" ? "is-on" : undefined} onClick={() => setMode("gantt")}>Gantt</button>
      </div>
      {mode === "list" && <label className="fp-inline">Group by<select aria-label="Group by" value={groupBy} onChange={(event) => setGroupBy(event.target.value)}>
        <option value="status">Status</option><option value="job">Job order</option><option value="section">Section (stage now)</option></select></label>}
      <input aria-label="Search items" value={filter.text} placeholder="Search JD, area, person…" onChange={(event) => setFilter({ ...filter, text: event.target.value })} />
      <select aria-label="Job order" value={filter.job} onChange={(event) => setFilter({ ...filter, job: event.target.value })}><option value="">All jobs</option>
        {planning.jobs.map((job) => <option key={job.id} value={job.id}>{job.number} · {show(job.project_name || job.customer || job.product)}</option>)}</select>
      <select aria-label="Tag" value={filter.tag} onChange={(event) => setFilter({ ...filter, tag: event.target.value })}><option value="">All tags</option>
        {planning.tags.map((tag) => <option key={tag.id} value={tag.id}>{tag.name}</option>)}</select>
      <select aria-label="Stage now" value={filter.section} onChange={(event) => setFilter({ ...filter, section: event.target.value })}><option value="">Any stage</option>
        {planning.stages.map((stage) => <option key={stage.id} value={stage.id}>{stage.label}</option>)}</select>
      <label className="fp-inline fp-check"><input type="checkbox" checked={filter.late} onChange={(event) => setFilter({ ...filter, late: event.target.checked })} /> Late only</label>
      {planning.can_keep_tags && <button type="button" className="button button-outline" aria-expanded={tagsOpen} onClick={() => setTagsOpen(!tagsOpen)}>Manage tags</button>}
    </div>
    {tagsOpen && planning.can_keep_tags && <TagManager planning={planning} unitId={unitId} csrfToken={csrfToken} onDone={done} />}
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {mode === "list" ? <ListView rows={rows} planning={planning} groupBy={groupBy} unitId={unitId} csrfToken={csrfToken} onDone={done} />
      : <GanttView rows={rows} planning={planning} />}
  </div>;
}

function Tile({ label, value, note, tone }: { label: string; value: ReactNode; note?: string; tone?: string }) {
  return <div className={`fp-tile${tone ? ` fp-tile-${tone}` : ""}`}><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</div>;
}

export function DashboardView({ unitId }: { unitId: number }) {
  const [board, setBoard] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    call<Dashboard>(`/api/factory/dashboard/?unit=${unitId}`).then(setBoard).catch((caught) => setError(errorText(caught, "The dashboard could not be loaded.")));
  }, [unitId]);
  if (!board) return <>{error ? <div className="alert" role="alert">{error}</div> : <p className="sp-empty" role="status">Loading…</p>}</>;
  const items = board.items;
  const statusBar = ["done", "in_progress", "stuck", "not_started"] as const;
  const peak = Math.max(1, ...board.stages.map((stage) => stage.waiting));
  const issues = board.payments.filter((row) => row.issue);
  return <div className="fp-dashboard">
    <div className="fp-tiles">
      <Tile label="Items in production" value={items.in_progress} note={`of ${items.total} JD items`} />
      <Tile label="Completed" value={items.done} tone="green" />
      <Tile label="Stuck" value={items.stuck} tone={items.stuck ? "red" : undefined} />
      <Tile label="Late items" value={items.late} tone={items.late ? "red" : undefined} />
      <Tile label="Jobs in production" value={board.jobs.in_production} note={`${board.jobs.waiting} waiting · ${board.jobs.late} late`} />
      <Tile label="Work in progress" value={`QAR ${qar(board.work_in_progress)}`} note="spent on open jobs" />
      <Tile label="Payment issues" value={issues.length} tone={issues.length ? "red" : undefined} note="ready, balance not paid" />
    </div>
    <section className="fp-card" aria-label="Item status">
      <h3>Item status</h3>
      <div className="fp-stack" role="img" aria-label={statusBar.map((key) => `${STATUS_LABEL[key]} ${items[key]}`).join(", ")}>
        {statusBar.map((key) => items[key] > 0 && <span key={key} className={`fp-stack-${key}`} style={{ flexGrow: items[key] }}>{STATUS_LABEL[key]} {items[key]}</span>)}
        {!items.total && <span className="fp-muted">No items yet.</span>}
      </div>
    </section>
    <div className="fp-dash-grid">
      <section className="fp-card" aria-label="Bottlenecks">
        <h3>Where the work is waiting</h3>
        <table className="sp-table bc-table fp-mini"><thead><tr><th scope="col">Stage · section</th><th scope="col">Items here now</th><th scope="col" className="bc-num">Late</th><th scope="col" className="bc-num">Stuck</th><th scope="col" className="bc-num">Days taken / planned</th></tr></thead>
          <tbody>{board.stages.map((stage) => <tr key={stage.kind} className={stage.kind === board.bottleneck ? "fp-bottleneck" : undefined}>
            <th scope="row"><span className="fp-dot" style={{ background: STAGE_COLOR[stage.kind] }} />{stage.label}<small>{stage.section}{stage.kind === board.bottleneck && " · bottleneck"}</small></th>
            <td><span className="fp-hbar"><span style={{ width: `${(stage.waiting * 100) / peak}%`, background: STAGE_COLOR[stage.kind] }} /></span>{stage.waiting}</td>
            <td className={`bc-num${stage.late ? " fp-late" : ""}`}>{stage.late}</td>
            <td className={`bc-num${stage.stuck ? " fp-late" : ""}`}>{stage.stuck}</td>
            <td className="bc-num">{stage.average_days ? num(stage.average_days) : "–"} / {stage.planned_days ? num(stage.planned_days) : "–"}</td>
          </tr>)}</tbody></table>
      </section>
      <section className="fp-card" aria-label="Late work">
        <h3>Late work{board.late_count > board.late.length && ` (top ${board.late.length} of ${board.late_count})`}</h3>
        {board.late.length ? <ul className="fp-items">{board.late.map((row, index) => <li key={`${row.job}-${row.code}-${row.stage}-${index}`}>
          <strong className="fp-late">{row.days_late} d</strong> <span className="pw-mono">{row.code}</span> {show(row.description)} · {row.stage}<small>{row.job} · due {day(row.planned_end)}{row.lead && ` · ${show(row.lead)}`}</small></li>)}</ul>
          : <p className="sp-empty">Nothing is late.</p>}
        {board.stuck.length > 0 && <><h4>Stuck</h4><ul className="fp-items">{board.stuck.map((row, index) => <li key={`${row.code}-${index}`}><span className="pw-mono">{row.code}</span> {show(row.description)} · {row.stage}<small>{row.job} · {show(row.reason)}</small></li>)}</ul></>}
      </section>
      <section className="fp-card" aria-label="Team workload">
        <h3>Team workload</h3>
        <p className="sp-hint">{board.workload.summary.people} people · {board.workload.summary.busy} busy · {board.workload.summary.free} free{board.workload.summary.no_section > 0 && ` · ${board.workload.summary.no_section} without a section`}. Full record on Factory team.</p>
        {board.workload.people.length ? <table className="sp-table bc-table fp-mini"><thead><tr><th scope="col">Person · section</th><th scope="col">Load</th><th scope="col" className="bc-num">Working on</th><th scope="col" className="bc-num">Late</th><th scope="col" className="bc-num">Hours this month</th></tr></thead>
          <tbody>{board.workload.people.map((person) => <tr key={person.id}><th scope="row">{show(person.name)}<small>{person.section_label || "no section yet"}</small></th>
            <td><span className={`fp-load fp-load-${person.load}`}>{person.load_label}</span></td>
            <td className="bc-num">{person.active}<small>{person.planned_soon} planned soon</small></td>
            <td className={`bc-num${person.late ? " fp-late" : ""}`}>{person.late}</td><td className="bc-num">{num(person.hours_month)}</td></tr>)}</tbody></table>
          : <p className="sp-empty">No factory staff yet. Employees in the Factory department appear here.</p>}
      </section>
      <section className="fp-card" aria-label="Materials to order">
        <h3>Materials to order</h3>
        {board.to_order.length ? <ul className="fp-items">{board.to_order.map((row) => <li key={row.variant_id}>
          <strong className="fp-late">{num(row.to_order)} {row.unit.toLowerCase()}</strong> <span className="pw-mono">{row.sku}</span> {show(row.name)}
          <small>needed {num(row.needed)}, free {num(row.free)} · {row.jobs.map((job) => `${job.job} short ${num(job.short)} (due ${day(job.due_date)})`).join(" · ")}</small></li>)}</ul>
          : <p className="sp-empty">Stock covers every open job's materials.</p>}
      </section>
      <section className="fp-card" aria-label="Machines">
        <h3>Machines</h3>
        {board.machines.maintenance.length + board.machines.condition.length + board.machines.parts.length === 0
          ? <p className="sp-empty">{board.machines.machines ? `${board.machines.machines} machines, nothing due.` : "No machines on the list yet."}</p>
          : <ul className="fp-items">
            {board.machines.maintenance.map((row) => <li key={`m-${row.code}`}><strong className={row.overdue ? "fp-late" : undefined}>{row.code}</strong> {show(row.name)}<small>{row.label}</small></li>)}
            {board.machines.condition.map((row) => <li key={`c-${row.code}`}><strong className="fp-late">{row.code}</strong> {show(row.name)}<small>{row.label}</small></li>)}
            {board.machines.parts.map((row) => <li key={`p-${row.name}`}><strong>Order</strong> {show(row.name)}<small>{num(row.on_hand)} {row.unit} left</small></li>)}
          </ul>}
      </section>
      <section className="fp-card" aria-label="Job cost">
        <h3>Cost against estimate</h3>
        {board.costs.length ? <table className="sp-table bc-table fp-mini"><thead><tr><th scope="col">Job</th><th scope="col">Spent of estimate</th><th scope="col" className="bc-num">Spent</th><th scope="col" className="bc-num">Estimate</th></tr></thead>
          <tbody>{board.costs.map((row) => <tr key={row.job}><th scope="row">{row.job}{row.over && <small className="fp-late">over the limit, Finance approves</small>}</th>
            <td><span className="fp-hbar fp-cost"><span className={row.over ? "is-over" : undefined} style={{ width: `${Math.min(100, Number(row.percent || 0))}%` }} /></span>{row.percent ? `${num(row.percent)}%` : "–"}</td>
            <td className="bc-num">{qar(row.spent)}</td><td className="bc-num">{qar(row.estimate)}</td></tr>)}</tbody></table>
          : <p className="sp-empty">No open jobs.</p>}
      </section>
      <section className="fp-card" aria-label="Payments">
        <h3>Client payments</h3>
        {board.payments.length ? <table className="sp-table bc-table fp-mini"><thead><tr><th scope="col">Job · client</th><th scope="col">Paid</th><th scope="col" className="bc-num">Balance</th></tr></thead>
          <tbody>{board.payments.map((row) => <tr key={row.job} className={row.issue ? "fp-issue" : undefined}><th scope="row">{row.job}<small>{show(row.customer)}{row.issue && " · ready, not fully paid"}</small></th>
            <td><span className="fp-hbar fp-paid"><span style={{ width: `${Math.min(100, Number(row.percent))}%` }} /></span>{num(row.percent)}%</td>
            <td className={`bc-num${row.issue ? " fp-late" : ""}`}>{qar(row.balance)}<small>of {qar(row.total)}</small></td></tr>)}</tbody></table>
          : <p className="sp-empty">No client jobs.</p>}
      </section>
      <section className="fp-card" aria-label="Work by tag">
        <h3>Work done by tag</h3>
        {board.tags.rows.length ? <table className="sp-table bc-table fp-mini"><thead><tr><th scope="col">Tag</th>{board.tags.years.map((year) => <th key={year} scope="col" className="bc-num">{year}</th>)}<th scope="col" className="bc-num">In production</th></tr></thead>
          <tbody>{board.tags.rows.map((tag) => <tr key={tag.id}><th scope="row"><span className="fp-tag" style={{ background: tag.color }}>{tag.name}</span></th>
            {tag.years.map((year) => <td key={year.year} className="bc-num">{year.items}<small>qty {num(year.quantity)}</small></td>)}<td className="bc-num">{tag.in_production}</td></tr>)}</tbody></table>
          : <p className="sp-empty">No tags yet. The Factory Manager adds them on the Production schedule page.</p>}
      </section>
    </div>
  </div>;
}

