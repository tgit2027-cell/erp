import { Fragment, useCallback, useEffect, useState } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Line = {
  entitlement: number; employee: number; name: string; staff_number: string; unit: string; plan: string; plan_label: string;
  months: string; kind: "commission" | "reward"; amount: string; pending: string[]; steps: string[];
};
type Company = {
  pending: string[]; steps: string[]; revenue_target: string; revenue_actual: string; ebitda_target: string; ebitda_actual: string;
  operational: Record<string, string>; cpi: string; company_factor: string; negative_ebitda: boolean;
};
type Unit = {
  id: number; unit_id: number; unit: string; counts_in_company: boolean; figures: Record<string, string>; gp_margin: string;
  minimum_margin: string; validated: boolean; validated_by: string; unit_factor: string; pending: string[]; steps: string[];
};
type Team = { id: number; code: string; name: string; unit_id: number; unit: string; revenue_target: string; gp_target: string; collection_fraction: string };
type Plan = {
  id: number; employee: number; name: string; staff_number: string; grade: string; unit_id: number; unit: string; plan: string;
  plan_label: string; channel: string; reward_band: string; category: string; first_month: number; last_month: number;
  revenue_target: string; gp_target: string; revenue_actual: string; gp_actual: string; erp_revenue: string;
  collection_fraction: string; rating: string; team: number | null; team_share: string; note: string; status: string;
  status_label: string; proposed_by: string; approved_by: string;
};
type Instalment = { employee?: string; kind: string; month: string; amount: string; paid: boolean };
type Run = {
  id: number; number: number; status: string; status_label: string; policy_version: number; lines: Line[]; commission_total: string;
  reward_total: string; pending_count: number; prepared_by: string; prepared_at: string; checked_by: string; approved_by: string;
  released_by: string; instalments: Instalment[]; can_redo: boolean; can_check: boolean; can_approve: boolean; can_release: boolean;
};
type Choices = { plans: Record<string, string>; channels: Record<string, string>; ratings: Record<string, string>;
  categories: Record<string, string>; operational: Record<string, string> };
type Policy = Record<string, unknown>;
type Overview = {
  entity: string; year: number; policy_label: string; full: boolean; exists: boolean; can_prepare: boolean; can_check: boolean;
  can_approve: boolean; year_over: boolean; choices: Choices; years: number[];
  mine?: { plans: { plan_label: string; months: string; status_label: string; revenue_target: string; revenue_actual: string }[]; lines: Line[]; instalments: Instalment[] };
  policy?: Policy; policy_version?: number; policy_status?: string; policy_status_label?: string; policy_approved_by?: string;
  reward_payment_month?: string; frozen?: boolean; company?: Company; units?: Unit[]; teams?: Team[]; entitlements?: Plan[];
  preview?: { lines: Line[]; pending: number; commission: string; reward: string }; runs?: Run[];
  units_available?: { id: number; name: string }[]; staff?: { id: number; name: string; staff_number: string; grade: string; unit_id: number }[];
  history?: { what: string; by: string; at: string; detail: Record<string, unknown> }[];
};

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : Object.values(data).flat().join(" ") || "The request could not be completed.";
    throw new Error(String(detail));
  }
  return data as T;
}

const show = (text: string) => text.replace(/TEST ONLY:?\s*/g, "");
const qar = (value: string | number) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const money = (value: string) => (value === "" ? "—" : qar(value));
const day = (value: string) => new Date(value).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const monthName = (value: string) => new Date(`${value}-01T00:00:00`).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const TABS = { overview: "Company and units", people: "People and plans", teams: "Sales teams", policy: "Rules and rates", runs: "Runs and payments", history: "History" } as const;
type Tab = keyof typeof TABS;

function Amount({ line }: { line: Line }) {
  if (line.pending.length) return <span className="sp-status sp-status-waiting">Pending</span>;
  return <strong>{qar(line.amount)}</strong>;
}

function Why({ line }: { line: Line }) {
  if (!line.pending.length && !line.steps.length) return null;
  return <details className="ic-why"><summary>{line.pending.length ? "What is missing" : "How it was worked out"}</summary>
    <ul>{[...line.pending, ...line.steps].map((step, index) => <li key={index}>{step}</li>)}</ul>
  </details>;
}

function Field({ label, value, onChange, type = "text", placeholder }: { label: string; value: string; onChange: (next: string) => void; type?: string; placeholder?: string }) {
  return <label>{label}<input aria-label={label} type={type} inputMode={type === "text" ? "decimal" : undefined} value={value} placeholder={placeholder}
    onChange={(event) => onChange(event.target.value)} /></label>;
}

