import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Line = { account_code: string; account?: string; debit: string; credit: string; memo: string };
type Journal = {
  id: number; number: string; date: string; description: string; status: "draft" | "submitted" | "posted"; status_label: string;
  reverse_next_month: boolean; total: string; balanced: boolean; difference: string; prepared_by: string; prepared_at: string;
  returned_reason: string; approved_by: string; approved_at: string; journal_entry_id: number | null; auto_reversal_entry_id: number | null;
  reversal_of: string; reversed_by: string; can_edit?: boolean; can_decide?: boolean; can_reverse?: boolean;
  lines?: Line[]; history?: { at: string; by: string; change: string; note: string }[];
};
type Account = { code: string; name: string; kind: string };
type Overview = {
  unit: { id: number; code: string; name: string }; journals: Journal[]; waiting: number; waiting_for_me: number;
  accounts: Account[]; control_accounts: string[]; can_prepare: boolean; can_approve: boolean; policy: string;
};

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string"
      ? data.detail
      : Object.values(data).flat().join(" ") || "The request could not be completed.";
    throw new Error(String(detail));
  }
  return data as T;
}

const today = () => new Date().toLocaleDateString("en-CA");
const fmt = (value: string | number) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const json = (csrfToken: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken });
const KINDS: [string, string][] = [["asset", "Assets"], ["liability", "Liabilities"], ["revenue", "Income"], ["expense", "Costs"]];
const STATUS_CLASS: Record<Journal["status"], string> = { draft: "mj-draft", submitted: "ex-waiting", posted: "ex-booked" };
const blank = (): Line => ({ account_code: "", debit: "", credit: "", memo: "" });
const cents = (value: string) => Math.round((Number(value) || 0) * 100);

