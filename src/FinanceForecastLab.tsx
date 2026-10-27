import { useEffect, useMemo, useRef, useState } from "react";
import type { LabContext } from "./finance-draft-lab-api";
import { LabError, labRequest } from "./finance-draft-lab-api";
import {
  blankForecastDraft, blankForecastEntry, calculateForecast, payloadFromForecast, validateForecast,
} from "./finance-forecast";
import type { ForecastDraft, ForecastEntryDraft, ForecastPayload, ForecastReview } from "./finance-forecast";
import "./finance-forecast.css";
import { reopenFromQuery } from "./finance-lab-deep-link";

type ForecastSnapshot = ForecastPayload & { id: number; revision: number; review: ForecastReview };
type ForecastDetail = ForecastSnapshot & {
  history: { revision: number; created_at: string; snapshot: ForecastSnapshot }[];
};
type ForecastList = { forecasts: { id: number; name: string; revision: number }[] };

function copyForecast(row: ForecastPayload): ForecastDraft {
  return {
    name: row.name,
    as_of_date: row.as_of_date,
    opening_balance: row.opening_balance,
    entries: row.entries.map((entry) => ({
      date: entry.date,
      direction: entry.direction,
      amount: entry.amount,
      description: entry.description,
      business_unit_id: entry.business_unit_id === null ? "" : String(entry.business_unit_id),
      dimension_value_ids: entry.dimension_value_ids.map(String),
    })),
  };
}
function unavailable(id: string, values: { id: number }[]) {
  return id !== "" && !values.some((item) => String(item.id) === id);
}

