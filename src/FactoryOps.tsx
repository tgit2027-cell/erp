import { useCallback, useEffect, useState, type FormEvent } from "react";
import { STAGE_COLOR } from "./FactoryPlanning";

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The request could not be completed.");
  return data as T;
}
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const qar = (value: string | number) => Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = (value: string | number) => Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 });
const day = (value: string) => value ? new Date(`${value}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "–";
const errorText = (caught: unknown, fallback: string) => (caught instanceof Error ? show(caught.message) : fallback);
type Option = { id: string; label: string };

function useLoad<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setData(await call<T>(path)); setError(""); }
    catch (caught) { setError(errorText(caught, "This page could not be loaded.")); }
  }, [path]);
  useEffect(() => { void load(); }, [load]);
  return { data, error, load, setData };
}

function useSend<T>(unitId: number, csrfToken: string, onDone: (result: T, message: string) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const send = async (path: string, body: unknown, message: string) => {
    setBusy(true); setError("");
    try {
      const result = await call<T>(`${path}?unit=${unitId}`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify(body) });
      onDone(result, message);
      return true;
    } catch (caught) {
      setError(errorText(caught, "That could not be saved."));
      return false;
    } finally { setBusy(false); }
  };
  return { busy, error, send };
}

function Loading({ error }: { error: string }) {
  return error ? <div className="alert" role="alert">{error}</div> : <p className="sp-empty" role="status">Loading…</p>;
}

// Factory team and workload.

type Assignment = { job: string; project: string; code: string; description: string; stage: string; role: string; status: string; status_label: string; planned_start: string; planned_end: string; finished_on: string; late: boolean };
type Worker = {
  id: number; name: string; job_title: string; department: string; section: string; section_label: string;
  active: number; planned_soon: number; assigned: number; late: number; load: "busy" | "normal" | "free"; load_label: string;
  hours_week: string; hours_month: string; hours_year: string; completed_year: number; work: Assignment[];
  hours_by_jd?: { job: string; code: string; description: string; hours: string; overtime: string; last: string }[];
  done?: Assignment[];
};
type Team = {
  today: string; people: Worker[]; sections: Option[];
  summary: { people: number; busy: number; free: number; no_section: number };
  can_keep_team: boolean; staff: { id: number; name: string; department: string }[];
};

function PersonRecord({ unitId, person }: { unitId: number; person: Worker }) {
  const { data, error } = useLoad<Team>(`/api/factory/team/?unit=${unitId}&person=${person.id}`);
  const record = data?.people[0];
  if (!record) return <Loading error={error} />;
  return <div className="fo-record">
    <section><h4>Assigned now ({record.work.length})</h4>
      {record.work.length ? <ul className="fp-items">{record.work.map((row, index) => <li key={`${row.job}-${row.code}-${row.stage}-${index}`}>
        <span className={`fp-status fp-status-${row.status}`}>{row.status_label}</span> <span className="pw-mono">{row.code}</span> {show(row.description)} · {row.stage} ({row.role.toLowerCase()})
        <small className={row.late ? "fp-late" : undefined}>{row.job}{row.project && ` · ${show(row.project)}`} · {row.planned_start ? `${day(row.planned_start)} – ${day(row.planned_end)}` : "not planned yet"}{row.late && " · late"}</small></li>)}</ul>
        : <p className="sp-empty">Nothing assigned.</p>}</section>
    <section><h4>Hours by JD</h4>
      {record.hours_by_jd?.length ? <table className="sp-table bc-table fp-mini"><thead><tr><th scope="col">JD</th><th scope="col" className="bc-num">Hours</th><th scope="col">Last worked</th></tr></thead>
        <tbody>{record.hours_by_jd.map((row) => <tr key={`${row.job}-${row.code}`}><th scope="row"><span className="pw-mono">{row.code}</span><small>{show(row.description)}{row.description && " · "}{row.job}</small></th>
          <td className="bc-num">{num(row.hours)}{Number(row.overtime) > 0 && <small>{num(row.overtime)} overtime</small>}</td><td>{day(row.last)}</td></tr>)}</tbody></table>
        : <p className="sp-empty">No hours recorded yet.</p>}</section>
    <section><h4>Completed in the last year ({record.done?.length ?? 0})</h4>
      {record.done?.length ? <ul className="fp-items">{record.done.map((row, index) => <li key={`${row.job}-${row.code}-${row.stage}-${index}`}><span className="pw-mono">{row.code}</span> {show(row.description)} · {row.stage}<small>{row.job} · done {day(row.finished_on)}</small></li>)}</ul>
        : <p className="sp-empty">Nothing completed yet.</p>}</section>
  </div>;
}

export function TeamView({ unitId, csrfToken }: { unitId: number; csrfToken: string }) {
  const { data, error, setData } = useLoad<Team>(`/api/factory/team/?unit=${unitId}`);
  const [notice, setNotice] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [adding, setAdding] = useState({ employee_id: "", section: "" });
  const [section, setSection] = useState("");
  const { busy, error: sendError, send } = useSend<Team>(unitId, csrfToken, (result, message) => { setData(result); setNotice(message); });
  if (!data) return <Loading error={error} />;
  const rows = data.people.filter((person) => !section || person.section === section || (section === "none" && !person.section));
  return <div className="fo-page">
    <div className="fp-tiles">
      <div className="fp-tile"><span>Factory staff</span><strong>{data.summary.people}</strong></div>
      <div className={`fp-tile${data.summary.busy ? " fp-tile-red" : ""}`}><span>Busy</span><strong>{data.summary.busy}</strong><small>3+ stages at once, or late</small></div>
      <div className="fp-tile fp-tile-green"><span>Free</span><strong>{data.summary.free}</strong><small>nothing now or in the next 14 days</small></div>
      <div className="fp-tile"><span>Without a section</span><strong>{data.summary.no_section}</strong></div>
    </div>
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {(sendError || error) && <div className="alert" role="alert">{sendError || error}</div>}
    <div className="fp-toolbar"><select aria-label="Section" value={section} onChange={(event) => setSection(event.target.value)}>
      <option value="">All sections</option>{data.sections.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}<option value="none">No section yet</option></select></div>
    {rows.length === 0 ? <p className="sp-empty">No factory staff{section ? " in this section" : ""}. Employees in the Factory department appear here.</p>
      : <div className="sp-table-wrap"><table className="sp-table bc-table fo-team" aria-label="Factory team">
        <thead><tr><th scope="col">Person</th><th scope="col">Section</th><th scope="col">Load</th><th scope="col" className="bc-num">Working on</th><th scope="col" className="bc-num">Late</th><th scope="col" className="bc-num">Hours this week</th><th scope="col" className="bc-num">This month</th><th scope="col" className="bc-num">Done this year</th></tr></thead>
        <tbody>{rows.map((person) => <PersonRow key={person.id} person={person} team={data} open={open === person.id} onToggle={() => setOpen(open === person.id ? null : person.id)} busy={busy}
          onSection={(value) => void send("/api/factory/team/", { employee_id: person.id, section: value }, `${show(person.name)} is ${value ? `in ${data.sections.find((option) => option.id === value)?.label}` : "without a section"}.`)} unitId={unitId} />)}</tbody>
      </table></div>}
    {data.can_keep_team && data.staff.length > 0 && <form className="fp-tag-form fo-add" aria-label="Add someone to the factory" onSubmit={(event: FormEvent) => {
      event.preventDefault();
      void send("/api/factory/team/", { employee_id: Number(adding.employee_id), section: adding.section }, "Added to the factory team.").then((ok) => ok && setAdding({ employee_id: "", section: "" }));
    }}>
      <span>Add someone from another department:</span>
      <select aria-label="Employee" value={adding.employee_id} onChange={(event) => setAdding({ ...adding, employee_id: event.target.value })}><option value="">Employee…</option>
        {data.staff.map((person) => <option key={person.id} value={person.id}>{show(person.name)} · {person.department}</option>)}</select>
      <select aria-label="Their section" value={adding.section} onChange={(event) => setAdding({ ...adding, section: event.target.value })}><option value="">Section…</option>
        {data.sections.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select>
      <button className="button button-outline" type="submit" disabled={busy || !adding.employee_id || !adding.section}>Add</button>
    </form>}
    <p className="sp-hint">The factory's people are the employees in the Factory department, plus anyone the Factory Manager puts in a section. The section decides where they appear first in each stage's team list.</p>
  </div>;
}

function PersonRow({ person, team, open, onToggle, busy, onSection, unitId }: { person: Worker; team: Team; open: boolean; onToggle: () => void; busy: boolean; onSection: (value: string) => void; unitId: number }) {
  return <>
    <tr className={open ? "fp-open" : undefined}>
      <th scope="row"><button type="button" className="fp-toggle" aria-expanded={open} onClick={onToggle}>{show(person.name)}</button><small>{person.job_title}</small></th>
      <td>{team.can_keep_team ? <select aria-label={`${show(person.name)} section`} value={person.section} disabled={busy} onChange={(event) => onSection(event.target.value)}>
        <option value="">No section</option>{team.sections.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select> : person.section_label || "–"}</td>
      <td><span className={`fp-load fp-load-${person.load}`}>{person.load_label}</span></td>
      <td className="bc-num">{person.active}<small>{person.planned_soon} planned soon · {person.assigned} assigned</small></td>
      <td className={`bc-num${person.late ? " fp-late" : ""}`}>{person.late}</td>
      <td className="bc-num">{num(person.hours_week)}</td>
      <td className="bc-num">{num(person.hours_month)}<small>{num(person.hours_year)} in 12 months</small></td>
      <td className="bc-num">{person.completed_year}</td>
    </tr>
    {open && <tr className="fp-detail-row"><td colSpan={8}><PersonRecord unitId={unitId} person={person} /></td></tr>}
  </>;
}

// Machines, maintenance and spare parts.

type Machine = {
  id: number; code: string; name: string; kind: string; kind_label: string; section: string; section_label: string;
  make_model: string; serial_number: string; quantity: number; condition: string; condition_label: string; bought_on: string;
  cost: string; service_every_days: number | ""; last_service_on: string; next_service_on: string;
  service: "none" | "overdue" | "soon" | "ok"; service_label: string; key_machine: boolean;
  treatment: "asset" | "expense"; treatment_label: string; fixed_asset: { number: string; status_label: string } | null; finance_missing: boolean;
  note: string; is_active: boolean; services: { id: number; kind_label: string; done_on: string; done_by: string; cost: string; note: string }[]; can_update: boolean;
};
type Part = { id: number; name: string; kind: string; kind_label: string; machine_id: number | null; machine: string; unit: string; on_hand: string; reorder_at: string; unit_cost: string; value: string; order_needed: boolean; is_active: boolean };
type Machines = {
  today: string; machines: Machine[]; parts: Part[]; alerts: { level: "red" | "amber"; kind: string; text: string }[];
  summary: { machines: number; need_action: number; maintenance_due: number; parts_to_order: number; by_kind: { kind: string; label: string; count: number }[]; by_condition: { condition: string; label: string; count: number }[] };
  kinds: Option[]; conditions: Option[]; sections: Option[]; service_kinds: Option[]; part_kinds: Option[];
  policy: { asset_threshold: string; life_months: number; due_soon_days: number }; can_update: boolean;
};
const blankMachine = { id: "", code: "", name: "", kind: "cnc", section: "cnc", make_model: "", serial_number: "", quantity: "1", condition: "good", bought_on: "", cost: "", service_every_days: "", last_service_on: "", note: "", is_active: true };
const blankPart = { id: "", name: "", kind: "consumable", machine_id: "", unit: "pcs", on_hand: "", reorder_at: "", unit_cost: "", is_active: true };

function MachineForm({ data, machine, send, busy, onClose }: { data: Machines; machine?: Machine; send: (path: string, body: unknown, message: string) => Promise<boolean>; busy: boolean; onClose: () => void }) {
  const [form, setForm] = useState(machine ? { ...blankMachine, ...machine, id: String(machine.id), quantity: String(machine.quantity), service_every_days: String(machine.service_every_days), cost: machine.cost } : blankMachine);
  const set = (key: string, value: string | boolean) => setForm({ ...form, [key]: value });
  const asset = Number(form.cost || 0) >= Number(data.policy.asset_threshold) && Number(form.cost || 0) > 0;
  return <form className="sp-form fac-form" aria-label={machine ? `Change ${machine.code}` : "Add a machine"} onSubmit={(event) => {
    event.preventDefault();
    void send("/api/factory/machines/", { ...form, id: form.id ? Number(form.id) : null, quantity: Number(form.quantity), service_every_days: form.service_every_days ? Number(form.service_every_days) : null },
      `${form.code.toUpperCase()} is saved.`).then((ok) => ok && onClose());
  }}>
    <h3>{machine ? `Change ${machine.code}` : "Add a machine"}</h3>
    <div className="sp-fields fac-grid">
      <label>Code<input value={form.code} maxLength={20} required placeholder="e.g. CNC-1" onChange={(event) => set("code", event.target.value)} /></label>
      <label className="fac-wide">Name<input value={form.name} maxLength={120} required placeholder="e.g. Biesse Rover CNC router" onChange={(event) => set("name", event.target.value)} /></label>
      <label>Kind<select value={form.kind} onChange={(event) => set("kind", event.target.value)}>{data.kinds.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>
      <label>Section<select value={form.section} onChange={(event) => set("section", event.target.value)}>{data.sections.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>
      <label>Condition<select value={form.condition} onChange={(event) => set("condition", event.target.value)}>{data.conditions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>
      <label>How many<input value={form.quantity} inputMode="numeric" onChange={(event) => set("quantity", event.target.value)} /></label>
      <label>Make and model<input value={form.make_model} maxLength={120} onChange={(event) => set("make_model", event.target.value)} /></label>
      <label>Serial number<input value={form.serial_number} maxLength={60} onChange={(event) => set("serial_number", event.target.value)} /></label>
      <label>Bought on<input type="date" value={form.bought_on} max={data.today} onChange={(event) => set("bought_on", event.target.value)} /></label>
      <label>Cost (QAR, each)<input value={form.cost} inputMode="decimal" onChange={(event) => set("cost", event.target.value)} /></label>
      <label>Service every (days)<input value={form.service_every_days} inputMode="numeric" placeholder="e.g. 90" onChange={(event) => set("service_every_days", event.target.value)} /></label>
      <label>Last serviced<input type="date" value={form.last_service_on} max={data.today} onChange={(event) => set("last_service_on", event.target.value)} /></label>
      <label className="fac-wide">Note<input value={form.note} maxLength={200} onChange={(event) => set("note", event.target.value)} /></label>
      {machine && <label className="fac-check"><input type="checkbox" checked={form.is_active} onChange={(event) => set("is_active", event.target.checked)} /> Still in use</label>}
    </div>
    {form.cost && <p className="sp-hint">Finance: {asset ? `a fixed asset (from QAR ${qar(data.policy.asset_threshold)}), depreciated over ${data.policy.life_months} months. Register it in Finance › Fixed assets as Machinery and tools, then tie it here.` : `below QAR ${qar(data.policy.asset_threshold)}, so an expense (consumable).`}</p>}
    <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy}>Save</button><button className="button button-quiet" type="button" onClick={onClose}>Back</button></div>
  </form>;
}

function MachineDetail({ machine, data, send, busy }: { machine: Machine; data: Machines; send: (path: string, body: unknown, message: string) => Promise<boolean>; busy: boolean }) {
  const [service, setService] = useState({ kind: "routine", done_on: data.today, done_by: "", cost: "", note: "", condition: "" });
  const [asset, setAsset] = useState(machine.fixed_asset?.number ?? "");
  return <div className="fo-machine">
    {machine.can_update && <div className="mj-inline sl-inline fac-inline" aria-label={`Record service on ${machine.code}`}>
      <select aria-label="What was done" value={service.kind} onChange={(event) => setService({ ...service, kind: event.target.value })}>{data.service_kinds.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select>
      <input aria-label="Date done" type="date" value={service.done_on} max={data.today} onChange={(event) => setService({ ...service, done_on: event.target.value })} />
      <input aria-label="Done by" value={service.done_by} placeholder="Done by (person or supplier)" onChange={(event) => setService({ ...service, done_by: event.target.value })} />
      <input aria-label="Service cost" value={service.cost} inputMode="decimal" placeholder="Cost QAR" className="fac-qty" onChange={(event) => setService({ ...service, cost: event.target.value })} />
      <select aria-label="Condition after" value={service.condition} onChange={(event) => setService({ ...service, condition: event.target.value })}><option value="">Condition unchanged</option>{data.conditions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select>
      <input aria-label="Service note" value={service.note} placeholder="What was done" onChange={(event) => setService({ ...service, note: event.target.value })} />
      <button className="button button-dark" type="button" disabled={busy} onClick={() => void send(`/api/factory/machines/${machine.id}/service/`, service, `${machine.code}: service recorded.`)}>Record service</button>
    </div>}
    {machine.services.length > 0 ? <ul className="fp-items">{machine.services.map((row) => <li key={row.id}>{day(row.done_on)} · {row.kind_label}{row.done_by && ` by ${show(row.done_by)}`}{Number(row.cost) > 0 && ` · QAR ${qar(row.cost)}`}<small>{show(row.note)}</small></li>)}</ul>
      : <p className="sp-empty">No service recorded yet.</p>}
    {machine.treatment === "asset" && machine.can_update && <div className="mj-inline sl-inline fac-inline" aria-label={`Tie ${machine.code} to Finance`}>
      <span className="fac-hint">Finance fixed asset number:</span>
      <input aria-label="Fixed asset number" value={asset} placeholder="e.g. FA-0012" onChange={(event) => setAsset(event.target.value)} />
      <button className="button button-outline" type="button" disabled={busy} onClick={() => void send(`/api/factory/machines/${machine.id}/asset/`, { asset_number: asset }, asset ? `${machine.code} is tied to ${asset}.` : `${machine.code} is no longer tied to a fixed asset.`)}>Save</button>
    </div>}
  </div>;
}

export function MachinesView({ unitId, csrfToken }: { unitId: number; csrfToken: string }) {
  const { data, error, setData } = useLoad<Machines>(`/api/factory/machines/?unit=${unitId}`);
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<Machine | "new" | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [part, setPart] = useState(blankPart);
  const [use, setUse] = useState<Record<number, string>>({});
  const { busy, error: sendError, send } = useSend<Machines>(unitId, csrfToken, (result, message) => { setData(result); setNotice(message); });
  if (!data) return <Loading error={error} />;
  return <div className="fo-page">
    <div className="fp-tiles">
      <div className="fp-tile"><span>Machines</span><strong>{data.summary.machines}</strong><small>{data.summary.by_kind.slice(0, 3).map((row) => `${row.count} ${row.label.toLowerCase()}`).join(" · ")}</small></div>
      <div className={`fp-tile${data.summary.maintenance_due ? " fp-tile-red" : ""}`}><span>Maintenance due</span><strong>{data.summary.maintenance_due}</strong><small>overdue or within {data.policy.due_soon_days} days</small></div>
      <div className={`fp-tile${data.summary.need_action ? " fp-tile-red" : ""}`}><span>Need repair or replacing</span><strong>{data.summary.need_action}</strong></div>
      <div className={`fp-tile${data.summary.parts_to_order ? " fp-tile-red" : ""}`}><span>Parts to order</span><strong>{data.summary.parts_to_order}</strong></div>
    </div>
    {data.alerts.length > 0 && <section className="fp-card fo-alerts" aria-label="Machine alerts"><h3>Alerts</h3>
      <ul className="fp-items">{data.alerts.map((alert, index) => <li key={index} className={`fo-alert-${alert.level}`}>{show(alert.text)}</li>)}</ul></section>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {sendError && <div className="alert" role="alert">{sendError}</div>}
    {data.can_update && editing === null && <div className="sp-actions"><button className="button button-dark" type="button" onClick={() => setEditing("new")}>Add a machine</button></div>}
    {editing !== null && <MachineForm key={editing === "new" ? "new" : editing.id} data={data} machine={editing === "new" ? undefined : editing} send={send} busy={busy} onClose={() => setEditing(null)} />}
    {data.machines.length === 0 ? <p className="sp-empty">No machines on the list yet.</p>
      : <div className="sp-table-wrap"><table className="sp-table bc-table fo-machines" aria-label="Machines">
        <thead><tr><th scope="col">Machine</th><th scope="col">Section</th><th scope="col" className="bc-num">Count</th><th scope="col">Condition</th><th scope="col">Maintenance</th><th scope="col">Finance</th><th scope="col" /></tr></thead>
        <tbody>{data.machines.map((machine) => <MachineRows key={machine.id} machine={machine} data={data} open={open === machine.id} onToggle={() => setOpen(open === machine.id ? null : machine.id)} onEdit={() => setEditing(machine)} send={send} busy={busy} />)}</tbody>
      </table></div>}
    <section className="fo-parts" aria-label="Spare parts and consumables">
      <h3>Spare parts and consumables</h3>
      <p className="sp-hint">Always an expense. A line turns red when what is left reaches its order-at level.</p>
      {data.parts.length === 0 ? <p className="sp-empty">No spare parts or consumables yet.</p>
        : <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label="Parts">
          <thead><tr><th scope="col">Part</th><th scope="col">For</th><th scope="col" className="bc-num">On hand</th><th scope="col" className="bc-num">Order at</th><th scope="col" className="bc-num">Value</th><th scope="col">Use or add</th></tr></thead>
          <tbody>{data.parts.map((row) => <tr key={row.id} className={row.order_needed ? "fac-short" : undefined}>
            <th scope="row">{show(row.name)}<small>{row.kind_label}{row.order_needed && " · order more"}</small></th><td>{row.machine ? show(row.machine) : "Any machine"}</td>
            <td className="bc-num">{num(row.on_hand)} <small>{row.unit}</small></td><td className="bc-num">{num(row.reorder_at)}</td><td className="bc-num">{qar(row.value)}</td>
            <td>{data.can_update && <span className="fo-use"><input aria-label={`Quantity of ${show(row.name)}`} value={use[row.id] ?? ""} inputMode="decimal" className="fac-qty" placeholder="Qty" onChange={(event) => setUse({ ...use, [row.id]: event.target.value })} />
              <button className="button button-quiet" type="button" disabled={busy || !use[row.id]} onClick={() => void send("/api/factory/parts/", { id: row.id, change: "use", quantity: use[row.id] }, `${show(row.name)}: ${use[row.id]} used.`).then((ok) => ok && setUse({ ...use, [row.id]: "" }))}>Used</button>
              <button className="button button-quiet" type="button" disabled={busy || !use[row.id]} onClick={() => void send("/api/factory/parts/", { id: row.id, change: "add", quantity: use[row.id] }, `${show(row.name)}: ${use[row.id]} added.`).then((ok) => ok && setUse({ ...use, [row.id]: "" }))}>Received</button></span>}</td>
          </tr>)}</tbody></table></div>}
      {data.can_update && <form className="fp-tag-form fo-add" aria-label="Add a part" onSubmit={(event) => {
        event.preventDefault();
        void send("/api/factory/parts/", { ...part, id: null, machine_id: part.machine_id ? Number(part.machine_id) : null }, `${part.name} is added.`).then((ok) => ok && setPart(blankPart));
      }}>
        <input aria-label="Part name" value={part.name} placeholder="e.g. CNC bit 6 mm" required onChange={(event) => setPart({ ...part, name: event.target.value })} />
        <select aria-label="Part kind" value={part.kind} onChange={(event) => setPart({ ...part, kind: event.target.value })}>{data.part_kinds.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select>
        <select aria-label="For machine" value={part.machine_id} onChange={(event) => setPart({ ...part, machine_id: event.target.value })}><option value="">Any machine</option>{data.machines.map((machine) => <option key={machine.id} value={machine.id}>{machine.code}</option>)}</select>
        <input aria-label="Unit" value={part.unit} className="fac-qty" onChange={(event) => setPart({ ...part, unit: event.target.value })} />
        <input aria-label="On hand" value={part.on_hand} inputMode="decimal" className="fac-qty" placeholder="On hand" onChange={(event) => setPart({ ...part, on_hand: event.target.value })} />
        <input aria-label="Order at" value={part.reorder_at} inputMode="decimal" className="fac-qty" placeholder="Order at" onChange={(event) => setPart({ ...part, reorder_at: event.target.value })} />
        <input aria-label="Unit cost" value={part.unit_cost} inputMode="decimal" className="fac-qty" placeholder="QAR each" onChange={(event) => setPart({ ...part, unit_cost: event.target.value })} />
        <button className="button button-outline" type="submit" disabled={busy || !part.name.trim()}>Add part</button>
      </form>}
    </section>
    <p className="sp-hint">Finance rule: a machine costing QAR {qar(data.policy.asset_threshold)} or more is a fixed asset (Machinery and tools, {data.policy.life_months} months); below that, and every spare part and consumable, is an expense. The threshold is set on Factory setup.</p>
  </div>;
}

function MachineRows({ machine, data, open, onToggle, onEdit, send, busy }: { machine: Machine; data: Machines; open: boolean; onToggle: () => void; onEdit: () => void; send: (path: string, body: unknown, message: string) => Promise<boolean>; busy: boolean }) {
  const tone = machine.condition === "repair" || machine.condition === "replace" || machine.condition === "out" ? "rejected" : machine.condition === "fair" ? "waiting" : "approved";
  return <>
    <tr className={`${open ? "fp-open " : ""}${machine.is_active ? "" : "fp-skipped"}`}>
      <th scope="row"><button type="button" className="fp-toggle" aria-expanded={open} onClick={onToggle}><span className="pw-mono">{machine.code}</span> {show(machine.name)}</button>
        <small>{machine.kind_label}{machine.make_model && ` · ${show(machine.make_model)}`}{!machine.is_active && " · no longer in use"}</small></th>
      <td>{machine.section_label}</td>
      <td className="bc-num">{machine.quantity}</td>
      <td><span className={`sp-status sp-status-${tone}`}>{machine.condition_label}</span></td>
      <td className={machine.service === "overdue" ? "fp-late" : undefined}>{machine.service_label}{machine.last_service_on && <small>last {day(machine.last_service_on)}{machine.service_every_days && `, every ${machine.service_every_days} days`}</small>}</td>
      <td>{machine.treatment_label}<small className={machine.finance_missing ? "fp-late" : undefined}>{machine.fixed_asset ? `${machine.fixed_asset.number} · ${machine.fixed_asset.status_label}` : machine.finance_missing ? "not yet in Finance's fixed assets" : `QAR ${qar(machine.cost)}`}</small></td>
      <td>{machine.can_update && <button className="button button-quiet" type="button" onClick={onEdit}>Change</button>}</td>
    </tr>
    {open && <tr className="fp-detail-row"><td colSpan={7}><MachineDetail machine={machine} data={data} send={send} busy={busy} /></td></tr>}
  </>;
}

// What the factory learned.

type Learned = {
  tags: { id: number; name: string; color: string; finished: number; profile: null | {
    samples: number; units: string; total_days: number; accessories_per_unit: string;
    stages: Record<string, { days: number | null; samples: number; usually_skipped: boolean }>;
    materials: { variant_id: number; sku: string; name: string; accessory: boolean; per_unit: string }[];
    hours_per_unit: Record<string, string>; leads: Record<string, { id: number; name: string; times: number }>;
  } }[];
  min_samples: number; years: number; finished_items: number; stages: { id: string; label: string; usual_days: number }[];
};

export function LearningView({ unitId }: { unitId: number }) {
  const { data, error } = useLoad<Learned>(`/api/factory/learning/?unit=${unitId}`);
  if (!data) return <Loading error={error} />;
  return <div className="fo-page">
    <p className="fac-intro">Learned from {data.finished_items} JDs finished in the last {data.years} years. A tag needs at least {data.min_samples} finished JDs before it is used. On the Production schedule, open a tagged JD and press "Suggest from past work" to fill it the same way.</p>
    {data.tags.length === 0 ? <p className="sp-empty">No tags yet. The Factory Manager adds them on the Production schedule page.</p>
      : <div className="fo-learned">{data.tags.map((tag) => <section key={tag.id} className="fp-card" aria-label={`Learned for ${tag.name}`}>
        <h3><span className="fp-tag" style={{ background: tag.color }}>{tag.name}</span> <small>{tag.finished} finished</small></h3>
        {!tag.profile ? <p className="sp-empty">Not enough finished work yet ({tag.finished} of {data.min_samples}).</p> : <>
          <div className="fo-stage-strip" role="img" aria-label={data.stages.filter((stage) => tag.profile!.stages[stage.id]?.days && !tag.profile!.stages[stage.id].usually_skipped).map((stage) => `${stage.label} ${tag.profile!.stages[stage.id].days} days`).join(", ")}>
            {data.stages.filter((stage) => tag.profile!.stages[stage.id]?.days && !tag.profile!.stages[stage.id].usually_skipped).map((stage) => <span key={stage.id} style={{ flexGrow: tag.profile!.stages[stage.id].days ?? 1, background: STAGE_COLOR[stage.id] }} title={`${stage.label}: ${tag.profile!.stages[stage.id].days} working days (usual plan ${stage.usual_days})`}>{tag.profile!.stages[stage.id].days}</span>)}
          </div>
          <dl className="fo-facts">
            <div><dt>Usual time</dt><dd>{tag.profile.total_days} working days</dd></div>
            <div><dt>Accessories per unit</dt><dd>{num(tag.profile.accessories_per_unit)}</dd></div>
            <div><dt>Not usually needed</dt><dd>{data.stages.filter((stage) => tag.profile!.stages[stage.id]?.usually_skipped).map((stage) => stage.label).join(", ") || "–"}</dd></div>
            <div><dt>Usual team</dt><dd>{Object.entries(tag.profile.leads).map(([kind, lead]) => `${data.stages.find((stage) => stage.id === kind)?.label}: ${show(lead.name)}`).join(" · ") || "–"}</dd></div>
            {Object.keys(tag.profile.hours_per_unit).length > 0 && <div><dt>Hours per unit</dt><dd>{Object.entries(tag.profile.hours_per_unit).map(([shop, hours]) => `${shop} ${num(hours)}`).join(" · ")}</dd></div>}
          </dl>
          {tag.profile.materials.length > 0 && <table className="sp-table bc-table fp-mini" aria-label={`${tag.name} materials per unit`}><thead><tr><th scope="col">Material per unit</th><th scope="col" className="bc-num">Qty</th></tr></thead>
            <tbody>{tag.profile.materials.map((row) => <tr key={row.variant_id}><th scope="row">{row.sku}<small>{show(row.name)}{row.accessory && " · accessory"}</small></th><td className="bc-num">{num(row.per_unit)}</td></tr>)}</tbody></table>}
          <p className="sp-hint">Based on {tag.profile.samples} JDs ({num(tag.profile.units)} units).</p>
        </>}
      </section>)}</div>}
  </div>;
}
