import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Account = { id: number; name: string; kind: "bank" | "cash"; opening_date: string };
type Row = {
  id: number; number: string; date: string; category: string; category_label: string; description: string; payee: string;
  amount: string; paid_from: { id: number; name: string; kind: string } | null; claimant: { id: number; name: string } | null;
  has_receipt: boolean; receipt_url: string; status: "submitted" | "approved" | "rejected" | "reimbursed"; status_label: string;
  submitted_by: string; decided_by: string; rejection_reason: string; journal_entry_id: number | null;
  reimbursed_from: string; can_decide?: boolean;
};
type Overview = {
  unit: { id: number; code: string; name: string }; month: string; can_record: boolean; can_approve: boolean; can_reimburse: boolean;
  rows: Row[]; totals: { booked: string; waiting: number; waiting_amount: string; owed_to_staff: string; owed_count: number };
  by_category: { category: string; label: string; total: string; count: number }[];
  categories: { value: string; label: string }[]; accounts?: Account[]; people?: { id: number; name: string }[]; policy_label: string;
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

const today = () => new Date().toLocaleDateString("en-CA");
const fmt = (value: string) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const show = (name: string) => name.replace(/TEST ONLY\s*/g, "");
const STATUS_CLASS: Record<Row["status"], string> = {
  submitted: "ex-waiting", approved: "ex-booked", rejected: "ex-rejected", reimbursed: "ex-booked",
};

function ExpenseForm({ overview, csrfToken, onDone, onCancel }: {
  overview: Overview; csrfToken: string; onDone: (message: string) => void; onCancel: () => void;
}) {
  const accounts = overview.accounts ?? [];
  const cashBox = accounts.find((account) => account.kind === "cash") ?? accounts[0];
  const [form, setForm] = useState({
    date: today(), category: overview.categories[0]?.value ?? "", amount: "", description: "", payee: "",
    paid_by: accounts.length ? "account" : "staff", account_id: String(cashBox?.id ?? ""), claimant_id: String(overview.people?.[0]?.id ?? ""),
  });
  const [receipt, setReceipt] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (field: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [field]: event.target.value });
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    const body = new FormData();
    Object.entries(form).forEach(([key, value]) => body.append(key, value));
    if (receipt) body.append("receipt", receipt);
    try {
      const row = await call<Row>(`/api/finance/expenses/?unit=${overview.unit.id}`, {
        method: "POST", headers: { "X-CSRFToken": csrfToken }, body,
      });
      onDone(`${row.number} entered. It is booked once a second person approves it.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The expense could not be entered.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form" onSubmit={submit} aria-label="Enter an expense">
    <h3>Enter an expense</h3>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-fields">
      <label>Date<input type="date" value={form.date} max={today()} onChange={set("date")} required /></label>
      <label>Category<select value={form.category} onChange={set("category")}>
        {overview.categories.map((category) => <option key={category.value} value={category.value}>{category.label}</option>)}
      </select></label>
      <label>Amount (QAR)<input inputMode="decimal" value={form.amount} onChange={set("amount")} required /></label>
      <label>What it was for<input value={form.description} onChange={set("description")} maxLength={200} placeholder="e.g. Showroom electricity, October" required /></label>
      <label>Shop or supplier (optional)<input value={form.payee} onChange={set("payee")} maxLength={120} /></label>
      <label>Paid by<select value={form.paid_by} onChange={set("paid_by")} aria-label="Paid by">
        {accounts.length > 0 && <option value="account">The company (bank account or cash box)</option>}
        <option value="staff">A member of staff, to be repaid</option>
      </select></label>
      {form.paid_by === "account"
        ? <label>Paid from<select value={form.account_id} onChange={set("account_id")}>
          {accounts.map((account) => <option key={account.id} value={account.id}>{show(account.name)}{account.kind === "cash" ? " (petty cash)" : ""}</option>)}
        </select></label>
        : <label>Who paid<select value={form.claimant_id} onChange={set("claimant_id")}>
          {(overview.people ?? []).map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}
        </select></label>}
      <label>Receipt (PDF, PNG or JPG, optional)<input type="file" accept=".pdf,.png,.jpg,.jpeg" onChange={(event) => setReceipt(event.target.files?.[0] ?? null)} /></label>
    </div>
    <p className="sp-hint">Nothing is booked yet. A second person approves it, and only then does it reach the accounts{form.paid_by === "staff" ? " and show as owed to the member of staff" : ""}.</p>
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy}>Send for approval</button>
      <button className="button button-outline" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
    </div>
  </form>;
}

function Decision({ row, overview, csrfToken, onDone }: { row: Row; overview: Overview; csrfToken: string; onDone: (message: string) => void }) {
  const [mode, setMode] = useState<"" | "reject" | "repay">("");
  const [reason, setReason] = useState("");
  const [accountId, setAccountId] = useState(String(overview.accounts?.[0]?.id ?? ""));
  const [date, setDate] = useState(today());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async (body: object, message: string) => {
    setBusy(true); setError("");
    try {
      await call(`/api/finance/expenses/${row.id}/?unit=${overview.unit.id}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify(body),
      });
      setMode(""); setReason("");
      onDone(message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That did not work.");
    } finally { setBusy(false); }
  };
  const canRepay = row.status === "approved" && row.claimant && overview.can_reimburse;
  if (!row.can_decide && !canRepay) return null;
  return <div className="sp-actions ex-actions">
    {error && <div className="alert" role="alert">{error}</div>}
    {row.can_decide && mode === "" && <>
      <button className="button button-dark ex-small" type="button" disabled={busy} onClick={() => void send({ decision: "approve" }, `${row.number} approved and booked.`)}>Approve</button>
      <button className="button button-outline ex-small" type="button" disabled={busy} onClick={() => setMode("reject")}>Reject</button>
    </>}
    {mode === "reject" && <form className="ex-inline" onSubmit={(event) => { event.preventDefault(); void send({ decision: "reject", reason }, `${row.number} rejected.`); }}>
      <input aria-label={`Why ${row.number} is rejected`} value={reason} onChange={(event) => setReason(event.target.value)} maxLength={200} placeholder="Reason" required />
      <button className="button button-dark ex-small" type="submit" disabled={busy}>Reject</button>
      <button className="button button-outline ex-small" type="button" onClick={() => setMode("")}>Cancel</button>
    </form>}
    {canRepay && mode === "" && <button className="button button-outline ex-small" type="button" aria-label={`Repay ${row.claimant!.name}`} onClick={() => setMode("repay")}>Repay</button>}
    {mode === "repay" && <form className="ex-inline" onSubmit={(event) => { event.preventDefault(); void send({ decision: "repay", account_id: Number(accountId), date }, `${row.claimant!.name} repaid for ${row.number}.`); }}>
      <select aria-label="Repay from" value={accountId} onChange={(event) => setAccountId(event.target.value)}>
        {(overview.accounts ?? []).map((account) => <option key={account.id} value={account.id}>{show(account.name)}</option>)}
      </select>
      <input type="date" aria-label="Repaid on" value={date} max={today()} onChange={(event) => setDate(event.target.value)} required />
      <button className="button button-dark ex-small" type="submit" disabled={busy}>Repay</button>
      <button className="button button-outline ex-small" type="button" onClick={() => setMode("")}>Cancel</button>
    </form>}
  </div>;
}

