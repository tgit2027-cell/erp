import { useEffect, useRef, useState } from "react";
import type { LabContext } from "./finance-draft-lab-api";
import { LabError, labRequest } from "./finance-draft-lab-api";
import {
  blankCustomerCreditDraft, blankCustomerCreditLine, calculateCustomerCreditReview,
  payloadFromCustomerCredit, validateCustomerCredit,
} from "./finance-customer-credit";
import type {
  CustomerCreditDraft, CustomerCreditLineDraft, CustomerCreditPayload, CustomerCreditReview, CustomerInvoiceChoice,
} from "./finance-customer-credit";
import "./finance-invoice.css";
import { reopenFromQuery } from "./finance-lab-deep-link";

type CreditRow = { id: number; customer_reference: string; document_reference: string; revision: number };
type CreditList = { customer_credits: CreditRow[] };
type CreditSnapshot = CustomerCreditPayload & {
  id: number; revision: number; review: CustomerCreditReview;
  linked_invoice: null | { id: number; revision: number; customer_reference: string; invoice_reference: string };
};
type CreditDetail = CreditSnapshot & { history: { revision: number; created_at: string; snapshot: CreditSnapshot }[] };
type CustomerInvoiceList = { customer_invoices: CustomerInvoiceChoice[] };

function copyCredit(row: CustomerCreditPayload): CustomerCreditDraft {
  return {
    customer_reference: row.customer_reference, document_reference: row.document_reference,
    proposal_date: row.proposal_date, reason: row.reason,
    linked_invoice_id: row.linked_invoice_id === null ? "" : String(row.linked_invoice_id),
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

export function FinanceCustomerCreditLab() {
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [context, setContext] = useState<LabContext | null>(null);
  const [invoices, setInvoices] = useState<CustomerInvoiceChoice[]>([]);
  const [credits, setCredits] = useState<CreditRow[]>([]);
  const [current, setCurrent] = useState<CreditDetail | null>(null);
  const [draft, setDraft] = useState<CustomerCreditDraft>(blankCustomerCreditDraft);
  const [review, setReview] = useState<CustomerCreditReview | null>(null);
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
  const errors = validateCustomerCredit(draft, context, invoices);

  function change(next: CustomerCreditDraft) {
    setDraft(next); dirtyRef.current = true; setDirty(true); setReviewed(false); setReview(null);
  }
  function clearProtectedData() {
    requestVersion.current++;
    setAuthenticated(false); setContext(null); setInvoices([]); setCredits([]); setCurrent(null);
    setDraft(blankCustomerCreditDraft()); setReview(null);
    dirtyRef.current = false; setDirty(false); setReviewed(false); setConflict(false); setUncertain(false); setListInspected(false);
    setStatus("Synthetic test access is unavailable.");
    setError("Synthetic test access is unavailable. Protected customer-credit inputs were cleared.");
  }
  useEffect(() => {
    alive.current = true;
    const beforeUnload = (event: BeforeUnloadEvent) => { if (dirtyRef.current || busyRef.current) event.preventDefault(); };
    window.addEventListener("beforeunload", beforeUnload);
    void labRequest<{ authenticated: boolean }>("session").then(async (session) => {
      if (!alive.current) return;
      setAuthenticated(session.authenticated);
      if (!session.authenticated) return;
      const [choices, invoiceList, saved] = await Promise.all([
        labRequest<LabContext>("context"), labRequest<CustomerInvoiceList>("customer-invoices"),
        labRequest<CreditList>("customer-credits"),
      ]);
      if (!alive.current) return;
      setContext(choices); setInvoices(invoiceList.customer_invoices); setCredits(saved.customer_credits);
      setStatus("Synthetic customer-credit proposal lab ready. Review only; no accounting authority.");
       reopenFromQuery((id) => { void openProposal(id); }, setError);
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
      else setError(`${cause instanceof Error ? cause.message : "Customer-credit lab unavailable."} Inputs remain unchanged.`);
    } finally { busyRef.current = false; if (alive.current) setBusy(false); }
  }
  async function refreshList() {
    const [saved, invoiceList] = await Promise.all([
      labRequest<CreditList>("customer-credits"), labRequest<CustomerInvoiceList>("customer-invoices"),
    ]);
    if (!alive.current) return;
    setCredits(saved.customer_credits); setInvoices(invoiceList.customer_invoices);
    if (uncertain) {
      setListInspected(true);
      setStatus("Saved proposal register refreshed. Inspect it and explicitly reopen a saved record before continuing after the unknown save outcome.");
    } else setStatus("Saved customer-credit proposal register refreshed.");
  }
  function mayReplace() {
    return (!dirtyRef.current && !busyRef.current) ||
      (!busyRef.current && window.confirm("Discard unsaved customer-credit edits and open the selected saved version?"));
  }
  function newProposal() {
    if (uncertain) { setError("Unknown save outcome: refresh and inspect the saved list, then explicitly reopen a saved proposal before starting or saving another."); return; }
    if (!mayReplace()) return;
    requestVersion.current++;
    setCurrent(null); setDraft(blankCustomerCreditDraft()); setReview(null);
    dirtyRef.current = false; setDirty(false); setReviewed(false); setConflict(false);
    setStatus("New customer-credit proposal. No refund, settlement, receivable adjustment, approval, or posting is created.");
  }
  async function openProposal(id: number, confirmed = false) {
    if (uncertain && !listInspected) { setError("Refresh and inspect the saved proposal list before reopening a record after an unknown save outcome."); return; }
    if (!confirmed && !mayReplace()) return;
    await run(async () => {
      const version = ++requestVersion.current;
      const row = await labRequest<CreditDetail>(`customer-credits/${id}`);
      if (!alive.current || version !== requestVersion.current) return;
      setCurrent(row); setDraft(copyCredit(row)); setReview(row.review);
      dirtyRef.current = false; setDirty(false); setReviewed(true); setConflict(false);
      setUncertain(false); setListInspected(false);
      setStatus(`Opened customer-credit proposal ${row.id}, revision ${row.revision}. Proposed only; no issued credit note, refund, settlement, receivable adjustment, approval, or posting.`);
    });
  }
  function reviewProposal() {
    if (errors.length) { setReviewed(false); setReview(null); setError(errors.join(" ")); return; }
    try {
      const payload = payloadFromCustomerCredit(draft, context!, invoices);
      setReview(calculateCustomerCreditReview(payload)); setReviewed(true); setError("");
      setStatus("Reviewed explicit QAR magnitudes. Internal proposal only; no customer-facing document or operational receivable is created.");
    } catch (cause) { setReviewed(false); setError(cause instanceof Error ? cause.message : "Correct the proposal inputs."); }
  }
  async function saveProposal() {
    if (!reviewed || errors.length || uncertain || conflict) {
      setError(errors.join(" ") || "Review the explicit customer-credit proposal before saving."); return;
    }
    await run(async () => {
      let writeAttempted = false;
      try {
        const version = requestVersion.current;
        const [freshContext, freshInvoices] = await Promise.all([
          labRequest<LabContext>("context"), labRequest<CustomerInvoiceList>("customer-invoices"),
        ]);
        if (!alive.current || version !== requestVersion.current) return;
        setContext(freshContext); setInvoices(freshInvoices.customer_invoices);
        const freshErrors = validateCustomerCredit(draft, freshContext, freshInvoices.customer_invoices);
        if (freshErrors.length) {
          setReviewed(false); setReview(null);
          throw new Error(`Saved choices or inputs need correction after context refresh. Original choices remain visible and are not replaced. ${freshErrors.join(" ")}`);
        }
        const payload = payloadFromCustomerCredit(draft, freshContext, freshInvoices.customer_invoices);
        const body = { ...payload, ...(current ? { expected_revision: current.revision } : {}) };
        writeAttempted = true;
        const saved = await labRequest<CreditDetail>(
          current ? `customer-credits/${current.id}` : "customer-credits", current ? "PUT" : "POST", body,
        );
        if (!saved || !Number.isSafeInteger(saved.id) || !Number.isSafeInteger(saved.revision) || !Array.isArray(saved.history)) {
          throw new Error("The save response was incomplete.");
        }
        if (!alive.current || version !== requestVersion.current) return;
        setCurrent(saved); setDraft(copyCredit(saved)); setReview(saved.review);
        dirtyRef.current = false; setDirty(false); setConflict(false); setUncertain(false); setListInspected(false); setReviewed(true);
        setStatus(`Saved customer-credit proposal ${saved.id}, revision ${saved.revision}. Proposal only — no issued note, refund, settlement, operational receivable adjustment, approval, or posting.`);
        try {
          await refreshList();
          if (alive.current) setStatus(`Saved customer-credit proposal ${saved.id}, revision ${saved.revision}. Register refreshed. Proposal only; no accounting action.`);
        } catch (cause) {
          if (cause instanceof LabError && cause.status === 403) throw cause;
          if (alive.current) setError("Proposal saved successfully, but the saved-list refresh failed. The save was acknowledged; refresh the list when available.");
        }
      } catch (cause) {
        if (alive.current && cause instanceof LabError && cause.status === 409) setConflict(true);
        if (alive.current && writeAttempted && (!(cause instanceof LabError) || cause.status >= 500)) {
          setUncertain(true); setListInspected(false);
          setStatus("Save response unavailable; the database may have committed. Refresh and inspect the saved list, then explicitly reopen a saved proposal before another save.");
        }
        throw cause;
      }
    });
  }
  function setField<K extends keyof CustomerCreditDraft>(key: K, value: CustomerCreditDraft[K]) {
    change({ ...draft, [key]: value });
  }
  function changeLine(index: number, patch: Partial<CustomerCreditLineDraft>) {
    setField("lines", draft.lines.map((line, row) => row === index ? { ...line, ...patch } : line));
  }
  function addLine() { if (draft.lines.length < 200) setField("lines", [...draft.lines, blankCustomerCreditLine()]); }
  function removeLine(index: number) { if (draft.lines.length > 1) setField("lines", draft.lines.filter((_, row) => row !== index)); }

  return <main className="invoice-lab" aria-labelledby="customer-credit-title" data-testid="customer-credit-editor">
    <header className="invoice-banner">
      <p className="invoice-kicker">TIVOLI FINANCE · ISOLATED REVIEW WORKSHEET</p>
      <h1 id="customer-credit-title">Customer credit proposals</h1>
      <p>Record explicit customer references, proposal dates, reasons, and QAR magnitudes for careful internal review. Original user text is retained as entered.</p>
      <p><strong>Proposal only.</strong> Not an issued credit note, refund, settlement, operational receivable adjustment, approval, or posting.</p>
      <p>Arabic user content is preserved raw. Customer-facing issued documents remain separately bilingual and unaffected.</p>
      <a href="/lab-ui/">Return to Finance draft lab setup</a>
    </header>
    {error && <p className="invoice-alert" role="alert">{error}</p>}
    <p className="invoice-live" role="status" aria-live="polite">{loading ? "Loading isolated customer-credit lab…" : busy ? "Working with the isolated customer-credit lab…" : status}</p>
    {loading && <section className="invoice-panel" aria-label="Loading customer-credit proposals"><div className="invoice-skeleton" aria-hidden="true" /></section>}
    {!loading && !authenticated && <section className="invoice-panel">
      <h2>Isolated synthetic access</h2>
      <p>Open the existing Finance draft lab to configure a synthetic session and saved Finance choices. This worksheet creates none of those.</p>
      <a href="/lab-ui/">Open Finance draft lab setup</a>
    </section>}
    {!loading && authenticated && <>
      <section className="invoice-panel" aria-label="Saved customer-credit proposals">
        <div className="invoice-heading">
          <div><p className="invoice-kicker">SAVED PROPOSALS</p><h2>Customer-credit register</h2></div>
          <div className="invoice-toolbar">
            <button type="button" className="invoice-secondary" disabled={busy} onClick={() => void run(refreshList)}>Refresh saved proposals</button>
            <button type="button" disabled={busy || !context || uncertain} onClick={newProposal}>New customer-credit proposal</button>
          </div>
        </div>
        {!credits.length ? <p>No saved customer-credit proposals yet. An optional saved customer invoice link is explicit and never copies or changes its source.</p> :
          <ul className="invoice-register">{credits.map((item) => <li key={item.id}>
            <button type="button" className="invoice-secondary" disabled={busy}
              aria-label={`Open customer-credit proposal ${item.id}: ${item.customer_reference} / ${item.document_reference}`}
              onClick={() => void openProposal(item.id)}>
              {item.customer_reference} · {item.document_reference} · revision {item.revision}
            </button>
          </li>)}</ul>}
        {uncertain && <p role="alert">Unknown save outcome blocks another save or new record. Refresh and inspect this list, then explicitly reopen a saved proposal to recover.</p>}
      </section>
      {!context && <section className="invoice-panel"><h2>Saved setup is unavailable</h2><p>Saved accounts, business units, or dimension values are unavailable. Configure synthetic choices in the existing lab; none are created here.</p><a href="/lab-ui/">Open draft lab setup</a></section>}
      {context && <fieldset className="invoice-editor" disabled={busy} aria-busy={busy} data-dirty={dirty} data-testid="customer-credit-editor-fields">
        <legend>{current ? `Editing customer-credit proposal ${current.id}, revision ${current.revision}` : "New customer-credit proposal"}</legend>
        <aside className="invoice-boundary">
          <strong>Interpretation boundary</strong>
          <p>Each line is an explicit non-negative magnitude. No sign, tax, refund, settlement, or receivable effect is inferred. A saved customer invoice link is optional and does not populate this proposal.</p>
          <p>The link records the source invoice revision. Its customer reference must exactly match after trimming surrounding ASCII whitespace only. The source invoice and its revisions are never changed.</p>
        </aside>
        <section className="invoice-fields" aria-label="Customer-credit details">
          <label>Customer reference · required · exact match to linked invoice
            <input aria-label="Customer reference" maxLength={200} value={draft.customer_reference} onChange={(event) => setField("customer_reference", event.target.value)} />
          </label>
          <label>Document reference · required
            <input aria-label="Document reference" maxLength={200} value={draft.document_reference} onChange={(event) => setField("document_reference", event.target.value)} />
          </label>
          <label>Proposal date · required
            <input type="date" aria-label="Proposal date" value={draft.proposal_date} onChange={(event) => setField("proposal_date", event.target.value)} />
          </label>
          <label>Linked customer invoice · optional · explicit selection only
            <select aria-label="Linked customer invoice" value={draft.linked_invoice_id} onChange={(event) => setField("linked_invoice_id", event.target.value)}>
              <option value="">No invoice link proposed</option>
              {unavailable(draft.linked_invoice_id, invoices) && <option value={draft.linked_invoice_id}>Unavailable saved invoice ({draft.linked_invoice_id}) — choose or clear</option>}
              {invoices.map((invoice) => <option key={invoice.id} value={invoice.id}>Invoice {invoice.id} · {invoice.customer_reference} · {invoice.invoice_reference} · revision {invoice.revision}</option>)}
            </select>
          </label>
          <label className="invoice-wide">Reason · required
            <textarea aria-label="Reason" maxLength={2000} value={draft.reason} onChange={(event) => setField("reason", event.target.value)} />
          </label>
          <div className="invoice-currency"><span>Currency</span><strong>QAR</strong><span>Fixed · no conversion</span></div>
        </section>
        <div className="invoice-heading">
          <div><h2>Proposal lines</h2><p>Enter 1–200 explicit non-negative magnitudes. Each line needs an existing saved account.</p></div>
          <button type="button" className="invoice-secondary" disabled={busy || draft.lines.length >= 200} onClick={addLine}>Add proposal line</button>
        </div>
        <ol className="invoice-lines">{draft.lines.map((line, index) => {
          const number = index + 1;
          return <li className="invoice-line" key={index}>
            <div className="invoice-line-heading"><h3>Line {number}</h3>
              <button type="button" className="invoice-secondary" disabled={busy || draft.lines.length <= 1} aria-label={`Remove proposal line ${number}`} onClick={() => removeLine(index)}>Remove line</button>
            </div>
            <div className="invoice-line-fields">
              <label className="invoice-wide">Description · required
                <textarea aria-label={`Line description ${number}`} maxLength={2000} value={line.description} onChange={(event) => changeLine(index, { description: event.target.value })} />
              </label>
              <label>Amount · QAR · explicit non-negative magnitude
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
                <select multiple aria-label={`Line dimensions ${number}`} value={line.dimension_value_ids} onChange={(event) => changeLine(index, { dimension_value_ids: [...event.target.selectedOptions].map((option) => option.value) })}>
                  {line.dimension_value_ids.filter((id) => unavailable(id, context.dimension_values)).map((id) => <option key={`unavailable-${id}`} value={id}>Unavailable saved choice ({id}) — remove or replace</option>)}
                  {context.dimension_values.map((value) => <option key={value.id} value={value.id}>{value.code} — {value.label_en}</option>)}
                </select>
                <small>Only existing saved values are selectable. Missing saved IDs remain visible and block save until corrected.</small>
              </label>
            </div>
          </li>;
        })}</ol>
        {errors.length > 0 && <div className="invoice-note" role="note" aria-label="Customer credit validation">
          <strong>Complete or correct these explicit inputs before review</strong><ul>{errors.map((issue) => <li key={issue}>{issue}</li>)}</ul>
        </div>}
        {review && <section className="invoice-review" aria-label="Customer credit review">
          <p className="invoice-kicker">LOCAL QAR CALCULATION · PROPOSAL ONLY</p><h2>Review customer credit</h2>
          <p>Explicit non-negative magnitudes. Proposed: yes · issued credit note: no · refund: no · settlement: no · operational receivable adjustment: no · approved: no · posted: no.</p>
          <div className="invoice-total"><span>Total · QAR</span><strong className="invoice-number">{review.total}</strong></div>
        </section>}
        {current && <p className="invoice-note">Saved review at revision {current.revision}: {current.review.total} QAR · {current.review.amount_basis} · no accounting action.</p>}
        <div className="invoice-actions">
          <button type="button" className="invoice-secondary" disabled={busy} onClick={reviewProposal}>Review customer-credit proposal</button>
          <button type="button" disabled={busy || !reviewed || errors.length > 0 || uncertain || conflict} onClick={() => void saveProposal()}>
            {current ? "Save customer-credit revision" : "Save customer-credit proposal"}
          </button>
        </div>
      </fieldset>}
      {conflict && current && <section className="invoice-panel" role="alert">
        <h2>Saved revision changed</h2><p>Your local inputs remain unchanged. Reload only after explicit confirmation.</p>
        <button type="button" className="invoice-secondary" disabled={busy} onClick={() => {
          if (window.confirm("Discard local customer-credit edits and reload the saved version?")) void openProposal(current.id, true);
        }}>Reload saved customer credit</button>
      </section>}
      {current && <section className="invoice-history" aria-label="Customer-credit revision history">
        <h2>Saved revision history</h2><p>Snapshots preserve proposal inputs only. They do not issue a credit note, refund, settle, adjust a receivable, approve, or post.</p>
        {current.history.map((item) => <details key={item.revision}>
          <summary>Revision {item.revision} — {item.snapshot.customer_reference} · {item.snapshot.document_reference} · {item.snapshot.review.total} QAR</summary>
          <p>{item.created_at} · proposal date {item.snapshot.proposal_date} · linked invoice {item.snapshot.linked_invoice?.id ?? "not proposed"} · currency QAR</p>
          <p>Reason: {item.snapshot.reason}</p>
          {item.snapshot.lines.map((line, index) => <p key={index}>
            Line {index + 1}: {line.description} · magnitude {line.amount} QAR · account ID {line.account_id}
            {line.business_unit_id === null ? " · business unit not proposed" : ` · business unit ID ${line.business_unit_id}`}
            {line.dimension_value_ids.length ? ` · dimension IDs ${line.dimension_value_ids.join(", ")}` : " · no dimensions proposed"}
          </p>)}
        </details>)}
      </section>}
    </>}
  </main>;
}
export default FinanceCustomerCreditLab;
