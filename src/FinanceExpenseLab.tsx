import { useEffect, useMemo, useRef, useState } from "react";
import type { LabContext } from "./finance-draft-lab-api";
import { LabError, labRequest } from "./finance-draft-lab-api";
import {
  blankExpenseDraft, emptyExpenseEntry, expenseTotal, payloadFromExpense, validateExpense,
} from "./finance-expense";
import type { ExpenseDraft, ExpensePayload, ExpenseEntryDraft } from "./finance-expense";
import "./finance-allocation.css";
import "./finance-expense.css";
import { reopenFromQuery } from "./finance-lab-deep-link";

type ExpenseReview = {
  currency: "QAR"; proposed_total: string; entry_count: number; missing_receipt_rows: number[];
  unproposed_business_unit_rows: number[]; approved: false; paid: false; posted: false;
};
type ExpenseDetail = ExpensePayload & {
  id: number; revision: number; review: ExpenseReview;
  history: { revision: number; created_at: string; snapshot: ExpensePayload & { id: number; revision: number; review: ExpenseReview } }[];
};
type ExpenseList = { proposals: { id: number; name: string; proposal_type: string; revision: number }[] };

function copyExpense(row: ExpensePayload): ExpenseDraft {
  return {
    name: row.name, proposalType: row.proposal_type, explanation: row.explanation,
    entries: row.entries.map((entry) => ({
      expenseDate: entry.expense_date, partyReference: entry.party_reference, description: entry.description,
      amount: entry.amount, accountId: String(entry.account_id),
      businessUnitId: entry.business_unit_id === null ? "" : String(entry.business_unit_id),
      dimensionIds: entry.dimension_value_ids.map(String), receiptReference: entry.receipt_reference,
    })),
  };
}
const savedAccountChoices = (context: LabContext) => context.accounts.map((item) => ({ id: item.id, code: item.code, name: item.name_en }));
const savedUnitChoices = (context: LabContext) => context.business_units.map((item) => ({ id: item.id, code: item.code, name: item.name }));
const savedDimensionChoices = (context: LabContext) => context.dimension_values.map((item) => ({ id: item.id, dimension: item.dimension_id, code: item.code, name: item.label_en }));

