import { useEffect, useRef, useState } from "react";
import type { LabContext } from "./finance-draft-lab-api";
import { LabError, labRequest } from "./finance-draft-lab-api";
import {
  blankCustomerInvoiceDraft, blankCustomerInvoiceLine, calculateCustomerInvoiceReview,
  payloadFromCustomerInvoice, validateCustomerInvoice,
} from "./finance-customer-invoice";
import type {
  CustomerInvoiceDraft, CustomerInvoiceLineDraft, CustomerInvoicePayload, CustomerInvoiceReview,
} from "./finance-customer-invoice";
import "./finance-invoice.css";
import { reopenFromQuery } from "./finance-lab-deep-link";

type CustomerInvoiceList = { customer_invoices: { id: number; customer_reference: string; invoice_reference: string; revision: number }[] };
type DuplicateWarning = { possible_duplicate: boolean; matches: { id: number; revision: number }[] };
type CustomerInvoiceSnapshot = CustomerInvoicePayload & { id: number; revision: number; review: CustomerInvoiceReview };
type CustomerInvoiceDetail = CustomerInvoiceSnapshot & {
  duplicate_warning: DuplicateWarning;
  history: { revision: number; created_at: string; snapshot: CustomerInvoiceSnapshot }[];
};
function copyCustomerInvoice(row: CustomerInvoicePayload): CustomerInvoiceDraft {
  return {
    customer_reference: row.customer_reference, customer_details: row.customer_details ?? "",
    invoice_reference: row.invoice_reference, invoice_date: row.invoice_date, due_date: row.due_date ?? "",
    lines: row.lines.map((line) => ({
      description: line.description, amount: line.amount, account_id: String(line.account_id),
      business_unit_id: line.business_unit_id === null ? "" : String(line.business_unit_id),
      dimension_value_ids: line.dimension_value_ids.map(String),
    })),
  };
}
function unavailable(value: string, options: { id: number }[]) {
  return Boolean(value) && !options.some((item) => String(item.id) === value);
}

