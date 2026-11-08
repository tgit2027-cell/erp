import { Fragment, useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";
import "./payment-plan.css";

type Part = { date: string; amount: string; value: string };
type Line = {
  due: string; source: string; source_label: string; label: string; payee: string; currency: string; late: boolean;
  amount: string; value: string; total_amount: string; total_value: string; send_on: Part[]; split: boolean;
  first_day: string; last_day: string; after_due: boolean; days_after_due: number;
};
type Money = { currency: string; amount: string; value: string };
type Account = { id: number | null; name: string; bank_name: string; account_last4: string; is_default?: boolean };
type Group = { account: Account; value: string; currencies: Money[]; lines: Line[] };
type Day = { date: string; banks: { bank_name: string; used: string; limit: string }[] };
type Period = { index: number; start: string; end: string; value: string; count: number; currencies: Money[]; groups: Group[]; days: Day[]; late: boolean; busy: boolean };
type Planned = { id: number; name: string; amount: string; currency: string; first_date: string; repeat: string; repeat_label: string; until: string | null; account: { id: number; name: string; bank_name: string } | null; created_by: string };
type Plan = {
  unit: { id: number; code: string; name: string }; today: string; view: "week" | "month"; end: string; periods: Period[]; total: string;
  late: { count: number; value: string }; busy_days: { bank_name: string; date: string; due: string; limit: string; over_by: string }[];
  moved: { payee: string; label: string; currency: string; amount: string; due: string; last_day: string; split: boolean }[];
  after_end: { count: number; value: string }; limits: { bank_name: string; daily_limit: string | null; set_by: string | null }[];
  accounts: Account[]; rates: { currency: string; rate: string; source: string }[]; planned: Planned[];
  assumptions: { supplier_days: number; periods: number }; can_manage: boolean; currency: string; policy: string;
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

const todayIso = () => new Date().toLocaleDateString("en-CA");
const fmt = (value: string | number) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const whole = (value: string | number) => Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 });
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const date = (iso: string) => new Date(`${iso}T00:00:00`);
const day = (iso: string) => date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
const weekday = (iso: string) => date(iso).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
const month = (iso: string) => date(iso).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
const periodName = (plan: Plan, period: Period) => plan.view === "week" ? `Week of ${day(period.start)}` : month(period.start);
const accountName = (account: Account) => account.id === null ? account.name
  : `${account.bank_name ? `${account.bank_name} · ` : ""}${show(account.name)}${account.account_last4 ? ` ··${account.account_last4}` : ""}`;

function Currencies({ money }: { money: Money[] }) {
  return <span className="pp-chips">{money.map((row) => <span key={row.currency} className="pp-chip">
    <b>{row.currency}</b> {fmt(row.amount)}
  </span>)}</span>;
}

function LimitDays({ days }: { days: Day[] }) {
  const banks = days[0]?.banks.map((bank) => bank.bank_name) ?? [];
  if (banks.length === 0) return null;
  return <div className="pp-days" aria-label="Foreign-currency transfers against each bank's daily limit">
    <h4>Foreign-currency transfers each day, against the bank's limit (QAR)</h4>
    {banks.map((bank, bankIndex) => <div key={bank} className="pp-day-row">
      <span className="pp-day-bank">{bank}</span>
      {days.map((row) => {
        const { used, limit } = row.banks[bankIndex];
        const share = Math.min(100, (Number(used) / Number(limit)) * 100);
        return <span key={row.date} className="pp-day" title={`${weekday(row.date)}: ${fmt(used)} of ${fmt(limit)}`}>
          <small>{weekday(row.date).split(" ")[0]}</small>
          <span className="pp-meter"><span style={{ width: `${share}%` }} className={share >= 100 ? "pp-full" : undefined} /></span>
          <small className="pw-mono">{Number(used) ? whole(used) : "–"}</small>
        </span>;
      })}
    </div>)}
  </div>;
}

