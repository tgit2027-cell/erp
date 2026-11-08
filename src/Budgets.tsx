import { useCallback, useEffect, useState } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Compare = { budget: string; actual: string; difference: string; used_percent: number | null; flag: "" | "over" | "below" };
type Change = { actual: string; change: string; change_percent: number | null };
type Previous = { year: number; month: Change; to_date: Change; full_year: string };
type Line = {
  line: string; label: string; kind: "income" | "cost"; budget: string[]; actual: string[]; last_year: string[];
  year_budget: string; month: Compare; to_date: Compare; previous: Previous[];
};
type Profit = { month: Compare; to_date: Compare; year_budget: string; previous: Previous[] };
type View = {
  unit: { id: number; code: string; name: string }; year: number; month: number; years: number[];
  budget: null | { id: number; status: "draft" | "submitted" | "approved"; status_label: string; note: string; prepared_by: string; approved_by: string; approved_at: string; updated_at: string };
  lines: Line[]; gross_profit: Profit; net_profit: Profit;
  history: { at: string; by: string; what: string; note: string; total: string }[];
  can_prepare: boolean; can_approve: boolean; can_edit: boolean; can_submit: boolean; can_decide: boolean; can_reopen: boolean;
  policy_label: string;
};
type Grid = Record<string, string[]>;

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

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const num = (value: string) => Number(String(value).replace(/,/g, "")) || 0;
const fmt = (value: string | number) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
const signed = (value: string) => `${num(value) > 0 ? "+" : ""}${fmt(value)}`;
const STATUS_CLASS = { draft: "ex-waiting", submitted: "ex-waiting", approved: "ex-booked" } as const;
const total = (months: string[]) => months.reduce((sum, value) => sum + num(value), 0);
// Whole riyals per month; the leftover riyals go one each to the last months, any fils to December.
const spread = (amount: number) => {
  const whole = Math.floor(Math.max(amount, 0));
  const fils = Math.round((Math.max(amount, 0) - whole) * 100);
  const each = Math.floor(whole / 12);
  const extra = whole - each * 12;
  return MONTHS.map((_, index) => {
    const value = each + (index >= 12 - extra ? 1 : 0) + (index === 11 ? fils / 100 : 0);
    return value ? (Number.isInteger(value) ? String(value) : value.toFixed(2)) : "";
  });
};
const plain = (value: number) => value ? (Number.isInteger(value) ? String(value) : value.toFixed(2)) : "";

function Used({ compare }: { compare: Compare }) {
  if (compare.used_percent === null) return <span className="sp-muted">no budget</span>;
  const width = Math.min(compare.used_percent, 100);
  return <span className={`bg-used${compare.flag ? ` bg-${compare.flag}` : ""}`} title={`${compare.used_percent}% of budget`}>
    <span className="bg-bar"><span style={{ width: `${width}%` }} /></span>{compare.used_percent}%
  </span>;
}

function Row({ label, compare, toDate, year, strong, line }: { label: string; compare: Compare; toDate: Compare; year: string; strong?: boolean; line?: string }) {
  const flag = (value: Compare) => value.flag === "over" ? "bg-flag-over" : value.flag === "below" ? "bg-flag-below" : "";
  return <tr className={strong ? "bg-total" : ""} data-line={line}>
    <th scope="row">{label}</th>
    <td className="pw-mono bc-num">{fmt(compare.budget)}</td>
    <td className="pw-mono bc-num">{fmt(compare.actual)}</td>
    <td className={`pw-mono bc-num ${flag(compare)}`}>{signed(compare.difference)}</td>
    <td className="pw-mono bc-num bg-gap">{fmt(toDate.budget)}</td>
    <td className="pw-mono bc-num">{fmt(toDate.actual)}</td>
    <td className={`pw-mono bc-num ${flag(toDate)}`}>{signed(toDate.difference)}</td>
    <td><Used compare={toDate} /></td>
    <td className="pw-mono bc-num">{fmt(year)}</td>
  </tr>;
}