function CompanyForm({ overview, busy, post }: { overview: Overview; busy: boolean; post: (body: Record<string, unknown>, message: string) => void }) {
  const company = overview.company!;
  const [draft, setDraft] = useState({ ebitda_target: company.ebitda_target, ebitda_actual: company.ebitda_actual, ...company.operational });
  const set = (key: string) => (next: string) => setDraft({ ...draft, [key]: next });
  const operational = Object.fromEntries(Object.keys(overview.choices.operational).map((key) => [key, (draft as Record<string, string>)[key] ?? ""]));
  return <div className="ic-form" aria-label="Company figures">
    <Field label="EBITDA target (QAR)" value={draft.ebitda_target} onChange={set("ebitda_target")} />
    <Field label="Actual EBITDA (QAR)" value={draft.ebitda_actual} onChange={set("ebitda_actual")} />
    {Object.entries(overview.choices.operational).map(([key, label]) => <Field key={key} label={`${label} achievement (%)`}
      value={(draft as Record<string, string>)[key] ?? ""} onChange={set(key)} />)}
    <button className="button button-outline" type="button" disabled={busy || overview.frozen}
      onClick={() => post({ action: "company", ebitda_target: draft.ebitda_target, ebitda_actual: draft.ebitda_actual, operational }, "Company figures saved.")}>Save company figures</button>
  </div>;
}

function UnitForm({ unit, units, busy, post }: { unit?: Unit; units: { id: number; name: string }[]; busy: boolean; post: (body: Record<string, unknown>, message: string) => void }) {
  const [draft, setDraft] = useState({
    unit_id: unit?.unit_id ?? units[0]?.id ?? 0, counts_in_company: unit?.counts_in_company ?? true,
    collections_target: unit?.figures.collections_target ?? "", collections_actual: unit?.figures.collections_actual ?? "",
    operational_achievement: unit?.figures.operational ?? "", minimum_margin: unit?.minimum_margin ?? "",
  });
  const set = (key: string) => (next: string) => setDraft({ ...draft, [key]: next });
  return <div className="ic-form" aria-label={unit ? `Figures for ${unit.unit}` : "Add a business unit"}>
    {!unit && <label>Business unit<select aria-label="Business unit" value={draft.unit_id} onChange={(event) => setDraft({ ...draft, unit_id: Number(event.target.value) })}>
      {units.map((row) => <option key={row.id} value={row.id}>{show(row.name)}</option>)}</select></label>}
    <Field label="Collections target (QAR)" value={draft.collections_target} onChange={set("collections_target")} />
    <Field label="Actual collections (QAR)" value={draft.collections_actual} onChange={set("collections_actual")} />
    <Field label="Operational achievement (%)" value={draft.operational_achievement} onChange={set("operational_achievement")} />
    <Field label="Minimum margin (%)" value={draft.minimum_margin} onChange={set("minimum_margin")} />
    <label className="ic-check"><input type="checkbox" checked={draft.counts_in_company} onChange={(event) => setDraft({ ...draft, counts_in_company: event.target.checked })} />Counts in company revenue</label>
    <button className="button button-outline" type="button" disabled={busy || !draft.unit_id}
      onClick={() => post({ action: "unit", ...draft }, "Unit figures saved.")}>{unit ? "Save" : "Add unit"}</button>
  </div>;
}

