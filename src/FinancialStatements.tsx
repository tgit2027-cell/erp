import { Fragment, useEffect, useState, type FormEvent } from "react";

type Scope = "unit" | "company";
type StatementRow = { account_id?: number; code: string; name: string } & Record<string, string | number | undefined>;
type Section = { key: string; label: string; rows: StatementRow[]; total: Record<string, string> };
type IncomeStatement = {
  scope_label: string; start: string; end: string; year_start: string; sections: Section[];
  gross_profit: Record<string, string>; gross_margin_percent: Record<string, string | null>; net_profit: Record<string, string>; policy_label: string;
};
type BalanceSheet = {
  scope_label: string; as_of: string; sections: Section[]; total_assets: string; total_liabilities_and_equity: string;
  balanced: boolean; difference: string; policy_label: string;
};
type Month = {
  month: string; label: string; status: "closed" | "open" | "current"; entry_count: number; debit: string; credit: string;
  closed_by: string; closed_at: string; can_close: boolean; close_blocked: string; warnings: string[]; can_reopen: boolean;
  checklist?: MonthCheck[];
};
type MonthCheck = { key: string; label: string; done: boolean; detail: string; required: boolean };
type MonthOverview = {
  scope_label: string; can_close: boolean; months: Month[];
  history: { month: string; action: string; action_label: string; actor: string; at: string; reason: string; entry_count: number; total_debit: string; checks_not_done?: string[] }[];
};

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The request could not be completed.");
  return data as T;
}

const amount = (value: string | number | undefined) => {
  const number = Number(value ?? 0);
  const text = Math.abs(number).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return number < 0 ? `(${text})` : text;
};
const when = (value: string) => new Date(value).toLocaleString();

function query(unitId: number | null, params: Record<string, string | undefined>) {
  const search = new URLSearchParams();
  if (unitId) search.set("unit", String(unitId));
  for (const [key, value] of Object.entries(params)) if (value) search.set(key, value);
  return search.toString();
}

function useStatement<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!path) return;
    let live = true;
    setData(null);
    call<T>(path).then((result) => { if (live) { setData(result); setError(""); } })
      .catch((caught: Error) => { if (live) setError(caught.message); });
    return () => { live = false; };
  }, [path]);
  return { data, error };
}

function Excel({ view, unitId, params }: { view: string; unitId: number | null; params: Record<string, string | undefined> }) {
  return <a className="button button-outline gl-excel" href={`/api/finance/ledger/export/${view}/?${query(unitId, params)}`} download>Download Excel</a>;
}

function SectionRows({ section, columns, onAccount }: { section: Section; columns: string[]; onAccount: (id: number) => void }) {
  return <tbody>
    <tr className="gl-class"><th scope="rowgroup" colSpan={columns.length + 1}>{section.label}</th></tr>
    {section.rows.length === 0 && <tr><td className="fs-none" colSpan={columns.length + 1}>Nothing posted</td></tr>}
    {section.rows.map((row) => <tr key={row.account_id ?? row.name}>
      <th scope="row">{row.account_id
        ? <><button type="button" className="gl-link" onClick={() => onAccount(row.account_id as number)}>{row.name}</button><small>{row.code}</small></>
        : row.name}</th>
      {columns.map((column) => <td className="gl-num" key={column}>{amount(row[column])}</td>)}
    </tr>)}
    <tr className="fs-subtotal"><th scope="row">Total {section.label.toLowerCase()}</th>
      {columns.map((column) => <td className="gl-num" key={column}>{amount(section.total[column])}</td>)}</tr>
  </tbody>;
}

export function IncomeStatementView({ unitId, scope, start, end, onAccount }: {
  unitId: number | null; scope: Scope; start: string; end: string; onAccount: (id: number) => void;
}) {
  const { data, error } = useStatement<IncomeStatement>(`/api/finance/ledger/income-statement/?${query(unitId, { start, end, scope })}`);
  if (error) return <div className="alert" role="alert">{error}</div>;
  if (!data) return <p className="gl-empty" role="status">Loading the profit and loss…</p>;
  const columns = ["period", "year_to_date"];
  const [sales, cost, ...rest] = data.sections;
  const margin = (key: string) => data.gross_margin_percent[key] === null ? "" : ` · ${data.gross_margin_percent[key]}% margin`;
  return <section className="fs-statement" aria-labelledby="fs-pnl-title">
    <div className="gl-section-head">
      <h3 id="fs-pnl-title">Profit and loss <small>{data.start} to {data.end} · year to date from {data.year_start}</small></h3>
      <Excel view="income-statement" unitId={unitId} params={{ start, end, scope }} />
    </div>
    <div className="gl-table-wrap"><table className="gl-table fs-table">
      <thead><tr><th scope="col">QAR</th><th scope="col" className="gl-num">This period</th><th scope="col" className="gl-num">Year to date</th></tr></thead>
      <SectionRows section={sales} columns={columns} onAccount={onAccount} />
      <SectionRows section={cost} columns={columns} onAccount={onAccount} />
      <tbody><tr className="fs-result"><th scope="row">Gross profit</th>
        {columns.map((column) => <td className="gl-num" key={column}>{amount(data.gross_profit[column])}<small>{margin(column)}</small></td>)}</tr></tbody>
      {rest.map((section) => <SectionRows key={section.key} section={section} columns={columns} onAccount={onAccount} />)}
      <tfoot><tr><th scope="row">Net profit</th>{columns.map((column) => <td className="gl-num" key={column}>{amount(data.net_profit[column])}</td>)}</tr></tfoot>
    </table></div>
    <p className="gl-hint">Amounts in brackets are losses. Cost of sales is the cost of goods delivered plus stock count differences.</p>
  </section>;
}

