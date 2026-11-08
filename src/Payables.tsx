import { useCallback, useEffect, useState } from "react";
import "./receivables.css";

type Unit = { id: number; code: string; name: string };
type BucketKey = string;
type AgeBy = "days" | "years";
type Bucket = { key: BucketKey; label: string };
type Amounts = Record<BucketKey, string>;
type Item = {
  receipt_id: number | null; old_balance_id?: number; bill?: { number: string } | null; received_on: string; age_days: number; bucket: BucketKey; paying_for: string; label: string;
  currency: string; booked: string; cleared: string; owed: string; owed_in_currency: string | null; waiting_approval: string;
};
type SupplierRow = { id: number; code: string; name: string; buckets: Amounts; total: string; oldest_days: number; waiting_approval: string; items: Item[] };
type Aging = {
  unit: Unit; as_of: string; buckets: Bucket[]; suppliers: SupplierRow[]; totals: Amounts; total: string;
  waiting_approval: string; can_pay: boolean; currency: string; policy_label: string;
};

async function call<T>(path: string): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Payables could not be loaded.");
  return data as T;
}

const today = () => new Date().toLocaleDateString("en-CA");
const qar = (value: string) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const money = (value: string) => (Number(value) === 0 ? "—" : qar(value));
const showName = (name: string) => name.replace(/^TEST ONLY\s*/, "");

export default function Payables({ selectedUnitId }: { selectedUnitId: number | null }) {
  const [asOf, setAsOf] = useState(today);
  const [by, setBy] = useState<AgeBy>("days");
  const [aging, setAging] = useState<Aging | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const unitQuery = selectedUnitId ? `unit=${selectedUnitId}` : "";
  const load = useCallback(() => {
    let live = true;
    call<Aging>(`/api/finance/payables/?${unitQuery}&as_of=${asOf}&by=${by}`)
      .then((data) => { if (live) { setAging(data); setError(""); } })
      .catch((caught: Error) => { if (live) { setAging(null); setError(caught.message); } });
    return () => { live = false; };
  }, [unitQuery, asOf, by]);
  useEffect(load, [load]);
  return <section className="receivables payables" aria-labelledby="pv-title">
    <div className="rv-head">
      <span className="eyebrow">FINANCE · TEST ONLY SYNTHETIC PAYABLES</span>
      <h2 id="pv-title">What we owe{aging ? ` · ${aging.unit.name}` : ""}</h2>
      <p>Every purchase receipt with something still to pay: the supplier for the goods, and the freight, clearance or other parties for its charges. Grouped by how long ago the goods were received. Only approved payments reduce what is owed.</p>
    </div>
    <div className="rv-filters">
      <label>Owed at the end of<input type="date" value={asOf} onChange={(event) => setAsOf(event.target.value || today())} /></label>
      <div className="rv-switch" role="group" aria-label="Age by">
        <button type="button" aria-pressed={by === "days"} onClick={() => setBy("days")}>By days</button>
        <button type="button" aria-pressed={by === "years"} onClick={() => setBy("years")}>By years</button>
      </div>
      {aging?.can_pay && <a className="button button-outline rv-small" href="#/finance/supplier-payments">Go to supplier payments</a>}
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    {!aging && !error && <p className="rv-hint" role="status">Loading payables…</p>}
    {aging && <>
      <ul className="rv-tiles" aria-label="Total owed by age">
        <li className="rv-tile-total"><span>Total we owe</span><strong>{qar(aging.total)}</strong><small>QAR · {aging.suppliers.length} {aging.suppliers.length === 1 ? "supplier" : "suppliers"}</small></li>
        {aging.buckets.map((bucket) => <li key={bucket.key} className={bucket.key === aging.buckets[aging.buckets.length - 1].key && Number(aging.totals[bucket.key]) > 0 ? "rv-tile-late" : ""}>
          <span>{bucket.label}</span><strong>{money(aging.totals[bucket.key])}</strong>
        </li>)}
        {Number(aging.waiting_approval) > 0 && <li className="pv-tile-waiting"><span>Waiting for approval</span><strong>{qar(aging.waiting_approval)}</strong><small>Prepared, not yet paid</small></li>}
      </ul>
      {aging.suppliers.length === 0
        ? <p className="rv-empty">{aging.unit.name} owes nothing on its purchase receipts at the end of {aging.as_of}.</p>
        : <div className="rv-table-wrap"><table className="rv-table" aria-label="Amounts owed by supplier">
          <thead><tr>
            <th scope="col">Supplier</th>
            {aging.buckets.map((bucket) => <th scope="col" className="rv-num" key={bucket.key}>{bucket.label}</th>)}
            <th scope="col" className="rv-num">Total</th>
          </tr></thead>
          <tbody>{aging.suppliers.map((row) => <SupplierRows key={row.id} row={row} buckets={aging.buckets}
            expanded={open === row.id} onToggle={() => setOpen(open === row.id ? null : row.id)} />)}</tbody>
          <tfoot><tr><th scope="row">Total</th>
            {aging.buckets.map((bucket) => <td className="rv-num" key={bucket.key}>{money(aging.totals[bucket.key])}</td>)}
            <td className="rv-num">{qar(aging.total)}</td>
          </tr></tfoot>
        </table></div>}
      <p className="rv-hint">{aging.policy_label}</p>
    </>}
  </section>;
}

