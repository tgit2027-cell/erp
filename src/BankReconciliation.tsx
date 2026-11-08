import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type StatementRow = {
  id: number; date: string; description: string; reference: string; amount: string;
  matched_to: number | null; matched_date: string | null; matched_description: string; suggested: number[];
};
type BookRow = { line_id: number; date: string; journal_entry_id: number; description: string; source: string; amount: string };
type Rec = {
  id: number; month: string; status: "prepared" | "approved" | "rejected"; status_label: string;
  statement_opening: string; statement_closing: string; statement_movement: string; book_balance: string;
  outstanding_in: string; outstanding_out: string; note: string; prepared_by: string; prepared_at: string;
  decided_by: string | null; decision_note: string;
};
type View = {
  unit: { id: number; code: string; name: string }; accounts: { id: number; name: string; kind: string }[];
  month: string; month_end: string; can_manage: boolean; can_approve: boolean; me: string; can_read_documents: boolean;
  account: { id: number; name: string; kind: string; kind_label: string; opening_date: string } | null;
  statement?: StatementRow[]; statement_movement?: string; suggested_opening?: string; book_balance?: string;
  outstanding?: BookRow[]; outstanding_in?: string; outstanding_out?: string; expected_closing?: string;
  unmatched_count?: number; late_count?: number; reconciliation?: Rec | null; history?: Rec[]; locked?: boolean;
};
type Draft = { date: string; description: string; reference: string; amount: string; checks: string[]; include: boolean };
type Reading = {
  file_name: string; lines: Draft[]; warnings: string[]; notice: string; line_count: number;
  statement: { bank_name: string; account_last4: string; period_start: string; period_end: string; opening_balance: string; closing_balance: string };
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

const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto
  ? crypto.randomUUID() : `bank-rec-${Date.now()}-${Math.random().toString(36).slice(2)}`);
const fmt = (value: string | number) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const show = (name: string) => name.replace(/TEST ONLY\s*/g, "");
const lastMonth = () => {
  const now = new Date();
  const first = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return `${first.getFullYear()}-${String(first.getMonth() + 1).padStart(2, "0")}`;
};
const blank = (month: string): Draft => ({ date: `${month}-01`, description: "", reference: "", amount: "", checks: [], include: true });
const total = (rows: Draft[]) => rows.filter((row) => row.include).reduce((sum, row) => sum + (Number(row.amount) || 0), 0);

function DraftLines({ title, intro, initial, month, reading, onAdd, onCancel }: {
  title: string; intro: string; initial: Draft[]; month: string; reading?: Reading;
  onAdd: (rows: Draft[]) => Promise<void>; onCancel: () => void;
}) {
  const [rows, setRows] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const edit = (index: number, change: Partial<Draft>) => setRows(rows.map((row, at) => at === index ? { ...row, ...change } : row));
  const chosen = rows.filter((row) => row.include);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try { await onAdd(chosen); } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The lines could not be added.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form" onSubmit={submit} aria-label={title}>
    <h3>{title}</h3>
    <p className="sp-hint">{intro}</p>
    {reading && <>
      <p className="sp-notice br-ai">{reading.notice} <small>{reading.file_name}
        {reading.statement.period_start && ` · ${reading.statement.period_start} to ${reading.statement.period_end}`}
        {reading.statement.opening_balance && ` · opening ${fmt(reading.statement.opening_balance.replace(/,/g, ""))}`}
        {reading.statement.closing_balance && ` · closing ${fmt(reading.statement.closing_balance.replace(/,/g, ""))}`}</small></p>
      {reading.warnings.map((warning) => <div className="alert" role="alert" key={warning}>{warning}</div>)}
    </>}
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-table-wrap"><table className="sp-table br-drafts" aria-label="Lines to add">
      <thead><tr><th scope="col">Add</th><th scope="col">Date</th><th scope="col">Description</th><th scope="col">Reference</th><th scope="col" className="bc-num">Amount (+ in, − out)</th><th scope="col">Check</th></tr></thead>
      <tbody>{rows.map((row, index) => <tr key={index} className={row.checks.length ? "br-flag" : undefined}>
        <td><input type="checkbox" aria-label={`Add line ${index + 1}`} checked={row.include} onChange={(event) => edit(index, { include: event.target.checked })} /></td>
        <td><input type="date" aria-label={`Date of line ${index + 1}`} value={row.date} onChange={(event) => edit(index, { date: event.target.value })} /></td>
        <td><input aria-label={`Description of line ${index + 1}`} value={row.description} maxLength={160} onChange={(event) => edit(index, { description: event.target.value })} /></td>
        <td><input aria-label={`Reference of line ${index + 1}`} value={row.reference} maxLength={80} onChange={(event) => edit(index, { reference: event.target.value })} /></td>
        <td><input className="bc-num" inputMode="decimal" aria-label={`Amount of line ${index + 1}`} value={row.amount} onChange={(event) => edit(index, { amount: event.target.value })} /></td>
        <td>{row.checks.length ? row.checks.map((check) => <span className="sp-loss br-check" key={check}>{check}</span>) : <span className="sp-muted">{reading ? "Looks fine" : ""}</span>}</td>
      </tr>)}</tbody>
    </table></div>
    <p className="sp-hint">{chosen.length} of {rows.length} lines ticked · net {fmt(total(rows))} QAR</p>
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy || chosen.length === 0}>Add {chosen.length} {chosen.length === 1 ? "line" : "lines"}</button>
      {!reading && <button className="button button-outline" type="button" onClick={() => setRows([...rows, blank(month)])} disabled={busy}>Another line</button>}
      <button className="button button-outline" type="button" onClick={onCancel} disabled={busy}>{reading ? "Discard" : "Cancel"}</button>
    </div>
  </form>;
}