export function BalanceSheetView({ unitId, scope, asOf, onAccount }: {
  unitId: number | null; scope: Scope; asOf: string; onAccount: (id: number) => void;
}) {
  const { data, error } = useStatement<BalanceSheet>(`/api/finance/ledger/balance-sheet/?${query(unitId, { as_of: asOf, scope })}`);
  if (error) return <div className="alert" role="alert">{error}</div>;
  if (!data) return <p className="gl-empty" role="status">Loading the balance sheet…</p>;
  const [assets, ...claims] = data.sections;
  return <section className="fs-statement" aria-labelledby="fs-bs-title">
    <div className="gl-section-head">
      <h3 id="fs-bs-title">Balance sheet <small>As of {data.as_of}</small></h3>
      <Excel view="balance-sheet" unitId={unitId} params={{ end: asOf, scope }} />
    </div>
    <ul className="gl-checks" aria-label="Balance sheet check">
      <li className={data.balanced ? "gl-ok" : "gl-bad"}><span aria-hidden="true">{data.balanced ? "✓" : "!"}</span>
        {data.balanced ? "Assets equal liabilities plus equity" : `Out of balance by ${amount(data.difference)}`}</li>
    </ul>
    <div className="gl-table-wrap"><table className="gl-table fs-table">
      <thead><tr><th scope="col">QAR</th><th scope="col" className="gl-num">Balance</th></tr></thead>
      <SectionRows section={assets} columns={["balance"]} onAccount={onAccount} />
      {claims.map((section) => <SectionRows key={section.key} section={section} columns={["balance"]} onAccount={onAccount} />)}
      <tfoot><tr><th scope="row">Total liabilities and equity</th><td className="gl-num">{amount(data.total_liabilities_and_equity)}</td></tr></tfoot>
    </table></div>
    <p className="gl-hint">There are no equity accounts yet, so equity is the profit kept from earlier years plus this year's profit to date.</p>
  </section>;
}

function ReopenForm({ month, busy, onReopen, onCancel }: { month: Month; busy: boolean; onReopen: (reason: string) => void; onCancel: () => void }) {
  const [reason, setReason] = useState("");
  const submit = (event: FormEvent) => { event.preventDefault(); onReopen(reason); };
  return <form className="fs-reopen" onSubmit={submit} aria-label={`Reopen ${month.label}`}>
    <label>Why reopen {month.label}?<input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={240}
      placeholder="For example: a supplier invoice dated in this month arrived late" required /></label>
    <button className="button button-dark" type="submit" disabled={busy || reason.trim().length < 10}>Reopen month</button>
    <button className="button button-outline" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
  </form>;
}

const notDone = (month: Month) => (month.checklist ?? []).filter((check) => check.required && !check.done);

function MonthChecklist({ month }: { month: Month }) {
  const checks = month.checklist ?? [];
  const done = checks.filter((check) => check.done).length;
  return <div className="fs-checklist">
    <strong>Before closing {month.label}: {done} of {checks.length} done</strong>
    <ul aria-label={`Before closing ${month.label}`}>{checks.map((check) => <li key={check.key} className={check.done ? "is-done" : check.required ? "is-open" : "is-optional"}>
      <span className="fs-check-mark" aria-hidden="true">{check.done ? "✓" : check.required ? "!" : "·"}</span>
      <span><b>{check.label}</b>{!check.required && <em> (good to do)</em>}<span className="sr-only">{check.done ? ": done" : ": not done"}</span>
        <small>{check.detail}</small></span>
    </li>)}</ul>
  </div>;
}

