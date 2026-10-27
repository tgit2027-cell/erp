import { useEffect, useRef, useState } from "react";
import type { LabContext } from "./finance-draft-lab-api";
import { LabError, labRequest } from "./finance-draft-lab-api";
import type { AdjustmentDraft, AdjustmentPayload, AdjustmentReview, AdjustmentLineDraft } from "./finance-adjustment";
import { blankAdjustmentDraft, blankAdjustmentLine, calculateAdjustmentTotal, payloadFromAdjustment, validateAdjustment } from "./finance-adjustment";
import "./finance-invoice.css";
import { reopenFromQuery } from "./finance-lab-deep-link";

type InvoiceChoice = { id: number; supplier_reference: string; invoice_reference: string; revision: number };
type AdjustmentRow = { id: number; proposal_type: "credit" | "debit"; supplier_reference: string; document_reference: string; revision: number };
type SavedAdjustment = AdjustmentPayload & {
  id: number;
  revision: number;
  review: AdjustmentReview;
  linked_invoice: null | { id: number; revision: number; supplier_reference: string; invoice_reference: string };
};
type AdjustmentDetail = SavedAdjustment & {
  history: { revision: number; created_at: string; snapshot: SavedAdjustment }[];
};
type AdjustmentList = { adjustments: AdjustmentRow[] };
type InvoiceList = { invoices: InvoiceChoice[] };