export function FinanceCustomerInvoiceLab() {
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [context, setContext] = useState<LabContext | null>(null);
  const [invoices, setInvoices] = useState<CustomerInvoiceList["customer_invoices"]>([]);
  const [current, setCurrent] = useState<CustomerInvoiceDetail | null>(null);
  const [draft, setDraft] = useState<CustomerInvoiceDraft>(blankCustomerInvoiceDraft);
  const [review, setReview] = useState<CustomerInvoiceReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [dirty, setDirty] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [listInspected, setListInspected] = useState(false);
  const busyRef = useRef(false);
  const dirtyRef = useRef(false);
  const alive = useRef(true);
  const requestVersion = useRef(0);
  const errors = validateCustomerInvoice(draft, context);

  function change(next: CustomerInvoiceDraft) {
    setDraft(next); dirtyRef.current = true; setDirty(true); setReviewed(false); setReview(null);
  }
  function clearProtectedData() {
    requestVersion.current++;
    setAuthenticated(false); setContext(null); setInvoices([]); setCurrent(null);
    setDraft(blankCustomerInvoiceDraft()); setReview(null); dirtyRef.current = false; setDirty(false);
    setReviewed(false); setConflict(false); setUncertain(false); setListInspected(false);
    setStatus("Synthetic test access is unavailable.");
    setError("Synthetic test access is unavailable. Protected customer-invoice inputs were cleared.");
  }
  useEffect(() => {
    alive.current = true;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current || busyRef.current) event.preventDefault();
    };
    window.addEventListener("beforeunload", beforeUnload);
    void labRequest<{ authenticated: boolean }>("session").then(async (session) => {
      if (!alive.current) return;
      setAuthenticated(session.authenticated);
      if (!session.authenticated) return;
      const [choices, list] = await Promise.all([
        labRequest<LabContext>("context"), labRequest<CustomerInvoiceList>("customer-invoices"),
      ]);
      if (!alive.current) return;
      setContext(choices); setInvoices(list.customer_invoices);
      setStatus("Synthetic customer-invoice worksheet ready. Every record remains an internal proposal.");
       reopenFromQuery((id) => { void openInvoice(id); }, setError);
    }).catch((cause: Error) => {
      if (!alive.current) return;
      if (cause instanceof LabError && cause.status === 403) clearProtectedData();
      else setError(cause.message);
    }).finally(() => { if (alive.current) setLoading(false); });
    return () => {
      alive.current = false; requestVersion.current++;
      window.removeEventListener("beforeunload", beforeUnload);
    };
  }, []);
  async function run(action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError("");
    try { await action(); }
    catch (cause) {
      if (!alive.current) return;
      if (cause instanceof LabError && cause.status === 403) clearProtectedData();
      else setError(`${cause instanceof Error ? cause.message : "Customer-invoice lab unavailable."} Inputs remain unchanged.`);
    } finally {
      busyRef.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function refreshList() {
    const result = await labRequest<CustomerInvoiceList>("customer-invoices");
    if (!alive.current) return;
    setInvoices(result.customer_invoices);
    if (uncertain) {
      setListInspected(true);
      setStatus("Saved customer-invoice register refreshed. Inspect it and explicitly reopen a saved record before continuing after the unknown save outcome.");
    } else setStatus("Saved customer-invoice register refreshed.");
  }
  function mayReplace() {
    return (!dirtyRef.current && !busyRef.current) ||
      (!busyRef.current && window.confirm("Discard unsaved customer-invoice edits and open the selected saved version?"));
  }
  function newInvoice() {
    if (uncertain) {
      setError("Unknown save outcome: refresh and inspect the saved register, then explicitly reopen a saved invoice before starting or saving another.");
      return;
    }
    if (!mayReplace()) return;
    requestVersion.current++;
    setCurrent(null); setDraft(blankCustomerInvoiceDraft()); setReview(null);
    dirtyRef.current = false; setDirty(false); setReviewed(false); setConflict(false);
    setStatus("New customer-invoice proposal. No customer master is created and no receivable is operational.");
  }
  async function openInvoice(id: number, confirmed = false) {
    if (uncertain && !listInspected) {
      setError("Refresh and inspect the saved customer-invoice register before reopening a record after an unknown save outcome.");
      return;
    }
    if (!confirmed && !mayReplace()) return;
    await run(async () => {
      const version = ++requestVersion.current;
      const row = await labRequest<CustomerInvoiceDetail>(`customer-invoices/${id}`);
      if (!alive.current || version !== requestVersion.current) return;
      setCurrent(row); setDraft(copyCustomerInvoice(row)); setReview(row.review);
      dirtyRef.current = false; setDirty(false); setReviewed(true); setConflict(false);
      setUncertain(false); setListInspected(false);
      setStatus(`Opened customer invoice ${row.id}, revision ${row.revision}. Internal proposal only — not issued, operational, approved, or posted.`);
    });
  }
  function reviewInvoice() {
    if (errors.length) { setReviewed(false); setReview(null); setError(errors.join(" ")); return; }
    try {
      const payload = payloadFromCustomerInvoice(draft, context!);
      setReview(calculateCustomerInvoiceReview(payload)); setReviewed(true); setError("");
      setStatus("Reviewed explicit QAR inputs. Internal proposal only; no customer-facing document or operational receivable is created.");
    } catch (cause) {
      setReviewed(false); setError(cause instanceof Error ? cause.message : "Correct the customer-invoice inputs.");
    }
  }
  async function saveInvoice() {
    if (!reviewed || errors.length || uncertain || conflict) {
      setError(errors.join(" ") || "Review the explicit customer-invoice proposal before saving.");
      return;
    }
    await run(async () => {
      let writeAttempted = false;
      try {
        const version = requestVersion.current;
        const freshContext = await labRequest<LabContext>("context");
        if (!alive.current || version !== requestVersion.current) return;
        setContext(freshContext);
        const freshErrors = validateCustomerInvoice(draft, freshContext);
        if (freshErrors.length) {
          setReviewed(false); setReview(null);
          throw new Error(`Saved choices or inputs need correction after context refresh. Original choices remain visible and are not replaced. ${freshErrors.join(" ")}`);
        }
        const payload = payloadFromCustomerInvoice(draft, freshContext);
        const body = { ...payload, ...(current ? { expected_revision: current.revision } : {}) };
        writeAttempted = true;
        const saved = await labRequest<CustomerInvoiceDetail>(
          current ? `customer-invoices/${current.id}` : "customer-invoices", current ? "PUT" : "POST", body,
        );
        if (!saved || !Number.isSafeInteger(saved.id) || !Number.isSafeInteger(saved.revision) || !Array.isArray(saved.history)) {
          throw new Error("The save response was incomplete.");
        }
        if (!alive.current || version !== requestVersion.current) return;
        setCurrent(saved); setDraft(copyCustomerInvoice(saved)); setReview(saved.review);
        dirtyRef.current = false; setDirty(false); setConflict(false); setUncertain(false);
        setListInspected(false); setReviewed(true);
        setStatus(`Saved customer invoice ${saved.id}, revision ${saved.revision}. Internal proposal only — not issued, operational, approved, or posted.`);
        try {
          await refreshList();
          if (alive.current) setStatus(`Saved customer invoice ${saved.id}, revision ${saved.revision}. Register refreshed. Internal proposal only — not issued, operational, approved, or posted.`);
        } catch (cause) {
          if (cause instanceof LabError && cause.status === 403) throw cause;
          if (alive.current) setError("Customer invoice saved successfully, but the register refresh failed. The save was acknowledged; refresh the register when available.");
        }
      } catch (cause) {
        if (alive.current && cause instanceof LabError && cause.status === 409) setConflict(true);
        if (alive.current && writeAttempted && (!(cause instanceof LabError) || cause.status >= 500)) {
          setUncertain(true); setListInspected(false);
          setStatus("Save response unavailable; the database may have committed. Refresh and inspect the saved register, then explicitly reopen a saved customer invoice before another save.");
        }
        throw cause;
      }
    });
  }
  function setField<K extends keyof CustomerInvoiceDraft>(key: K, value: CustomerInvoiceDraft[K]) {
    change({ ...draft, [key]: value });
  }
  function changeLine(index: number, patch: Partial<CustomerInvoiceLineDraft>) {
    setField("lines", draft.lines.map((line, row) => row === index ? { ...line, ...patch } : line));
  }
  function addLine() {
    if (draft.lines.length < 200) setField("lines", [...draft.lines, blankCustomerInvoiceLine()]);
  }
  function removeLine(index: number) {
    if (draft.lines.length > 1) setField("lines", draft.lines.filter((_, row) => row !== index));
  }
  function duplicateNotice(warning: DuplicateWarning | undefined) {
    if (!warning?.possible_duplicate) return null;
    return <aside className="invoice-duplicate" role="note" aria-label="Possible duplicate warning">
      <strong>Possible reference match</strong>
      <p>Another current proposal in this entity has the same case-sensitive customer and invoice references (comparison ignores surrounding ASCII whitespace only). Advisory only—not a uniqueness rule, merge, or save block.</p>
      <ul className="invoice-warning-list">{warning.matches.map((match) => <li key={match.id}>Invoice {match.id} · revision {match.revision}</li>)}</ul>
    </aside>;
  }
  return <main className="invoice-lab" aria-labelledby="customer-invoice-title" data-testid="finance-customer-invoice-lab">
    <header className="invoice-banner">
      <p className="invoice-kicker">TIVOLI FINANCE · ISOLATED REVIEW WORKSHEET</p>
      <h1 id="customer-invoice-title">Customer invoices</h1>
      <p>Prepare an exact-input internal review proposal from explicit customer references, dates, and QAR line amounts. Original text is retained exactly as entered.</p>
      <p><strong>Internal proposal only.</strong> This does not issue a customer invoice or create an operational receivable. Customer-facing issued documents remain separately bilingual and unaffected.</p>
      <p>Arabic user content is preserved as entered; it is not translated.</p>
      <a href="/lab-ui/">Return to Finance draft lab setup</a>
    </header>
    {error && <p className="invoice-alert" role="alert">{error}</p>}
    <p className="invoice-live" role="status" aria-live="polite">{loading ? "Loading isolated customer-invoice lab…" : busy ? "Working with the isolated customer-invoice lab…" : status}</p>
    {loading && <section className="invoice-panel" aria-label="Loading customer invoices"><div className="invoice-skeleton" aria-hidden="true" /></section>}
    {!loading && !authenticated && <section className="invoice-panel">
      <h2>Isolated synthetic access</h2>
      <p>Open the existing Finance draft lab to configure a synthetic test session and saved Finance choices. This worksheet creates none of those.</p>
      <a href="/lab-ui/">Open Finance draft lab setup</a>
    </section>}
    {!loading && authenticated && <>
      <section className="invoice-panel" aria-label="Saved customer invoices">
        <div className="invoice-heading">
          <div><p className="invoice-kicker">SAVED PROPOSALS</p><h2>Customer-invoice register</h2></div>
          <div className="invoice-toolbar">
            <button type="button" className="invoice-secondary" disabled={busy} onClick={() => void run(refreshList)}>Refresh saved customer invoices</button>
            <button type="button" disabled={busy || !context || uncertain} onClick={newInvoice}>New customer-invoice proposal</button>
          </div>
        </div>
        {!invoices.length ? <p>No saved customer-invoice proposals yet. Existing saved Finance accounts are required for each line; this worksheet creates no setup choices.</p> :
          <ul className="invoice-register">{invoices.map((item) => <li key={item.id}>
            <button type="button" className="invoice-secondary" disabled={busy}
              aria-label={`Open customer invoice ${item.id}: ${item.customer_reference} / ${item.invoice_reference}`}
              onClick={() => void openInvoice(item.id)}>
              {item.customer_reference} · {item.invoice_reference} · revision {item.revision}
            </button>
          </li>)}</ul>}
        {uncertain && <p role="alert">Unknown save outcome blocks another save or new record. Refresh and inspect this register, then explicitly reopen a saved invoice to recover.</p>}
      </section>
      {!context && <section className="invoice-panel">
        <h2>Saved Finance choices are unavailable</h2>
        <p>Existing accounts, business units, or dimension values are unavailable. Configure synthetic choices in the existing lab; none are created here.</p>
        <a href="/lab-ui/">Open draft lab setup</a>
      </section>}
      {context && <fieldset className="invoice-editor" disabled={busy} aria-busy={busy} data-dirty={dirty} data-testid="customer-invoice-editor">
        <legend>{current ? `Editing customer invoice ${current.id}, revision ${current.revision}` : "New customer-invoice proposal"}</legend>
        <aside className="invoice-boundary">
          <strong>Review boundary</strong>
          <p>Customer references and details are manually entered; there is no customer master lookup or access. Customer details are optional descriptive input only. Due date is independently entered and optional; no terms or order date are inferred.</p>
          <p>QAR is fixed. No tax, payment, credit-limit, operational invoice/receivable, approval, posting, print, or export function is provided.</p>
          <p>Arabic content is kept raw and is not translated. Customer-facing issued documents remain separately bilingual and unaffected.</p>
        </aside>
        {duplicateNotice(current?.duplicate_warning)}
        <section className="invoice-fields" aria-label="Customer invoice details">
          <label>Customer reference · required
            <input aria-label="Customer reference" maxLength={200} value={draft.customer_reference} onChange={(event) => setField("customer_reference", event.target.value)} />
          </label>
          <label>Invoice reference · required
            <input aria-label="Invoice reference" maxLength={200} value={draft.invoice_reference} onChange={(event) => setField("invoice_reference", event.target.value)} />
          </label>
          <label className="invoice-wide">Customer details · optional · manually entered
            <textarea aria-label="Customer details" maxLength={2000} value={draft.customer_details} onChange={(event) => setField("customer_details", event.target.value)} />
          </label>
          <label>Invoice date · required
            <input type="date" aria-label="Invoice date" value={draft.invoice_date} onChange={(event) => setField("invoice_date", event.target.value)} />
          </label>
          <label>Due date · optional · entered independently
            <input type="date" aria-label="Due date" value={draft.due_date} onChange={(event) => setField("due_date", event.target.value)} />
          </label>
          <div className="invoice-currency"><span>Currency</span><strong>QAR</strong><span>Fixed · no conversion</span></div>
        </section>
        <div className="invoice-heading">
          <div><h2>Invoice lines</h2><p>Enter 1–200 explicit lines. Each line needs an existing saved account.</p></div>
          <button type="button" className="invoice-secondary" disabled={busy || draft.lines.length >= 200} onClick={addLine}>Add invoice line</button>
        </div>
        <ol className="invoice-lines">{draft.lines.map((line, index) => {
          const number = index + 1;
          return <li className="invoice-line" key={index}>
            <div className="invoice-line-heading"><h3>Line {number}</h3>
              <button type="button" className="invoice-secondary" disabled={busy || draft.lines.length <= 1}
                aria-label={`Remove invoice line ${number}`} onClick={() => removeLine(index)}>Remove line</button>
            </div>
            <div className="invoice-line-fields">
              <label className="invoice-wide">Description · required
                <textarea aria-label={`Line description ${number}`} maxLength={2000} value={line.description} onChange={(event) => changeLine(index, { description: event.target.value })} />
              </label>
              <label>Amount · QAR · exact decimal
                <input aria-label={`Line amount ${number}`} inputMode="decimal" value={line.amount} onChange={(event) => changeLine(index, { amount: event.target.value })} />
              </label>
              <label>Saved account · required
                <select aria-label={`Line account ${number}`} value={line.account_id} onChange={(event) => changeLine(index, { account_id: event.target.value })}>
                  <option value="">Choose an existing saved account</option>
                  {unavailable(line.account_id, context.accounts) && <option value={line.account_id}>Unavailable saved choice ({line.account_id}) — choose another</option>}
                  {context.accounts.map((account) => <option key={account.id} value={account.id}>{account.code} — {account.name_en}</option>)}
                </select>
              </label>
              <label>Business unit · optional
                <select aria-label={`Line business unit ${number}`} value={line.business_unit_id} onChange={(event) => changeLine(index, { business_unit_id: event.target.value })}>
                  <option value="">Not proposed</option>
                  {unavailable(line.business_unit_id, context.business_units) && <option value={line.business_unit_id}>Unavailable saved choice ({line.business_unit_id}) — choose or clear</option>}
                  {context.business_units.map((unit) => <option key={unit.id} value={unit.id}>{unit.code} — {unit.name}</option>)}
                </select>
              </label>
              <label className="invoice-wide">Dimensions · optional; select saved values
                <select multiple aria-label={`Line dimensions ${number}`} value={line.dimension_value_ids}
                  onChange={(event) => changeLine(index, { dimension_value_ids: [...event.target.selectedOptions].map((option) => option.value) })}>
                  {line.dimension_value_ids.filter((id) => unavailable(id, context.dimension_values)).map((id) =>
                    <option key={`unavailable-${id}`} value={id}>Unavailable saved choice ({id}) — remove or replace</option>)}
                  {context.dimension_values.map((value) => <option key={value.id} value={value.id}>{value.code} — {value.label_en}</option>)}
                </select>
                <small>Only existing saved values are selectable. Missing saved IDs remain visible and block save until corrected.</small>
              </label>
            </div>
          </li>;
        })}</ol>
        {errors.length > 0 && <div className="invoice-note" role="note" aria-label="Customer invoice validation">
          <strong>Complete or correct these explicit inputs before review</strong><ul>{errors.map((issue) => <li key={issue}>{issue}</li>)}</ul>
        </div>}
        {review && <section className="invoice-review" aria-label="Customer invoice review">
          <p className="invoice-kicker">LOCAL QAR CALCULATION · INTERNAL PROPOSAL ONLY</p>
          <h2>Review customer invoice</h2>
          <p>Exact total of entered lines. Proposed: yes · issued customer invoice: no · operational receivable: no · approved: no · posted: no.</p>
          <div className="invoice-total"><span>Total · QAR</span><strong className="invoice-number">{review.total}</strong></div>
          {duplicateNotice(current?.duplicate_warning)}
        </section>}
        {current && <p className="invoice-note">Saved review at revision {current.revision}: {current.review.total} QAR · internal proposal only · not issued, operational, approved, or posted.</p>}
        <div className="invoice-actions">
          <button type="button" className="invoice-secondary" disabled={busy} onClick={reviewInvoice}>Review customer-invoice proposal</button>
          <button type="button" disabled={busy || !reviewed || errors.length > 0 || uncertain || conflict}
            onClick={() => void saveInvoice()}>{current ? "Save customer-invoice revision" : "Save customer-invoice proposal"}</button>
        </div>
      </fieldset>}
      {conflict && current && <section className="invoice-panel" role="alert">
        <h2>Saved revision changed</h2>
        <p>Your local inputs remain unchanged. Reload only after explicit confirmation.</p>
        <button type="button" className="invoice-secondary" disabled={busy} onClick={() => {
          if (window.confirm("Discard local customer-invoice edits and reload the saved version?")) void openInvoice(current.id, true);
        }}>Reload saved customer invoice</button>
      </section>}
      {current && <section className="invoice-history" aria-label="Customer invoice revision history">
        <h2>Saved revision history</h2>
        <p>Snapshots preserve proposal inputs only. They do not issue or approve a customer invoice or post a receivable.</p>
        {current.history.map((item) => <details key={item.revision}>
          <summary>Revision {item.revision} — {item.snapshot.customer_reference} · {item.snapshot.invoice_reference} · {item.snapshot.review.total} QAR</summary>
          <p>{item.created_at} · invoice date {item.snapshot.invoice_date} · due date {item.snapshot.due_date ?? "not proposed"} · currency QAR</p>
          <p>Customer details: {item.snapshot.customer_details || "not provided"}</p>
          {item.snapshot.lines.map((line, index) => <p key={index}>
            Line {index + 1}: {line.description} · {line.amount} QAR · account ID {line.account_id}
            {line.business_unit_id === null ? " · business unit not proposed" : ` · business unit ID ${line.business_unit_id}`}
            {line.dimension_value_ids.length ? ` · dimension IDs ${line.dimension_value_ids.join(", ")}` : " · no dimensions proposed"}
          </p>)}
        </details>)}
      </section>}
    </>}
  </main>;
}
export default FinanceCustomerInvoiceLab;