function Decision({ rec, view, csrfToken, onDone }: { rec: Rec; view: View; csrfToken: string; onDone: (message: string) => void }) {
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const decide = async (action: "approve" | "reject") => {
    setError("");
    try {
      await call(`/api/finance/bank-reconciliation/${rec.id}/${action}/?unit=${view.unit.id}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify({ note }),
      });
      onDone(action === "approve" ? `${rec.month} approved. The month is now locked.` : `${rec.month} sent back. Fix it and prepare it again.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The decision could not be saved.");
    }
  };
  return <>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-decision">
      <button className="button button-dark" type="button" onClick={() => void decide("approve")}>Approve</button>
      <label>Reason to send back<input value={note} onChange={(event) => setNote(event.target.value)} maxLength={200} /></label>
      <button className="button button-outline" type="button" onClick={() => void decide("reject")} disabled={!note.trim()}>Send back</button>
    </div>
  </>;
}

export default function BankReconciliation({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [accountId, setAccountId] = useState<number | null>(null);
  const [month, setMonth] = useState(lastMonth);
  const [panel, setPanel] = useState<"add" | "reading" | null>(null);
  const [reading, setReading] = useState<Reading | null>(null);
  const [busy, setBusy] = useState("");
  const [picks, setPicks] = useState<Record<number, string>>({});
  const [figures, setFigures] = useState({ opening: "", closing: "", note: "" });
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    const account = accountId ? `&account=${accountId}` : "";
    try {
      const data = await call<View>(`/api/finance/bank-reconciliation/?unit=${selectedUnitId}${account}&month=${month}`);
      setView(data);
      setFigures({ opening: data.suggested_opening ?? "", closing: "", note: "" });
      setError("");
    } catch (caught) {
      setView(null);
      setError(caught instanceof Error ? caught.message : "Bank reconciliation could not be loaded.");
    }
  }, [selectedUnitId, accountId, month]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setPanel(null); setReading(null); setNotice(message); setBusy(""); void load(); };
  const fail = (caught: unknown, fallback: string) => { setBusy(""); setNotice(""); setError(caught instanceof Error ? caught.message : fallback); };
  const json = (body: unknown, key = false) => ({
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken, ...(key ? { "Idempotency-Key": newKey() } : {}) },
  });
  const statementUrl = view ? `/api/finance/bank-reconciliation/statement/?unit=${view.unit.id}` : "";

  const act = async (label: string, body: Record<string, unknown>, message: (data: Record<string, unknown>) => string, key = false) => {
    setBusy(label); setError(""); setNotice("");
    try { done(message(await call<Record<string, unknown>>(statementUrl, json(body, key)))); } catch (caught) { fail(caught, "That could not be done."); }
  };
  const addLines = async (rows: Draft[]) => {
    const data = await call<{ added: number }>(statementUrl, json({
      action: "add", account_id: view?.account?.id,
      lines: rows.map(({ date, description, reference, amount }) => ({ date, description, reference, amount })),
    }, true));
    done(`${data.added} statement ${data.added === 1 ? "line" : "lines"} added. Match them below.`);
  };
  const upload = async (file: File | undefined) => {
    if (!file || !view?.account) return;
    setBusy("upload"); setError(""); setNotice(""); setPanel(null);
    const form = new FormData();
    form.append("file", file);
    form.append("account_id", String(view.account.id));
    form.append("month", view.month);
    try {
      const data = await call<Reading>(`/api/finance/bank-reconciliation/read-statement/?unit=${view.unit.id}`, {
        method: "POST", headers: { "X-CSRFToken": csrfToken }, body: form,
      });
      setReading(data); setPanel("reading"); setBusy("");
    } catch (caught) { fail(caught, "The statement could not be read."); }
    if (fileInput.current) fileInput.current.value = "";
  };
  const prepare = async (event: FormEvent) => {
    event.preventDefault();
    if (!view?.account) return;
    setBusy("prepare"); setError(""); setNotice("");
    try {
      await call(`/api/finance/bank-reconciliation/?unit=${view.unit.id}`, json({
        account_id: view.account.id, month: view.month, statement_opening: figures.opening, statement_closing: figures.closing, note: figures.note,
      }, true));
      done(`${view.month} prepared. Someone with approval rights checks and approves it.`);
    } catch (caught) { fail(caught, "The reconciliation could not be prepared."); }
  };

  const account = view?.account;
  const rec = view?.reconciliation ?? null;
  const editable = Boolean(view?.can_manage && account && !view.locked && rec?.status !== "prepared");
  const outstanding = view?.outstanding ?? [];
  const unmatchedBook = outstanding.filter((row) => !(view?.statement ?? []).some((line) => line.matched_to === row.line_id));
  const statementTotal = Number(figures.opening || 0) + Number(view?.statement_movement || 0);
  const ties = figures.closing !== "" && view && Math.abs(Number(figures.closing) - Number(view.expected_closing)) < 0.005
    && Math.abs(Number(figures.closing) - statementTotal) < 0.005;

  return <section className="content-card supplier-payments bank-cash bank-rec" aria-labelledby="bank-rec-title">
    <header className="sp-head">
      <span className="eyebrow">FINANCE · BANK RECONCILIATION · TEST ONLY{view ? ` · ${view.unit.name}` : ""}</span>
      <h2 id="bank-rec-title">Bank reconciliation</h2>
      <p>Check each month's bank statement against what we recorded. Upload the statement and the lines are read for you, or type them in. Match each line to our record, explain what is left, then a second person approves the month.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {busy === "upload" && <p className="sp-notice br-ai" role="status">Reading the statement… this can take up to a minute.</p>}
    {!view && !error && <p className="sp-empty" role="status">Loading bank reconciliation…</p>}
    {view && !account && <p className="sp-empty">{view.unit.name} has no bank account or cash box yet. Add one in Bank and cash first.</p>}
    {view && account && <>
      <div className="bc-filters">
        <label>Account<select value={account.id} onChange={(event) => { setPanel(null); setNotice(""); setAccountId(Number(event.target.value)); }}>
          {view.accounts.map((each) => <option key={each.id} value={each.id}>{show(each.name)}</option>)}
        </select></label>
        <label>Month<input type="month" value={month} onChange={(event) => { setPanel(null); setNotice(""); setMonth(event.target.value || lastMonth()); }} /></label>
        {editable && !panel && <div className="bc-buttons">
          {view.can_read_documents && <>
            <button className="button button-dark" type="button" disabled={busy !== ""} onClick={() => fileInput.current?.click()}>Upload statement</button>
            <input ref={fileInput} type="file" hidden accept=".pdf,.png,.jpg,.jpeg,.webp,.csv,.txt" aria-label="Statement file"
              onChange={(event) => void upload(event.target.files?.[0])} />
          </>}
          <button className="button button-outline" type="button" onClick={() => { setNotice(""); setPanel("add"); }}>Type lines in</button>
          {(view.unmatched_count ?? 0) > 0 && <button className="button button-outline" type="button" disabled={busy !== ""}
            onClick={() => void act("auto", { action: "auto_match", account_id: account.id, month: view.month },
              (data) => `${data.matched} ${data.matched === 1 ? "line" : "lines"} matched automatically.`)}>Match automatically</button>}
        </div>}
      </div>
      {editable && !view.can_read_documents && <p className="sp-hint">Reading statements with AI is not switched on here, so type the lines in.</p>}
      {panel === "reading" && reading && <DraftLines title="Check the lines read from the statement" month={view.month} reading={reading}
        intro="Lines flagged in red need a look and are not ticked. Correct anything misread, tick what belongs on this account, then add them."
        initial={reading.lines} onAdd={addLines} onCancel={() => { setPanel(null); setReading(null); }} />}
      {panel === "add" && <DraftLines title="Type in statement lines" month={view.month} initial={[blank(view.month)]}
        intro="One line per movement on the statement. Money in is positive; money out is negative." onAdd={addLines} onCancel={() => setPanel(null)} />}

      <dl className="sp-totals">
        <div><dt>Lines on the statement</dt><dd>{view.statement?.length ?? 0} · net {fmt(view.statement_movement ?? 0)}</dd></div>
        <div><dt>Not matched yet</dt><dd className={(view.unmatched_count ?? 0) ? "br-open" : undefined}>{view.unmatched_count}</dd></div>
        <div><dt>Our balance at {view.month_end}</dt><dd>{fmt(view.book_balance ?? 0)}</dd></div>
        <div><dt>Statement should close at</dt><dd>{fmt(view.expected_closing ?? 0)}</dd></div>
      </dl>
      {view.locked && <p className="sp-notice">{view.month} is approved for {show(account.name)}. Its statement lines and matches are locked.</p>}

      <h3>Statement lines <small>{show(account.name)} · {view.month}</small></h3>
      {(view.statement ?? []).length === 0
        ? <p className="sp-empty">No statement lines for {view.month} yet.{editable ? " Upload the statement or type the lines in." : ""}</p>
        : <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label="Statement lines">
          <thead><tr><th scope="col">Date</th><th scope="col">On the statement</th><th scope="col" className="bc-num">Amount</th><th scope="col">Our record</th></tr></thead>
          <tbody>{view.statement!.map((line) => {
            const options = [...unmatchedBook].filter((row) => row.amount === line.amount)
              .sort((a, b) => Number(line.suggested.includes(b.line_id)) - Number(line.suggested.includes(a.line_id)));
            const pick = picks[line.id] ?? String(line.suggested[0] ?? options[0]?.line_id ?? "");
            const amount = Number(line.amount);
            return <tr key={line.id}>
              <th scope="row">{line.date}</th>
              <td>{show(line.description)}{line.reference && <small>Ref {line.reference}</small>}</td>
              <td className="pw-mono bc-num">{fmt(line.amount)}</td>
              <td>{line.matched_to
                ? <><span className="sp-gain">Matched</span><small>{line.matched_date} · {show(line.matched_description)}</small>
                  {editable && <button className="button button-outline br-small" type="button" disabled={busy !== ""}
                    onClick={() => void act(`u${line.id}`, { action: "unmatch", statement_line_id: line.id }, () => "Match undone.")}>Undo</button>}</>
                : <div className="br-match">
                  <span className="sp-loss">Not matched</span>
                  {editable && options.length > 0 && <>
                    <select aria-label={`Our record for ${line.description}`} value={pick} onChange={(event) => setPicks({ ...picks, [line.id]: event.target.value })}>
                      {options.map((row) => <option key={row.line_id} value={row.line_id}>{row.date} · {show(row.description)}{line.suggested.includes(row.line_id) ? " (suggested)" : ""}</option>)}
                    </select>
                    <button className="button button-dark br-small" type="button" disabled={busy !== "" || !pick}
                      onClick={() => void act(`m${line.id}`, { action: "match", statement_line_id: line.id, journal_line_id: Number(pick) }, () => "Matched.")}>Match</button>
                  </>}
                  {editable && options.length === 0 && <small>Nothing recorded with this amount.</small>}
                  {editable && <span className="br-more">
                    {amount < 0 && <button className="button button-outline br-small" type="button" disabled={busy !== ""}
                      onClick={() => void act(`c${line.id}`, { action: "record", statement_line_id: line.id, kind: "bank_charge" }, () => "Recorded as a bank charge and matched.", true)}>Record as bank charge</button>}
                    {amount > 0 && <button className="button button-outline br-small" type="button" disabled={busy !== ""}
                      onClick={() => void act(`i${line.id}`, { action: "record", statement_line_id: line.id, kind: "interest" }, () => "Recorded as interest and matched.", true)}>Record as interest</button>}
                    <button className="button button-outline br-small" type="button" disabled={busy !== ""}
                      onClick={() => void act(`r${line.id}`, { action: "remove", statement_line_id: line.id }, () => "Line removed.")}>Remove</button>
                  </span>}
                </div>}</td>
            </tr>;
          })}</tbody>
        </table></div>}

      <h3>Recorded by us, not on this statement <small>{outstanding.length}</small></h3>
      {outstanding.length === 0
        ? <p className="sp-empty">Everything we recorded up to {view.month_end} is on the statement.</p>
        : <>
          <p className="sp-hint">Money we recorded that the bank had not shown by month end: deposits in transit {fmt(view.outstanding_in ?? 0)}, payments not yet cleared {fmt(view.outstanding_out ?? 0)}.</p>
          <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label="Outstanding items">
            <thead><tr><th scope="col">Date</th><th scope="col">What</th><th scope="col" className="bc-num">Amount</th></tr></thead>
            <tbody>{outstanding.map((row) => <tr key={row.line_id}>
              <th scope="row">{row.date}</th><td>{show(row.description)}<small>{row.source} · journal #{row.journal_entry_id}</small></td>
              <td className="pw-mono bc-num">{fmt(row.amount)}</td>
            </tr>)}</tbody>
          </table></div>
        </>}

      {rec && <div className="sp-form br-rec" aria-label="This month's reconciliation">
        <h3>{view.month} reconciliation <span className={`sp-status sp-status-${rec.status}`}>{rec.status_label}</span></h3>
        <dl className="sp-preview">
          <div><dt>Statement opening</dt><dd>{fmt(rec.statement_opening)}</dd></div>
          <div><dt>Statement lines</dt><dd>{fmt(rec.statement_movement)}</dd></div>
          <div><dt>Statement closing</dt><dd>{fmt(rec.statement_closing)}</dd></div>
          <div><dt>Our balance less in transit plus not cleared</dt><dd>{fmt(rec.book_balance)} − {fmt(rec.outstanding_in)} + {fmt(rec.outstanding_out)}</dd></div>
        </dl>
        <p className="sp-hint">Prepared by {rec.prepared_by}{rec.note && `: ${rec.note}`}{rec.decided_by && ` · ${rec.status_label.toLowerCase()} by ${rec.decided_by}`}{rec.decision_note && `: ${rec.decision_note}`}</p>
        {rec.status === "prepared" && view.can_approve && rec.prepared_by !== view.me && <Decision rec={rec} view={view} csrfToken={csrfToken} onDone={done} />}
        {rec.status === "prepared" && rec.prepared_by === view.me && <p className="sp-hint">Someone else with approval rights needs to approve it.</p>}
      </div>}

      {!rec && editable && <form className="sp-form" onSubmit={prepare} aria-label="Prepare the reconciliation">
        <h3>Prepare {view.month}</h3>
        {(view.unmatched_count ?? 0) > 0 && <p className="sp-hint">Match or explain every statement line before preparing. {view.unmatched_count} still open.</p>}
        <div className="sp-fields">
          <label>Statement opening balance<input inputMode="decimal" value={figures.opening} onChange={(event) => setFigures({ ...figures, opening: event.target.value })} required /></label>
          <label>Statement closing balance<input inputMode="decimal" value={figures.closing} onChange={(event) => setFigures({ ...figures, closing: event.target.value })} placeholder={fmt(statementTotal)} required /></label>
          <label>Note (optional)<input value={figures.note} onChange={(event) => setFigures({ ...figures, note: event.target.value })} maxLength={200} /></label>
        </div>
        {figures.closing !== "" && <p className={ties ? "sp-notice" : "alert"} role="status">{ties
          ? "The statement ties to our records."
          : `Opening plus the lines is ${fmt(statementTotal)} and our records say the statement should close at ${fmt(view.expected_closing ?? 0)}. Find the difference before preparing.`}</p>}
        <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy !== "" || (view.unmatched_count ?? 0) > 0}>Prepare for approval</button></div>
      </form>}

      {(view.history ?? []).length > 0 && <>
        <h3>History <small>{show(account.name)}</small></h3>
        <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label="Reconciliation history">
          <thead><tr><th scope="col">Month</th><th scope="col">Status</th><th scope="col" className="bc-num">Statement closing</th><th scope="col">Prepared by</th><th scope="col">Decided by</th></tr></thead>
          <tbody>{view.history!.map((row) => <tr key={row.id}>
            <th scope="row">{row.month}</th><td><span className={`sp-status sp-status-${row.status}`}>{row.status_label}</span>{row.decision_note && <small>{row.decision_note}</small>}</td>
            <td className="pw-mono bc-num">{fmt(row.statement_closing)}</td><td>{row.prepared_by}</td><td>{row.decided_by ?? ""}</td>
          </tr>)}</tbody>
        </table></div>
      </>}
    </>}
  </section>;
}
