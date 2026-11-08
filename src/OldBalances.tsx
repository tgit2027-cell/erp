import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Side = "customer" | "supplier";
type Balance = {
  id: number; party: { id: number; name: string; number: string | null }; reference: string; date: string; age_days: number;
  currency: string; amount: string; value: string; owed: string; owed_in_currency: string; waiting: string; can_pay_more: boolean;
};
type Settlement = {
  id: number; balance_id: number; reference: string; party: string; status: "requested" | "approved" | "rejected"; status_label: string;
  currency: string; amount: string; exchange_rate: string; paid_value: string; cleared_value: string | null; paid_on: string;
  cash_account: string; bank_reference: string; prepared_by: string; decided_by: string; decision_note: string; can_decide: boolean;
};
type Overview = {
  unit: { id: number; code: string; name: string }; side: Side; balances: Balance[]; total_owed: string;
  waiting: Settlement[]; recent: Settlement[]; accounts: { id: number; name: string; kind: string }[];
  can_prepare: boolean; can_approve: boolean; policy: string;
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

const today = () => new Date().toLocaleDateString("en-CA");
const fmt = (value: string | number) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const headers = (csrfToken: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken });
const STATUS_CLASS: Record<Settlement["status"], string> = { requested: "ex-waiting", approved: "ex-booked", rejected: "ex-rejected" };
const WORDS: Record<Side, { who: string; money: string; action: string; into: string }> = {
  customer: { who: "Customer", money: "Money received", action: "Record money received", into: "Into" },
  supplier: { who: "Supplier", money: "Payment", action: "Record a payment", into: "From" },
};