function CompanyTab({ overview, busy, post }: { overview: Overview; busy: boolean; post: (body: Record<string, unknown>, message: string) => void }) {
  const company = overview.company!;
  const [month, setMonth] = useState(overview.reward_payment_month ?? "");
  const [editing, setEditing] = useState<number | null>(null);
  return <>
    <ul className="bc-accounts ex-tiles" aria-label="Company performance">
      <li className="bc-total"><span>Company revenue</span><strong>{money(company.revenue_actual)}</strong><small>QAR against a target of {money(company.revenue_target)}</small></li>
      <li className="bc-total"><span>Company Performance Index</span><strong>{company.cpi ? `${company.cpi}%` : "Pending"}</strong><small>40% revenue, 40% EBITDA, 20% operational</small></li>
      <li className="bc-total"><span>Company reward factor</span><strong>{company.company_factor ? `${company.company_factor}%` : "Pending"}</strong><small>{company.negative_ebitda ? "EBITDA is negative: no company rewards" : "applies to fixed rewards"}</small></li>
    </ul>
    {company.pending.length > 0 && <div className="ic-pending" role="status"><strong>Still missing, so results stay pending:</strong><ul>{company.pending.map((reason) => <li key={reason}>{reason}</li>)}</ul></div>}
    {company.steps.length > 0 && <details className="ic-why"><summary>How the index was worked out</summary><ul>{company.steps.map((step) => <li key={step}>{step}</li>)}</ul></details>}
    {overview.can_prepare && <CompanyForm key={JSON.stringify(company)} overview={overview} busy={busy} post={post} />}
    <h3>Business units</h3>
    <p className="sp-hint">Revenue and direct-cost targets come from each unit's approved budget, and actuals from the posted ledger. Finance validates the figures; any later change needs validating again.</p>
    <div className="sp-table-wrap"><table className="sp-table" aria-label="Business unit figures">
      <thead><tr><th scope="col">Unit</th><th scope="col">Revenue (QAR)</th><th scope="col">GP (QAR)</th><th scope="col">Margin</th><th scope="col">Collections (QAR)</th><th scope="col">Operational</th><th scope="col">Unit factor</th><th scope="col" /></tr></thead>
      <tbody>{(overview.units ?? []).map((unit) => <Fragment key={unit.id}><tr>
        <th scope="row">{show(unit.unit)}{!unit.counts_in_company && <small>Not in company revenue</small>}</th>
        <td className="bc-num">{money(unit.figures.revenue_actual)}<small>target {money(unit.figures.revenue_target)}</small></td>
        <td className="bc-num">{money(unit.figures.gp_actual)}<small>target {money(unit.figures.gp_target)}</small></td>
        <td>{unit.gp_margin ? `${unit.gp_margin}%` : "—"}{unit.minimum_margin && <small>minimum {unit.minimum_margin}%</small>}</td>
        <td className="bc-num">{money(unit.figures.collections_actual)}<small>target {money(unit.figures.collections_target)}</small></td>
        <td>{unit.figures.operational ? `${unit.figures.operational}%` : "—"}</td>
        <td>{unit.unit_factor ? `${unit.unit_factor}%` : <span className="sp-status sp-status-waiting">Pending</span>}
          {unit.pending.length > 0 && <small>{unit.pending.join(" ")}</small>}</td>
        <td>{unit.validated ? <small>Validated by {show(unit.validated_by)}</small>
          : overview.can_prepare && !overview.frozen && <button className="button button-outline" type="button" disabled={busy}
            onClick={() => post({ action: "validate_unit", id: unit.id }, `${show(unit.unit)}'s figures validated.`)}>Validate</button>}
          {overview.can_prepare && !overview.frozen && <button className="button button-outline" type="button" onClick={() => setEditing(editing === unit.id ? null : unit.id)}>Edit</button>}</td>
      </tr>
      {editing === unit.id && <tr><td colSpan={8}><UnitForm unit={unit} units={[]} busy={busy} post={post} /></td></tr>}</Fragment>)}</tbody>
    </table></div>
    {overview.can_prepare && !overview.frozen && (overview.units_available ?? []).length > 0 && <UnitForm units={overview.units_available ?? []} busy={busy} post={post} />}
    <h3>When fixed rewards are paid</h3>
    <div className="ic-form">
      <label>Payroll month<input aria-label="Reward payment month" type="month" value={month} onChange={(event) => setMonth(event.target.value)} /></label>
      <button className="button button-outline" type="button" disabled={busy || month === (overview.reward_payment_month ?? "")}
        onClick={() => post({ action: "reward_month", reward_payment_month: month }, month ? `Fixed rewards will be paid in the ${monthName(month)} payroll.` : "Reward month cleared.")}>Save month</button>
      <small>Commission is paid {((overview.policy?.instalments ?? []) as { month: number; percent: string }[]).map((row) => `${row.percent}% in ${MONTHS[row.month - 1]}`).join(", ")} of {overview.year + 1}.</small>
    </div>
  </>;
}

const EMPTY_PLAN = { id: null as number | null, employee: 0, plan: "sales_commission", unit_id: 0, channel: "retail", reward_band: "", category: "",
  first_month: 1, last_month: 12, revenue_target: "", gp_target: "", revenue_actual: "", gp_actual: "", collection_fraction: "", rating: "",
  team: "" as number | "", team_share: "", note: "" };

