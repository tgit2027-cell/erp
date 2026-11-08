import { useCallback, useEffect, useState } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Order = { id: number; label: string; customer: string; status: string };
type Row = {
  id: number; number: string; unit: string; order: Order; work_date: string; rest_day: boolean; rate_percent: number; work: string;
  status: "submitted" | "approved" | "rejected" | "cancelled"; status_label: string;
  people: { id: number; name: string; department: string; hours: string }[]; total_hours: string;
  prepared_by: string; prepared_at: string; decided_by: string; decision_note: string; can_decide: boolean; can_cancel: boolean;
};
type Overview = {
  unit: { id: number; name: string } | null; month: string; month_label: string; today: string; rows: Row[];
  by_order: (Order & { normal_hours: string; rest_hours: string; people: number })[]; orders: Order[];
  people: { id: number; name: string; department: string; job_title: string }[]; can_prepare: boolean; is_hr: boolean;
  max_hours: { working_day: string; rest_day: string }; rates: { working_day: number; rest_day: number }; policy: string;
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
const STATUS_CLASS: Record<Row["status"], string> = { submitted: "ex-waiting", approved: "ex-booked", rejected: "ex-rejected", cancelled: "ex-rejected" };
const unitQuery = (unitId: number | null) => unitId !== null ? `unit=${unitId}` : "";
const orderText = (order: Order) => `${order.label} · ${show(order.customer)}`;

function Prepare({ overview, unitId, csrfToken, onDone }: { overview: Overview; unitId: number | null; csrfToken: string; onDone: (message: string) => void }) {
  const [orderId, setOrderId] = useState(String(overview.orders[0]?.id ?? ""));
  const [day, setDay] = useState("");
  const [work, setWork] = useState("");
  const [hours, setHours] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const chosen = Object.entries(hours).filter(([, value]) => value !== "");
  const submit = async () => {
    setBusy(true); setError("");
    try {
      const row = await call<Row>(`/api/hr/overtime/${unitId !== null ? `?${unitQuery(unitId)}` : ""}`, {
        method: "POST", headers: headers(csrfToken),
        body: JSON.stringify({ sales_order_id: Number(orderId), work_date: day, work, lines: chosen.map(([id, value]) => ({ employee_id: Number(id), hours: value })) }),
      });
      setDay(""); setWork(""); setHours({});
      onDone(`${row.number}: ${row.total_hours} hours on ${row.work_date} for ${orderText(row.order)} ${row.rest_day ? "(a rest day, +50%) " : ""}are waiting for approval.`);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "The overtime could not be recorded.");
    } finally { setBusy(false); }
  };
  return <div className="sp-form an-form" aria-label="Record overtime">
    <h3>Record overtime before it is worked</h3>
    {overview.orders.length === 0 ? <p className="sp-empty">There is no confirmed sales order to charge overtime to.</p> : <>
      <div className="sp-fields an-grid">
        <label>Sales order<select value={orderId} onChange={(event) => setOrderId(event.target.value)}>
          {overview.orders.map((order) => <option key={order.id} value={order.id}>{orderText(order)} ({order.status.toLowerCase()})</option>)}
        </select></label>
        <label>Day<input type="date" value={day} min={overview.today} onChange={(event) => setDay(event.target.value)} /></label>
        <label className="an-wide">Work to be done<input value={work} maxLength={200} onChange={(event) => setWork(event.target.value)} placeholder="e.g. install the wardrobe at the client's villa" /></label>
      </div>
      <div className="sp-table-wrap"><table className="sp-table bc-table ot-people" aria-label="Who works the overtime">
        <thead><tr><th scope="col">Who</th><th scope="col">Hours</th></tr></thead>
        <tbody>{overview.people.map((person) => {
          const value = hours[person.id] ?? "";
          return <tr key={person.id} className={value ? "ot-chosen" : ""}>
            <th scope="row"><label className="ot-check"><input type="checkbox" checked={value !== ""} onChange={(event) => setHours((current) => ({ ...current, [person.id]: event.target.checked ? overview.max_hours.working_day : "" }))} />
              <span>{show(person.name)}<small>{person.job_title} · {person.department}</small></span></label></th>
            <td><input aria-label={`Hours for ${show(person.name)}`} inputMode="decimal" value={value} disabled={value === ""} onChange={(event) => setHours((current) => ({ ...current, [person.id]: event.target.value }))} /></td>
          </tr>;
        })}</tbody>
      </table></div>
      <p className="sp-hint">Paid at +{overview.rates.working_day - 100}% on a working day (at most {overview.max_hours.working_day} hours) or +{overview.rates.rest_day - 100}% on a rest day (at most {overview.max_hours.rest_day} hours). Someone else approves it. The cost goes to the sales order in payroll.</p>
      {error && <div className="alert" role="alert">{error}</div>}
      <div className="sp-actions"><button className="button button-dark" type="button" disabled={busy || !orderId || !day || !work.trim() || chosen.length === 0} onClick={() => void submit()}>Send for approval</button></div>
    </>}
  </div>;
}

