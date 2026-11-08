import { useCallback, useEffect, useMemo, useState } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Cell = { code: string; kind: string; status?: string; status_label?: string; check_in?: string; check_out?: string; note?: string; recorded_by?: string; excused?: boolean; minutes_late?: number; minutes_early?: number };
type Totals = { present: number; late: number; absent: number; leave: number; missing: number };
type Person = { id: number; name: string; staff_number: string; department: string; job_title: string; hours?: string; cells: Record<string, Cell>; totals: Totals; can_record: boolean };
type Month = {
  unit: { id: number; name: string } | null; month: string; month_label: string; today: string;
  days: { date: string; weekday: string; day: number }[]; people: Person[]; me_id: number | null;
  confirmation: { status: string; confirmed_by: string; confirmed_at: string; reopened_by: string; reopen_reason: string } | null;
  locked: boolean; can_confirm: boolean; can_reopen: boolean; is_hr: boolean; manager_days_back: number;
  statuses: { value: string; label: string }[]; policy: string;
};
type Entry = { status: string; check_in: string; check_out: string; note: string; excused: boolean };

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
const LEAVE_LABEL: Record<string, string> = { AL: "On annual leave", SL: "On sick leave", UL: "On unpaid leave" };
const recordable = (cell: Cell | undefined) => !!cell && ["missing", "present", "late", "absent"].includes(cell.kind);
const unitQuery = (unitId: number | null) => unitId !== null ? `unit=${unitId}` : "";

function defaultDay(month: Month) {
  const last = month.days[month.days.length - 1].date;
  return month.today < last ? month.today : last;
}

function RecordDay({ month, unitId, csrfToken, onDone }: { month: Month; unitId: number | null; csrfToken: string; onDone: (message: string) => void }) {
  const [day, setDay] = useState(defaultDay(month));
  const people = month.people.filter((person) => person.can_record);
  const start = (person: Person): Entry => {
    const cell = person.cells[day];
    return { status: cell?.status ?? "present", check_in: cell?.check_in ?? "", check_out: cell?.check_out ?? "", note: cell?.note ?? "", excused: cell?.excused ?? false };
  };
  const [entries, setEntries] = useState<Record<number, Entry>>(() => Object.fromEntries(people.map((person) => [person.id, start(person)])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setEntries(Object.fromEntries(people.map((person) => [person.id, start(person)]))); setError(""); }, [day, month]); // eslint-disable-line react-hooks/exhaustive-deps
  const open = people.filter((person) => recordable(person.cells[day]));
  const closed = !month.people.some((person) => person.cells[day] && person.cells[day].kind !== "closed" && person.cells[day].kind !== "not_employed");
  const set = (id: number, change: Partial<Entry>) => setEntries((current) => ({ ...current, [id]: { ...current[id], ...change, ...(change.status === "absent" ? { check_in: "", check_out: "", excused: false } : {}) } }));
  const save = async () => {
    setBusy(true); setError("");
    try {
      const result = await call<{ saved: number; changed: number }>(`/api/hr/attendance/${unitId !== null ? `?${unitQuery(unitId)}` : ""}`, {
        method: "POST", headers: headers(csrfToken),
        body: JSON.stringify({ day, entries: open.map((person) => ({ employee_id: person.id, ...entries[person.id] })) }),
      });
      onDone(result.saved === 0 ? `Nothing changed for ${day}.` : `Attendance for ${day} is saved for ${result.saved} ${result.saved === 1 ? "person" : "people"}${result.changed ? `, ${result.changed} of them corrected` : ""}.`);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "The attendance could not be saved.");
    } finally { setBusy(false); }
  };
  return <div className="sp-form an-form at-record" aria-label="Record a day">
    <h3>Record a day</h3>
    <div className="sp-fields an-grid">
      <label>Day<input type="date" value={day} min={month.days[0].date} max={defaultDay(month)} onChange={(event) => event.target.value && setDay(event.target.value)} /></label>
    </div>
    {closed && <p className="sp-empty">{day} is a day off.</p>}
    {!closed && <div className="sp-table-wrap"><table className="sp-table bc-table at-entry" aria-label={`Attendance on ${day}`}>
      <thead><tr><th scope="col">Who</th><th scope="col">Attendance</th><th scope="col">Arrived</th><th scope="col">Left</th><th scope="col" title="A late arrival or early leaving that was approved does not count">Approved</th><th scope="col">Note</th></tr></thead>
      <tbody>{people.map((person) => {
        const cell = person.cells[day];
        const entry = entries[person.id];
        if (!recordable(cell) || !entry) return <tr key={person.id} className="at-muted">
          <th scope="row">{show(person.name)}<small>{person.department}</small></th>
          <td colSpan={5}>{cell?.kind === "leave" ? LEAVE_LABEL[cell.code] : cell?.kind === "closed" ? "Day off" : "Not working here that day"}</td>
        </tr>;
        return <tr key={person.id}>
          <th scope="row">{show(person.name)}<small>{person.department}{person.hours ? ` · ${show(person.hours)}` : ""}{cell.kind !== "missing" ? ` · recorded by ${cell.recorded_by}` : ""}</small></th>
          <td><select aria-label={`Attendance for ${show(person.name)}`} value={entry.status} onChange={(event) => set(person.id, { status: event.target.value })}>
            {month.statuses.map((status) => <option key={status.value} value={status.value}>{status.label}</option>)}
          </select></td>
          <td><input type="time" aria-label={`Arrived ${show(person.name)}`} value={entry.check_in} disabled={entry.status === "absent"} required={entry.status === "late"} onChange={(event) => set(person.id, { check_in: event.target.value })} /></td>
          <td><input type="time" aria-label={`Left ${show(person.name)}`} value={entry.check_out} disabled={entry.status === "absent"} onChange={(event) => set(person.id, { check_out: event.target.value })} /></td>
          <td><input type="checkbox" aria-label={`Late or early approved for ${show(person.name)}`} checked={entry.excused} disabled={entry.status === "absent"} onChange={(event) => set(person.id, { excused: event.target.checked })} /></td>
          <td><input aria-label={`Note for ${show(person.name)}`} value={entry.note} maxLength={200} onChange={(event) => set(person.id, { note: event.target.value })} placeholder="optional" /></td>
        </tr>;
      })}</tbody>
    </table></div>}
    <p className="sp-hint">Everyone not yet recorded starts as present: change only who was late or absent. {month.is_hr ? "" : `Managers can record up to ${month.manager_days_back} days back.`}</p>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions"><button className="button button-dark" type="button" disabled={busy || closed || open.length === 0} onClick={() => void save()}>Save the day</button></div>
  </div>;
}