function PlanForm({ overview, plan, busy, post, done }: { overview: Overview; plan?: Plan; busy: boolean; post: (body: Record<string, unknown>, message: string) => void; done: () => void }) {
  const staff = overview.staff ?? [];
  const [draft, setDraft] = useState(plan ? { ...EMPTY_PLAN, ...plan, team: plan.team ?? "" } : { ...EMPTY_PLAN, employee: staff[0]?.id ?? 0, unit_id: staff[0]?.unit_id ?? 0 });
  const set = (key: string) => (next: string) => setDraft({ ...draft, [key]: next });
  const commission = draft.plan === "sales_commission" || draft.plan === "supervisor_hybrid";
  const reward = draft.plan === "performance_reward";
  const bands = Object.keys((overview.policy?.reward_bands ?? {}) as Record<string, string>);
  const units = [...(overview.units ?? []).map((row) => ({ id: row.unit_id, name: row.unit }))];
  const pick = (label: string, key: string, options: Record<string, string>, allowBlank = true) => <label>{label}
    <select aria-label={label} value={String((draft as Record<string, unknown>)[key] ?? "")} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}>
      {allowBlank && <option value="">Not set</option>}
      {Object.entries(options).filter(([value]) => value !== "").map(([value, text]) => <option key={value} value={value}>{text}</option>)}
    </select></label>;
  return <div className="ic-form" aria-label={plan ? `Plan for ${show(plan.name)}` : "Propose a plan"}>
    {!plan && <label>Person<select aria-label="Person" value={draft.employee} onChange={(event) => {
      const person = staff.find((row) => row.id === Number(event.target.value));
      setDraft({ ...draft, employee: Number(event.target.value), unit_id: person?.unit_id ?? draft.unit_id });
    }}>{staff.map((row) => <option key={row.id} value={row.id}>{show(row.name)} ({row.staff_number}{row.grade ? `, ${row.grade}` : ""})</option>)}</select></label>}
    {pick("Plan", "plan", overview.choices.plans, false)}
    <label>Results counted from<select aria-label="Results counted from" value={draft.unit_id} onChange={(event) => setDraft({ ...draft, unit_id: Number(event.target.value) })}>
      {units.map((row) => <option key={row.id} value={row.id}>{show(row.name)}</option>)}</select></label>
    <label>From<select aria-label="First month" value={draft.first_month} onChange={(event) => setDraft({ ...draft, first_month: Number(event.target.value) })}>
      {MONTHS.map((name, index) => <option key={name} value={index + 1}>{name}</option>)}</select></label>
    <label>To<select aria-label="Last month" value={draft.last_month} onChange={(event) => setDraft({ ...draft, last_month: Number(event.target.value) })}>
      {MONTHS.map((name, index) => <option key={name} value={index + 1}>{name}</option>)}</select></label>
    {commission && <>
      {pick("Sales channel", "channel", overview.choices.channels, false)}
      <Field label="Revenue target (QAR)" value={draft.revenue_target} onChange={set("revenue_target")} />
      <Field label="GP target (QAR)" value={draft.gp_target} onChange={set("gp_target")} />
      <Field label="Validated revenue (QAR)" value={draft.revenue_actual} onChange={set("revenue_actual")} placeholder={plan?.erp_revenue ? `ERP: ${qar(plan.erp_revenue)}` : ""} />
      <Field label="Validated GP (QAR)" value={draft.gp_actual} onChange={set("gp_actual")} />
      <Field label="Collection-eligible fraction (0 to 1)" value={draft.collection_fraction} onChange={set("collection_fraction")} />
    </>}
    {draft.plan === "supervisor_hybrid" && <>
      <label>Team<select aria-label="Team" value={draft.team} onChange={(event) => setDraft({ ...draft, team: event.target.value ? Number(event.target.value) : "" })}>
        <option value="">Not set</option>{(overview.teams ?? []).map((team) => <option key={team.id} value={team.id}>{team.code} · {show(team.name)}</option>)}</select></label>
      <Field label="Share of the team pool (%)" value={draft.team_share} onChange={set("team_share")} />
    </>}
    {reward && <>
      {pick("Reward band", "reward_band", Object.fromEntries(bands.map((code) => [code, `${code} · QAR ${qar(((overview.policy?.reward_bands ?? {}) as Record<string, string>)[code])}`])))}
      {pick("Category", "category", overview.choices.categories)}
      {pick("Performance rating", "rating", overview.choices.ratings)}
    </>}
    <Field label="Note" value={draft.note} onChange={set("note")} />
    <div className="sp-actions">
      <button className="button button-dark" type="button" disabled={busy || !draft.employee || !draft.unit_id}
        onClick={() => { post({ action: "plan", ...draft, id: plan?.id ?? null, team: draft.team === "" ? null : draft.team, channel: reward ? "" : draft.channel }, plan ? "Plan saved." : "Plan proposed. Someone else approves it."); done(); }}>{plan ? "Save plan" : "Propose plan"}</button>
      <button className="button button-outline" type="button" onClick={done}>Cancel</button>
    </div>
  </div>;
}

