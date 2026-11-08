import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Use = {
  id: number; receipt_id: number; status: "requested" | "approved" | "rejected"; status_label: string; currency: string; amount: string;
  paid_value: string; cleared_value: string | null; exchange_difference: string | null; paid_on: string; paid_by: string;
  decided_by: string | null; decision_note: string; can_decide: boolean;
};
type Advance = {
  id: number; number: string; supplier: { id: number; name: string }; paid_on: string; purpose: string; currency: string;
  amount: string; exchange_rate: string; rate_source: string; value: string; bank_reference: string;
  status: "requested" | "approved" | "rejected"; status_label: string; used: string; waiting: string; left: string;
  prepared_by: string; decided_by: string; decision_note: string; journal_entry_id: number | null; can_decide: boolean; uses: Use[];
};
type Goods = { id: number; supplier_id: number; supplier: string; bill_number: string; currency: string; exchange_rate: string; payable: string; remaining: string; received_on: string };
type Overview = {
  unit: { id: number; code: string; name: string }; advances: Advance[];
  totals: { held_value: string; open: number; waiting: number }; open_goods: Goods[];
  suppliers: { id: number; name: string }[]; currencies: string[];
  can_prepare: boolean; can_approve: boolean; currency: string; policy: string;
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
const fmt = (value: string | number) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const headers = (csrfToken: string, key?: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken, ...(key ? { "Idempotency-Key": key } : {}) });
const newKey = (what: string) => `${what}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
const STATUS_CLASS: Record<Advance["status"], string> = { requested: "ex-waiting", approved: "ex-booked", rejected: "ex-rejected" };

function Prepare({ overview, csrfToken, onDone, onCancel }: {
  overview: Overview; csrfToken: string; onDone: (advance: Advance, message: string) => void; onCancel: () => void;
}) {
  const [supplierId, setSupplierId] = useState("");
  const [currency, setCurrency] = useState(overview.currency);
  const [amount, setAmount] = useState("");
  const [rate, setRate] = useState("");
  const [rateSource, setRateSource] = useState("");
  const [paidOn, setPaidOn] = useState(today());
  const [purpose, setPurpose] = useState("");
  const [reference, setReference] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const foreign = currency !== overview.currency;
  const value = foreign && Number(amount) && Number(rate) ? Number(amount) * Number(rate) : null;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    const body = {
      supplier_id: Number(supplierId), currency, amount, paid_on: paidOn, purpose, bank_reference: reference,
      ...(foreign ? { exchange_rate: rate, rate_source: rateSource, rate_date: paidOn } : {}),
    };
    try {
      const advance = await call<Advance>(`/api/finance/advances/?unit=${overview.unit.id}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify(body) });
      onDone(advance, `${advance.number} is waiting for someone else to approve it. Nothing has left the bank yet.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The advance could not be saved.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form an-form" onSubmit={(event) => void submit(event)} aria-label="Pay an advance">
    <h3>Pay an advance</h3>
    <div className="sp-fields an-grid">
      <label className="an-wide">Supplier<select value={supplierId} onChange={(event) => setSupplierId(event.target.value)} required>
        <option value="">Choose…</option>
        {overview.suppliers.map((row) => <option key={row.id} value={row.id}>{show(row.name)}</option>)}
      </select></label>
      <label>Currency<select value={currency} onChange={(event) => setCurrency(event.target.value)}>
        {overview.currencies.map((code) => <option key={code} value={code}>{code}</option>)}
      </select></label>
      <label>Amount ({currency})<input inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} required /></label>
      <label>Paid on<input type="date" value={paidOn} max={today()} onChange={(event) => setPaidOn(event.target.value)} required /></label>
      {foreign && <label>QAR per 1 {currency}<input inputMode="decimal" value={rate} onChange={(event) => setRate(event.target.value)} required /></label>}
      {foreign && <label>Where the rate comes from<input value={rateSource} maxLength={120} onChange={(event) => setRateSource(event.target.value)} required placeholder="e.g. QNB transfer advice" /></label>}
      <label>Bank reference (optional)<input value={reference} maxLength={80} onChange={(event) => setReference(event.target.value)} /></label>
      <label className="an-wide">What it is for<input value={purpose} maxLength={200} onChange={(event) => setPurpose(event.target.value)} required placeholder="e.g. 30% deposit on order PO-118, proforma PF-2291" /></label>
    </div>
    {value !== null && <p className="sp-hint">Leaves the bank as {fmt(value)} QAR.</p>}
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy || !supplierId}>Send for approval</button>
      <button className="button button-quiet" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </form>;
}

function Detail({ advance, overview, csrfToken, onChanged, onClose }: {
  advance: Advance; overview: Overview; csrfToken: string; onChanged: (message: string) => void; onClose: () => void;
}) {
  const goods = overview.open_goods.filter((row) => row.supplier_id === advance.supplier.id && row.currency === advance.currency);
  const [receiptId, setReceiptId] = useState(goods[0] ? String(goods[0].id) : "");
  const receipt = goods.find((row) => String(row.id) === receiptId);
  const suggested = receipt ? Math.min(Number(advance.left), Number(receipt.remaining)) : 0;
  const [amount, setAmount] = useState(suggested ? suggested.toFixed(2) : "");
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (path: string, body: Record<string, unknown>, message: string, key?: string) => {
    setBusy(true); setError("");
    try {
      await call(path, { method: "POST", headers: headers(csrfToken, key), body: JSON.stringify(body) });
      onChanged(message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That could not be done.");
    } finally { setBusy(false); }
  };
  const base = `/api/finance/advances/${advance.id}/?unit=${overview.unit.id}`;
  const tooMuch = Boolean(receipt) && (Number(amount) > Number(advance.left) || Number(amount) > Number(receipt!.remaining));
  return <article className="sp-form an-detail" aria-label={`Advance ${advance.number}`}>
    <header className="mj-detail-head">
      <h3>{advance.number} <span className={`ex-status ${STATUS_CLASS[advance.status]}`}>{advance.status_label}</span></h3>
      <button className="button button-quiet" type="button" onClick={onClose}>Close</button>
    </header>
    <dl className="an-facts">
      <div><dt>Supplier</dt><dd>{show(advance.supplier.name)}</dd></div>
      <div><dt>Paid</dt><dd className="pw-mono">{fmt(advance.amount)} {advance.currency}{advance.currency !== overview.currency ? ` (${fmt(advance.value)} QAR at ${advance.exchange_rate})` : ""}</dd></div>
      <div><dt>Used</dt><dd className="pw-mono">{fmt(advance.used)}{Number(advance.waiting) > 0 ? ` + ${fmt(advance.waiting)} waiting` : ""}</dd></div>
      <div><dt>Left to use</dt><dd className="pw-mono">{fmt(advance.left)} {advance.currency}</dd></div>
      <div><dt>For</dt><dd>{show(advance.purpose)}</dd></div>
      <div><dt>Paid on</dt><dd>{advance.paid_on}{advance.bank_reference ? ` · ${advance.bank_reference}` : ""}</dd></div>
    </dl>
    <p className="sp-hint">Prepared by {advance.prepared_by}{advance.decided_by ? `; ${advance.status === "approved" ? "approved" : "rejected"} by ${advance.decided_by}` : ""}{advance.decision_note ? `: ${show(advance.decision_note)}` : ""}.</p>
    {advance.can_decide && <div className="sp-actions">
      <button className="button button-dark" type="button" disabled={busy} onClick={() => void run(base, { action: "approve" }, `${advance.number} is approved and paid.`)}>Approve and pay</button>
      <button className="button button-outline" type="button" disabled={busy} onClick={() => setRejecting(!rejecting)}>Reject</button>
    </div>}
    {rejecting && <div className="mj-inline">
      <input aria-label="Why it is rejected" value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder="Why it is rejected" />
      <button className="button button-dark" type="button" disabled={busy || !reason.trim()} onClick={() => void run(base, { action: "reject", reason }, `${advance.number} is rejected.`)}>Reject the advance</button>
    </div>}
    {advance.status === "approved" && Number(advance.left) > 0 && overview.can_prepare && <div className="sa-use">
      <h4>Set against the supplier's goods</h4>
      {goods.length === 0
        ? <p className="sp-hint">No goods from {show(advance.supplier.name)} in {advance.currency} have a recorded bill with something still to pay.</p>
        : <div className="sp-fields an-grid">
          <label className="an-wide">Goods received<select value={receiptId} onChange={(event) => setReceiptId(event.target.value)}>
            {goods.map((row) => <option key={row.id} value={row.id}>Receipt {row.id} · bill {show(row.bill_number)} · {fmt(row.remaining)} {row.currency} still to pay</option>)}
          </select></label>
          <label>Amount ({advance.currency})<input inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} /></label>
          <div className="sa-use-go"><button className="button button-dark" type="button" disabled={busy || !receipt || !Number(amount) || tooMuch}
            onClick={() => void run(base, { action: "use", receipt_id: Number(receiptId), amount }, `${fmt(amount)} ${advance.currency} of ${advance.number} is set against receipt ${receiptId}, waiting for someone else to approve it.`, newKey("advance-use"))}>Set against the goods</button></div>
        </div>}
      {receipt && advance.currency !== overview.currency && <p className="sp-hint">The goods clear at the receipt rate ({receipt.exchange_rate}) and the advance at its own rate ({advance.exchange_rate}); the gap is booked as an exchange gain or loss.</p>}
      {tooMuch && <p className="alert" role="alert">That is more than is left on the advance or still to pay on the goods.</p>}
    </div>}
    {advance.uses.length > 0 && <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label={`Uses of ${advance.number}`}>
      <thead><tr><th scope="col">Receipt</th><th scope="col">Date</th><th scope="col" className="bc-num">Amount</th><th scope="col" className="bc-num">Exchange difference</th><th scope="col">Status</th></tr></thead>
      <tbody>{advance.uses.map((use) => <tr key={use.id}>
        <th scope="row">Receipt {use.receipt_id}</th>
        <td>{use.paid_on}</td>
        <td className="pw-mono bc-num">{fmt(use.amount)} {use.currency}</td>
        <td className="pw-mono bc-num">{use.exchange_difference === null ? "" : Number(use.exchange_difference) === 0 ? "none" : `${Number(use.exchange_difference) > 0 ? "gain" : "loss"} ${fmt(Math.abs(Number(use.exchange_difference)))} QAR`}</td>
        <td><span className={`ex-status ${STATUS_CLASS[use.status]}`}>{use.status_label}</span>
          {use.can_decide && <span className="sa-use-actions">
            <button className="button button-dark" type="button" disabled={busy} onClick={() => void run(`/api/finance/supplier-payments/${use.id}/approve/?unit=${overview.unit.id}`, {}, `The use of ${advance.number} on receipt ${use.receipt_id} is approved.`, newKey("advance-use-approve"))}>Approve</button>
            <button className="button button-outline" type="button" disabled={busy} onClick={() => void run(`/api/finance/supplier-payments/${use.id}/reject/?unit=${overview.unit.id}`, { note: "Rejected from supplier advances" }, `The use of ${advance.number} on receipt ${use.receipt_id} is rejected.`)}>Reject</button>
          </span>}</td>
      </tr>)}</tbody>
    </table></div>}
    {error && <div className="alert" role="alert">{error}</div>}
  </article>;
}

export default function SupplierAdvances({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [status, setStatus] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [preparing, setPreparing] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    const query = new URLSearchParams({ unit: String(selectedUnitId), ...(status ? { status } : {}) });
    try {
      setOverview(await call<Overview>(`/api/finance/advances/?${query}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Supplier advances could not be loaded.");
    }
  }, [selectedUnitId, status]);
  useEffect(() => { void load(); }, [load]);
  const open = overview?.advances.find((row) => row.id === openId) ?? null;

  return <section className="content-card supplier-payments bank-cash supplier-advances" aria-labelledby="supplier-advances-title">
    <header className="sp-head">
      <span className="eyebrow">FINANCE · SUPPLIER ADVANCES · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="supplier-advances-title">Supplier advances</h2>
      <p>Money paid to a supplier before their bill, such as a deposit on an order. When the goods arrive and their bill is recorded, the advance is set against them, in full or in parts. A second person approves each advance and each use, just like any supplier payment.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading supplier advances…</p>}
    {overview && <>
      <ul className="bc-accounts ex-tiles" aria-label="Advances">
        <li className="bc-total"><span>Held by suppliers</span><strong>{fmt(overview.totals.held_value)}</strong><small>QAR not yet set against goods</small></li>
        <li className="bc-total"><span>Advances with money left</span><strong>{overview.totals.open}</strong><small>can be set against goods</small></li>
        <li className="bc-total"><span>Waiting for approval</span><strong>{overview.totals.waiting}</strong><small>advances and uses</small></li>
      </ul>
      <div className="bc-filters">
        <label>Show<select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All</option><option value="open">With money left</option><option value="requested">Waiting for approval</option>
          <option value="approved">Approved</option><option value="rejected">Rejected</option>
        </select></label>
        {overview.can_prepare && !preparing && <div className="bc-buttons">
          <button className="button button-dark" type="button" onClick={() => { setNotice(""); setOpenId(null); setPreparing(true); }}>Pay an advance</button>
        </div>}
      </div>
      {preparing && <Prepare overview={overview} csrfToken={csrfToken} onCancel={() => setPreparing(false)}
        onDone={(advance, message) => { setPreparing(false); setOpenId(advance.id); setNotice(message); void load(); }} />}
      {!preparing && open && <Detail key={`${open.id}-${open.left}-${open.uses.length}-${open.status}`} advance={open} overview={overview} csrfToken={csrfToken}
        onChanged={(message) => { setNotice(message); void load(); }} onClose={() => setOpenId(null)} />}
      {overview.advances.length === 0
        ? <p className="sp-empty">No advances{status ? " match" : " yet"}.</p>
        : <div className="sp-table-wrap"><table className="sp-table bc-table sa-table" aria-label="Supplier advances">
          <thead><tr><th scope="col">Advance</th><th scope="col">Supplier</th><th scope="col">Paid on</th><th scope="col" className="bc-num">Paid</th>
            <th scope="col" className="bc-num">Left to use</th><th scope="col">Status</th></tr></thead>
          <tbody>{overview.advances.map((advance) => <tr key={advance.id} className={openId === advance.id ? "mj-open" : undefined}>
            <th scope="row"><button className="fa-link" type="button" onClick={() => { setNotice(""); setPreparing(false); setOpenId(advance.id); }}>{advance.number}</button>
              <small>{show(advance.purpose)}</small></th>
            <td>{show(advance.supplier.name)}</td>
            <td>{advance.paid_on}</td>
            <td className="pw-mono bc-num">{fmt(advance.amount)} {advance.currency}</td>
            <td className="pw-mono bc-num">{advance.status === "approved" ? `${fmt(advance.left)} ${advance.currency}` : ""}{Number(advance.waiting) > 0 && <small>{fmt(advance.waiting)} waiting</small>}</td>
            <td><span className={`ex-status ${STATUS_CLASS[advance.status]}`}>{advance.status_label}</span>
              <small>{advance.decided_by ? `by ${advance.decided_by}` : `prepared by ${advance.prepared_by}`}{advance.can_decide || advance.uses.some((use) => use.can_decide) ? " · for you to approve" : ""}</small></td>
          </tr>)}</tbody>
        </table></div>}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
