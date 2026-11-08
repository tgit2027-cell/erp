import { Fragment, useCallback, useEffect, useState } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Overtime = { order_id: number; label: string; customer: string; hours: string; amount: string };
type Late = { day: string; minutes: number; outcome: string; amount: string };
type Detail = {
  pay: Record<string, string>; day_rate: string; hour_basic: string; hour_total: string; overtime: Overtime[];
  absent_days: string[]; absences: string; unpaid_days: number; unpaid: string; lateness: Late[]; lateness_total: string;
  warning_letters: number; end_of_service: Settled | null; capped: boolean; notes: string[];
  loans?: { loan: number; label: string; due: string; amount: string; balance_after: string }[];
  commission?: { instalment: number; year: number; kind: "commission" | "reward"; month: string; amount: string }[];
  bank_name: string; iban: string;
};
type Settled = {
  years: string; amount: string; settlement?: number; gratuity?: string; already_paid?: string; leave_days?: string; leave_pay?: string;
  additions?: string; deductions?: string;
};
type Line = {
  id: number; employee: { id: number; name: string; department: string; job_title: string }; days_paid: number;
  basic: string; allowances: string; overtime: string; overtime_hours: string; end_of_service: string; deductions: string; net: string;
  detail: Detail;
};
type Run = {
  id: number; month: string; month_label: string; period_start: string; period_end: string; pay_date: string;
  status: "prepared" | "posted"; status_label: string; totals: Record<string, string | number>; warnings: string[];
  prepared_by: string; prepared_at: string; approved_by: string; approved_at: string; journal_entry: number | null;
  lines: Line[]; can_recalculate: boolean; can_approve: boolean; can_download: boolean;
};
type Wps = { employer_eid: string; payer_bank: string; payer_iban: string; set_by: string };
type Overview = {
  unit: { id: number; name: string }; today: string; month: string; month_label: string;
  period: { start: string; cut_off: string; pay_date: string }; run: Run | null; can_prepare: boolean; cut_off_passed: boolean;
  recent: { month: string; status_label: string; net: string }[]; wps: Wps | null; wps_banks: { code: string; name: string }[];
  can_manage: boolean; policy: string;
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
const qar = (value: string | number) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const day = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const money = (value: string) => (Number(value) ? qar(value) : "");

function Breakdown({ line }: { line: Line }) {
  const { detail } = line;
  const pay = detail.pay;
  const parts = ["housing", "transport", "mobile", "other"].filter((part) => Number(pay[part]));
  return <div className="pr-detail" aria-label={`How ${show(line.employee.name)}'s pay was worked out`}>
    <ul>
      <li><strong>Monthly pay:</strong> basic {qar(pay.basic)}{parts.map((part) => `, ${part === "other" && pay.other_label ? show(pay.other_label) : part} ${qar(pay[part])}`).join("")} = {qar(pay.total)} QAR{line.days_paid < 30 ? `; paid for ${line.days_paid} of 30 days` : ""}.</li>
      {detail.overtime.map((entry) => <li key={entry.order_id}><strong>Overtime:</strong> {entry.hours} hours for {entry.label} ({show(entry.customer)}) = {qar(entry.amount)} QAR, at basic ÷ 30 ÷ 8 = {qar(detail.hour_basic)} an hour.</li>)}
      {detail.absent_days.length > 0 && <li><strong>Absent:</strong> {detail.absent_days.map(day).join(", ")}: {detail.absent_days.length} × {qar(detail.day_rate)} = {qar(detail.absences)} QAR.</li>}
      {detail.unpaid_days > 0 && <li><strong>Unpaid leave:</strong> {detail.unpaid_days} working days × {qar(detail.day_rate)} = {qar(detail.unpaid)} QAR.</li>}
      {detail.lateness.length > 0 && <li><strong>Late or left early without approval:</strong> {detail.lateness.map((item, index) => `${index + 1}. ${day(item.day)} ${item.minutes} min, ${item.outcome}${Number(item.amount) ? ` ${qar(item.amount)}` : ""}`).join("; ")}.{detail.warning_letters > 0 && ` ${detail.warning_letters} warning letter${detail.warning_letters > 1 ? "s" : ""} to issue.`}</li>}
      {(detail.commission ?? []).map((row) => <li key={row.instalment}><strong>{row.kind === "reward" ? "Performance reward" : "Commission"}:</strong> {qar(row.amount)} QAR for {row.year}, due in {new Date(`${row.month}-01T00:00:00`).toLocaleDateString("en-GB", { month: "long", year: "numeric" })}, as released on the Commission and rewards page.</li>)}
      {(detail.loans ?? []).map((row) => <li key={row.loan}><strong>{row.label} instalment:</strong> {qar(row.amount)} QAR{row.amount !== row.due ? ` of ${qar(row.due)} due` : ""}, leaving {qar(row.balance_after)} QAR still owed.</li>)}
      {detail.end_of_service && !detail.end_of_service.settlement && <li><strong>End of service:</strong> {detail.end_of_service.years} years of service × basic × 21 ÷ 30 = {qar(detail.end_of_service.amount)} QAR.</li>}
      {detail.end_of_service?.settlement && <li><strong>Final settlement:</strong> end of service {qar(detail.end_of_service.gratuity ?? "0")}{Number(detail.end_of_service.already_paid) ? `, less ${qar(detail.end_of_service.already_paid ?? "0")} paid before` : ""}, unused leave {detail.end_of_service.leave_days} days {qar(detail.end_of_service.leave_pay ?? "0")}{Number(detail.end_of_service.additions) ? `, extras ${qar(detail.end_of_service.additions ?? "0")}` : ""}{Number(detail.end_of_service.deductions) ? `, less deductions ${qar(detail.end_of_service.deductions ?? "0")}` : ""} = {qar(detail.end_of_service.amount)} QAR, as approved on the End of service page.</li>}
      {detail.notes.map((note) => <li key={note}>{note}</li>)}
      <li><strong>Paid to:</strong> {detail.bank_name ? `${show(detail.bank_name)} ` : ""}<span className="pw-mono">{detail.iban || "no IBAN"}</span></li>
    </ul>
  </div>;
}

function WpsDetails({ overview, unitId, csrfToken, onDone }: { overview: Overview; unitId: number; csrfToken: string; onDone: (message: string) => void }) {
  const current = overview.wps;
  const [editing, setEditing] = useState(false);
  const [eid, setEid] = useState(current?.employer_eid ?? "");
  const [bank, setBank] = useState(current?.payer_bank ?? "");
  const [iban, setIban] = useState("");
  const [error, setError] = useState("");
  const save = async () => {
    setError("");
    try {
      await call(`/api/hr/wps-settings/?unit=${unitId}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ employer_eid: eid, payer_bank: bank, payer_iban: iban }) });
      setEditing(false);
      onDone(`WPS details for ${overview.unit.name} are saved.`);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "The WPS details could not be saved.");
    }
  };
  return <div className="py-hours" aria-label="WPS details">
    <span><strong>WPS:</strong> {current ? `employer ID ${current.employer_eid}, paid from ${current.payer_bank} ${current.payer_iban} (set by ${current.set_by})` : "employer ID and paying account not set yet, so the salary file cannot be made"}</span>
    {overview.can_manage && !editing && <button className="fa-link" type="button" onClick={() => setEditing(true)}>{current ? "Change" : "Set them"}</button>}
    {editing && <span className="mj-inline">
      <label>Employer ID<input inputMode="numeric" value={eid} maxLength={8} onChange={(event) => setEid(event.target.value)} placeholder="7 or 8 digits" /></label>
      <label>Paying bank<select value={bank} onChange={(event) => setBank(event.target.value)}>
        <option value="">Choose…</option>
        {overview.wps_banks.map((option) => <option key={option.code} value={option.code}>{option.name}</option>)}
      </select></label>
      <label>Paying account IBAN<input className="pr-iban" value={iban} maxLength={40} onChange={(event) => setIban(event.target.value)} placeholder="QA.." /></label>
      <button className="button button-dark" type="button" onClick={() => void save()}>Save</button>
      <button className="button button-quiet" type="button" onClick={() => setEditing(false)}>Cancel</button>
    </span>}
    {error && <div className="alert" role="alert">{error}</div>}
  </div>;
}

export default function Payroll({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [month, setMonth] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [open, setOpen] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (selectedUnitId === null) { setError("Choose a business unit to see its payroll."); return; }
    try {
      setOverview(await call<Overview>(`/api/hr/payroll/?unit=${selectedUnitId}${month ? `&month=${month}` : ""}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Payroll could not be loaded.");
    }
  }, [selectedUnitId, month]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); setActionError(""); void load(); };

  const act = async (work: () => Promise<string>) => {
    setBusy(true); setActionError(""); setNotice("");
    try { done(await work()); } catch (caught) {
      setActionError(caught instanceof Error ? show(caught.message) : "That could not be done.");
    } finally { setBusy(false); }
  };
  const prepare = () => act(async () => {
    const run = await call<Run>(`/api/hr/payroll/?unit=${selectedUnitId}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ month: overview!.month }) });
    return `${run.month_label} payroll is worked out: ${qar(run.totals.net)} QAR for ${run.totals.people} people. Someone with approval rights now checks and approves it.`;
  });
  const decide = (action: "approve" | "discard") => act(async () => {
    const run = overview!.run!;
    await call(`/api/hr/payroll/${run.id}/?unit=${selectedUnitId}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ action }) });
    return action === "approve" ? `${run.month_label} payroll is approved and posted to the books. Download the WPS file for the bank.` : `${run.month_label} payroll is discarded.`;
  });
  const download = () => act(async () => {
    const run = overview!.run!;
    const response = await fetch(`/api/hr/payroll/${run.id}/wps-file/?unit=${selectedUnitId}`, { credentials: "same-origin" });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(typeof data.detail === "string" ? data.detail : "The WPS file could not be made.");
    }
    const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") ?? "")?.[1] ?? "wps.csv";
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    link.click();
    URL.revokeObjectURL(link.href);
    return `The WPS file ${name} is downloaded. Upload it to the bank's WPS portal.`;
  });

  const run = overview?.run ?? null;
  return <section className="content-card supplier-payments bank-cash payroll-page" aria-labelledby="payroll-title">
    <header className="sp-head">
      <span className="eyebrow">HR · PAYROLL · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="payroll-title">Payroll</h2>
      <p>Each month's pay: salaries and allowances, overtime, absences and late arrivals, and end of service. One person prepares it, another approves it, and then the WPS file goes to the bank.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading payroll…</p>}
    {overview && selectedUnitId !== null && <>
      <div className="pr-bar">
        <label>Month<input type="month" value={overview.month} onChange={(event) => { setMonth(event.target.value); setNotice(""); setActionError(""); setOpen([]); }} /></label>
        <p>Counts overtime, absences and late arrivals from <strong>{day(overview.period.start)}</strong> to the cut-off on <strong>{day(overview.period.cut_off)}</strong>. Paid on <strong>{day(overview.period.pay_date)}</strong>.</p>
      </div>
      {actionError && <div className="alert" role="alert">{actionError}</div>}
      {!run && <div className="pr-empty">
        <p>{overview.month_label} payroll has not been prepared yet.{!overview.cut_off_passed && ` It can be prepared after the cut-off on ${day(overview.period.cut_off)}.`}</p>
        {overview.can_prepare && <button className="button button-dark" type="button" disabled={busy} onClick={() => void prepare()}>Work out {overview.month_label} payroll</button>}
      </div>}
      {run && <>
        <ul className="bc-accounts ex-tiles" aria-label={`${run.month_label} payroll totals`}>
          <li className="bc-total"><span>Net to pay</span><strong>{qar(run.totals.net)}</strong><small>QAR to {run.totals.people} people on {day(run.pay_date)}</small></li>
          <li className="bc-total"><span>Salaries and allowances</span><strong>{qar(Number(run.totals.basic) + Number(run.totals.allowances))}</strong><small>basic {qar(run.totals.basic)}, allowances {qar(run.totals.allowances)}</small></li>
          <li className="bc-total"><span>Overtime</span><strong>{qar(run.totals.overtime)}</strong><small>charged to its sales orders</small></li>
          <li className="bc-total"><span>Deductions</span><strong>{qar(run.totals.deductions)}</strong><small>absences, unpaid leave, late arrivals</small></li>
          {Number(run.totals.commission ?? 0) > 0 && <li className="bc-total"><span>Commission and rewards</span><strong>{qar(run.totals.commission)}</strong><small>released on the Commission and rewards page</small></li>}
          {Number(run.totals.end_of_service) > 0 && <li className="bc-total"><span>End of service</span><strong>{qar(run.totals.end_of_service)}</strong><small>for people leaving this month</small></li>}
        </ul>
        <p className="pr-status"><span className={`sp-status sp-status-${run.status === "posted" ? "approved" : "waiting"}`}>{run.status_label}</span>
          {" "}Prepared by {run.prepared_by}{run.approved_by && `, approved by ${run.approved_by} on ${day(run.approved_at.slice(0, 10))} and posted as journal #${run.journal_entry}`}.</p>
        {run.warnings.length > 0 && <div className="pr-warnings" role="note" aria-label="Check before approving"><strong>{run.status === "posted" ? "Noted when it was prepared" : "Check before approving"}</strong><ul>{run.warnings.map((warning) => <li key={warning}>{show(warning)}</li>)}</ul></div>}
        <div className="sp-actions pr-actions">
          {run.can_approve && <button className="button button-dark" type="button" disabled={busy} onClick={() => void decide("approve")}>Approve and post</button>}
          {run.can_recalculate && <button className="button button-quiet" type="button" disabled={busy} onClick={() => void prepare()}>Work it out again</button>}
          {run.can_recalculate && <button className="button button-quiet" type="button" disabled={busy} onClick={() => void decide("discard")}>Discard</button>}
          {run.can_download && <button className="button button-dark" type="button" disabled={busy} onClick={() => void download()}>Download WPS file</button>}
        </div>
        <div className="lv-block">
          <div className="sp-table-wrap"><table className="sp-table bc-table pr-table" aria-label={`${run.month_label} payroll`}>
            <thead><tr><th scope="col">Who</th><th scope="col" className="bc-num">Days</th><th scope="col" className="bc-num">Basic</th><th scope="col" className="bc-num">Allowances</th><th scope="col" className="bc-num">Overtime</th><th scope="col" className="bc-num">Deductions</th><th scope="col" className="bc-num">End of service</th><th scope="col" className="bc-num">Net</th></tr></thead>
            <tbody>{run.lines.map((line) => <Fragment key={line.id}>
              <tr>
                <th scope="row">{show(line.employee.name)}<small>{line.employee.job_title} · {line.employee.department}</small>
                  <button className="fa-link" type="button" onClick={() => setOpen(open.includes(line.id) ? open.filter((id) => id !== line.id) : [...open, line.id])}>{open.includes(line.id) ? "Hide workings" : "Workings"}</button></th>
                <td className="pw-mono bc-num">{line.days_paid}</td>
                <td className="pw-mono bc-num">{qar(line.basic)}</td>
                <td className="pw-mono bc-num">{money(line.allowances)}</td>
                <td className="pw-mono bc-num">{money(line.overtime)}{Number(line.overtime_hours) > 0 && <small>{line.overtime_hours} h</small>}</td>
                <td className="pw-mono bc-num">{Number(line.deductions) ? `−${qar(line.deductions)}` : ""}</td>
                <td className="pw-mono bc-num">{money(line.end_of_service)}</td>
                <td className="pw-mono bc-num"><strong>{qar(line.net)}</strong></td>
              </tr>
              {open.includes(line.id) && <tr className="pr-open"><td colSpan={8}><Breakdown line={line} /></td></tr>}
            </Fragment>)}</tbody>
          </table></div>
        </div>
      </>}
      <WpsDetails key={`${notice}-${overview.wps?.employer_eid ?? ""}`} overview={overview} unitId={selectedUnitId} csrfToken={csrfToken} onDone={done} />
      {overview.recent.length > 0 && <p className="sp-hint">Recent months: {overview.recent.map((row) => `${row.month} ${row.status_label.toLowerCase()}${row.net ? ` ${qar(row.net)}` : ""}`).join(" · ")}</p>}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