function PeopleTab({ overview, busy, post }: { overview: Overview; busy: boolean; post: (body: Record<string, unknown>, message: string) => void }) {
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const preview = new Map((overview.preview?.lines ?? []).map((line) => [line.entitlement, line]));
  const plans = overview.entitlements ?? [];
  const canDecide = overview.can_check || overview.can_approve;
  return <>
    <ul className="bc-accounts ex-tiles" aria-label="Preview">
      <li className="bc-total"><span>Commission so far</span><strong>{qar(overview.preview?.commission ?? 0)}</strong><small>QAR, before approval</small></li>
      <li className="bc-total"><span>Fixed rewards so far</span><strong>{qar(overview.preview?.reward ?? 0)}</strong><small>QAR, before approval</small></li>
      <li className="bc-total"><span>Pending</span><strong>{overview.preview?.pending ?? 0}</strong><small>people with missing figures or approvals</small></li>
    </ul>
    <p className="sp-hint">One plan per person at a time. A plan is proposed by Finance and approved by someone else; a grade on its own does not make anyone eligible. Changing a plan's terms sends it back for approval.</p>
    {overview.can_prepare && editing === null && <div className="sp-actions"><button className="button button-dark" type="button" onClick={() => setEditing("new")}>Propose a plan</button></div>}
    {editing === "new" && <PlanForm overview={overview} busy={busy} post={post} done={() => setEditing(null)} />}
    {plans.length === 0 ? <p className="sp-empty">No plans yet.</p> : <div className="sp-table-wrap"><table className="sp-table" aria-label="People and plans">
      <thead><tr><th scope="col">Person</th><th scope="col">Plan</th><th scope="col">Months</th><th scope="col">Status</th><th scope="col">Amount (QAR)</th></tr></thead>
      <tbody>{plans.map((plan) => { const line = preview.get(plan.id); return <Fragment key={plan.id}><tr>
        <th scope="row">{show(plan.name)}<small>{plan.staff_number}{plan.grade ? ` · ${plan.grade}` : ""} · {show(plan.unit)}</small></th>
        <td>{plan.plan_label}<small>{[overview.choices.channels[plan.channel] && plan.channel ? overview.choices.channels[plan.channel] : "", plan.reward_band, overview.choices.categories[plan.category] ?? ""].filter(Boolean).join(" · ")}</small></td>
        <td>{MONTHS[plan.first_month - 1]} to {MONTHS[plan.last_month - 1]}</td>
        <td><span className={`sp-status sp-status-${plan.status === "approved" ? "approved" : "waiting"}`}>{plan.status_label}</span>
          <small>{plan.approved_by ? `Approved by ${show(plan.approved_by)}` : `Proposed by ${show(plan.proposed_by)}`}</small>
          <span className="ic-actions">{plan.status !== "withdrawn" && <>
          {overview.can_prepare && <button className="button button-outline" type="button" onClick={() => setEditing(editing === plan.id ? null : plan.id)}>Edit</button>}
          {canDecide && plan.status === "proposed" && <button className="button button-outline" type="button" disabled={busy}
            onClick={() => post({ action: "decide_plan", id: plan.id, decision: "approve" }, `${show(plan.name)}'s plan approved.`)}>Approve</button>}
          {(overview.can_prepare || overview.can_approve) && line && <button className="button button-outline" type="button" disabled={busy}
            onClick={() => post({ action: "decide_plan", id: plan.id, decision: "withdraw" }, `${show(plan.name)}'s plan withdrawn.`)}>Withdraw</button>}
        </>}</span></td>
        <td className="bc-num">{line ? <><Amount line={line} /><Why line={line} /></> : plan.status === "withdrawn" ? "" : <small>In an approved run</small>}</td>
      </tr>
      {editing === plan.id && <tr><td colSpan={5}><PlanForm overview={overview} plan={plan} busy={busy} post={post} done={() => setEditing(null)} /></td></tr>}
      </Fragment>; })}</tbody>
    </table></div>}
  </>;
}

function TeamsTab({ overview, busy, post }: { overview: Overview; busy: boolean; post: (body: Record<string, unknown>, message: string) => void }) {
  const blank = { id: null as number | null, code: "", name: "", unit_id: overview.units?.[0]?.unit_id ?? 0, revenue_target: "", gp_target: "", collection_fraction: "" };
  const [draft, setDraft] = useState(blank);
  const set = (key: string) => (next: string) => setDraft({ ...draft, [key]: next });
  return <>
    <p className="sp-hint">A supervisor's team part is worked out from the team's own target and the sum of its salespeople's validated figures, so nothing is entered twice. A supervisor's own sales count only in their personal part. Supervisors' shares of one team cannot add up to more than 100%.</p>
    {(overview.teams ?? []).length > 0 && <div className="sp-table-wrap"><table className="sp-table" aria-label="Sales teams">
      <thead><tr><th scope="col">Team</th><th scope="col">Unit</th><th scope="col">Revenue target (QAR)</th><th scope="col">GP target (QAR)</th><th scope="col">Collection fraction</th><th scope="col" /></tr></thead>
      <tbody>{(overview.teams ?? []).map((team) => <tr key={team.id}>
        <th scope="row">{team.code}<small>{show(team.name)}</small></th><td>{show(team.unit)}</td>
        <td className="bc-num">{money(team.revenue_target)}</td><td className="bc-num">{money(team.gp_target)}</td><td>{team.collection_fraction || "—"}</td>
        <td>{overview.can_prepare && !overview.frozen && <button className="button button-outline" type="button" onClick={() => setDraft({ ...team, id: team.id })}>Edit</button>}</td>
      </tr>)}</tbody></table></div>}
    {overview.can_prepare && !overview.frozen && <div className="ic-form" aria-label={draft.id ? "Edit team" : "Add a team"}>
      <Field label="Team code" value={draft.code} onChange={set("code")} />
      <Field label="Team name" value={draft.name} onChange={set("name")} />
      <label>Business unit<select aria-label="Team unit" value={draft.unit_id} onChange={(event) => setDraft({ ...draft, unit_id: Number(event.target.value) })}>
        {(overview.units ?? []).map((row) => <option key={row.unit_id} value={row.unit_id}>{show(row.unit)}</option>)}</select></label>
      <Field label="Team revenue target (QAR)" value={draft.revenue_target} onChange={set("revenue_target")} />
      <Field label="Team GP target (QAR)" value={draft.gp_target} onChange={set("gp_target")} />
      <Field label="Team collection-eligible fraction (0 to 1)" value={draft.collection_fraction} onChange={set("collection_fraction")} />
      <button className="button button-outline" type="button" disabled={busy || !draft.unit_id}
        onClick={() => { post({ action: "team", ...draft }, `Team ${draft.code} saved.`); setDraft(blank); }}>{draft.id ? "Save team" : "Add team"}</button>
    </div>}
  </>;
}