export function FinanceExpenseLab() {
  const [authenticated, setAuthenticated] = useState(false);
  const [context, setContext] = useState<LabContext | null>(null);
  const [proposals, setProposals] = useState<ExpenseList["proposals"]>([]);
  const [current, setCurrent] = useState<ExpenseDetail | null>(null);
  const [draft, setDraft] = useState<ExpenseDraft>(blankExpenseDraft);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [dirty, setDirty] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const busyRef = useRef(false);
  const dirtyRef = useRef(false);
  const alive = useRef(true);
  const accounts = useMemo(() => context ? savedAccountChoices(context) : [], [context]);
  const units = useMemo(() => context ? savedUnitChoices(context) : [], [context]);
  const dimensions = useMemo(() => context ? savedDimensionChoices(context) : [], [context]);
  const errors = validateExpense(draft, context);
  const total = expenseTotal(draft.entries);

  function change(next: ExpenseDraft, typeChanged = false) {
    setDraft(next); dirtyRef.current = true; setDirty(true); setReviewed(false);
    if (typeChanged) setStatus("Proposal type changed. Rows are retained; review claimant/custodian references for this type, then review again.");
  }
  function clearProtectedData() {
    setAuthenticated(false); setContext(null); setProposals([]); setCurrent(null); setDraft(blankExpenseDraft());
    dirtyRef.current = false; setDirty(false); setReviewed(false); setConflict(false); setUncertain(false);
    setStatus("Synthetic test access is unavailable.");
    setError("Synthetic test access is unavailable. Protected expense inputs were cleared.");
  }
  useEffect(() => {
    alive.current = true;
    const beforeUnload = (event: BeforeUnloadEvent) => { if (dirtyRef.current || busyRef.current) event.preventDefault(); };
    window.addEventListener("beforeunload", beforeUnload);
    void labRequest<{ authenticated: boolean }>("session").then(async (session) => {
      if (!alive.current) return;
      setAuthenticated(session.authenticated);
      if (!session.authenticated) return;
      const [choices, list] = await Promise.all([labRequest<LabContext>("context"), labRequest<ExpenseList>("expenses")]);
      if (alive.current) {
        setContext(choices); setProposals(list.proposals);
        reopenFromQuery((id) => { void openProposal(id); }, setError);
      }
    }).catch((cause: Error) => {
      if (!alive.current) return;
      if (cause instanceof LabError && cause.status === 403) clearProtectedData();
      else setError(cause.message);
    });
    return () => { alive.current = false; window.removeEventListener("beforeunload", beforeUnload); };
  }, []);
  async function run(action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError("");
    try { await action(); }
    catch (cause) {
      if (!alive.current) return;
      if (cause instanceof LabError && cause.status === 403) clearProtectedData();
      else setError(`${cause instanceof Error ? cause.message : "Expense lab unavailable."} Inputs remain unchanged.`);
    } finally { busyRef.current = false; if (alive.current) setBusy(false); }
  }
  async function refreshList() {
    const result = await labRequest<ExpenseList>("expenses");
    if (alive.current) setProposals(result.proposals);
  }
  function mayReplace() {
    return (!dirtyRef.current && !busyRef.current) ||
      (!busyRef.current && window.confirm("Discard unsaved expense proposal edits and open the selected saved version?"));
  }
  function newProposal() {
    if (uncertain) { setError("Unknown save outcome: refresh the list and explicitly reopen a saved proposal before starting or saving another."); return; }
    if (!mayReplace()) return;
    setCurrent(null); setDraft(blankExpenseDraft()); dirtyRef.current = false; setDirty(false);
    setReviewed(false); setConflict(false); setUncertain(false);
    setStatus("New proposal worksheet. Enter an explicit type; no approval, payment, posting, or journal lines are created.");
  }
  async function openProposal(id: number, confirmed = false) {
    if (!confirmed && !mayReplace()) return;
    await run(async () => {
      const [row, choices] = await Promise.all([labRequest<ExpenseDetail>(`expenses/${id}`), labRequest<LabContext>("context")]);
      if (!alive.current) return;
      setContext(choices); setCurrent(row); setDraft(copyExpense(row)); dirtyRef.current = false; setDirty(false);
      setReviewed(true); setConflict(false); setUncertain(false);
      setStatus(`Opened expense proposal ${row.id}, revision ${row.revision}. Proposal only — not approved, paid, or posted.`);
    });
  }
  function reviewProposal() {
    if (errors.length) { setReviewed(false); setError(errors.join(" ")); return; }
    setReviewed(true); setError("");
    setStatus(`Reviewed explicit ${draft.proposalType === "expense" ? "expense" : "petty-cash"} inputs. Proposed total ${total ?? "—"} QAR across ${draft.entries.length} entered rows; missing receipts and business units are informational.`);
  }
  async function saveProposal() {
    if (!reviewed || errors.length || uncertain || conflict) { setError(errors.join(" ") || "Review the explicit proposal before saving."); return; }
    await run(async () => {
      let writeAttempted = false;
      try {
        const freshContext = await labRequest<LabContext>("context");
        if (!alive.current) return;
        setContext(freshContext);
        const freshErrors = validateExpense(draft, freshContext);
        if (freshErrors.length) {
          setReviewed(false);
          throw new Error(`Saved choices or inputs need correction after context refresh. Original choices remain visible. ${freshErrors.join(" ")}`);
        }
        const payload: ExpensePayload = payloadFromExpense(draft, freshContext);
        writeAttempted = true;
        const saved = await labRequest<ExpenseDetail>(
          current ? `expenses/${current.id}` : "expenses", current ? "PUT" : "POST",
          { ...payload, ...(current ? { expected_revision: current.revision } : {}) },
        );
        if (!alive.current) return;
        setCurrent(saved); setDraft(copyExpense(saved)); dirtyRef.current = false; setDirty(false);
        setConflict(false); setUncertain(false); setReviewed(true);
        setStatus(`Saved expense proposal ${saved.id}, revision ${saved.revision}. It remains not approved, not paid, and not posted.`);
        try { await refreshList(); }
        catch (cause) {
          if (cause instanceof LabError && cause.status === 403) throw cause;
          if (alive.current) setError("Proposal saved successfully, but the saved-list refresh failed. Refresh the list; do not create a replacement.");
        }
      } catch (cause) {
        if (alive.current && cause instanceof LabError && cause.status === 409) setConflict(true);
        if (alive.current && writeAttempted && (!(cause instanceof LabError) || cause.status >= 500)) {
          setUncertain(true);
          setStatus("Save response unavailable; the database may have committed. Refresh the list, then explicitly reopen a saved proposal before another save.");
        }
        throw cause;
      }
    });
  }
  function updateEntry(index: number, patch: Partial<ExpenseEntryDraft>) {
    change({ ...draft, entries: draft.entries.map((entry, row) => row === index ? { ...entry, ...patch } : entry) });
  }

  return <main className="finance-draft-prototype finance-lab allocation-lab expense-lab" aria-labelledby="expense-title" data-testid="finance-expense-lab">
    <header className="finance-draft-banner">
      <p className="finance-draft-eyebrow">TIVOLI FINANCE · ISOLATED REVIEW WORKSHEET</p>
      <h1 id="expense-title">Expense proposals</h1>
      <p>Record explicit expense or petty-cash inputs for reviewer discussion. QAR is fixed; each entered row remains visible and independently described.</p>
      <p><strong>Proposal only.</strong> Synthetic text references are not evidence verification, custody assignment, or employee lookup. Nothing is approved, paid, or posted.</p>
      <a href="/lab-ui/">Return to Finance draft lab</a>
    </header>
    {error && <p className="expense-alert" role="alert">{error}</p>}
    <p className="expense-live" role="status" aria-live="polite">{busy ? "Working with the isolated expense lab…" : status}</p>
    {!authenticated && <section className="finance-draft-card"><h2>Isolated synthetic access</h2>
      <p>Use the existing Finance draft lab to open the synthetic test session and configure saved accounts, business units, and dimension values. This worksheet creates no session or setup.</p>
      <a href="/lab-ui/">Open Finance draft lab setup</a>
    </section>}
    {authenticated && <>
      <section className="finance-draft-card" aria-label="Saved expense proposals">
        <div className="expense-section-heading"><div><p className="finance-draft-eyebrow">SAVED WORKSHEETS</p><h2>Proposal register</h2></div>
          <div className="expense-toolbar">
            <button type="button" className="expense-secondary" disabled={busy} onClick={() => void run(refreshList)}>Refresh saved list</button>
            <button type="button" disabled={busy || !context || uncertain} onClick={newProposal}>New expense proposal</button>
          </div>
        </div>
        {!proposals.length ? <p>No saved proposals yet. Add synthetic setup choices in the existing Finance draft lab before creating a worksheet.</p> :
          <ul className="expense-register-list">{proposals.map((item) => <li key={item.id}>
            <button type="button" className="expense-secondary" disabled={busy} aria-label={`Open expense proposal ${item.id}: ${item.name}`} onClick={() => void openProposal(item.id)}>
              {item.name} · {item.proposal_type.replace("_", " ")} · revision {item.revision}
            </button>
          </li>)}</ul>}
      </section>
      {!context && <section className="finance-draft-card"><h2>Saved setup is unavailable</h2>
        <p>Account, business-unit, or dimension choices are unavailable. Configure synthetic choices in the existing lab; none are created here.</p><a href="/lab-ui/">Open draft lab setup</a>
      </section>}
      {context && <fieldset className="expense-editor" disabled={busy} aria-busy={busy} data-dirty={dirty} data-testid="expense-editor">
        <legend>{current ? `Editing expense proposal ${current.id}, revision ${current.revision}` : "New unsaved expense proposal"}</legend>
        <aside className="expense-boundary"><strong>Explicit input boundary</strong>
          <p>No category mapping, tax, threshold, cash limit, eligibility, calendar cutoff, account inference, offsetting journal, approval, payment, or posting is applied. A blank business unit means “Not proposed.”</p>
        </aside>
        {!accounts.length && <p role="note">No saved accounts are available. Configure synthetic choices in the <a href="/lab-ui/">draft lab setup</a>; none are created here.</p>}
        <section className="expense-meta-grid" aria-label="Proposal details">
          <label>Proposal name <input aria-label="Expense proposal name" maxLength={200} value={draft.name} onChange={(event) => change({ ...draft, name: event.target.value })} /></label>
          <label>Proposal type <select aria-label="Proposal type" value={draft.proposalType} onChange={(event) => change({ ...draft, proposalType: event.target.value as ExpenseDraft["proposalType"] }, true)}>
            <option value="">Choose a proposal type</option><option value="expense">Expense</option><option value="petty_cash">Petty cash</option>
          </select></label>
          <label className="expense-wide">Proposal explanation <textarea aria-label="Proposal explanation" maxLength={2000} value={draft.explanation} onChange={(event) => change({ ...draft, explanation: event.target.value })} /></label>
          <div className="expense-currency"><span>Currency</span><strong>QAR</strong><span>Fixed · no conversion</span></div>
        </section>
        {draft.proposalType === "" && <p className="expense-note" role="note">Select expense or petty cash explicitly before review. No proposal type is assumed.</p>}
        {draft.proposalType !== "" && <p className="expense-note" role="note">Type-specific reference reminder: confirm that each synthetic claimant/custodian reference is appropriate to this proposal type. It does not identify or assign a real person.</p>}
        <section aria-label="Expense proposal entries">
          <div className="expense-section-heading"><div><p className="finance-draft-eyebrow">EXPLICIT ROWS · 1–100</p><h2>Proposed entries</h2></div>
            <button type="button" className="expense-secondary" disabled={draft.entries.length >= 100} onClick={() => change({ ...draft, entries: [...draft.entries, emptyExpenseEntry()] })}>Add proposal row</button>
          </div>
          {draft.entries.map((entry, index) => <fieldset className="expense-row" key={index}>
            <legend>Entry {index + 1}</legend>
            <label>Expense date
              <input type="date" aria-label={`Expense date ${index + 1}`} value={entry.expenseDate} onChange={(event) => updateEntry(index, { expenseDate: event.target.value })} />
            </label>
            <label>Claimant / custodian reference
              <input aria-label={`Claimant/custodian reference ${index + 1}`} maxLength={200} value={entry.partyReference} onChange={(event) => updateEntry(index, { partyReference: event.target.value })} />
            </label>
            <label>Proposed amount · QAR
              <input inputMode="decimal" aria-label={`Expense amount ${index + 1}`} value={entry.amount} onChange={(event) => updateEntry(index, { amount: event.target.value })} />
            </label>
            <label className="expense-wide">Expense description
              <textarea aria-label={`Expense description ${index + 1}`} maxLength={2000} value={entry.description} onChange={(event) => updateEntry(index, { description: event.target.value })} />
            </label>
            <label>Saved account · required
              <select aria-label={`Expense account ${index + 1}`} value={entry.accountId} onChange={(event) => updateEntry(index, { accountId: event.target.value })}>
                <option value="">Choose an account</option>
                {entry.accountId && !accounts.some((item) => String(item.id) === entry.accountId) && <option value={entry.accountId}>Unavailable saved choice ({entry.accountId}) — choose another</option>}
                {accounts.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name}</option>)}
              </select>
            </label>
            <label>Business unit · optional
              <select aria-label={`Expense business unit ${index + 1}`} value={entry.businessUnitId} onChange={(event) => updateEntry(index, { businessUnitId: event.target.value })}>
                <option value="">Not proposed</option>
                {entry.businessUnitId && !units.some((item) => String(item.id) === entry.businessUnitId) && <option value={entry.businessUnitId}>Unavailable saved choice ({entry.businessUnitId}) — choose another</option>}
                {units.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name}</option>)}
              </select>
            </label>
            <label>Dimensions · optional; select saved values
              <select multiple aria-label={`Expense dimensions ${index + 1}`} value={entry.dimensionIds} onChange={(event) => updateEntry(index, { dimensionIds: [...event.target.selectedOptions].map((option) => option.value) })}>
                {entry.dimensionIds.filter((id) => !dimensions.some((item) => String(item.id) === id)).map((id) => <option key={`unavailable-${id}`} value={id}>Unavailable saved choice ({id}) — remove or replace</option>)}
                {dimensions.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name}</option>)}
              </select>
            </label>
            <label className="expense-wide">Synthetic receipt reference · optional text only
              <input aria-label={`Synthetic receipt reference ${index + 1}`} maxLength={200} value={entry.receiptReference} onChange={(event) => updateEntry(index, { receiptReference: event.target.value })} />
            </label>
            <button type="button" className="expense-secondary" disabled={draft.entries.length <= 1} aria-label={`Remove proposal row ${index + 1}`} onClick={() => change({ ...draft, entries: draft.entries.filter((_, row) => row !== index) })}>Remove entry {index + 1}</button>
          </fieldset>)}
        </section>
        <section className="expense-total" aria-label="Proposed total">
          <span>Exact proposed total · each entered row once</span><strong data-testid="expense-proposed-total">{total === null ? "Enter valid amounts" : `${total} QAR`}</strong>
        </section>
        <p className="expense-note">Arithmetic only. Zero is a valid draft amount. No inferred eligibility, limit, tax, or account mapping.</p>
        <section className="expense-review" aria-label="Entry review" aria-live="polite">
          <h3>Row-by-row notes</h3>
          <p>Missing synthetic receipt references and unproposed business units are informational only; neither is a rejection or evidence check.</p>
          <ul>{draft.entries.map((row, index) => {
            const number = index + 1;
            const hasReceipt = row.receiptReference.trim().length > 0;
            const hasUnit = row.businessUnitId.trim().length > 0;
            return <li className="expense-review-row" key={index}><strong>Entry {number}</strong> · {row.expenseDate || "Date not entered"} · {row.amount || "Amount not entered"} QAR · {row.partyReference || "Reference not entered"}
              <ul>
                {!hasReceipt && <li>No synthetic receipt reference in the current draft · information only</li>}
                {!hasUnit && <li>Business unit not proposed in the current draft · information only</li>}
                {hasReceipt && <li>Synthetic receipt reference entered · not verified evidence</li>}
                {hasUnit && <li>Business unit explicitly proposed</li>}
              </ul>
            </li>;
          })}</ul>
          {current && <p>Saved review at revision {current.revision}: {current.review.entry_count} entries · {current.review.missing_receipt_rows.length} missing receipt references · {current.review.unproposed_business_unit_rows.length} rows without proposed business unit.</p>}
        </section>
        {errors.length > 0 && <div className="expense-note" role="note" aria-label="Proposal validation">
          <strong>Complete these explicit inputs before saving</strong><ul>{errors.map((issue) => <li key={issue}>{issue}</li>)}</ul>
        </div>}
        <div className="expense-actions">
          <button type="button" className="expense-secondary" disabled={busy} onClick={reviewProposal}>Review proposal</button>
          <button type="button" disabled={busy || !reviewed || errors.length > 0 || uncertain || conflict} onClick={() => void saveProposal()}>{current ? "Save proposal revision" : "Save expense proposal"}</button>
        </div>
        {uncertain && <p role="alert">Unknown outcome blocks another save or new proposal. Refresh the list, then explicitly reopen a saved proposal to recover.</p>}
      </fieldset>}
      {conflict && current && <section className="finance-draft-card" role="alert">
        <h2>Saved revision changed</h2><p>Your local inputs remain unchanged. Reload only after explicit confirmation.</p>
        <button type="button" className="expense-secondary" disabled={busy} onClick={() => {
          if (window.confirm("Discard local expense proposal edits and reload the saved version?")) void openProposal(current.id, true);
        }}>Reload saved proposal</button>
      </section>}
      {current && <section className="finance-draft-card expense-history" aria-label="Expense proposal revision history" data-testid="expense-revision-history">
        <h2>Saved revision history</h2><p>Snapshots record proposal inputs only; approval, payment, and posting remain false.</p>
        {current.history.map((item) => <details key={item.revision}>
          <summary>Revision {item.revision} — {item.snapshot.name} · {item.snapshot.review.proposed_total} QAR · {item.snapshot.review.entry_count} entries</summary>
          <p>{item.created_at} · {item.snapshot.proposal_type.replace("_", " ")} · explanation: {item.snapshot.explanation || "None entered"}</p>
          <ul>{item.snapshot.entries.map((entry, index) => <li key={index}>Entry {index + 1}: {entry.expense_date} · {entry.amount} QAR · {entry.party_reference} · {entry.description} · account ID {entry.account_id}{entry.business_unit_id === null ? " · business unit not proposed" : ` · business unit ID ${entry.business_unit_id}`}{entry.receipt_reference ? ` · synthetic receipt reference: ${entry.receipt_reference}` : ""}</li>)}</ul>
        </details>)}
      </section>}
    </>}
  </main>;
}