function Record({ balance, overview, csrfToken, onDone, onCancel }: {
  balance: Balance; overview: Overview; csrfToken: string; onDone: (message: string) => void; onCancel: () => void;
}) {
  const left = Number(balance.owed_in_currency) - Number(balance.waiting);
  const foreign = balance.currency !== "QAR";
  const [amount, setAmount] = useState(left.toFixed(2));
  const [paidOn, setPaidOn] = useState(today());
  const [accountId, setAccountId] = useState(overview.accounts[0] ? String(overview.accounts[0].id) : "");
  const [reference, setReference] = useState("");
  const [rate, setRate] = useState("");
  const [rateSource, setRateSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const words = WORDS[overview.side];
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    const body = {
      balance_id: balance.id, amount, paid_on: paidOn, cash_account_id: Number(accountId), bank_reference: reference,
      ...(foreign ? { exchange_rate: rate, rate_source: rateSource, rate_date: paidOn } : {}),
    };
    try {
      await call(`/api/finance/old-balances/?unit=${overview.unit.id}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify(body) });
      onDone(`${fmt(amount)} ${balance.currency} on ${show(balance.reference)} is waiting for someone else to approve it. Nothing is booked yet.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That could not be saved.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form an-form" onSubmit={(event) => void submit(event)} aria-label={words.action}>
    <h3>{words.action} on {show(balance.reference)} <small>{show(balance.party.name)} · {fmt(left)} {balance.currency} left to pay</small></h3>
    <div className="sp-fields an-grid">
      <label>Amount ({balance.currency})<input inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} required /></label>
      <label>Date<input type="date" value={paidOn} max={today()} onChange={(event) => setPaidOn(event.target.value)} required /></label>
      <label>{words.into}<select value={accountId} onChange={(event) => setAccountId(event.target.value)} required>
        {overview.accounts.length === 0 && <option value="">No bank account or cash box yet</option>}
        {overview.accounts.map((account) => <option key={account.id} value={account.id}>{show(account.name)}</option>)}
      </select></label>
      {foreign && <label>QAR per 1 {balance.currency}<input inputMode="decimal" value={rate} onChange={(event) => setRate(event.target.value)} required /></label>}
      {foreign && <label>Where the rate comes from<input value={rateSource} maxLength={120} onChange={(event) => setRateSource(event.target.value)} required placeholder="e.g. QNB transfer advice" /></label>}
      <label>Bank reference (optional)<input value={reference} maxLength={80} onChange={(event) => setReference(event.target.value)} /></label>
    </div>
    {foreign && Number(rate) > 0 && Number(amount) > 0 && <p className="sp-hint">Leaves the bank as {fmt(Number(amount) * Number(rate))} QAR. Any gap with the go-live value is booked as an exchange gain or loss.</p>}
    {Number(amount) > left && <p className="alert" role="alert">That is more than is left to pay on this {overview.side === "customer" ? "invoice" : "bill"}.</p>}
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy || !accountId || !Number(amount) || Number(amount) > left}>Send for approval</button>
      <button className="button button-quiet" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </form>;
}

function Settlements({ rows, label, overview, csrfToken, onChanged }: {
  rows: Settlement[]; label: string; overview: Overview; csrfToken: string; onChanged: (message: string) => void;
}) {
  const [rejecting, setRejecting] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (row: Settlement, action: "approve" | "reject") => {
    setBusy(true); setError("");
    try {
      await call(`/api/finance/old-balances/${row.id}/?unit=${overview.unit.id}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ action, note }) });
      setRejecting(null); setNote("");
      onChanged(action === "approve" ? `${fmt(row.amount)} ${row.currency} on ${show(row.reference)} is approved and booked.` : `The payment on ${show(row.reference)} is rejected.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That could not be done.");
    } finally { setBusy(false); }
  };
  return <>
    <h3>{label}</h3>
    <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label={label}>
      <thead><tr><th scope="col">{overview.side === "customer" ? "Invoice" : "Bill"}</th><th scope="col">Date</th><th scope="col" className="bc-num">Amount</th>
        <th scope="col">{WORDS[overview.side].into}</th><th scope="col">Status</th></tr></thead>
      <tbody>{rows.map((row) => <tr key={row.id}>
        <th scope="row">{show(row.reference)}<small>{show(row.party)}</small></th>
        <td>{row.paid_on}</td>
        <td className="pw-mono bc-num">{fmt(row.amount)} {row.currency}{row.currency !== "QAR" && <small>{fmt(row.paid_value)} QAR at {row.exchange_rate}</small>}</td>
        <td>{show(row.cash_account)}{row.bank_reference && <small>{row.bank_reference}</small>}</td>
        <td><span className={`ex-status ${STATUS_CLASS[row.status]}`}>{row.status_label}</span>
          <small>{row.decided_by ? `by ${row.decided_by}${row.decision_note ? `: ${show(row.decision_note)}` : ""}` : `prepared by ${row.prepared_by}`}</small>
          {row.can_decide && <span className="sa-use-actions">
            <button className="button button-dark" type="button" disabled={busy} onClick={() => void run(row, "approve")}>Approve and book</button>
            <button className="button button-outline" type="button" disabled={busy} onClick={() => { setRejecting(rejecting === row.id ? null : row.id); setNote(""); }}>Reject</button>
          </span>}
          {rejecting === row.id && <span className="mj-inline">
            <input aria-label="Why it is rejected" value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} placeholder="Why it is rejected" />
            <button className="button button-dark" type="button" disabled={busy || !note.trim()} onClick={() => void run(row, "reject")}>Reject it</button>
          </span>}</td>
      </tr>)}</tbody>
    </table></div>
    {error && <div className="alert" role="alert">{error}</div>}
  </>;
}

export default function OldBalances({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [side, setSide] = useState<Side>("customer");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [recording, setRecording] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    try {
      setOverview(await call<Overview>(`/api/finance/old-balances/?unit=${selectedUnitId}&side=${side}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Old balances could not be loaded.");
    }
  }, [selectedUnitId, side]);
  useEffect(() => { void load(); }, [load]);
  const balance = overview?.balances.find((row) => row.id === recording) ?? null;
  const words = WORDS[side];

  return <section className="content-card supplier-payments bank-cash old-balances" aria-labelledby="old-balances-title">
    <header className="sp-head">
      <span className="eyebrow">FINANCE · OLD SYSTEM BALANCES · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="old-balances-title">Old system balances</h2>
      <p>Invoices and supplier bills that were still unpaid in the old system at go-live. They come in through Go-live imports, show in who owes us, who we owe and the customer statement, and are paid off here. A second person approves every payment.</p>
    </header>
    <div className="bc-filters">
      <label>Show<select value={side} onChange={(event) => { setSide(event.target.value as Side); setRecording(null); setNotice(""); }}>
        <option value="customer">What customers still owe us</option><option value="supplier">What we still owe suppliers</option>
      </select></label>
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading old balances…</p>}
    {overview && <>
      <ul className="bc-accounts ex-tiles" aria-label="Old balances">
        <li className="bc-total"><span>Still owed</span><strong>{fmt(overview.total_owed)}</strong><small>QAR on {overview.balances.length} {side === "customer" ? "invoices" : "bills"}</small></li>
        <li className="bc-total"><span>Waiting for approval</span><strong>{overview.waiting.length}</strong><small>{side === "customer" ? "receipts" : "payments"}</small></li>
      </ul>
      {balance && <Record key={balance.id} balance={balance} overview={overview} csrfToken={csrfToken} onCancel={() => setRecording(null)}
        onDone={(message) => { setRecording(null); setNotice(message); void load(); }} />}
      {overview.balances.length === 0
        ? <p className="sp-empty">Nothing is left to pay on old {side === "customer" ? "invoices" : "bills"}.</p>
        : <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label={side === "customer" ? "Unpaid old invoices" : "Unpaid old bills"}>
          <thead><tr><th scope="col">{side === "customer" ? "Invoice" : "Bill"}</th><th scope="col">{words.who}</th><th scope="col">Date</th>
            <th scope="col" className="bc-num">Days</th><th scope="col" className="bc-num">At go-live</th><th scope="col" className="bc-num">Still owed</th><th scope="col" /></tr></thead>
          <tbody>{overview.balances.map((row) => <tr key={row.id} className={recording === row.id ? "mj-open" : undefined}>
            <th scope="row">{show(row.reference)}</th>
            <td>{show(row.party.name)}{row.party.number && <small>{row.party.number.replace("TEST-SUP-", "")}</small>}</td>
            <td>{row.date}</td>
            <td className="pw-mono bc-num">{row.age_days}</td>
            <td className="pw-mono bc-num">{fmt(row.amount)} {row.currency}</td>
            <td className="pw-mono bc-num">{fmt(row.owed_in_currency)} {row.currency}{row.currency !== "QAR" && <small>{fmt(row.owed)} QAR</small>}{Number(row.waiting) > 0 && <small>{fmt(row.waiting)} waiting</small>}</td>
            <td>{overview.can_prepare && row.can_pay_more && <button className="button button-outline" type="button" onClick={() => { setNotice(""); setRecording(row.id); }}>{words.action}</button>}</td>
          </tr>)}</tbody>
        </table></div>}
      {overview.waiting.length > 0 && <Settlements rows={overview.waiting} label="Waiting for approval" overview={overview} csrfToken={csrfToken} onChanged={(message) => { setNotice(message); void load(); }} />}
      {overview.recent.length > 0 && <Settlements rows={overview.recent} label="Recently decided" overview={overview} csrfToken={csrfToken} onChanged={(message) => { setNotice(message); void load(); }} />}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