const POLICY_TITLES: Record<string, string> = {
  cpi_weights: "Company Performance Index weights (%)", revenue_score: "Revenue achievement → score", ebitda_score: "EBITDA achievement → score",
  operational_score: "Operational component achievement → score", company_factor: "CPI → company reward factor",
  commission_gate: "Commission gate (% of the annual target)", commission_schedules: "Progressive commission rates (% of each slice)",
  gp_factor: "GP achievement → commission GP factor", sales_cpi_bonus: "CPI → extra sales bonus on validated commission",
  hybrid: "Supervisor split: personal and team (%)", instalments: "Commission instalments in the next year",
  reward_bands: "Fixed reward bands (QAR a year)", rating_factors: "Individual rating → factor (%)",
  category_weights: "Fixed reward weights by category: company, unit, individual (%)", max_multiplier: "Highest performance multiplier (%)",
  unit_factor: "Unit factor weights and caps (%)",
};

function setAt(target: unknown, path: (string | number)[], value: string): unknown {
  if (!path.length) return value;
  const [head, ...rest] = path;
  if (Array.isArray(target)) return target.map((item, index) => (index === head ? setAt(item, rest, value) : item));
  return { ...(target as Record<string, unknown>), [head]: setAt((target as Record<string, unknown>)[head], rest, value) };
}

function Leaves({ value, path, onChange, disabled }: { value: unknown; path: (string | number)[]; onChange: (path: (string | number)[], next: string) => void; disabled: boolean }) {
  if (typeof value === "string" || typeof value === "number") {
    const label = path.join(" · ");
    return <span className="al-edit"><input aria-label={label} value={String(value)} disabled={disabled} onChange={(event) => onChange(path, event.target.value)} /></span>;
  }
  if (Array.isArray(value)) {
    return <div className="ic-rows">{value.map((row, index) => <div key={index} className="ic-row">
      {Object.entries(row as Record<string, unknown>).map(([key, item]) => <label key={key}>{key === "from" ? "from %" : key === "to" ? "to %" : key === "value" ? "gives %" : key === "rate" ? "rate %" : key}
        <Leaves value={item} path={[...path, index, key]} onChange={onChange} disabled={disabled} /></label>)}
    </div>)}</div>;
  }
  return <div className="ic-rows">{Object.entries(value as Record<string, unknown>).map(([key, item]) => <div key={key} className="ic-row">
    <span className="ic-key">{key.replace(/_/g, " ")}</span><Leaves value={item} path={[...path, key]} onChange={onChange} disabled={disabled} />
  </div>)}</div>;
}

function PolicyTab({ overview, busy, post }: { overview: Overview; busy: boolean; post: (body: Record<string, unknown>, message: string) => void }) {
  const [draft, setDraft] = useState<Policy>(overview.policy ?? {});
  const [reason, setReason] = useState("");
  const changed = JSON.stringify(draft) !== JSON.stringify(overview.policy);
  const editable = overview.can_prepare && !overview.frozen;
  return <>
    <p className="pr-status"><span className={`sp-status sp-status-${overview.policy_status === "approved" ? "approved" : "waiting"}`}>{overview.policy_status_label}</span>
      {" "}Version {overview.policy_version}{overview.policy_approved_by ? `, approved by ${show(overview.policy_approved_by)}` : ". These are the brief's working figures until someone approves them."}</p>
    <p className="sp-hint">Each threshold applies from itself until the next one; nothing in between is interpolated. Every change makes a new version, kept in the history, and someone other than the last editor approves it.</p>
    <div className="sp-actions">
      {overview.can_approve && overview.policy_status !== "approved" && !changed && <button className="button button-dark" type="button" disabled={busy}
        onClick={() => post({ action: "approve_policy" }, `Version ${overview.policy_version} of the rules approved.`)}>Approve these rules</button>}
    </div>
    {[...Object.keys(POLICY_TITLES), ...Object.keys(draft).filter((key) => !(key in POLICY_TITLES))].filter((key) => key !== "label" && key in draft)
      .map((key) => [key, draft[key]] as const).map(([key, value]) => <section key={key} className="ic-policy" aria-label={POLICY_TITLES[key] ?? key}>
      <h4>{POLICY_TITLES[key] ?? key}</h4>
      <Leaves value={value} path={[key]} disabled={!editable} onChange={(path, next) => setDraft(setAt(draft, path, next) as Policy)} />
    </section>)}
    {editable && changed && <div className="ic-form">
      <Field label="Why the rules changed" value={reason} onChange={setReason} />
      <button className="button button-dark" type="button" disabled={busy} onClick={() => post({ action: "policy", policy: draft, reason }, "New version of the rules saved. Someone else approves it.")}>Save new version</button>
      <button className="button button-outline" type="button" onClick={() => setDraft(overview.policy ?? {})}>Undo changes</button>
    </div>}
  </>;
}