function PeriodDetail({ plan, period, onClose }: { plan: Plan; period: Period; onClose: () => void }) {
  return <article className="sp-form pp-detail" aria-label={periodName(plan, period)}>
    <header className="mj-detail-head"><h3>{periodName(plan, period)}: {day(period.start)} to {day(period.end)}</h3>
      <button className="button button-quiet" type="button" onClick={onClose}>Close</button></header>
    {plan.view === "week" && <LimitDays days={period.days} />}
    {period.groups.length === 0 ? <p className="sp-empty">Nothing to pay in this {plan.view}.</p> : period.groups.map((group) =>
      <section key={group.account.id ?? 0} className="pp-group" aria-label={accountName(group.account)}>
        <header className="pp-group-head">
          <h4>{accountName(group.account)}</h4>
          <Currencies money={group.currencies} />
          <strong className="pw-mono">QAR {fmt(group.value)}</strong>
        </header>
        <div className="sp-table-wrap"><table className="sp-table bc-table pp-table">
          <thead><tr><th scope="col">Send on</th><th scope="col">Pay to</th><th scope="col">For</th><th scope="col" className="bc-num">Amount</th><th scope="col" className="bc-num">QAR</th></tr></thead>
          <tbody>{group.lines.map((line, index) => <tr key={index}>
            <td>{line.send_on.map((part) => <span key={part.date} className="pp-send">{weekday(part.date)}</span>)}
              <small>due {day(line.due)}</small></td>
            <th scope="row">{show(line.payee)}<small>{line.source_label}</small></th>
            <td>{show(line.label)}
              {line.late && <span className="pp-flag pp-flag-late">Late</span>}
              {line.after_due && <span className="pp-flag">Moved {line.days_after_due} day{line.days_after_due === 1 ? "" : "s"} for the bank limit</span>}
              {line.split && <span className="pp-flag">Split over days: {line.total_amount !== line.amount ? `${fmt(line.total_amount)} in all` : "all in this period"}</span>}
            </td>
            <td className="pw-mono bc-num">{line.currency} {fmt(line.amount)}</td>
            <td className="pw-mono bc-num">{fmt(line.value)}</td>
          </tr>)}</tbody>
        </table></div>
      </section>)}
  </article>;
}

function PaymentForm({ plan, csrfToken, onDone }: { plan: Plan; csrfToken: string; onDone: (message: string) => void }) {
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("QAR");
  const [account, setAccount] = useState("");
  const [first, setFirst] = useState(todayIso());
  const [repeat, setRepeat] = useState("monthly");
  const [until, setUntil] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await call(`/api/finance/cash-forecast/?unit=${plan.unit.id}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ name, direction: "out", amount, currency, account: account ? Number(account) : null,
          first_date: first, repeat, until: until || null }),
      });
      setName(""); setAmount(""); setUntil("");
      onDone(`${name} is added to the payment plan and the cash-flow forecast.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The payment could not be added.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form" onSubmit={(event) => void submit(event)} aria-label="Add a regular payment">
    <div className="sp-fields">
      <label>Pay to / what for<input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} required placeholder="e.g. Showroom rent, Natuzzi monthly order" /></label>
      <label>Amount<input inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} required /></label>
      <label>Currency<select value={currency} onChange={(event) => setCurrency(event.target.value)}>{["QAR", "USD", "EUR", "GBP"].map((code) => <option key={code}>{code}</option>)}</select></label>
      <label>Paid from<select value={account} onChange={(event) => setAccount(event.target.value)}>
        <option value="">Default bank account</option>
        {plan.accounts.map((row) => <option key={row.id ?? 0} value={String(row.id)}>{accountName(row)}</option>)}
      </select></label>
      <label>First payment<input type="date" value={first} onChange={(event) => setFirst(event.target.value)} required /></label>
      <label>Repeats<select value={repeat} onChange={(event) => setRepeat(event.target.value)}><option value="once">Once</option><option value="weekly">Every week</option><option value="monthly">Every month</option></select></label>
      {repeat !== "once" && <label>Until (optional)<input type="date" value={until} min={first} onChange={(event) => setUntil(event.target.value)} /></label>}
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy}>Add the payment</button></div>
  </form>;
}