function copyAdjustment(row: AdjustmentPayload): AdjustmentDraft {
  return {
    proposal_type: row.proposal_type,
    supplier_reference: row.supplier_reference,
    document_reference: row.document_reference,
    proposal_date: row.proposal_date,
    reason: row.reason,
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
function copySavedPayload(row: SavedAdjustment): AdjustmentPayload {
  return {
    proposal_type: row.proposal_type, supplier_reference: row.supplier_reference,
    document_reference: row.document_reference, proposal_date: row.proposal_date, reason: row.reason,
    linked_invoice_id: row.linked_invoice_id, currency: "QAR", lines: row.lines,
  };
}

export function FinanceAdjustmentLab() {
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [context, setContext] = useState<LabContext | null>(null);
  const [invoices, setInvoices] = useState<InvoiceChoice[]>([]);
  const [adjustments, setAdjustments] = useState<AdjustmentRow[]>([]);
  const [current, setCurrent] = useState<AdjustmentDetail | null>(null);
  const [draft, setDraft] = useState<AdjustmentDraft>(blankAdjustmentDraft);
  const [review, setReview] = useState<AdjustmentReview | null>(null);
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
  const errors = validateAdjustment(draft, context, invoices);

  function change(next: AdjustmentDraft) {
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
    setAdjustments([]);
    setCurrent(null);
    setDraft(blankAdjustmentDraft());
    setReview(null);
    dirtyRef.current = false;
    setDirty(false);
    setReviewed(false);
    setConflict(false);
    setUncertain(false);
    setListInspected(false);
    setStatus("Synthetic test access is unavailable.");
    setError("Synthetic test access is unavailable. Protected adjustment inputs were cleared.");
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
      const [choices, invoiceList, result] = await Promise.all([
        labRequest<LabContext>("context"), labRequest<InvoiceList>("invoices"), labRequest<AdjustmentList>("adjustments"),
      ]);
      if (!alive.current) return;
      setContext(choices);
      setInvoices(invoiceList.invoices);
      setAdjustments(result.adjustments);
      setStatus("Synthetic supplier credit/debit proposal lab ready. Review only; no accounting authority.");
       reopenFromQuery((id) => { void openAdjustment(id); }, setError);
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
      else setError(`${cause instanceof Error ? cause.message : "Adjustment lab unavailable."} Inputs remain unchanged.`);
    } finally {
      busyRef.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function refreshList() {
    const [result, invoiceList] = await Promise.all([
      labRequest<AdjustmentList>("adjustments"), labRequest<InvoiceList>("invoices"),
    ]);
    if (!alive.current) return;
    setAdjustments(result.adjustments);
    setInvoices(invoiceList.invoices);
    if (uncertain) {
      setListInspected(true);
      setStatus("Saved proposal register refreshed. Inspect it and explicitly reopen a saved record before continuing after the unknown save outcome.");
    } else setStatus("Saved proposal register refreshed.");
  }
  function mayReplace() {
    return (!dirtyRef.current && !busyRef.current) ||
      (!busyRef.current && window.confirm("Discard unsaved adjustment edits and open the selected saved version?"));
  }
  function newProposal() {
    if (uncertain) {
      setError("Unknown save outcome: refresh and inspect the saved list, then explicitly reopen a saved proposal before starting or saving another.");
      return;
    }
    if (!mayReplace()) return;
    setCurrent(null);
    setDraft(blankAdjustmentDraft());
    setReview(null);
    dirtyRef.current = false;
    setDirty(false);
    setReviewed(false);
    setConflict(false);
    setStatus("New supplier adjustment proposal. No payable, settlement, approval, or posting is created.");
  }
  async function openAdjustment(id: number, confirmed = false) {
    if (uncertain && !listInspected) {
      setError("Refresh and inspect the saved proposal list before reopening a record after an unknown save outcome.");
      return;
    }
    if (!confirmed && !mayReplace()) return;
    await run(async () => {
      const row = await labRequest<AdjustmentDetail>(`adjustments/${id}`);
      if (!alive.current) return;
      setCurrent(row);
      setDraft(copyAdjustment(copySavedPayload(row)));
      setReview(row.review);
      dirtyRef.current = false;
      setDirty(false);
      setReviewed(true);
      setConflict(false);
      setUncertain(false);
      setListInspected(false);
      setStatus(`Opened adjustment proposal ${row.id}, revision ${row.revision}. Proposed only — not an issued credit note, settlement, payable adjustment, approved, or posted.`);
    });
  }
  function reviewProposal() {
    if (errors.length) {
      setReviewed(false);
      setReview(null);
      setError(errors.join(" "));
      return;
    }
    try {
      const payload = payloadFromAdjustment(draft, context!, invoices);
      setReview({
        currency: "QAR",
        total: calculateAdjustmentTotal(payload.lines),
        interpretation: "Explicit non-negative magnitudes; credit/debit is classification only.",
        amount_basis: "explicit_non_negative_magnitudes",
        proposed: true,
        issued_credit_note: false,
        settlement: false,
        operational_payable_adjustment: false,
        approved: false,
        posted: false,
      });
      setReviewed(true);
      setError("");
      setStatus("Reviewed explicit QAR magnitudes. Classification does not apply a sign or change a payable.");
    } catch (cause) {
      setReviewed(false);
      setError(cause instanceof Error ? cause.message : "Correct the proposal inputs.");
    }
  }
  async function saveProposal() {
    if (!reviewed || errors.length || uncertain || conflict) {
      setError(errors.join(" ") || "Review the explicit adjustment proposal before saving.");
      return;
    }
    await run(async () => {
      let writeAttempted = false;
      try {
        const freshContext = await labRequest<LabContext>("context");
        if (!alive.current) return;
        setContext(freshContext);
        const freshErrors = validateAdjustment(draft, freshContext, invoices);
        if (freshErrors.length) {
          setReviewed(false);
          setReview(null);
          throw new Error(`Saved choices or inputs need correction after context refresh. Original choices remain visible and are not replaced. ${freshErrors.join(" ")}`);
        }
        const payload = payloadFromAdjustment(draft, freshContext, invoices);
        const body = { ...payload, ...(current ? { expected_revision: current.revision } : {}) };
        writeAttempted = true;
        const saved = await labRequest<AdjustmentDetail>(
          current ? `adjustments/${current.id}` : "adjustments", current ? "PUT" : "POST", body,
        );
        if (!saved || !Number.isSafeInteger(saved.id) || !Number.isSafeInteger(saved.revision) || !Array.isArray(saved.history)) {
          throw new Error("The save response was incomplete.");
        }
        if (!alive.current) return;
        setCurrent(saved);
        setDraft(copyAdjustment(copySavedPayload(saved)));
        setReview(saved.review);
        dirtyRef.current = false;
        setDirty(false);
        setConflict(false);
        setUncertain(false);
        setListInspected(false);
        setReviewed(true);
        setStatus(`Saved adjustment proposal ${saved.id}, revision ${saved.revision}. Proposal only — no issued note, settlement, operational payable adjustment, approval, or posting.`);
        try {
          await refreshList();
          if (alive.current) setStatus(`Saved adjustment proposal ${saved.id}, revision ${saved.revision}. Register refreshed. Proposal only; no accounting action.`);
        } catch (cause) {
          if (cause instanceof LabError && cause.status === 403) throw cause;
          if (alive.current) setError("Proposal saved successfully, but the saved-list refresh failed. The save was acknowledged; refresh the list when available.");
        }
      } catch (cause) {
        if (alive.current && cause instanceof LabError && cause.status === 409) setConflict(true);
        if (alive.current && writeAttempted && (!(cause instanceof LabError) || cause.status >= 500)) {
          setUncertain(true);
          setListInspected(false);
          setStatus("Save response unavailable; the database may have committed. Refresh and inspect the saved list, then explicitly reopen a saved proposal before another save.");
        }
        throw cause;
      }
    });
  }
  function setField<K extends keyof AdjustmentDraft>(key: K, value: AdjustmentDraft[K]) {
    change({ ...draft, [key]: value });
  }
  function changeLine(index: number, patch: Partial<AdjustmentLineDraft>) {
    setField("lines", draft.lines.map((line, row) => row === index ? { ...line, ...patch } : line));
  }
  function addLine() {
    if (draft.lines.length < 200) setField("lines", [...draft.lines, blankAdjustmentLine()]);
  }
  function removeLine(index: number) {
    if (draft.lines.length > 1) setField("lines", draft.lines.filter((_, row) => row !== index));
  }

  return <main className="invoice-lab" aria-labelledby="adjustment-title" data-testid="finance-adjustment-lab">
    <header className="invoice-banner">
      <p className="invoice-kicker">TIVOLI FINANCE · ISOLATED REVIEW WORKSHEET</p>
      <h1 id="adjustment-title">Supplier adjustments</h1>
      <p>Inspect explicit supplier credit/debit proposal amounts. Classification is a label only; amounts stay non-negative and are never mapped to payable activity.</p>
      <p><strong>Proposal only.</strong> Not an issued credit note, settlement, operational payable adjustment, approval, or posting.</p>
      <a href="/lab-ui/">Return to Finance draft lab setup</a>
    </header>
    {error && <p className="invoice-alert" role="alert">{error}</p>}
    <p className="invoice-live" role="status" aria-live="polite">{loading ? "Loading isolated adjustment lab…" : busy ? "Working with the isolated adjustment lab…" : status}</p>
    {loading && <section className="invoice-panel" aria-label="Loading adjustment proposals"><div className="invoice-skeleton" aria-hidden="true" /></section>}
    {!loading && !authenticated && <section className="invoice-panel">
      <h2>Isolated synthetic access</h2>
      <p>Open the existing Finance draft lab to configure the synthetic test session and saved accounts, units, and dimensions. This worksheet creates none of those.</p>
      <a href="/lab-ui/">Open Finance draft lab setup</a>
    </section>}
    {!loading && authenticated && <>
      <section className="invoice-panel" aria-label="Saved supplier adjustment proposals">
        <div className="invoice-heading">
          <div><p className="invoice-kicker">SAVED PROPOSALS</p><h2>Adjustment register</h2></div>
          <div className="invoice-toolbar">
            <button type="button" className="invoice-secondary" disabled={busy} onClick={() => void run(refreshList)}>Refresh saved proposals</button>
            <button type="button" disabled={busy || !context || uncertain} onClick={newProposal}>New adjustment proposal</button>
          </div>
        </div>
        {!adjustments.length ? <p>No saved supplier adjustment proposals yet. A saved invoice link is optional and never fills in proposal data.</p> :
          <ul className="invoice-register">{adjustments.map((item) => <li key={item.id}>
            <button type="button" className="invoice-secondary" disabled={busy}
              aria-label={`Open adjustment proposal ${item.id}: ${item.proposal_type} ${item.supplier_reference} / ${item.document_reference}`}
              onClick={() => void openAdjustment(item.id)}>
              {item.proposal_type.toUpperCase()} · {item.supplier_reference} · {item.document_reference} · revision {item.revision}
            </button>
          </li>)}</ul>}
        {uncertain && <p role="alert">Unknown save outcome blocks another save or new record. Refresh and inspect this list, then explicitly reopen a saved proposal to recover.</p>}
      </section>
      {!context && <section className="invoice-panel">
        <h2>Saved setup is unavailable</h2>
        <p>Saved accounts, business units, or dimension values are unavailable. Configure synthetic choices in the existing lab; none are created here.</p>
        <a href="/lab-ui/">Open draft lab setup</a>
      </section>}
      {context && <fieldset className="invoice-editor" disabled={busy} aria-busy={busy} data-dirty={dirty} data-testid="adjustment-editor">
        <legend>{current ? `Editing adjustment proposal ${current.id}, revision ${current.revision}` : "New supplier adjustment proposal"}</legend>
        <aside className="invoice-boundary">
          <strong>Interpretation boundary</strong>
          <p>Enter each amount as an explicit non-negative QAR magnitude. Credit/debit classification applies no sign, accounting mapping, or payable effect. A saved invoice link is optional and selected explicitly; it does not infer the supplier or copy lines.</p>
          <p>The link records the invoice revision validated when this proposal is saved. It does not live-track later invoice edits; the original invoice is unchanged. The server validates supplier and entity consistency.</p>
        </aside>
        <section className="invoice-fields" aria-label="Supplier adjustment details">
          <label>Proposal classification · required
            <select aria-label="Proposal type" value={draft.proposal_type} onChange={(event) => setField("proposal_type", event.target.value as AdjustmentDraft["proposal_type"])}>
              <option value="">Choose credit or debit classification</option>
              <option value="credit">Credit proposal</option><option value="debit">Debit proposal</option>
            </select>
          </label>
          <label>Supplier reference · required · exact match to linked invoice
            <input aria-label="Supplier reference" maxLength={200} value={draft.supplier_reference} onChange={(event) => setField("supplier_reference", event.target.value)} />
          </label>
          <label>Document reference · required
            <input aria-label="Document reference" maxLength={200} value={draft.document_reference} onChange={(event) => setField("document_reference", event.target.value)} />
          </label>
          <label>Proposal date · required
            <input type="date" aria-label="Proposal date" value={draft.proposal_date} onChange={(event) => setField("proposal_date", event.target.value)} />
          </label>
          <label>Link to saved invoice · optional · explicit selection only
            <select aria-label="Linked invoice" value={draft.linked_invoice_id} onChange={(event) => setField("linked_invoice_id", event.target.value)}>
              <option value="">No invoice link proposed</option>
              {unavailable(draft.linked_invoice_id, invoices) && <option value={draft.linked_invoice_id}>Unavailable saved invoice ({draft.linked_invoice_id}) — choose or clear</option>}
              {invoices.map((invoice) => <option key={invoice.id} value={invoice.id}>Invoice {invoice.id} · {invoice.supplier_reference} · {invoice.invoice_reference} · revision {invoice.revision}</option>)}
            </select>
          </label>
          <label className="invoice-wide">Reason · required
            <textarea aria-label="Reason" maxLength={2000} value={draft.reason} onChange={(event) => setField("reason", event.target.value)} />
          </label>
          <div className="invoice-currency"><span>Currency</span><strong>QAR</strong><span>Fixed · no conversion</span></div>
        </section>
        <div className="invoice-heading">
          <div><h2>Proposal lines</h2><p>Enter 1–200 explicit lines. Each line needs an existing account. Magnitudes never become negative.</p></div>
          <button type="button" className="invoice-secondary" disabled={busy || draft.lines.length >= 200} onClick={addLine}>Add proposal line</button>
        </div>
        <ol className="invoice-lines">{draft.lines.map((line, index) => {
          const number = index + 1;
          return <li className="invoice-line" key={index}>
            <div className="invoice-line-heading"><h3>Line {number}</h3>
              <button type="button" className="invoice-secondary" disabled={busy || draft.lines.length <= 1}
                aria-label={`Remove proposal line ${number}`} onClick={() => removeLine(index)}>Remove line</button>
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
        })}</ol>
        {errors.length > 0 && <div className="invoice-note" role="note" aria-label="Adjustment validation">
          <strong>Complete or correct these explicit inputs before review</strong>
          <ul>{errors.map((issue) => <li key={issue}>{issue}</li>)}</ul>
        </div>}
        {review && <section className="invoice-review" aria-label="Adjustment review">
          <p className="invoice-kicker">LOCAL QAR CALCULATION · PROPOSAL ONLY</p>
          <h2>Review adjustment</h2>
          <p>Explicit non-negative magnitudes. Credit/debit classification is not a sign or payable mapping.</p>
          <div className="invoice-total"><span>Total · QAR · {draft.proposal_type} classification</span><strong className="invoice-number">{review.total}</strong></div>
          <p>Proposed: yes · issued credit note: no · settlement: no · operational payable adjustment: no · approved: no · posted: no.</p>
        </section>}
        {current && <p className="invoice-note">Saved review at revision {current.revision}: {current.review.total} QAR · {current.review.amount_basis} · no accounting action.</p>}
        <div className="invoice-actions">
          <button type="button" className="invoice-secondary" disabled={busy} onClick={reviewProposal}>Review adjustment proposal</button>
          <button type="button" disabled={busy || !reviewed || errors.length > 0 || uncertain || conflict}
            onClick={() => void saveProposal()}>{current ? "Save adjustment revision" : "Save adjustment proposal"}</button>
        </div>
      </fieldset>}
      {conflict && current && <section className="invoice-panel" role="alert">
        <h2>Saved revision changed</h2>
        <p>Your local inputs remain unchanged. Reload only after explicit confirmation.</p>
        <button type="button" className="invoice-secondary" disabled={busy} onClick={() => {
          if (window.confirm("Discard local supplier-adjustment edits and reload the saved version?")) void openAdjustment(current.id, true);
        }}>Reload saved adjustment</button>
      </section>}
      {current && <section className="invoice-history" aria-label="Supplier adjustment revision history">
        <h2>Saved revision history</h2>
        <p>Snapshots contain proposal inputs and review metadata only. They do not issue a credit note, settle, adjust a payable, approve, or post.</p>
        {current.history.map((item) => <details key={item.revision}>
          <summary>Revision {item.revision} — {item.snapshot.proposal_type.toUpperCase()} · {item.snapshot.supplier_reference} · {item.snapshot.document_reference} · {item.snapshot.review.total} QAR</summary>
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

export default FinanceAdjustmentLab;
