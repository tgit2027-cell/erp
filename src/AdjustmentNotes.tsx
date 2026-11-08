import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Side = "customer" | "supplier";
type Note = {
  id: number; kind: string; kind_label: string; side: Side; lowers: boolean; effect: string; number: string; their_reference: string;
  date: string; counterparty: string; document: string; invoice_id: number | null; receipt_id: number | null;
  reason: string; reason_label: string; description: string; currency: string; amount: string; value: string;
  status: "submitted" | "approved" | "rejected"; status_label: string; prepared_by: string; prepared_at: string;
  decided_by: string; decided_at: string; decision_note: string; journal_entry_id: number | null; can_decide: boolean;
  document_now?: { total: string; owed: string; currency: string };
};
type InvoiceChoice = { id: number; number: string; customer: string; date: string; total: string; owed: string; waiting: string; room: string };
type ReceiptChoice = {
  id: number; supplier: string; date: string; bill_number: string; currency: string; rate: string; payable: string;
  owed: string; waiting: string; room: string;
};
type Overview = {
  unit: { id: number; code: string; name: string }; side: Side; notes: Note[];
  waiting: { customer: number; supplier: number; for_me: number };
  invoices: InvoiceChoice[]; receipts: ReceiptChoice[];
  kinds: { value: string; label: string }[]; reasons: { value: string; label: string }[];
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
const json = (csrfToken: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken });
const STATUS_CLASS: Record<Note["status"], string> = { submitted: "ex-waiting", approved: "ex-booked", rejected: "ex-rejected" };
const SUPPLIER_KINDS: [string, string, string][] = [
  ["supplier_credit", "The supplier's credit note", "lowers what we owe"],
  ["our_debit", "Our debit note to the supplier", "lowers what we owe; we give it a number"],
  ["supplier_debit", "The supplier's debit note", "raises what we owe"],
];

