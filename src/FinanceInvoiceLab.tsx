import { useEffect, useRef, useState } from "react";
import type { LabContext } from "./finance-draft-lab-api";
import { LabError, labRequest } from "./finance-draft-lab-api";
import {
  blankInvoiceDraft, blankInvoiceLine, calculateInvoiceReview, payloadFromInvoice, validateInvoice,
} from "./finance-invoice";
import type { InvoiceDraft, InvoiceLineDraft, InvoicePayload, InvoiceReview } from "./finance-invoice";
import "./finance-invoice.css";
import { reopenFromQuery } from "./finance-lab-deep-link";

type InvoiceList = { invoices: { id: number; supplier_reference: string; invoice_reference: string; revision: number }[] };
type DuplicateWarning = { possible_duplicate: boolean; matches: { id: number; revision: number }[] };
type InvoiceSnapshot = InvoicePayload & {
  id: number; revision: number; review: InvoiceReview;
};
type InvoiceDetail = InvoiceSnapshot & {
  duplicate_warning: DuplicateWarning;
  history: { revision: number; created_at: string; snapshot: InvoiceSnapshot }[];
};

function copyInvoice(row: InvoicePayload): InvoiceDraft {
  return {
    supplier_reference: row.supplier_reference,
    invoice_reference: row.invoice_reference,
    invoice_date: row.invoice_date,
    due_date: row.due_date ?? "",
    lines: row.lines.map((line) => ({
      description: line.description,
      amount: line.amount,
      account_id: String(line.account_id),
      business_unit_id: line.business_unit_id === null ? "" : String(line.business_unit_id),
      dimension_value_ids: line.dimension_value_ids.map(String),
    })),
  };
}
function unavailable(value: string, options: { id: number }[]) {
  return Boolean(value) && !options.some((item) => String(item.id) === value);
}

