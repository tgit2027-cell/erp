import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";

type Supplier = { id: number; code: string; name: string };
type Line = {
  sku: string; description: string; received_quantity: string; unit_cost: string; value: string;
  ordered_quantity: string | null; ordered_unit_cost: string | null; ordered_currency: string | null;
};
type Receipt = { id: number; supplier: Supplier; received_on: string; currency: string; expected_amount: string; lines: Line[] };
type Bill = {
  id: number; receipt_id: number; supplier: Supplier; bill_number: string; bill_date: string; currency: string;
  amount: string; expected_amount: string; difference: string; status: "matched" | "on_hold" | "accepted" | "rejected" | "voided";
  status_label: string; note: string; recorded_by: string; decided_by: string | null; decision_note: string;
  difference_value: string | null; journal_entry_id: number | null; received_on: string; can_void: boolean; lines?: Line[];
  settlement: "" | "unpaid" | "waiting" | "part_paid" | "paid"; settlement_label: string; paid: string | null; waiting: string | null; outstanding: string | null;
};
type Overview = {
  unit: { id: number; code: string; name: string }; can_record: boolean; can_decide: boolean; me: string;
  needing_bill: Receipt[]; waiting: Bill[]; bills: Bill[]; policy_label: string;
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
const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto
  ? crypto.randomUUID() : `supplier-bill-${Date.now()}-${Math.random().toString(36).slice(2)}`);
const fmt = (value: string | number) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const showName = (name: string) => name.replace(/^TEST ONLY\s*/, "");

function Difference({ value, currency }: { value: string; currency: string }) {
  const number = Number(value);
  if (number === 0) return <span className="sp-even">Matches</span>;
  return <span className={number > 0 ? "sp-loss" : "sp-gain"}>{number > 0 ? "Bill higher by" : "Bill lower by"} {fmt(Math.abs(number))} {currency}</span>;
}

function Received({ lines, currency }: { lines: Line[]; currency: string }) {
  return <ul className="sb-lines">{lines.map((line, index) => <li key={index}>
    {fmt(line.received_quantity)} × {line.sku} at {fmt(line.unit_cost)} {currency}
    {line.ordered_quantity !== null && <span> · ordered {fmt(line.ordered_quantity)} at {fmt(line.ordered_unit_cost ?? "0")} {line.ordered_currency}</span>}
  </li>)}</ul>;
}

function BillForm({ receipt, csrfToken, unitId, onDone, onCancel }: {
  receipt: Receipt; csrfToken: string; unitId: number; onDone: (message: string) => void; onCancel: () => void;
}) {
  const [form, setForm] = useState({ bill_number: "", bill_date: today(), amount: "", note: "" });
  const [key] = useState(newKey);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (field: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [field]: event.target.value });
  const amount = Number(form.amount);
  const gap = form.amount.trim() && amount > 0 ? Math.round((amount - Number(receipt.expected_amount)) * 100) / 100 : null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const bill = await call<Bill>(`/api/finance/supplier-bills/?unit=${unitId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken, "Idempotency-Key": key },
        body: JSON.stringify({ receipt_id: receipt.id, ...form }),
      });
      onDone(bill.status === "matched"
        ? `Bill ${bill.bill_number} matches receipt ${receipt.id}. The goods can now be paid.`
        : `Bill ${bill.bill_number} differs from receipt ${receipt.id}. It is waiting for a second person to accept or reject the difference.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The bill could not be recorded.");
    } finally { setBusy(false); }
  };

  return <form className="sp-form" onSubmit={submit} aria-label={`Record bill for receipt ${receipt.id}`}>
    <h3>Record the bill from {showName(receipt.supplier.name)} · receipt {receipt.id}</h3>
    <p className="sp-hint">Goods received on {receipt.received_on}, booked at <strong>{fmt(receipt.expected_amount)} {receipt.currency}</strong>. Enter the bill exactly as the supplier issued it.</p>
    <Received lines={receipt.lines} currency={receipt.currency} />
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-fields">
      <label>Supplier's bill number<input value={form.bill_number} onChange={set("bill_number")} maxLength={40} required /></label>
      <label>Bill date<input type="date" value={form.bill_date} onChange={set("bill_date")} required /></label>
      <label>Bill amount ({receipt.currency})<input inputMode="decimal" value={form.amount} onChange={set("amount")} required /></label>
      <label>Note (optional)<input value={form.note} onChange={set("note")} maxLength={240} /></label>
    </div>
    {gap !== null && <p className="sb-check" aria-live="polite">
      {gap === 0
        ? <><span className="sp-even">Matches</span> The goods can be paid as soon as the bill is saved.</>
        : <><Difference value={String(gap)} currency={receipt.currency} /> A second person in Finance must accept this difference before the goods can be paid.</>}
    </p>}
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy}>Record bill</button>
      <button className="button button-outline" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
    </div>
  </form>;
}

