import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Row = {
  id: number; direction: "received" | "issued"; number: string; drawn_on: string; account: { id: number; name: string } | null; received_on: string | null; party: string;
  customer_id: number | null; order: { id: number; label: string } | null; supplier_payment_id: number | null; amount: string;
  cheque_date: string; days: number; status: string; status_label: string; note: string; reason: string; deposited_on: string | null;
  finished_on: string | null; replaces: string | null; replaced_by: string | null; journal_entry_id: number | null;
  recorded_by: string; updated_by: string; actions: string[];
};
type Tile = { count: number; total: string };
type Order = { id: number; customer_id: number; customer: string; label: string; unpaid: string; held: string; open: string };
type Overview = {
  unit: { id: number; name: string }; today: string;
  tiles: { held: Tile; due_this_week: Tile; deposited: Tile; bounced: Tile; to_cash: Tile };
  received: Row[]; issued: Row[]; finished: Row[];
  weeks: { start: string; end: string; in: string; out: string; in_count: number; out_count: number }[];
  accounts: { id: number; name: string; bank_name: string }[]; orders: Order[];
  supplier_payments: { id: number; label: string; amount: string; paid_on: string; status_label: string }[];
  can_manage: boolean; policy: string;
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

const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const headers = (csrfToken: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken });
const money = (value: string) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const STATUS_CLASS: Record<string, string> = {
  held: "ex-waiting", deposited: "ex-waiting", issued: "ex-waiting", cleared: "ex-booked", bounced: "ex-rejected", returned: "ex-rejected", cancelled: "ex-rejected",
};
const ACTION_LABEL: Record<string, string> = {
  deposit: "Deposited at the bank", clear: "Cleared", bounce: "Bounced", return: "Handed back", cancel: "Cancel or stop", replace: "Record a replacement",
};
const NEEDS_REASON = new Set(["bounce", "return", "cancel"]);
const when = (row: Row) => row.days === 0 ? "today" : row.days > 0 ? `in ${row.days} day${row.days === 1 ? "" : "s"}` : `${-row.days} day${row.days === -1 ? "" : "s"} ago`;

