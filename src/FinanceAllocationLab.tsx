import { useEffect, useMemo, useRef, useState } from "react";
import type { LabContext } from "./finance-draft-lab-api";
import { LabError, labRequest } from "./finance-draft-lab-api";
import {
  allocationTotals, emptyAllocationLine, emptyDestination, validateJournal, validateProposal,
} from "./finance-allocation";
import type {
  AllocationLineDraft, AllocationList, DestinationDraft, SavedAllocation,
} from "./finance-allocation";
import { AllocationDestinationFields, AllocationJournalFields } from "./FinanceAllocationFields";
import "./finance-allocation.css";
import { reopenFromQuery } from "./finance-lab-deep-link";

const blankMeta = () => ({ title: "", titleAr: "", sourceAmount: "", currency: "", date: "", explanation: "" });
const accountChoices = (context: LabContext) => context.accounts.map((row) => ({ id: row.id, code: row.code, name: row.name_en }));
const unitChoices = (context: LabContext) => context.business_units.map((row) => ({ id: row.id, code: row.code, name: row.name }));
const dimensionChoices = (context: LabContext) => context.dimension_values.map((row) => ({
  id: row.id, code: row.code, label: row.label_en,
}));

function responseDraft(row: SavedAllocation, context: LabContext) {
  const lines: AllocationLineDraft[] = row.lines.map((line) => ({
    accountId: String(line.account_id),
    businessUnitId: line.business_unit_id === null ? "" : String(line.business_unit_id),
    dimensionIds: line.dimension_value_ids.map(String),
    debit: line.debit, credit: line.credit, memo: line.memo_en ?? "",
    memoAr: line.memo_ar ?? "",
  }));
  const destinations: DestinationDraft[] = row.allocation_proposal.destinations.map((destination) => ({
    amount: destination.amount,
    accountId: String(destination.account_id),
    businessUnitId: destination.business_unit_id === null ? "" : String(destination.business_unit_id),
    dimensionIds: destination.dimension_value_ids.map(String),
  }));
  // Keep ids intact, but surface a stale choice instead of silently remapping it.
  const hasForeignChoice = [
    ...lines.map((line) => ({
      account: line.accountId, unit: line.businessUnitId, dimensions: line.dimensionIds,
    })),
    ...destinations.map((row) => ({
      account: row.accountId, unit: row.businessUnitId, dimensions: row.dimensionIds,
    })),
  ].some((line) =>
    !context.accounts.some((choice) => String(choice.id) === line.account) ||
    (!!line.unit && !context.business_units.some((choice) => String(choice.id) === line.unit)) ||
    line.dimensions.some((id) => !context.dimension_values.some((choice) => String(choice.id) === id)));
  return {
    meta: {
      title: row.title_en, titleAr: row.title_ar ?? "", sourceAmount: row.allocation_proposal.source_amount,
      currency: row.allocation_proposal.currency, date: row.allocation_proposal.date,
      explanation: row.allocation_proposal.explanation,
    },
    lines, destinations, hasForeignChoice,
  };
}

