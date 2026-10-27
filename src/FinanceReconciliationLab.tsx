import { useEffect, useRef, useState } from "react";
import { LabError, labRequest } from "./finance-draft-lab-api";
import { cents } from "./finance-allocation";
import {
  blankReconciliation, emptyReconciliationRow, payloadFromDraft, reconciliationMath,
  validateReconciliation,
} from "./finance-reconciliation";
import type { ReconciliationDraft, ReconciliationPayload, ReconciliationRowDraft } from "./finance-reconciliation";
import "./finance-allocation.css";
import "./finance-reconciliation.css";
import { reopenFromQuery } from "./finance-lab-deep-link";

type Review = {
  currency: "QAR"; balance_difference: string; statement_net: string; book_net: string;
  unmatched_statement_ids: string[]; unmatched_book_ids: string[];
  unmatched_statement_net: string; unmatched_book_net: string; unmatched_row_difference: string;
  comparison_residual: string; matched_count: number;
  warnings: { code: "balance_dates_differ"; message: string }[];
  posted: false; operational_reconciled: false;
};
type Detail = ReconciliationPayload & {
  id: number; revision: number; review: Review;
  history: { revision: number; created_at: string; snapshot: ReconciliationPayload & { review: Review } }[];
};
type List = { proposals: { id: number; name: string; revision: number }[] };

const copyPayload = (row: ReconciliationPayload): ReconciliationDraft => ({
  ...row, statement_rows: row.statement_rows.map((item) => ({ ...item })),
  book_rows: row.book_rows.map((item) => ({ ...item })), matches: row.matches.map((item) => ({ ...item })),
});