function RecordForm({ overview, csrfToken, replacing, onDone }: { overview: Overview; csrfToken: string; replacing: Row | null; onDone: (message: string) => void }) {
  const [direction, setDirection] = useState<"received" | "issued">("received");
  const [customerId, setCustomerId] = useState(replacing ? String(replacing.customer_id) : "");
  const [orderId, setOrderId] = useState(replacing?.order ? String(replacing.order.id) : "");
  const [payee, setPayee] = useState("");
  const [paymentId, setPaymentId] = useState("");
  const [number, setNumber] = useState("");
  const [drawnOn, setDrawnOn] = useState("");
  const [amount, setAmount] = useState(replacing ? replacing.amount : "");
  const [chequeDate, setChequeDate] = useState("");
  const [accountId, setAccountId] = useState(String(overview.accounts[0]?.id ?? ""));
  const [receivedOn, setReceivedOn] = useState(overview.today);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const customers = [...new Map(overview.orders.map((order) => [order.customer_id, order.customer])).entries()];
  const orders = overview.orders.filter((order) => String(order.customer_id) === customerId);
  const order = overview.orders.find((row) => String(row.id) === orderId);
  const received = replacing !== null || direction === "received";
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const body = received
        ? { direction: "received", order_id: Number(orderId), drawn_on: drawnOn, received_on: receivedOn, ...(replacing ? { replaces_id: replacing.id } : {}) }
        : { direction: "issued", payee, account_id: Number(accountId), ...(paymentId ? { supplier_payment_id: Number(paymentId) } : {}) };
      const row = await call<Row>(`/api/finance/cheques/?unit=${overview.unit.id}`, { method: "POST", headers: headers(csrfToken),
        body: JSON.stringify({ ...body, number, amount, cheque_date: chequeDate, note }) });
      onDone(`Cheque ${row.number} for QAR ${money(row.amount)} ${received ? `from ${show(row.party)} is recorded. It is booked when the bank clears it.` : `to ${show(row.party)} is recorded.`}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The cheque could not be recorded.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form an-form" onSubmit={(event) => void submit(event)} aria-label="Record a cheque">
    <h3>{replacing ? `Replacement for cheque ${replacing.number}` : "Record a cheque"}</h3>
    <div className="sp-fields an-grid">
      {!replacing && <label>Cheque<select value={direction} onChange={(event) => setDirection(event.target.value as typeof direction)}>
        <option value="received">Received from a customer</option><option value="issued">Written by us</option>
      </select></label>}
      {received && !replacing && <label>Customer<select value={customerId} onChange={(event) => { setCustomerId(event.target.value); setOrderId(""); }} required>
        <option value="">Choose…</option>{customers.map(([id, name]) => <option key={id} value={id}>{show(name)}</option>)}
      </select></label>}
      {received && !replacing && <label>Order<select value={orderId} onChange={(event) => setOrderId(event.target.value)} required disabled={!customerId}>
        <option value="">Choose…</option>{orders.map((row) => <option key={row.id} value={row.id}>{row.label} · QAR {money(row.open)} open</option>)}
      </select></label>}
      {received && <label>Drawn on (customer's bank)<input value={drawnOn} maxLength={80} onChange={(event) => setDrawnOn(event.target.value)} placeholder="e.g. Commercial Bank" required /></label>}
      {!received && <label>Pay to<input value={payee} maxLength={120} onChange={(event) => setPayee(event.target.value)} placeholder="e.g. Roche Bobois" required /></label>}
      {!received && overview.supplier_payments.length > 0 && <label>For supplier payment (optional)<select value={paymentId} onChange={(event) => setPaymentId(event.target.value)}>
        <option value="">None</option>{overview.supplier_payments.map((row) => <option key={row.id} value={row.id}>{show(row.label)} · QAR {money(row.amount)} · {row.status_label}</option>)}
      </select></label>}
      <label>Cheque number<input value={number} inputMode="numeric" maxLength={12} onChange={(event) => setNumber(event.target.value)} required /></label>
      <label>Amount (QAR)<input value={amount} inputMode="decimal" onChange={(event) => setAmount(event.target.value)} required /></label>
      {received && <label>Received on<input type="date" value={receivedOn} max={overview.today} onChange={(event) => setReceivedOn(event.target.value)} required /></label>}
      <label>Date on the cheque<input type="date" value={chequeDate} onChange={(event) => setChequeDate(event.target.value)} required /></label>
      {!received && <label>Our account<select value={accountId} onChange={(event) => setAccountId(event.target.value)}>
        {overview.accounts.map((account) => <option key={account.id} value={account.id}>{show(account.name)}</option>)}
      </select></label>}
      <label className="an-wide">Note (optional)<input value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} /></label>
    </div>
    {received && order && <p className="sp-hint">{order.label}: QAR {money(order.unpaid)} unpaid, QAR {money(order.held)} already covered by cheques.</p>}
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy || (!received && overview.accounts.length === 0)}>Record the cheque</button></div>
  </form>;
}

function Table({ title, rows, overview, csrfToken, onDone, onReplace }: {
  title: string; rows: Row[]; overview: Overview; csrfToken: string; onDone: (message: string) => void; onReplace: (row: Row) => void;
}) {
  const [asking, setAsking] = useState<{ id: number; action: string } | null>(null);
  const [reason, setReason] = useState("");
  const [bankId, setBankId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (row: Row, action: string) => {
    if (action === "replace") { onReplace(row); return; }
    const asks = NEEDS_REASON.has(action) || (action === "clear" && row.direction === "received");
    if (asks && !(asking?.id === row.id && asking.action === action)) { setAsking({ id: row.id, action }); setReason(""); setBankId(""); return; }
    setBusy(true); setError("");
    try {
      const done = await call<Row>(`/api/finance/cheques/${row.id}/?unit=${overview.unit.id}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ action, reason, ...(action === "clear" && row.direction === "received" ? { account_id: Number(bankId) } : {}) }) });
      setAsking(null);
      onDone(`Cheque ${done.number}: ${done.status_label.toLowerCase()}${done.journal_entry_id ? `. Booked as ${show(done.party)}'s payment (journal ${done.journal_entry_id}).` : "."}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That could not be done.");
    } finally { setBusy(false); }
  };
  return <div className="lv-block">
    <h3>{title}</h3>
    <div className="sp-table-wrap"><table className="sp-table bc-table cq-table" aria-label={title}>
      <thead><tr><th scope="col">Cheque</th><th scope="col">{rows[0]?.direction === "issued" ? "Pay to" : "From"}</th><th scope="col">Date on it</th><th scope="col" className="bc-num">QAR</th><th scope="col">Status</th></tr></thead>
      <tbody>{rows.map((row) => <tr key={row.id}>
        <th scope="row">{row.number}<small>{row.direction === "received" ? `${show(row.drawn_on)}${row.account ? ` → ${show(row.account.name)}` : ""}` : show(row.account?.name ?? "")}</small></th>
        <td>{show(row.party)}<small>{row.order?.label}{row.supplier_payment_id ? `Supplier payment ${row.supplier_payment_id}` : ""}{row.replaces ? ` · replaces ${row.replaces}` : ""}</small></td>
        <td>{row.cheque_date}{["held", "issued"].includes(row.status) && <small className={row.days <= 0 ? "cq-due" : ""}>{when(row)}</small>}
          {row.received_on && <small>Received {row.received_on}</small>}</td>
        <td className="pw-mono bc-num">{money(row.amount)}</td>
        <td><span className={`ex-status ${STATUS_CLASS[row.status] ?? ""}`}>{row.status_label}</span>
          {row.reason && <small>{show(row.reason)}</small>}
          {row.replaced_by && <small>Replaced by cheque {row.replaced_by}</small>}
          {row.actions.length > 0 && <span className="lv-actions">{row.actions.map((action) =>
            <button key={action} className={`button ${action === "clear" || action === "deposit" ? "button-dark" : "button-outline"}`} type="button" disabled={busy} onClick={() => void run(row, action)}>{ACTION_LABEL[action]}</button>)}</span>}
          {asking?.id === row.id && asking.action === "clear" && <span className="mj-inline">
            <select aria-label="Bank account the money went into" value={bankId} onChange={(event) => setBankId(event.target.value)}>
              <option value="">Choose the bank account</option>
              {overview.accounts.map((account) => <option key={account.id} value={account.id}>{show(account.name)}</option>)}
            </select>
            <button className="button button-dark" type="button" disabled={busy || !bankId} onClick={() => void run(row, "clear")}>Confirm cleared</button>
          </span>}
          {asking?.id === row.id && asking.action !== "clear" && <span className="mj-inline">
            <input aria-label="Reason" value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder={asking.action === "bounce" ? "e.g. insufficient funds" : "Why"} />
            <button className="button button-dark" type="button" disabled={busy || !reason.trim()} onClick={() => void run(row, asking.action)}>Confirm</button>
          </span>}
        </td>
      </tr>)}</tbody>
    </table></div>
    {error && <div className="alert" role="alert">{error}</div>}
  </div>;
}

