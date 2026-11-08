import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./customer-refunds.css";

type Line = { quote_line_id: number; item_id: string; description: string; brand: string; quantity: string; condition: string; inspection_notes: string };
type ReviewLine = { quote_line_id: number; quantity: string; net_unit_price: string; value: string; cost: string; condition: string };
type CreditNote = {
  number: string; issued_at: string; issued_by: string; invoice_number: string; credit_total: string;
  applied_to_balance: string; refunded_amount: string; refund_method: string; returned_value: string; returned_cost: string; note: string;
  lines?: ReviewLine[];
};
type Return = {
  id: number; quote_id: number; status: string; request_date: string; recorded_by: string;
  customer: { id: number; name: string; qid: string }; location: { code: string; name: string };
  proposed_refund_amount: string; requested_refund_method: "cash" | "card"; original_payment_method: string; lines: Line[];
  review?: { invoice_number?: string; returned_value?: string; returned_cost?: string; outstanding_balance?: string; lines?: ReviewLine[]; problem: string; own_return: boolean };
  credit_note?: CreditNote; rejection?: { reason: string; at: string; by: string };
};
type Queue = { status: "pending" | "decided"; unit: { name: string }; waiting: number; returns: Return[]; policy_label: string };

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The request could not be completed.");
  return data as T;
}