function EarlierRow({ label, now, previous, period, kind, strong, line }: { label: string; now: Compare; previous: Previous[]; period: "month" | "to_date"; kind: "income" | "cost"; strong?: boolean; line?: string }) {
  const [last, before] = previous;
  const worse = (change: Change) => kind === "income" ? num(change.change) < 0 : num(change.change) > 0;
  return <tr className={strong ? "bg-total" : ""} data-line={line}>
    <th scope="row">{label}</th>
    <td className="pw-mono bc-num">{fmt(now.actual)}</td>
    <td className="pw-mono bc-num bg-gap">{fmt(last[period].actual)}</td>
    <td className={`pw-mono bc-num ${worse(last[period]) ? "bg-flag-over" : ""}`}>{signed(last[period].change)}
      {last[period].change_percent !== null && <span className="bg-percent"> {last[period].change_percent > 0 ? "+" : ""}{last[period].change_percent}%</span>}</td>
    {before && <td className="pw-mono bc-num">{fmt(before[period].actual)}</td>}
    {before && <td className={`pw-mono bc-num ${worse(before[period]) ? "bg-flag-over" : ""}`}>{signed(before[period].change)}</td>}
    <td className="pw-mono bc-num bg-gap">{fmt(last.full_year)}</td>
    {before && <td className="pw-mono bc-num">{fmt(before.full_year)}</td>}
  </tr>;
}

