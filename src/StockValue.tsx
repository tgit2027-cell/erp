import { useEffect, useState } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";
import "./stock-value.css";

type Group = { name: string; quantity: string; value: string; item_count: number; share: string; in_transit?: boolean };
type Line = {
  variant_id: number; sku: string; item_code: string; description: string; variant_description: string; brand: string;
  category: string; location_id: number; location: string; in_transit: boolean; quantity: string; value: string; average_cost: string;
};
type GroupKey = "location" | "brand" | "category";
type Report = {
  unit: { id: number; code: string; name: string }; as_of: string; today: string;
  total: { value: string; quantity: string; item_count: number; in_transit: string };
  groups: Record<GroupKey, Group[]>; lines: Line[]; line_count: number; policy_label: string;
  ledger: { balance: string; difference: string; agrees: boolean } | null;
};

const GROUPS: { key: GroupKey; label: string }[] = [
  { key: "location", label: "Location" }, { key: "brand", label: "Brand" }, { key: "category", label: "Category" },
];
const SHOWN = 200;

const money = (value: string) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const qty = (value: string) => Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 });
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const day = (value: string) => new Date(`${value}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
const lineKey = (line: Line, key: GroupKey) => key === "location" ? line.location : key === "brand" ? line.brand : line.category;

function lastMonthEnd(today: string) {
  const first = new Date(`${today.slice(0, 8)}01T00:00:00`);
  first.setDate(0);
  return `${first.getFullYear()}-${String(first.getMonth() + 1).padStart(2, "0")}-${String(first.getDate()).padStart(2, "0")}`;
}

export default function StockValue({ selectedUnitId }: { selectedUnitId: number }) {
  const [report, setReport] = useState<Report | null>(null);
  const [asOf, setAsOf] = useState("");
  const [group, setGroup] = useState<GroupKey>("location");
  const [chosen, setChosen] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    setLoading(true);
    const search = new URLSearchParams({ unit: String(selectedUnitId) });
    if (asOf) search.set("as_of", asOf);
    fetch(`/api/inventory/stock-value/?${search}`, { credentials: "same-origin" })
      .then(async (response) => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The stock value could not be loaded.");
        return data as Report;
      })
      .then((data) => { if (live) { setReport(data); setError(""); } })
      .catch((caught: Error) => { if (live) setError(caught.message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [selectedUnitId, asOf]);

  const chooseGroup = (key: GroupKey) => { setGroup(key); setChosen(""); };
  const lines = report ? report.lines.filter((line) => !chosen || lineKey(line, group) === chosen) : [];
  const exportUrl = report ? `/api/inventory/stock-value/?unit=${selectedUnitId}&as_of=${report.as_of}&export=xlsx` : "";

  return <section className="content-card supplier-payments stock-value" aria-labelledby="stock-value-title">
    <header className="sp-head">
      <span className="eyebrow">INVENTORY · STOCK VALUE · TEST ONLY{report ? ` · ${report.unit.name}` : ""}</span>
      <h2 id="stock-value-title">Stock value</h2>
      <p>What the stock is worth at cost on any day, by location, brand or category, and whether it agrees with the stock account in the books. Pick a past date to see the value at a month end.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {!report && !error && <p className="sp-empty" role="status">Loading stock value…</p>}
    {report && <>
      <div className="bc-filters">
        <label>Value at the end of
          <input type="date" value={asOf || report.as_of} max={report.today} onChange={(event) => { setAsOf(event.target.value); setChosen(""); }} />
        </label>
        <div className="sv-quick" role="group" aria-label="Quick dates">
          <button type="button" className="button button-outline ls-small" aria-pressed={report.as_of === report.today} onClick={() => setAsOf("")}>Today</button>
          <button type="button" className="button button-outline ls-small" aria-pressed={report.as_of === lastMonthEnd(report.today)} onClick={() => setAsOf(lastMonthEnd(report.today))}>Last month end</button>
        </div>
        <div className="bc-buttons">
          <a className="button button-outline" href={exportUrl} download>Download Excel</a>
        </div>
      </div>
      <dl className="sp-totals" aria-busy={loading}>
        <div><dt>Stock value on {day(report.as_of)}</dt><dd>QAR {money(report.total.value)}</dd></div>
        <div><dt>Items in stock</dt><dd>{report.total.item_count}</dd></div>
        <div><dt>Of which in transit or missing</dt><dd>QAR {money(report.total.in_transit)}</dd></div>
        <div><dt>Stock account in the books</dt>{report.ledger
          ? <dd>QAR {money(report.ledger.balance)}<small className={report.ledger.agrees ? "sv-agrees" : "sv-differs"}>
            {report.ledger.agrees ? "Agrees with the stock" : `Differs by QAR ${money(report.ledger.difference)}`}</small></dd>
          : <dd><small>Needs the “view general ledger” permission</small></dd>}</div>
      </dl>
      {report.ledger && !report.ledger.agrees && <p className="sv-note">The stock and the books can differ when a receipt or count is booked in the ledger on a different date from the day the goods moved, or when a manual journal touches the stock account. Look at the stock account in the general ledger for that date to find it.</p>}
      {report.line_count === 0 ? <p className="sp-empty">There was no stock in {report.unit.name} at the end of {day(report.as_of)}.</p> : <>
        <div className="gl-tabs sv-tabs" role="group" aria-label="Group by">
          {GROUPS.map((each) => <button key={each.key} type="button" aria-pressed={group === each.key} onClick={() => chooseGroup(each.key)}>By {each.label.toLowerCase()}</button>)}
        </div>
        <div className="sp-table-wrap"><table className="sp-table bc-table sv-groups" aria-label={`Stock value by ${group}`}>
          <thead><tr><th scope="col">{GROUPS.find((each) => each.key === group)?.label}</th><th scope="col" className="bc-num">Items</th>
            <th scope="col" className="bc-num">Quantity</th><th scope="col" className="bc-num">Value (QAR)</th><th scope="col">Share of the total</th></tr></thead>
          <tbody>{report.groups[group].map((row) => <tr key={row.name} className={chosen === row.name ? "sv-chosen" : undefined}>
            <th scope="row"><button type="button" className="sv-link" aria-pressed={chosen === row.name} onClick={() => setChosen(chosen === row.name ? "" : row.name)}>{show(row.name)}</button>
              {row.in_transit && <small>In transit or missing, not on a shelf</small>}</th>
            <td className="pw-mono bc-num">{row.item_count}</td>
            <td className="pw-mono bc-num">{qty(row.quantity)}</td>
            <td className="pw-mono bc-num">{money(row.value)}</td>
            <td><span className="sv-share"><span className="sv-bar" style={{ width: `${Math.min(Number(row.share), 100)}%` }} /></span><span className="sv-pct">{row.share}%</span></td>
          </tr>)}</tbody>
          <tfoot><tr><th scope="row">Total</th><td className="pw-mono bc-num">{report.total.item_count}</td><td className="pw-mono bc-num">{qty(report.total.quantity)}</td>
            <td className="pw-mono bc-num">{money(report.total.value)}</td><td /></tr></tfoot>
        </table></div>
        <h3 className="sv-lines-title">{chosen ? `Items in ${show(chosen)}` : "All items"} <small>{lines.length} {lines.length === 1 ? "line" : "lines"}, most valuable first{chosen && <> · <button type="button" className="sv-link" onClick={() => setChosen("")}>Show all</button></>}</small></h3>
        <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label="Stock value by item">
          <thead><tr><th scope="col">Item</th><th scope="col">Brand · category</th><th scope="col">Location</th><th scope="col" className="bc-num">Quantity</th>
            <th scope="col" className="bc-num">Average cost</th><th scope="col" className="bc-num">Value (QAR)</th></tr></thead>
          <tbody>{lines.slice(0, SHOWN).map((line) => <tr key={`${line.variant_id}-${line.location_id}`}>
            <th scope="row">{line.sku}<small>{show(line.description)} · {show(line.variant_description)}</small></th>
            <td>{show(line.brand)}<small>{show(line.category)}</small></td>
            <td className="sv-where">{show(line.location)}{line.in_transit && <small>In transit or missing</small>}</td>
            <td className="pw-mono bc-num">{qty(line.quantity)}</td>
            <td className="pw-mono bc-num">{line.average_cost ? money(line.average_cost) : ""}</td>
            <td className="pw-mono bc-num">{money(line.value)}</td>
          </tr>)}</tbody>
        </table></div>
        {lines.length > SHOWN && <p className="sp-hint">Showing the {SHOWN} most valuable lines. Download Excel for all {lines.length}.</p>}
        {report.line_count > report.lines.length && <p className="sp-hint">The screen holds the {report.lines.length} most valuable of {report.line_count} lines; the totals above include them all.</p>}
      </>}
      <p className="sp-hint">{report.policy_label}</p>
    </>}
  </section>;
}