export default function Cheques({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [replacing, setReplacing] = useState<Row | null>(null);
  const load = useCallback(async () => {
    if (selectedUnitId === null) { setError("Choose a business unit first."); return; }
    try {
      setOverview(await call<Overview>(`/api/finance/cheques/?unit=${selectedUnitId}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Cheques could not be loaded.");
    }
  }, [selectedUnitId]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); setReplacing(null); void load(); };
  const tiles = overview?.tiles;

  return <section className="content-card supplier-payments bank-cash cheques-page" aria-labelledby="cheques-title">
    <header className="sp-head">
      <span className="eyebrow">FINANCE · CHEQUES · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="cheques-title">Cheques</h2>
      <p>Post-dated cheques from customers and to suppliers. A customer's cheque is booked as their payment only when the bank clears it.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading cheques…</p>}
    {overview && tiles && <>
      <ul className="bc-accounts ex-tiles" aria-label="Cheques at a glance">
        <li className="bc-total"><span>Held, not yet due</span><strong>{money(tiles.held.total)}</strong><small>{tiles.held.count} cheque{tiles.held.count === 1 ? "" : "s"} from customers</small></li>
        <li className="bc-total"><span>Due within 7 days</span><strong>{money(tiles.due_this_week.total)}</strong><small>{tiles.due_this_week.count} to deposit</small></li>
        <li className="bc-total"><span>At the bank</span><strong>{money(tiles.deposited.total)}</strong><small>{tiles.deposited.count} waiting to clear</small></li>
        <li className={`bc-total${tiles.bounced.count ? " cq-alert" : ""}`}><span>Bounced</span><strong>{money(tiles.bounced.total)}</strong><small>{tiles.bounced.count} not replaced yet</small></li>
        <li className="bc-total"><span>Written, not cashed</span><strong>{money(tiles.to_cash.total)}</strong><small>{tiles.to_cash.count} to suppliers</small></li>
      </ul>
      {overview.accounts.length === 0 && <p className="lt-warn">Add a bank account on the Bank and cash screen first. Cheques go through a bank account.</p>}
      {overview.can_manage && <RecordForm key={`${notice}-${replacing?.id ?? ""}`} overview={overview} csrfToken={csrfToken} replacing={replacing} onDone={done} />}
      <div className="lv-block">
        <h3>Next 8 weeks</h3>
        <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label="Next 8 weeks">
          <thead><tr><th scope="col">Week</th><th scope="col" className="bc-num">Coming in</th><th scope="col" className="bc-num">Going out</th></tr></thead>
          <tbody>{overview.weeks.map((week) => <tr key={week.start}>
            <th scope="row">{week.start} to {week.end}</th>
            <td className="pw-mono bc-num">{week.in_count ? `${money(week.in)} (${week.in_count})` : ""}</td>
            <td className="pw-mono bc-num">{week.out_count ? `${money(week.out)} (${week.out_count})` : ""}</td>
          </tr>)}</tbody>
        </table></div>
      </div>
      {overview.received.length > 0 && <Table title="Cheques from customers" rows={overview.received} overview={overview} csrfToken={csrfToken} onDone={done} onReplace={setReplacing} />}
      {overview.issued.length > 0 && <Table title="Cheques we wrote" rows={overview.issued} overview={overview} csrfToken={csrfToken} onDone={done} onReplace={setReplacing} />}
      {overview.finished.length > 0 && <Table title="Finished" rows={overview.finished} overview={overview} csrfToken={csrfToken} onDone={done} onReplace={setReplacing} />}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