function RunsTab({ overview, busy, post }: { overview: Overview; busy: boolean; post: (body: Record<string, unknown>, message: string) => void }) {
  const runs = overview.runs ?? [];
  const open = runs.find((run) => run.status === "prepared" || run.status === "checked");
  return <>
    <p className="sp-hint">Finance prepares a run once the year is over, HR checks it, management approves it (which records the cost), and payroll releases it. Each step is a different person, and never someone the run pays. Pending lines are not paid; a later run picks them up.</p>
    <div className="sp-actions">
      {overview.can_prepare && overview.year_over && open?.status !== "checked" && <button className="button button-dark" type="button" disabled={busy}
        onClick={() => post({ action: "prepare" }, "Run prepared and sent to HR to check.")}>{open ? "Prepare it again" : `Prepare ${overview.year} commission and rewards`}</button>}
      {!overview.year_over && <small>A run can be prepared once {overview.year} is over.</small>}
    </div>
    {runs.length === 0 ? <p className="sp-empty">No runs yet.</p> : runs.map((run) => <section key={run.id} className="ic-run" aria-label={`Run ${run.number}`}>
      <h3>Run {run.number} <span className={`sp-status sp-status-${["approved", "released", "paid"].includes(run.status) ? "approved" : "waiting"}`}>{run.status_label}</span></h3>
      <p className="sp-hint">Rules version {run.policy_version}. Prepared by {show(run.prepared_by)} on {day(run.prepared_at)}{run.checked_by && `, checked by ${show(run.checked_by)}`}{run.approved_by && `, approved by ${show(run.approved_by)}`}{run.released_by && `, released by ${show(run.released_by)}`}.</p>
      <ul className="bc-accounts ex-tiles"><li className="bc-total"><span>Commission</span><strong>{qar(run.commission_total)}</strong><small>QAR</small></li>
        <li className="bc-total"><span>Fixed rewards</span><strong>{qar(run.reward_total)}</strong><small>QAR</small></li>
        <li className="bc-total"><span>Pending</span><strong>{run.pending_count}</strong><small>not paid by this run</small></li></ul>
      <div className="sp-actions">
        {run.can_check && <button className="button button-dark" type="button" disabled={busy} onClick={() => post({ action: "check", run_id: run.id }, `Run ${run.number} checked and sent for approval.`)}>Mark as checked</button>}
        {run.can_approve && <button className="button button-dark" type="button" disabled={busy} onClick={() => post({ action: "approve", run_id: run.id }, `Run ${run.number} approved and its cost recorded.`)}>Approve run</button>}
        {run.can_release && <button className="button button-dark" type="button" disabled={busy} onClick={() => post({ action: "release", run_id: run.id }, `Run ${run.number} released to payroll.`)}>Release to payroll</button>}
        {(run.status === "prepared" || run.status === "checked") && (overview.can_prepare || overview.can_check || overview.can_approve) && <button className="button button-outline" type="button" disabled={busy}
          onClick={() => post({ action: "send_back", run_id: run.id }, `Run ${run.number} sent back.`)}>Send back</button>}
      </div>
      <div className="sp-table-wrap"><table className="sp-table" aria-label={`Run ${run.number} lines`}>
        <thead><tr><th scope="col">Person</th><th scope="col">Plan</th><th scope="col">Amount (QAR)</th></tr></thead>
        <tbody>{run.lines.map((line) => <tr key={line.entitlement}><th scope="row">{show(line.name)}<small>{line.staff_number} · {show(line.unit)}</small></th>
          <td>{line.plan_label}<small>{line.months}</small></td><td className="bc-num"><Amount line={line} /><Why line={line} /></td></tr>)}</tbody>
      </table></div>
      {run.instalments.length > 0 && <div className="sp-table-wrap"><table className="sp-table" aria-label={`Run ${run.number} payments`}>
        <thead><tr><th scope="col">Person</th><th scope="col">What</th><th scope="col">Payroll month</th><th scope="col">Amount (QAR)</th><th scope="col">Paid</th></tr></thead>
        <tbody>{run.instalments.map((row, index) => <tr key={index}><th scope="row">{show(row.employee ?? "")}</th><td>{row.kind}</td><td>{monthName(row.month)}</td>
          <td className="bc-num">{qar(row.amount)}</td><td>{row.paid ? "Paid" : "Waiting for payroll"}</td></tr>)}</tbody>
      </table></div>}
    </section>)}
  </>;
}