export function FinanceAllocationLab() {
  const [authenticated, setAuthenticated] = useState(false);
  const [context, setContext] = useState<LabContext | null>(null);
  const [proposals, setProposals] = useState<AllocationList["proposals"]>([]);
  const [current, setCurrent] = useState<SavedAllocation | null>(null);
  const [meta, setMeta] = useState(blankMeta);
  const [lines, setLines] = useState<AllocationLineDraft[]>([emptyAllocationLine(), emptyAllocationLine()]);
  const [destinations, setDestinations] = useState<DestinationDraft[]>([emptyDestination()]);
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [conflict, setConflict] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false);
  const pendingRef = useRef(false);
  const alive = useRef(true);
  const accounts = useMemo(() => context ? accountChoices(context) : [], [context]);
  const units = useMemo(() => context ? unitChoices(context) : [], [context]);
  const values = useMemo(() => context ? dimensionChoices(context) : [], [context]);

  function markDirty() { dirtyRef.current = true; setDirty(true); setReviewed(false); }
  useEffect(() => {
    alive.current = true;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current || pendingRef.current) event.preventDefault();
    };
    window.addEventListener("beforeunload", beforeUnload);
    void labRequest<{ authenticated: boolean }>("session").then(async (session) => {
      if (!alive.current) return;
      setAuthenticated(session.authenticated);
    if (session.authenticated) {
      await loadContextAndList();
      if (alive.current) reopenFromQuery((id) => { void openProposal(id); }, setError);
    }
    }).catch((e: Error) => { if (alive.current) setError(e.message); });
    return () => { alive.current = false; window.removeEventListener("beforeunload", beforeUnload); };
  }, []);

  async function loadContextAndList() {
    const [choices, list] = await Promise.all([
      labRequest<LabContext>("context"), labRequest<AllocationList>("allocations"),
    ]);
    if (!alive.current) return;
    setContext(choices); setProposals(list.proposals);
  }
  async function refreshList() {
    const list = await labRequest<AllocationList>("allocations");
    if (alive.current) setProposals(list.proposals);
  }
  async function run(action: () => Promise<void>) {
    if (pendingRef.current) return;
    pendingRef.current = true; setBusy(true); setError("");
    try { await action(); }
    catch (cause) {
      if (alive.current) {
        if (cause instanceof LabError && cause.status === 403) {
          setAuthenticated(false); setContext(null); setProposals([]); setCurrent(null);
          setMeta(blankMeta()); setLines([emptyAllocationLine(), emptyAllocationLine()]);
          setDestinations([emptyDestination()]); setDirty(false); dirtyRef.current = false;
          setStatus("Synthetic test access is unavailable.");
          setError("Synthetic test access is unavailable. Protected allocation inputs were cleared.");
        } else setError(`${cause instanceof Error ? cause.message : "Allocation lab unavailable."} Inputs remain unchanged.`);
      }
    } finally { pendingRef.current = false; if (alive.current) setBusy(false); }
  }
  function mayReplace() {
    return (!dirtyRef.current && !pendingRef.current) ||
      (!pendingRef.current && window.confirm("Discard unsaved local allocation edits and open the saved version?"));
  }
  function newProposal() {
    if (uncertain) { setError("Unknown save outcome: refresh the list and explicitly reopen a saved allocation before starting another proposal."); return; }
    if (!mayReplace()) return;
    setCurrent(null); setMeta(blankMeta()); setLines([emptyAllocationLine(), emptyAllocationLine()]);
    setDestinations([emptyDestination()]); setReviewed(false); setConflict(false); setUncertain(false);
    dirtyRef.current = false; setDirty(false); setStatus("New allocation proposal — no accounting decision has been inferred.");
  }
  async function openProposal(id: number, confirmedReload = false) {
    if (!confirmedReload && !mayReplace()) return;
    await run(async () => {
      const [row, choices] = await Promise.all([
        labRequest<SavedAllocation>(`allocations/${id}`), labRequest<LabContext>("context"),
      ]);
      if (!alive.current) return;
      const draft = responseDraft(row, choices);
      setContext(choices); setCurrent(row); setMeta(draft.meta); setLines(draft.lines); setDestinations(draft.destinations);
      setReviewed(true); setConflict(false); setUncertain(false); dirtyRef.current = false; setDirty(false);
      setStatus(`Opened saved allocation proposal ${row.id}, revision ${row.revision}.`);
      if (draft.hasForeignChoice) setError("A saved option is unavailable in the current context. The original IDs are retained; refresh the context or contact the lab reviewer before saving.");
    });
  }
  function buildPayload() {
    if (!context) throw new Error("Refresh the lab context before saving.");
    function requireChoice(rawId: string, rows: { id: number }[], description: string): number {
      const id = Number(rawId);
      if (!rawId || !Number.isSafeInteger(id) || !rows.some((row) => row.id === id)) {
        throw new Error(`${description} is not an available saved option. Refresh choices; no substitution was made.`);
      }
      return id;
    }
    return {
      title_en: meta.title.trim(),
      ...(meta.titleAr.trim() ? { title_ar: meta.titleAr } : {}),
      lines: lines.map((line) => ({
        account_id: requireChoice(line.accountId, context.accounts, "Journal account"),
        business_unit_id: line.businessUnitId ? requireChoice(line.businessUnitId, context.business_units, "Journal business unit") : null,
        dimension_value_ids: line.dimensionIds.map((id) => requireChoice(id, context.dimension_values, "Journal dimension")),
        debit: line.debit || "0", credit: line.credit || "0",
        ...(line.memo.trim() ? { memo_en: line.memo.trim() } : {}),
        ...(line.memoAr ? { memo_ar: line.memoAr } : {}),
      })),
      allocation_proposal: {
        source_amount: meta.sourceAmount.trim(), currency: meta.currency,
        date: meta.date, explanation: meta.explanation.trim(),
        destinations: destinations.map((row) => ({
          amount: row.amount.trim(),
          account_id: requireChoice(row.accountId, context.accounts, "Destination account"),
          business_unit_id: row.businessUnitId ? requireChoice(row.businessUnitId, context.business_units, "Destination business unit") : null,
          dimension_value_ids: row.dimensionIds.map((id) => requireChoice(id, context.dimension_values, "Destination dimension")),
        })),
      },
    };
  }
  function reviewProposal() {
    const errors = validateProposal({ ...meta, destinations });
    const journal = validateJournal(lines, accounts.map((row) => row.id));
    const choicesValid = lines.every((line) => line.accountId && accounts.some((a) => String(a.id) === line.accountId) &&
      (!line.businessUnitId || units.some((unit) => String(unit.id) === line.businessUnitId)) &&
      line.dimensionIds.every((id) => values.some((value) => String(value.id) === id))) &&
      destinations.every((row) => accounts.some((a) => String(a.id) === row.accountId) &&
        (!row.businessUnitId || units.some((unit) => String(unit.id) === row.businessUnitId)) &&
        row.dimensionIds.every((id) => values.some((value) => String(value.id) === id)));
    if (!choicesValid) errors.push("One or more selected options are unavailable in the current lab context.");
    setReviewed(true);
    setError([...errors, ...journal.issues.map((issue) => issue.en)].join(" "));
    if (!errors.length && journal.balanced) setStatus("Reviewed proposal inputs. Remaining amount is allowed; no posting or mapping is performed.");
  }
  async function saveProposal() {
    if (!reviewed || !context || uncertain) return;
    const errors = validateProposal({ ...meta, destinations });
    const journal = validateJournal(lines, accounts.map((row) => row.id));
    if (errors.length || !journal.balanced) { setError([...errors, ...journal.issues.map((issue) => issue.en)].join(" ")); return; }
    let payload;
    try { payload = buildPayload(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Invalid saved choice."); return; }
    await run(async () => {
      try {
        const row = await labRequest<SavedAllocation>(
          current ? `allocations/${current.id}` : "allocations",
          current ? "PUT" : "POST",
          { ...payload, ...(current ? { expected_revision: current.revision } : {}) },
        );
        if (!alive.current) return;
        const normalized = responseDraft(row, context);
        setCurrent(row); setConflict(false); setUncertain(false); dirtyRef.current = false; setDirty(false);
        setMeta(normalized.meta); setLines(normalized.lines); setDestinations(normalized.destinations);
        setReviewed(true);
        if (normalized.hasForeignChoice) setError("The saved response includes an option unavailable in the current context. The original IDs are retained.");
        setStatus(`Saved allocation proposal ${row.id}, revision ${row.revision}. Proposal only — not posted.`);
        try { await refreshList(); }
        catch { if (alive.current) setError("Allocation saved, but list refresh failed. Refresh the list; do not create a replacement."); }
      } catch (cause) {
        if (alive.current && cause instanceof LabError && cause.status === 409) setConflict(true);
        if (alive.current && !(cause instanceof LabError)) {
          setUncertain(true);
          setStatus("Save response unavailable; the database may have committed. Refresh the list and explicitly reopen before another save.");
        }
        throw cause;
      }
    });
  }
  const totals = allocationTotals(meta.sourceAmount, destinations);
  const journalReview = validateJournal(lines, accounts.map((row) => row.id));
  const saveDisabled = busy || uncertain || !reviewed || !context ||
    conflict || validateProposal({ ...meta, destinations }).length > 0 || !journalReview.balanced;

  return <main className="finance-draft-prototype finance-lab allocation-lab" aria-labelledby="allocation-title">
    <header className="finance-draft-banner allocation-banner">
      <p className="finance-draft-eyebrow">TIVOLI FINANCE · ISOLATED REVIEW WORKSHEET</p>
      <h1 id="allocation-title">Allocation proposals</h1>
      <p>Record explicit source details and proposed destinations. Totals reflect entered amounts only; no allocation formula, account mapping, approval or posting is inferred.</p>
      <p><strong>Proposal only.</strong> This isolated lab uses the synthetic test session and disposable data.</p>
      <a href="/lab-ui/">Return to Finance draft lab</a>
    </header>
    {error && <p className="finance-draft-alert" role="alert">{error}</p>}
    <p role="status" aria-live="polite">{busy ? "Working with the isolated allocation lab…" : status}</p>
    {!authenticated && <section className="finance-draft-card">
      <h2>Isolated synthetic access</h2>
      <p>Use the existing Finance draft lab to open the synthetic test session and configure saved accounts, business units, and dimensions.</p>
      <a href="/lab-ui/">Open Finance draft lab setup</a>
    </section>}
    {authenticated && <>
      <section className="finance-draft-card allocation-register" aria-label="Saved allocation proposals">
        <div className="allocation-section-heading"><div><p className="finance-draft-eyebrow">SAVED PROPOSALS</p><h2>Register</h2></div>
          <div className="allocation-toolbar">
            <button disabled={busy} onClick={() => void run(refreshList)}>Refresh saved list</button>
            <button disabled={busy || !context} onClick={newProposal}>New allocation proposal</button>
          </div>
        </div>
        {!proposals.length ? <p>No saved allocation proposals yet. Create a proposal when the synthetic context is ready.</p> :
          <ul>{proposals.map((row) => <li key={row.id}><button disabled={busy} onClick={() => void openProposal(row.id)}>Open allocation proposal {row.id}: {row.title_en} — revision {row.revision}</button></li>)}</ul>}
      </section>
      {!context && <section className="finance-draft-card"><h2>Saved setup is unavailable</h2>
        <p>No account, business-unit, or dimension choices are seeded here. Set up synthetic choices in the existing draft lab.</p><a href="/lab-ui/">Open draft lab setup</a>
      </section>}
      {context && <fieldset className="allocation-editor finance-lab-editor" disabled={busy} aria-busy={busy} data-dirty={dirty}
        onChangeCapture={markDirty}>
        <legend>{current ? `Editing allocation proposal ${current.id}, base revision ${current.revision}` : "New unsaved allocation proposal"}</legend>
        <aside className="finance-draft-boundary"><strong>Explicit inputs only</strong><p>Allocation destinations do not create or modify journal lines. Underallocated proposals remain drafts with a visible remainder.</p></aside>
        {!accounts.length && <p role="note">No saved accounts are available. Add synthetic choices in the <a href="/lab-ui/">draft lab setup</a>; this page does not seed choices.</p>}
        <section className="allocation-metadata" aria-label="Allocation source metadata">
          <h2>Source and review details</h2>
          <label>English title<input aria-label="Allocation title" value={meta.title} onChange={(e) => { setMeta({ ...meta, title: e.target.value }); markDirty(); }} /></label>
          <label>Arabic title (optional)<input aria-label="Allocation title Arabic (optional)" value={meta.titleAr} onChange={(e) => { setMeta({ ...meta, titleAr: e.target.value }); markDirty(); }} /></label>
          <label>Source amount<input aria-label="Allocation source amount" inputMode="decimal" value={meta.sourceAmount} onChange={(e) => { setMeta({ ...meta, sourceAmount: e.target.value }); markDirty(); }} /></label>
          <label>Currency — explicit, no conversion<input aria-label="Allocation currency" value={meta.currency} onChange={(e) => { setMeta({ ...meta, currency: e.target.value }); markDirty(); }} placeholder="Enter currency (QAR only)" /></label>
          <p>QAR is currently the only supported currency. No FX rate or conversion is inferred.</p>
          <label>Proposed date<input aria-label="Allocation proposed date" type="date" value={meta.date} onChange={(e) => { setMeta({ ...meta, date: e.target.value }); markDirty(); }} /></label>
          <label>Explicit explanation<textarea aria-label="Allocation explanation" maxLength={2000} value={meta.explanation} onChange={(e) => { setMeta({ ...meta, explanation: e.target.value }); markDirty(); }} /></label>
        </section>
        <AllocationDestinationFields rows={destinations} accounts={accounts} units={units} values={values}
          update={(index, patch) => { setDestinations((rows) => rows.map((row, i) => i === index ? { ...row, ...patch } : row)); markDirty(); }}
          add={() => { setDestinations((rows) => [...rows, emptyDestination()]); markDirty(); }}
          remove={(index) => { setDestinations((rows) => rows.filter((_, i) => i !== index)); markDirty(); }} />
        <section className={`allocation-remainder${totals?.difference.startsWith("-") ? " is-overallocated" : ""}`} aria-live="polite">
          <span>Source</span><strong>{totals?.source ?? "—"} {meta.currency || "currency not selected"}</strong>
          <span>Explicit destination total</span><strong>{totals?.allocated ?? "—"}</strong>
          <span>{totals?.difference.startsWith("-") ? "Overallocated by" : "Unallocated remainder"}</span>
          <strong>{totals?.difference.startsWith("-") ? totals.difference.slice(1) : totals?.difference ?? "—"}</strong>
          <p>{totals?.difference.startsWith("-")
            ? "Overallocated — destination total exceeds the source. Correct the proposal before review or save."
            : totals && totals.difference !== "0.00"
              ? "Remaining — not fully allocated; not ready as a complete allocation."
              : "This total is arithmetic only; it does not create journal entries."}</p>
        </section>
        <AllocationJournalFields lines={lines} accounts={accounts} units={units} values={values}
          update={(index, patch) => { setLines((rows) => rows.map((row, i) => i === index ? { ...row, ...patch } : row)); markDirty(); }}
          add={() => { setLines((rows) => [...rows, emptyAllocationLine()]); markDirty(); }}
          remove={(index) => { setLines((rows) => rows.filter((_, i) => i !== index)); markDirty(); }} />
        <section className="allocation-journal-review" aria-label="Journal balance review">
          <h3>Independent journal balance</h3>
          <p>Debits {journalReview.debitTotal} QAR · Credits {journalReview.creditTotal} QAR · Difference {journalReview.difference} QAR</p>
          {journalReview.issues.length > 0 && <ul>{journalReview.issues.map((issue) => <li key={issue.code}>{issue.en}</li>)}</ul>}
          {journalReview.balanced && <p>Balanced journal review — not posted.</p>}
        </section>
        <div className="allocation-actions">
          <button type="button" disabled={busy} onClick={reviewProposal}>Review allocation proposal</button>
          <button type="button" disabled={saveDisabled} onClick={() => void saveProposal()}>Save allocation proposal</button>
          {uncertain && <p role="alert">Unknown outcome blocks automatic resubmission. Refresh the list, then explicitly reopen the saved proposal to recover.</p>}
        </div>
      </fieldset>}
      {conflict && current && <section role="alert" className="finance-draft-card">
        <p>Conflicting edit: your local proposal remains unchanged. Reload only after explicit confirmation.</p>
        <button disabled={busy} onClick={() => {
          if (window.confirm("Discard local allocation edits and reload the saved version?")) void openProposal(current.id, true);
        }}>Reload saved allocation</button>
      </section>}
      {current && <section className="finance-draft-card allocation-history" aria-label="Allocation proposal revision history">
        <h2>Saved revision history</h2><p>Preserved proposal snapshots; not approvals or posted entries.</p>
        {current.history.map((item) => <details key={item.revision}>
          <summary>Revision {item.revision} — {item.snapshot.title_en} · source {item.snapshot.review.source_amount} {item.snapshot.review.currency} · allocated {item.snapshot.review.allocated_total}</summary>
          <p>{item.created_at}</p><p>{item.snapshot.allocation_proposal.date} · {item.snapshot.allocation_proposal.explanation}</p>
          <p>Remainder {item.snapshot.review.difference} — {item.snapshot.review.state}</p>
          <ul>{item.snapshot.allocation_proposal.destinations.map((destination, index) => <li key={index}>Destination {index + 1}: {destination.amount} · account ID {destination.account_id}</li>)}</ul>
        </details>)}
      </section>}
    </>}
  </main>;
}
