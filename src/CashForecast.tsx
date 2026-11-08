import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Line = { date: string; direction: "in" | "out"; source: string; source_label: string; label: string; amount: string; late: boolean };
type Week = { index: number; start: string; end: string; opening: string; in: string; out: string; net: string; closing: string; lines: Line[] };
type Planned = { id: number; name: string; direction: "in" | "out"; amount: string; currency?: string; first_date: string; repeat: string; repeat_label: string; until: string | null; created_by: string };
type Forecast = {
  unit: { id: number; code: string; name: string }; today: string; end: string; weeks: Week[]; opening: string; closing: string;
  lowest: { week: number; start: string; closing: string } | null; goes_negative: boolean;
  by_source: { key: string; label: string; in: string; out: string }[]; late: { in: string; out: string }; later: { in: string; out: string };
  assumptions: { weeks: number; customer_days: Record<string, number>; customer_types: { code: string; label: string }[]; supplier_days: number }; planned: Planned[]; can_plan: boolean; currency: string; policy: string;
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
const short = (value: string | number) => {
  const number = Number(value);
  const size = Math.abs(number);
  const text = size >= 1_000_000 ? `${(size / 1_000_000).toFixed(1)}M` : size >= 1000 ? `${Math.round(size / 1000)}k` : String(Math.round(size));
  return `${number < 0 ? "−" : ""}${text}`;
};
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const day = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
const POSITIVE = "#2a78d6";
const NEGATIVE = "#d14b3a";

/** Closing balance per week: bars from a zero line, blue above and red below, with the lowest week labelled. */
function BalanceChart({ forecast, selected, onSelect }: { forecast: Forecast; selected: number | null; onSelect: (index: number) => void }) {
  const [hover, setHover] = useState<number | null>(null);
  const values = forecast.weeks.map((week) => Number(week.closing));
  const top = Math.max(0, ...values);
  const bottom = Math.min(0, ...values);
  const span = top - bottom || 1;
  const width = 760;
  const height = 200;
  const pad = { left: 8, right: 8, top: 22, bottom: 44 };
  const plot = height - pad.top - pad.bottom;
  const slot = (width - pad.left - pad.right) / values.length;
  const bar = Math.max(6, Math.min(36, slot - 6));
  const y = (value: number) => pad.top + ((top - value) / span) * plot;
  const zero = y(0);
  const shown = hover ?? selected;
  const label = (index: number) => index === 0 || index === values.length - 1 || index + 1 === forecast.lowest?.week;
  return <figure className="cf-chart">
    <figcaption>Bank and cash at the end of each week (QAR)</figcaption>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Balance from ${fmt(forecast.opening)} today to ${fmt(forecast.closing)} by ${forecast.end}${forecast.lowest ? `; lowest ${fmt(forecast.lowest.closing)} in week ${forecast.lowest.week}` : ""}`}>
      <line x1={pad.left} x2={width - pad.right} y1={zero} y2={zero} className="cf-zero" />
      {values.map((value, index) => {
        const x = pad.left + index * slot + (slot - bar) / 2;
        const tip = Math.abs(y(value) - zero) < 2 ? 2 : Math.abs(y(value) - zero);
        const up = value >= 0;
        const radius = Math.min(4, bar / 2, tip / 2);
        const yTop = up ? zero - tip : zero;
        const path = up
          ? `M${x},${zero} V${yTop + radius} Q${x},${yTop} ${x + radius},${yTop} H${x + bar - radius} Q${x + bar},${yTop} ${x + bar},${yTop + radius} V${zero} Z`
          : `M${x},${zero} V${zero + tip - radius} Q${x},${zero + tip} ${x + radius},${zero + tip} H${x + bar - radius} Q${x + bar},${zero + tip} ${x + bar},${zero + tip - radius} V${zero} Z`;
        return <g key={index} className={shown === index ? "cf-bar cf-bar-on" : "cf-bar"}>
          <rect x={pad.left + index * slot} y={pad.top - 18} width={slot} height={plot + 18 + pad.bottom} fill="transparent"
            onMouseEnter={() => setHover(index)} onMouseLeave={() => setHover(null)} onClick={() => onSelect(index)}>
            <title>{`Week ${index + 1}, ${day(forecast.weeks[index].start)} to ${day(forecast.weeks[index].end)}: in ${fmt(forecast.weeks[index].in)}, out ${fmt(forecast.weeks[index].out)}, ends at ${fmt(value)}`}</title>
          </rect>
          <path d={path} fill={up ? POSITIVE : NEGATIVE} pointerEvents="none" />
          {(label(index) || shown === index) && <text x={x + bar / 2} y={up ? zero - tip - 6 : zero + tip + 12} textAnchor="middle" className="cf-value">{short(value)}</text>}
          {(index % 2 === 0 || shown === index) && <text x={pad.left + index * slot + slot / 2} y={height - 8} textAnchor="middle" className="cf-axis">{day(forecast.weeks[index].start)}</text>}
        </g>;
      })}
    </svg>
  </figure>;
}

function PlanForm({ forecast, csrfToken, onDone }: { forecast: Forecast; csrfToken: string; onDone: (message: string) => void }) {
  const [name, setName] = useState("");
  const [direction, setDirection] = useState("out");
  const [amount, setAmount] = useState("");
  const [first, setFirst] = useState(today());
  const [repeat, setRepeat] = useState("monthly");
  const [until, setUntil] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await call(`/api/finance/cash-forecast/?unit=${forecast.unit.id}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ name, direction, amount, first_date: first, repeat, until: until || null }),
      });
      setName(""); setAmount(""); setUntil("");
      onDone(`${name} is added to the forecast.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The planned item could not be added.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form cf-plan" onSubmit={(event) => void submit(event)} aria-label="Add a planned item">
    <div className="sp-fields cf-plan-grid">
      <label>What it is<input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} required placeholder="e.g. Showroom rent, October salaries" /></label>
      <label>Money<select value={direction} onChange={(event) => setDirection(event.target.value)}><option value="out">Goes out</option><option value="in">Comes in</option></select></label>
      <label>Amount (QAR)<input inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} required /></label>
      <label>First date<input type="date" value={first} onChange={(event) => setFirst(event.target.value)} required /></label>
      <label>Repeats<select value={repeat} onChange={(event) => setRepeat(event.target.value)}><option value="once">Once</option><option value="weekly">Every week</option><option value="monthly">Every month</option></select></label>
      {repeat !== "once" && <label>Until (optional)<input type="date" value={until} min={first} onChange={(event) => setUntil(event.target.value)} /></label>}
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy}>Add to the forecast</button></div>
  </form>;
}

export default function CashForecast({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [weeks, setWeeks] = useState("13");
  const [customerDays, setCustomerDays] = useState<Record<string, string>>({ individual: "30", company: "60", government: "90" });
  const [supplierDays, setSupplierDays] = useState("30");
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [planning, setPlanning] = useState(false);

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    const query = new URLSearchParams({ unit: String(selectedUnitId), weeks, supplier_days: supplierDays,
      ...Object.fromEntries(Object.entries(customerDays).map(([kind, days]) => [`${kind}_days`, days])) });
    try {
      setForecast(await call<Forecast>(`/api/finance/cash-forecast/?${query}`));
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The forecast could not be loaded.");
    }
  }, [selectedUnitId, weeks, customerDays, supplierDays]);
  useEffect(() => { void load(); }, [load]);
  const remove = async (item: Planned) => {
    if (!forecast) return;
    try {
      await call(`/api/finance/cash-forecast/items/${item.id}/?unit=${forecast.unit.id}`, { method: "DELETE", headers: { "X-CSRFToken": csrfToken } });
      setNotice(`${show(item.name)} is removed from the forecast.`);
      void load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The planned item could not be removed.");
    }
  };
  const week = forecast && selected !== null ? forecast.weeks[selected] : null;

  return <section className="content-card supplier-payments bank-cash cash-forecast" aria-labelledby="cash-forecast-title">
    <header className="sp-head">
      <span className="eyebrow">FINANCE · CASH-FLOW FORECAST · TEST ONLY{forecast ? ` · ${forecast.unit.name}` : ""}</span>
      <h2 id="cash-forecast-title">Cash-flow forecast</h2>
      <p>How much will be in the bank each week, from what the books already know: what customers still owe, what we owe suppliers, payments already prepared and staff claims to repay. Add planned items, such as rent or salaries, for what the books cannot know yet.</p>
    </header>
    <div className="bc-filters cf-assumptions">
      <label>Weeks<select value={weeks} onChange={(event) => setWeeks(event.target.value)}>{["4", "8", "13", "26"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
      {(forecast?.assumptions.customer_types ?? [{ code: "individual", label: "Individual" }, { code: "company", label: "Company" }, { code: "government", label: "Government" }]).map((kind) =>
        <label key={kind.code}>{kind.label} customers pay after (days)<input type="number" min={0} max={365} value={customerDays[kind.code] ?? ""}
          onChange={(event) => setCustomerDays({ ...customerDays, [kind.code]: event.target.value })} /></label>)}
      <label>We pay suppliers after (days)<input type="number" min={0} max={365} value={supplierDays} onChange={(event) => setSupplierDays(event.target.value)} /></label>
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!forecast && !error && <p className="sp-empty" role="status">Working out the forecast…</p>}
    {forecast && <>
      <ul className="bc-accounts ex-tiles" aria-label="Forecast">
        <li className="bc-total"><span>In the bank today</span><strong>{fmt(forecast.opening)}</strong><small>QAR, as on Bank and cash</small></li>
        <li className="bc-total"><span>Lowest point</span><strong className={forecast.lowest && Number(forecast.lowest.closing) < 0 ? "cf-negative" : undefined}>{forecast.lowest ? fmt(forecast.lowest.closing) : "–"}</strong>
          <small>{forecast.lowest ? `week ${forecast.lowest.week}, from ${day(forecast.lowest.start)}` : ""}{forecast.goes_negative ? " · goes below zero" : ""}</small></li>
        <li className="bc-total"><span>In {forecast.assumptions.weeks} weeks</span><strong>{fmt(forecast.closing)}</strong><small>QAR by {day(forecast.end)}</small></li>
        <li className="bc-total"><span>Already late</span><strong>{fmt(forecast.late.in)}</strong><small>owed to us · {fmt(forecast.late.out)} we owe</small></li>
      </ul>
      {forecast.goes_negative && <p className="alert" role="alert">The balance goes below zero in this forecast. Look at the red weeks below.</p>}
      <BalanceChart forecast={forecast} selected={selected} onSelect={(index) => setSelected(selected === index ? null : index)} />
      <div className="sp-table-wrap"><table className="sp-table bc-table cf-table" aria-label="Week by week">
        <thead><tr><th scope="col">Week</th><th scope="col" className="bc-num">Starts with</th><th scope="col" className="bc-num">Money in</th><th scope="col" className="bc-num">Money out</th><th scope="col" className="bc-num">Ends with</th></tr></thead>
        <tbody>{forecast.weeks.map((row, index) => <tr key={row.index} className={selected === index ? "mj-open" : undefined}>
          <th scope="row"><button className="fa-link" type="button" onClick={() => setSelected(selected === index ? null : index)}>Week {row.index}</button><small>{day(row.start)} to {day(row.end)}</small></th>
          <td className="pw-mono bc-num">{fmt(row.opening)}</td>
          <td className="pw-mono bc-num">{Number(row.in) ? fmt(row.in) : ""}</td>
          <td className="pw-mono bc-num">{Number(row.out) ? fmt(row.out) : ""}</td>
          <td className={`pw-mono bc-num${Number(row.closing) < 0 ? " cf-negative" : ""}`}>{fmt(row.closing)}</td>
        </tr>)}</tbody>
      </table></div>
      {week && <article className="sp-form cf-week" aria-label={`Week ${week.index}`}>
        <header className="mj-detail-head"><h3>Week {week.index}: {day(week.start)} to {day(week.end)}</h3>
          <button className="button button-quiet" type="button" onClick={() => setSelected(null)}>Close</button></header>
        {week.lines.length === 0 ? <p className="sp-empty">Nothing expected this week.</p>
          : <ul className="cf-lines">{week.lines.map((line, index) => <li key={index}>
            <span className="cf-line-date">{day(line.date)}</span>
            <span className="cf-line-what">{show(line.label)}<small>{line.source_label}{line.late ? " · already late" : ""}</small></span>
            <span className={`pw-mono cf-line-amount ${line.direction === "in" ? "cf-in" : "cf-out"}`}>{line.direction === "in" ? "+" : "−"}{fmt(line.amount)}</span>
          </li>)}</ul>}
      </article>}
      <div className="cf-sources">
        <h3>Where it comes from</h3>
        <dl className="an-facts">{forecast.by_source.filter((row) => Number(row.in) || Number(row.out)).map((row) => <div key={row.key}>
          <dt>{row.label}</dt><dd className="pw-mono">{Number(row.in) ? `+${fmt(row.in)}` : ""}{Number(row.in) && Number(row.out) ? " / " : ""}{Number(row.out) ? `−${fmt(row.out)}` : ""}</dd>
        </div>)}</dl>
        {(Number(forecast.later.in) > 0 || Number(forecast.later.out) > 0) && <p className="sp-hint">After {day(forecast.end)}: {fmt(forecast.later.in)} more expected in and {fmt(forecast.later.out)} out.</p>}
      </div>
      <div className="cf-planned">
        <header className="mj-detail-head"><h3>Planned items</h3>
          {forecast.can_plan && <button className="button button-outline" type="button" onClick={() => setPlanning(!planning)}>{planning ? "Done" : "Add a planned item"}</button>}</header>
        {planning && <PlanForm forecast={forecast} csrfToken={csrfToken} onDone={(message) => { setNotice(message); void load(); }} />}
        {forecast.planned.length === 0 ? <p className="sp-empty">No planned items yet{forecast.can_plan ? "" : "; someone who records bank and cash can add them"}.</p>
          : <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label="Planned items">
            <thead><tr><th scope="col">What</th><th scope="col">When</th><th scope="col" className="bc-num">Amount</th><th scope="col"><span className="visually-hidden">Remove</span></th></tr></thead>
            <tbody>{forecast.planned.map((item) => <tr key={item.id}>
              <th scope="row">{show(item.name)}<small>added by {item.created_by}</small></th>
              <td>{item.repeat_label} from {day(item.first_date)}{item.until ? ` until ${day(item.until)}` : ""}</td>
              <td className={`pw-mono bc-num ${item.direction === "in" ? "cf-in" : "cf-out"}`}>{item.direction === "in" ? "+" : "−"}{item.currency && item.currency !== "QAR" ? `${item.currency} ` : ""}{fmt(item.amount)}</td>
              <td>{forecast.can_plan && <button className="button button-quiet" type="button" onClick={() => void remove(item)}>Remove</button>}</td>
            </tr>)}</tbody>
          </table></div>}
      </div>
      <p className="sp-hint">{forecast.policy}</p>
    </>}
  </section>;
}
