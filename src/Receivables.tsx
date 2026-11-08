import { useCallback, useEffect, useState } from "react";
import "./receivables.css";

type Unit = { id: number; code: string; name: string };
type Bucket = { key: BucketKey; label: string };
type BucketKey = string;
type Amounts = Record<BucketKey, string>;
type AgeBy = "days" | "years";
type Show = "all" | "unpaid";
type Customer = { id: number; name: string; title: string; client_number: string | null; project_number: string | null; contact: string };
type OpenInvoice = { id: number; kind: "invoice" | "old_balance"; number: string; date: string; quote_id: number | null; total: string; paid: string; credited: string; owed: string; age_days: number; bucket: BucketKey };
type AgingRow = Customer & { buckets: Amounts; total: string; oldest_days: number; invoices: OpenInvoice[] };
type Aging = { unit: Unit; as_of: string; by: AgeBy; buckets: Bucket[]; customers: AgingRow[]; totals: Amounts; total: string; policy_label: string };
type StatementLine = { kind: string; reference: string; description: string; date: string; debit: string; credit: string; balance: string };
type UnpaidInvoice = OpenInvoice & { running: string };
type Statement = {
  unit: Unit; customer: Customer; show: Show; by: AgeBy; start: string; end: string; opening_balance?: string; closing_balance?: string;
  total_debits?: string; total_credits?: string; lines: StatementLine[]; unpaid?: UnpaidInvoice[]; aging: Amounts; invoices_owed: string;
  buckets: Bucket[]; policy_label: string;
};

async function call<T>(path: string): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Receivables could not be loaded.");
  return data as T;
}

const today = () => new Date().toLocaleDateString("en-CA");
const yearStart = () => `${new Date().getFullYear()}-01-01`;
const qar = (value: string) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const money = (value: string) => (Number(value) === 0 ? "—" : qar(value));
const showName = (name: string) => name.replace(/^TEST ONLY\s*/, "");
const numbers = (customer: Customer) => [customer.client_number, customer.project_number].filter(Boolean).join(" · ") || "—";
const lastKey = (buckets: Bucket[]) => buckets[buckets.length - 1]?.key;
const balanceWords = (value: string) => {
  const amount = Number(value);
  if (amount > 0) return `${qar(value)} QAR owed by the customer`;
  if (amount < 0) return `${qar(String(-amount))} QAR held for the customer`;
  return "Nothing owed";
};

