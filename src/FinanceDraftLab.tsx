import { useEffect, useRef, useState } from "react";
import { FinanceDraftWorkspace } from "./FinanceDraftWorkspace";
import type { DraftSnapshot } from "./FinanceDraftWorkspace";
import { editorState, journalPayload, labRequest, LabError } from "./finance-draft-lab-api";
import type { LabContext, SavedDraft } from "./finance-draft-lab-api";
import { FinanceRecurringProposal, FinanceRecurringPreview } from "./FinanceRecurringProposal";
import type { RecurringInputs, RecurringPreview } from "./FinanceRecurringProposal";
import { reopenFromQuery } from "./finance-lab-deep-link";
// Keep existing imports/test entry points compatible without eagerly importing
// this journal implementation from the standalone shell.
export { FinanceDraftLab } from "./FinanceLabShell";

type DraftKind = "journal" | "recurring_template" | "opening_balance" | "accrual_adjustment" | "prepayment_adjustment";
type Summary = { id: number; title_en: string; title_ar: string; revision: number; kind?: DraftKind; as_of_date?: string | null };
const emptyRecurrence = (): RecurringInputs => ({ start: "", end: "", datesText: "" });

export function JournalLab() {
  const [authenticated, setAuthenticated] = useState(false);
  const [context, setContext] = useState<LabContext | null>(null);
  const [drafts, setDrafts] = useState<Summary[]>([]);
  const [draft, setDraft] = useState<SavedDraft | undefined>();
  const [editorKey, setEditorKey] = useState(0);
  const [initialDraft, setInitialDraft] = useState<SavedDraft | undefined>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [conflict, setConflict] = useState(false);
  const [uncertainSave, setUncertainSave] = useState(false);
  const [kind, setKind] = useState<DraftKind>("journal");
  const [asOfDate, setAsOfDate] = useState("");
  const [recurrence, setRecurrence] = useState<RecurringInputs>(emptyRecurrence);
  const [preview, setPreview] = useState<RecurringPreview | null>(null);
  const dirty = useRef(false);
  const pending = useRef(false);
  const alive = useRef(true);
  const requestVersion = useRef(0);

  useEffect(() => {
    alive.current = true;
    void labRequest<{ authenticated: boolean }>("session").then((session) => {
      if (!alive.current) return;
      setAuthenticated(session.authenticated);
       if (session.authenticated) void refresh().then(() => {
         if (alive.current) reopenFromQuery((id) => { void open(id); }, setError);
       }).catch((e: Error) => { if (alive.current) setError(e.message); });
    }).catch((e: Error) => { if (alive.current) setError(e.message); });
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirty.current || pending.current) event.preventDefault();
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => { alive.current = false; requestVersion.current++; window.removeEventListener("beforeunload", beforeUnload); };
  }, []);

  async function refresh() {
    const [choices, result] = await Promise.all([
      labRequest<LabContext>("context"), labRequest<{ drafts: Summary[] }>("journals"),
    ]);
    if (alive.current) { setContext(choices); setDrafts(result.drafts); }
    return choices;
  }
  async function run(action: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError("");
    try { await action(); }
    catch (e) {
      if (alive.current) {
        setError(`${e instanceof Error ? e.message : "Lab unavailable."} Inputs remain unchanged.`);
        if (e instanceof LabError && e.status === 403) {
          setContext(null); setDraft(undefined); setInitialDraft(undefined); setDrafts([]); dirty.current = false;
          setAuthenticated(false); setStatus("Synthetic test access is unavailable.");
          setPreview(null); setRecurrence(emptyRecurrence()); setKind("journal"); setAsOfDate("");
        }
      }
    } finally { pending.current = false; if (alive.current) setBusy(false); }
  }
  function mayReplace() {
    return !dirty.current || window.confirm("Discard unsaved local edits and open the saved version?");
  }
  async function open(id: number) {
    if (!mayReplace()) return;
    await run(async () => {
      const version = ++requestVersion.current;
      const [row, choices] = await Promise.all([labRequest<SavedDraft>(`journals/${id}`), labRequest<LabContext>("context")]);
      if (!alive.current || version !== requestVersion.current) return;
      setContext(choices); setDraft(row); setInitialDraft(row); setEditorKey((key) => key + 1);
      setConflict(false); setUncertainSave(false); dirty.current = false;
      setKind(row.kind ?? "journal"); setPreview(null);
      setAsOfDate(row.kind === "opening_balance" ? row.as_of_date ?? "" : "");
      setRecurrence(row.recurrence_proposal?.dates ? {
        start: row.recurrence_proposal.start, end: row.recurrence_proposal.end,
        datesText: row.recurrence_proposal.dates.join("\n"),
      } : emptyRecurrence());
      setStatus(`Opened saved draft ${row.id}, revision ${row.revision}.`);
    });
  }
  async function save(snapshot: DraftSnapshot) {
    if (!context || pending.current || uncertainSave) return;
    await run(async () => {
      try {
        const row = await labRequest<SavedDraft>(draft ? `journals/${draft.id}` : "journals", draft ? "PUT" : "POST", {
          ...journalPayload(snapshot, context), ...(draft ? { expected_revision: draft.revision } : {}),
          kind,
           ...(kind === "opening_balance" ? { as_of_date: snapshot.asOfDate ?? asOfDate } : {}),
          recurrence_proposal: kind === "recurring_template" ? {
            start: recurrence.start, end: recurrence.end, dates: recurrence.datesText.split(/\r?\n/),
          } : {},
           adjustment_proposal: kind === "accrual_adjustment" || kind === "prepayment_adjustment"
             ? { date: snapshot.adjustmentProposal?.date ?? "", explanation: snapshot.adjustmentProposal?.explanation ?? "" }
             : {},
        });
        if (!alive.current) return;
        setDraft(row); setConflict(false); setPreview(null); dirty.current = false;
         setAsOfDate(row.kind === "opening_balance" ? row.as_of_date ?? snapshot.asOfDate ?? "" : "");
         setStatus(`Saved ${kind === "recurring_template" ? "recurring template" : kind === "opening_balance" ? "opening-balance worksheet" : kind === "accrual_adjustment" ? "accrual adjustment proposal" : kind === "prepayment_adjustment" ? "prepayment adjustment proposal" : "draft"} ${row.id}, revision ${row.revision} in disposable PostgreSQL — proposal only; not posted or scheduled.`);
        // A list-refresh failure must never misrepresent a committed save as failed.
        try {
          const result = await labRequest<{ drafts: Summary[] }>("journals");
          if (alive.current) setDrafts(result.drafts);
        } catch {
          if (alive.current) setError("Draft saved, but list refresh failed. Refresh the list; do not create a replacement.");
        }
      } catch (e) {
        if (alive.current && e instanceof LabError && e.status === 409) setConflict(true);
        if (alive.current && !(e instanceof LabError)) {
          setUncertainSave(true);
          setStatus("Save response unavailable; the database may have committed. Refresh the list and reopen the saved draft before another save.");
        }
        throw e;
      }
    });
  }
  function newDraft(nextKind: DraftKind) {
    if (pending.current || !mayReplace()) return;
    dirty.current = false; setDraft(undefined); setInitialDraft(undefined); setConflict(false); setUncertainSave(false);
    setKind(nextKind); setAsOfDate(""); setRecurrence(emptyRecurrence()); setPreview(null);
    setStatus("New unsaved proposal"); setEditorKey((key) => key + 1);
  }

  return <div className="finance-draft-prototype finance-lab">
    <header className="finance-draft-banner">
      <h1>Disposable Finance draft lab</h1>
      <p>Review saved work or reopen any existing isolated proposal from the shared lab navigation above.</p>
      <p>No shared users or permissions. Only the explicitly opened synthetic test session can use this isolated backend.
        Drafts survive reload while this runner is active, not after it exits.</p>
      {!authenticated && <button disabled={busy} onClick={() => void run(async () => {
        const session = await labRequest<{ authenticated: boolean }>("session", "POST", {});
        setAuthenticated(session.authenticated); await refresh();
      })}>Open isolated synthetic test session</button>}
    </header>
    {error && <p role="alert" className="finance-draft-alert">{error}</p>}
    <p role="status" aria-live="polite">{busy ? "Working with disposable database…" : status}</p>
    {authenticated && <section className="finance-draft-card" aria-label="Persisted synthetic draft register">
      <h2>Saved database drafts</h2>
      <button disabled={busy} onClick={() => void run(async () => { await refresh(); })}>Refresh saved list</button>
      <button disabled={busy || !context} onClick={() => newDraft("journal")}>New draft</button>
      <button disabled={busy || !context} onClick={() => newDraft("opening_balance")}>New opening-balance worksheet</button>
      <button disabled={busy || !context} onClick={() => newDraft("recurring_template")}>New recurring template</button>
      <button disabled={busy || !context} onClick={() => newDraft("accrual_adjustment")}>New accrual proposal</button>
      <button disabled={busy || !context} onClick={() => newDraft("prepayment_adjustment")}>New prepayment proposal</button>
      {!drafts.length && <p>No saved drafts.</p>}
      <section aria-label="Opening-balance worksheet register">
        <h3>Opening-balance worksheets — synthetic, not imported or posted</h3>
        {!drafts.some((item) => item.kind === "opening_balance") && <p>No saved opening-balance worksheets.</p>}
        <ul>{drafts.filter((item) => item.kind === "opening_balance").map((item) => <li key={item.id}>
          <button disabled={busy} onClick={() => void open(item.id)}>Open worksheet {item.id}: {item.title_en} — revision {item.revision}</button>
          {item.as_of_date && <span>As of {item.as_of_date}</span>}
          {item.title_ar && <span lang="ar" dir="rtl">{item.title_ar}</span>}
        </li>)}</ul>
      </section>
      <ul>{drafts.filter((item) => item.kind !== "recurring_template" && item.kind !== "opening_balance" && item.kind !== "accrual_adjustment" && item.kind !== "prepayment_adjustment").map((item) => <li key={item.id}>
        <button disabled={busy} onClick={() => void open(item.id)}>Open draft {item.id}: {item.title_en} — revision {item.revision}</button>
        {item.title_ar && <span lang="ar" dir="rtl">{item.title_ar}</span>}
      </li>)}</ul>
      <section aria-label="Recurring template register">
        <h3>Recurring template proposals — not scheduled</h3>
        {!drafts.some((item) => item.kind === "recurring_template") && <p>No saved templates.</p>}
        <ul>{drafts.filter((item) => item.kind === "recurring_template").map((item) => <li key={item.id}>
          <button disabled={busy} onClick={() => void open(item.id)}>Open template {item.id}: {item.title_en} — revision {item.revision}</button>
          <span lang="ar" dir="rtl">{item.title_ar}</span>
        </li>)}</ul>
      </section>
      <section aria-label="Saved adjustment proposal register">
        <h3>Saved adjustment proposals — explicit inputs only; not posted</h3>
        {!drafts.some((item) => item.kind === "accrual_adjustment" || item.kind === "prepayment_adjustment") && <p>No saved adjustment proposals.</p>}
        <ul>{drafts.filter((item) => item.kind === "accrual_adjustment" || item.kind === "prepayment_adjustment").map((item) => <li key={item.id}>
          <button disabled={busy} onClick={() => void open(item.id)}>Open {item.kind === "accrual_adjustment" ? "accrual" : "prepayment"} proposal {item.id}: {item.title_en} — revision {item.revision}</button>
          {item.title_ar && <span lang="ar" dir="rtl">{item.title_ar}</span>}
        </li>)}</ul>
      </section>
      {conflict && draft && <div role="alert">
        <p>Conflicting edit: your inputs were not saved or discarded. Reload explicitly before editing again.</p>
        <button disabled={busy} onClick={() => void open(draft.id)}>Reload saved version (discard local edits)</button>
      </div>}
    </section>}
    {authenticated && context && <fieldset className="finance-lab-editor" disabled={busy} aria-busy={busy}
      onChangeCapture={() => { dirty.current = true; setPreview(null); }}>
       <legend>{draft ? `Editing ${kind === "recurring_template" ? "template" : kind === "opening_balance" ? "opening-balance worksheet" : kind === "accrual_adjustment" ? "accrual adjustment proposal" : kind === "prepayment_adjustment" ? "prepayment adjustment proposal" : "draft"} ${draft.id}, base revision ${draft.revision}` : kind === "recurring_template" ? "New unsaved recurring template" : kind === "opening_balance" ? "New unsaved opening-balance worksheet" : kind === "accrual_adjustment" ? "New unsaved accrual adjustment proposal" : kind === "prepayment_adjustment" ? "New unsaved prepayment adjustment proposal" : "New unsaved draft"}</legend>
      {kind === "recurring_template" && <FinanceRecurringProposal value={recurrence} onChange={(value) => {
        setRecurrence(value); dirty.current = true; setPreview(null);
      }} />}
      <FinanceDraftWorkspace key={editorKey} legalEntityLabel={context.legal_entity_label}
        businessUnits={context.business_units.map((unit) => ({ code: unit.code, nameEn: unit.name }))}
        initialState={editorState(context, initialDraft)} onSave={save} onDraftChange={() => { dirty.current = true; setPreview(null); }}
          worksheetKind={kind === "opening_balance" ? "opening_balance"
            : kind === "accrual_adjustment" || kind === "prepayment_adjustment" ? kind : "journal"}
          initialAsOfDate={asOfDate}
          adjustmentKind={kind === "accrual_adjustment" || kind === "prepayment_adjustment" ? kind : undefined}
          initialAdjustmentProposal={initialDraft?.adjustment_proposal}
        saveUnavailableReason={uncertainSave ? "Unknown save outcome: refresh the saved list and explicitly reopen; no automatic retry." : undefined}
        onSetup={async (kind, data) => {
          let failure: unknown;
          await run(async () => {
            try {
              const payload: Record<string, unknown> = { ...data };
              if (kind === "dimension-values") {
                payload.dimension_id = context.dimensions.find((d) => d.code === data.dimension_code)?.id;
                delete payload.dimension_code;
              }
              await labRequest(kind, "POST", payload); await refresh();
              setStatus("Synthetic setup saved in disposable PostgreSQL.");
            } catch (e) { failure = e; throw e; }
          });
          if (failure) throw failure;
        }} />
    </fieldset>}
    {authenticated && draft?.kind === "recurring_template" && <section className="finance-draft-card">
      <h2>Preview saved template revision {draft.revision}</h2>
      <p>Unsaved edits are not included. Save balanced changes first. This creates no jobs or journals.</p>
      <button disabled={busy || uncertainSave} onClick={() => {
        if (dirty.current) { setPreview(null); setError("Save current balanced changes before preview; your inputs are retained."); return; }
        void run(async () => {
          const result = await labRequest<RecurringPreview>(`journals/${draft.id}/preview`);
          if (alive.current) setPreview(result);
        });
      }}>Preview saved proposed occurrences</button>
      {preview && <FinanceRecurringPreview preview={preview} />}
    </section>}
    {draft && <section className="finance-draft-card" aria-label="Preserved draft revision history">
      <h2>Preserved saved revisions</h2>
      <p>Database-protected snapshots; not approvals or posted entries.</p>
      {draft.history.map((item) => <details key={item.revision}>
        <summary>Revision {item.revision} — {item.snapshot.title_en} · {item.snapshot.review.debit_total} = {item.snapshot.review.credit_total} QAR{item.snapshot.as_of_date ? ` · as of ${item.snapshot.as_of_date}` : ""}</summary>
        <p>{item.created_at}</p><p lang="ar" dir="rtl">{item.snapshot.title_ar}</p>
        {item.snapshot.recurrence_proposal?.dates && <p>Proposed dates: {item.snapshot.recurrence_proposal.dates.join(", ")}</p>}
         {item.snapshot.adjustment_proposal?.date && <section aria-label="Preserved adjustment proposal metadata">
           <p>Proposed date: {item.snapshot.adjustment_proposal.date}</p>
           <p>Explicit explanation: {item.snapshot.adjustment_proposal.explanation}</p>
           <p>Proposal only — no recognition, allocation, scheduling, approval, import, or posting.</p>
         </section>}
        <ul>{item.snapshot.lines.map((line, index) => <li key={index}>{line.account_code} · {line.business_unit_code || "No BU"} · {line.dimensions.map((value) => value.code).join(", ")} · {line.debit} / {line.credit} · {line.memo_en} <span lang="ar" dir="rtl">{line.memo_ar}</span></li>)}</ul>
      </details>)}
    </section>}
  </div>;
}
