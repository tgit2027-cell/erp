import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";
import "./staff-loans.css";

type Kind = "loan" | "advance";
type Loan = {
  id: number; kind: Kind; kind_label: string; employee: { id: number; name: string; staff_number: string; department: string };
  amount: string; instalment: string; first_month: string; first_month_label: string; reason: string;
  status: "requested" | "approved" | "repaying" | "repaid" | "rejected" | "cancelled"; status_label: string;
  repaid: string; balance: string; months_left: number; repayments: { month: string; amount: string; from_settlement: boolean }[];
  requested_by: string; decided_by: string; decision_note: string; paid_from: string; paid_on: string; paid_by: string;
  can_decide: boolean; can_pay: boolean; can_cancel: boolean;
};
type Limits = { eos: string; month_pay: string; owed: string; room: string; basic: string };
type Overview = {
  unit: { id: number; name: string }; today: string; next_month: string;
  tiles: { owed: string; repaying: number; waiting: number; to_pay: number };
  loans: Loan[]; people: { id: number; name: string; department: string; limits: Limits | null }[];
  accounts: { id: number; name: string; kind_label: string }[];
  can_request: boolean; policy: string;
};

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The request could not be completed.");
  return data as T;
}
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const qar = (value: string | number) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const headers = (csrfToken: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken });
const TONE: Record<string, string> = { requested: "waiting", approved: "waiting", repaying: "approved", repaid: "done", rejected: "rejected", cancelled: "done" };

function monthsAhead(start: string, count: number) {
  const [year, month] = start.split("-").map(Number);
  return Array.from({ length: count }, (_, index) => {
    const day = new Date(year, month - 1 + index, 1);
    return { value: `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}`, label: day.toLocaleDateString("en-GB", { month: "long", year: "numeric" }) };
  });
}

function RequestForm({ overview, csrfToken, onDone }: { overview: Overview; csrfToken: string; onDone: (message: string) => void }) {
  const [kind, setKind] = useState<Kind>("advance");
  const [employeeId, setEmployeeId] = useState("");
  const [amount, setAmount] = useState("");
  const [instalment, setInstalment] = useState("");
  const [firstMonth, setFirstMonth] = useState(overview.next_month);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const person = overview.people.find((row) => String(row.id) === employeeId);
  const limits = person?.limits ?? null;
  const each = Number(instalment || amount);
  const months = amount && each > 0 ? Math.ceil(Number(amount) / each) : 0;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const row = await call<Loan>(`/api/hr/loans/?unit=${overview.unit.id}`, { method: "POST", headers: headers(csrfToken),
        body: JSON.stringify({ kind, employee_id: Number(employeeId), amount, ...(instalment ? { instalment } : {}), first_month: firstMonth, reason }) });
      onDone(`${row.kind_label} of QAR ${qar(row.amount)} for ${show(row.employee.name)} is recorded and waiting for approval.`);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "It could not be recorded.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form an-form" onSubmit={(event) => void submit(event)} aria-label="Record a loan or an advance">
    <h3>Record a loan or an advance</h3>
    <div className="sp-fields an-grid">
      <label>Type<select value={kind} onChange={(event) => setKind(event.target.value as Kind)}>
        <option value="advance">Salary advance</option><option value="loan">Staff loan</option>
      </select></label>
      <label>Employee<select value={employeeId} onChange={(event) => setEmployeeId(event.target.value)} required>
        <option value="">Choose…</option>
        {overview.people.map((row) => <option key={row.id} value={row.id}>{show(row.name)} · {row.department}</option>)}
      </select></label>
      <label>Amount (QAR)<input value={amount} inputMode="decimal" onChange={(event) => setAmount(event.target.value)} required /></label>
      <label>Monthly instalment (QAR)<input value={instalment} inputMode="decimal" placeholder={amount ? `${amount}, all in one month` : "the whole amount"} onChange={(event) => setInstalment(event.target.value)} /></label>
      <label>First instalment in<select value={firstMonth} onChange={(event) => setFirstMonth(event.target.value)}>
        {monthsAhead(overview.next_month, 4).map((month) => <option key={month.value} value={month.value}>{month.label} payroll</option>)}
      </select></label>
      <label className="an-wide">Reason<input value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder="e.g. school fees" required /></label>
    </div>
    <p className="sp-hint sl-hint">
      Enter the amount and instalment management agreed. Everything owed must stay within the end of service earned so far plus the coming month's salary.
      {limits && ` ${show(person!.name)} can borrow up to QAR ${qar(limits.room)} now (end of service ${qar(limits.eos)} + salary ${qar(limits.month_pay)}${Number(limits.owed) ? ` − already owed ${qar(limits.owed)}` : ""}).`}
      {months > 0 ? ` At this instalment it takes ${months} ${months === 1 ? "month" : "months"}.` : ""}
    </p>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy}>Send for approval</button></div>
  </form>;
}