function StatementView({ unitQuery, customerId, by, onBack }: { unitQuery: string; customerId: number; by: AgeBy; onBack: () => void }) {
  const [start, setStart] = useState(yearStart);
  const [end, setEnd] = useState(today);
  const [show, setShow] = useState<Show>("all");
  const [statement, setStatement] = useState<Statement | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    call<Statement>(`/api/finance/receivables/${customerId}/statement/?${unitQuery}&start=${start}&end=${end}&show=${show}&by=${by}`)
      .then((data) => { if (live) { setStatement(data); setError(""); } })
      .catch((caught: Error) => { if (live) { setStatement(null); setError(caught.message); } });
    return () => { live = false; };
  }, [unitQuery, customerId, start, end, show, by]);
  return <section className="rv-statement" aria-labelledby="rv-statement-title">
    <button type="button" className="button button-outline rv-back" onClick={onBack}>← Back to who owes us</button>
    {statement && <div className="rv-statement-head">
      <span className="eyebrow">CUSTOMER STATEMENT · {statement.unit.name}</span>
      <h3 id="rv-statement-title">{statement.customer.title && <span className="rv-title">{statement.customer.title}</span>}{showName(statement.customer.name)}</h3>
      <p>{numbers(statement.customer)}{statement.customer.contact ? ` · ${statement.customer.contact}` : ""}</p>
    </div>}
    <div className="rv-filters">
      <div className="rv-switch" role="group" aria-label="What the statement shows">
        <button type="button" aria-pressed={show === "unpaid"} onClick={() => setShow("unpaid")}>Unpaid invoices only</button>
        <button type="button" aria-pressed={show === "all"} onClick={() => setShow("all")}>All invoices and payments</button>
      </div>
      {show === "all" && <label>From<input type="date" value={start} max={end} onChange={(event) => setStart(event.target.value)} /></label>}
      <label>{show === "all" ? "To" : "Unpaid at the end of"}<input type="date" value={end} min={show === "all" ? start : undefined} onChange={(event) => setEnd(event.target.value)} /></label>
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    {!statement && !error && <p className="rv-hint" role="status">Loading the statement…</p>}
    {statement && statement.show === "unpaid" && statement.unpaid && <>
      <div className="rv-table-wrap"><table className="rv-table" aria-label="Unpaid invoices">
        <thead><tr><th scope="col">Invoice</th><th scope="col">Date</th><th scope="col" className="rv-num">Days</th><th scope="col" className="rv-num">Invoice total</th><th scope="col" className="rv-num">Paid</th><th scope="col" className="rv-num">Credited</th><th scope="col" className="rv-num">Still owed</th><th scope="col" className="rv-num">Running total</th></tr></thead>
        <tbody>
          {statement.unpaid.length === 0 && <tr><td colSpan={8} className="rv-hint">No unpaid invoices at the end of {statement.end}.</td></tr>}
          {statement.unpaid.map((invoice) => <tr key={`${invoice.kind}-${invoice.id}`}>
            <td>{invoice.number}<small>{invoice.quote_id === null ? "From the old system" : `Quote ${invoice.quote_id}`}</small></td><td>{invoice.date}</td><td className="rv-num">{invoice.age_days}</td>
            <td className="rv-num">{qar(invoice.total)}</td><td className="rv-num">{money(invoice.paid)}</td><td className="rv-num">{money(invoice.credited)}</td>
            <td className="rv-num rv-strong">{qar(invoice.owed)}</td><td className="rv-num">{qar(invoice.running)}</td>
          </tr>)}
        </tbody>
        <tfoot><tr><th scope="row" colSpan={6}>Total unpaid</th><td className="rv-num">{qar(statement.invoices_owed)}</td><td /></tr></tfoot>
      </table></div>
    </>}
    {statement && statement.show === "all" && <>
      <dl className="rv-summary">
        <div><dt>Opening balance {statement.start}</dt><dd>{qar(statement.opening_balance ?? "0")}</dd></div>
        <div><dt>Charged in period</dt><dd>{qar(statement.total_debits ?? "0")}</dd></div>
        <div><dt>Paid or credited in period</dt><dd>{qar(statement.total_credits ?? "0")}</dd></div>
        <div className="rv-closing"><dt>Closing balance {statement.end}</dt><dd>{qar(statement.closing_balance ?? "0")}<small>{balanceWords(statement.closing_balance ?? "0")}</small></dd></div>
      </dl>
      <div className="rv-table-wrap"><table className="rv-table" aria-label="Statement lines">
        <thead><tr><th scope="col">Date</th><th scope="col">Reference</th><th scope="col">Details</th><th scope="col" className="rv-num">Charged</th><th scope="col" className="rv-num">Paid or credited</th><th scope="col" className="rv-num">Balance</th></tr></thead>
        <tbody>
          <tr className="rv-opening"><td>{statement.start}</td><td /><td>Opening balance</td><td /><td /><td className="rv-num">{qar(statement.opening_balance ?? "0")}</td></tr>
          {statement.lines.length === 0 && <tr><td colSpan={6} className="rv-hint">Nothing moved in this period.</td></tr>}
          {statement.lines.map((line, index) => <tr key={`${line.reference}-${line.kind}-${index}`}>
            <td>{line.date}</td><td>{line.reference}</td><td>{line.description}</td>
            <td className="rv-num">{line.debit ? qar(line.debit) : ""}</td><td className="rv-num">{line.credit ? qar(line.credit) : ""}</td>
            <td className="rv-num">{qar(line.balance)}</td>
          </tr>)}
        </tbody>
      </table></div>
    </>}
    {statement && <>
      <div className="rv-due" aria-label="Unpaid invoices by age">
        <h4>Unpaid invoices on {statement.end}: {qar(statement.invoices_owed)} QAR</h4>
        <ul>{statement.buckets.map((bucket) => <li key={bucket.key}><span>{bucket.label}</span><strong>{money(statement.aging[bucket.key])}</strong></li>)}</ul>
        {statement.show === "all" && <p className="rv-hint">A negative balance is money the customer paid ahead, for example a deposit on an order not yet delivered.</p>}
      </div>
    </>}
  </section>;
}

