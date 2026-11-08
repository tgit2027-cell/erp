import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Balance = {
  year: number; eligible_from: string; leave_year: { start: string; end: string } | null; entitlement: string; carried: number;
  adjusted: string; taken: number; waiting: number; left: string; parts_used: number; parts_allowed: number;
  sick: { used: number; bands: { label: string; days: number; used: number }[]; beyond: number; qualifies: boolean }; unpaid: number;
};
type Request = {
  id: number; employee: { id: number; name: string; staff_number: string; department: string }; kind: string; kind_label: string;
  start: string; end: string; days: number; reason: string; status: "requested" | "approved" | "rejected" | "cancelled";
  status_label: string; requested_by: string; requested_at: string; decided_by: string; decision_note: string;
  special_approval: null | { why: string; manager: string; waiting_for: "" | "line manager" | "HR" };
  can_decide: boolean; can_cancel: boolean;
};
type Person = { id: number; name: string; staff_number: string; job_title: string; department: string; joined_on: string; balance: Balance };
type Overview = {
  unit: { id: number; name: string } | null; year: number; today: string; me: (Person & { requests: Request[] }) | null;
  to_decide: Request[]; away: Request[]; people: Person[]; can_request_for: { id: number; name: string }[];
  can_adjust: boolean; kinds: { value: string; label: string }[]; policy: string;
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
const STATUS_CLASS: Record<Request["status"], string> = { requested: "ex-waiting", approved: "ex-booked", rejected: "ex-rejected", cancelled: "ex-rejected" };
const num = (value: string) => String(Number(value));
const span = (request: Request) => request.start === request.end ? request.start : `${request.start} to ${request.end}`;
const yearText = (balance: Balance) => {
  if (!balance.leave_year) return `Annual leave starts on ${balance.eligible_from}, after 1 year of service`;
  const extra = [balance.carried ? `${balance.carried} carried over` : "", Number(balance.adjusted) ? `${Number(balance.adjusted) > 0 ? "+" : "−"}${Math.abs(Number(balance.adjusted))} from HR` : ""].filter(Boolean);
  return `of ${num(balance.entitlement)}${extra.length ? ` + ${extra.join(", ")}` : ""}, until ${balance.leave_year.end}`;
};
const sickText = (balance: Balance) => {
  const parts = balance.sick.bands.filter((band) => band.used > 0).map((band) => `${band.used} on ${band.label}`);
  return parts.length ? parts.join(", ") : "none this year";
};

function Ask({ overview, csrfToken, onDone }: { overview: Overview; csrfToken: string; onDone: (message: string) => void }) {
  const [employeeId, setEmployeeId] = useState(overview.me ? "" : String(overview.can_request_for[0]?.id ?? ""));
  const [kind, setKind] = useState("annual");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [reason, setReason] = useState("");
  const [special, setSpecial] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const request = await call<Request>("/api/hr/leave/", { method: "POST", headers: headers(csrfToken),
        body: JSON.stringify({ kind, start, end, reason, ...(special ? { special_approval: true } : {}), ...(employeeId ? { employee_id: Number(employeeId) } : {}) }) });
      setStart(""); setEnd(""); setReason(""); setSpecial(false);
      onDone(`${request.days} ${request.kind === "annual" ? "" : "working "}day${request.days === 1 ? "" : "s"} of ${request.kind_label.toLowerCase()} for ${show(request.employee.name)} ${request.status === "requested" ? "are waiting for approval" : "are recorded"}.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The leave could not be asked for.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form an-form" onSubmit={(event) => void submit(event)} aria-label="Ask for leave">
    <h3>Ask for leave</h3>
    <div className="sp-fields an-grid">
      {overview.can_request_for.length > 0 && <label>For<select value={employeeId} onChange={(event) => setEmployeeId(event.target.value)}>
        {overview.me && <option value="">Myself</option>}
        {overview.can_request_for.filter((person) => person.id !== overview.me?.id).map((person) => <option key={person.id} value={person.id}>{show(person.name)}</option>)}
      </select></label>}
      <label>Kind of leave<select value={kind} onChange={(event) => setKind(event.target.value)}>
        {overview.kinds.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
      </select></label>
      <label>First day<input type="date" value={start} onChange={(event) => { setStart(event.target.value); if (!end || end < event.target.value) setEnd(event.target.value); }} required /></label>
      <label>Last day<input type="date" value={end} min={start || undefined} onChange={(event) => setEnd(event.target.value)} required /></label>
      <label className="an-wide">Reason (optional)<input value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder="e.g. family visit" /></label>
    </div>
    <p className="sp-hint">Annual leave counts calendar days and can be taken in at most 2 parts a year. Sick and unpaid leave count working days only. Nobody can be away more than 30 days in a row.</p>
    {error && <div className="alert" role="alert">{error}</div>}
    {(special || error.includes("special approval")) && <label className="lv-special"><input type="checkbox" checked={special} onChange={(event) => setSpecial(event.target.checked)} /> Ask for a special approval: the line manager approves first, then HR. Give the reason above.</label>}
    <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy || !start || !end || (special && !reason.trim())}>Send for approval</button></div>
  </form>;
}

function Decisions({ rows, title, csrfToken, onDone }: { rows: Request[]; title: string; csrfToken: string; onDone: (message: string) => void }) {
  const [rejecting, setRejecting] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (request: Request, action: string, message: string) => {
    setBusy(true); setError("");
    try {
      await call(`/api/hr/leave/${request.id}/`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ action, note }) });
      setRejecting(null); setNote("");
      onDone(message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That could not be done.");
    } finally { setBusy(false); }
  };
  return <div className="lv-block">
    <h3>{title}</h3>
    <div className="sp-table-wrap"><table className="sp-table bc-table lv-table" aria-label={title}>
      <thead><tr><th scope="col">Who</th><th scope="col">Leave</th><th scope="col">Dates</th><th scope="col" className="bc-num">Days</th><th scope="col">Status</th></tr></thead>
      <tbody>{rows.map((request) => <tr key={request.id}>
        <th scope="row">{show(request.employee.name)}<small>{request.employee.department}</small></th>
        <td>{request.kind_label}{request.reason && <small>{show(request.reason)}</small>}</td>
        <td>{span(request)}</td>
        <td className="pw-mono bc-num">{request.days}</td>
        <td><span className={`ex-status ${STATUS_CLASS[request.status]}`}>{request.status_label}</span>
          {request.decided_by && <small>by {request.decided_by}{request.decision_note ? `: ${show(request.decision_note)}` : ""}</small>}
          {request.special_approval && <small className="lv-special-note">Special approval: {show(request.special_approval.why)}{request.special_approval.manager ? ` Line manager approved (${request.special_approval.manager}).` : ""}{request.special_approval.waiting_for ? ` Waiting for ${request.special_approval.waiting_for}.` : ""}</small>}
          {(request.can_decide || request.can_cancel) && <span className="lv-actions">
            {request.can_decide && <button className="button button-dark" type="button" disabled={busy} onClick={() => void run(request, "approve", request.special_approval?.waiting_for === "line manager" ? `You approved ${show(request.employee.name)}'s ${request.kind_label.toLowerCase()}. It now goes to HR.` : `${show(request.employee.name)}'s ${request.kind_label.toLowerCase()} is approved.`)}>Approve</button>}
            {request.can_decide && <button className="button button-outline" type="button" disabled={busy} onClick={() => { setRejecting(rejecting === request.id ? null : request.id); setNote(""); }}>Reject</button>}
            {request.can_cancel && !request.can_decide && <button className="button button-quiet" type="button" disabled={busy} onClick={() => void run(request, "cancel", `The ${request.kind_label.toLowerCase()} from ${request.start} is cancelled.`)}>Cancel</button>}
          </span>}
          {rejecting === request.id && <span className="mj-inline">
            <input aria-label="Why it is rejected" value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} placeholder="Why it is rejected" />
            <button className="button button-dark" type="button" disabled={busy || !note.trim()} onClick={() => void run(request, "reject", `${show(request.employee.name)}'s ${request.kind_label.toLowerCase()} is rejected.`)}>Reject the leave</button>
          </span>}
        </td>
      </tr>)}</tbody>
    </table></div>
    {error && <div className="alert" role="alert">{error}</div>}
  </div>;
}

