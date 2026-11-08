import { Fragment, useCallback, useEffect, useState } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

const PARTS = ["basic", "housing", "transport", "mobile", "other"] as const;
type Part = typeof PARTS[number];
type Pay = Record<Part, string> & {
  id: number; effective_from: string; other_label: string; total: string; bank_name: string; iban: string; reason: string; set_by: string; set_at: string;
};
type Person = {
  id: number; name: string; staff_number: string; job_title: string; department: string; joined_on: string; is_me: boolean; hours_id: number | null;
  pay: Pay | null; upcoming: Pay | null; history: Pay[];
};
type Hours = { id: number; name: string; starts_at: string; ends_at: string; grace_minutes: number; people: number; set_by: string; set_at: string };
type Overview = {
  unit: { id: number; name: string }; month: string; people: Person[]; missing: number;
  totals: Record<Part | "total", string>; working_hours: Hours[]; no_hours: number; can_manage: boolean; can_set_hours: boolean; policy: string;
};

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string"
      ? data.detail
      : Object.values(data).flat().join(" ") || "The request could not be completed.";
    throw new Error(String(detail));
  }
  return data as T;
}

const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const headers = (csrfToken: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken });
const qar = (value: string | number) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
const LABELS: Record<Part, string> = { basic: "Basic", housing: "Housing", transport: "Transport", mobile: "Mobile", other: "Other" };

function ChangePay({ person, month, unitId, csrfToken, onDone, onCancel }: { person: Person; month: string; unitId: number; csrfToken: string; onDone: (message: string) => void; onCancel: () => void }) {
  const base = person.upcoming ?? person.pay;
  const [from, setFrom] = useState(base ? month : person.joined_on.slice(0, 7));
  const [amounts, setAmounts] = useState<Record<Part, string>>(Object.fromEntries(PARTS.map((part) => [part, base ? String(Number(base[part])) : ""])) as Record<Part, string>);
  const [otherLabel, setOtherLabel] = useState(base?.other_label ?? "");
  const [bank, setBank] = useState("");
  const [iban, setIban] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    setBusy(true); setError("");
    try {
      const pay = await call<Pay>(`/api/hr/pay/?unit=${unitId}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({
        employee_id: person.id, effective_from: from, ...amounts, other_label: otherLabel, bank_name: bank, iban, reason }) });
      onDone(`${show(person.name)}'s pay is ${qar(pay.total)} QAR a month from ${pay.effective_from}.`);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "The pay could not be saved.");
    } finally { setBusy(false); }
  };
  const sum = PARTS.reduce((total, part) => total + (Number(amounts[part]) || 0), 0);
  return <div className="sp-form an-form py-change" aria-label={`Change pay for ${show(person.name)}`}>
    <div className="sp-fields an-grid">
      <label>From month<input type="month" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
      {PARTS.map((part) => <label key={part}>{LABELS[part]}{part === "basic" ? "" : " allowance"} (QAR)<input inputMode="decimal" value={amounts[part]} onChange={(event) => setAmounts((current) => ({ ...current, [part]: event.target.value }))} /></label>)}
      {Number(amounts.other) > 0 && <label>Other allowance is for<input value={otherLabel} maxLength={60} onChange={(event) => setOtherLabel(event.target.value)} placeholder="e.g. food" /></label>}
      <label>Bank{base?.bank_name ? ` (now ${show(base.bank_name)})` : ""}<input value={bank} maxLength={80} onChange={(event) => setBank(event.target.value)} placeholder={base?.bank_name ? "leave empty to keep" : "e.g. Doha Bank"} /></label>
      <label>IBAN{base?.iban ? ` (now ${base.iban})` : ""}<input value={iban} maxLength={40} onChange={(event) => setIban(event.target.value)} placeholder={base?.iban ? "leave empty to keep" : "QA.."} /></label>
      <label className="an-wide">Reason<input value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder="e.g. yearly raise, offer letter" /></label>
    </div>
    <p className="sp-hint">New monthly total: <strong>{qar(sum)} QAR</strong>. A change starts on the 1st of the month; the old pay stays in the history.</p>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions">
      <button className="button button-dark" type="button" disabled={busy || !from || !amounts.basic || !reason.trim()} onClick={() => void save()}>Save the pay</button>
      <button className="button button-quiet" type="button" onClick={onCancel}>Cancel</button>
    </div>
  </div>;
}