export default function Receivables({ selectedUnitId }: { selectedUnitId: number | null }) {
  const [asOf, setAsOf] = useState(today);
  const [by, setBy] = useState<AgeBy>("days");
  const [aging, setAging] = useState<Aging | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [statementFor, setStatementFor] = useState<number | null>(null);
  const unitQuery = selectedUnitId ? `unit=${selectedUnitId}` : "";
  const load = useCallback(() => {
    let live = true;
    call<Aging>(`/api/finance/receivables/?${unitQuery}&as_of=${asOf}&by=${by}`)
      .then((data) => { if (live) { setAging(data); setError(""); } })
      .catch((caught: Error) => { if (live) { setAging(null); setError(caught.message); } });
    return () => { live = false; };
  }, [unitQuery, asOf, by]);
  useEffect(load, [load]);
  if (statementFor !== null) {
    return <section className="receivables"><StatementView unitQuery={unitQuery} customerId={statementFor} by={by} onBack={() => setStatementFor(null)} /></section>;
  }
  return <section className="receivables" aria-labelledby="rv-title">
    <div className="rv-head">
      <span className="eyebrow">FINANCE · TEST ONLY SYNTHETIC RECEIVABLES</span>
      <h2 id="rv-title">Who owes us{aging ? ` · ${aging.unit.name}` : ""}</h2>
      <p>Every invoice with money still owed on it, grouped by how long ago it was issued. What is owed is the invoice total less payments and credit notes. Open a customer to see their statement.</p>
    </div>
    <div className="rv-filters">
      <label>Owed at the end of<input type="date" value={asOf} onChange={(event) => setAsOf(event.target.value || today())} /></label>
      <div className="rv-switch" role="group" aria-label="Age by">
        <button type="button" aria-pressed={by === "days"} onClick={() => setBy("days")}>By days</button>
        <button type="button" aria-pressed={by === "years"} onClick={() => setBy("years")}>By years</button>
      </div>
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    {!aging && !error && <p className="rv-hint" role="status">Loading receivables…</p>}
    {aging && <>
      <ul className="rv-tiles" aria-label="Total owed by age">
        <li className="rv-tile-total"><span>Total owed</span><strong>{qar(aging.total)}</strong><small>QAR · {aging.customers.length} {aging.customers.length === 1 ? "customer" : "customers"}</small></li>
        {aging.buckets.map((bucket) => <li key={bucket.key} className={bucket.key === lastKey(aging.buckets) && Number(aging.totals[bucket.key]) > 0 ? "rv-tile-late" : ""}>
          <span>{bucket.label}</span><strong>{money(aging.totals[bucket.key])}</strong>
        </li>)}
      </ul>
      {aging.customers.length === 0
        ? <p className="rv-empty">No customer owes {aging.unit.name} anything at the end of {aging.as_of}.</p>
        : <div className="rv-table-wrap"><table className="rv-table" aria-label="Amounts owed by customer">
          <thead><tr>
            <th scope="col">Customer</th><th scope="col">Number</th>
            {aging.buckets.map((bucket) => <th scope="col" className="rv-num" key={bucket.key}>{bucket.label}</th>)}
            <th scope="col" className="rv-num">Total</th><th scope="col"><span className="visually-hidden">Actions</span></th>
          </tr></thead>
          <tbody>{aging.customers.map((row) => <CustomerRows key={row.id} row={row} buckets={aging.buckets}
            expanded={open === row.id} onToggle={() => setOpen(open === row.id ? null : row.id)} onStatement={() => setStatementFor(row.id)} />)}</tbody>
          <tfoot><tr><th scope="row" colSpan={2}>Total</th>
            {aging.buckets.map((bucket) => <td className="rv-num" key={bucket.key}>{money(aging.totals[bucket.key])}</td>)}
            <td className="rv-num">{qar(aging.total)}</td><td />
          </tr></tfoot>
        </table></div>}
      <p className="rv-hint">{aging.policy_label} Amounts in QAR.</p>
    </>}
  </section>;
}

function CustomerRows({ row, buckets, expanded, onToggle, onStatement }: {
  row: AgingRow; buckets: Bucket[]; expanded: boolean; onToggle: () => void; onStatement: () => void;
}) {
  return <>
    <tr className={expanded ? "rv-open" : ""}>
      <th scope="row">
        <button type="button" className="rv-link" aria-expanded={expanded} onClick={onToggle}>
          {row.title && <span className="rv-title">{row.title}</span>}{showName(row.name)}
        </button>
        <small>{row.invoices.length} unpaid {row.invoices.length === 1 ? "invoice" : "invoices"} · oldest {row.oldest_days} {row.oldest_days === 1 ? "day" : "days"}</small>
      </th>
      <td className="rv-number">{numbers(row)}</td>
      {buckets.map((bucket) => <td className={`rv-num${bucket.key === lastKey(buckets) && Number(row.buckets[bucket.key]) > 0 ? " rv-late" : ""}`} key={bucket.key}>{money(row.buckets[bucket.key])}</td>)}
      <td className="rv-num rv-strong">{qar(row.total)}</td>
      <td><button type="button" className="button button-outline rv-small" onClick={onStatement}>Statement</button></td>
    </tr>
    {expanded && <tr className="rv-detail"><td colSpan={buckets.length + 4}>
      <table className="rv-table rv-invoices" aria-label={`Unpaid invoices for ${showName(row.name)}`}>
        <thead><tr><th scope="col">Invoice</th><th scope="col">Date</th><th scope="col" className="rv-num">Days</th><th scope="col" className="rv-num">Invoice total</th><th scope="col" className="rv-num">Paid</th><th scope="col" className="rv-num">Credited</th><th scope="col" className="rv-num">Still owed</th></tr></thead>
        <tbody>{row.invoices.map((invoice) => <tr key={`${invoice.kind}-${invoice.id}`}>
          <td>{invoice.number}<small>{invoice.quote_id === null ? "From the old system" : `Quote ${invoice.quote_id}`}</small></td><td>{invoice.date}</td><td className="rv-num">{invoice.age_days}</td>
          <td className="rv-num">{qar(invoice.total)}</td><td className="rv-num">{money(invoice.paid)}</td><td className="rv-num">{money(invoice.credited)}</td>
          <td className="rv-num rv-strong">{qar(invoice.owed)}</td>
        </tr>)}</tbody>
      </table>
    </td></tr>}
  </>;
}
