import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";
import "./end-of-service.css";

type Item = { label: string; amount: string; loan?: number };
type Figures = {
  last_day: string; service_days: number; unpaid_days: number; years: string; basic: string; gratuity: string; already_paid: string;
  leave_days: string; leave_pay: string; additions: Item[]; deductions: Item[]; total: string; day_rate: string; leave: string;
  joined_on: string; notes: string[];
};
type Settlement = {
  id: number; status: "prepared" | "approved" | "cancelled"; status_label: string; pay_month: string; pay_month_label: string;
  paid_in: string; figures: Figures; note: string; prepared_by: string; prepared_at: string; approved_by: string; approved_at: string;
  can_approve: boolean; can_change: boolean; can_cancel: boolean; payroll_to_redo?: boolean;
};
type Person = {
  id: number; name: string; staff_number: string; job_title: string; department: string; status_label: string; last_day: string;
  leaving_reason: string; settlement: Settlement | null; preview: Figures | null; pay_month_label?: string; problem: string;
};
type Overview = { unit: { id: number; name: string }; people: Person[]; can_prepare: boolean; policy: string };

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The request could not be completed.");
  return data as T;
}
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const qar = (value: string | number) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const day = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const blank = (): Item => ({ label: "", amount: "" });

function Statement({ figures }: { figures: Figures }) {
  const leave = Number(figures.leave_days);
  return <table className="eos-statement" aria-label="Settlement">
    <tbody>
      <tr><th scope="row">End of service<small>{figures.years} years from {day(figures.joined_on)}{figures.unpaid_days ? `, less ${figures.unpaid_days} days of unpaid leave` : ""} × basic {qar(figures.basic)} × 21 ÷ 30</small></th><td>{qar(figures.gratuity)}</td></tr>
      {Number(figures.already_paid) > 0 && <tr><th scope="row">Less end of service already paid<small>in an earlier payroll</small></th><td>−{qar(figures.already_paid)}</td></tr>}
      <tr><th scope="row">{leave < 0 ? "Leave taken beyond what was earned" : "Unused annual leave"}<small>{show(figures.leave)}: {figures.leave_days} days × {qar(figures.day_rate)}</small></th><td>{leave < 0 ? "−" : ""}{qar(Math.abs(Number(figures.leave_pay)))}</td></tr>
      {figures.additions.map((item) => <tr key={`a-${item.label}`}><th scope="row">{show(item.label)}</th><td>{qar(item.amount)}</td></tr>)}
      {figures.deductions.map((item) => <tr key={`d-${item.label}`}><th scope="row">Less {show(item.label)}</th><td>−{qar(item.amount)}</td></tr>)}
    </tbody>
    <tfoot><tr><th scope="row">Total to pay</th><td>{qar(figures.total)} QAR</td></tr></tfoot>
  </table>;
}

function Items({ title, items, onChange }: { title: string; items: Item[]; onChange: (items: Item[]) => void }) {
  const set = (index: number, field: keyof Item, value: string) => onChange(items.map((item, at) => at === index ? { ...item, [field]: value } : item));
  return <fieldset className="eos-items"><legend>{title}</legend>
    {items.map((item, index) => <div key={index} className="eos-item">
      <label>Reason<input value={item.label} maxLength={120} onChange={(event) => set(index, "label", event.target.value)} /></label>
      <label>Amount (QAR)<input inputMode="decimal" value={item.amount} onChange={(event) => set(index, "amount", event.target.value)} /></label>
      <button className="fa-link" type="button" onClick={() => onChange(items.filter((_, at) => at !== index))}>Remove</button>
    </div>)}
    {items.length < 10 && <button className="fa-link" type="button" onClick={() => onChange([...items, blank()])}>Add {items.length ? "another" : "one"}</button>}
  </fieldset>;
}

function PrepareForm({ person, unitId, csrfToken, onDone, onCancel }: {
  person: Person; unitId: number; csrfToken: string; onDone: (message: string) => void; onCancel: () => void;
}) {
  const current = person.settlement;
  const [additions, setAdditions] = useState<Item[]>(current?.figures.additions ?? []);
  const [deductions, setDeductions] = useState<Item[]>((current?.figures.deductions ?? []).filter((item) => !item.loan));
  const [note, setNote] = useState(current?.note ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const keep = (items: Item[]) => items.filter((item) => item.label.trim() || item.amount.trim());
      const saved = await call<Settlement>(`/api/hr/end-of-service/?unit=${unitId}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ employee_id: person.id, additions: keep(additions), deductions: keep(deductions), note }),
      });
      onDone(`${show(person.name)}'s settlement of ${qar(saved.figures.total)} QAR is ready. Someone with approval rights now checks and approves it.`);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "The settlement could not be prepared.");
    } finally { setBusy(false); }
  };
  return <form className="eos-form" onSubmit={submit} aria-label={`Prepare ${show(person.name)}'s settlement`}>
    <p>End of service and unused leave are worked out from the staff record, salary and leave. Add anything else agreed, each with a reason.</p>
    <Items title="Extra amounts (for example an air ticket)" items={additions} onChange={setAdditions} />
    <Items title="Deductions (for example equipment not returned)" items={deductions} onChange={setDeductions} />
    <label className="eos-note">Note for the approver (optional)<input value={note} maxLength={300} onChange={(event) => setNote(event.target.value)} /></label>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy}>{current ? "Work it out again" : "Prepare settlement"}</button>
      <button className="button button-quiet" type="button" onClick={onCancel}>Cancel</button>
    </div>
  </form>;
}