function CloseAnywayForm({ month, busy, onClose, onCancel }: { month: Month; busy: boolean; onClose: (reason: string) => void; onCancel: () => void }) {
  const [reason, setReason] = useState("");
  const submit = (event: FormEvent) => { event.preventDefault(); onClose(reason.trim()); };
  return <form className="fs-reopen fs-close-anyway" onSubmit={submit} aria-label={`Close ${month.label} anyway`}>
    <p>Not done yet: {notDone(month).map((check) => check.label.toLowerCase()).join(", ")}. You can finish them first, or close anyway and say why. The reason is kept with the close record.</p>
    <label>Reason for closing anyway<input value={reason} onChange={(event) => setReason(event.target.value)} minLength={10} maxLength={500}
      placeholder="For example: the bank statement for the last day arrives next week" required /></label>
    <button className="button button-dark" type="submit" disabled={busy || reason.trim().length < 10}>Close anyway</button>
    <button className="button button-outline" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
  </form>;
}

export function MonthCloseView({ unitId, csrfToken }: { unitId: number | null; csrfToken: string }) {
  const [data, setData] = useState<MonthOverview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [reopening, setReopening] = useState("");
  const [closing, setClosing] = useState("");
  useEffect(() => {
    let live = true;
    call<MonthOverview>(`/api/finance/ledger/months/?${query(unitId, {})}`)
      .then((result) => { if (live) setData(result); })
      .catch((caught: Error) => { if (live) setError(caught.message); });
    return () => { live = false; };
  }, [unitId]);
  const act = async (action: "close" | "reopen", month: Month, reason?: string) => {
    setBusy(true);
    setNotice("");
    try {
      const result = await call<MonthOverview>(`/api/finance/ledger/months/${action}/?${query(unitId, {})}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify(action === "close" && !reason ? { month: month.month } : { month: month.month, reason }),
      });
      setData(result);
      setReopening("");
      setClosing("");
      setError("");
      setNotice(action === "close" ? `${month.label} is closed. Nothing dated in it can be posted now.` : `${month.label} is open again for postings.`);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!data) return error ? <div className="alert" role="alert">{error}</div> : <p className="gl-empty" role="status">Loading months…</p>;
  return <section className="fs-months" aria-labelledby="fs-months-title">
    <h3 id="fs-months-title">Month-end close <small>{data.scope_label}</small></h3>
    <p className="gl-hint">Close each month once it has ended and its postings are complete. After that, nothing dated in the month can be posted for this business unit. Months close in order, and only the latest closed month can be reopened, with a reason.
      {!data.can_close && " You can see the months, but closing needs the “Close months” permission."}</p>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="fs-notice" role="status">{notice}</p>}
    <div className="gl-table-wrap"><table className="gl-table">
      <thead><tr><th scope="col">Month</th><th scope="col">Status</th><th scope="col" className="gl-num">Entries</th><th scope="col" className="gl-num">Debits (QAR)</th><th scope="col">Close</th></tr></thead>
      <tbody>{data.months.map((month) => <Fragment key={month.month}><tr className={`fs-month is-${month.status}`}>
        <th scope="row">{month.label}</th>
        <td><span className={`fs-status fs-${month.status}`}>{month.status === "closed" ? "Closed" : month.status === "current" ? "In progress" : "Open"}</span>
          {month.status === "closed" && <small>by {month.closed_by}, {when(month.closed_at)}</small>}</td>
        <td className="gl-num">{month.entry_count}</td>
        <td className="gl-num">{amount(month.debit)}</td>
        <td><div className="fs-action">
          {month.can_close && closing !== month.month && <button className="button button-dark" type="button" disabled={busy}
            onClick={() => notDone(month).length ? setClosing(month.month) : void act("close", month)}>Close {month.label}</button>}
          {closing === month.month && <CloseAnywayForm month={month} busy={busy} onClose={(reason) => void act("close", month, reason)} onCancel={() => setClosing("")} />}
          {month.can_reopen && reopening !== month.month && <button className="button button-outline" type="button" disabled={busy} onClick={() => setReopening(month.month)}>Reopen</button>}
          {reopening === month.month && <ReopenForm month={month} busy={busy} onReopen={(reason) => void act("reopen", month, reason)} onCancel={() => setReopening("")} />}
          {!month.can_close && month.status !== "closed" && month.close_blocked && <small>{month.close_blocked}</small>}
          {!month.checklist?.length && month.warnings.map((warning) => <small className="fs-warning" key={warning}>{warning}</small>)}
        </div></td>
      </tr>
      {!!month.checklist?.length && <tr className="fs-checklist-row"><td colSpan={5}><MonthChecklist month={month} /></td></tr>}
      </Fragment>)}</tbody>
    </table></div>
    <h4>Close history</h4>
    {data.history.length === 0 ? <p className="gl-empty">No month has been closed yet.</p>
      : <ul className="fs-history">{data.history.map((event, index) => <li key={index}>
        <strong>{event.action_label} {event.month}</strong> · {event.actor}, {when(event.at)} · {event.entry_count} entries, {amount(event.total_debit)} QAR
        {!!event.checks_not_done?.length && <span>Closed before finishing: {event.checks_not_done.join(", ")}</span>}
        {event.reason && <span>Reason: {event.reason}</span>}
      </li>)}</ul>}
  </section>;
}