function SupplierRows({ row, buckets, expanded, onToggle }: { row: SupplierRow; buckets: Bucket[]; expanded: boolean; onToggle: () => void }) {
  return <>
    <tr className={expanded ? "rv-open" : ""}>
      <th scope="row">
        <button type="button" className="rv-link" aria-expanded={expanded} onClick={onToggle}>{showName(row.name)}</button>
        <small>{row.code} · {row.items.length} unpaid {row.items.length === 1 ? "item" : "items"} · oldest {row.oldest_days} days
          {Number(row.waiting_approval) > 0 ? ` · ${qar(row.waiting_approval)} waiting for approval` : ""}</small>
      </th>
      {buckets.map((bucket) => <td className={`rv-num${bucket.key === buckets[buckets.length - 1].key && Number(row.buckets[bucket.key]) > 0 ? " rv-late" : ""}`} key={bucket.key}>{money(row.buckets[bucket.key])}</td>)}
      <td className="rv-num rv-strong">{qar(row.total)}</td>
    </tr>
    {expanded && <tr className="rv-detail"><td colSpan={buckets.length + 2}>
      <table className="rv-table rv-invoices" aria-label={`Unpaid items for ${showName(row.name)}`}>
        <thead><tr><th scope="col">Receipt</th><th scope="col">For</th><th scope="col">Received</th><th scope="col" className="rv-num">Days</th><th scope="col" className="rv-num">Booked (QAR)</th><th scope="col" className="rv-num">Paid (QAR)</th><th scope="col" className="rv-num">Still owed (QAR)</th><th scope="col" className="rv-num">In its currency</th></tr></thead>
        <tbody>{row.items.map((item) => <tr key={`${item.receipt_id ?? `old-${item.old_balance_id}`}-${item.paying_for}`}>
          <td>{item.receipt_id === null ? <>Bill {item.bill?.number}<small>From the old system</small></> : `Receipt ${item.receipt_id}`}</td><td>{item.label}{Number(item.waiting_approval) > 0 && <small>{qar(item.waiting_approval)} QAR waiting for approval</small>}</td>
          <td>{item.received_on}</td><td className="rv-num">{item.age_days}</td>
          <td className="rv-num">{qar(item.booked)}</td><td className="rv-num">{money(item.cleared)}</td>
          <td className="rv-num rv-strong">{qar(item.owed)}</td>
          <td className="rv-num">{item.owed_in_currency ? `${item.currency} ${qar(item.owed_in_currency)}` : "—"}</td>
        </tr>)}</tbody>
      </table>
    </td></tr>}
  </>;
}