function PersonCard({ person, overview, unitId, csrfToken, onDone }: {
  person: Person; overview: Overview; unitId: number; csrfToken: string; onDone: (message: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const settlement = person.settlement;
  const figures = settlement?.figures ?? person.preview;
  const decide = async (action: "approve" | "cancel") => {
    setError("");
    try {
      const result = await call<Settlement>(`/api/hr/end-of-service/${settlement!.id}/?unit=${unitId}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify({ action, reason }),
      });
      onDone(action === "approve"
        ? `${show(person.name)}'s settlement is approved and will be paid in the ${result.pay_month_label} payroll.${result.payroll_to_redo ? ` That payroll is already worked out, so work it out again to include it.` : ""}`
        : `${show(person.name)}'s settlement is cancelled.`);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "That could not be done.");
    }
  };
  const tone = settlement?.status === "approved" ? "approved" : settlement ? "waiting" : "draft";
  return <article className="eos-card" aria-label={show(person.name)}>
    <header>
      <div><h3>{show(person.name)}</h3><p>{person.job_title} · {person.department} · {person.staff_number}</p>
        <p>{person.status_label}, last day {day(person.last_day)}{person.leaving_reason && ` · ${show(person.leaving_reason)}`}</p></div>
      <span className={`sp-status sp-status-${tone}`}>{settlement ? settlement.status_label : "Not prepared yet"}</span>
    </header>
    {person.problem && <p className="eos-problem">{show(person.problem)}</p>}
    {figures && <Statement figures={figures} />}
    {figures && figures.notes.length > 0 && <ul className="eos-notes">{figures.notes.map((note) => <li key={note}>{note}</li>)}</ul>}
    <p className="eos-who">{settlement
      ? <>Prepared by {settlement.prepared_by}{settlement.approved_by && `, approved by ${settlement.approved_by}`}. {settlement.paid_in ? `Paid in the ${settlement.paid_in} payroll.` : `To be paid in the ${settlement.pay_month_label} payroll as a WPS final settlement.`}{settlement.note && ` Note: ${show(settlement.note)}`}</>
      : person.preview ? `A first look. Once prepared and approved, it is paid in the ${person.pay_month_label} payroll.` : ""}</p>
    {error && <div className="alert" role="alert">{error}</div>}
    {editing ? <PrepareForm person={person} unitId={unitId} csrfToken={csrfToken} onDone={(message) => { setEditing(false); onDone(message); }} onCancel={() => setEditing(false)} />
      : cancelling ? <div className="mj-inline eos-cancel">
        <label>Why is it cancelled?<input value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} /></label>
        <button className="button button-dark" type="button" onClick={() => void decide("cancel")}>Cancel settlement</button>
        <button className="button button-quiet" type="button" onClick={() => setCancelling(false)}>Keep it</button>
      </div>
      : <div className="sp-actions">
        {settlement?.can_approve && <button className="button button-dark" type="button" onClick={() => void decide("approve")}>Approve</button>}
        {((!settlement && overview.can_prepare && !person.problem) || settlement?.can_change) && <button className={`button ${settlement ? "button-quiet" : "button-dark"}`} type="button" onClick={() => setEditing(true)}>{settlement ? "Change" : "Prepare settlement"}</button>}
        {settlement?.can_cancel && <button className="button button-quiet" type="button" onClick={() => setCancelling(true)}>Cancel</button>}
      </div>}
  </article>;
}

export default function EndOfService({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    if (selectedUnitId === null) { setError("Choose a business unit to see who is leaving."); return; }
    try {
      setOverview(await call<Overview>(`/api/hr/end-of-service/?unit=${selectedUnitId}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "End-of-service settlements could not be loaded.");
    }
  }, [selectedUnitId]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); void load(); };
  return <section className="content-card supplier-payments bank-cash end-of-service-page" aria-labelledby="eos-title">
    <header className="sp-head">
      <span className="eyebrow">HR · END OF SERVICE · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="eos-title">End of service</h2>
      <p>What is owed to someone who leaves: end of service pay, unused leave and anything else agreed. One person prepares it, another approves it, and it is paid with the payroll of the month they leave.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading…</p>}
    {overview && selectedUnitId !== null && <>
      {overview.people.length === 0
        ? <p className="sp-empty">Nobody in {overview.unit.name} is serving notice or has left. Record a last day on the staff record to start a settlement.</p>
        : <div className="eos-list">{overview.people.map((person) => <PersonCard key={`${person.id}-${person.settlement?.id ?? 0}-${person.settlement?.status ?? ""}`}
          person={person} overview={overview} unitId={selectedUnitId} csrfToken={csrfToken} onDone={done} />)}</div>}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
