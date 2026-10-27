import { useEffect, useRef, useState } from "react";
import type { LabContext } from "./finance-draft-lab-api";
import { LabError, labRequest } from "./finance-draft-lab-api";
import {
  blankSupplierAdvanceDraft, calculateSupplierAdvanceReview, payloadFromSupplierAdvance, validateSupplierAdvance,
} from "./finance-supplier-advance";
import type {
  SupplierAdvanceDraft, SupplierAdvancePayload, SupplierAdvanceReview,
} from "./finance-supplier-advance";
import "./finance-invoice.css";
import { reopenFromQuery } from "./finance-lab-deep-link";

type AdvanceRow = {
  id: number; supplier_reference: string; document_reference: string; proposed_date: string; amount: string; revision: number;
};
type AdvanceList = { advances: AdvanceRow[] };
type AdvanceSnapshot = SupplierAdvancePayload & { id: number; revision: number; review: SupplierAdvanceReview };
type AdvanceDetail = AdvanceSnapshot & {
  history: { revision: number; created_at: string; snapshot: AdvanceSnapshot }[];
};

function copyAdvance(row: SupplierAdvancePayload): SupplierAdvanceDraft {
  return {
    supplier_reference: row.supplier_reference, document_reference: row.document_reference,
    proposed_date: row.proposed_date, amount: row.amount, purpose: row.purpose,
    account_id: row.account_id === null ? "" : String(row.account_id),
    business_unit_id: row.business_unit_id === null ? "" : String(row.business_unit_id),
    dimension_value_ids: row.dimension_value_ids.map(String),
  };
}
function unavailable(value: string, options: { id: number }[]) {
  return Boolean(value) && !options.some((item) => String(item.id) === value);
}

