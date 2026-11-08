import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./general-ledger.css";
import { BalanceSheetView, IncomeStatementView, MonthCloseView } from "./FinancialStatements";
import FxRevaluationView from "./FxRevaluation";

type Unit = { id: number; code: string; name: string };
type Scope = "unit" | "company";
type Owner = { document_date: string; posted_on: string; legal_entity_code: string; unit_code: string };
type BalanceRow = {
  account_id: number; code: string; name: string; account_class: string; class_label: string;
  opening_debit: string; opening_credit: string; debit: string; credit: string;
  closing_debit: string; closing_credit: string; closing_balance: string;
};
type Amounts = Pick<BalanceRow, "opening_debit" | "opening_credit" | "debit" | "credit" | "closing_debit" | "closing_credit">;
type TrialBalance = {
  unit: Unit; scope: Scope; scope_label: string; legal_entity: Unit; can_view_company: boolean; start: string; end: string; rows: BalanceRow[]; totals: Amounts;
  checks: { period_balanced: boolean; closing_balanced: boolean; unbalanced_entries: number };
  entry_count: number; kinds: { kind: string; label: string }[]; policy_label: string;
};
type Pagination = { page: number; page_size: number; total_count: number };
type AccountLedger = {
  account: { id: number; code: string; name: string; class_label: string; normal_side: string };
  start: string; end: string; opening_balance: string; closing_balance: string; debit: string; credit: string;
  lines: (Owner & { id: number; entry_id: number; kind: string; description: string; memo: string; debit: string; credit: string; balance: string })[];
  pagination: Pagination;
};
type EntryRow = Owner & { id: number; kind: string; kind_label: string; description: string; total: string; line_count: number };
type EntryList = { entries: EntryRow[]; pagination: Pagination };
type EntryDetail = Owner & {
  id: number; kind_label: string; description: string; policy_label: string;
  lines: { account_id: number; code: string; name: string; debit: string; credit: string; memo: string }[];
  debit: string; credit: string; balanced: boolean;
};
type View = { kind: "balance" } | { kind: "entries" } | { kind: "pnl" } | { kind: "sheet" } | { kind: "fx" } | { kind: "months" }
  | { kind: "account"; id: number; back: View } | { kind: "entry"; id: number; back: View };
type Tab = "balance" | "entries" | "pnl" | "sheet" | "fx" | "months";
const TABS: { tab: Tab; label: string }[] = [
  { tab: "balance", label: "Trial balance" }, { tab: "entries", label: "Journal entries" },
  { tab: "pnl", label: "Profit and loss" }, { tab: "sheet", label: "Balance sheet" },
  { tab: "fx", label: "Currency revaluation" }, { tab: "months", label: "Month-end close" },
];
const tabOf = (view: View): Tab => view.kind === "account" || view.kind === "entry" ? tabOf(view.back) : view.kind;

async function call<T>(path: string): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The ledger could not be loaded.");
  return data as T;
}

const dateInput = (date: Date) => date.toLocaleDateString("en-CA");
const money = (value: string) => {
  const number = Number(value);
  return number === 0 ? "—" : number.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
const signed = (value: string) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const AMOUNT_COLUMNS: { key: keyof Amounts; label: string }[] = [
  { key: "opening_debit", label: "Opening debit" }, { key: "opening_credit", label: "Opening credit" },
  { key: "debit", label: "Period debit" }, { key: "credit", label: "Period credit" },
  { key: "closing_debit", label: "Closing debit" }, { key: "closing_credit", label: "Closing credit" },
];

function query(unitId: number | null, params: Record<string, string | number | undefined>) {
  const search = new URLSearchParams();
  if (unitId) search.set("unit", String(unitId));
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "") search.set(key, String(value));
  return search.toString();
}

function ExcelLink({ view, unitId, params }: { view: string; unitId: number | null; params: Record<string, string | number | undefined> }) {
  return <a className="button button-outline gl-excel" href={`/api/finance/ledger/export/${view}/?${query(unitId, params)}`} download>Download Excel</a>;
}