function LimitRow({ plan, row, csrfToken, onDone }: { plan: Plan; row: Plan["limits"][number]; csrfToken: string; onDone: (message: string) => void }) {
  const [value, setValue] = useState(row.daily_limit ?? "");
  const [error, setError] = useState("");
  const save = async () => {
    setError("");
    try {
      await call(`/api/finance/payment-plan/limits/?unit=${plan.unit.id}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ bank_name: row.bank_name, daily_limit: value || null }),
      });
      onDone(value ? `${row.bank_name}'s daily limit is now QAR ${fmt(value)}.` : `${row.bank_name} has no daily limit now.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The limit could not be saved.");
    }
  };
  return <tr>
    <th scope="row">{row.bank_name}{row.set_by && <small>set by {row.set_by}</small>}</th>
    <td>{plan.can_manage
      ? <label className="pp-limit"><span className="visually-hidden">Daily limit for {row.bank_name} (QAR)</span>
        <input inputMode="decimal" value={value} placeholder="No limit" onChange={(event) => setValue(event.target.value)} /></label>
      : <span className="pw-mono">{row.daily_limit ? fmt(row.daily_limit) : "No limit"}</span>}
      {error && <div className="alert" role="alert">{error}</div>}</td>
    <td>{plan.can_manage && <button className="button button-outline" type="button" onClick={() => void save()}>Save</button>}</td>
  </tr>;
}

export default function PaymentPlan({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [view, setView] = useState<"week" | "month">("week");
  const [periods, setPeriods] = useState("8");
  const [supplierDays, setSupplierDays] = useState("30");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<number | null>(0);
  const [adding, setAdding] = useState(false);
  const [newBank, setNewBank] = useState("");

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    const query = new URLSearchParams({ unit: String(selectedUnitId), view, periods, supplier_days: supplierDays });
    try {
      setPlan(await call<Plan>(`/api/finance/payment-plan/?${query}`));
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The payment plan could not be loaded.");
    }
  }, [selectedUnitId, view, periods, supplierDays]);
  useEffect(() => { void load(); }, [load]);
  const switchView = (next: "week" | "month") => { setView(next); setPeriods(next === "week" ? "8" : "3"); setSelected(0); };
  const done = (message: string) => { setNotice(message); void load(); };
  const remove = async (item: Planned) => {
    if (!plan) return;
    try {
      await call(`/api/finance/cash-forecast/items/${item.id}/?unit=${plan.unit.id}`, { method: "DELETE", headers: { "X-CSRFToken": csrfToken } });
      done(`${show(item.name)} is removed.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The payment could not be removed.");
    }
  };
  const period = plan && selected !== null ? plan.periods[selected] : null;
  const first = plan?.periods[0];
  const thisName = view === "week" ? "this week" : "this month";

  return <section className="content-card supplier-payments bank-cash payment-plan" aria-labelledby="payment-plan-title">
    <header className="sp-head">
      <span className="eyebrow">FINANCE · PAYMENT PLAN · TEST ONLY{plan ? ` · ${plan.unit.name}` : ""}</span>
      <h2 id="payment-plan-title">Weekly and monthly payments</h2>
      <p>Every payment due each week or month, by bank account and currency: supplier bills, payments already prepared, staff claims and your regular payments such as rent, salaries and loan instalments. Foreign-currency transfers are spread over the days so no bank goes over its daily limit.</p>
    </header>
    <div className="bc-filters">
      <div className="report-view-toggle" role="group" aria-label="Show by">
        <button type="button" aria-pressed={view === "week"} onClick={() => switchView("week")}>By week</button>
        <button type="button" aria-pressed={view === "month"} onClick={() => switchView("month")}>By month</button>
      </div>
      <label>{view === "week" ? "Weeks" : "Months"}<select value={periods} onChange={(event) => setPeriods(event.target.value)}>
        {(view === "week" ? ["4", "8", "13", "26"] : ["1", "3", "6", "12"]).map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>We pay suppliers after (days)<input type="number" min={0} max={365} value={supplierDays} onChange={(event) => setSupplierDays(event.target.value)} /></label>
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!plan && !error && <p className="sp-empty" role="status">Working out the payments…</p>}
    {plan && first && <>
      <ul className="bc-accounts ex-tiles" aria-label="Summary">
        <li className="bc-total"><span>To pay {thisName}</span><strong>{fmt(first.value)}</strong><small>QAR · {first.count} payment{first.count === 1 ? "" : "s"}</small></li>
        <li className="bc-total"><span>Already late</span><strong className={plan.late.count ? "cf-negative" : undefined}>{fmt(plan.late.value)}</strong><small>{plan.late.count} payment{plan.late.count === 1 ? "" : "s"}, planned for now</small></li>
        <li className="bc-total"><span>Days over a bank limit</span><strong className={plan.busy_days.length ? "cf-negative" : undefined}>{plan.busy_days.length}</strong><small>{plan.moved.length} payment{plan.moved.length === 1 ? "" : "s"} moved to later days</small></li>
        <li className="bc-total"><span>Up to {day(plan.end)}</span><strong>{fmt(plan.total)}</strong><small>QAR in all</small></li>
      </ul>
      {plan.busy_days.length > 0 && <div className="pp-warning" role="alert">
        <strong>More foreign-currency payments fall due than the bank allows on these days:</strong>
        <ul>{plan.busy_days.map((row) => <li key={`${row.bank_name}-${row.date}`}>{weekday(row.date)}, {row.bank_name}: QAR {fmt(row.due)} due, limit {fmt(row.limit)} (over by {fmt(row.over_by)})</li>)}</ul>
        {plan.moved.length > 0 && <p>So these go later than due: {plan.moved.map((row) => `${show(row.payee)} ${row.currency} ${fmt(row.amount)} (due ${day(row.due)}, last part ${day(row.last_day)}${row.split ? ", split" : ""})`).join("; ")}. Pay earlier or ask the bank for a higher limit if that is too late.</p>}
      </div>}
      <div className="sp-table-wrap"><table className="sp-table bc-table pp-periods" aria-label={view === "week" ? "Week by week" : "Month by month"}>
        <thead><tr><th scope="col">{view === "week" ? "Week" : "Month"}</th><th scope="col">Currencies</th><th scope="col" className="bc-num">Payments</th><th scope="col" className="bc-num">Total QAR</th></tr></thead>
        <tbody>{plan.periods.map((row, index) => <Fragment key={row.index}><tr className={selected === index ? "mj-open" : undefined}>
          <th scope="row"><button className="fa-link" type="button" onClick={() => setSelected(selected === index ? null : index)}>{periodName(plan, row)}</button>
            <small>{day(row.start)} to {day(row.end)}{row.late ? " · includes late" : ""}{row.busy ? " · bank limit reached" : ""}</small></th>
          <td><Currencies money={row.currencies} /></td>
          <td className="pw-mono bc-num">{row.count || ""}</td>
          <td className="pw-mono bc-num">{Number(row.value) ? fmt(row.value) : ""}</td>
        </tr></Fragment>)}</tbody>
      </table></div>
      {plan.after_end.count > 0 && <p className="sp-hint">After {day(plan.end)}: {plan.after_end.count} more payment{plan.after_end.count === 1 ? "" : "s"}, QAR {fmt(plan.after_end.value)}.</p>}
      {period && <PeriodDetail plan={plan} period={period} onClose={() => setSelected(null)} />}

      <div className="cf-planned">
        <header className="mj-detail-head"><h3>Regular payments</h3>
          {plan.can_manage && <button className="button button-outline" type="button" onClick={() => setAdding(!adding)}>{adding ? "Done" : "Add a payment"}</button>}</header>
        <p className="sp-hint">Payments the books cannot know yet, such as rent, salaries, loan instalments or a standing order to a supplier. They also show in the cash-flow forecast.</p>
        {adding && <PaymentForm plan={plan} csrfToken={csrfToken} onDone={done} />}
        {plan.planned.length === 0 ? <p className="sp-empty">No regular payments yet{plan.can_manage ? "" : "; someone who records bank and cash can add them"}.</p>
          : <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label="Regular payments">
            <thead><tr><th scope="col">Pay to</th><th scope="col">When</th><th scope="col">From</th><th scope="col" className="bc-num">Amount</th><th scope="col"><span className="visually-hidden">Remove</span></th></tr></thead>
            <tbody>{plan.planned.map((item) => <tr key={item.id}>
              <th scope="row">{show(item.name)}<small>added by {item.created_by}</small></th>
              <td>{item.repeat_label} from {day(item.first_date)}{item.until ? ` until ${day(item.until)}` : ""}</td>
              <td>{item.account ? `${item.account.bank_name ? `${item.account.bank_name} · ` : ""}${show(item.account.name)}` : "Default bank account"}</td>
              <td className="pw-mono bc-num">{item.currency} {fmt(item.amount)}</td>
              <td>{plan.can_manage && <button className="button button-quiet" type="button" onClick={() => void remove(item)}>Remove</button>}</td>
            </tr>)}</tbody>
          </table></div>}
      </div>

      <div className="cf-planned">
        <header className="mj-detail-head"><h3>Daily foreign-currency limits</h3></header>
        <p className="sp-hint">The most each bank lets you send abroad in foreign currency in one working day, in QAR. Leave empty for no limit.</p>
        <div className="sp-table-wrap"><table className="sp-table bc-table pp-limits" aria-label="Bank limits">
          <thead><tr><th scope="col">Bank</th><th scope="col">Daily limit (QAR)</th><th scope="col"><span className="visually-hidden">Save</span></th></tr></thead>
          <tbody>{plan.limits.map((row) => <LimitRow key={`${row.bank_name}-${row.daily_limit}`} plan={plan} row={row} csrfToken={csrfToken} onDone={done} />)}
            {plan.limits.length === 0 && <tr><td colSpan={3} className="sp-empty">No banks yet. Add bank accounts on Bank and cash{plan.can_manage ? ", or name a bank below" : ""}.</td></tr>}
          </tbody>
        </table></div>
        {plan.can_manage && <div className="bc-filters">
          <label>Another bank<input value={newBank} maxLength={80} onChange={(event) => setNewBank(event.target.value)} placeholder="Bank name" /></label>
          {newBank.trim() && <table className="sp-table bc-table pp-limits"><tbody>
            <LimitRow plan={plan} row={{ bank_name: newBank.trim(), daily_limit: null, set_by: null }} csrfToken={csrfToken} onDone={(message) => { setNewBank(""); done(message); }} />
          </tbody></table>}
        </div>}
      </div>
      <p className="sp-hint">Rates used for QAR: {plan.rates.map((row) => `${row.currency} ${row.rate}${row.source === "indicative" ? " (indicative until the books have one)" : ""}`).join(", ")}. {show(plan.policy).replace(/^./, (letter) => letter.toUpperCase())}</p>
    </>}
  </section>;
}
