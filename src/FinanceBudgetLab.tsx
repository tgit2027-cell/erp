import { useEffect, useMemo, useRef, useState } from "react";
import type { LabContext } from "./finance-draft-lab-api";
import { LabError, labRequest } from "./finance-draft-lab-api";
import {
  blankBudgetScenario, budgetTotal, budgetWarnings, emptyBudgetEntry, validateBudget,
} from "./finance-budget";
import type { BudgetEntryDraft, BudgetPayload, BudgetScenarioDraft } from "./finance-budget";
import "./finance-allocation.css";
import "./finance-budget.css";
import { reopenFromQuery } from "./finance-lab-deep-link";

type BudgetWarning = { code: "duplicate_entry" | "overlapping_dates"; rows: number[]; message: string };
type Review = { currency: "QAR"; proposed_total: string; entry_count: number; warnings: BudgetWarning[]; posted: false };
type ScenarioDetail = BudgetPayload & {
  id: number; revision: number; review: Review;
  history: { revision: number; created_at: string; snapshot: BudgetPayload & { review: Review } }[];
};
type ScenarioList = { scenarios: { id: number; name: string; revision: number }[] };
const accountChoices = (context: LabContext) => context.accounts.map((row) => ({ id: row.id, code: row.code, name: row.name_en }));
const unitChoices = (context: LabContext) => context.business_units.map((row) => ({ id: row.id, code: row.code, name: row.name }));
const dimensionChoices = (context: LabContext) => context.dimension_values.map((row) => ({ id: row.id, code: row.code, name: row.label_en }));
const scenarioDraft = (row: ScenarioDetail): BudgetScenarioDraft => ({
  name: row.name, startDate: row.start_date, endDate: row.end_date,
  entries: row.entries.map((entry) => ({
    startDate: entry.start_date, endDate: entry.end_date, amount: entry.amount,
    accountId: String(entry.account_id), businessUnitId: entry.business_unit_id === null ? "" : String(entry.business_unit_id),
    dimensionIds: entry.dimension_value_ids.map(String), explanation: entry.explanation,
  })),
});