function Editor({ view, csrfToken, onSaved, onCancel }: { view: View; csrfToken: string; onSaved: (view: View, message: string) => void; onCancel: () => void }) {
  const [grid, setGrid] = useState<Grid>(() => Object.fromEntries(view.lines.map((line) => [line.line, line.budget.map((value) => plain(num(value)))])));
  const [yearly, setYearly] = useState<Record<string, string>>({});
  const [note, setNote] = useState(view.budget?.note ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const without = (line: string) => Object.fromEntries(Object.entries(yearly).filter(([key]) => key !== line));
  const setCell = (line: string, index: number, value: string) => setGrid({ ...grid, [line]: grid[line].map((old, at) => at === index ? value : old) });
  const fromLastYear = () => setGrid(Object.fromEntries(view.lines.map((line) => [line.line, line.last_year.map((value) => num(value) > 0 ? plain(Math.round(num(value))) : "")])));
  const save = async (andSubmit: boolean) => {
    setBusy(true); setError("");
    const headers = { "Content-Type": "application/json", "X-CSRFToken": csrfToken };
    try {
      let next = await call<View>(`/api/finance/budgets/?unit=${view.unit.id}`, {
        method: "POST", headers, body: JSON.stringify({ year: view.year, amounts: grid, note }),
      });
      if (andSubmit) {
        next = await call<View>(`/api/finance/budgets/${view.year}/submit/?unit=${view.unit.id}`, { method: "POST", headers, body: "{}" });
      }
      onSaved(next, andSubmit ? `The ${view.year} budget was sent for approval.` : `The ${view.year} budget was saved.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The budget could not be saved.");
    } finally { setBusy(false); }
  };
  return <div className="sp-form bg-editor">
    <h3>{view.budget ? "Change" : "Prepare"} the {view.year} budget <small>QAR per month</small></h3>
    {error && <div className="alert" role="alert">{error}</div>}
    <p className="sp-hint">Type a year total and press Spread to divide it evenly over the twelve months, or fill each month. Leave a month empty for nothing planned.
      {view.lines.some((line) => total(line.last_year) > 0) && <> <button className="button button-outline ex-small" type="button" onClick={fromLastYear}>Start from {view.year - 1} actual figures</button></>}</p>
    <div className="sp-table-wrap"><table className="sp-table bc-table bg-grid" aria-label={`${view.year} budget by month`}>
      <thead><tr><th scope="col">Line</th><th scope="col" className="bc-num">Year total</th>{MONTHS.map((month) => <th key={month} scope="col" className="bc-num">{month}</th>)}</tr></thead>
      <tbody>{view.lines.map((line) => <tr key={line.line}>
        <th scope="row">{line.label}</th>
        <td className="bg-year">
          <input aria-label={`${line.label} for the year`} inputMode="decimal" value={yearly[line.line] ?? plain(Math.round(total(grid[line.line]) * 100) / 100)}
            onChange={(event) => setYearly({ ...yearly, [line.line]: event.target.value })} />
          <button className="button button-outline ex-small" type="button" aria-label={`Spread ${line.label} over the year`}
            onClick={() => { setGrid({ ...grid, [line.line]: spread(num(yearly[line.line] ?? String(total(grid[line.line])))) }); setYearly(without(line.line)); }}>Spread</button>
        </td>
        {MONTHS.map((month, index) => <td key={month}><input aria-label={`${line.label}, ${MONTH_NAMES[index]}`} inputMode="decimal" value={grid[line.line][index]}
          onChange={(event) => { setCell(line.line, index, event.target.value); setYearly(without(line.line)); }} /></td>)}
      </tr>)}</tbody>
    </table></div>
    <label className="bg-note">Note (optional)<input value={note} onChange={(event) => setNote(event.target.value)} maxLength={300} placeholder="e.g. assumes the second showroom opens in March" /></label>
    <div className="sp-actions">
      <button className="button button-dark" type="button" disabled={busy} onClick={() => void save(true)}>Save and send for approval</button>
      <button className="button button-outline" type="button" disabled={busy} onClick={() => void save(false)}>Save as draft</button>
      <button className="button button-outline" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </div>;
}

function Decision({ view, csrfToken, onDone }: { view: View; csrfToken: string; onDone: (view: View, message: string) => void }) {
  const [mode, setMode] = useState<"" | "return" | "reopen">("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const send = async (action: string, message: string) => {
    setBusy(true); setError("");
    try {
      const next = await call<View>(`/api/finance/budgets/${view.year}/${action}/?unit=${view.unit.id}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify({ reason }),
      });
      setMode(""); setReason("");
      onDone(next, message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That did not work.");
    } finally { setBusy(false); }
  };
  if (!view.can_decide && !view.can_reopen) return null;
  return <div className="bg-decision">
    {error && <div className="alert" role="alert">{error}</div>}
    {view.can_decide && mode === "" && <>
      <button className="button button-dark" type="button" disabled={busy} onClick={() => void send("approve", `The ${view.year} budget is approved.`)}>Approve budget</button>
      <button className="button button-outline" type="button" disabled={busy} onClick={() => setMode("return")}>Send back</button>
    </>}
    {view.can_reopen && mode === "" && <button className="button button-outline" type="button" onClick={() => setMode("reopen")}>Reopen to change</button>}
    {mode && <form className="ex-inline bg-reason" onSubmit={(event) => { event.preventDefault(); void send(mode, mode === "return" ? `The ${view.year} budget was sent back.` : `The ${view.year} budget is open for changes again.`); }}>
      <input aria-label={mode === "return" ? "Why it is sent back" : "Why it is reopened"} value={reason} onChange={(event) => setReason(event.target.value)} maxLength={300} placeholder="Reason" required />
      <button className="button button-dark ex-small" type="submit" disabled={busy}>{mode === "return" ? "Send back" : "Reopen"}</button>
      <button className="button button-outline ex-small" type="button" onClick={() => setMode("")}>Cancel</button>
    </form>}
  </div>;
}

export default function Budgets({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState(false);
  const [year, setYear] = useState(new Date().getFullYear());
  const [month, setMonth] = useState<number | null>(null);
  const [period, setPeriod] = useState<"month" | "to_date">("to_date");

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    try {
      setView(await call<View>(`/api/finance/budgets/?unit=${selectedUnitId}&year=${year}${month ? `&month=${month}` : ""}`));
      setError("");
    } catch (caught) {
      setView(null);
      setError(caught instanceof Error ? caught.message : "Budgets could not be loaded.");
    }
  }, [selectedUnitId, year, month]);
  useEffect(() => { void load(); }, [load]);
  const done = (next: View, message: string) => { setView(next); setEditing(false); setNotice(message); };
  const costs = view?.lines.filter((line) => line.kind === "cost" && line.line !== "cost_of_sales") ?? [];
  const otherIncome = view?.lines.filter((line) => line.kind === "income" && line.line !== "sales") ?? [];
  const sales = view?.lines.find((line) => line.line === "sales");
  const cost = view?.lines.find((line) => line.line === "cost_of_sales");
  const runningMonth = costs.reduce((sum, line) => [sum[0] + num(line.month.budget), sum[1] + num(line.month.actual)], [0, 0]);

  return <section className="content-card supplier-payments bank-cash budgets" aria-labelledby="budgets-title">
    <header className="sp-head">
      <span className="eyebrow">FINANCE · BUDGETS · TEST ONLY{view ? ` · ${view.unit.name}` : ""}</span>
      <h2 id="budgets-title">Budgets</h2>
      <p>What this business unit plans to sell and spend each month, next to what actually happened. The actual figures come from the accounts, so they match the profit and loss statement. One person prepares the budget and another approves it; once approved it is locked.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!view && !error && <p className="sp-empty" role="status">Loading budgets…</p>}
    {view && sales && cost && <>
      <div className="bc-filters">
        <label>Year<select value={view.year} onChange={(event) => { setNotice(""); setEditing(false); setYear(Number(event.target.value)); }}>
          {view.years.map((each) => <option key={each} value={each}>{each}</option>)}
        </select></label>
        <label>Compare up to<select value={view.month} onChange={(event) => setMonth(Number(event.target.value))}>
          {MONTH_NAMES.map((name, index) => <option key={name} value={index + 1}>{name}</option>)}
        </select></label>
        <div className="bg-state">
          {view.budget
            ? <><span className={`ex-status ${STATUS_CLASS[view.budget.status]}`}>{view.budget.status_label}</span>
              <small>{view.budget.status === "approved" ? `approved by ${view.budget.approved_by}` : `prepared by ${view.budget.prepared_by}`}</small></>
            : <span className="sp-muted">No {view.year} budget yet</span>}
        </div>
        {!editing && <div className="bc-buttons">
          {view.can_edit && <button className="button button-dark" type="button" onClick={() => { setNotice(""); setEditing(true); }}>{view.budget ? "Change budget" : `Prepare the ${view.year} budget`}</button>}
          {view.can_submit && <button className="button button-outline" type="button" onClick={async () => {
            try {
              done(await call<View>(`/api/finance/budgets/${view.year}/submit/?unit=${view.unit.id}`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: "{}" }), `The ${view.year} budget was sent for approval.`);
            } catch (caught) { setError(caught instanceof Error ? caught.message : "It could not be sent."); }
          }}>Send for approval</button>}
        </div>}
      </div>
      {!editing && <Decision view={view} csrfToken={csrfToken} onDone={done} />}
      {view.budget?.note && <p className="sp-hint">Note: {view.budget.note}</p>}
      {editing && <Editor view={view} csrfToken={csrfToken} onSaved={done} onCancel={() => setEditing(false)} />}
      {!editing && <>
      <ul className="bc-accounts ex-tiles" aria-label={`${MONTH_NAMES[view.month - 1]} against budget`}>
        <li className="bc-total"><span>Sales in {MONTH_NAMES[view.month - 1]}</span><strong>{fmt(sales.month.actual)}</strong><small>budget {fmt(sales.month.budget)} · {signed(sales.month.difference)}</small></li>
        <li className="bc-total"><span>Running costs in {MONTH_NAMES[view.month - 1]}</span><strong>{fmt(runningMonth[1])}</strong><small>budget {fmt(runningMonth[0])} · {signed(String(runningMonth[1] - runningMonth[0]))}</small></li>
        <li className="bc-total"><span>Net profit, January to {MONTH_NAMES[view.month - 1]}</span><strong>{fmt(view.net_profit.to_date.actual)}</strong><small>budget {fmt(view.net_profit.to_date.budget)} · {signed(view.net_profit.to_date.difference)}</small></li>
      </ul>
      <div className="sp-table-wrap"><table className="sp-table bc-table bg-table" aria-label="Budget against actual">
        <thead>
          <tr><th scope="col" rowSpan={2}>QAR</th><th scope="colgroup" colSpan={3}>{MONTH_NAMES[view.month - 1]} {view.year}</th><th scope="colgroup" colSpan={4} className="bg-gap">January to {MONTH_NAMES[view.month - 1]}</th><th scope="col" rowSpan={2} className="bc-num">Year budget</th></tr>
          <tr><th scope="col" className="bc-num">Budget</th><th scope="col" className="bc-num">Actual</th><th scope="col" className="bc-num">Difference</th>
            <th scope="col" className="bc-num bg-gap">Budget</th><th scope="col" className="bc-num">Actual</th><th scope="col" className="bc-num">Difference</th><th scope="col">Used</th></tr>
        </thead>
        <tbody>
          <Row label="Sales" compare={sales.month} toDate={sales.to_date} year={sales.year_budget} line="sales" />
          <Row label="Cost of sales" compare={cost.month} toDate={cost.to_date} year={cost.year_budget} line="cost_of_sales" />
          <Row label="Gross profit" compare={view.gross_profit.month} toDate={view.gross_profit.to_date} year={view.gross_profit.year_budget} strong />
          {costs.map((line) => <Row key={line.line} label={line.label} compare={line.month} toDate={line.to_date} year={line.year_budget} line={line.line} />)}
          {otherIncome.map((line) => <Row key={line.line} label={line.label} compare={line.month} toDate={line.to_date} year={line.year_budget} line={line.line} />)}
          <Row label="Net profit" compare={view.net_profit.month} toDate={view.net_profit.to_date} year={view.net_profit.year_budget} strong />
        </tbody>
      </table></div>
      <p className="sp-hint">Red: spent more than budgeted, or income below budget. Difference is actual minus budget.</p>
      {(() => {
        const [last, before] = view.net_profit.previous;
        const when = period === "month" ? MONTH_NAMES[view.month - 1] : `January to ${MONTH_NAMES[view.month - 1]}`;
        const rows = [sales, cost];
        return <>
          <div className="bg-earlier-head">
            <h3>Compared with earlier years</h3>
            <label>Period<select value={period} onChange={(event) => setPeriod(event.target.value as "month" | "to_date")}>
              <option value="to_date">January to {MONTH_NAMES[view.month - 1]}</option>
              <option value="month">{MONTH_NAMES[view.month - 1]} only</option>
            </select></label>
          </div>
          <div className="sp-table-wrap"><table className="sp-table bc-table bg-table" aria-label="Compared with earlier years">
            <thead>
              <tr><th scope="col" rowSpan={2}>QAR, actual</th><th scope="colgroup" colSpan={before ? 5 : 3}>{when}</th><th scope="colgroup" colSpan={before ? 2 : 1} className="bg-gap">Full year</th></tr>
              <tr><th scope="col" className="bc-num">{view.year}</th><th scope="col" className="bc-num bg-gap">{last.year}</th><th scope="col" className="bc-num">Change</th>
                {before && <><th scope="col" className="bc-num">{before.year}</th><th scope="col" className="bc-num">Change</th></>}
                <th scope="col" className="bc-num bg-gap">{last.year}</th>{before && <th scope="col" className="bc-num">{before.year}</th>}</tr>
            </thead>
            <tbody>
              {rows.map((line) => <EarlierRow key={line.line} label={line.label} now={line[period]} previous={line.previous} period={period} kind={line.kind} line={line.line} />)}
              <EarlierRow label="Gross profit" now={view.gross_profit[period]} previous={view.gross_profit.previous} period={period} kind="income" strong />
              {[...costs, ...otherIncome].map((line) => <EarlierRow key={line.line} label={line.label} now={line[period]} previous={line.previous} period={period} kind={line.kind} line={line.line} />)}
              <EarlierRow label="Net profit" now={view.net_profit[period]} previous={view.net_profit.previous} period={period} kind="income" strong />
            </tbody>
          </table></div>
          <p className="sp-hint">Change is {view.year} minus the earlier year for the same months. Red: costs went up, or income or profit went down.</p>
        </>;
      })()}
      </>}
      {view.history.length > 0 && <><h3>History</h3><ul className="bg-history">
        {view.history.map((event, index) => <li key={index}><time>{new Date(event.at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}</time> {event.by} {event.what}{event.note ? `: ${event.note}` : ""}</li>)}
      </ul></>}
      <p className="sp-hint">{view.policy_label}</p>
    </>}
  </section>;
}