export function FinanceReconciliationLab() {
  const [authenticated, setAuthenticated] = useState(false);
  const [proposals, setProposals] = useState<List["proposals"]>([]);
  const [current, setCurrent] = useState<Detail | null>(null);
  const [draft, setDraft] = useState<ReconciliationDraft>(blankReconciliation);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [dirty, setDirty] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [pairStatement, setPairStatement] = useState("");
  const [pairBook, setPairBook] = useState("");
  const [pairExplanation, setPairExplanation] = useState("");
  const alive = useRef(true);
  const busyRef = useRef(false);
  const dirtyRef = useRef(false);
  const errors = validateReconciliation(draft);
  const math = reconciliationMath(draft);

  function clearProtectedData() {
    setAuthenticated(false); setProposals([]); setCurrent(null); setDraft(blankReconciliation());
    setPairStatement(""); setPairBook(""); setPairExplanation("");
    dirtyRef.current = false; setDirty(false); setReviewed(false); setConflict(false); setUncertain(false);
    setStatus("Synthetic test access is unavailable.");
    setError("Synthetic test access is unavailable. Protected reconciliation inputs were cleared.");
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
      const list = await labRequest<List>("reconciliations");
       if (alive.current) {
         setProposals(list.proposals);
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
      else setError(`${cause instanceof Error ? cause.message : "Reconciliation lab unavailable."} Inputs remain unchanged.`);
    } finally { busyRef.current = false; if (alive.current) setBusy(false); }
  }
  async function refreshList() {
    const result = await labRequest<List>("reconciliations");
    if (alive.current) setProposals(result.proposals);
  }
  function replaceAllowed() {
    return (!dirtyRef.current && !busyRef.current) ||
      (!busyRef.current && window.confirm("Discard unsaved reconciliation edits and open the selected saved version?"));
  }
  function change(next: ReconciliationDraft) {
    setDraft(next); dirtyRef.current = true; setDirty(true); setReviewed(false);
  }
  function newProposal() {
    if (uncertain) { setError("Unknown save outcome: refresh the list and explicitly reopen a saved proposal before starting or saving another."); return; }
    if (!replaceAllowed()) return;
    setCurrent(null); setDraft(blankReconciliation()); dirtyRef.current = false; setDirty(false);
    setReviewed(false); setConflict(false); setUncertain(false);
    setStatus("New explicit-input worksheet. No bank connection, approval, or operational reconciliation is performed.");
  }
  async function openProposal(id: number, confirmed = false) {
    if (!confirmed && !replaceAllowed()) return;
    await run(async () => {
      const row = await labRequest<Detail>(`reconciliations/${id}`);
      if (!alive.current) return;
      setCurrent(row); setDraft(copyPayload(row)); dirtyRef.current = false; setDirty(false);
      setReviewed(true); setConflict(false); setUncertain(false);
      setStatus(`Opened saved reconciliation proposal ${row.id}, revision ${row.revision}.`);
    });
  }
  function reviewDraft() {
    setReviewed(true);
    if (errors.length) { setError(errors.join(" ")); return; }
    setError("");
    setStatus("Reviewed explicit inputs. Arithmetic is descriptive only; it is not proof of reconciliation or approval.");
  }
  async function saveDraft() {
    if (!reviewed || errors.length || uncertain || conflict) { setError(errors.join(" ") || "Review the explicit proposal before saving."); return; }
    const payload = payloadFromDraft(draft);
    await run(async () => {
      try {
        const saved = await labRequest<Detail>(
          current ? `reconciliations/${current.id}` : "reconciliations",
          current ? "PUT" : "POST",
          { ...payload, ...(current ? { expected_revision: current.revision } : {}) },
        );
        if (!alive.current) return;
        setCurrent(saved); setDraft(copyPayload(saved)); dirtyRef.current = false; setDirty(false);
        setConflict(false); setUncertain(false); setReviewed(true);
        setStatus(`Saved proposal ${saved.id}, revision ${saved.revision}. Saved worksheet only — not posted or operationally reconciled.`);
        try { await refreshList(); }
        catch (cause) {
          if (cause instanceof LabError && cause.status === 403) throw cause;
          if (alive.current) setError("Proposal saved, but the saved-list refresh failed. Refresh the list; do not create a replacement.");
        }
      } catch (cause) {
        if (alive.current && cause instanceof LabError && cause.status === 409) setConflict(true);
        if (alive.current && (!(cause instanceof LabError) || cause.status >= 500)) {
          setUncertain(true);
          setStatus("Save response unavailable; the database may have committed. Refresh the list, then explicitly reopen a saved proposal before another save.");
        }
        throw cause;
      }
    });
  }

  function editRow(side: "statement_rows" | "book_rows", index: number, patch: Partial<ReconciliationRowDraft>) {
    change({ ...draft, [side]: draft[side].map((row, i) => i === index ? { ...row, ...patch } : row) });
  }
  function addRow(side: "statement_rows" | "book_rows") {
    if (draft[side].length >= 100) return;
    change({ ...draft, [side]: [...draft[side], emptyReconciliationRow()] });
  }
  function removeRow(side: "statement_rows" | "book_rows", rowId: string, index: number) {
    if (draft.matches.some((match) => side === "statement_rows" ? match.statement_row_id === rowId : match.book_row_id === rowId)) return;
    change({ ...draft, [side]: draft[side].filter((_, i) => i !== index) });
  }
  function addMatch() {
    change({ ...draft, matches: [...draft.matches, {
      statement_row_id: pairStatement, book_row_id: pairBook, explanation: pairExplanation,
    }] });
    setPairExplanation("");
  }
  function rowEditor(side: "statement_rows" | "book_rows", label: string) {
    const rows = draft[side];
    return <section className="rec-source" aria-label={`${label} source rows`}>
      <div className="rec-source-head"><div><p className="finance-draft-eyebrow">EXPLICIT SOURCE INPUT</p><h3>{label} rows</h3></div>
        <button type="button" aria-label={`Add ${label.toLowerCase()} row`} disabled={busy || rows.length >= 100} onClick={() => addRow(side)}>Add {label.toLowerCase()} row</button></div>
      <p>Enter source row IDs yourself. No bank reference format is assumed. Dates are not constrained to the balance date.</p>
      {rows.map((row, index) => {
        const matched = draft.matches.some((match) => side === "statement_rows" ? match.statement_row_id === row.row_id : match.book_row_id === row.row_id);
        return <fieldset className="rec-row" key={`${side}-${index}`} aria-label={`${label} source row ${index + 1}`} data-testid={`reconciliation-row-${side === "statement_rows" ? "statement" : "book"}-${index + 1}`}><legend>{label} row {index + 1}{row.row_id ? ` · ${row.row_id}` : ""}</legend>
          <label>Explicit row ID <input aria-label={`${label} row ID ${index + 1}`} maxLength={64} value={row.row_id} onChange={(event) => editRow(side, index, { row_id: event.target.value })} /></label>
          <label>Source row date <input aria-label={`${label} row date ${index + 1}`} type="date" value={row.date} onChange={(event) => editRow(side, index, { date: event.target.value })} /></label>
          <label>Amount (QAR, non-negative) <input aria-label={`${label} row amount ${index + 1}`} inputMode="decimal" value={row.amount} onChange={(event) => editRow(side, index, { amount: event.target.value })} /></label>
          <label>Direction <select aria-label={`${label} row direction ${index + 1}`} value={row.direction} onChange={(event) => editRow(side, index, { direction: event.target.value as ReconciliationRowDraft["direction"] })}>
            <option value="">Choose direction</option><option value="increase">Increase</option><option value="decrease">Decrease</option></select></label>
          <label>Reference <input aria-label={`${label} row reference ${index + 1}`} maxLength={200} value={row.reference} onChange={(event) => editRow(side, index, { reference: event.target.value })} /></label>
          <label className="wide">Row explanation (optional)<textarea aria-label={`${label} row explanation ${index + 1}`} maxLength={2000} value={row.explanation} onChange={(event) => editRow(side, index, { explanation: event.target.value })} /></label>
          <button className="rec-remove" type="button" disabled={busy || matched} onClick={() => removeRow(side, row.row_id, index)} aria-describedby={matched ? `matched-${side}-${index}` : undefined}>Remove {label.toLowerCase()} row {index + 1}</button>
          {matched && <span id={`matched-${side}-${index}`} className="rec-match-alert">Unmatch this row explicitly before removing it.</span>}
        </fieldset>;
      })}
    </section>;
  }

  return <main className="finance-draft-prototype finance-lab allocation-lab reconciliation-lab" aria-labelledby="reconciliation-title" data-testid="finance-reconciliation-lab">
    <header className="finance-draft-banner allocation-banner">
      <p className="finance-draft-eyebrow">TIVOLI FINANCE · ISOLATED REVIEW WORKSHEET</p>
      <h1 id="reconciliation-title">Reconciliation proposals</h1>
      <p>A deliberate comparison of source rows and dated balance snapshots. Every input and every full-row match is explicit.</p>
      <p><strong>Proposal only.</strong> No bank connection, row completion, approval, or posting.</p>
      <a href="/lab-ui/">Return to Finance draft lab</a>
    </header>
    {error && <p className="finance-draft-alert" role="alert">{error}</p>}
    <p className="budget-live-status" role="status" aria-live="polite">{busy ? "Working with the isolated reconciliation lab…" : status}</p>
    {!authenticated && <section className="finance-draft-card budget-empty">
      <h2>Isolated synthetic access</h2>
      <p>Open the existing Finance draft lab to establish its synthetic test session. This worksheet never creates a session or setup.</p>
      <a href="/lab-ui/">Open Finance draft lab setup</a>
    </section>}
    {authenticated && <>
      <section className="finance-draft-card rec-register" aria-label="Saved reconciliation proposals">
        <div className="allocation-section-heading"><div><p className="finance-draft-eyebrow">SAVED WORKSHEETS</p><h2>Proposal register</h2></div>
          <div className="rec-toolbar">
            <button type="button" disabled={busy} onClick={() => void run(refreshList)}>Refresh saved list</button>
            <button type="button" disabled={busy || uncertain} onClick={newProposal}>New reconciliation proposal</button>
          </div></div>
        {!proposals.length ? <p className="budget-register-empty">No saved proposals yet. Start with manually entered balances and rows.</p> :
          <ul className="rec-register-list">{proposals.map((item) => <li key={item.id}>
            <button type="button" disabled={busy} onClick={() => void openProposal(item.id)}>Open proposal {item.id}: {item.name} — revision {item.revision}</button>
          </li>)}</ul>}
      </section>
      <fieldset className="rec-editor" disabled={busy} aria-busy={busy} data-dirty={dirty} data-testid="reconciliation-editor">
        <legend>{current ? `Editing proposal ${current.id}, base revision ${current.revision}` : "New unsaved reconciliation proposal"}</legend>
        <aside className="rec-boundary"><strong>Explicit-input boundary</strong>
          <p>Balances are snapshots, not opening balances. A row set is not assumed complete. No automatic matching, tolerance, netting, adjustment, completion, approval, or operational reconciliation occurs.</p>
        </aside>
        <section className="rec-meta" aria-label="Proposal details">
          <h2>Balance snapshots</h2>
          <label>Proposal name <input aria-label="Reconciliation proposal name" maxLength={200} value={draft.name} onChange={(event) => change({ ...draft, name: event.target.value })} /></label>
          <div className="rec-currency"><span>Currency</span><strong>QAR</strong><small>Fixed currency. No FX.</small></div>
          <label>Statement balance date <input aria-label="Statement balance date" type="date" value={draft.statement_date} onChange={(event) => change({ ...draft, statement_date: event.target.value })} /></label>
          <label>Statement balance (signed QAR) <input aria-label="Statement balance" inputMode="decimal" value={draft.statement_balance} onChange={(event) => change({ ...draft, statement_balance: event.target.value })} /></label>
          <label>Book balance date <input aria-label="Book balance date" type="date" value={draft.book_date} onChange={(event) => change({ ...draft, book_date: event.target.value })} /></label>
          <label>Book balance (signed QAR) <input aria-label="Book balance" inputMode="decimal" value={draft.book_balance} onChange={(event) => change({ ...draft, book_balance: event.target.value })} /></label>
          <label className="wide">Proposal explanation (optional)<textarea aria-label="Reconciliation explanation" maxLength={2000} value={draft.explanation} onChange={(event) => change({ ...draft, explanation: event.target.value })} /></label>
        </section>
        {rowEditor("statement_rows", "Statement")}
        {rowEditor("book_rows", "Book")}
        <section className="rec-source" aria-label="Explicit full-row matches">
          <div className="rec-source-head"><div><p className="finance-draft-eyebrow">MANUAL PAIRS ONLY</p><h3>Full-row matches</h3></div>
            <span>{draft.matches.length} entered</span></div>
          <p>One-to-one, whole rows only. Exact magnitude and direction must agree. Dates and references may differ. No tolerance or partial/net matching.</p>
          <div className="rec-match">
            <label>Statement row ID <select aria-label="Match statement row" value={pairStatement} onChange={(event) => setPairStatement(event.target.value)}>
              <option value="">Choose entered statement row</option>{draft.statement_rows.map((row, index) => <option key={`${row.row_id}-${index}`} value={row.row_id}>{row.row_id || `(blank row ${index + 1})`}</option>)}</select></label>
            <label>Book row ID <select aria-label="Match book row" value={pairBook} onChange={(event) => setPairBook(event.target.value)}>
              <option value="">Choose entered book row</option>{draft.book_rows.map((row, index) => <option key={`${row.row_id}-${index}`} value={row.row_id}>{row.row_id || `(blank row ${index + 1})`}</option>)}</select></label>
            <label>Match explanation (optional)<textarea aria-label="Match explanation" maxLength={2000} value={pairExplanation} onChange={(event) => setPairExplanation(event.target.value)} /></label>
            <button type="button" aria-label="Add manual match" disabled={busy || draft.matches.length >= 100} onClick={addMatch}>Add explicit match</button>
          </div>
          {draft.matches.length === 0 ? <p>No rows are matched. Nothing is inferred.</p> : draft.matches.map((match, index) => {
            const statement = draft.statement_rows.find((row) => row.row_id === match.statement_row_id);
            const book = draft.book_rows.find((row) => row.row_id === match.book_row_id);
            const valid = !!statement && !!book && statement.amount !== "" && book.amount !== "" &&
              cents(statement.amount) !== null && cents(statement.amount) === cents(book.amount) &&
              statement.direction === book.direction;
            const repeated = draft.matches.some((item, i) => i !== index &&
              (item.statement_row_id === match.statement_row_id || item.book_row_id === match.book_row_id));
            return <article className="rec-match" key={`match-${index}`} data-testid={`reconciliation-match-${index + 1}`}>
              <div><strong>Pair {index + 1}</strong><p>Statement: {match.statement_row_id || "(blank / dangling)"} ↔ Book: {match.book_row_id || "(blank / dangling)"}</p>
                <p>{match.explanation || "No match explanation."}</p>
                {(!valid || repeated) && <p className="rec-match-alert" role="note">Invalid pair retained: {!statement || !book ? "missing or dangling source row" : repeated ? "row is paired more than once" : "amount or direction is incompatible"}. Correct it or remove the pair; it will not be silently dropped.</p>}</div>
              <button type="button" onClick={() => change({ ...draft, matches: draft.matches.filter((_, i) => i !== index) })}>Unmatch pair {index + 1}</button>
            </article>;
          })}
        </section>
        <section className="rec-formula" aria-label="Arithmetic-only comparison" aria-live="polite">
          <h2>Arithmetic-only comparison · QAR</h2>
          {math ? <dl className="rec-math">
            <div><dt>Statement row net (increases − decreases)</dt><dd aria-label="Statement row net result" data-testid="reconciliation-formula-statement-net">{math.statementNet}</dd></div>
            <div><dt>Book row net (increases − decreases)</dt><dd aria-label="Book row net result" data-testid="reconciliation-formula-book-net">{math.bookNet}</dd></div>
            <div><dt>Balance difference (statement snapshot − book snapshot)</dt><dd aria-label="Balance difference result" data-testid="reconciliation-formula-balance-difference">{math.balanceDifference}</dd></div>
            <div><dt>Unmatched statement row net</dt><dd aria-label="Unmatched statement row net result" data-testid="reconciliation-formula-unmatched-statement-net">{math.unmatchedStatementNet}</dd></div>
            <div><dt>Unmatched book row net</dt><dd aria-label="Unmatched book row net result" data-testid="reconciliation-formula-unmatched-book-net">{math.unmatchedBookNet}</dd></div>
            <div><dt>Unmatched row difference (statement − book)</dt><dd aria-label="Unmatched row difference result" data-testid="reconciliation-formula-unmatched-row-difference">{math.unmatchedRowDifference}</dd></div>
            <div><dt>Comparison residual (balance difference − unmatched row difference)</dt><dd aria-label="Comparison residual result" data-testid="reconciliation-formula-comparison-residual">{math.comparisonResidual}</dd></div>
            <div><dt>Unmatched statement IDs</dt><dd aria-label="Unmatched statement row IDs" data-testid="reconciliation-formula-unmatched-statement-ids">{math.unmatchedStatementIds.join(", ") || "None"}</dd></div>
            <div><dt>Unmatched book IDs</dt><dd aria-label="Unmatched book row IDs" data-testid="reconciliation-formula-unmatched-book-ids">{math.unmatchedBookIds.join(", ") || "None"}</dd></div>
          </dl> : <p>Enter valid balances and row amounts to calculate exact minor-unit arithmetic.</p>}
          <p>Zero residual is not proof of reconciliation or approval. Row sets are not assumed complete; balances are snapshots, not opening balances.</p>
          {draft.statement_date && draft.book_date && draft.statement_date !== draft.book_date &&
            <p role="note">Balance dates differ. Arithmetic-only advisory; no date cutoff or rejection policy is inferred.</p>}
          {current?.review.warnings.map((warning, index) => <p role="note" key={`${warning.code}-${index}`}>{warning.message}</p>)}
        </section>
        {errors.length > 0 && <div className="rec-validation" role="note" aria-label="Proposal validation">
          <strong>Resolve explicit input issues before saving</strong><ul>{errors.map((issue, index) => <li key={`${index}-${issue}`}>{issue}</li>)}</ul>
        </div>}
        <div className="rec-actions">
          <button type="button" disabled={busy} onClick={reviewDraft}>Review proposal</button>
          <button type="button" disabled={busy || !reviewed || errors.length > 0 || uncertain || conflict} onClick={() => void saveDraft()}>{current ? "Save proposal revision" : "Save reconciliation proposal"}</button>
          {uncertain && <p role="alert">Unknown outcome blocks further writes/new proposals. Refresh the list, then explicitly reopen a saved proposal.</p>}
        </div>
      </fieldset>
      {conflict && current && <section role="alert" className="finance-draft-card rec-conflict">
        <h2>Saved revision changed</h2><p>Your local work remains unchanged. Reload only after explicit confirmation.</p>
        <button type="button" disabled={busy} onClick={() => {
          if (window.confirm("Discard local reconciliation edits and reload the saved version?")) void openProposal(current.id, true);
        }}>Reload saved proposal</button>
      </section>}
      {current && <section className="finance-draft-card rec-history" aria-label="Reconciliation proposal revision history">
        <h2>Saved revision history</h2><p>Snapshots preserve explicit source rows and explanations; they are not approval or posting records.</p>
        {current.history.map((item) => <details key={item.revision}>
          <summary>Revision {item.revision} — {item.snapshot.name}</summary>
          <p>{item.created_at} · Statement {item.snapshot.statement_date}: {item.snapshot.statement_balance} QAR · Book {item.snapshot.book_date}: {item.snapshot.book_balance} QAR</p>
          <h3>Statement source rows</h3><ul>{item.snapshot.statement_rows.map((row, index) => <li key={`${row.row_id}-${index}`}>{row.row_id} · {row.date} · {row.direction} {row.amount} QAR · {row.reference}{row.explanation ? ` · ${row.explanation}` : ""}</li>)}</ul>
          <h3>Book source rows</h3><ul>{item.snapshot.book_rows.map((row, index) => <li key={`${row.row_id}-${index}`}>{row.row_id} · {row.date} · {row.direction} {row.amount} QAR · {row.reference}{row.explanation ? ` · ${row.explanation}` : ""}</li>)}</ul>
          <h3>Explicit matches</h3><ul>{item.snapshot.matches.map((match, index) => <li key={index}>{match.statement_row_id} ↔ {match.book_row_id}{match.explanation ? ` · ${match.explanation}` : ""}</li>)}</ul>
        </details>)}
      </section>}
    </>}
  </main>;
}