function Dated({ row }: { row: Owner }) {
  return <td className="gl-date">{row.document_date}{row.posted_on !== row.document_date && <small>posted {row.posted_on}</small>}</td>;
}

function Pager({ pagination, onPage }: { pagination: Pagination; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(pagination.total_count / pagination.page_size));
  if (pages === 1) return null;
  return <nav className="gl-pager" aria-label="Pages">
    <button className="button button-outline" type="button" disabled={pagination.page <= 1} onClick={() => onPage(pagination.page - 1)}>Previous</button>
    <span>Page {pagination.page} of {pages}</span>
    <button className="button button-outline" type="button" disabled={pagination.page >= pages} onClick={() => onPage(pagination.page + 1)}>Next</button>
  </nav>;
}

function Check({ ok, children }: { ok: boolean; children: string }) {
  return <li className={ok ? "gl-ok" : "gl-bad"}><span aria-hidden="true">{ok ? "✓" : "!"}</span>{children}</li>;
}

function TrialBalanceView({ report, onAccount }: { report: TrialBalance; onAccount: (id: number) => void }) {
  const classes = [...new Set(report.rows.map((row) => row.class_label))];
  return <>
    <ul className="gl-checks" aria-label="Ledger checks">
      <Check ok={report.checks.period_balanced}>Period debits equal credits</Check>
      <Check ok={report.checks.closing_balanced}>Closing balances net to zero</Check>
      <Check ok={report.checks.unbalanced_entries === 0}>{report.checks.unbalanced_entries === 0 ? "Every posted entry balances" : `${report.checks.unbalanced_entries} posted entries do not balance`}</Check>
      <li className="gl-count">{report.entry_count} entries dated in this period</li>
    </ul>
    {report.rows.length === 0
      ? <p className="gl-empty">Nothing has been posted for {report.scope_label} up to {report.end}.</p>
      : <div className="gl-table-wrap"><table className="gl-table gl-trial-balance">
        <thead><tr><th scope="col">Account</th>{AMOUNT_COLUMNS.map((column) => <th scope="col" className="gl-num" key={column.key}>{column.label}</th>)}</tr></thead>
        {classes.map((label) => <tbody key={label}>
          <tr className="gl-class"><th scope="rowgroup" colSpan={7}>{label}</th></tr>
          {report.rows.filter((row) => row.class_label === label).map((row) => <tr key={row.account_id}>
            <th scope="row"><button type="button" className="gl-link" onClick={() => onAccount(row.account_id)}>{row.name}</button><small>{row.code}</small></th>
            {AMOUNT_COLUMNS.map((column) => <td className="gl-num" key={column.key}>{money(row[column.key])}</td>)}
          </tr>)}
        </tbody>)}
        <tfoot><tr><th scope="row">Total</th>{AMOUNT_COLUMNS.map((column) => <td className="gl-num" key={column.key}>{money(report.totals[column.key])}</td>)}</tr></tfoot>
      </table></div>}
  </>;
}

