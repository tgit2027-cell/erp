import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import "./supplier-payments.css";

type Supplier = { id: number; code: string; name: string };
type Item = {
  charge_id: number | null;
  paying_for: string;
  label: string;
  currency: string;
  exchange_rate: string;
  payable: string;
  paid: string;
  waiting: string;
  remaining: string;
  carried_value: string;
  bill: { id: number | null; number: string; status: string; label: string; date: string | null; payable: boolean } | null;
};
type OpenReceipt = {
  id: number;
  supplier: Supplier;
  received_on: string;
  currency: string;
  rate_date: string | null;
  items: Item[];
};
type Payment = {
  id: number;
  receipt_id: number;
  status: "requested" | "approved" | "rejected";
  status_label: string;
  supplier: Supplier;
  paying_for: string;
  paying_for_label: string;
  charge_id: number | null;
  payee_name: string;
  currency: string;
  amount: string;
  exchange_rate: string;
  rate_source: string;
  rate_date: string | null;
  paid_value: string;
  cleared_value: string | null;
  exchange_difference: string | null;
  paid_on: string;
  bank_reference: string;
  paid_by: string;
  decided_by: string | null;
  decision_note: string;
  journal_entry_id: number | null;
};
type Overview = {
  unit: { id: number; code: string; name: string };
  can_prepare: boolean;
  can_approve: boolean;
  me: string;
  open_receipts: OpenReceipt[];
  waiting: Payment[];
  payments: Payment[];
  totals: { exchange_gain: string; exchange_loss: string; net: string };
};
type Paying = { receipt: OpenReceipt; item: Item };
type Form = { payee_name: string; amount: string; exchange_rate: string; rate_source: string; rate_date: string; paid_on: string; bank_reference: string };

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
const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto
  ? crypto.randomUUID() : `supplier-payment-${Date.now()}-${Math.random().toString(36).slice(2)}`);
const qar = (value: number) => value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const round2 = (value: number) => Math.round((value + Number.EPSILON) * 100) / 100;

function difference(value: string) {
  const number = Number(value);
  if (number > 0) return <span className="sp-gain">Gain {qar(number)}</span>;
  if (number < 0) return <span className="sp-loss">Loss {qar(-number)}</span>;
  return <span className="sp-even">None</span>;
}

function PaymentForm({ paying: { receipt, item }, csrfToken, onDone, onCancel }: {
  paying: Paying; csrfToken: string; onDone: () => void; onCancel: () => void;
}) {
  const foreign = item.currency !== "QAR";
  const goods = item.charge_id === null;
  const [form, setForm] = useState<Form>({
    payee_name: goods ? receipt.supplier.name : "", amount: item.remaining, exchange_rate: foreign ? "" : "1",
    rate_source: "", rate_date: today(), paid_on: today(), bank_reference: "",
  });
  const [key] = useState(newKey);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (field: keyof Form) => (event: { target: { value: string } }) => setForm({ ...form, [field]: event.target.value });

  // Indicative only: the system calculates the final figures when the payment is approved.
  const preview = useMemo(() => {
    const amount = Number(form.amount);
    const rate = foreign ? Number(form.exchange_rate) : 1;
    if (!(amount > 0) || !(rate > 0)) return null;
    const paid = round2(amount * rate);
    const cleared = round2(amount * Number(item.exchange_rate));
    return { paid, cleared, difference: round2(cleared - paid) };
  }, [form.amount, form.exchange_rate, foreign, item]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await call("/api/finance/supplier-payments/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken, "Idempotency-Key": key },
        body: JSON.stringify({
          receipt_id: receipt.id, charge_id: item.charge_id, amount: form.amount, paid_on: form.paid_on,
          bank_reference: form.bank_reference, ...(goods ? {} : { payee_name: form.payee_name }),
          ...(foreign ? { exchange_rate: form.exchange_rate, rate_source: form.rate_source, rate_date: form.rate_date } : {}),
        }),
      });
      onDone();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The payment could not be prepared.");
    } finally { setBusy(false); }
  };

  return (
    <form className="sp-form" onSubmit={submit} aria-label={`Prepare payment for receipt ${receipt.id}`}>
      <h3>{goods ? `Pay ${receipt.supplier.name} for the goods` : `Pay the ${item.label.toLowerCase()}`} · receipt {receipt.id}</h3>
      <p className="sp-hint">
        Still to pay: <strong>{item.remaining} {item.currency}</strong>
        {foreign && <> · booked at {item.exchange_rate} QAR per {item.currency} on receipt</>}
        {Number(item.waiting) > 0 && <> · {item.waiting} {item.currency} already waiting for approval</>}
        <br />You can pay all of it or only part now; whatever is left stays to pay.
      </p>
      {error && <div className="alert" role="alert">{error}</div>}
      <div className="sp-fields">
        <label>Paid to{goods
          ? <input value={form.payee_name} readOnly aria-readonly="true" />
          : <input value={form.payee_name} onChange={set("payee_name")} maxLength={120} placeholder="e.g. freight forwarder or customs broker" required />}</label>
        <label>Amount ({item.currency})<input inputMode="decimal" value={form.amount} onChange={set("amount")} required /></label>
        {foreign && <>
          <label>Bank rate on payment day (QAR per {item.currency})<input inputMode="decimal" value={form.exchange_rate} onChange={set("exchange_rate")} required /></label>
          <label>Rate source<input value={form.rate_source} onChange={set("rate_source")} maxLength={120} placeholder="e.g. bank transfer advice" required /></label>
          <label>Rate date<input type="date" value={form.rate_date} onChange={set("rate_date")} required /></label>
        </>}
        <label>Payment date<input type="date" value={form.paid_on} onChange={set("paid_on")} required /></label>
        <label>Bank reference<input value={form.bank_reference} onChange={set("bank_reference")} maxLength={80} /></label>
      </div>
      {preview && <dl className="sp-preview" aria-live="polite">
        <div><dt>From bank</dt><dd>{qar(preview.paid)} QAR</dd></div>
        <div><dt>Cleared at the receipt rate</dt><dd>{qar(preview.cleared)} QAR</dd></div>
        <div><dt>Exchange result</dt><dd>{difference(String(preview.difference))}</dd></div>
      </dl>}
      <p className="sp-hint">Nothing is paid or posted yet. A second person with approval rights checks the payment and approves it; only then is the journal posted.</p>
      <div className="sp-actions">
        <button className="button button-dark" type="submit" disabled={busy}>Send for approval</button>
        <button className="button button-outline" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}