function Editor({ overview, journal, csrfToken, onDone, onCancel }: {
  overview: Overview; journal: Journal | null; csrfToken: string; onDone: (journal: Journal, message: string) => void; onCancel: () => void;
}) {
  const locked = Boolean(journal?.reversal_of);
  const [date, setDate] = useState(journal?.date ?? today());
  const [description, setDescription] = useState(journal ? journal.description : "");
  const [reverse, setReverse] = useState(journal?.reverse_next_month ?? false);
  const [lines, setLines] = useState<Line[]>(journal?.lines?.length
    ? journal.lines.map((line) => ({ ...line, debit: line.debit === "0.00" ? "" : line.debit, credit: line.credit === "0.00" ? "" : line.credit }))
    : [blank(), blank()]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const debit = lines.reduce((sum, line) => sum + cents(line.debit), 0);
  const credit = lines.reduce((sum, line) => sum + cents(line.credit), 0);
  const set = (index: number, field: keyof Line, value: string) =>
    setLines(lines.map((line, at) => at !== index ? line
      : { ...line, [field]: value, ...(field === "debit" && value ? { credit: "" } : {}), ...(field === "credit" && value ? { debit: "" } : {}) }));
  const send = async (submit: boolean) => {
    setBusy(true); setError("");
    const body = { date, description, reverse_next_month: reverse, submit, lines: lines.map(({ account_code, debit: d, credit: c, memo }) => ({ account_code, debit: d, credit: c, memo })) };
    try {
      const result = journal
        ? await call<Journal>(`/api/finance/journals/${journal.id}/?unit=${overview.unit.id}`, { method: "POST", headers: json(csrfToken), body: JSON.stringify({ ...body, action: "save" }) })
        : await call<Journal>(`/api/finance/journals/?unit=${overview.unit.id}`, { method: "POST", headers: json(csrfToken), body: JSON.stringify(body) });
      onDone(result, submit ? `${result.number} is waiting for someone else to approve it.` : `${result.number} is saved as a draft.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The journal could not be saved.");
    } finally { setBusy(false); }
  };
  const submit = (event: FormEvent) => { event.preventDefault(); void send(true); };
  return <form className="sp-form mj-editor" onSubmit={submit} aria-label={journal ? `Change ${journal.number}` : "Prepare a journal"}>
    <h3>{journal ? `Change ${journal.number}` : "Prepare a journal"}</h3>
    {journal?.returned_reason && <p className="mj-returned"><strong>Sent back:</strong> {journal.returned_reason}</p>}
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-fields">
      <label>Date<input type="date" value={date} max={today()} onChange={(event) => setDate(event.target.value)} required /></label>
      <label className="mj-wide">What it is for<input value={description} maxLength={200} onChange={(event) => setDescription(event.target.value)} required
        placeholder="e.g. October electricity, bill not received yet" /></label>
    </div>
    {!locked && <label className="bc-check mj-reverse"><input type="checkbox" checked={reverse} onChange={(event) => setReverse(event.target.checked)} />
      <span>Undo it on the 1st of next month<small>For an accrual: the cost counts this month, and the real bill replaces it next month.</small></span></label>}
    {locked && <p className="sp-hint">This journal reverses {journal?.reversal_of}, so its lines are the same with the sides swapped and cannot be changed.</p>}
    <div className="sp-table-wrap"><table className="sp-table mj-lines" aria-label="Journal lines">
      <thead><tr><th scope="col">Account</th><th scope="col" className="bc-num">Debit</th><th scope="col" className="bc-num">Credit</th><th scope="col">Note</th><th scope="col"><span className="sr-only">Remove</span></th></tr></thead>
      <tbody>{lines.map((line, index) => <tr key={index}>
        <td><select aria-label={`Account, line ${index + 1}`} value={line.account_code} disabled={locked} onChange={(event) => set(index, "account_code", event.target.value)}>
          <option value="">Choose an account…</option>
          {KINDS.map(([kind, label]) => <optgroup key={kind} label={label}>
            {overview.accounts.filter((account) => account.kind === kind).map((account) => <option key={account.code} value={account.code}>{account.name}</option>)}
          </optgroup>)}
        </select></td>
        <td><input aria-label={`Debit, line ${index + 1}`} inputMode="decimal" value={line.debit} disabled={locked} onChange={(event) => set(index, "debit", event.target.value)} /></td>
        <td><input aria-label={`Credit, line ${index + 1}`} inputMode="decimal" value={line.credit} disabled={locked} onChange={(event) => set(index, "credit", event.target.value)} /></td>
        <td><input aria-label={`Note, line ${index + 1}`} value={line.memo} maxLength={120} disabled={locked} onChange={(event) => set(index, "memo", event.target.value)} /></td>
        <td>{!locked && lines.length > 2 && <button className="button button-quiet" type="button" aria-label={`Remove line ${index + 1}`} onClick={() => setLines(lines.filter((_, at) => at !== index))}>Remove</button>}</td>
      </tr>)}</tbody>
      <tfoot><tr><th scope="row">Total</th><td className="pw-mono bc-num">{fmt(debit / 100)}</td><td className="pw-mono bc-num">{fmt(credit / 100)}</td>
        <td colSpan={2}>{debit === credit && debit > 0
          ? <span className="ex-status ex-booked">Balanced</span>
          : <span className="ex-status ex-rejected">{debit === 0 && credit === 0 ? "Enter the amounts" : `Out by ${fmt(Math.abs(debit - credit) / 100)}`}</span>}</td></tr></tfoot>
    </table></div>
    {!locked && <button className="button button-outline mj-add" type="button" onClick={() => setLines([...lines, blank()])}>Add a line</button>}
    <p className="sp-hint">Not available here, because their own screens keep them right: {overview.control_accounts.join("; ")}.</p>
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy || debit !== credit || debit === 0}>Send for approval</button>
      <button className="button button-outline" type="button" disabled={busy} onClick={() => void send(false)}>Save as draft</button>
      <button className="button button-quiet" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </form>;
}

function Detail({ journal, overview, csrfToken, onChanged, onEdit, onClose }: {
  journal: Journal; overview: Overview; csrfToken: string; onChanged: (journal: Journal, message: string) => void;
  onEdit: () => void; onClose: () => void;
}) {
  const [mode, setMode] = useState<"" | "return" | "reverse">("");
  const [reason, setReason] = useState("");
  const [date, setDate] = useState(today());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const act = async (body: Record<string, string>, message: (result: Journal) => string) => {
    setBusy(true); setError("");
    try {
      const result = await call<Journal>(`/api/finance/journals/${journal.id}/?unit=${overview.unit.id}`, { method: "POST", headers: json(csrfToken), body: JSON.stringify(body) });
      setMode(""); onChanged(result, message(result));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That could not be done.");
    } finally { setBusy(false); }
  };
  const mine = !journal.can_decide && journal.status === "submitted" && overview.can_approve;
  return <article className="sp-form mj-detail" aria-label={`Journal ${journal.number}`}>
    <header className="mj-detail-head">
      <h3>{journal.number} <span className={`ex-status ${STATUS_CLASS[journal.status]}`}>{journal.status_label}</span>
        <small>{journal.date} · {show(journal.description)}</small></h3>
      <button className="button button-quiet" type="button" onClick={onClose}>Close</button>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {journal.returned_reason && journal.status === "draft" && <p className="mj-returned"><strong>Sent back:</strong> {journal.returned_reason}</p>}
    {journal.reverse_next_month && <p className="sp-hint">Undoes itself on the 1st of the following month{journal.auto_reversal_entry_id ? ` (journal #${journal.auto_reversal_entry_id})` : ""}.</p>}
    {journal.reversal_of && <p className="sp-hint">Reverses {journal.reversal_of}.</p>}
    {journal.reversed_by && <p className="sp-hint">Reversed by {journal.reversed_by}.</p>}
    <div className="sp-table-wrap"><table className="sp-table mj-lines" aria-label={`Lines of ${journal.number}`}>
      <thead><tr><th scope="col">Account</th><th scope="col" className="bc-num">Debit</th><th scope="col" className="bc-num">Credit</th><th scope="col">Note</th></tr></thead>
      <tbody>{(journal.lines ?? []).map((line, index) => <tr key={index}>
        <td>{line.account}</td><td className="pw-mono bc-num">{line.debit === "0.00" ? "" : fmt(line.debit)}</td>
        <td className="pw-mono bc-num">{line.credit === "0.00" ? "" : fmt(line.credit)}</td><td>{show(line.memo)}</td></tr>)}</tbody>
      <tfoot><tr><th scope="row">Total</th><td className="pw-mono bc-num">{fmt(journal.total)}</td><td className="pw-mono bc-num">{fmt(Number(journal.total) - Number(journal.difference))}</td><td /></tr></tfoot>
    </table></div>
    <div className="sp-actions ex-actions">
      {journal.can_decide && <>
        <button className="button button-dark" type="button" disabled={busy} onClick={() => void act({ action: "approve" }, (result) => `${result.number} is approved and posted.`)}>Approve and post</button>
        <button className="button button-outline" type="button" disabled={busy} onClick={() => setMode(mode === "return" ? "" : "return")}>Send back</button>
      </>}
      {mine && <span className="sp-muted">You prepared this journal, so someone else must approve it.</span>}
      {journal.can_edit && <>
        <button className="button button-dark" type="button" disabled={busy} onClick={onEdit}>Change</button>
        <button className="button button-outline" type="button" disabled={busy || !journal.balanced} onClick={() => void act({ action: "submit" }, (result) => `${result.number} is waiting for someone else to approve it.`)}>Send for approval</button>
      </>}
      {journal.status === "submitted" && !journal.can_decide && journal.prepared_by && overview.can_prepare && !overview.can_approve &&
        <button className="button button-outline" type="button" disabled={busy} onClick={() => void act({ action: "withdraw" }, (result) => `${result.number} is back in draft.`)}>Take back</button>}
      {journal.can_reverse && <button className="button button-outline" type="button" disabled={busy} onClick={() => setMode(mode === "reverse" ? "" : "reverse")}>Reverse</button>}
    </div>
    {mode === "return" && <div className="ex-inline mj-inline">
      <input aria-label="Why it is sent back" value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder="Why it is sent back" />
      <button className="button button-dark" type="button" disabled={busy || !reason.trim()} onClick={() => void act({ action: "return", reason }, (result) => `${result.number} is sent back to ${result.prepared_by}.`)}>Send back</button>
    </div>}
    {mode === "reverse" && <div className="ex-inline mj-inline">
      <label>Reversal date<input type="date" value={date} min={journal.date} max={today()} onChange={(event) => setDate(event.target.value)} /></label>
      <button className="button button-dark" type="button" disabled={busy} onClick={() => void act({ action: "reverse", date }, (result) => `${result.number} is prepared as a draft that reverses ${journal.number}. Send it for approval when ready.`)}>Prepare the reversal</button>
    </div>}
    {journal.history && journal.history.length > 0 && <ul className="bg-history" aria-label="History">
      {journal.history.map((row, index) => <li key={index}>{new Date(row.at).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })} · {row.by}: <strong>{row.change}</strong>{row.note ? ` (${show(row.note)})` : ""}</li>)}
    </ul>}
  </article>;
}

export default function ManualJournals({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [status, setStatus] = useState("");
  const [month, setMonth] = useState("");
  const [editing, setEditing] = useState<Journal | null | "new">(null);
  const [open, setOpen] = useState<Journal | null>(null);

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    const query = new URLSearchParams({ unit: String(selectedUnitId), ...(status ? { status } : {}), ...(month ? { month } : {}) });
    try {
      setOverview(await call<Overview>(`/api/finance/journals/?${query}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Journal entries could not be loaded.");
    }
  }, [selectedUnitId, status, month]);
  useEffect(() => { void load(); }, [load]);
  const view = async (id: number) => {
    if (!overview) return;
    setNotice(""); setEditing(null);
    try {
      setOpen(await call<Journal>(`/api/finance/journals/${id}/?unit=${overview.unit.id}`));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The journal could not be opened.");
    }
  };
  const changed = (journal: Journal, message: string) => { setEditing(null); setOpen(journal); setNotice(message); void load(); };

  return <section className="content-card supplier-payments bank-cash manual-journals" aria-labelledby="manual-journals-title">
    <header className="sp-head">
      <span className="eyebrow">FINANCE · JOURNAL ENTRIES · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="manual-journals-title">Journal entries</h2>
      <p>Entries Finance types in by hand: corrections, costs of the month not billed yet (accruals) and costs paid ahead (prepayments). One person prepares a journal and a different person approves it; only then does it reach the accounts. A posted journal is never changed: it is reversed by a new one.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading journal entries…</p>}
    {overview && <>
      <ul className="bc-accounts ex-tiles" aria-label="Waiting for approval">
        <li className="bc-total"><span>Waiting for approval</span><strong>{overview.waiting}</strong><small>{overview.can_approve ? `${overview.waiting_for_me} for you to approve` : "approved by someone with the approve permission"}</small></li>
      </ul>
      <div className="bc-filters">
        <label>Show<select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All</option><option value="submitted">Waiting for approval</option><option value="draft">Drafts</option><option value="posted">Posted</option>
        </select></label>
        <label>Month<input type="month" value={month} onChange={(event) => setMonth(event.target.value)} /></label>
        {overview.can_prepare && editing === null && <div className="bc-buttons">
          <button className="button button-dark" type="button" onClick={() => { setNotice(""); setOpen(null); setEditing("new"); }}>Prepare a journal</button>
        </div>}
      </div>
      {editing !== null && <Editor key={editing === "new" ? "new" : editing.id} overview={overview} journal={editing === "new" ? null : editing} csrfToken={csrfToken}
        onDone={changed} onCancel={() => setEditing(null)} />}
      {editing === null && open && <Detail key={open.id} journal={open} overview={overview} csrfToken={csrfToken} onChanged={changed}
        onEdit={() => setEditing(open)} onClose={() => setOpen(null)} />}
      {overview.journals.length === 0
        ? <p className="sp-empty">No journal entries{status || month ? " match" : " yet"}.</p>
        : <div className="sp-table-wrap"><table className="sp-table bc-table mj-table" aria-label="Journal entries">
          <thead><tr><th scope="col">Journal</th><th scope="col">Date</th><th scope="col" className="bc-num">Amount</th><th scope="col">Status</th></tr></thead>
          <tbody>{overview.journals.map((journal) => <tr key={journal.id} className={open?.id === journal.id ? "mj-open" : undefined}>
            <th scope="row"><button className="fa-link" type="button" onClick={() => void view(journal.id)}>{journal.number}</button><small>{show(journal.description)}</small></th>
            <td>{journal.date}{journal.reverse_next_month && <small>undone on the 1st of next month</small>}</td>
            <td className="pw-mono bc-num">{fmt(journal.total)}{!journal.balanced && journal.status === "draft" && <small>out by {fmt(Math.abs(Number(journal.difference)))}</small>}</td>
            <td><span className={`ex-status ${STATUS_CLASS[journal.status]}`}>{journal.status_label}</span>
              <small>{journal.status === "posted" ? `approved by ${journal.approved_by}` : journal.returned_reason ? `sent back: ${journal.returned_reason}` : `prepared by ${journal.prepared_by}`}
                {journal.can_decide ? " · for you to approve" : ""}</small></td>
          </tr>)}</tbody>
        </table></div>}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