function AccountView({ unitId, scope, accountId, start, end, onEntry }: {
  unitId: number | null; scope: Scope; accountId: number; start: string; end: string; onEntry: (id: number) => void;
}) {
  const [page, setPage] = useState(1);
  const [ledger, setLedger] = useState<AccountLedger | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    call<AccountLedger>(`/api/finance/ledger/accounts/${accountId}/?${query(unitId, { start, end, page, scope })}`)
      .then((data) => { if (live) { setLedger(data); setError(""); } })
      .catch((caught: Error) => { if (live) setError(caught.message); });
    return () => { live = false; };
  }, [unitId, scope, accountId, start, end, page]);
  if (error) return <div className="alert" role="alert">{error}</div>;
  if (!ledger) return <p className="gl-empty" role="status">Loading the account…</p>;
  return <section className="gl-account" aria-labelledby="gl-account-title">
    <div className="gl-section-head">
      <h3 id="gl-account-title">{ledger.account.name} <small>{ledger.account.code} · {ledger.account.class_label}, normally a {ledger.account.normal_side} balance</small></h3>
      {ledger.lines.length > 0 && <ExcelLink view="account" unitId={unitId} params={{ start, end, scope, account: accountId }} />}
    </div>
    <dl className="gl-summary">
      <div><dt>Opening balance</dt><dd>{signed(ledger.opening_balance)}</dd></div>
      <div><dt>Debits</dt><dd>{signed(ledger.debit)}</dd></div>
      <div><dt>Credits</dt><dd>{signed(ledger.credit)}</dd></div>
      <div><dt>Closing balance</dt><dd>{signed(ledger.closing_balance)}</dd></div>
    </dl>
    {ledger.lines.length === 0 ? <p className="gl-empty">No postings to this account in the period.</p>
      : <div className="gl-table-wrap"><table className="gl-table">
        <thead><tr><th scope="col">Document date</th><th scope="col">Entry</th>{scope === "company" && <th scope="col">Unit</th>}<th scope="col">Description</th><th scope="col" className="gl-num">Debit</th><th scope="col" className="gl-num">Credit</th><th scope="col" className="gl-num">Balance</th></tr></thead>
        <tbody>{ledger.lines.map((line) => <tr key={line.id}>
          <Dated row={line} />
          <td><button type="button" className="gl-link" onClick={() => onEntry(line.entry_id)}>#{line.entry_id}</button></td>
          {scope === "company" && <td>{line.unit_code}</td>}
          <td>{line.description}<small>{line.memo}</small></td>
          <td className="gl-num">{money(line.debit)}</td><td className="gl-num">{money(line.credit)}</td><td className="gl-num">{signed(line.balance)}</td>
        </tr>)}</tbody>
      </table></div>}
    <Pager pagination={ledger.pagination} onPage={setPage} />
  </section>;
}

function EntriesView({ unitId, scope, start, end, kinds, onEntry }: {
  unitId: number | null; scope: Scope; start: string; end: string; kinds: TrialBalance["kinds"]; onEntry: (id: number) => void;
}) {
  const [kind, setKind] = useState("");
  const [search, setSearch] = useState("");
  const [applied, setApplied] = useState("");
  const [page, setPage] = useState(1);
  const [list, setList] = useState<EntryList | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    call<EntryList>(`/api/finance/ledger/entries/?${query(unitId, { start, end, kind, q: applied, page, scope })}`)
      .then((data) => { if (live) { setList(data); setError(""); } })
      .catch((caught: Error) => { if (live) setError(caught.message); });
    return () => { live = false; };
  }, [unitId, scope, start, end, kind, applied, page]);
  const submit = (event: FormEvent) => { event.preventDefault(); setPage(1); setApplied(search.trim()); };
  return <section className="gl-entries" aria-label="Journal entries">
    <form className="gl-filters" onSubmit={submit} role="search">
      <label>Type<select value={kind} onChange={(event) => { setPage(1); setKind(event.target.value); }}>
        <option value="">All types</option>
        {kinds.map((item) => <option key={item.kind} value={item.kind}>{item.label}</option>)}
      </select></label>
      <label>Search<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Entry number or description" /></label>
      <button className="button button-outline" type="submit">Search</button>
      {list && list.entries.length > 0 && <ExcelLink view="entries" unitId={unitId} params={{ start, end, kind, q: applied, scope }} />}
    </form>
    {error && <div className="alert" role="alert">{error}</div>}
    {!list && !error && <p className="gl-empty" role="status">Loading entries…</p>}
    {list && (list.entries.length === 0 ? <p className="gl-empty">No posted entries match.</p>
      : <div className="gl-table-wrap"><table className="gl-table">
        <thead><tr><th scope="col">Document date</th><th scope="col">Entry</th>{scope === "company" && <th scope="col">Unit</th>}<th scope="col">Type</th><th scope="col">Description</th><th scope="col" className="gl-num">Lines</th><th scope="col" className="gl-num">Amount (QAR)</th></tr></thead>
        <tbody>{list.entries.map((entry) => <tr key={entry.id}>
          <Dated row={entry} />
          <td><button type="button" className="gl-link" onClick={() => onEntry(entry.id)}>#{entry.id}</button></td>
          {scope === "company" && <td>{entry.unit_code}</td>}
          <td>{entry.kind_label}</td><td>{entry.description}</td>
          <td className="gl-num">{entry.line_count}</td><td className="gl-num">{signed(entry.total)}</td>
        </tr>)}</tbody>
      </table></div>)}
    {list && <Pager pagination={list.pagination} onPage={setPage} />}
  </section>;
}