export function FinanceSupplierAdvanceLab() {
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [context, setContext] = useState<LabContext | null>(null);
  const [advances, setAdvances] = useState<AdvanceRow[]>([]);
  const [current, setCurrent] = useState<AdvanceDetail | null>(null);
  const [draft, setDraft] = useState<SupplierAdvanceDraft>(blankSupplierAdvanceDraft);
  const [review, setReview] = useState<SupplierAdvanceReview | null>(null);
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
  const errors = validateSupplierAdvance(draft, context);

  function change(next: SupplierAdvanceDraft) {
    setDraft(next); dirtyRef.current = true; setDirty(true); setReviewed(false); setReview(null);
  }
  function clearProtectedData() {
    requestVersion.current++;
    setAuthenticated(false); setContext(null); setAdvances([]); setCurrent(null);
    setDraft(blankSupplierAdvanceDraft()); setReview(null);
    dirtyRef.current = false; setDirty(false); setReviewed(false); setConflict(false); setUncertain(false); setListInspected(false);
    setStatus("Synthetic test access is unavailable.");
    setError("Synthetic test access is unavailable. Protected supplier-advance inputs were cleared.");
  }
  useEffect(() => {
    alive.current = true;
    const beforeUnload = (event: BeforeUnloadEvent) => { if (dirtyRef.current || busyRef.current) event.preventDefault(); };
    window.addEventListener("beforeunload", beforeUnload);
    void labRequest<{ authenticated: boolean }>("session").then(async (session) => {
      if (!alive.current) return;
      setAuthenticated(session.authenticated);
      if (!session.authenticated) return;
      const [choices, saved] = await Promise.all([
        labRequest<LabContext>("context"), labRequest<AdvanceList>("supplier-advances"),
      ]);
      if (!alive.current) return;
      setContext(choices); setAdvances(saved.advances);
      setStatus("Synthetic supplier-advance proposal lab ready. Review only; no payment or payable change.");
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
      else setError(`${cause instanceof Error ? cause.message : "Supplier-advance lab unavailable."} Inputs remain unchanged.`);
    } finally { busyRef.current = false; if (alive.current) setBusy(false); }
  }
  async function refreshList() {
    const saved = await labRequest<AdvanceList>("supplier-advances");
    if (!alive.current) return;
    setAdvances(saved.advances);
    if (uncertain) {
      setListInspected(true);
      setStatus("Saved proposal register refreshed. Inspect it and explicitly reopen a saved record before continuing after the unknown save outcome.");
    } else setStatus("Saved supplier-advance proposal register refreshed.");
  }
  function mayReplace() {
    return (!dirtyRef.current && !busyRef.current) ||
      (!busyRef.current && window.confirm("Discard unsaved supplier-advance edits and open the selected saved version?"));
  }
  function newProposal() {
    if (uncertain) { setError("Unknown save outcome: refresh and inspect the saved list, then explicitly reopen a saved proposal before starting or saving another."); return; }
    if (!mayReplace()) return;
    requestVersion.current++;
    setCurrent(null); setDraft(blankSupplierAdvanceDraft()); setReview(null);
    dirtyRef.current = false; setDirty(false); setReviewed(false); setConflict(false);
    setStatus("New supplier-advance proposal. It remains unpaid and unapplied; no operational payable, approval, or posting is created.");
  }
  async function openProposal(id: number, confirmed = false) {
    if (uncertain && !listInspected) { setError("Refresh and inspect the saved proposal list before reopening a record after an unknown save outcome."); return; }
    if (!confirmed && !mayReplace()) return;
    await run(async () => {
      const version = ++requestVersion.current;
      const row = await labRequest<AdvanceDetail>(`supplier-advances/${id}`);
      if (!alive.current || version !== requestVersion.current) return;
      setCurrent(row); setDraft(copyAdvance(row)); setReview(row.review);
      dirtyRef.current = false; setDirty(false); setReviewed(true); setConflict(false);
      setUncertain(false); setListInspected(false);
      setStatus(`Opened supplier-advance proposal ${row.id}, revision ${row.revision}. Unpaid and unapplied; no operational payment or payable change.`);
    });
  }
  function reviewProposal() {
    if (errors.length) { setReviewed(false); setReview(null); setError(errors.join(" ")); return; }
    try {
      const payload = payloadFromSupplierAdvance(draft, context);
      setReview(calculateSupplierAdvanceReview(payload)); setReviewed(true); setError("");
      setStatus("Reviewed explicit QAR amount. Proposal only; it remains unpaid and unapplied.");
    } catch (cause) { setReviewed(false); setError(cause instanceof Error ? cause.message : "Correct the proposal inputs."); }
  }
  async function saveProposal() {
    if (!reviewed || errors.length || uncertain || conflict) {
      setError(errors.join(" ") || "Review the explicit supplier-advance proposal before saving."); return;
    }
    await run(async () => {
      let writeAttempted = false;
      try {
        const version = requestVersion.current;
        const freshContext = await labRequest<LabContext>("context");
        if (!alive.current || version !== requestVersion.current) return;
        setContext(freshContext);
        const freshErrors = validateSupplierAdvance(draft, freshContext);
        if (freshErrors.length) {
          setReviewed(false); setReview(null);
          throw new Error(`Saved choices or inputs need correction after context refresh. Original choices remain visible. ${freshErrors.join(" ")}`);
        }
        const payload = payloadFromSupplierAdvance(draft, freshContext);
        const body = { ...payload, ...(current ? { expected_revision: current.revision } : {}) };
        writeAttempted = true;
        const saved = await labRequest<AdvanceDetail>(
          current ? `supplier-advances/${current.id}` : "supplier-advances", current ? "PUT" : "POST", body,
        );
        if (!saved || !Number.isSafeInteger(saved.id) || !Number.isSafeInteger(saved.revision) || !Array.isArray(saved.history)) {
          throw new Error("The save response was incomplete.");
        }
        if (!alive.current || version !== requestVersion.current) return;
        setCurrent(saved); setDraft(copyAdvance(saved)); setReview(saved.review);
        dirtyRef.current = false; setDirty(false); setConflict(false); setUncertain(false); setListInspected(false); setReviewed(true);
        setStatus(`Save acknowledged for supplier-advance proposal ${saved.id}, revision ${saved.revision}. Unpaid and unapplied; no operational payment or payable change.`);
        try {
          await refreshList();
          if (alive.current) setStatus(`Saved supplier-advance proposal ${saved.id}, revision ${saved.revision}; register refreshed. Unpaid and unapplied.`);
        } catch (cause) {
          if (cause instanceof LabError && cause.status === 403) throw cause;
          if (alive.current) setError("Proposal saved successfully, but the saved-list refresh failed. The save was acknowledged; refresh the list when available.");
        }
      } catch (cause) {
        if (alive.current && cause instanceof LabError && cause.status === 409) setConflict(true);
        if (alive.current && writeAttempted && (!(cause instanceof LabError) || cause.status >= 500)) {
          setUncertain(true); setListInspected(false);
          setStatus("Save response unavailable; the database may have committed. No exactly-once guarantee is implied. Refresh and inspect the saved list, then explicitly reopen a saved proposal before another save.");
        }
        throw cause;
      }
    });
  }
  function setField<K extends keyof SupplierAdvanceDraft>(key: K, value: SupplierAdvanceDraft[K]) {
    change({ ...draft, [key]: value });
  }

  return <main className="invoice-lab" aria-labelledby="supplier-advance-title" data-testid="supplier-advance-editor">
    <header className="invoice-banner">
      <p className="invoice-kicker">TIVOLI FINANCE · ISOLATED REVIEW WORKSHEET</p>
      <h1 id="supplier-advance-title">Supplier advance proposals</h1>
      <p>Record explicit supplier references, proposal dates, purposes, and QAR amounts for careful internal review. Original text is retained exactly as entered.</p>
      <p><strong>Proposal only.</strong> It is not a payment, invoice application, payable change, approval, or posting.</p>
      <p>Arabic business text is preserved raw. English controls do not translate or alter customer-facing bilingual documents.</p>
      <a href="/lab-ui/">Return to Finance draft lab setup</a>
    </header>
    {error && <p className="invoice-alert" role="alert">{error}</p>}
    <p className="invoice-live" role="status" aria-live="polite">{loading ? "Loading isolated supplier-advance lab…" : busy ? "Working with the isolated supplier-advance lab…" : status}</p>
    {loading && <section className="invoice-panel" aria-label="Loading supplier-advance proposals"><div className="invoice-skeleton" aria-hidden="true" /></section>}
    {!loading && !authenticated && <section className="invoice-panel">
      <h2>Isolated synthetic access</h2>
      <p>Open the existing Finance draft lab to configure a synthetic session and saved Finance choices. This worksheet creates none of those.</p>
      <a href="/lab-ui/">Open Finance draft lab setup</a>
    </section>}
    {!loading && authenticated && <>
      <section className="invoice-panel" aria-label="Saved supplier-advance proposals">
        <div className="invoice-heading">
          <div><p className="invoice-kicker">SAVED PROPOSALS</p><h2>Supplier-advance register</h2></div>
          <div className="invoice-toolbar">
            <button type="button" className="invoice-secondary" disabled={busy} onClick={() => void run(refreshList)}>Refresh saved proposals</button>
            <button type="button" disabled={busy || !context || uncertain} onClick={newProposal}>New supplier-advance proposal</button>
          </div>
        </div>
        {!advances.length ? <p>No saved supplier-advance proposals yet. A saved account, business unit, or dimension value is optional; this worksheet creates no setup choices.</p> :
          <ul className="invoice-register">{advances.map((item) => <li key={item.id}>
            <button type="button" className="invoice-secondary" disabled={busy}
              aria-label={`Open supplier-advance proposal ${item.id}: ${item.supplier_reference} / ${item.document_reference}`}
              onClick={() => void openProposal(item.id)}>
              {item.supplier_reference} · {item.document_reference} · {item.proposed_date} · {item.amount} QAR · revision {item.revision}
            </button>
          </li>)}</ul>}
        {uncertain && <p role="alert">Unknown save outcome blocks another save or new record. Refresh and inspect this list, then explicitly reopen a saved proposal to recover.</p>}
      </section>
      {!context && <section className="invoice-panel"><h2>Saved setup is unavailable</h2><p>Saved accounts, business units, or dimension values are unavailable. Configure synthetic choices in the existing lab; none are created here.</p><a href="/lab-ui/">Open draft lab setup</a></section>}
      {context && <fieldset className="invoice-editor" disabled={busy} aria-busy={busy} data-dirty={dirty} data-testid="supplier-advance-editor-fields">
        <legend>{current ? `Editing supplier-advance proposal ${current.id}, revision ${current.revision}` : "New supplier-advance proposal"}</legend>
        <aside className="invoice-boundary">
          <strong>Review boundary</strong>
          <p>The entered amount is an explicit non-negative QAR magnitude only. Any saved account, business unit, or dimensions are optional independently selected references; blank is valid. No invoice is linked and no payment terms, deposit percentage, mapping, tax, approval, or posting is inferred.</p>
          <p>Every saved proposal remains unpaid and unapplied. History preserves proposal snapshots only; no operational payment or payable is changed.</p>
        </aside>
        <section className="invoice-fields" aria-label="Supplier advance details">
          <label>Supplier reference · required
            <input aria-label="Supplier reference" maxLength={200} value={draft.supplier_reference} onChange={(event) => setField("supplier_reference", event.target.value)} />
          </label>
          <label>Document reference · required
            <input aria-label="Document reference" maxLength={200} value={draft.document_reference} onChange={(event) => setField("document_reference", event.target.value)} />
          </label>
          <label>Proposed date · required
            <input type="date" aria-label="Proposed date" value={draft.proposed_date} onChange={(event) => setField("proposed_date", event.target.value)} />
          </label>
          <label>Amount · QAR · exact non-negative magnitude
            <input aria-label="Amount" inputMode="decimal" value={draft.amount} onChange={(event) => setField("amount", event.target.value)} />
            <small>Up to 999999999999.99; at most two decimal places. Saved review uses two decimals.</small>
          </label>
          <label className="invoice-wide">Purpose · required
            <textarea aria-label="Purpose" maxLength={2000} value={draft.purpose} onChange={(event) => setField("purpose", event.target.value)} />
          </label>
          <label>Saved account · optional
            <select aria-label="Account" value={draft.account_id} onChange={(event) => setField("account_id", event.target.value)}>
              <option value="">Not proposed</option>
              {unavailable(draft.account_id, context.accounts) && <option value={draft.account_id}>Unavailable saved choice ({draft.account_id}) — choose or clear</option>}
              {context.accounts.map((account) => <option key={account.id} value={account.id}>{account.code} — {account.name_en}</option>)}
            </select>
          </label>
          <label>Business unit · optional
            <select aria-label="Business unit" value={draft.business_unit_id} onChange={(event) => setField("business_unit_id", event.target.value)}>
              <option value="">Not proposed</option>
              {unavailable(draft.business_unit_id, context.business_units) && <option value={draft.business_unit_id}>Unavailable saved choice ({draft.business_unit_id}) — choose or clear</option>}
              {context.business_units.map((unit) => <option key={unit.id} value={unit.id}>{unit.code} — {unit.name}</option>)}
            </select>
          </label>
          <label className="invoice-wide">Dimensions · optional; select saved values
            <select multiple aria-label="Dimensions" value={draft.dimension_value_ids}
              onChange={(event) => setField("dimension_value_ids", [...event.target.selectedOptions].map((option) => option.value))}>
              {draft.dimension_value_ids.filter((id) => unavailable(id, context.dimension_values)).map((id) => <option key={`unavailable-${id}`} value={id}>Unavailable saved choice ({id}) — remove or replace</option>)}
              {context.dimension_values.map((value) => <option key={value.id} value={value.id}>{value.code} — {value.label_en}</option>)}
            </select>
            <small>Only existing saved values are selectable. Missing saved IDs remain visible and block save until corrected.</small>
          </label>
          <div className="invoice-currency"><span>Currency</span><strong>QAR</strong><span>Fixed · no conversion</span></div>
        </section>
        {errors.length > 0 && <div className="invoice-note" role="note" aria-label="Supplier advance validation">
          <strong>Complete or correct these explicit inputs before review</strong><ul>{errors.map((issue) => <li key={issue}>{issue}</li>)}</ul>
        </div>}
        {review && <section className="invoice-review" aria-label="Supplier advance review">
          <p className="invoice-kicker">EXPLICIT QAR REVIEW · PROPOSAL ONLY</p><h2>Review supplier advance</h2>
          <p>Proposed: yes · unpaid: yes · unapplied: yes · actual payment: no · invoice application: no · operational payable change: no · approved: no · posted: no.</p>
          <div className="invoice-total"><span>Amount · QAR</span><strong className="invoice-number">{review.total}</strong></div>
        </section>}
        {current && <p className="invoice-note">Saved review at revision {current.revision}: {current.review.total} QAR · unpaid · unapplied · no operational payment or payable change.</p>}
        <div className="invoice-actions">
          <button type="button" className="invoice-secondary" disabled={busy} onClick={reviewProposal}>Review supplier-advance proposal</button>
          <button type="button" disabled={busy || !reviewed || errors.length > 0 || uncertain || conflict} onClick={() => void saveProposal()}>
            {current ? "Save supplier-advance revision" : "Save supplier-advance proposal"}
          </button>
        </div>
      </fieldset>}
      {conflict && current && <section className="invoice-panel" role="alert">
        <h2>Saved revision changed</h2><p>Your local inputs remain unchanged. Reload only after explicit confirmation.</p>
        <button type="button" className="invoice-secondary" disabled={busy} onClick={() => {
          if (window.confirm("Discard local supplier-advance edits and reload the saved version?")) void openProposal(current.id, true);
        }}>Reload saved supplier advance</button>
      </section>}
      {current && <section className="invoice-history" aria-label="Supplier advance revision history">
        <h2>Saved revision history</h2>
        <p>Snapshots preserve proposal inputs only. Each remains unpaid and unapplied; no operational payment, payable change, approval, or posting is created.</p>
        {current.history.map((item) => <details key={item.revision}>
          <summary>Revision {item.revision} — {item.snapshot.supplier_reference} · {item.snapshot.document_reference} · {item.snapshot.review.total} QAR</summary>
          <p>{item.created_at} · proposed date {item.snapshot.proposed_date} · currency QAR · unpaid · unapplied</p>
          <p>Purpose: {item.snapshot.purpose}</p>
          <p>Account: {item.snapshot.account_id ?? "not proposed"} · business unit: {item.snapshot.business_unit_id ?? "not proposed"} · dimension IDs: {item.snapshot.dimension_value_ids.length ? item.snapshot.dimension_value_ids.join(", ") : "not proposed"}</p>
        </details>)}
      </section>}
    </>}
  </main>;
}
export default FinanceSupplierAdvanceLab;