function Adjust({ person, csrfToken, onDone, onCancel }: { person: Person; csrfToken: string; onDone: (message: string) => void; onCancel: () => void }) {
  const [days, setDays] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true); setError("");
    try {
      await call("/api/hr/leave/adjust/", { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ employee_id: person.id, days, reason }) });
      onDone(`${show(person.name)}'s annual leave for this leave year is changed by ${Number(days) > 0 ? "+" : ""}${days} days.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The balance could not be changed.");
    } finally { setBusy(false); }
  };
  return <div className="mj-inline lv-adjust">
    <input aria-label={`Days to add for ${show(person.name)}`} inputMode="decimal" value={days} onChange={(event) => setDays(event.target.value)} placeholder="Days, e.g. 5 or -2" />
    <input aria-label="Why" value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder="Why, e.g. carried over from last year" />
    <button className="button button-dark" type="button" disabled={busy || !days || !reason.trim()} onClick={() => void save()}>Save</button>
    <button className="button button-quiet" type="button" onClick={onCancel}>Cancel</button>
    {error && <span className="alert" role="alert">{error}</span>}
  </div>;
}

export default function Leave({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [adjusting, setAdjusting] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      setOverview(await call<Overview>(`/api/hr/leave/${selectedUnitId !== null ? `?unit=${selectedUnitId}` : ""}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Leave could not be loaded.");
    }
  }, [selectedUnitId]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); setAdjusting(null); void load(); };
  const me = overview?.me ?? null;

  return <section className="content-card supplier-payments bank-cash leave-page" aria-labelledby="leave-title">
    <header className="sp-head">
      <span className="eyebrow">HR · LEAVE · TEST ONLY{overview?.unit ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="leave-title">Leave</h2>
      <p>Ask for annual, sick or unpaid leave and see what is left. The person's manager, or HR, approves it. Managers and HR also see who is away and everyone's balance.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading leave…</p>}
    {overview && <>
      {me && <ul className="bc-accounts ex-tiles" aria-label="My leave">
        <li className="bc-total"><span>Annual leave left</span><strong>{num(me.balance.left)}</strong><small>{yearText(me.balance)}</small></li>
        <li className="bc-total"><span>Taken</span><strong>{me.balance.taken}</strong><small>{me.balance.waiting ? `${me.balance.waiting} more waiting for approval` : "annual leave days this leave year"}</small></li>
        <li className="bc-total"><span>Parts used</span><strong>{me.balance.parts_used} of {me.balance.parts_allowed}</strong><small>annual leave can be split in two</small></li>
        <li className="bc-total"><span>Sick leave</span><strong>{me.balance.sick.used}</strong><small>{sickText(me.balance)}</small></li>
      </ul>}
      {(me || overview.can_request_for.length > 0) && <Ask key={notice} overview={overview} csrfToken={csrfToken} onDone={done} />}
      {overview.to_decide.length > 0 && <Decisions title="Waiting for you" rows={overview.to_decide} csrfToken={csrfToken} onDone={done} />}
      {overview.away.some((row) => !row.can_decide) && <Decisions title="Away in the next 30 days" rows={overview.away.filter((row) => !row.can_decide)} csrfToken={csrfToken} onDone={done} />}
      {overview.people.length > 0 && <div className="lv-block">
        <h3>Annual leave balances</h3>
        <div className="sp-table-wrap"><table className="sp-table bc-table lv-table" aria-label="Balances">
          <thead><tr><th scope="col">Who</th><th scope="col">Leave year</th><th scope="col" className="bc-num">Annual</th><th scope="col" className="bc-num">Carried over</th><th scope="col" className="bc-num">Changed by HR</th>
            <th scope="col" className="bc-num">Taken</th><th scope="col" className="bc-num">Waiting</th><th scope="col" className="bc-num">Left</th><th scope="col" className="bc-num">Parts</th><th scope="col" className="bc-num">Sick days</th></tr></thead>
          <tbody>{overview.people.map((person) => <tr key={person.id}>
            <th scope="row">{show(person.name)}<small>{person.job_title} · {person.department}</small>
              {overview.can_adjust && person.balance.leave_year && adjusting !== person.id && <button className="fa-link lv-adjust-link" type="button" onClick={() => setAdjusting(person.id)}>Change balance</button>}
              {adjusting === person.id && <Adjust person={person} csrfToken={csrfToken} onDone={done} onCancel={() => setAdjusting(null)} />}</th>
            <td>{person.balance.leave_year ? `${person.balance.leave_year.start} to ${person.balance.leave_year.end}` : <small>from {person.balance.eligible_from}</small>}</td>
            <td className="pw-mono bc-num">{num(person.balance.entitlement)}</td>
            <td className="pw-mono bc-num">{person.balance.carried || ""}</td>
            <td className="pw-mono bc-num">{Number(person.balance.adjusted) ? num(person.balance.adjusted) : ""}</td>
            <td className="pw-mono bc-num">{person.balance.taken}</td>
            <td className="pw-mono bc-num">{person.balance.waiting || ""}</td>
            <td className={`pw-mono bc-num${Number(person.balance.left) < 0 ? " hr-expired" : ""}`}><strong>{num(person.balance.left)}</strong></td>
            <td className="pw-mono bc-num">{person.balance.leave_year ? `${person.balance.parts_used}/${person.balance.parts_allowed}` : ""}</td>
            <td className="pw-mono bc-num">{person.balance.sick.used || ""}{!person.balance.sick.qualifies && <small>under 3 months</small>}</td>
          </tr>)}</tbody>
        </table></div>
      </div>}
      {me && me.requests.length > 0 && <Decisions title="My requests" rows={me.requests} csrfToken={csrfToken} onDone={done} />}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