export default function Expenses({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [entering, setEntering] = useState(false);
  const [month, setMonth] = useState(today().slice(0, 7));
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    const query = new URLSearchParams({ unit: String(selectedUnitId), month, status, search });
    try {
      setOverview(await call<Overview>(`/api/finance/expenses/?${query}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Expenses could not be loaded.");
    }
  }, [selectedUnitId, month, status, search]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setEntering(false); setNotice(message); void load(); };

  return <section className="content-card supplier-payments bank-cash expenses" aria-labelledby="expenses-title">
    <header className="sp-head">
      <span className="eyebrow">FINANCE · EXPENSES AND PETTY CASH · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="expenses-title">Expenses and petty cash</h2>
      <p>Running costs such as rent, electricity, fuel and repairs. Enter what was spent and attach the receipt; a second person approves it before it reaches the accounts. If a member of staff paid with their own money, it shows as owed to them until it is repaid.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading expenses…</p>}
    {overview && <>
      <ul className="bc-accounts ex-tiles" aria-label="This month">
        <li className="bc-total"><span>Booked in {new Date(`${overview.month}-01T00:00:00`).toLocaleDateString("en-GB", { month: "long", year: "numeric" })}</span><strong>{fmt(overview.totals.booked)}</strong><small>QAR, approved expenses</small></li>
        <li className="bc-total"><span>Waiting for approval</span><strong>{overview.totals.waiting}</strong><small>{fmt(overview.totals.waiting_amount)} QAR, any month</small></li>
        <li className="bc-total"><span>Owed to staff</span><strong>{fmt(overview.totals.owed_to_staff)}</strong><small>QAR across {overview.totals.owed_count} expense{overview.totals.owed_count === 1 ? "" : "s"}</small></li>
      </ul>
      {overview.by_category.length > 0 && <p className="ex-categories" aria-label="By category">
        {overview.by_category.map((category) => <span key={category.category}>{category.label} <strong>{fmt(category.total)}</strong></span>)}
      </p>}
      <div className="bc-filters">
        <label>Month<input type="month" value={month} onChange={(event) => setMonth(event.target.value || today().slice(0, 7))} /></label>
        <label>Status<select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All</option><option value="submitted">Waiting for approval</option><option value="approved">Approved</option>
          <option value="reimbursed">Repaid to staff</option><option value="rejected">Rejected</option>
        </select></label>
        <label>Search<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Number, description or shop" /></label>
        {overview.can_record && !entering && <div className="bc-buttons">
          <button className="button button-dark" type="button" onClick={() => { setNotice(""); setEntering(true); }}>Enter an expense</button>
        </div>}
      </div>
      {entering && <ExpenseForm overview={overview} csrfToken={csrfToken} onDone={done} onCancel={() => setEntering(false)} />}
      {overview.rows.length === 0
        ? <p className="sp-empty">No expenses for this month{status || search ? " match" : " yet"}.</p>
        : <div className="sp-table-wrap"><table className="sp-table bc-table ex-table" aria-label="Expenses">
          <thead><tr><th scope="col">Expense</th><th scope="col">What</th><th scope="col">Paid by</th><th scope="col" className="bc-num">QAR</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{overview.rows.map((row) => <tr key={row.id}>
            <th scope="row">{row.number}<small>{row.date} · {row.category_label}</small></th>
            <td>{row.description}<small>{[row.payee, `entered by ${row.submitted_by}`].filter(Boolean).join(" · ")}{row.has_receipt && <> · <a href={`${row.receipt_url}?unit=${overview.unit.id}`} target="_blank" rel="noreferrer">receipt</a></>}</small></td>
            <td>{row.paid_from ? show(row.paid_from.name) : row.claimant?.name}<small>{row.paid_from ? (row.paid_from.kind === "cash" ? "petty cash" : "bank") : "staff, to be repaid"}</small></td>
            <td className="pw-mono bc-num">{fmt(row.amount)}</td>
            <td><span className={`ex-status ${STATUS_CLASS[row.status]}`}>{row.status_label}</span>
              <small>{row.status === "rejected" ? `${row.decided_by}: ${row.rejection_reason}` : row.status === "reimbursed" ? `from ${show(row.reimbursed_from)}` : row.decided_by ? `by ${row.decided_by}${row.journal_entry_id ? ` · journal #${row.journal_entry_id}` : ""}` : row.status === "submitted" && !row.can_decide && overview.can_approve ? "needs someone else" : ""}</small></td>
            <td><Decision row={row} overview={overview} csrfToken={csrfToken} onDone={done} /></td>
          </tr>)}</tbody>
        </table></div>}
      <p className="sp-hint">{overview.policy_label}</p>
    </>}
  </section>;
}