function Action({ bill, overview, csrfToken, onDone }: { bill: Bill; overview: Overview; csrfToken: string; onDone: (message: string) => void }) {
  const [key] = useState(newKey);
  const [asking, setAsking] = useState<"reject" | "void" | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const held = bill.status === "on_hold";
  const decide = held && overview.can_decide && bill.recorded_by !== overview.me;
  const canVoid = bill.can_void && (overview.can_record || overview.can_decide);
  const send = async (action: "accept" | "reject" | "void") => {
    setBusy(true); setError("");
    try {
      await call(`/api/finance/supplier-bills/${bill.id}/${action}/?unit=${overview.unit.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken, ...(action === "accept" ? { "Idempotency-Key": key } : {}) },
        body: JSON.stringify({ note }),
      });
      onDone({
        accept: `Difference on bill ${bill.bill_number} accepted and posted. The goods can now be paid at the bill amount.`,
        reject: `Bill ${bill.bill_number} rejected. Record the corrected bill when the supplier sends it.`,
        void: `Bill ${bill.bill_number} voided. Record the right bill for receipt ${bill.receipt_id}.`,
      }[action]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The decision could not be saved.");
    } finally { setBusy(false); }
  };
  if (!decide && !canVoid) return held ? <span className="sp-muted">Waiting for a second person</span> : <span className="sp-muted">—</span>;
  return <div className="sp-decision">
    {error && <div className="alert" role="alert">{error}</div>}
    {asking
      ? <><label>Reason<input value={note} onChange={(event) => setNote(event.target.value)} maxLength={240} required /></label>
        <button className="button button-outline" type="button" disabled={busy || !note.trim()} onClick={() => void send(asking)}>{asking === "reject" ? "Reject" : "Void"}</button>
        <button className="button button-outline" type="button" disabled={busy} onClick={() => setAsking(null)}>Back</button></>
      : <>
        {decide && <button className="button button-dark" type="button" disabled={busy} onClick={() => void send("accept")}>Accept difference</button>}
        {decide && <button className="button button-outline" type="button" disabled={busy} onClick={() => setAsking("reject")}>Reject…</button>}
        {held && !decide && <span className="sp-muted">{bill.recorded_by === overview.me ? "You recorded this; someone else decides" : "Waiting for a second person"}</span>}
        {canVoid && !decide && <button className="button button-outline" type="button" disabled={busy} onClick={() => setAsking("void")}>Void…</button>}
      </>}
  </div>;
}

export default function SupplierBills({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [recording, setRecording] = useState<Receipt | null>(null);
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    try {
      setOverview(await call<Overview>(`/api/finance/supplier-bills/?unit=${selectedUnitId}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Supplier bills could not be loaded.");
    }
  }, [selectedUnitId]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setRecording(null); setNotice(message); void load(); };

  return <section className="content-card supplier-payments supplier-bills" aria-labelledby="supplier-bills-title">
    <header className="sp-head">
      <span className="eyebrow">FINANCE · SUPPLIER BILLS · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="supplier-bills-title">Supplier bills</h2>
      <p>Record each supplier's bill against the goods received. A bill that matches the receipt to the cent can be paid straight away. Any difference waits until a second person in Finance, who did not record the bill, accepts or rejects it.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading supplier bills…</p>}
    {overview && recording && <BillForm receipt={recording} csrfToken={csrfToken} unitId={overview.unit.id}
      onDone={done} onCancel={() => setRecording(null)} />}
    {overview && !recording && <>
      <h3>Differences waiting for a second person <small>{overview.waiting.length}</small></h3>
      {overview.waiting.length === 0 ? <p className="sp-empty">No bill differences are waiting.</p> : <div className="sp-table-wrap"><table className="sp-table sb-waiting">
        <thead><tr><th scope="col">Bill</th><th scope="col">Supplier · goods received</th><th scope="col">Bill amount</th><th scope="col">Receipt amount</th><th scope="col">Difference</th><th scope="col">Recorded by</th><th scope="col">Decision</th></tr></thead>
        <tbody>{overview.waiting.map((bill) => <tr key={bill.id}>
          <th scope="row">{bill.bill_number}<small>{bill.bill_date}</small></th>
          <td>{showName(bill.supplier.name)}<small>Receipt #{bill.receipt_id} · {bill.received_on}</small>{bill.lines && <Received lines={bill.lines} currency={bill.currency} />}{bill.note && <small>Note: {bill.note}</small>}</td>
          <td className="pw-mono">{fmt(bill.amount)} {bill.currency}</td>
          <td className="pw-mono">{fmt(bill.expected_amount)} {bill.currency}</td>
          <td><Difference value={bill.difference} currency={bill.currency} /></td>
          <td>{bill.recorded_by}</td>
          <td><Action bill={bill} overview={overview} csrfToken={csrfToken} onDone={done} /></td>
        </tr>)}</tbody>
      </table></div>}
      <h3>Goods waiting for the supplier's bill <small>{overview.needing_bill.length}</small></h3>
      {overview.needing_bill.length === 0 ? <p className="sp-empty">Every receipt with goods still to pay has a bill.</p> : <div className="sp-table-wrap"><table className="sp-table sb-needing">
        <thead><tr><th scope="col">Receipt</th><th scope="col">Goods received</th><th scope="col">Booked</th><th scope="col"><span className="sr-only">Action</span></th></tr></thead>
        <tbody>{overview.needing_bill.map((receipt) => <tr key={receipt.id}>
          <th scope="row">#{receipt.id}<small>{showName(receipt.supplier.name)} · {receipt.received_on}</small></th>
          <td><Received lines={receipt.lines} currency={receipt.currency} /></td>
          <td className="pw-mono"><strong>{fmt(receipt.expected_amount)} {receipt.currency}</strong></td>
          <td>{overview.can_record
            ? <button className="button button-outline" type="button" onClick={() => { setNotice(""); setRecording(receipt); }}>Record bill</button>
            : <span className="sp-muted">Recorded by supplier-payment staff</span>}</td>
        </tr>)}</tbody>
      </table></div>}
      <h3>Recorded bills</h3>
      {overview.bills.length === 0 ? <p className="sp-empty">No bills recorded yet.</p> : <div className="sp-table-wrap"><table className="sp-table sb-bills">
        <thead><tr><th scope="col">Bill</th><th scope="col">Supplier · receipt</th><th scope="col">Status</th><th scope="col">Bill amount</th><th scope="col">Difference</th><th scope="col">Paid · still to pay</th><th scope="col">Recorded · decided</th><th scope="col"><span className="sr-only">Action</span></th></tr></thead>
        <tbody>{overview.bills.map((bill) => <tr key={bill.id}>
          <th scope="row">{bill.bill_number}<small>{bill.bill_date}</small></th>
          <td>{showName(bill.supplier.name)}<small>Receipt #{bill.receipt_id}{bill.journal_entry_id && ` · journal #${bill.journal_entry_id}`}</small></td>
          <td><span className={`sp-status sb-status-${bill.status}`}>{bill.status_label}</span>{bill.decision_note && <small>{bill.decision_note}</small>}</td>
          <td className="pw-mono">{fmt(bill.amount)} {bill.currency}</td>
          <td>{Number(bill.difference) === 0 ? <span className="sp-even">Matches</span> : <Difference value={bill.difference} currency={bill.currency} />}{bill.difference_value && Number(bill.difference_value) !== 0 && <small>{fmt(bill.difference_value)} QAR posted</small>}</td>
          <td>{bill.settlement && <><span className={`sp-status sb-settle-${bill.settlement}`}>{bill.settlement_label}</span>
            <small>Paid {fmt(bill.paid!)}</small>{Number(bill.waiting) > 0 && <small>Waiting approval {fmt(bill.waiting!)}</small>}
            <small>Still to pay {fmt(bill.outstanding!)} {bill.currency}</small></>}</td>
          <td>{bill.recorded_by}<small>{bill.decided_by}</small></td>
          <td>{bill.status === "matched" && <Action bill={bill} overview={overview} csrfToken={csrfToken} onDone={done} />}
            {overview.can_record && Number(bill.outstanding) > 0 && <a className="button button-outline sb-pay" href="#/finance/supplier-payments">Pay all or part</a>}</td>
        </tr>)}</tbody>
      </table></div>}
      <p className="sp-hint">{overview.policy_label}</p>
    </>}
  </section>;
}