function Prepare({ overview, csrfToken, onDone, onCancel }: {
  overview: Overview; csrfToken: string; onDone: (note: Note, message: string) => void; onCancel: () => void;
}) {
  const customer = overview.side === "customer";
  const [kind, setKind] = useState(customer ? "customer_credit" : "supplier_credit");
  const [documentId, setDocumentId] = useState("");
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [reason, setReason] = useState("price");
  const [description, setDescription] = useState("");
  const [date, setDate] = useState(today());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const invoice = overview.invoices.find((row) => String(row.id) === documentId);
  const receipt = overview.receipts.find((row) => String(row.id) === documentId);
  const currency = customer ? overview.currency : receipt?.currency ?? "";
  const room = customer ? invoice?.room : kind === "supplier_debit" ? undefined : receipt?.room;
  const tooMuch = room !== undefined && Number(amount) > Number(room);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    const body = {
      kind, amount, reason, description, date, their_reference: reference,
      ...(customer ? { invoice_id: Number(documentId) } : { receipt_id: Number(documentId) }),
    };
    try {
      const note = await call<Note>(`/api/finance/notes/?unit=${overview.unit.id}`, { method: "POST", headers: json(csrfToken), body: JSON.stringify(body) });
      onDone(note, `The note on ${note.document} is waiting for someone else to approve it.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The note could not be saved.");
    } finally { setBusy(false); }
  };
  const choices = customer ? overview.invoices : overview.receipts;
  return <form className="sp-form an-form" onSubmit={(event) => void submit(event)} aria-label={customer ? "Prepare a customer credit note" : "Record a supplier note"}>
    <h3>{customer ? "Prepare a customer credit note" : "Record a supplier note"}</h3>
    {!customer && <fieldset className="an-kinds"><legend>Which note</legend>
      {SUPPLIER_KINDS.map(([value, label, hint]) => <label key={value} className="bc-check">
        <input type="radio" name="an-kind" value={value} checked={kind === value} onChange={() => setKind(value)} />
        <span>{label}<small>{hint}</small></span>
      </label>)}
    </fieldset>}
    <div className="sp-fields an-grid">
      <label className="an-wide">{customer ? "Invoice" : "Goods received (with the supplier's bill)"}
        <select value={documentId} onChange={(event) => setDocumentId(event.target.value)} required>
          <option value="">{choices.length ? "Choose…" : customer ? "No invoice has anything owed on it" : "No receipt has a recorded bill"}</option>
          {customer
            ? overview.invoices.map((row) => <option key={row.id} value={row.id}>{row.number} · {show(row.customer)} · {fmt(row.room)} QAR can be credited</option>)
            : overview.receipts.map((row) => <option key={row.id} value={row.id}>Receipt {row.id} · {show(row.supplier)} · bill {show(row.bill_number)} · {fmt(row.owed)} {row.currency} unpaid</option>)}
        </select></label>
      {!customer && kind !== "our_debit" && <label>Supplier's note number<input value={reference} maxLength={40} onChange={(event) => setReference(event.target.value)} required /></label>}
      <label>Amount{currency ? ` (${currency})` : ""}<input inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} required /></label>
      <label>Date<input type="date" value={date} max={today()} onChange={(event) => setDate(event.target.value)} required /></label>
      <label>Reason<select value={reason} onChange={(event) => setReason(event.target.value)}>
        {overview.reasons.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
      </select></label>
      <label className="an-wide">What happened<input value={description} maxLength={200} onChange={(event) => setDescription(event.target.value)} required
        placeholder={customer ? "e.g. 5% agreed after a delivery delay" : "e.g. two chairs arrived scratched; supplier agreed USD 120 off"} /></label>
    </div>
    {invoice && <p className="sp-hint">{invoice.number}: total {fmt(invoice.total)}, still owed {fmt(invoice.owed)}{Number(invoice.waiting) > 0 ? `, ${fmt(invoice.waiting)} already in notes waiting for approval` : ""}. A credit note cannot be more than what is still owed; for a paid invoice use returns and refunds.</p>}
    {receipt && <p className="sp-hint">Receipt {receipt.id}: bill {fmt(receipt.payable)} {receipt.currency}, unpaid {fmt(receipt.owed)} {receipt.currency}{Number(receipt.waiting) > 0 ? `, ${fmt(receipt.waiting)} already in notes waiting for approval` : ""}.{receipt.currency !== overview.currency ? ` Counted in QAR at the receipt rate of ${receipt.rate}.` : ""}</p>}
    {tooMuch && <p className="alert" role="alert">That is more than the {fmt(room!)} {currency} that can be {customer ? "credited" : "lowered"} on this {customer ? "invoice" : "receipt"}.</p>}
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy || !documentId || tooMuch}>Send for approval</button>
      <button className="button button-quiet" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </form>;
}

function Detail({ note, overview, csrfToken, onChanged, onClose }: {
  note: Note; overview: Overview; csrfToken: string; onChanged: (note: Note, message: string) => void; onClose: () => void;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const act = async (body: Record<string, string>, message: (result: Note) => string) => {
    setBusy(true); setError("");
    try {
      const result = await call<Note>(`/api/finance/notes/${note.id}/?unit=${overview.unit.id}`, { method: "POST", headers: json(csrfToken), body: JSON.stringify(body) });
      onChanged(result, message(result));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That could not be done.");
    } finally { setBusy(false); }
  };
  const title = note.number || (note.their_reference ? `${note.kind_label} ${note.their_reference}` : note.kind_label);
  return <article className="sp-form an-detail" aria-label={`Note ${title}`}>
    <header className="mj-detail-head">
      <h3>{title} <span className={`ex-status ${STATUS_CLASS[note.status]}`}>{note.status_label}</span></h3>
      <button className="button button-quiet" type="button" onClick={onClose}>Close</button>
    </header>
    <dl className="an-facts">
      <div><dt>{note.side === "customer" ? "Customer" : "Supplier"}</dt><dd>{show(note.counterparty)}</dd></div>
      <div><dt>On</dt><dd>{show(note.document)}</dd></div>
      <div><dt>Amount</dt><dd className="pw-mono">{fmt(note.amount)} {note.currency}{note.currency !== overview.currency ? ` (${fmt(note.value)} QAR)` : ""}</dd></div>
      <div><dt>Effect</dt><dd>{note.effect}</dd></div>
      <div><dt>Reason</dt><dd>{note.reason_label}: {show(note.description)}</dd></div>
      <div><dt>Dated</dt><dd>{note.date}</dd></div>
      {note.document_now && <div><dt>Still owed now</dt><dd className="pw-mono">{fmt(note.document_now.owed)} {note.document_now.currency} of {fmt(note.document_now.total)}</dd></div>}
    </dl>
    <p className="sp-hint">Prepared by {note.prepared_by}{note.decided_by ? `; ${note.status === "approved" ? "approved" : "rejected"} by ${note.decided_by}` : ""}{note.decision_note ? `: ${show(note.decision_note)}` : ""}.</p>
    {note.can_decide && <div className="sp-actions">
      <button className="button button-dark" type="button" disabled={busy} onClick={() => void act({ action: "approve" }, (result) => `${result.number || title} is approved and posted.`)}>Approve and post</button>
      <button className="button button-outline" type="button" disabled={busy} onClick={() => setRejecting(!rejecting)}>Reject</button>
    </div>}
    {note.status === "submitted" && !note.can_decide && <p className="sp-hint">{overview.can_approve ? "You prepared this note, so someone else must approve it." : "Waiting for someone with the approve permission."}</p>}
    {rejecting && <div className="mj-inline">
      <input aria-label="Why it is rejected" value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder="Why it is rejected" />
      <button className="button button-dark" type="button" disabled={busy || !reason.trim()} onClick={() => void act({ action: "reject", reason }, () => `${title} is rejected.`)}>Reject the note</button>
    </div>}
    {error && <div className="alert" role="alert">{error}</div>}
  </article>;
}

export default function AdjustmentNotes({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [side, setSide] = useState<Side>("customer");
  const [status, setStatus] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [preparing, setPreparing] = useState(false);
  const [open, setOpen] = useState<Note | null>(null);

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    const query = new URLSearchParams({ unit: String(selectedUnitId), side, ...(status ? { status } : {}) });
    try {
      setOverview(await call<Overview>(`/api/finance/notes/?${query}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Credit and debit notes could not be loaded.");
    }
  }, [selectedUnitId, side, status]);
  useEffect(() => { void load(); }, [load]);
  const view = async (id: number) => {
    if (!overview) return;
    setNotice(""); setPreparing(false);
    try {
      setOpen(await call<Note>(`/api/finance/notes/${id}/?unit=${overview.unit.id}`));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The note could not be opened.");
    }
  };
  const changed = (note: Note, message: string) => { setPreparing(false); setOpen(note); setNotice(message); void load(); };
  const switchSide = (next: Side) => { setSide(next); setOpen(null); setPreparing(false); setNotice(""); };

  return <section className="content-card supplier-payments bank-cash adjustment-notes" aria-labelledby="adjustment-notes-title">
    <header className="sp-head">
      <span className="eyebrow">FINANCE · CREDIT AND DEBIT NOTES · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="adjustment-notes-title">Credit and debit notes</h2>
      <p>Change what a customer owes us, or what we owe a supplier, when no goods come back: a price agreed after the sale, a billing mistake, damaged goods kept, or an extra charge. One person prepares a note and a different person approves it; only then does it change the balance and reach the accounts. Returned goods still go through returns and refunds.</p>
    </header>
    <div className="an-tabs" role="tablist" aria-label="Customers or suppliers">
      {(["customer", "supplier"] as Side[]).map((value) => <button key={value} role="tab" type="button" aria-selected={side === value}
        className={side === value ? "an-tab an-tab-on" : "an-tab"} onClick={() => switchSide(value)}>
        {value === "customer" ? "Customers" : "Suppliers"}{overview && overview.waiting[value] > 0 ? <span className="an-count">{overview.waiting[value]}</span> : null}
      </button>)}
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading notes…</p>}
    {overview && <>
      <div className="bc-filters">
        <label>Show<select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All</option><option value="submitted">Waiting for approval</option><option value="approved">Approved</option><option value="rejected">Rejected</option>
        </select></label>
        {overview.can_approve && <p className="an-mine">{overview.waiting.for_me} {overview.waiting.for_me === 1 ? "note waits" : "notes wait"} for you to approve</p>}
        {overview.can_prepare && !preparing && <div className="bc-buttons">
          <button className="button button-dark" type="button" onClick={() => { setNotice(""); setOpen(null); setPreparing(true); }}>
            {side === "customer" ? "Prepare a credit note" : "Record a supplier note"}</button>
        </div>}
      </div>
      {preparing && <Prepare key={side} overview={overview} csrfToken={csrfToken} onDone={changed} onCancel={() => setPreparing(false)} />}
      {!preparing && open && <Detail key={open.id} note={open} overview={overview} csrfToken={csrfToken} onChanged={changed} onClose={() => setOpen(null)} />}
      {overview.notes.length === 0
        ? <p className="sp-empty">No {side} notes{status ? " match" : " yet"}.</p>
        : <div className="sp-table-wrap"><table className="sp-table bc-table an-table" aria-label={side === "customer" ? "Customer credit notes" : "Supplier notes"}>
          <thead><tr><th scope="col">Note</th><th scope="col">{side === "customer" ? "Customer and invoice" : "Supplier and receipt"}</th>
            <th scope="col">Date</th><th scope="col" className="bc-num">Amount</th><th scope="col">Status</th></tr></thead>
          <tbody>{overview.notes.map((note) => <tr key={note.id} className={open?.id === note.id ? "mj-open" : undefined}>
            <th scope="row"><button className="fa-link" type="button" onClick={() => void view(note.id)}>{note.number || note.their_reference || `Note ${note.id}`}</button>
              <small>{side === "supplier" ? `${note.kind_label} · ` : ""}{note.reason_label}</small></th>
            <td>{show(note.counterparty)}<small>{show(note.document)}</small></td>
            <td>{note.date}</td>
            <td className="pw-mono bc-num">{note.lowers ? "−" : "+"}{fmt(note.amount)} {note.currency}</td>
            <td><span className={`ex-status ${STATUS_CLASS[note.status]}`}>{note.status_label}</span>
              <small>{note.decided_by ? `by ${note.decided_by}` : `prepared by ${note.prepared_by}`}{note.can_decide ? " · for you to approve" : ""}</small></td>
          </tr>)}</tbody>
        </table></div>}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