function Confirm({ month, unitId, csrfToken, onDone }: { month: Month; unitId: number | null; csrfToken: string; onDone: (message: string) => void }) {
  const [reopening, setReopening] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (action: string) => {
    setBusy(true); setError("");
    try {
      await call(`/api/hr/attendance/confirm/?${unitQuery(unitId)}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ month: month.month, action, reason }) });
      onDone(action === "confirm" ? `${month.month_label} is confirmed. It can no longer change unless HR reopens it.` : `${month.month_label} is reopened.`);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "That could not be done.");
    } finally { setBusy(false); }
  };
  if (!month.can_confirm && !month.can_reopen) return null;
  return <div className="at-confirm">
    {month.can_confirm && <button className="button button-dark" type="button" disabled={busy} onClick={() => void run("confirm")}>Confirm {month.month_label}</button>}
    {month.can_reopen && !reopening && <button className="button button-outline" type="button" onClick={() => setReopening(true)}>Reopen {month.month_label}</button>}
    {reopening && <span className="mj-inline">
      <input aria-label="Why it is reopened" value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder="Why it is reopened" />
      <button className="button button-dark" type="button" disabled={busy || !reason.trim()} onClick={() => void run("reopen")}>Reopen the month</button>
      <button className="button button-quiet" type="button" onClick={() => setReopening(false)}>Cancel</button>
    </span>}
    {error && <div className="alert" role="alert">{error}</div>}
  </div>;
}

export default function Attendance({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [month, setMonth] = useState<Month | null>(null);
  const [wanted, setWanted] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    const query = [unitQuery(selectedUnitId), wanted ? `month=${wanted}` : ""].filter(Boolean).join("&");
    try {
      setMonth(await call<Month>(`/api/hr/attendance/${query ? `?${query}` : ""}`));
      setError("");
    } catch (caught) {
      setMonth(null);
      setError(caught instanceof Error ? caught.message : "Attendance could not be loaded.");
    }
  }, [selectedUnitId, wanted]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); void load(); };
  const totals = useMemo(() => (month?.people ?? []).reduce((sum, person) => ({
    present: sum.present + person.totals.present, late: sum.late + person.totals.late, absent: sum.absent + person.totals.absent,
    leave: sum.leave + person.totals.leave, missing: sum.missing + person.totals.missing,
  }), { present: 0, late: 0, absent: 0, leave: 0, missing: 0 }), [month]);
  const canRecord = !!month && !month.locked && month.days[0].date <= month.today && month.people.some((person) => person.can_record);

  return <section className="content-card supplier-payments bank-cash attendance-page" aria-labelledby="attendance-title">
    <header className="sp-head">
      <span className="eyebrow">HR · ATTENDANCE · TEST ONLY{month?.unit ? ` · ${month.unit.name}` : ""}</span>
      <h2 id="attendance-title">Attendance</h2>
      <p>Who was in, late or absent each working day. Managers record their team; HR records anyone and confirms each month once it ends. Approved leave shows on its own.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!month && !error && <p className="sp-empty" role="status">Loading attendance…</p>}
    {month && <>
      <div className="sp-fields an-grid at-month">
        <label>Month<input type="month" value={wanted || month.month} onChange={(event) => { setNotice(""); setWanted(event.target.value); }} /></label>
      </div>
      <ul className="bc-accounts ex-tiles" aria-label={`Attendance in ${month.month_label}`}>
        <li className="bc-total"><span>Days present</span><strong>{totals.present + totals.late}</strong><small>across {month.people.length} {month.people.length === 1 ? "person" : "people"}</small></li>
        <li className="bc-total"><span>Late</span><strong>{totals.late}</strong><small>days arrived late</small></li>
        <li className="bc-total"><span>Absent</span><strong>{totals.absent}</strong><small>days without leave</small></li>
        <li className="bc-total"><span>Not recorded yet</span><strong className={totals.missing ? "hr-expired" : ""}>{totals.missing}</strong><small>{totals.leave} days of leave this month</small></li>
      </ul>
      {month.confirmation && <p className={`sp-hint at-state${month.locked ? " at-locked" : ""}`}>
        {month.locked ? `Confirmed by ${month.confirmation.confirmed_by} on ${month.confirmation.confirmed_at.slice(0, 10)}. It can no longer change.` : `Reopened by ${month.confirmation.reopened_by}: ${show(month.confirmation.reopen_reason)}`}
      </p>}
      <Confirm key={`confirm-${month.month}`} month={month} unitId={selectedUnitId} csrfToken={csrfToken} onDone={done} />
      {canRecord && <RecordDay key={`record-${month.month}`} month={month} unitId={selectedUnitId} csrfToken={csrfToken} onDone={done} />}
      <div className="lv-block">
        <h3>{month.month_label}</h3>
        <div className="sp-table-wrap at-grid-wrap"><table className="sp-table at-grid" aria-label={`Attendance grid for ${month.month_label}`}>
          <thead><tr><th scope="col" className="at-who">Who</th>
            {month.days.map((day) => <th scope="col" key={day.date} className={day.date === month.today ? "at-today" : ""}><span>{day.weekday.slice(0, 2)}</span>{day.day}</th>)}
            <th scope="col" className="at-total" title="Present, including late">In</th><th scope="col" className="at-total">Late</th><th scope="col" className="at-total">Absent</th><th scope="col" className="at-total">Leave</th><th scope="col" className="at-total" title="Working days not recorded yet">Missing</th></tr></thead>
          <tbody>{month.people.map((person) => <tr key={person.id}>
            <th scope="row" className="at-who">{show(person.name)}<small>{person.department}</small></th>
            {month.days.map((day) => {
              const cell = person.cells[day.date];
              const timing = [cell.minutes_late ? `${cell.minutes_late} min late` : "", cell.minutes_early ? `left ${cell.minutes_early} min early` : ""].filter(Boolean).join(", ");
              const title = cell.kind === "leave" ? LEAVE_LABEL[cell.code] : cell.status_label ? `${cell.status_label}${cell.check_in ? ` ${cell.check_in}` : ""}${cell.check_out ? ` to ${cell.check_out}` : ""}${timing ? ` (${timing}${cell.excused ? ", approved" : ""})` : ""}${cell.note ? `: ${show(cell.note)}` : ""}` : cell.kind === "missing" ? "Not recorded yet" : cell.kind === "closed" ? "Day off" : "";
              return <td key={day.date} className={`at-cell at-${cell.kind}`} title={title}>{cell.code}</td>;
            })}
            <td className="pw-mono at-total">{person.totals.present + person.totals.late}</td>
            <td className="pw-mono at-total">{person.totals.late || ""}</td>
            <td className="pw-mono at-total">{person.totals.absent || ""}</td>
            <td className="pw-mono at-total">{person.totals.leave || ""}</td>
            <td className={`pw-mono at-total${person.totals.missing ? " hr-expired" : ""}`}>{person.totals.missing || ""}</td>
          </tr>)}</tbody>
        </table></div>
        <p className="sp-hint at-legend">P present · L late · A absent · AL annual leave · SL sick leave · UL unpaid leave · ? not recorded yet · shaded days are days off.</p>
      </div>
      <p className="sp-hint">{month.policy}</p>
    </>}
  </section>;
}