const qar = (value: string | undefined) => Number(value ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const day = (value: string) => new Date(value).toLocaleDateString();
const showName = (name: string) => name.replace(/^TEST ONLY\s*/, "");
const key = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`);

function Decision({ item, busy, onApprove, onReject }: {
  item: Return; busy: boolean;
  onApprove: (credit: string, method: string, note: string) => void; onReject: (reason: string) => void;
}) {
  const review = item.review!;
  const value = review.returned_value ?? "0.00";
  const [credit, setCredit] = useState(Math.min(Number(item.proposed_refund_amount), Number(value)).toFixed(2));
  const [method, setMethod] = useState<string>(item.requested_refund_method);
  const [note, setNote] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const amount = Number(credit);
  const outstanding = Number(review.outstanding_balance ?? 0);
  const applied = Math.min(amount, outstanding);
  const lower = amount < Number(value);
  const invalid = !(amount > 0) || amount > Number(value) || (lower && note.trim().length < 5);
  const approve = (event: FormEvent) => { event.preventDefault(); onApprove(credit, method, note); };
  const reject = (event: FormEvent) => { event.preventDefault(); onReject(reason); };
  if (review.problem) return <p className="cr-problem" role="alert">{review.problem}</p>;
  if (review.own_return) return <p className="cr-hint">You recorded this return, so someone else in Finance must decide it.</p>;
  if (rejecting) return <form className="cr-form" onSubmit={reject} aria-label={`Reject return ${item.id}`}>
    <label>Why is the refund rejected?<input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={240} placeholder="For example: the item was used" /></label>
    <div className="cr-actions">
      <button className="button button-dark" type="submit" disabled={busy || reason.trim().length < 10}>Reject return</button>
      <button className="button button-outline" type="button" disabled={busy} onClick={() => setRejecting(false)}>Back</button>
    </div>
  </form>;
  return <form className="cr-form" onSubmit={approve} aria-label={`Approve return ${item.id}`}>
    <div className="cr-fields">
      <label>Credit amount (QAR)<input inputMode="decimal" value={credit} onChange={(event) => setCredit(event.target.value)} /></label>
      <label>Refund by<select value={method} onChange={(event) => setMethod(event.target.value)}>
        <option value="card">Card</option><option value="cash">Cash</option>
      </select></label>
      <label className="cr-note">Note{lower ? " (needed for a lower credit)" : ""}<input value={note} onChange={(event) => setNote(event.target.value)} maxLength={240} placeholder={lower ? "Why is the credit lower than the sale?" : "Optional"} /></label>
    </div>
    {amount > 0 && amount <= Number(value) && <p className="cr-split">
      {applied > 0 ? `${qar(String(applied))} reduces what the customer still owes on ${review.invoice_number}, and ${qar(String(amount - applied))} is refunded by ${method}.`
        : `${qar(credit)} is refunded by ${method}. The invoice is already paid.`}
    </p>}
    {amount > Number(value) && <p className="cr-problem">The credit cannot be more than the returned goods were sold for ({qar(value)} QAR).</p>}
    <div className="cr-actions">
      <button className="button button-dark" type="submit" disabled={busy || invalid}>Approve and issue credit note</button>
      <button className="button button-outline" type="button" disabled={busy} onClick={() => setRejecting(true)}>Reject…</button>
    </div>
  </form>;
}

function ReturnCard({ item, busy, onApprove, onReject }: {
  item: Return; busy: boolean; onApprove: (item: Return, credit: string, method: string, note: string) => void; onReject: (item: Return, reason: string) => void;
}) {
  const review = item.review;
  const values = new Map((review?.lines ?? item.credit_note?.lines ?? []).map((line) => [line.quote_line_id, line]));
  return <article className={`cr-card is-${item.status}`} aria-label={`Return ${item.id}`}>
    <header>
      <div><span className="eyebrow">RETURN {item.id} · SALE #{item.quote_id}</span>
        <h3>{showName(item.customer.name)}{item.customer.qid && <small>QID {item.customer.qid}</small>}</h3></div>
      <span className={`cr-status cr-${item.status}`}>{item.status === "finance_pending" ? "Waiting for Finance" : item.status === "refunded" ? "Refunded" : "Rejected"}</span>
    </header>
    <p className="cr-hint">Asked on {item.request_date}, received at {item.location.name} ({item.location.code}), recorded by {item.recorded_by}. Paid by {item.original_payment_method}; asks for a {item.requested_refund_method} refund of {qar(item.proposed_refund_amount)} QAR.</p>
    <table className="cr-table">
      <thead><tr><th scope="col">Item</th><th scope="col">Inspected</th><th scope="col" className="cr-num">Qty</th><th scope="col" className="cr-num">Sold for</th><th scope="col" className="cr-num">Original cost</th></tr></thead>
      <tbody>{item.lines.map((line) => {
        const valued = values.get(line.quote_line_id);
        return <tr key={line.quote_line_id}>
          <td>{line.description}<small>{line.brand} · {line.item_id}</small></td>
          <td><span className={`cr-condition ${line.condition === "good" ? "is-good" : "is-flagged"}`}>{line.condition}</span>{line.inspection_notes && <small>{line.inspection_notes}</small>}</td>
          <td className="cr-num">{Number(line.quantity)}</td>
          <td className="cr-num">{valued ? qar(valued.value) : "—"}</td>
          <td className="cr-num">{valued ? qar(valued.cost) : "—"}</td>
        </tr>;
      })}</tbody>
    </table>
    {item.status === "finance_pending" && review && <Decision item={item} busy={busy}
      onApprove={(credit, method, note) => onApprove(item, credit, method, note)} onReject={(reason) => onReject(item, reason)} />}
    {item.credit_note && <dl className="cr-note-facts">
      <div><dt>Credit note</dt><dd>{item.credit_note.number}</dd></div>
      <div><dt>Against invoice</dt><dd>{item.credit_note.invoice_number}</dd></div>
      <div><dt>Credit</dt><dd>{qar(item.credit_note.credit_total)}</dd></div>
      <div><dt>Refunded by {item.credit_note.refund_method}</dt><dd>{qar(item.credit_note.refunded_amount)}</dd></div>
      <div><dt>Off the balance</dt><dd>{qar(item.credit_note.applied_to_balance)}</dd></div>
      <div><dt>Goods back in stock at cost</dt><dd>{qar(item.credit_note.returned_cost)}</dd></div>
      {item.credit_note.note && <div className="cr-wide"><dt>Note</dt><dd>{item.credit_note.note}</dd></div>}
      <div className="cr-wide"><dt>Approved</dt><dd>{item.credit_note.issued_by}, {day(item.credit_note.issued_at)}</dd></div>
    </dl>}
    {item.rejection && <p className="cr-hint"><strong>Rejected by {item.rejection.by}, {day(item.rejection.at)}:</strong> {item.rejection.reason}</p>}
  </article>;
}

export default function CustomerRefunds({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [status, setStatus] = useState<"pending" | "decided">("pending");
  const [queue, setQueue] = useState<Queue | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const unit = selectedUnitId ? `unit=${selectedUnitId}` : "";
  const load = useCallback(() => {
    let live = true;
    call<Queue>(`/api/finance/refunds/?${unit}&status=${status}`)
      .then((data) => { if (live) { setQueue(data); setError(""); } })
      .catch((caught: Error) => { if (live) { setQueue(null); setError(caught.message); } });
    return () => { live = false; };
  }, [unit, status]);
  useEffect(load, [load]);
  const decide = async (item: Return, action: "approve" | "reject", body: Record<string, string>) => {
    setBusy(true);
    setNotice("");
    try {
      const result = await call<Return>(`/api/finance/refunds/${item.id}/${action}/?${unit}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken, "Idempotency-Key": key() },
        body: JSON.stringify(body),
      });
      setNotice(action === "approve"
        ? `Credit note ${result.credit_note?.number} issued for ${showName(item.customer.name)}. ${qar(result.credit_note?.refunded_amount)} QAR refunded and the goods are back in stock.`
        : `Return ${item.id} rejected. Nothing was posted.`);
      load();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return <section className="customer-refunds" aria-labelledby="cr-title">
    <div className="cr-head">
      <span className="eyebrow">FINANCE · TEST ONLY SYNTHETIC REFUNDS</span>
      <h2 id="cr-title">Customer refunds{queue ? ` · ${queue.unit.name}` : ""}</h2>
      <p>Returns recorded and inspected at the showroom or warehouse wait here for Finance. Approving issues a credit note against the sale's invoice. The credit first reduces anything the customer still owes, and the rest is refunded. The returned goods go back into stock at their original cost. Someone other than the person who recorded the return must decide.</p>
    </div>
    <div className="cr-tabs" role="group" aria-label="Refund list">
      <button type="button" aria-pressed={status === "pending"} onClick={() => setStatus("pending")}>Waiting for Finance{queue && status === "pending" ? ` (${queue.waiting})` : ""}</button>
      <button type="button" aria-pressed={status === "decided"} onClick={() => setStatus("decided")}>Decided</button>
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="cr-notice" role="status">{notice}</p>}
    {!queue && !error && <p className="cr-hint" role="status">Loading returns…</p>}
    {queue && queue.returns.length === 0 && <p className="cr-hint">{status === "pending" ? "No returns are waiting for Finance." : "No returns have been decided yet."}</p>}
    {queue?.returns.map((item) => <ReturnCard key={item.id} item={item} busy={busy}
      onApprove={(target, credit, method, note) => void decide(target, "approve", { credit_total: credit, refund_method: method, note })}
      onReject={(target, reason) => void decide(target, "reject", { reason })} />)}
    {queue && <p className="cr-hint">{queue.policy_label}. Amounts in QAR.</p>}
  </section>;
}