function EntryView({ unitId, scope, entryId, onAccount }: { unitId: number | null; scope: Scope; entryId: number; onAccount: (id: number) => void }) {
  const [entry, setEntry] = useState<EntryDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    call<EntryDetail>(`/api/finance/ledger/entries/${entryId}/?${query(unitId, { scope })}`)
      .then((data) => { if (live) setEntry(data); })
      .catch((caught: Error) => { if (live) setError(caught.message); });
    return () => { live = false; };
  }, [unitId, scope, entryId]);
  if (error) return <div className="alert" role="alert">{error}</div>;
  if (!entry) return <p className="gl-empty" role="status">Loading the entry…</p>;
  return <section className="gl-entry" aria-labelledby="gl-entry-title">
    <h3 id="gl-entry-title">Journal entry #{entry.id} <small>{entry.kind_label}</small></h3>
    <dl className="gl-summary gl-entry-meta">
      <div><dt>Document date</dt><dd>{entry.document_date}</dd></div>
      <div><dt>Posted on</dt><dd>{entry.posted_on}</dd></div>
      <div><dt>Legal entity</dt><dd>{entry.legal_entity_code}</dd></div>
      <div><dt>Business unit</dt><dd>{entry.unit_code}</dd></div>
    </dl>
    <p className="gl-hint">{entry.description}</p>
    <div className="gl-table-wrap"><table className="gl-table">
      <thead><tr><th scope="col">Account</th><th scope="col">Memo</th><th scope="col" className="gl-num">Debit</th><th scope="col" className="gl-num">Credit</th></tr></thead>
      <tbody>{entry.lines.map((line, index) => <tr key={index}>
        <th scope="row"><button type="button" className="gl-link" onClick={() => onAccount(line.account_id)}>{line.name}</button><small>{line.code}</small></th>
        <td>{line.memo}</td><td className="gl-num">{money(line.debit)}</td><td className="gl-num">{money(line.credit)}</td>
      </tr>)}</tbody>
      <tfoot><tr><th scope="row" colSpan={2}>Total</th><td className="gl-num">{signed(entry.debit)}</td><td className="gl-num">{signed(entry.credit)}</td></tr></tfoot>
    </table></div>
    <ul className="gl-checks"><Check ok={entry.balanced}>{entry.balanced ? "Debits equal credits" : "This entry does not balance"}</Check></ul>
  </section>;
}