function Statement({ mine }: { mine: NonNullable<Overview["mine"]> }) {
  return <section className="ic-mine" aria-label="My commission and rewards">
    <h3>My commission and rewards</h3>
    {mine.plans.length === 0 ? <p className="sp-empty">You have no incentive plan for this year.</p>
      : <ul>{mine.plans.map((plan, index) => <li key={index}>{plan.plan_label}, months {plan.months}: {plan.status_label.toLowerCase()}{plan.revenue_target ? `. Target QAR ${qar(plan.revenue_target)}${plan.revenue_actual ? `, validated sales QAR ${qar(plan.revenue_actual)}` : ""}` : ""}.</li>)}</ul>}
    {mine.lines.map((line) => <div key={line.entitlement}><strong>{line.plan_label}:</strong> <Amount line={line} /> QAR <Why line={line} /></div>)}
    {mine.instalments.length > 0 && <div className="sp-table-wrap"><table className="sp-table" aria-label="My payments">
      <thead><tr><th scope="col">What</th><th scope="col">Payroll month</th><th scope="col">Amount (QAR)</th><th scope="col">Paid</th></tr></thead>
      <tbody>{mine.instalments.map((row, index) => <tr key={index}><td>{row.kind}</td><td>{monthName(row.month)}</td><td className="bc-num">{qar(row.amount)}</td><td>{row.paid ? "Paid" : "To come"}</td></tr>)}</tbody>
    </table></div>}
  </section>;
}

export default function Incentives({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [year, setYear] = useState(() => String(new Date().getFullYear() - 1));
  const [tab, setTab] = useState<Tab>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const path = `/api/hr/incentives/?unit=${selectedUnitId ?? ""}`;

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    try {
      setOverview(await call<Overview>(`${path}&year=${year}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Commission and rewards could not be loaded.");
    }
  }, [path, year, selectedUnitId]);
  useEffect(() => { void load(); }, [load]);

  const post = async (body: Record<string, unknown>, message: string) => {
    setBusy(true); setError(""); setNotice("");
    try {
      setOverview(await call<Overview>(path, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify({ year: Number(year), ...body }),
      }));
      setNotice(message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That could not be done.");
    } finally { setBusy(false); }
  };
  const run = (body: Record<string, unknown>, message: string) => { void post(body, message); };

  return <section className="content-card supplier-payments incentives" aria-labelledby="incentives-title">
    <header className="sp-head">
      <span className="eyebrow">HR & PAYROLL · COMMISSION AND REWARDS · TEST ONLY{overview ? ` · ${show(overview.entity)}` : ""}</span>
      <h2 id="incentives-title">Commission and rewards</h2>
      <p>Sales commission, supervisor commission and fixed QAR performance rewards for the year, worked out from one set of company and unit figures. Finance prepares, HR checks, management approves and payroll pays.</p>
    </header>
    <div className="sp-actions">
      <label className="sc-month">Year <input aria-label="Incentive year" type="number" min={2000} max={2100} value={year} onChange={(event) => event.target.value && setYear(event.target.value)} /></label>
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading commission and rewards…</p>}
    {overview && !overview.exists && <>
      <p className="sp-empty">{overview.year} is not set up yet.</p>
      {overview.can_prepare && <button className="button button-dark" type="button" disabled={busy}
        onClick={() => run({ action: "open_year" }, `${overview.year} set up with the brief's working rules, marked provisional.`)}>Set up {overview.year}</button>}
    </>}
    {overview?.exists && overview.mine && (overview.mine.plans.length > 0 || !overview.full) && <Statement mine={overview.mine} />}
    {overview?.exists && overview.full && <>
      <div className="an-tabs" role="tablist" aria-label="Commission and rewards">
        {(Object.keys(TABS) as Tab[]).map((key) => <button key={key} role="tab" type="button" aria-selected={tab === key}
          className={tab === key ? "an-tab an-tab-on" : "an-tab"} onClick={() => setTab(key)}>{TABS[key]}</button>)}
      </div>
      {overview.frozen && <p className="sp-hint">A run for {overview.year} is approved, so its rules and figures are frozen.</p>}
      {tab === "overview" && <CompanyTab overview={overview} busy={busy} post={run} />}
      {tab === "people" && <PeopleTab overview={overview} busy={busy} post={run} />}
      {tab === "teams" && <TeamsTab overview={overview} busy={busy} post={run} />}
      {tab === "policy" && <PolicyTab key={`${overview.policy_version}`} overview={overview} busy={busy} post={run} />}
      {tab === "runs" && <RunsTab overview={overview} busy={busy} post={run} />}
      {tab === "history" && <ul className="ic-history">{(overview.history ?? []).map((event, index) => <li key={index}>{day(event.at)}: {show(event.by)} {event.what}.</li>)}</ul>}
    </>}
    {overview && <p className="sp-hint">{show(overview.policy_label)}</p>}
  </section>;
}
