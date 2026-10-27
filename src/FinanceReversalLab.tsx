import { useEffect, useRef, useState } from "react";
import type { LabContext } from "./finance-draft-lab-api";
import { LabError, labRequest } from "./finance-draft-lab-api";
import { reopenFromQuery } from "./finance-lab-deep-link";
import "./finance-invoice.css";

type ReversalLine = {
  account_id: string;
  business_unit_id: string;
  dimension_value_ids: string[];
  debit: string;
  credit: string;
  memo_en: string;
};
type ReversalDraft = {
  source_journal_id: string;
  source_revision: string;
  proposed_date: string;
  explanation: string;
  lines: ReversalLine[];
};
type JournalSummary = { id: number; title_en: string; revision: number; kind?: string };
type SourceLine = {
  account_id?: number; account_code?: string; account_name_en?: string;
  business_unit_id?: number | null; business_unit_code?: string;
  dimension_value_ids?: number[]; dimensions?: { id?: number; code?: string; label_en?: string }[];
  debit?: string; credit?: string; memo_en?: string;
};
type SourceSnapshot = {
  id: number; revision: number; title_en: string; kind: string;
  lines: SourceLine[]; review?: { debit_total?: string; credit_total?: string; [key: string]: unknown };
  [key: string]: unknown;
};
type SourceResponse = { source: SourceSnapshot };
type ReversalReview = {
  currency: string; debit_total: string; credit_total: string; difference: string;
  balanced: boolean; issues: string[]; offsets_source: boolean; comparison: string;
  bucket_differences: { account_id: number; business_unit_id: number | null; dimension_value_ids: number[]; combined_net: string }[];
};
type ReversalPayload = Omit<ReversalDraft, "source_journal_id" | "source_revision" | "lines"> & {
  source_journal_id: number; source_revision: number;
  lines: { account_id: number; business_unit_id: number | null; dimension_value_ids: number[];
    debit: string; credit: string; memo_en?: string }[];
};
type ReversalDetail = ReversalPayload & {
  id: number; revision: number; source: SourceSnapshot; review: ReversalReview;
  history: { revision: number; snapshot: ReversalPayload & { review?: ReversalReview }; created_at: string }[];
};
type ReversalRow = { id: number; revision: number; source_journal_id: number; source_revision: number; proposed_date: string; explanation: string };

const emptyDraft = (): ReversalDraft => ({
  source_journal_id: "", source_revision: "", proposed_date: "", explanation: "", lines: [],
});
const emptyLine = (): ReversalLine => ({
  account_id: "", business_unit_id: "", dimension_value_ids: [], debit: "", credit: "", memo_en: "",
});
function validId(value: string): number | null {
  if (!/^[1-9]\d*$/.test(value)) return null;
  const result = Number(value);
  return Number.isSafeInteger(result) && result > 0 ? result : null;
}
function validDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value)
    && !Number.isNaN(Date.parse(`${value}T00:00:00Z`))
    && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}
function validMoney(value: string) {
  return /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value.trim());
}
function safeJournal(row: JournalSummary): row is JournalSummary {
  return Number.isSafeInteger(row.id) && row.id > 0 && Number.isSafeInteger(row.revision) && row.revision > 0;
}
function sourceLineText(line: SourceLine, index: number) {
  const account = [line.account_code, line.account_name_en].filter(Boolean).join(" — ")
    || (line.account_id ? `Account ${line.account_id}` : "Account not identified");
  const unit = line.business_unit_code ?? (line.business_unit_id ? `Business unit ${line.business_unit_id}` : "No business unit");
  const dimensions = line.dimensions?.map((item) => [item.code, item.label_en].filter(Boolean).join(" — ") || String(item.id ?? "")).filter(Boolean).join(", ")
    ?? line.dimension_value_ids?.join(", ") ?? "None";
  return `Line ${index + 1}: ${account} · ${unit} · dimensions ${dimensions} · debit ${line.debit ?? "—"} · credit ${line.credit ?? "—"}${line.memo_en ? ` · ${line.memo_en}` : ""}`;
}