export function FinanceForecastLab() {
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [context, setContext] = useState<LabContext | null>(null);
  const [forecasts, setForecasts] = useState<ForecastList["forecasts"]>([]);
  const [current, setCurrent] = useState<ForecastDetail | null>(null);
  const [draft, setDraft] = useState<ForecastDraft>(blankForecastDraft);
  const [review, setReview] = useState<ForecastReview | null>(null);
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
  const businessUnits = useMemo(() => context?.business_units ?? [], [context]);
  const dimensionValues = useMemo(() => context?.dimension_values ?? [], [context]);
  const errors = validateForecast(draft, context);

  function change(next: ForecastDraft) {
    setDraft(next);
    dirtyRef.current = true;
    setDirty(true);
    setReviewed(false);
    setReview(null);
  }
  function clearProtectedData() {
    setAuthenticated(false);
    setContext(null);
    setForecasts([]);
    setCurrent(null);
    setDraft(blankForecastDraft());
    setReview(null);
    dirtyRef.current = false;
    setDirty(false);
    setReviewed(false);
    setConflict(false);
    setUncertain(false);
    setListInspected(false);
    setStatus("Synthetic test access is unavailable.");
    setError("Synthetic test access is unavailable. Protected forecast inputs were cleared.");
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
        labRequest<LabContext>("context"), labRequest<ForecastList>("forecasts"),
      ]);
      if (!alive.current) return;
      setContext(choices);
      setForecasts(list.forecasts);
      setStatus("Synthetic forecast worksheet ready. Inputs are proposals only.");
       reopenFromQuery((id) => { void openForecast(id); }, setError);
    }).catch((cause: Error) => {
      if (!alive.current) return;
      if (cause instanceof LabError && cause.status === 403) clearProtectedData();
      else setError(cause.message);
    }).finally(() => {
      if (alive.current) setLoading(false);
    });
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
    try {
      await action();
    } catch (cause) {
      if (!alive.current) return;
      if (cause instanceof LabError && cause.status === 403) clearProtectedData();
      else setError(`${cause instanceof Error ? cause.message : "Forecast lab unavailable."} Inputs remain unchanged.`);
    } finally {
      busyRef.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function refreshList() {
    const result = await labRequest<ForecastList>("forecasts");
    if (!alive.current) return;
    setForecasts(result.forecasts);
    if (uncertain) {
      setListInspected(true);
      setStatus("Saved forecast list refreshed. Inspect it and explicitly reopen a saved record before continuing after the unknown save outcome.");
    } else setStatus("Saved forecast list refreshed.");
  }
  function mayReplace() {
    return (!dirtyRef.current && !busyRef.current) ||
      (!busyRef.current && window.confirm("Discard unsaved cash forecast edits and open the selected saved version?"));
  }
  function newForecast() {
    if (uncertain) {
      setError("Unknown save outcome: refresh and inspect the saved list, then explicitly reopen a saved forecast before starting or saving another.");
      return;
    }
    if (!mayReplace()) return;
    setCurrent(null);
    setDraft(blankForecastDraft());
    setReview(null);
    dirtyRef.current = false;
    setDirty(false);
    setReviewed(false);
    setConflict(false);
    setStatus("New cash forecast worksheet. Proposed only; no actual cash, approval, or posting.");
  }
  async function openForecast(id: number, confirmed = false) {
    if (uncertain && !listInspected) {
      setError("Refresh and inspect the saved forecast list before reopening a record after an unknown save outcome.");
      return;
    }
    if (!confirmed && !mayReplace()) return;
    await run(async () => {
      const [row, choices] = await Promise.all([
        labRequest<ForecastDetail>(`forecasts/${id}`), labRequest<LabContext>("context"),
      ]);
      if (!alive.current) return;
      setContext(choices);
      setCurrent(row);
      setDraft(copyForecast(row));
      setReview(row.review);
      dirtyRef.current = false;
      setDirty(false);
      setReviewed(true);
      setConflict(false);
      setUncertain(false);
      setListInspected(false);
      setStatus(`Opened cash forecast ${row.id}, revision ${row.revision}. Proposed only — not actual cash, approved, or posted.`);
    });
  }
  function reviewForecast() {
    if (errors.length) {
      setReviewed(false);
      setReview(null);
      setError(errors.join(" "));
      return;
    }
    try {
      const payload = payloadFromForecast(draft, context!);
      setReview(calculateForecast(payload));
      setReviewed(true);
      setError("");
      setStatus("Reviewed the explicit QAR scenario. Date sorting is presentation-only; it is not execution or payment priority.");
    } catch (cause) {
      setReviewed(false);
      setError(cause instanceof Error ? cause.message : "Correct the forecast inputs.");
    }
  }
  async function saveForecast() {
    if (!reviewed || errors.length || uncertain || conflict) {
      setError(errors.join(" ") || "Review the explicit cash forecast before saving.");
      return;
    }
    await run(async () => {
      let writeAttempted = false;
      try {
        const freshContext = await labRequest<LabContext>("context");
        if (!alive.current) return;
        setContext(freshContext);
        const freshErrors = validateForecast(draft, freshContext);
        if (freshErrors.length) {
          setReviewed(false);
          setReview(null);
          throw new Error(`Saved choices or inputs need correction after context refresh. Original choices remain visible and are not replaced. ${freshErrors.join(" ")}`);
        }
        const payload = payloadFromForecast(draft, freshContext);
        const body = { ...payload, ...(current ? { expected_revision: current.revision } : {}) };
        writeAttempted = true;
        const saved = await labRequest<ForecastDetail>(
          current ? `forecasts/${current.id}` : "forecasts", current ? "PUT" : "POST", body,
        );
        if (!saved || !Number.isSafeInteger(saved.id) || !Number.isSafeInteger(saved.revision) || !Array.isArray(saved.history)) {
          throw new Error("The save response was incomplete.");
        }
        if (!alive.current) return;
        setCurrent(saved);
        setDraft(copyForecast(saved));
        setReview(saved.review);
        dirtyRef.current = false;
        setDirty(false);
        setConflict(false);
        setUncertain(false);
        setListInspected(false);
        setReviewed(true);
        setStatus(`Saved cash forecast ${saved.id}, revision ${saved.revision}. Proposed only — not actual cash, approved, or posted.`);
        try {
          await refreshList();
          if (alive.current) setStatus(`Saved cash forecast ${saved.id}, revision ${saved.revision}. Saved list refreshed. Proposed only — not actual cash, approved, or posted.`);
        } catch (cause) {
          if (cause instanceof LabError && cause.status === 403) throw cause;
          if (alive.current) setError("Forecast saved successfully, but the saved-list refresh failed. The save was acknowledged; refresh the list when available.");
        }
      } catch (cause) {
        if (alive.current && cause instanceof LabError && cause.status === 409) setConflict(true);
        if (alive.current && writeAttempted && (!(cause instanceof LabError) || cause.status >= 500)) {
          setUncertain(true);
          setListInspected(false);
          setStatus("Save response unavailable; the database may have committed. Refresh and inspect the saved list, then explicitly reopen a saved forecast before another save.");
        }
        throw cause;
      }
    });
  }
  function setField<K extends keyof ForecastDraft>(key: K, value: ForecastDraft[K]) {
    change({ ...draft, [key]: value });
  }
  function changeEntry(index: number, patch: Partial<ForecastEntryDraft>) {
    const entries = draft.entries.map((entry, row) => row === index ? { ...entry, ...patch } : entry);
    change({ ...draft, entries });
  }
  function addEntry() {
    if (draft.entries.length >= 200) return;
    change({ ...draft, entries: [...draft.entries, blankForecastEntry()] });
  }
  function removeEntry(index: number) {
    if (draft.entries.length <= 1) return;
    change({ ...draft, entries: draft.entries.filter((_, row) => row !== index) });
  }

  return <main className="forecast-lab" aria-labelledby="forecast-title" data-testid="finance-forecast-lab">
    <header className="forecast-banner">
      <p className="forecast-eyebrow">TIVOLI FINANCE · ISOLATED REVIEW WORKSHEET</p>
      <h1 id="forecast-title">Cash-flow scenarios</h1>
      <p>Build a dated, synthetic QAR scenario from explicit inputs. Exact decimal strings are calculated locally; descriptions and source row order stay intact.</p>
      <p><strong>Proposal only.</strong> No bank connection, actual cash, inferred timing, approval, schedule, or posting.</p>
      <a href="/lab-ui/">Return to Finance draft lab setup</a>
    </header>
    {error && <p className="forecast-alert" role="alert">{error}</p>}
    <p className="forecast-live" role="status" aria-live="polite">{loading ? "Loading isolated forecast lab…" : busy ? "Working with the isolated forecast lab…" : status}</p>
    {loading && <section className="forecast-card" aria-label="Loading forecasts"><p>Loading saved scenarios and setup choices…</p></section>}
    {!loading && !authenticated && <section className="forecast-card">
      <h2>Isolated synthetic access</h2>
      <p>Open the existing Finance draft lab to configure the synthetic test session and saved choices. This worksheet does not create a session or setup.</p>
      <a href="/lab-ui/">Open Finance draft lab setup</a>
    </section>}
    {!loading && authenticated && <>
      <section className="forecast-card" aria-label="Saved cash forecasts">
        <div className="forecast-heading">
          <div><p className="forecast-eyebrow">SAVED WORKSHEETS</p><h2>Scenario register</h2></div>
          <div className="forecast-toolbar">
            <button type="button" className="forecast-secondary" disabled={busy} onClick={() => void run(refreshList)}>Refresh saved forecasts</button>
            <button type="button" disabled={busy || !context || uncertain} onClick={newForecast}>New forecast draft</button>
          </div>
        </div>
        {!forecasts.length ? <p>No saved cash forecasts yet. Add synthetic business units or dimensions in the existing lab if needed; this worksheet creates none.</p> :
          <ul className="forecast-register">{forecasts.map((item) => <li key={item.id}>
            <button type="button" className="forecast-secondary" disabled={busy}
              aria-label={`Open forecast draft ${item.id}: ${item.name}`} onClick={() => void openForecast(item.id)}>
              {item.name} · revision {item.revision}
            </button>
          </li>)}</ul>}
        {uncertain && <p role="alert">Unknown save outcome blocks another save or new record. Refresh and inspect this saved list, then explicitly reopen a saved forecast to recover.</p>}
      </section>
      {!context && <section className="forecast-card">
        <h2>Saved setup is unavailable</h2>
        <p>Existing business-unit or dimension choices are unavailable. Configure synthetic choices in the existing lab; none are created here.</p>
        <a href="/lab-ui/">Open draft lab setup</a>
      </section>}
      {context && <fieldset className="forecast-editor" disabled={busy} aria-busy={busy} data-dirty={dirty} data-testid="forecast-editor">
        <legend>{current ? `Editing cash forecast ${current.id}, revision ${current.revision}` : "New forecast draft"}</legend>
        <aside className="forecast-boundary">
          <strong>Explicit scenario boundary</strong>
          <p>Dates are entered by the user. At review, rows are sorted by date and original input position for same-date presentation only—not execution or payment priority. Running balances may be negative and are not policy violations.</p>
          <p>QAR only. Zero is accepted as a technical draft amount. No account is required, and blank business unit means “Not proposed.” Descriptions are preserved as entered.</p>
        </aside>
        <section className="forecast-fields" aria-label="Forecast details">
          <label>Forecast name · required
            <input aria-label="Forecast name" maxLength={200} value={draft.name} onChange={(event) => setField("name", event.target.value)} />
          </label>
          <label>Opening as-of date · required
            <input type="date" aria-label="Opening as-of date" value={draft.as_of_date} onChange={(event) => setField("as_of_date", event.target.value)} />
          </label>
          <label>Opening QAR balance · signed exact decimal
            <input inputMode="decimal" aria-label="Opening QAR balance" value={draft.opening_balance} onChange={(event) => setField("opening_balance", event.target.value)} />
          </label>
          <div className="forecast-currency"><span>Currency</span><strong>QAR</strong><span>Fixed · no conversion</span></div>
        </section>
        <div className="forecast-heading">
          <div><h2>Cash-flow rows</h2><p>At least one explicit dated row is required.</p></div>
          <button type="button" className="forecast-secondary" disabled={busy || draft.entries.length >= 200} onClick={addEntry}>Add cash flow</button>
        </div>
        <ol className="flow-list">
          {draft.entries.map((entry, index) => {
            const number = index + 1;
            const buLabel = `Flow business unit ${number}`;
            const dimLabel = `Flow dimensions ${number}`;
            return <li className="flow-row" key={index}>
              <div className="flow-heading">
                <h3>Cash flow {number}</h3>
                <button type="button" className="forecast-secondary" disabled={busy || draft.entries.length <= 1}
                  aria-label={`Remove cash flow ${number}`} onClick={() => removeEntry(index)}>Remove row</button>
              </div>
              <div className="flow-fields">
                <label>Flow date · required
                  <input type="date" aria-label={`Flow date ${number}`} value={entry.date} onChange={(event) => changeEntry(index, { date: event.target.value })} />
                </label>
                <label>Flow direction
                  <select aria-label={`Flow direction ${number}`} value={entry.direction}
                    onChange={(event) => changeEntry(index, { direction: event.target.value as "inflow" | "outflow" })}>
                    <option value="inflow">Inflow</option><option value="outflow">Outflow</option>
                  </select>
                </label>
                <label>Flow amount · QAR
                  <input inputMode="decimal" aria-label={`Flow amount ${number}`} value={entry.amount} onChange={(event) => changeEntry(index, { amount: event.target.value })} />
                </label>
                <label className="flow-description">Flow description · required
                  <textarea aria-label={`Flow description ${number}`} maxLength={2000} value={entry.description} onChange={(event) => changeEntry(index, { description: event.target.value })} />
                </label>
                <label>Business unit · optional
                  <select aria-label={buLabel} value={entry.business_unit_id} onChange={(event) => changeEntry(index, { business_unit_id: event.target.value })}>
                    <option value="">Not proposed</option>
                    {unavailable(entry.business_unit_id, businessUnits) && <option value={entry.business_unit_id}>Unavailable saved choice ({entry.business_unit_id}) — choose or clear</option>}
                    {businessUnits.map((unit) => <option key={unit.id} value={unit.id}>{unit.code} — {unit.name}</option>)}
                  </select>
                </label>
                <label className="flow-dimensions">Dimensions · optional; select saved values
                  <select multiple aria-label={dimLabel} value={entry.dimension_value_ids} onChange={(event) =>
                    changeEntry(index, { dimension_value_ids: [...event.target.selectedOptions].map((option) => option.value) })}>
                    {entry.dimension_value_ids.filter((id) => unavailable(id, dimensionValues)).map((id) =>
                      <option key={`unavailable-${id}`} value={id}>Unavailable saved choice ({id}) — remove or replace</option>)}
                    {dimensionValues.map((dimension) => <option key={dimension.id} value={dimension.id}>{dimension.code} — {dimension.label_en}</option>)}
                  </select>
                  <small>Choose only existing saved dimension values. Unavailable selections stay visible until corrected.</small>
                </label>
              </div>
            </li>;
          })}
        </ol>
        {errors.length > 0 && <div className="forecast-note" role="note" aria-label="Forecast validation">
          <strong>Complete or correct these explicit inputs before review</strong>
          <ul>{errors.map((issue) => <li key={issue}>{issue}</li>)}</ul>
        </div>}
        {review && <section className="forecast-review" aria-label="Forecast review">
          <p className="forecast-eyebrow">LOCAL QAR CALCULATION · PROPOSAL ONLY</p>
          <h2>Review · date ascending</h2>
          <p>Presentation order only; same-date rows retain original input order. This is not execution or payment priority. Actual cash: no · approved: no · posted: no.</p>
          <div className="review-totals">
            {[
              ["Opening balance", review.opening_balance], ["Total inflows", review.total_inflows],
              ["Total outflows", review.total_outflows], ["Net change", review.net_change], ["Closing balance", review.closing_balance],
            ].map(([label, amount]) => <div key={label}><span>{label} · QAR</span><strong>{amount}</strong></div>)}
          </div>
          <div className="review-table-wrap"><table className="review-table">
            <thead><tr><th>Input row</th><th>Date</th><th>Direction</th><th>Description</th><th>Amount · QAR</th><th>Running balance · QAR</th></tr></thead>
            <tbody>{review.entries.map((item) => <tr key={item.line_number}>
              <td>{item.line_number}</td><td>{item.date}</td><td>{item.direction}</td><td>{item.description}</td>
              <td className="forecast-number">{item.amount}</td><td className="forecast-number">{item.running_balance}</td>
            </tr>)}</tbody>
          </table></div>
        </section>}
        {current && <p className="forecast-note">Saved review at revision {current.revision}: closing balance {current.review.closing_balance} QAR · proposed: yes · actual cash: no · approved: no · posted: no.</p>}
        <div className="forecast-actions">
          <button type="button" className="forecast-secondary" disabled={busy} onClick={reviewForecast}>Review forecast</button>
          <button type="button" disabled={busy || !reviewed || errors.length > 0 || uncertain || conflict}
            onClick={() => void saveForecast()}>{current ? "Save forecast revision" : "Save forecast draft"}</button>
        </div>
      </fieldset>}
      {conflict && current && <section className="forecast-card" role="alert">
        <h2>Saved revision changed</h2>
        <p>Your local inputs remain unchanged. Reload only after explicit confirmation.</p>
        <button type="button" className="forecast-secondary" disabled={busy} onClick={() => {
          if (window.confirm("Discard local cash forecast edits and reload the saved version?")) void openForecast(current.id, true);
        }}>Reload saved forecast</button>
      </section>}
      {current && <section className="forecast-card forecast-history" aria-label="Forecast revision history">
        <h2>Saved revision history</h2>
        <p>Snapshots contain proposed scenario inputs and review metadata only; no approval or posting is performed.</p>
        {current.history.map((item) => <details key={item.revision}>
          <summary>Forecast revision {item.revision} — {item.snapshot.name} · closing {item.snapshot.review.closing_balance} QAR</summary>
          <p>{item.created_at} · as of {item.snapshot.as_of_date} · opening {item.snapshot.opening_balance} QAR · {item.snapshot.entries.length} cash-flow rows</p>
          <ol>{item.snapshot.entries.map((entry, index) => <li key={index}>{entry.date} · {entry.direction} {entry.amount} QAR · {entry.description}
            {entry.business_unit_id === null ? " · business unit not proposed" : ` · business unit ID ${entry.business_unit_id}`}
            {entry.dimension_value_ids.length ? ` · dimension IDs ${entry.dimension_value_ids.join(", ")}` : " · no dimensions proposed"}</li>)}</ol>
        </details>)}
      </section>}
    </>}
  </main>;
}

export default FinanceForecastLab;