export default function GeneralLedger({ selectedUnitId, csrfToken = "" }: { selectedUnitId: number | null; csrfToken?: string }) {
  const now = new Date();
  const [start, setStart] = useState(() => dateInput(new Date(now.getFullYear(), now.getMonth(), 1)));
  const [end, setEnd] = useState(() => dateInput(now));
  const [view, setView] = useState<View>({ kind: "balance" });
  const [scope, setScope] = useState<Scope>("unit");
  const [report, setReport] = useState<TrialBalance | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    let live = true;
    call<TrialBalance>(`/api/finance/ledger/trial-balance/?${query(selectedUnitId, { start, end, scope })}`)
      .then((data) => { if (live) { setReport(data); setError(""); } })
      .catch((caught: Error) => { if (live) { setReport(null); setError(caught.message); } });
    return () => { live = false; };
  }, [selectedUnitId, start, end, scope]);
  const chooseScope = (next: Scope) => { setScope(next); setView({ kind: tabOf(view) } as View); };
  useEffect(load, [load]);
  const openEntry = (id: number) => setView({ kind: "entry", id, back: view });
  const openAccount = (id: number) => setView({ kind: "account", id, back: view });
  const tab = tabOf(view);
  return <section className="general-ledger" aria-labelledby="gl-title">
    <div className="gl-head">
      <span className="eyebrow">FINANCE · TEST ONLY SYNTHETIC ACCOUNTS</span>
      <h2 id="gl-title">General ledger{report ? ` · ${report.scope_label}` : ""}</h2>
      <p>Balances come from the journal entries that receipts, deliveries, customer payments and supplier payments have already posted. Every entry belongs to a legal entity and a business unit and is dated on its document date. Entries cannot be changed here; the only action is closing a finished month. Click an account to see its postings, or an entry number to see all its lines.</p>
    </div>
    <div className="gl-toolbar">
      <div className="gl-tabs" role="group" aria-label="Ledger view">
        {TABS.map((item) => <button key={item.tab} type="button" aria-pressed={tab === item.tab} onClick={() => setView({ kind: item.tab } as View)}>{item.label}</button>)}
      </div>
      {tab !== "months" && tab !== "fx" && (report?.can_view_company || scope === "company") && <div className="gl-tabs" role="group" aria-label="Ledger scope">
        <button type="button" aria-pressed={scope === "unit"} onClick={() => chooseScope("unit")}>{report?.unit.name ?? "This unit"}</button>
        <button type="button" aria-pressed={scope === "company"} onClick={() => chooseScope("company")}>Whole company</button>
      </div>}
      {tab !== "sheet" && tab !== "months" && tab !== "fx" && <label>From<input type="date" value={start} max={end} onChange={(event) => setStart(event.target.value)} /></label>}
      {tab !== "months" && tab !== "fx" && <label>{tab === "sheet" ? "As of" : "Through"}<input type="date" value={end} min={start} onChange={(event) => setEnd(event.target.value)} /></label>}
      {report && view.kind === "balance" && report.rows.length > 0 && <ExcelLink view="trial-balance" unitId={selectedUnitId} params={{ start, end, scope }} />}
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    {(view.kind === "account" || view.kind === "entry") && <button className="gl-back" type="button" onClick={() => setView(view.back)}>← Back</button>}
    {report && view.kind === "balance" && <TrialBalanceView report={report} onAccount={openAccount} />}
    {view.kind === "pnl" && <IncomeStatementView unitId={selectedUnitId} scope={scope} start={start} end={end} onAccount={openAccount} />}
    {view.kind === "sheet" && <BalanceSheetView unitId={selectedUnitId} scope={scope} asOf={end} onAccount={openAccount} />}
    {view.kind === "fx" && <FxRevaluationView unitId={selectedUnitId} csrfToken={csrfToken} />}
    {view.kind === "months" && <MonthCloseView unitId={selectedUnitId} csrfToken={csrfToken} />}
    {report && view.kind === "entries" && <EntriesView unitId={selectedUnitId} scope={scope} start={start} end={end} kinds={report.kinds} onEntry={openEntry} />}
    {view.kind === "account" && <AccountView key={`${scope}-${view.id}`} unitId={selectedUnitId} scope={scope} accountId={view.id} start={start} end={end} onEntry={openEntry} />}
    {view.kind === "entry" && <EntryView key={`${scope}-${view.id}`} unitId={selectedUnitId} scope={scope} entryId={view.id} onAccount={openAccount} />}
    {!report && !error && <p className="gl-empty" role="status">Loading the ledger…</p>}
    {report && <p className="gl-hint">{report.policy_label}. Amounts are in QAR. Periods follow the document date; the posting date is shown underneath when it differs.</p>}
  </section>;
}