function LoanRow({ loan, overview, csrfToken, onDone }: { loan: Loan; overview: Overview; csrfToken: string; onDone: (message: string) => void }) {
  const [asking, setAsking] = useState<"reject" | "pay" | "cancel" | null>(null);
  const [note, setNote] = useState("");
  const [accountId, setAccountId] = useState("");
  const [date, setDate] = useState(overview.today);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const name = show(loan.employee.name);
  const run = async (action: "approve" | "reject" | "pay" | "cancel") => {
    setBusy(true); setError("");
    try {
      const body = action === "pay" ? { action, account_id: Number(accountId), date } : { action, note };
      const row = await call<Loan>(`/api/hr/loans/${loan.id}/?unit=${overview.unit.id}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify(body) });
      onDone({
        approve: `${row.kind_label} for ${name} is approved. Finance can now pay it out.`,
        reject: `${row.kind_label} for ${name} is rejected.`,
        pay: `QAR ${qar(row.amount)} is paid out to ${name} from ${show(row.paid_from)}. Instalments start in the ${row.first_month_label} payroll.`,
        cancel: `${row.kind_label} for ${name} is cancelled.`,
      }[action]);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "That could not be done.");
    } finally { setBusy(false); }
  };
  const open = ["repaying", "repaid"].includes(loan.status);
  return <tr>
    <th scope="row">{name}<small>{loan.employee.department} · {loan.employee.staff_number}</small></th>
    <td>{loan.kind_label}<small>{show(loan.reason)}</small></td>
    <td className="pw-mono bc-num">{qar(loan.amount)}</td>
    <td>QAR {qar(loan.instalment)} a month<small>from {loan.first_month_label}</small></td>
    <td className="pw-mono bc-num">{open ? qar(loan.balance) : "–"}{loan.status === "repaying" && <small>{loan.months_left} {loan.months_left === 1 ? "month" : "months"} left</small>}</td>
    <td>
      <span className={`sp-status sp-status-${TONE[loan.status]}`}>{loan.status_label}</span>
      <small>Asked by {loan.requested_by}{loan.decided_by && `, ${loan.status === "rejected" ? "rejected" : "approved"} by ${loan.decided_by}`}
        {loan.paid_from && `, paid ${loan.paid_on} from ${show(loan.paid_from)}`}{loan.decision_note && ` · ${show(loan.decision_note)}`}</small>
      {loan.repayments.length > 0 && <details className="sl-history"><summary>{loan.repayments.length} {loan.repayments.length === 1 ? "repayment" : "repayments"}, QAR {qar(loan.repaid)}</summary>
        <ul>{loan.repayments.map((row, index) => <li key={index}>{row.month}: QAR {qar(row.amount)}{row.from_settlement ? " from the final settlement" : ""}</li>)}</ul></details>}
      {asking === null && (loan.can_decide || loan.can_pay || loan.can_cancel) && <span className="lv-actions">
        {loan.can_decide && <button className="button button-dark" type="button" disabled={busy} onClick={() => void run("approve")}>Approve</button>}
        {loan.can_decide && <button className="button button-outline" type="button" disabled={busy} onClick={() => { setAsking("reject"); setNote(""); }}>Reject</button>}
        {loan.can_pay && <button className="button button-dark" type="button" disabled={busy} onClick={() => setAsking("pay")}>Pay out</button>}
        {loan.can_cancel && <button className="button button-outline" type="button" disabled={busy} onClick={() => { setAsking("cancel"); setNote(""); }}>Cancel</button>}
      </span>}
      {asking === "pay" && <span className="mj-inline sl-inline">
        <select aria-label="Paid from" value={accountId} onChange={(event) => setAccountId(event.target.value)}>
          <option value="">Paid from…</option>
          {overview.accounts.map((account) => <option key={account.id} value={account.id}>{show(account.name)} · {account.kind_label}</option>)}
        </select>
        <input aria-label="Paid on" type="date" value={date} max={overview.today} onChange={(event) => setDate(event.target.value)} />
        <button className="button button-dark" type="button" disabled={busy || !accountId} onClick={() => void run("pay")}>Confirm payout</button>
        <button className="button button-quiet" type="button" onClick={() => setAsking(null)}>Back</button>
      </span>}
      {(asking === "reject" || asking === "cancel") && <span className="mj-inline sl-inline">
        <input aria-label="Reason" value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} placeholder="Why" />
        <button className="button button-dark" type="button" disabled={busy || !note.trim()} onClick={() => void run(asking)}>{asking === "reject" ? "Confirm reject" : "Confirm cancel"}</button>
        <button className="button button-quiet" type="button" onClick={() => setAsking(null)}>Back</button>
      </span>}
      {error && <div className="alert" role="alert">{error}</div>}
    </td>
  </tr>;
}

export default function StaffLoans({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [formKey, setFormKey] = useState(0);
  const load = useCallback(async () => {
    if (selectedUnitId === null) { setError("Choose a business unit to see staff loans and advances."); return; }
    try {
      setOverview(await call<Overview>(`/api/hr/loans/?unit=${selectedUnitId}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Staff loans could not be loaded.");
    }
  }, [selectedUnitId]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); setFormKey((key) => key + 1); void load(); };
  return <section className="content-card supplier-payments bank-cash staff-loans-page" aria-labelledby="sl-title">
    <header className="sp-head">
      <span className="eyebrow">HR · LOANS AND ADVANCES · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="sl-title">Loans and advances</h2>
      <p>Money lent to staff and salary advances. HR records it, someone else approves it, finance pays it out, and it is taken back a month at a time through payroll. Anything still owed when someone leaves comes off their final settlement.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading…</p>}
    {overview && <>
      <dl className="sp-totals" aria-label="Loans at a glance">
        <div><dt>Still owed by staff</dt><dd>QAR {qar(overview.tiles.owed)}</dd></div>
        <div><dt>Being repaid</dt><dd>{overview.tiles.repaying}</dd></div>
        <div><dt>Waiting for approval</dt><dd>{overview.tiles.waiting}</dd></div>
        <div><dt>Approved, to pay out</dt><dd>{overview.tiles.to_pay}</dd></div>
      </dl>
      {overview.can_request && <RequestForm key={formKey} overview={overview} csrfToken={csrfToken} onDone={done} />}
      {overview.loans.length === 0
        ? <p className="sp-empty">No loans or advances in {overview.unit.name} in the last year.</p>
        : <div className="sp-table-wrap"><table className="sp-table bc-table sl-table" aria-label="Loans and advances">
          <thead><tr><th scope="col">Employee</th><th scope="col">Type</th><th scope="col" className="bc-num">Amount</th><th scope="col">Instalment</th>
            <th scope="col" className="bc-num">Still owed</th><th scope="col">Status</th></tr></thead>
          <tbody>{overview.loans.map((loan) => <LoanRow key={`${loan.id}-${loan.status}`} loan={loan} overview={overview} csrfToken={csrfToken} onDone={done} />)}</tbody>
        </table></div>}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