function HoursForm({ hours, unitId, csrfToken, onDone, onCancel }: { hours: Hours | null; unitId: number; csrfToken: string; onDone: (message: string) => void; onCancel: () => void }) {
  const [name, setName] = useState(hours?.name ?? "");
  const [starts, setStarts] = useState(hours?.starts_at ?? "08:00");
  const [ends, setEnds] = useState(hours?.ends_at ?? "17:00");
  const [grace, setGrace] = useState(String(hours?.grace_minutes ?? 0));
  const [error, setError] = useState("");
  const save = async () => {
    setError("");
    try {
      await call(`/api/hr/working-hours/?unit=${unitId}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ id: hours?.id, name, starts_at: starts, ends_at: ends, grace_minutes: Number(grace) }) });
      onDone(`${name}: ${starts} to ${ends}${Number(grace) ? `, with ${grace} minutes' grace` : ""}.`);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "The working hours could not be saved.");
    }
  };
  return <span className="mj-inline" aria-label={hours ? `Change ${hours.name}` : "New working hours"}>
    <label>Name<input className="py-hours-name" value={name} maxLength={60} onChange={(event) => setName(event.target.value)} placeholder="e.g. Showroom evening shift" /></label>
    <label>Start<input type="time" value={starts} onChange={(event) => setStarts(event.target.value)} /></label>
    <label>Finish<input type="time" value={ends} onChange={(event) => setEnds(event.target.value)} /></label>
    <label>Grace minutes<input inputMode="numeric" value={grace} onChange={(event) => setGrace(event.target.value)} /></label>
    <button className="button button-dark" type="button" disabled={!name.trim()} onClick={() => void save()}>Save</button>
    <button className="button button-quiet" type="button" onClick={onCancel}>Cancel</button>
    {error && <div className="alert" role="alert">{error}</div>}
  </span>;
}

function WorkingHours({ overview, unitId, csrfToken, onDone }: { overview: Overview; unitId: number; csrfToken: string; onDone: (message: string) => void }) {
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [error, setError] = useState("");
  const standard = async () => {
    setError("");
    try {
      const result = await call<{ added: Hours[] }>(`/api/hr/working-hours/?unit=${unitId}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ action: "standard" }) });
      onDone(`${result.added.length} sets of Tivoli working hours added. Now choose each person's hours below.`);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "The working hours could not be added.");
    }
  };
  const done = (message: string) => { setEditing(null); onDone(message); };
  const list = overview.working_hours;
  return <div className="py-hours" aria-label="Working hours">
    <strong>Working hours</strong>
    {list.length === 0 && <span>None set yet, so minutes late or early are not counted.</span>}
    {list.length > 0 && <ul className="py-hours-list">{list.map((hours) => <li key={hours.id}>
      {editing === hours.id
        ? <HoursForm hours={hours} unitId={unitId} csrfToken={csrfToken} onDone={done} onCancel={() => setEditing(null)} />
        : <><span><strong>{show(hours.name)}</strong> {hours.starts_at} to {hours.ends_at}{hours.grace_minutes ? `, ${hours.grace_minutes} minutes' grace` : ""} · {hours.people} {hours.people === 1 ? "person" : "people"}</span>
          {overview.can_set_hours && <button className="fa-link" type="button" onClick={() => setEditing(hours.id)}>Change</button>}</>}
    </li>)}</ul>}
    {overview.no_hours > 0 && list.length > 0 && <span className="hr-expired">{overview.no_hours} {overview.no_hours === 1 ? "person has" : "people have"} no working hours yet.</span>}
    {overview.can_set_hours && editing === null && <span className="py-links">
      {list.length === 0 && <button className="fa-link" type="button" onClick={() => void standard()}>Add Tivoli's standard hours</button>}
      <button className="fa-link" type="button" onClick={() => setEditing("new")}>Add other hours</button>
    </span>}
    {editing === "new" && <HoursForm hours={null} unitId={unitId} csrfToken={csrfToken} onDone={done} onCancel={() => setEditing(null)} />}
    {error && <div className="alert" role="alert">{error}</div>}
  </div>;
}