function Requests({ rows, csrfToken, onDone, title }: { rows: Row[]; csrfToken: string; onDone: (message: string) => void; title: string }) {
  const [rejecting, setRejecting] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (row: Row, action: string, message: string) => {
    setBusy(true); setError("");
    try {
      await call(`/api/hr/overtime/${row.id}/`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ action, note }) });
      setRejecting(null); setNote("");
      onDone(message);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "That could not be done.");
    } finally { setBusy(false); }
  };
  return <div className="lv-block">
    <h3>{title}</h3>
    <div className="sp-table-wrap"><table className="sp-table bc-table lv-table" aria-label={title}>
      <thead><tr><th scope="col">Day</th><th scope="col">Sales order</th><th scope="col">Who</th><th scope="col" className="bc-num">Hours</th><th scope="col">Status</th></tr></thead>
      <tbody>{rows.map((row) => <tr key={row.id}>
        <th scope="row">{row.work_date}<small>{row.number}{row.rest_day ? " · rest day" : ""}</small></th>
        <td>{orderText(row.order)}<small>{show(row.work)}</small></td>
        <td>{row.people.map((person) => <span key={person.id} className="ot-person">{show(person.name)} <span className="pw-mono">{person.hours}h</span></span>)}</td>
        <td className="pw-mono bc-num">{row.total_hours}<small>+{row.rate_percent - 100}%</small></td>
        <td><span className={`ex-status ${STATUS_CLASS[row.status]}`}>{row.status_label}</span>
          <small>by {row.prepared_by}{row.decided_by ? `; ${row.status_label.toLowerCase()} by ${row.decided_by}` : ""}{row.decision_note ? `: ${show(row.decision_note)}` : ""}</small>
          {(row.can_decide || row.can_cancel) && <span className="lv-actions">
            {row.can_decide && <button className="button button-dark" type="button" disabled={busy} onClick={() => void run(row, "approve", `${row.number} is approved.`)}>Approve</button>}
            {row.can_decide && <button className="button button-outline" type="button" disabled={busy} onClick={() => { setRejecting(rejecting === row.id ? null : row.id); setNote(""); }}>Reject</button>}
            {row.can_cancel && <button className="button button-quiet" type="button" disabled={busy} onClick={() => void run(row, "cancel", `${row.number} is cancelled.`)}>Cancel</button>}
          </span>}
          {rejecting === row.id && <span className="mj-inline">
            <input aria-label="Why it is rejected" value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} placeholder="Why it is rejected" />
            <button className="button button-dark" type="button" disabled={busy || !note.trim()} onClick={() => void run(row, "reject", `${row.number} is rejected.`)}>Reject the overtime</button>
          </span>}
        </td>
      </tr>)}</tbody>
    </table></div>
    {error && <div className="alert" role="alert">{error}</div>}
  </div>;
}

export default function Overtime({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [wanted, setWanted] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    const query = [unitQuery(selectedUnitId), wanted ? `month=${wanted}` : ""].filter(Boolean).join("&");
    try {
      setOverview(await call<Overview>(`/api/hr/overtime/${query ? `?${query}` : ""}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Overtime could not be loaded.");
    }
  }, [selectedUnitId, wanted]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); void load(); };
  const rows = overview?.rows ?? [];
  const waiting = rows.filter((row) => row.status === "submitted");
  const approved = rows.filter((row) => row.status === "approved" && row.work_date.startsWith(overview?.month ?? ""));
  const sum = (list: Row[]) => list.reduce((total, row) => total + Number(row.total_hours), 0);

  return <section className="content-card supplier-payments bank-cash overtime-page" aria-labelledby="overtime-title">
    <header className="sp-head">
      <span className="eyebrow">HR · OVERTIME · TEST ONLY{overview?.unit ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="overtime-title">Overtime</h2>
      <p>The unit's manager records overtime before it is worked: the sales order, the hours and the people. Someone else approves it, and its cost goes to that sales order.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading overtime…</p>}
    {overview && <>
      <div className="sp-fields an-grid at-month">
        <label>Month<input type="month" value={wanted || overview.month} onChange={(event) => { setNotice(""); setWanted(event.target.value); }} /></label>
      </div>
      <ul className="bc-accounts ex-tiles" aria-label={`Overtime totals for ${overview.month_label}`}>
        <li className="bc-total"><span>Waiting for approval</span><strong>{waiting.length}</strong><small>{sum(waiting)} hours</small></li>
        <li className="bc-total"><span>Approved hours</span><strong>{sum(approved)}</strong><small>in {overview.month_label}</small></li>
        <li className="bc-total"><span>On rest days</span><strong>{sum(approved.filter((row) => row.rest_day))}</strong><small>approved hours at +{overview.rates.rest_day - 100}%</small></li>
        <li className="bc-total"><span>Sales orders</span><strong>{overview.by_order.length}</strong><small>with approved overtime</small></li>
      </ul>
      {overview.can_prepare && <Prepare key={notice} overview={overview} unitId={selectedUnitId} csrfToken={csrfToken} onDone={done} />}
      {rows.length > 0 ? <Requests title={`Overtime in ${overview.month_label}`} rows={rows} csrfToken={csrfToken} onDone={done} />
        : <p className="sp-empty">No overtime in {overview.month_label}.</p>}
      {overview.by_order.length > 0 && <div className="lv-block">
        <h3>Approved overtime by sales order</h3>
        <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label="By sales order">
          <thead><tr><th scope="col">Sales order</th><th scope="col">Client</th><th scope="col" className="bc-num">Working-day hours</th><th scope="col" className="bc-num">Rest-day hours</th><th scope="col" className="bc-num">People</th></tr></thead>
          <tbody>{overview.by_order.map((order) => <tr key={order.id}>
            <th scope="row">{order.label}</th><td>{show(order.customer)}</td>
            <td className="pw-mono bc-num">{order.normal_hours === "0" ? "" : order.normal_hours}</td><td className="pw-mono bc-num">{order.rest_hours === "0" ? "" : order.rest_hours}</td>
            <td className="pw-mono bc-num">{order.people}</td>
          </tr>)}</tbody>
        </table></div>
        <p className="sp-hint">The cost is worked out in payroll from each person's pay and charged to these sales orders.</p>
      </div>}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
