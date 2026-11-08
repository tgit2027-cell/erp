import { useEffect, useState, type FormEvent } from "react";

type Row = { receipt_id: number; supplier: string; what: string; currency: string; owed: string; carried: string; booked_rate: string; revalued: string | null; difference: string | null };
type Run = { month: string; label: string; rates: Record<string, string>; source: string; net: string; item_count: number; posted_by: string; posted_at: string; journal_entry_id: number | null; reversal_entry_id: number | null; lines: Row[] };
type Selected = {
  month: string; label: string; last_day: string; blocked: string; can_post: boolean; run?: Run;
  currencies?: string[]; last_rates?: Record<string, { rate: string; date: string; source: string }>; rows?: Row[];
  summary?: { loss: string; gain: string; net: string; complete: boolean };
};
type Overview = {
  unit: { id: number; name: string }; can_post: boolean; months: { month: string; label: string; revalued: boolean; blocked: string }[];
  selected: Selected; history: Run[]; policy_label: string;
};

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The request could not be completed.");
  return data as T;
}
const amount = (value: string | null | undefined) => {
  if (value === null || value === undefined) return "";
  const number = Number(value);
  const text = Math.abs(number).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return number < 0 ? `(${text})` : text;
};
const effect = (net: string) => Number(net) > 0 ? `loss of QAR ${amount(net)}` : Number(net) < 0 ? `gain of QAR ${amount(String(-Number(net)))}` : "no difference";
const day = (value: string) => new Date(`${value}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });

function url(unitId: number | null, month: string, rates: Record<string, string>) {
  const search = new URLSearchParams();
  if (unitId) search.set("unit", String(unitId));
  if (month) search.set("month", month);
  for (const [code, rate] of Object.entries(rates)) if (rate) search.set(`rate_${code}`, rate);
  return `/api/finance/ledger/fx-revaluation/?${search}`;
}

function Lines({ rows, label }: { rows: Row[]; label: string }) {
  return <div className="gl-table-wrap"><table className="gl-table fx-table" aria-label={label}>
    <thead><tr><th scope="col">Owed to</th><th scope="col">For</th><th scope="col" className="gl-num">Still owed</th>
      <th scope="col" className="gl-num">Booked rate</th><th scope="col" className="gl-num">Booked (QAR)</th>
      <th scope="col" className="gl-num">At month-end rate (QAR)</th><th scope="col" className="gl-num">Difference</th></tr></thead>
    <tbody>{rows.map((row) => <tr key={`${row.receipt_id}-${row.what}`}>
      <th scope="row">{row.supplier}<small>Receipt {row.receipt_id}</small></th>
      <td>{row.what}</td>
      <td className="gl-num">{row.currency} {amount(row.owed)}</td>
      <td className="gl-num">{row.booked_rate}</td>
      <td className="gl-num">{amount(row.carried)}</td>
      <td className="gl-num">{row.revalued === null ? <span className="fx-waiting">Enter the {row.currency} rate</span> : amount(row.revalued)}</td>
      <td className={`gl-num ${row.difference && Number(row.difference) > 0 ? "fx-loss" : row.difference && Number(row.difference) < 0 ? "fx-gain" : ""}`}>{amount(row.difference)}</td>
    </tr>)}</tbody>
  </table></div>;
}

export default function FxRevaluationView({ unitId, csrfToken }: { unitId: number | null; csrfToken: string }) {
  const [data, setData] = useState<Overview | null>(null);
  const [month, setMonth] = useState("");
  const [rates, setRates] = useState<Record<string, string>>({});
  const [source, setSource] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      call<Overview>(url(unitId, month, rates)).then((result) => {
        if (!live) return;
        setData(result);
        setError("");
        const offered = result.selected.last_rates ?? {};
        const missing = (result.selected.currencies ?? []).filter((code) => rates[code] === undefined && offered[code]);
        if (missing.length) setRates((current) => ({ ...current, ...Object.fromEntries(missing.map((code) => [code, offered[code].rate])) }));
      }).catch((caught: Error) => { if (live) setError(caught.message); });
    }, Object.keys(rates).length ? 300 : 0);
    return () => { live = false; clearTimeout(timer); };
  }, [unitId, month, rates]);
  const chooseMonth = (next: string) => { setMonth(next); setRates({}); setNotice(""); };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!data) return;
    setBusy(true);
    setNotice("");
    try {
      const result = await call<Overview>(url(unitId, "", {}), {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ month: data.selected.month, rates, source: source.trim() }),
      });
      setData(result);
      setError("");
      const run = result.selected.run;
      setNotice(run ? `${run.label} revalued: ${effect(run.net)}, reversed on the first day of the next month.` : "Revalued.");
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!data) return error ? <div className="alert" role="alert">{error}</div> : <p className="gl-empty" role="status">Loading…</p>;
  const selected = data.selected;
  const rows = selected.rows ?? [];
  const ready = selected.can_post && !!selected.summary?.complete && source.trim().length > 0;
  return <section className="fs-months fx-reval" aria-labelledby="fx-title">
    <h3 id="fx-title">Currency revaluation <small>{data.unit.name}</small></h3>
    <p className="gl-hint">At the end of each month, what we still owe suppliers in euros, dollars or pounds is valued again at that day's rate. The difference is posted as an unrealised exchange gain or loss on the last day of the month, and reversed on the first day of the next month, so the real gain or loss is still worked out when the supplier is paid. Do this before closing the month.
      {!data.can_post && " You can see the figures, but posting needs the “Close months” permission."}</p>
    <div className="gl-tabs" role="group" aria-label="Month to revalue">
      {data.months.map((row) => <button key={row.month} type="button" aria-pressed={selected.month === row.month} onClick={() => chooseMonth(row.month)}>
        {row.label}{row.revalued ? " ✓" : ""}</button>)}
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="fs-notice" role="status">{notice}</p>}
    {selected.run ? <div className="fx-run">
      <p><strong>{selected.run.label} is revalued:</strong> {effect(selected.run.net)} on {selected.run.item_count} open {selected.run.item_count === 1 ? "balance" : "balances"},
        at {Object.entries(selected.run.rates).map(([code, rate]) => `${code} ${rate}`).join(", ")} ({selected.run.source}). Posted by {selected.run.posted_by}.
        {selected.run.journal_entry_id && ` Journal entries ${selected.run.journal_entry_id} and ${selected.run.reversal_entry_id} (reversal).`}</p>
      <Lines rows={selected.run.lines} label={`Revalued at the end of ${selected.run.label}`} />
    </div> : <>
      {selected.blocked && <p className="fx-blocked">{selected.blocked}</p>}
      {rows.length === 0 ? <p className="gl-empty">Nothing in a foreign currency was owed to suppliers on {day(selected.last_day)}.</p> : <form className="fx-form" onSubmit={submit} aria-label={`Revalue ${selected.label}`}>
        <fieldset className="fx-rates"><legend>Rates on {day(selected.last_day)} (QAR for one unit)</legend>
          {(selected.currencies ?? []).map((code) => <label key={code}>{code}
            <input inputMode="decimal" value={rates[code] ?? ""} onChange={(event) => setRates((current) => ({ ...current, [code]: event.target.value }))} />
            {selected.last_rates?.[code] && <small>Last used {selected.last_rates[code].rate} on {day(selected.last_rates[code].date)}</small>}
          </label>)}
          <label className="fx-source">Where the rates come from<input value={source} maxLength={120} onChange={(event) => setSource(event.target.value)}
            placeholder="For example: QCB closing rates" /></label>
        </fieldset>
        <Lines rows={rows} label={`Owed in foreign currency at the end of ${selected.label}`} />
        {selected.summary && <p className="fx-total" aria-live="polite">
          {selected.summary.complete ? <>Posting will book an unrealised {effect(selected.summary.net)}
            {Number(selected.summary.loss) > 0 && Number(selected.summary.gain) > 0 && ` (losses ${amount(selected.summary.loss)}, gains ${amount(selected.summary.gain)})`}.</>
            : "Enter a rate for every currency to see the total."}</p>}
        {selected.can_post && <button className="button button-dark" type="submit" disabled={busy || !ready}>Post revaluation for {selected.label}</button>}
      </form>}
    </>}
    <h4>Revaluations posted</h4>
    {data.history.length === 0 ? <p className="gl-empty">No month has been revalued yet.</p>
      : <ul className="fs-history">{data.history.map((run) => <li key={run.month}>
        <strong>{run.label}</strong> · {effect(run.net)} · {Object.entries(run.rates).map(([code, rate]) => `${code} ${rate}`).join(", ")} · {run.posted_by}
        <span>{run.source}</span>
      </li>)}</ul>}
    <p className="gl-hint">{data.policy_label}</p>
  </section>;
}