function Decision({ payment, overview, csrfToken, onDone }: {
  payment: Payment; overview: Overview; csrfToken: string; onDone: (message: string) => void;
}) {
  const [key] = useState(newKey);
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!overview.can_approve) return <span className="sp-muted">Waiting for an approver</span>;
  if (payment.paid_by === overview.me) return <span className="sp-muted">You prepared this; someone else approves</span>;
  const send = async (action: "approve" | "reject") => {
    setBusy(true); setError("");
    try {
      await call(`/api/finance/supplier-payments/${payment.id}/${action}/?unit=${overview.unit.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken, ...(action === "approve" ? { "Idempotency-Key": key } : {}) },
        body: JSON.stringify(action === "reject" ? { note } : {}),
      });
      onDone(action === "approve" ? `Payment to ${payment.payee_name} approved and posted.` : `Payment to ${payment.payee_name} rejected.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The decision could not be saved.");
    } finally { setBusy(false); }
  };
  return <div className="sp-decision">
    {error && <div className="alert" role="alert">{error}</div>}
    {rejecting
      ? <><label>Reason<input value={note} onChange={(event) => setNote(event.target.value)} maxLength={240} required /></label>
        <button className="button button-outline" type="button" disabled={busy || !note.trim()} onClick={() => void send("reject")}>Reject</button>
        <button className="button button-outline" type="button" disabled={busy} onClick={() => setRejecting(false)}>Back</button></>
      : <><button className="button button-dark" type="button" disabled={busy} onClick={() => void send("approve")}>Approve and pay</button>
        <button className="button button-outline" type="button" disabled={busy} onClick={() => setRejecting(true)}>Reject…</button></>}
  </div>;
}