export function FinanceReversalLab() {
  const [loading, setLoading] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const [context, setContext] = useState<LabContext | null>(null);
  const [journals, setJournals] = useState<JournalSummary[]>([]);
  const [reversals, setReversals] = useState<ReversalRow[]>([]);
  const [current, setCurrent] = useState<ReversalDetail | null>(null);
  const [draft, setDraft] = useState<ReversalDraft>(emptyDraft);
  const [source, setSource] = useState<SourceSnapshot | null>(null);
  const [availableRevisions, setAvailableRevisions] = useState<number[]>([]);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [dirty, setDirty] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const dirtyRef = useRef(false);
  const busyRef = useRef(false);
  const alive = useRef(true);
  const sourceRequest = useRef(0);
  const recordRequest = useRef(0);

  function clearProtected() {
    sourceRequest.current++;
    recordRequest.current++;
    setSourceLoading(false);
    setAuthenticated(false); setContext(null); setJournals([]); setReversals([]);
    setCurrent(null); setDraft(emptyDraft()); setSource(null); setAvailableRevisions([]);
    dirtyRef.current = false; setDirty(false); setConflict(false); setUncertain(false);
    setStatus("Synthetic test access is unavailable.");
    setError("Synthetic test access is unavailable. Protected reversal inputs were cleared.");
  }
  async function loadLists() {
    const [choices, journalResult, reversalResult] = await Promise.all([
      labRequest<LabContext>("context"),
      labRequest<{ drafts?: JournalSummary[]; journals?: JournalSummary[] }>("journals"),
      labRequest<{ reversals: ReversalRow[] }>("reversals"),
    ]);
    if (!alive.current) return;
    const rows = journalResult.journals ?? journalResult.drafts ?? [];
    setContext(choices); setJournals(rows.filter(safeJournal));
    setReversals(reversalResult.reversals.filter((item) =>
      Number.isSafeInteger(item.id) && item.id > 0 && Number.isSafeInteger(item.revision) && item.revision > 0
      && Number.isSafeInteger(item.source_journal_id) && item.source_journal_id > 0
      && Number.isSafeInteger(item.source_revision) && item.source_revision > 0));
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
      await loadLists();
      if (alive.current) {
        setStatus("Reversal review lab ready. Every proposal is non-posting and requires explicit inputs.");
        reopenFromQuery((id) => { void openReversal(id); }, setError);
      }
    }).catch((cause: Error) => {
      if (!alive.current) return;
      if (cause instanceof LabError && cause.status === 403) clearProtected();
      else setError(cause.message);
    }).finally(() => { if (alive.current) setLoading(false); });
    return () => {
      alive.current = false; sourceRequest.current++; recordRequest.current++;
      window.removeEventListener("beforeunload", beforeUnload);
    };
  }, []);

  async function run(action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError("");
    try { await action(); }
    catch (cause) {
      if (!alive.current) return;
      if (cause instanceof LabError && cause.status === 403) clearProtected();
      else {
        if (cause instanceof LabError && cause.status === 409) setConflict(true);
        setError(`${cause instanceof Error ? cause.message : "Reversal lab unavailable."} Inputs remain unchanged.`);
      }
    } finally { busyRef.current = false; if (alive.current) setBusy(false); }
  }
  function markChanged(next: ReversalDraft) {
    setDraft(next); dirtyRef.current = true; setDirty(true);
  }
  function mayReplace() {
    return (!dirtyRef.current && !busyRef.current)
      || (!busyRef.current && window.confirm("Discard unsaved reversal inputs and open the selected saved version?"));
  }
  async function selectSourceJournal(value: string) {
    sourceRequest.current++;
    const request = sourceRequest.current;
    recordRequest.current++;
    setSource(null); setAvailableRevisions([]); setConflict(false); setSourceLoading(false);
    const next = { ...draft, source_journal_id: value, source_revision: "" };
    markChanged(next);
    if (!value) { setStatus("Choose a journal explicitly, then choose one of its saved revisions."); return; }
    const id = validId(value);
    if (!id) { setError("Choose a valid positive safe journal identifier."); return; }
    setSourceLoading(true); setError("");
    try {
      const detail = await labRequest<SourceSnapshot & { history?: { revision: number }[] }>(`journals/${id}`);
      if (!alive.current || request !== sourceRequest.current) return;
      const revisions = [detail.revision, ...(detail.history ?? []).map((item) => item.revision)]
        .filter((revision) => Number.isSafeInteger(revision) && revision > 0);
      setAvailableRevisions([...new Set(revisions)].sort((a, b) => b - a));
      setStatus("Journal revisions loaded. Select the exact saved revision to read its immutable source snapshot.");
    } catch (cause) {
      if (!alive.current || request !== sourceRequest.current) return;
      if (cause instanceof LabError && cause.status === 403) clearProtected();
      else setError(`${cause instanceof Error ? cause.message : "Journal revisions unavailable."} Source details remain cleared.`);
    } finally {
      if (alive.current && request === sourceRequest.current) setSourceLoading(false);
    }
  }
  async function selectSourceRevision(value: string) {
    sourceRequest.current++;
    const request = sourceRequest.current;
    setSource(null); setSourceLoading(false);
    const next = { ...draft, source_revision: value };
    markChanged(next);
    const id = validId(next.source_journal_id);
    const revision = validId(value);
    if (!id || !revision) return;
    setSourceLoading(true); setError("");
    try {
      const result = await labRequest<SourceResponse>(`reversal-sources/${id}/${revision}`);
      if (!alive.current || request !== sourceRequest.current) return;
      if (result.source?.id !== id || result.source?.revision !== revision) {
        setError("The source response does not match the selected journal and saved revision. Saving is blocked.");
        return;
      }
      setSource(result.source);
      setStatus(`Verified read-only source journal ${id}, revision ${revision}. Its original snapshot is not changed.`);
    } catch (cause) {
      if (!alive.current || request !== sourceRequest.current) return;
      if (cause instanceof LabError && cause.status === 403) clearProtected();
      else setError(`${cause instanceof Error ? cause.message : "Source snapshot unavailable."} The source remains unverified.`);
    } finally {
      if (alive.current && request === sourceRequest.current) setSourceLoading(false);
    }
  }
  function newReversal() {
    if (uncertain) {
      setError("The previous save outcome is unknown. Refresh and inspect the register, then explicitly reopen a saved proposal before starting another.");
      return;
    }
    if (!mayReplace()) return;
    sourceRequest.current++; recordRequest.current++;
    setSourceLoading(false);
    setCurrent(null); setDraft(emptyDraft()); setSource(null); setAvailableRevisions([]);
    setConflict(false); dirtyRef.current = false; setDirty(false);
    setStatus("New reversal proposal. Source and every proposed line must be selected or entered explicitly.");
  }
  async function openReversal(id: number, confirmed = false) {
    if (!Number.isSafeInteger(id) || id <= 0 || (!confirmed && !mayReplace())) return;
    await run(async () => {
      const request = ++recordRequest.current;
      sourceRequest.current++;
      setSourceLoading(false); setSource(null); setAvailableRevisions([]);
      const row = await labRequest<ReversalDetail>(`reversals/${id}`);
      if (!alive.current || request !== recordRequest.current) return;
      if (!Number.isSafeInteger(row.id) || row.id !== id || !Number.isSafeInteger(row.revision) || row.revision <= 0
        || !Number.isSafeInteger(row.source_journal_id) || row.source_journal_id <= 0
        || !Number.isSafeInteger(row.source_revision) || row.source_revision <= 0
        || row.source.id !== row.source_journal_id || row.source.revision !== row.source_revision) {
        throw new Error("Saved reversal or linked source identifiers do not match the record.");
      }
      setCurrent(row);
      const next: ReversalDraft = {
        source_journal_id: String(row.source_journal_id), source_revision: String(row.source_revision),
        proposed_date: row.proposed_date, explanation: row.explanation,
        lines: row.lines.map((line) => ({
          account_id: String(line.account_id), business_unit_id: line.business_unit_id === null ? "" : String(line.business_unit_id),
          dimension_value_ids: line.dimension_value_ids.map(String), debit: line.debit, credit: line.credit, memo_en: line.memo_en ?? "",
        })),
      };
      setDraft(next); setSource(row.source); setAvailableRevisions([row.source_revision]);
      setSourceLoading(false);
      dirtyRef.current = false; setDirty(false); setConflict(false); setUncertain(false);
      setStatus(`Opened saved reversal proposal ${row.id}, revision ${row.revision}; source ${row.source_journal_id}, revision ${row.source_revision} remains read-only.`);
    });
  }
  function updateLine(index: number, changes: Partial<ReversalLine>) {
    markChanged({ ...draft, lines: draft.lines.map((line, itemIndex) => itemIndex === index ? { ...line, ...changes } : line) });
  }
  function validate(): string[] {
    const issues: string[] = [];
    if (!validId(draft.source_journal_id) || !validId(draft.source_revision)) issues.push("Select a positive safe journal ID and an exact saved source revision.");
    if (!source || source.id !== validId(draft.source_journal_id) || source.revision !== validId(draft.source_revision)) {
      issues.push("Load and verify the exact selected source snapshot before saving.");
    }
    if (!validDate(draft.proposed_date)) issues.push("Enter a valid proposed date in YYYY-MM-DD format.");
    if (!draft.explanation.trim()) issues.push("Enter an explicit non-empty English explanation.");
    if (draft.explanation.length > 200) issues.push("The explicit explanation must be 200 characters or fewer.");
    if (draft.lines.length < 2) issues.push("Add and explicitly enter at least two proposed lines.");
    if (draft.lines.length > 200) issues.push("A proposal can contain no more than 200 explicit lines.");
    draft.lines.forEach((line, index) => {
      const label = `Line ${index + 1}`;
      const accountId = validId(line.account_id);
      if (!accountId || !context?.accounts.some((item) => item.id === accountId)) issues.push(`${label}: choose an available saved account.`);
      if (line.business_unit_id && (!validId(line.business_unit_id) || !context?.business_units.some((item) => item.id === Number(line.business_unit_id)))) issues.push(`${label}: choose an available business unit or clear it.`);
      if (line.dimension_value_ids.some((value) => !validId(value) || !context?.dimension_values.some((item) => item.id === Number(value)))) issues.push(`${label}: choose available dimension values.`);
      if (!validMoney(line.debit) || !validMoney(line.credit)) issues.push(`${label}: enter debit and credit as non-negative decimal strings.`);
    });
    return issues;
  }
  async function save() {
    const issues = validate();
    if (issues.length || conflict || uncertain) {
      setError(issues.join(" ") || (conflict ? "Reload the conflicting saved version only after confirmation." : "The previous save outcome is unknown. Refresh and inspect, then explicitly reopen before saving."));
      return;
    }
    await run(async () => {
      const request = recordRequest.current;
      const freshContext = await labRequest<LabContext>("context");
      if (!alive.current || request !== recordRequest.current) return;
      if (!freshContext.accounts.some((item) => item.id === Number(draft.lines[0]?.account_id))
        || draft.lines.some((line) => !freshContext.accounts.some((item) => item.id === Number(line.account_id))
          || (line.business_unit_id && !freshContext.business_units.some((item) => item.id === Number(line.business_unit_id)))
          || line.dimension_value_ids.some((value) => !freshContext.dimension_values.some((item) => item.id === Number(value))))) {
        throw new Error("A saved account, business unit, or dimension is no longer available. Inputs remain visible; refresh and correct them.");
      }
      setContext(freshContext);
      const payload: ReversalPayload = {
        source_journal_id: Number(draft.source_journal_id), source_revision: Number(draft.source_revision),
        proposed_date: draft.proposed_date, explanation: draft.explanation,
        lines: draft.lines.map((line) => ({
          account_id: Number(line.account_id),
          business_unit_id: line.business_unit_id ? Number(line.business_unit_id) : null,
          dimension_value_ids: line.dimension_value_ids.map(Number),
          debit: line.debit, credit: line.credit, ...(line.memo_en ? { memo_en: line.memo_en } : {}),
        })),
      };
      const path = current ? `reversals/${current.id}` : "reversals";
      const body = current ? { ...payload, expected_revision: current.revision } : payload;
      let writeAttempted = false;
      let saved: ReversalDetail;
      try {
        writeAttempted = true;
        saved = await labRequest<ReversalDetail>(path, current ? "PUT" : "POST", body);
      } catch (cause) {
        if (writeAttempted && (!(cause instanceof LabError) || cause.status >= 500)) {
          setUncertain(true);
          setStatus("Save acknowledgement unavailable; the database may have committed. Refresh and inspect the register, then explicitly reopen a saved proposal before another save.");
        }
        throw cause;
      }
      if (!alive.current || request !== recordRequest.current) return;
      if (saved.source?.id !== payload.source_journal_id || saved.source?.revision !== payload.source_revision) {
        throw new Error("Save acknowledgement linked a different source snapshot. Local inputs are retained.");
      }
      if (!Number.isSafeInteger(saved.id) || !Number.isSafeInteger(saved.revision) || !saved.review || !Array.isArray(saved.history)) {
        throw new Error("The save response was incomplete. Local inputs are retained.");
      }
      setCurrent(saved); setSource(saved.source); setDraft({
        ...payload, source_journal_id: String(payload.source_journal_id), source_revision: String(payload.source_revision),
        lines: payload.lines.map((line) => ({ ...line, business_unit_id: line.business_unit_id === null ? "" : String(line.business_unit_id), account_id: String(line.account_id), dimension_value_ids: line.dimension_value_ids.map(String), memo_en: line.memo_en ?? "" })),
      });
      dirtyRef.current = false; setDirty(false); setConflict(false);
      setUncertain(false);
      setStatus(`Saved reversal proposal ${saved.id}, revision ${saved.revision}. Server review is arithmetic comparison only; not approved or posted.`);
      try {
        const result = await labRequest<{ reversals: ReversalRow[] }>("reversals");
        if (alive.current) setReversals(result.reversals);
      } catch (cause) {
        if (cause instanceof LabError && cause.status === 403) throw cause;
        if (alive.current) setError("Proposal saved successfully, but the register refresh failed. Refresh the list when available.");
      }
    });
  }
  const issues = validate();

  return <main className="invoice-lab" aria-labelledby="reversal-title" data-testid="reversal-editor">
    <header className="invoice-banner">
      <p className="invoice-kicker">TIVOLI FINANCE · ISOLATED REVIEW WORKSHEET</p>
      <h1 id="reversal-title">Journal reversal proposals</h1>
      <p>Review a linked source snapshot, then enter every proposed date, explanation, account, dimension, and amount yourself.</p>
      <p><strong>Non-posting synthetic proposal.</strong> This is not an operational JournalEntry, an approved reversal, or a ledger action.</p>
      <a href="/lab-ui/">Return to Finance draft lab setup</a>
    </header>
    {error && <p className="invoice-alert" role="alert">{error}</p>}
    <p className="invoice-live" role="status" aria-live="polite">{loading ? "Loading isolated reversal lab…" : busy ? "Working with the isolated reversal lab…" : status}</p>
    {loading && <section className="invoice-panel" aria-label="Loading reversal proposals"><div className="invoice-skeleton" aria-hidden="true" /></section>}
    {!loading && !authenticated && <section className="invoice-panel"><h2>Isolated synthetic access</h2><p>Open the existing Finance draft lab to configure a synthetic session. This worksheet creates none of that setup.</p><a href="/lab-ui/">Open Finance draft lab setup</a></section>}
    {!loading && authenticated && <>
      <section className="invoice-panel" aria-label="Saved reversal proposals">
        <div className="invoice-heading"><div><p className="invoice-kicker">SAVED PROPOSALS</p><h2>Reversal register</h2></div>
          <div className="invoice-toolbar">
            <button type="button" className="invoice-secondary" disabled={busy} onClick={() => void run(loadLists)}>Refresh saved proposals</button>
            <button type="button" disabled={busy || uncertain} onClick={newReversal}>New reversal proposal</button>
          </div>
        </div>
        {!reversals.length ? <p>No saved reversal proposals yet. A source is never selected automatically.</p> :
          <ul className="invoice-register">{reversals.map((item) => <li key={item.id}><button type="button" className="invoice-secondary" disabled={busy}
            onClick={() => void openReversal(item.id)} aria-label={`Open reversal proposal ${item.id}, revision ${item.revision}`}>
            Proposal {item.id} · source {item.source_journal_id} revision {item.source_revision} · {item.proposed_date} · revision {item.revision}
          </button><span>{item.explanation}</span></li>)}</ul>}
      </section>
      {!context && <section className="invoice-panel"><h2>Saved setup is unavailable</h2><p>Configure synthetic accounts and dimensions in the existing lab; this worksheet does not create setup choices.</p><a href="/lab-ui/">Open draft lab setup</a></section>}
      {context && <fieldset className="invoice-editor" disabled={busy} data-dirty={dirty} aria-busy={busy} data-testid="reversal-editor-fields">
        <legend>{current ? `Editing reversal proposal ${current.id}, revision ${current.revision}` : "New unsaved reversal proposal"}</legend>
        <aside className="invoice-boundary"><strong>Review boundary</strong>
          <p>Source journals and their saved revisions are read-only. No date, account, business unit, dimension, memo, debit, or credit is copied, inverted, selected, or inferred from the source.</p>
          <p>Every line below is an explicit proposal input. Saved server comparison is arithmetic net comparison by account, business unit, and dimensions only; it is not an approved reversal.</p>
        </aside>
        <section className="invoice-fields" aria-label="Source selection and reversal details">
          <label>Source journal · required · select explicitly
            <select aria-label="Source journal" value={draft.source_journal_id} onChange={(event) => void selectSourceJournal(event.target.value)}>
              <option value="">Select a source journal</option>
              {journals.map((item) => <option key={item.id} value={item.id}>Journal {item.id} · {item.title_en} · current revision {item.revision}{item.kind ? ` · ${item.kind}` : ""}</option>)}
            </select>
          </label>
          <label>Specific saved source revision · required
            <select aria-label="Source revision" value={draft.source_revision} disabled={!availableRevisions.length || sourceLoading}
              onChange={(event) => void selectSourceRevision(event.target.value)}>
              <option value="">Select an exact saved revision</option>
              {availableRevisions.map((revision) => <option key={revision} value={revision}>Revision {revision}</option>)}
            </select>
          </label>
          <label>Proposed date · required
            <input type="date" aria-label="Proposed date" value={draft.proposed_date} onChange={(event) => markChanged({ ...draft, proposed_date: event.target.value })} />
          </label>
          <label className="invoice-wide">Explicit explanation · English · required · up to 200 characters
            <textarea aria-label="Explicit explanation" maxLength={200} value={draft.explanation} onChange={(event) => markChanged({ ...draft, explanation: event.target.value })} />
          </label>
        </section>
        <section className="invoice-panel" aria-label="Read-only source journal snapshot">
          <p className="invoice-kicker">IMMUTABLE SOURCE · READ ONLY</p>
          {sourceLoading && <p role="status">Loading selected source revision…</p>}
          {!source && !sourceLoading && <p>Select a journal and a specific revision to display its immutable source snapshot. No source is presumed.</p>}
          {source && <><h2>{source.title_en}</h2><p>Journal {source.id} · revision {source.revision} · type {source.kind}</p>
            <p>This source is not an operational JournalEntry in this worksheet.</p>
            <ul>{source.lines.map((line, index) => <li key={index}>{sourceLineText(line, index)}</li>)}</ul>
            {source.review && <p>Source snapshot review: balanced {typeof source.review.balanced === "boolean" ? (source.review.balanced ? "yes" : "no") : "not supplied"} · debit {source.review.debit_total ?? "not supplied"} · credit {source.review.credit_total ?? "not supplied"}</p>}
          </>}
        </section>
        <section className="invoice-panel" aria-label="Explicit proposed reversal lines">
          <div className="invoice-heading"><div><p className="invoice-kicker">MANUAL INPUTS ONLY</p><h2>Proposed lines</h2></div>
            <button type="button" className="invoice-secondary" onClick={() => markChanged({ ...draft, lines: [...draft.lines, emptyLine()] })}>Add explicit line</button>
          </div>
          {draft.lines.length < 2 && <p>At least two explicit proposed lines are required. Each line must be entered; none are generated from the source.</p>}
          {draft.lines.map((line, index) => <fieldset className="invoice-line" key={index}>
            <legend>Proposed line {index + 1}</legend>
            <div className="invoice-fields">
              <label>Account · required
                <select aria-label={`Account line ${index + 1}`} value={line.account_id} onChange={(event) => updateLine(index, { account_id: event.target.value })}>
                  <option value="">Select a saved account</option>
                  {context.accounts.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name_en}</option>)}
                </select>
              </label>
              <label>Business unit · optional
                <select aria-label={`Business unit line ${index + 1}`} value={line.business_unit_id} onChange={(event) => updateLine(index, { business_unit_id: event.target.value })}>
                  <option value="">None</option>{context.business_units.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name}</option>)}
                </select>
              </label>
              <label className="invoice-wide">Dimension values · optional · select explicitly
                <select multiple aria-label={`Dimensions line ${index + 1}`} value={line.dimension_value_ids}
                  onChange={(event) => updateLine(index, { dimension_value_ids: Array.from(event.target.selectedOptions, (option) => option.value) })}>
                  {context.dimension_values.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.label_en}</option>)}
                </select>
              </label>
              <label>Debit · QAR
                <input aria-label={`Debit line ${index + 1}`} inputMode="decimal" value={line.debit} onChange={(event) => updateLine(index, { debit: event.target.value })} />
              </label>
              <label>Credit · QAR
                <input aria-label={`Credit line ${index + 1}`} inputMode="decimal" value={line.credit} onChange={(event) => updateLine(index, { credit: event.target.value })} />
              </label>
              <label className="invoice-wide">Memo · English · optional
                <input aria-label={`Memo line ${index + 1}`} value={line.memo_en} onChange={(event) => updateLine(index, { memo_en: event.target.value })} />
              </label>
            </div>
            <button type="button" className="invoice-secondary" onClick={() => markChanged({ ...draft, lines: draft.lines.filter((_, itemIndex) => itemIndex !== index) })}>Remove line</button>
          </fieldset>)}
        </section>
        {!!issues.length && <div className="invoice-note" role="note" aria-label="Reversal validation"><strong>Complete explicit inputs before saving</strong><ul>{issues.map((item) => <li key={item}>{item}</li>)}</ul></div>}
        <div className="invoice-actions">
          <button type="button" disabled={busy || sourceLoading || !!issues.length || conflict || uncertain} onClick={() => void save()}>
            {current ? "Save reversal revision" : "Save reversal proposal"}
          </button>
        </div>
      </fieldset>}
      {uncertain && <section className="invoice-panel" role="alert"><h2>Save outcome unknown</h2>
        <p>{current
          ? "The edit may have committed. Keep local inputs until you inspect the saved register and revision history, then explicitly choose whether to reopen. Create-request status and replay do not recover edits."
          : "Do not create a replacement. Refresh and inspect the saved register, check or retry only the exact original request in the recovery panel, then explicitly reopen a saved proposal to continue."}</p>
        <button type="button" className="invoice-secondary" disabled={busy} onClick={() => void run(loadLists)}>Refresh and inspect saved register</button>
      </section>}
      {current && <section className="invoice-review" aria-label="Saved server reversal review">
        <p className="invoice-kicker">SERVER REVIEW · ARITHMETIC COMPARISON ONLY</p><h2>Saved review · revision {current.revision}</h2>
        {dirty && <p role="note">Unsaved inputs are not included in this saved review. No comparison has been calculated for the current unsaved inputs.</p>}
        <p>Currency: {current.review.currency} · debit {current.review.debit_total} · credit {current.review.credit_total} · difference {current.review.difference}</p>
        <p>Balanced: {current.review.balanced ? "yes" : "no"} · offsets source: {current.review.offsets_source ? "yes" : "no"}</p>
        <p>{current.review.comparison}</p>
        <p><strong>Not approved. Not posted. No operational journal or ledger changes.</strong></p>
        {current.review.issues.length > 0 && <ul>{current.review.issues.map((item, index) => <li key={index}>{item}</li>)}</ul>}
        <div className="invoice-table-scroll"><table><caption>Net comparison buckets · server provided</caption><thead><tr><th>Account ID</th><th>BU ID</th><th>Dimension IDs</th><th>Combined net</th></tr></thead>
          <tbody>{current.review.bucket_differences.map((item, index) => <tr key={index}><td>{item.account_id}</td><td>{item.business_unit_id ?? "None"}</td><td>{item.dimension_value_ids.join(", ") || "None"}</td><td>{item.combined_net}</td></tr>)}</tbody>
        </table></div>
      </section>}
      {conflict && current && <section className="invoice-panel" role="alert"><h2>Saved revision changed</h2><p>Your local inputs remain unchanged. Reload only after explicit confirmation.</p>
        <button type="button" className="invoice-secondary" disabled={busy} onClick={() => {
          if (window.confirm("Discard local reversal edits and reload the saved version?")) void openReversal(current.id, true);
        }}>Reload saved reversal</button>
      </section>}
      {current && <section className="invoice-history" aria-label="Reversal revision history"><h2>Saved proposal history</h2>
        <p>Snapshots contain proposal inputs only; none are approval or posting records.</p>
        {current.history.map((item) => <details key={item.revision}><summary>Revision {item.revision} · source {item.snapshot.source_journal_id}, revision {item.snapshot.source_revision} · {item.snapshot.proposed_date}</summary>
          <p>{item.created_at} · {item.snapshot.explanation}</p>
          <ul>{item.snapshot.lines.map((line, index) => <li key={index}>Account {line.account_id} · BU {line.business_unit_id ?? "none"} · dimensions {line.dimension_value_ids.join(", ") || "none"} · debit {line.debit} · credit {line.credit}{line.memo_en ? ` · ${line.memo_en}` : ""}</li>)}</ul>
        </details>)}
      </section>}
    </>}
  </main>;
}
export default FinanceReversalLab;