export function FinanceBudgetLab() {
  const [authenticated, setAuthenticated] = useState(false);
  const [context, setContext] = useState<LabContext | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioList["scenarios"]>([]);
  const [current, setCurrent] = useState<ScenarioDetail | null>(null);
  const [draft, setDraft] = useState<BudgetScenarioDraft>(blankBudgetScenario);
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
  const accounts = useMemo(() => context ? accountChoices(context) : [], [context]);
  const units = useMemo(() => context ? unitChoices(context) : [], [context]);
  const dimensions = useMemo(() => context ? dimensionChoices(context) : [], [context]);
  const warnings = budgetWarnings(draft.entries);
  const total = budgetTotal(draft.entries);
  const errors = validateBudget(draft, accounts.map((row) => row.id), units.map((row) => row.id), dimensions.map((row) => row.id));

  function change(next: BudgetScenarioDraft) { setDraft(next); dirtyRef.current = true; setDirty(true); setReviewed(false); }
  function clearProtectedData() {
    setAuthenticated(false); setContext(null); setScenarios([]); setCurrent(null); setDraft(blankBudgetScenario());
    setDirty(false); dirtyRef.current = false; setReviewed(false);
    setConflict(false); setUncertain(false);
    setStatus("Synthetic test access is unavailable.");
    setError("Synthetic test access is unavailable. Protected budget inputs were cleared.");
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
      const [choices, list] = await Promise.all([labRequest<LabContext>("context"), labRequest<ScenarioList>("budgets")]);
      if (alive.current) {
        setContext(choices); setScenarios(list.scenarios);
        reopenFromQuery((id) => { void openScenario(id); }, setError);
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
      if (cause instanceof LabError && cause.status === 403) {
        clearProtectedData();
      } else setError(`${cause instanceof Error ? cause.message : "Budget lab unavailable."} Inputs remain unchanged.`);
    } finally { busyRef.current = false; if (alive.current) setBusy(false); }
  }
  async function refreshList() {
    const result = await labRequest<ScenarioList>("budgets");
    if (alive.current) setScenarios(result.scenarios);
  }
  function mayReplace() {
    return (!dirtyRef.current && !busyRef.current) ||
      (!busyRef.current && window.confirm("Discard unsaved budget scenario edits and open the selected saved version?"));
  }
  function newScenario() {
    if (uncertain) { setError("Unknown save outcome: refresh the list and explicitly reopen a saved scenario before starting or saving another scenario."); return; }
    if (!mayReplace()) return;
    setCurrent(null); setDraft(blankBudgetScenario()); setDirty(false); dirtyRef.current = false;
    setReviewed(false); setConflict(false); setUncertain(false); setStatus("New scenario worksheet — proposed amounts only; no budget authority is created.");
  }
  async function openScenario(id: number, confirmed = false) {
    if (!confirmed && !mayReplace()) return;
    await run(async () => {
      const [row, choices] = await Promise.all([labRequest<ScenarioDetail>(`budgets/${id}`), labRequest<LabContext>("context")]);
      if (!alive.current) return;
      setContext(choices); setCurrent(row); setDraft(scenarioDraft(row)); dirtyRef.current = false;
      setDirty(false); setReviewed(true); setConflict(false); setUncertain(false);
      setStatus(`Opened saved budget scenario ${row.id}, revision ${row.revision}.`);
    });
  }
  function buildPayload(): BudgetPayload {
    if (!context) throw new Error("Refresh the lab context before saving.");
    const entries = draft.entries.map((entry, index) => {
      const accountId = Number(entry.accountId);
      const businessUnitId = entry.businessUnitId ? Number(entry.businessUnitId) : null;
      const dimensionIds = entry.dimensionIds.map(Number);
      if (!context.accounts.some((row) => row.id === accountId) ||
        (businessUnitId !== null && !context.business_units.some((row) => row.id === businessUnitId)) ||
        dimensionIds.some((id) => !context.dimension_values.some((row) => row.id === id))) {
        throw new Error(`Row ${index + 1} contains a choice unavailable in the current context. Original choices were not substituted.`);
      }
      return {
        start_date: entry.startDate, end_date: entry.endDate, amount: entry.amount.trim(),
        account_id: accountId, business_unit_id: businessUnitId,
        dimension_value_ids: dimensionIds, explanation: entry.explanation.trim(),
      };
    });
    return { name: draft.name.trim(), start_date: draft.startDate, end_date: draft.endDate, currency: "QAR", entries };
  }
  function reviewScenario() {
    const issues = validateBudget(draft, accounts.map((row) => row.id), units.map((row) => row.id), dimensions.map((row) => row.id));
    setReviewed(true); setError(issues.join(" "));
    if (!issues.length) setStatus(`Reviewed explicit worksheet inputs. Proposed total ${total ?? "—"} QAR across ${draft.entries.length} entered rows; warnings are advisory only.`);
  }
  async function saveScenario() {
    if (!reviewed || errors.length || uncertain) { setError(errors.join(" ") || "Review the scenario before saving."); return; }
    let payload: BudgetPayload;
    try { payload = buildPayload(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Invalid saved choice."); return; }
    await run(async () => {
      try {
        const row = await labRequest<ScenarioDetail>(
          current ? `budgets/${current.id}` : "budgets", current ? "PUT" : "POST",
          { ...payload, ...(current ? { expected_revision: current.revision } : {}) },
        );
        if (!alive.current) return;
        setCurrent(row); setDraft(scenarioDraft(row)); setDirty(false); dirtyRef.current = false;
        setConflict(false); setUncertain(false); setReviewed(true);
        setStatus(`Saved budget scenario ${row.id}, revision ${row.revision}. Worksheet only — not posted and not operative authority.`);
        try { await refreshList(); }
        catch (cause) {
          if (cause instanceof LabError && cause.status === 403) throw cause;
          if (alive.current) setError("Scenario saved, but the saved-list refresh failed. Refresh the list; do not create a replacement.");
        }
      } catch (cause) {
        if (alive.current && cause instanceof LabError && cause.status === 409) setConflict(true);
        if (alive.current && !(cause instanceof LabError) ||
          (alive.current && cause instanceof LabError && cause.status >= 500)) {
          setUncertain(true);
          setStatus("Save response unavailable; the database may have committed. Refresh the list, then explicitly reopen a saved scenario before another save.");
        }
        throw cause;
      }
    });
  }

  function updateEntry(index: number, patch: Partial<BudgetEntryDraft>) {
    change({ ...draft, entries: draft.entries.map((entry, row) => row === index ? { ...entry, ...patch } : entry) });
  }
  return <main className="finance-draft-prototype finance-lab allocation-lab budget-lab" aria-labelledby="budget-title" data-testid="finance-budget-lab">
    <header className="finance-draft-banner allocation-banner">
      <p className="finance-draft-eyebrow">TIVOLI FINANCE · ISOLATED REVIEW WORKSHEET</p>
      <h1 id="budget-title">Budget scenarios</h1>
      <p>A careful record of explicit proposed amounts and dates for reviewer discussion. This worksheet is not budget authority.</p>
      <p><strong>Proposal only.</strong> Disposable synthetic data; nothing is approved, allocated, enforced, or posted.</p>
      <a href="/lab-ui/">Return to Finance draft lab</a>
    </header>
    {error && <p className="finance-draft-alert" role="alert">{error}</p>}
    <p className="budget-live-status" role="status" aria-live="polite">{busy ? "Working with the isolated budget lab…" : status}</p>
    {!authenticated && <section className="finance-draft-card budget-empty">
      <h2>Isolated synthetic access</h2>
      <p>Use the existing Finance draft lab to open the synthetic test session and configure saved accounts, business units, and dimensions. This worksheet will not create a session or seed setup.</p>
      <a href="/lab-ui/">Open Finance draft lab setup</a>
    </section>}
    {authenticated && <>
      <section className="finance-draft-card allocation-register" aria-label="Saved budget scenarios">
        <div className="allocation-section-heading"><div><p className="finance-draft-eyebrow">SAVED WORKSHEETS</p><h2>Scenario register</h2></div>
          <div className="allocation-toolbar">
            <button type="button" disabled={busy} onClick={() => void run(refreshList)}>Refresh saved list</button>
            <button type="button" disabled={busy || !context || uncertain} onClick={newScenario}>New budget scenario</button>
          </div>
        </div>
        {!scenarios.length ? <p className="budget-register-empty">No saved scenarios yet. Add explicit setup choices in the existing Finance draft lab before creating a worksheet.</p> :
          <ul className="budget-register-list">{scenarios.map((item) => <li key={item.id}>
            <button type="button" disabled={busy} onClick={() => void openScenario(item.id)}>Open budget scenario {item.id}: {item.name} — revision {item.revision}</button>
          </li>)}</ul>}
      </section>
      {!context && <section className="finance-draft-card budget-empty"><h2>Saved setup is unavailable</h2>
        <p>Account, business-unit, or dimension choices are not available here. Configure synthetic choices in the existing lab; this page does not seed choices.</p><a href="/lab-ui/">Open draft lab setup</a>
      </section>}
      {context && <fieldset className="allocation-editor budget-editor" disabled={busy} aria-busy={busy} data-dirty={dirty} data-testid="budget-editor">
        <legend>{current ? `Editing scenario ${current.id}, base revision ${current.revision}` : "New unsaved budget scenario"}</legend>
        <aside className="finance-draft-boundary"><strong>Explicit worksheet boundary</strong>
          <p>No fiscal calendar, distribution, ownership, approval, actuals, allocation, enforcement, or posting is performed. No journal balancing is required.</p>
        </aside>
        {!accounts.length && <p role="note">No saved accounts are available. Add synthetic choices in the <a href="/lab-ui/">draft lab setup</a>; none are created here.</p>}
        <section className="budget-scenario-meta" aria-label="Scenario details">
          <h2>Scenario scope</h2>
          <label>Scenario name <input aria-label="Budget scenario name" maxLength={200} value={draft.name} onChange={(event) => change({ ...draft, name: event.target.value })} /></label>
          <label>Scenario start date <input aria-label="Scenario start date" type="date" value={draft.startDate} onChange={(event) => change({ ...draft, startDate: event.target.value })} /></label>
          <label>Scenario end date <input aria-label="Scenario end date" type="date" value={draft.endDate} onChange={(event) => change({ ...draft, endDate: event.target.value })} /></label>
          <div className="budget-currency"><span>Currency</span><strong>QAR</strong><small>Explicitly supported; no FX conversion.</small></div>
        </section>
        <section className="budget-entries" aria-label="Budget scenario entries">
          <div className="budget-entries-heading"><div><p className="finance-draft-eyebrow">ENTERED ROWS</p><h2>Proposed amounts</h2></div>
            <button type="button" disabled={draft.entries.length >= 100} onClick={() => change({ ...draft, entries: [...draft.entries, emptyBudgetEntry()] })}>Add scenario row</button></div>
          {draft.entries.map((entry, index) => <fieldset className="budget-entry" key={index}>
            <legend>Scenario row {index + 1}</legend>
            <label>Start date <input type="date" aria-label={`Budget row start date ${index + 1}`} value={entry.startDate} onChange={(event) => updateEntry(index, { startDate: event.target.value })} /></label>
            <label>End date <input type="date" aria-label={`Budget row end date ${index + 1}`} value={entry.endDate} onChange={(event) => updateEntry(index, { endDate: event.target.value })} /></label>
            <label>Proposed amount (QAR) <input inputMode="decimal" aria-label={`Budget row amount ${index + 1}`} value={entry.amount} onChange={(event) => updateEntry(index, { amount: event.target.value })} /></label>
            <label>Account <select aria-label={`Budget row account ${index + 1}`} value={entry.accountId} onChange={(event) => updateEntry(index, { accountId: event.target.value })}>
              <option value="">Choose an account</option>{accounts.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name}</option>)}</select></label>
            <label>Business unit <select aria-label={`Budget row business unit ${index + 1}`} value={entry.businessUnitId} onChange={(event) => updateEntry(index, { businessUnitId: event.target.value })}>
              <option value="">No business unit</option>{units.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name}</option>)}</select></label>
            <label>Dimensions <select multiple aria-label={`Budget row dimensions ${index + 1}`} value={entry.dimensionIds} onChange={(event) => updateEntry(index, { dimensionIds: [...event.target.selectedOptions].map((option) => option.value) })}>
              {dimensions.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name}</option>)}</select></label>
            <label className="budget-explanation">Row explanation (optional)<textarea aria-label={`Budget row explanation ${index + 1}`} maxLength={2000} value={entry.explanation} onChange={(event) => updateEntry(index, { explanation: event.target.value })} /></label>
            <button className="budget-remove" type="button" disabled={draft.entries.length <= 1} onClick={() => change({ ...draft, entries: draft.entries.filter((_, row) => row !== index) })}>Remove row {index + 1}</button>
          </fieldset>)}
        </section>
        <section className="budget-total" aria-label="Proposed total">
          <span>Proposed total · each entered row counted once</span><strong>{total ?? "Enter valid amounts"} QAR</strong>
          <p>Arithmetic only. Rows are not merged, dropped, distributed, accepted, or posted.</p>
        </section>
        <section className="budget-advisories" aria-label="Advisory overlap review" aria-live="polite">
          <h3>Advisory duplicate and date review</h3>
          <p>Potential duplicates and inclusive date intersections for the same account, business unit, and dimensions are informational only. No company acceptance policy is applied.</p>
          {warnings.length ? <ul>{warnings.map((warning, index) => <li key={`${warning.code}-${index}`}><strong>{warning.code === "duplicate_entry" ? "Possible duplicate" : "Overlapping dates"}</strong> · {warning.message}</li>)}</ul> :
            <p>No same-choice duplicate or overlapping date pair detected in valid rows.</p>}
          {current?.review.warnings?.length ? <div className="budget-saved-warnings"><h4>Saved review advisories</h4><ul>{current.review.warnings.map((warning, index) => <li key={`${warning.code}-${index}`}>{warning.message}</li>)}</ul></div> : null}
        </section>
        {errors.length > 0 && <div className="budget-validation" role="note" aria-label="Worksheet validation">
          <strong>Complete these explicit inputs before saving</strong><ul>{errors.map((issue) => <li key={issue}>{issue}</li>)}</ul>
        </div>}
        <div className="allocation-actions budget-actions">
          <button type="button" disabled={busy} onClick={reviewScenario}>Review worksheet</button>
          <button type="button" disabled={busy || !reviewed || errors.length > 0 || uncertain || conflict} onClick={() => void saveScenario()}>Save budget scenario</button>
          {uncertain && <p role="alert">Unknown outcome blocks another save or new scenario. Refresh the list, then explicitly reopen a saved scenario to recover.</p>}
        </div>
      </fieldset>}
      {conflict && current && <section role="alert" className="finance-draft-card budget-conflict">
        <h2>Saved revision changed</h2><p>Your local worksheet remains unchanged. Reload only after explicit confirmation.</p>
        <button type="button" disabled={busy} onClick={() => {
          if (window.confirm("Discard local budget scenario edits and reload the saved version?")) void openScenario(current.id, true);
        }}>Reload saved scenario</button>
      </section>}
      {current && <section className="finance-draft-card allocation-history budget-history" aria-label="Budget scenario revision history">
        <h2>Saved revision history</h2><p>Snapshots record worksheet revisions only; not approvals, authority, or posted entries.</p>
        {current.history.map((item) => <details key={item.revision}>
          <summary>Revision {item.revision} — {item.snapshot.name} · {item.snapshot.review.proposed_total} QAR · {item.snapshot.review.entry_count} rows</summary>
          <p>{item.created_at} · {item.snapshot.start_date} to {item.snapshot.end_date}</p>
          <ul>{item.snapshot.entries.map((entry, index) => <li key={index}>Row {index + 1}: {entry.start_date} to {entry.end_date} · {entry.amount} QAR · account ID {entry.account_id}{entry.explanation ? ` · ${entry.explanation}` : ""}</li>)}</ul>
        </details>)}
      </section>}
    </>}
  </main>;
}