export default function SupplierPayments({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [paying, setPaying] = useState<Paying | null>(null);
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    try {
      setOverview(await call<Overview>(`/api/finance/supplier-payments/?unit=${selectedUnitId}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Supplier payments could not be loaded.");
    }
  }, [selectedUnitId]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); void load(); };

  return (
    <section className="content-card supplier-payments" aria-labelledby="supplier-payments-title">
      <header className="sp-head">
        <span className="eyebrow">FINANCE · SUPPLIER PAYMENTS{overview ? ` · ${overview.unit.name}` : ""}</span>
        <h2 id="supplier-payments-title">Supplier payments</h2>
        <p>Pay the supplier for the goods once their bill is recorded and matches the receipt, and pay shipping, clearance and other charges to whoever billed them. One person prepares a payment and a different person approves it; the journal is posted only on approval. For foreign currencies, enter the bank rate on the payment day: the system books the realized exchange gain or loss against the rate the receipt was booked at.</p>
      </header>
      {error && <div className="alert" role="alert">{error}</div>}
      {notice && <p className="sp-notice" role="status">{notice}</p>}
      {!overview && !error && <p className="sp-empty" role="status">Loading receipts and payments…</p>}
      {overview && paying && <PaymentForm paying={paying} csrfToken={csrfToken} onCancel={() => setPaying(null)}
        onDone={() => { setPaying(null); done(`Payment for receipt ${paying.receipt.id} sent for approval.`); }} />}
      {overview && !paying && <>
        <dl className="sp-totals">
          <div><dt>Realized exchange gain</dt><dd>{overview.totals.exchange_gain} QAR</dd></div>
          <div><dt>Realized exchange loss</dt><dd>{overview.totals.exchange_loss} QAR</dd></div>
          <div><dt>Net</dt><dd>{difference(overview.totals.net)}</dd></div>
        </dl>
        <h3>Waiting for approval <small>{overview.waiting.length}</small></h3>
        {overview.waiting.length === 0 ? <p className="sp-empty">No payments are waiting for approval.</p> : <div className="sp-table-wrap"><table className="sp-table sp-waiting">
          <thead><tr><th scope="col">Payment date</th><th scope="col">Paid to · for</th><th scope="col">Amount</th><th scope="col">From bank (QAR)</th><th scope="col">Prepared by</th><th scope="col">Decision</th></tr></thead>
          <tbody>{overview.waiting.map((payment) => <tr key={payment.id}>
            <th scope="row">{payment.paid_on}{payment.bank_reference && <small>{payment.bank_reference}</small>}</th>
            <td>{payment.payee_name}<small>{payment.paying_for_label} · receipt #{payment.receipt_id}</small></td>
            <td className="pw-mono">{payment.amount} {payment.currency}{payment.currency !== "QAR" && <small>at {payment.exchange_rate}</small>}</td>
            <td className="pw-mono">{payment.paid_value}</td>
            <td>{payment.paid_by}</td>
            <td><Decision payment={payment} overview={overview} csrfToken={csrfToken} onDone={done} /></td>
          </tr>)}</tbody>
        </table></div>}
        <h3>Receipts still to pay</h3>
        {overview.open_receipts.length === 0 ? <p className="sp-empty">Every receipt in this business unit is paid.</p> : <div className="sp-table-wrap"><table className="sp-table">
          <thead><tr><th scope="col">Receipt</th><th scope="col">Item</th><th scope="col">Currency</th><th scope="col">Receipt rate</th><th scope="col">Owed</th><th scope="col">Paid</th><th scope="col">Waiting</th><th scope="col">Still to pay</th><th scope="col"><span className="sr-only">Action</span></th></tr></thead>
          {overview.open_receipts.map((receipt) => <tbody key={receipt.id}>{receipt.items.map((item, index) => <tr key={item.charge_id ?? "goods"} className={index === 0 ? "sp-first" : ""}>
            {index === 0 && <th scope="rowgroup" rowSpan={receipt.items.length}>#{receipt.id}<small>{receipt.supplier.name} · {receipt.received_on}</small></th>}
            <td>{item.label}{item.bill && <span className={`sp-bill${item.bill.payable ? "" : " sp-bill-missing"}`}>{item.bill.number ? `Bill ${item.bill.number} · ` : ""}{item.bill.label}</span>}</td>
            <td>{item.currency}</td>
            <td className="pw-mono">{item.currency === "QAR" ? "—" : item.exchange_rate}</td>
            <td className="pw-mono">{item.payable}</td>
            <td className="pw-mono">{item.paid}</td>
            <td className="pw-mono">{Number(item.waiting) > 0 ? item.waiting : "—"}</td>
            <td className="pw-mono"><strong>{item.remaining}</strong></td>
            <td>{overview.can_prepare && Number(item.remaining) > 0 && item.bill && !item.bill.payable && <a className="button button-outline" href="#/finance/supplier-bills">{item.bill.status === "missing" ? "Record bill" : "See bill"}</a>}
              {overview.can_prepare && Number(item.remaining) > 0 && (!item.bill || item.bill.payable) && <button className="button button-outline" type="button" onClick={() => { setNotice(""); setPaying({ receipt, item }); }}>Prepare payment</button>}</td>
          </tr>)}</tbody>)}
        </table></div>}
        <h3>Recent payments</h3>
        {overview.payments.length === 0 ? <p className="sp-empty">No payments approved or rejected yet.</p> : <div className="sp-table-wrap"><table className="sp-table">
          <thead><tr><th scope="col">Date</th><th scope="col">Paid to · for</th><th scope="col">Status</th><th scope="col">Amount</th><th scope="col">Paid (QAR)</th><th scope="col">Cleared (QAR)</th><th scope="col">Exchange result</th><th scope="col">Prepared · decided</th></tr></thead>
          <tbody>{overview.payments.map((payment) => <tr key={payment.id}>
            <th scope="row">{payment.paid_on}{payment.bank_reference && <small>{payment.bank_reference}</small>}</th>
            <td>{payment.payee_name}<small>{payment.paying_for_label} · receipt #{payment.receipt_id}{payment.journal_entry_id && ` · journal #${payment.journal_entry_id}`}</small></td>
            <td><span className={`sp-status sp-status-${payment.status}`}>{payment.status_label}</span>{payment.decision_note && <small>{payment.decision_note}</small>}</td>
            <td className="pw-mono">{payment.amount} {payment.currency}{payment.currency !== "QAR" && <small>at {payment.exchange_rate}</small>}</td>
            <td className="pw-mono">{payment.paid_value}</td>
            <td className="pw-mono">{payment.cleared_value ?? "—"}</td>
            <td>{payment.exchange_difference === null ? "—" : difference(payment.exchange_difference)}</td>
            <td>{payment.paid_by}<small>{payment.decided_by}</small></td>
          </tr>)}</tbody>
        </table></div>}
      </>}
    </section>
  );
}