function PersonHours({ person, overview, unitId, csrfToken, onDone }: { person: Person; overview: Overview; unitId: number; csrfToken: string; onDone: (message: string) => void }) {
  const [error, setError] = useState("");
  const current = overview.working_hours.find((hours) => hours.id === person.hours_id);
  if (!overview.can_set_hours || overview.working_hours.length === 0) {
    return current ? <small>{show(current.name)}, {current.starts_at} to {current.ends_at}</small> : null;
  }
  const choose = async (value: string) => {
    setError("");
    try {
      await call(`/api/hr/working-hours/?unit=${unitId}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ action: "assign", employee_id: person.id, hours_id: value ? Number(value) : null }) });
      const chosen = overview.working_hours.find((hours) => hours.id === Number(value));
      onDone(chosen ? `${show(person.name)} works ${show(chosen.name)} hours.` : `${show(person.name)} has no working hours now.`);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "The working hours could not be changed.");
    }
  };
  return <span className="py-person-hours">
    <select aria-label={`Working hours for ${show(person.name)}`} value={person.hours_id ?? ""} onChange={(event) => void choose(event.target.value)}>
      <option value="">No working hours</option>
      {overview.working_hours.map((hours) => <option key={hours.id} value={hours.id}>{show(hours.name)} ({hours.starts_at} to {hours.ends_at})</option>)}
    </select>
    {error && <span className="alert" role="alert">{error}</span>}
  </span>;
}

export default function PayRecords({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [changing, setChanging] = useState<number | null>(null);
  const [history, setHistory] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (selectedUnitId === null) { setError("Choose a business unit to see its salaries."); return; }
    try {
      setOverview(await call<Overview>(`/api/hr/pay/?unit=${selectedUnitId}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Salaries could not be loaded.");
    }
  }, [selectedUnitId]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); setChanging(null); void load(); };
  const allowances = overview ? Number(overview.totals.total) - Number(overview.totals.basic) : 0;

  return <section className="content-card supplier-payments bank-cash pay-page" aria-labelledby="pay-title">
    <header className="sp-head">
      <span className="eyebrow">HR · SALARIES · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="pay-title">Salaries</h2>
      <p>Each person's basic pay and allowances, and the bank account their salary goes to through WPS. Only people with payroll access see this page.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading salaries…</p>}
    {overview && selectedUnitId !== null && <>
      <ul className="bc-accounts ex-tiles" aria-label="Monthly pay">
        <li className="bc-total"><span>Monthly pay</span><strong>{qar(overview.totals.total)}</strong><small>QAR for {overview.people.length - overview.missing} people</small></li>
        <li className="bc-total"><span>Basic</span><strong>{qar(overview.totals.basic)}</strong><small>QAR a month</small></li>
        <li className="bc-total"><span>Allowances</span><strong>{qar(allowances)}</strong><small>housing {qar(overview.totals.housing)}, transport {qar(overview.totals.transport)}, mobile {qar(overview.totals.mobile)}{Number(overview.totals.other) ? `, other ${qar(overview.totals.other)}` : ""}</small></li>
        <li className="bc-total"><span>No pay set</span><strong className={overview.missing ? "hr-expired" : ""}>{overview.missing}</strong><small>{overview.missing ? "people need their pay entered" : "everyone has pay"}</small></li>
      </ul>
      <WorkingHours key={notice} overview={overview} unitId={selectedUnitId} csrfToken={csrfToken} onDone={done} />
      <div className="lv-block">
        <div className="sp-table-wrap"><table className="sp-table bc-table py-table" aria-label="Pay">
          <thead><tr><th scope="col">Who</th>{PARTS.map((part) => <th key={part} scope="col" className="bc-num">{LABELS[part]}</th>)}<th scope="col" className="bc-num">Total</th><th scope="col">Bank</th></tr></thead>
          <tbody>{overview.people.map((person) => <Fragment key={person.id}>
            <tr>
              <th scope="row">{show(person.name)}<small>{person.job_title} · {person.department}</small>
                <PersonHours person={person} overview={overview} unitId={selectedUnitId} csrfToken={csrfToken} onDone={done} />
                <span className="py-links">
                  {overview.can_manage && !person.is_me && changing !== person.id && <button className="fa-link" type="button" onClick={() => setChanging(person.id)}>{person.pay || person.upcoming ? "Change pay" : "Enter pay"}</button>}
                  {person.history.length > 1 && <button className="fa-link" type="button" onClick={() => setHistory(history === person.id ? null : person.id)}>{history === person.id ? "Hide history" : "History"}</button>}
                </span></th>
              {person.pay ? <>
                {PARTS.map((part) => <td key={part} className="pw-mono bc-num">{Number(person.pay![part]) ? qar(person.pay![part]) : ""}{part === "other" && person.pay!.other_label && <small>{show(person.pay!.other_label)}</small>}</td>)}
                <td className="pw-mono bc-num"><strong>{qar(person.pay.total)}</strong>{person.upcoming && <small>{qar(person.upcoming.total)} from {person.upcoming.effective_from}</small>}</td>
                <td>{show(person.pay.bank_name)}<small className="pw-mono">{person.pay.iban || "no IBAN"}</small></td>
              </> : <td colSpan={7} className="py-none">{person.upcoming ? `${qar(person.upcoming.total)} QAR from ${person.upcoming.effective_from}` : "No pay entered yet"}</td>}
            </tr>
            {history === person.id && <tr className="py-history"><td colSpan={8}><ul>{person.history.map((row) => <li key={row.id}>
              <strong>{row.effective_from}</strong> · {qar(row.total)} QAR (basic {qar(row.basic)}) · {show(row.reason)} · set by {row.set_by}</li>)}</ul></td></tr>}
            {changing === person.id && <tr><td colSpan={8}><ChangePay person={person} month={overview.month} unitId={selectedUnitId} csrfToken={csrfToken} onDone={done} onCancel={() => setChanging(null)} /></td></tr>}
          </Fragment>)}</tbody>
        </table></div>
      </div>
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