export function FinanceInvoiceLab() {
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [context, setContext] = useState<LabContext | null>(null);
  const [invoices, setInvoices] = useState<InvoiceList["invoices"]>([]);
  const [current, setCurrent] = useState<InvoiceDetail | null>(null);
  const [draft, setDraft] = useState<InvoiceDraft>(blankInvoiceDraft);
  const [review, setReview] = useState<InvoiceReview | null>(null);
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
  const errors = validateInvoice(draft, context);

  function change(next: InvoiceDraft) {
    setDraft(next);
    dirtyRef.current = true;
    setDirty(true);
    setReviewed(false);
    setReview(null);
  }
  function clearProtectedData() {
    setAuthenticated(false);
    setContext(null);
    setInvoices([]);
    setCurrent(null);
    setDraft(blankInvoiceDraft());
    setReview(null);
    dirtyRef.current = false;
    setDirty(false);
    setReviewed(false);
    setConflict(false);
    setUncertain(false);
    setListInspected(false);
    setStatus("Synthetic test access is unavailable.");
    setError("Synthetic test access is unavailable. Protected invoice inputs were cleared.");
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
        labRequest<LabContext>("context"), labRequest<InvoiceList>("invoices"),
      ]);
      if (!alive.current) return;
      setContext(choices);
      setInvoices(list.invoices);
      setStatus("Synthetic supplier-invoice worksheet ready. Every record remains a proposal.");
      reopenFromQuery((id) => { void openInvoice(id); }, setError);
    }).catch((cause: Error) => {
      if (!alive.current) return;
      if (cause instanceof LabError && cause.status === 403) clearProtectedData();
      else setError(cause.message);
    }).finally(() => { if (alive.current) setLoading(false); });
    return () => {
      alive.current = false;
      window.removeEventListener("beforeunload", beforeUnload);
    };
  }, []);
  async function run(action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try { await action(); }
    catch (cause) {
      if (!alive.current) return;
      if (cause instanceof LabError && cause.status === 403) clearProtectedData();
      else setError(`${cause instanceof Error ? cause.message : "Invoice lab unavailable."} Inputs remain unchanged.`);
    } finally {
      busyRef.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function refreshList() {
    const result = await labRequest<InvoiceList>("invoices");
    if (!alive.current) return;
    setInvoices(result.invoices);
    if (uncertain) {
      setListInspected(true);
      setStatus("Saved invoice list refreshed. Inspect it and explicitly reopen a saved record before continuing after the unknown save outcome.");
    } else setStatus("Saved invoice list refreshed.");
  }
  function mayReplace() {
    return (!dirtyRef.current && !busyRef.current) ||
      (!busyRef.current && window.confirm("Discard unsaved supplier-invoice edits and open the selected saved version?"));
  }
  function newInvoice() {
    if (uncertain) {
      setError("Unknown save outcome: refresh and inspect the saved list, then explicitly reopen a saved invoice before starting or saving another.");
      return;
    }
    if (!mayReplace()) return;
    setCurrent(null);
    setDraft(blankInvoiceDraft());
    setReview(null);
    dirtyRef.current = false;
    setDirty(false);
    setReviewed(false);
    setConflict(false);
    setStatus("New supplier-invoice proposal. No supplier master is created and no payable is operational.");
  }
  async function openInvoice(id: number, confirmed = false) {
    if (uncertain && !listInspected) {
      setError("Refresh and inspect the saved invoice list before reopening a record after an unknown save outcome.");
      return;
    }
    if (!confirmed && !mayReplace()) return;
    await run(async () => {
      const row = await labRequest<InvoiceDetail>(`invoices/${id}`);
      if (!alive.current) return;
      setCurrent(row);
      setDraft(copyInvoice(row));
      setReview(row.review);
      dirtyRef.current = false;
      setDirty(false);
      setReviewed(true);
      setConflict(false);
      setUncertain(false);
      setListInspected(false);
      setStatus(`Opened supplier invoice ${row.id}, revision ${row.revision}. Proposed only — not an operational payable, approved, or posted.`);
    });
  }
  function reviewInvoice() {
    if (errors.length) {
      setReviewed(false);
      setReview(null);
      setError(errors.join(" "));
      return;
    }
    try {
      const payload = payloadFromInvoice(draft, context!);
      setReview(calculateInvoiceReview(payload));
      setReviewed(true);
      setError("");
      setStatus("Reviewed explicit QAR invoice inputs. This proposal is not an operational payable, approval, or posting.");
    } catch (cause) {
      setReviewed(false);
      setError(cause instanceof Error ? cause.message : "Correct the invoice inputs.");
    }
  }
  async function saveInvoice() {
    if (!reviewed || errors.length || uncertain || conflict) {
      setError(errors.join(" ") || "Review the explicit supplier-invoice proposal before saving.");
      return;
    }
    await run(async () => {
      let writeAttempted = false;
      try {
        const freshContext = await labRequest<LabContext>("context");
        if (!alive.current) return;
        setContext(freshContext);
        const freshErrors = validateInvoice(draft, freshContext);
        if (freshErrors.length) {
          setReviewed(false);
          setReview(null);
          throw new Error(`Saved choices or inputs need correction after context refresh. Original choices remain visible and are not replaced. ${freshErrors.join(" ")}`);
        }
        const payload = payloadFromInvoice(draft, freshContext);
        const body = { ...payload, ...(current ? { expected_revision: current.revision } : {}) };
        writeAttempted = true;
        const saved = await labRequest<InvoiceDetail>(
          current ? `invoices/${current.id}` : "invoices", current ? "PUT" : "POST", body,
        );
        if (!saved || !Number.isSafeInteger(saved.id) || !Number.isSafeInteger(saved.revision) || !Array.isArray(saved.history)) {
          throw new Error("The save response was incomplete.");
        }
        if (!alive.current) return;
        setCurrent(saved);
        setDraft(copyInvoice(saved));
        setReview(saved.review);
        dirtyRef.current = false;
        setDirty(false);
        setConflict(false);
        setUncertain(false);
        setListInspected(false);
        setReviewed(true);
        setStatus(`Saved supplier invoice ${saved.id}, revision ${saved.revision}. Proposal only — not operational, approved, or posted.`);
        try {
          await refreshList();
          if (alive.current) setStatus(`Saved supplier invoice ${saved.id}, revision ${saved.revision}. Saved list refreshed. Proposal only — not operational, approved, or posted.`);
        } catch (cause) {
          if (cause instanceof LabError && cause.status === 403) throw cause;
          if (alive.current) setError("Invoice saved successfully, but the saved-list refresh failed. The save was acknowledged; refresh the list when available.");
        }
      } catch (cause) {
        if (alive.current && cause instanceof LabError && cause.status === 409) setConflict(true);
        if (alive.current && writeAttempted && (!(cause instanceof LabError) || cause.status >= 500)) {
          setUncertain(true);
          setListInspected(false);
          setStatus("Save response unavailable; the database may have committed. Refresh and inspect the saved list, then explicitly reopen a saved invoice before another save.");
        }
        throw cause;
      }
    });
  }
  function setField<K extends keyof InvoiceDraft>(key: K, value: InvoiceDraft[K]) {
    change({ ...draft, [key]: value });
  }
  function changeLine(index: number, patch: Partial<InvoiceLineDraft>) {
    setField("lines", draft.lines.map((line, row) => row === index ? { ...line, ...patch } : line));
  }
  function addLine() {
    if (draft.lines.length < 200) setField("lines", [...draft.lines, blankInvoiceLine()]);
  }
  function removeLine(index: number) {
    if (draft.lines.length > 1) setField("lines", draft.lines.filter((_, row) => row !== index));
  }
  function duplicateNotice(warning: DuplicateWarning | undefined) {
    if (!warning?.possible_duplicate) return null;
    return <aside className="invoice-duplicate" role="note" aria-label="Possible duplicate warning">
      <strong>Possible reference match</strong>
      <p>Another current proposal in this entity has the same case-sensitive supplier and invoice references (comparison ignores surrounding ASCII whitespace only). This is a warning—not a uniqueness rule, merge, or save block.</p>
      <ul className="invoice-warning-list">{warning.matches.map((match) => <li key={match.id}>Invoice {match.id} · revision {match.revision}</li>)}</ul>
    </aside>;
  }

  return <main className="invoice-lab" aria-labelledby="invoice-title" data-testid="finance-invoice-lab">
    <header className="invoice-banner">
      <p className="invoice-kicker">TIVOLI FINANCE · ISOLATED REVIEW WORKSHEET</p>
      <h1 id="invoice-title">Supplier invoices</h1>
      <p>Capture an explicit supplier reference, invoice date, and line-level QAR proposal using saved Finance choices. Your original text is kept exactly as entered.</p>
      <p><strong>Proposal only.</strong> Not a supplier master, operational payable, bank action, tax calculation, import, payment, approval, matching, or posting.</p>
      <a href="/lab-ui/">Return to Finance draft lab setup</a>
    </header>
    {error && <p className="invoice-alert" role="alert">{error}</p>}
    <p className="invoice-live" role="status" aria-live="polite">{loading ? "Loading isolated invoice lab…" : busy ? "Working with the isolated invoice lab…" : status}</p>
    {loading && <section className="invoice-panel" aria-label="Loading supplier invoices"><div className="invoice-skeleton" aria-hidden="true" /></section>}
    {!loading && !authenticated && <section className="invoice-panel">
      <h2>Isolated synthetic access</h2>
      <p>Open the existing Finance draft lab to configure the synthetic test session and saved accounts, units, and dimensions. This worksheet creates none of those.</p>
      <a href="/lab-ui/">Open Finance draft lab setup</a>
    </section>}
    {!loading && authenticated && <>
      <section className="invoice-panel" aria-label="Saved supplier invoices">
        <div className="invoice-heading">
          <div><p className="invoice-kicker">SAVED PROPOSALS</p><h2>Invoice register</h2></div>
          <div className="invoice-toolbar">
            <button type="button" className="invoice-secondary" disabled={busy} onClick={() => void run(refreshList)}>Refresh saved invoices</button>
            <button type="button" disabled={busy || !context || uncertain} onClick={newInvoice}>New invoice proposal</button>
          </div>
        </div>
        {!invoices.length ? <p>No saved invoice proposals yet. Saved Finance accounts are required for each line; this worksheet creates no setup choices.</p> :
          <ul className="invoice-register">{invoices.map((item) => <li key={item.id}>
            <button type="button" className="invoice-secondary" disabled={busy}
              aria-label={`Open supplier invoice ${item.id}: ${item.supplier_reference} / ${item.invoice_reference}`}
              onClick={() => void openInvoice(item.id)}>
              {item.supplier_reference} · {item.invoice_reference} · revision {item.revision}
            </button>
          </li>)}</ul>}
        {uncertain && <p role="alert">Unknown save outcome blocks another save or new record. Refresh and inspect this list, then explicitly reopen a saved invoice to recover.</p>}
      </section>
      {!context && <section className="invoice-panel">
        <h2>Saved setup is unavailable</h2>
        <p>Saved accounts, business units, or dimension values are unavailable. Configure synthetic choices in the existing lab; none are created here.</p>
        <a href="/lab-ui/">Open draft lab setup</a>
      </section>}
      {context && <fieldset className="invoice-editor" disabled={busy} aria-busy={busy} data-dirty={dirty} data-testid="invoice-editor">
        <legend>{current ? `Editing supplier invoice ${current.id}, revision ${current.revision}` : "New supplier-invoice proposal"}</legend>
        <aside className="invoice-boundary">
          <strong>Proposal boundary</strong>
          <p>Only saved account, business-unit, and dimension values can be selected. Due date is optional and independent of invoice date; no payment terms are inferred. References and descriptions retain their exact input, including Arabic text and whitespace.</p>
          <p>QAR is fixed. No supplier record is created. The review total is exact local arithmetic and does not create an operational payable.</p>
        </aside>
        {duplicateNotice(current?.duplicate_warning)}
        <section className="invoice-fields" aria-label="Supplier invoice details">
          <label>Supplier reference · required
            <input aria-label="Supplier reference" maxLength={200} value={draft.supplier_reference} onChange={(event) => setField("supplier_reference", event.target.value)} />
          </label>
          <label>Invoice reference · required
            <input aria-label="Invoice reference" maxLength={200} value={draft.invoice_reference} onChange={(event) => setField("invoice_reference", event.target.value)} />
          </label>
          <label>Invoice date · required
            <input type="date" aria-label="Invoice date" value={draft.invoice_date} onChange={(event) => setField("invoice_date", event.target.value)} />
          </label>
          <label>Due date · optional · no terms inferred
            <input type="date" aria-label="Due date" value={draft.due_date} onChange={(event) => setField("due_date", event.target.value)} />
          </label>
          <div className="invoice-currency"><span>Currency</span><strong>QAR</strong><span>Fixed · no conversion</span></div>
        </section>
        <div className="invoice-heading">
          <div><h2>Invoice lines</h2><p>Enter 1–200 explicit lines. Each line needs an existing account.</p></div>
          <button type="button" className="invoice-secondary" disabled={busy || draft.lines.length >= 200} onClick={addLine}>Add invoice line</button>
        </div>
        <ol className="invoice-lines">
          {draft.lines.map((line, index) => {
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
                  <small>Only existing saved dimension values are selectable. Missing saved IDs remain visible and block save until corrected.</small>
                </label>
              </div>
            </li>;
          })}
        </ol>
        {errors.length > 0 && <div className="invoice-note" role="note" aria-label="Invoice validation">
          <strong>Complete or correct these explicit inputs before review</strong>
          <ul>{errors.map((issue) => <li key={issue}>{issue}</li>)}</ul>
        </div>}
        {review && <section className="invoice-review" aria-label="Invoice review">
          <p className="invoice-kicker">LOCAL QAR CALCULATION · PROPOSAL ONLY</p>
          <h2>Review invoice</h2>
          <p>Exact total of entered lines. Proposed: yes · operational payable: no · approved: no · posted: no.</p>
          <div className="invoice-total"><span>Total · QAR</span><strong className="invoice-number">{review.total}</strong></div>
          {duplicateNotice(current?.duplicate_warning)}
        </section>}
        {current && <p className="invoice-note">Saved review at revision {current.revision}: {current.review.total} QAR · proposed: yes · operational payable: no · approved: no · posted: no.</p>}
        <div className="invoice-actions">
          <button type="button" className="invoice-secondary" disabled={busy} onClick={reviewInvoice}>Review invoice proposal</button>
          <button type="button" disabled={busy || !reviewed || errors.length > 0 || uncertain || conflict}
            onClick={() => void saveInvoice()}>{current ? "Save invoice revision" : "Save invoice proposal"}</button>
        </div>
      </fieldset>}
      {conflict && current && <section className="invoice-panel" role="alert">
        <h2>Saved revision changed</h2>
        <p>Your local inputs remain unchanged. Reload only after explicit confirmation.</p>
        <button type="button" className="invoice-secondary" disabled={busy} onClick={() => {
          if (window.confirm("Discard local supplier-invoice edits and reload the saved version?")) void openInvoice(current.id, true);
        }}>Reload saved invoice</button>
      </section>}
      {current && <section className="invoice-history" aria-label="Supplier invoice revision history">
        <h2>Saved revision history</h2>
        <p>Snapshots contain proposal inputs and review metadata only. They do not approve or post a payable.</p>
        {current.history.map((item) => <details key={item.revision}>
          <summary>Revision {item.revision} — {item.snapshot.supplier_reference} · {item.snapshot.invoice_reference} · {item.snapshot.review.total} QAR</summary>
          <p>{item.created_at} · invoice date {item.snapshot.invoice_date} · due date {item.snapshot.due_date ?? "not proposed"} · currency QAR</p>
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

export default FinanceInvoiceLab;
