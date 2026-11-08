import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Account = { id: number; name: string; kind: "bank" | "cash"; opening_date: string };
type Asset = {
  id: number; number: string; name: string; category: string; category_label: string; supplier: string; purchase_date: string;
  cost: string; residual_value: string; life_months: number; monthly: string; depreciated: string; book_value: string;
  fully_depreciated: boolean; paid_from: { id: number; name: string } | null; source: Source; source_label: string;
  from_stock: null | { sku: string; location: string; quantity: string }; status: "submitted" | "in_use" | "rejected" | "disposed";
  status_label: string; submitted_by: string; decided_by: string; rejection_reason: string; journal_entry_id: number | null;
  disposal: null | { date: string; proceeds: string; account: string; reason: string; by: string; journal_entry_id: number };
  can_decide?: boolean; can_dispose?: boolean; schedule?: { month: string; amount: string; posted: boolean }[];
};
type Source = "purchase" | "construction" | "stock";
type StockOption = { variant_id: number; location_id: number; sku: string; location: string; free: string; unit_value: string };
type Unbilled = { receipt_id: number; supplier: string; received_on: string };
type Overview = {
  unit: { id: number; code: string; name: string }; rows: Asset[];
  totals: { count: number; cost: string; depreciated: string; book_value: string; monthly: string; waiting: number };
  last_run: null | { month: string; total: string; by: string; journal_entry_id: number | null };
  next_run: null | { month: string; total: string; assets: number; unbilled: Unbilled[] };
  runs: { month: string; total: string; by: string; journal_entry_id: number | null }[];
  categories: { value: string; label: string; usual_life_months: number }[]; accounts?: Account[];
  under_construction?: string; stock_options?: StockOption[]; sources?: { value: Source; label: string }[];
  can_manage: boolean; can_approve: boolean; policy_label: string;
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
const fmt = (value: string) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const show = (name: string) => name.replace(/TEST ONLY\s*/g, "");
const monthName = (month: string) => new Date(`${month}-01T00:00:00`).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
const life = (months: number) => months % 12 === 0 ? `${months / 12} year${months === 12 ? "" : "s"}` : `${months} months`;
const STATUS_CLASS: Record<Asset["status"], string> = { submitted: "ex-waiting", in_use: "ex-booked", rejected: "ex-rejected", disposed: "fa-gone" };
const json = (csrfToken: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken });

function AssetForm({ overview, csrfToken, onDone, onCancel }: { overview: Overview; csrfToken: string; onDone: (message: string) => void; onCancel: () => void }) {
  const accounts = overview.accounts ?? [];
  const bank = accounts.find((account) => account.kind === "bank") ?? accounts[0];
  const [form, setForm] = useState({
    name: "", category: overview.categories[0]?.value ?? "", supplier: "", purchase_date: today(), cost: "",
    residual_value: "", life_months: "", account_id: String(bank?.id ?? ""), source: "purchase" as Source, stock: "", quantity: "1",
  });
  const stockOptions = overview.stock_options ?? [];
  const picked = stockOptions.find((option) => `${option.variant_id}-${option.location_id}` === form.stock);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (field: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [field]: event.target.value });
  const usual = overview.categories.find((category) => category.value === form.category)?.usual_life_months ?? 60;
  const months = Number(form.life_months) || usual;
  const cost = form.source === "stock" ? (picked ? Number(picked.unit_value) * (Number(form.quantity) || 0) : 0) : Number(form.cost);
  const monthly = (cost - (Number(form.residual_value) || 0)) / months;
  const ready = form.source === "purchase" ? accounts.length > 0 : form.source === "stock" ? Boolean(picked) : Number(overview.under_construction ?? 0) > 0;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const asset = await call<Asset>(`/api/finance/fixed-assets/?unit=${overview.unit.id}`, {
        method: "POST", headers: json(csrfToken), body: JSON.stringify({
          name: form.name, category: form.category, supplier: form.supplier, purchase_date: form.purchase_date, source: form.source,
          residual_value: form.residual_value, life_months: form.life_months,
          ...(form.source === "purchase" ? { cost: form.cost, account_id: Number(form.account_id) } : {}),
          ...(form.source === "construction" ? { cost: form.cost } : {}),
          ...(form.source === "stock" ? { variant_id: picked?.variant_id, location_id: picked?.location_id, quantity: form.quantity } : {}),
        }),
      });
      onDone(`${asset.number} registered. It is booked once a second person approves it.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The asset could not be registered.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form" onSubmit={submit} aria-label="Register a fixed asset">
    <h3>Register a fixed asset</h3>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-fields">
      <label>How it was added<select value={form.source} onChange={set("source")}>
        {(overview.sources ?? [{ value: "purchase" as Source, label: "Bought and paid from bank or cash" }]).map((source) => <option key={source.value} value={source.value}>{source.label}</option>)}
      </select></label>
    </div>
    {form.source === "purchase" && accounts.length === 0 && <p className="sp-hint">Add a bank account or cash box in Bank and cash first; the purchase is paid from one.</p>}
    {form.source === "construction" && <p className="sp-hint fa-source-hint">QAR {fmt(overview.under_construction ?? "0")} is waiting on assets under construction. Building costs such as a showroom fit-out are moved there with journal entries while the work goes on; once everything is in, register the finished asset here for up to that amount.</p>}
    {form.source === "stock" && <p className="sp-hint fa-source-hint">{stockOptions.length ? "Stock the company keeps for its own use leaves the warehouse at its value in the books and becomes an asset." : "No free stock to take."}</p>}
    <div className="sp-fields">
      <label>What it is<input value={form.name} onChange={set("name")} maxLength={120} placeholder="e.g. Delivery van, Toyota Hiace" required /></label>
      <label>Category<select value={form.category} onChange={set("category")}>
        {overview.categories.map((category) => <option key={category.value} value={category.value}>{category.label}</option>)}
      </select></label>
      <label>Supplier (optional)<input value={form.supplier} onChange={set("supplier")} maxLength={120} /></label>
      <label>{form.source === "purchase" ? "Bought on" : "In use from"}<input type="date" value={form.purchase_date} max={today()} onChange={set("purchase_date")} required /></label>
      {form.source !== "stock" && <label>Cost (QAR)<input inputMode="decimal" value={form.cost} onChange={set("cost")} required /></label>}
      {form.source === "purchase" && <label>Paid from<select value={form.account_id} onChange={set("account_id")}>
        {accounts.map((account) => <option key={account.id} value={account.id}>{show(account.name)}</option>)}
      </select></label>}
      {form.source === "stock" && <>
        <label>Item and location<select value={form.stock} onChange={set("stock")} required>
          <option value="">Choose…</option>
          {stockOptions.map((option) => <option key={`${option.variant_id}-${option.location_id}`} value={`${option.variant_id}-${option.location_id}`}>{option.sku} at {option.location} · {option.free} free · QAR {fmt(option.unit_value)} each</option>)}
        </select></label>
        <label>Quantity<input inputMode="decimal" value={form.quantity} onChange={set("quantity")} required /></label>
      </>}
      <label>Useful life (months)<input inputMode="numeric" value={form.life_months} onChange={set("life_months")} placeholder={`${usual} (usual for this category)`} /></label>
      <label>Value at the end (QAR, optional)<input inputMode="decimal" value={form.residual_value} onChange={set("residual_value")} placeholder="0" /></label>
    </div>
    <p className="sp-hint">{monthly > 0 ? `Depreciation of about QAR ${fmt(monthly.toFixed(2))} a month for ${life(months)}, starting from the day it is in use. ` : ""}Nothing is booked until a second person approves it.</p>
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy || !ready}>Send for approval</button>
      <button className="button button-outline" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
    </div>
  </form>;
}

function Actions({ asset, overview, csrfToken, onDone }: { asset: Asset; overview: Overview; csrfToken: string; onDone: (message: string) => void }) {
  const [mode, setMode] = useState<"" | "reject" | "dispose">("");
  const [form, setForm] = useState({ reason: "", date: today(), proceeds: "", account_id: String(overview.accounts?.[0]?.id ?? "") });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const send = async (body: object, message: string) => {
    setBusy(true); setError("");
    try {
      await call(`/api/finance/fixed-assets/${asset.id}/?unit=${overview.unit.id}`, { method: "POST", headers: json(csrfToken), body: JSON.stringify(body) });
      setMode("");
      onDone(message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That did not work.");
    } finally { setBusy(false); }
  };
  if (!asset.can_decide && !asset.can_dispose) return null;
  const set = (field: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [field]: event.target.value });
  return <div className="sp-actions ex-actions">
    {error && <div className="alert" role="alert">{error}</div>}
    {asset.can_decide && mode === "" && <>
      <button className="button button-dark ex-small" type="button" disabled={busy} onClick={() => void send({ decision: "approve" }, `${asset.number} approved and booked.`)}>Approve</button>
      <button className="button button-outline ex-small" type="button" onClick={() => setMode("reject")}>Reject</button>
    </>}
    {asset.can_dispose && mode === "" && <button className="button button-outline ex-small" type="button" onClick={() => setMode("dispose")}>Sell or write off</button>}
    {mode === "reject" && <form className="ex-inline" onSubmit={(event) => { event.preventDefault(); void send({ decision: "reject", reason: form.reason }, `${asset.number} rejected.`); }}>
      <input aria-label={`Why ${asset.number} is rejected`} value={form.reason} onChange={set("reason")} maxLength={200} placeholder="Reason" required />
      <button className="button button-dark ex-small" type="submit" disabled={busy}>Reject</button>
      <button className="button button-outline ex-small" type="button" onClick={() => setMode("")}>Cancel</button>
    </form>}
    {mode === "dispose" && <form className="ex-inline fa-dispose" aria-label={`Sell or write off ${asset.number}`} onSubmit={(event) => {
      event.preventDefault();
      void send({ decision: "dispose", date: form.date, proceeds: form.proceeds || "0", account_id: Number(form.account_id), reason: form.reason },
        Number(form.proceeds) > 0 ? `${asset.number} sold.` : `${asset.number} written off.`);
    }}>
      <input type="date" aria-label="Date" value={form.date} max={today()} onChange={set("date")} required />
      <input aria-label="Amount received (QAR)" inputMode="decimal" value={form.proceeds} onChange={set("proceeds")} placeholder="Received (0 = write off)" />
      {Number(form.proceeds) > 0 && <select aria-label="Paid into" value={form.account_id} onChange={set("account_id")}>
        {(overview.accounts ?? []).map((account) => <option key={account.id} value={account.id}>{show(account.name)}</option>)}
      </select>}
      <input aria-label="Reason" value={form.reason} onChange={set("reason")} maxLength={200} placeholder="Reason" required />
      <button className="button button-dark ex-small" type="submit" disabled={busy}>{Number(form.proceeds) > 0 ? "Sell" : "Write off"}</button>
      <button className="button button-outline ex-small" type="button" onClick={() => setMode("")}>Cancel</button>
    </form>}
  </div>;
}

function Schedule({ asset, unitId, onClose }: { asset: Asset; unitId: number; onClose: () => void }) {
  const [detail, setDetail] = useState<Asset | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    call<Asset>(`/api/finance/fixed-assets/${asset.id}/?unit=${unitId}`).then(setDetail).catch((caught) => setError(caught instanceof Error ? caught.message : "Could not load."));
  }, [asset.id, unitId]);
  return <div className="sp-form fa-schedule" aria-label={`Depreciation of ${asset.number}`}>
    <h3>{asset.number} · {show(asset.name)} <small>{life(asset.life_months)} from {monthName(asset.purchase_date.slice(0, 7))}</small></h3>
    {error && <div className="alert" role="alert">{error}</div>}
    {!detail && !error && <p className="sp-empty">Loading…</p>}
    {detail?.disposal && <p className="sp-hint">{detail.disposal.proceeds !== "0.00" ? `Sold for QAR ${fmt(detail.disposal.proceeds)} into ${show(detail.disposal.account)}` : "Written off"} on {detail.disposal.date} by {detail.disposal.by}: {show(detail.disposal.reason)}</p>}
    {detail?.schedule && <ol className="fa-months">
      {detail.schedule.map((month) => <li key={month.month} className={month.posted ? "fa-posted" : ""}><span>{monthName(month.month)}</span><strong>{fmt(month.amount)}</strong>{month.posted ? <small>posted</small> : <small>planned</small>}</li>)}
    </ol>}
    <button className="button button-outline" type="button" onClick={onClose}>Close</button>
  </div>;
}

export default function FixedAssets({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [adding, setAdding] = useState(false);
  const [status, setStatus] = useState("");
  const [open, setOpen] = useState<Asset | null>(null);
  const [running, setRunning] = useState(false);

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    try {
      setOverview(await call<Overview>(`/api/finance/fixed-assets/?unit=${selectedUnitId}${status ? `&status=${status}` : ""}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Fixed assets could not be loaded.");
    }
  }, [selectedUnitId, status]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setAdding(false); setNotice(message); void load(); };
  const runNext = async () => {
    if (!overview?.next_run) return;
    setRunning(true); setError("");
    try {
      setOverview(await call<Overview>(`/api/finance/fixed-assets/depreciation/?unit=${overview.unit.id}`, {
        method: "POST", headers: json(csrfToken), body: JSON.stringify({ month: overview.next_run.month }),
      }));
      setNotice(`Depreciation for ${monthName(overview.next_run.month)} posted.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Depreciation could not be run.");
    } finally { setRunning(false); }
  };

  return <section className="content-card supplier-payments bank-cash fixed-assets" aria-labelledby="fixed-assets-title">
    <header className="sp-head">
      <span className="eyebrow">FINANCE · FIXED ASSETS · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="fixed-assets-title">Fixed assets</h2>
      <p>Things the business keeps and uses for years: showroom furniture, fit-outs, vans, computers. Their cost is spread over their useful life by a monthly depreciation run, starting with the part of the first month they were in use. An asset can be bought, finished after construction, or taken from stock. A second person approves each purchase; selling or writing one off records any gain or loss.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading fixed assets…</p>}
    {overview && <>
      <ul className="bc-accounts ex-tiles" aria-label="Assets in use">
        <li className="bc-total"><span>Cost of assets in use</span><strong>{fmt(overview.totals.cost)}</strong><small>QAR, {overview.totals.count} asset{overview.totals.count === 1 ? "" : "s"}</small></li>
        <li className="bc-total"><span>Depreciated so far</span><strong>{fmt(overview.totals.depreciated)}</strong><small>QAR</small></li>
        <li className="bc-total"><span>Value in the books</span><strong>{fmt(overview.totals.book_value)}</strong><small>QAR, cost less depreciation</small></li>
        <li className="bc-total"><span>Depreciation per month</span><strong>{fmt(overview.totals.monthly)}</strong><small>QAR at the current rate</small></li>
      </ul>
      <div className={`fa-run${overview.next_run ? " fa-run-due" : ""}`}>
        {overview.next_run
          ? <><span><strong>{monthName(overview.next_run.month)}</strong> depreciation is not posted yet: QAR {fmt(overview.next_run.total)} across {overview.next_run.assets} asset{overview.next_run.assets === 1 ? "" : "s"}.</span>
            {overview.next_run.unbilled.length > 0 && <span className="fa-unbilled">First record the supplier bills for goods delivered by the end of the month: {overview.next_run.unbilled.map((row) => `receipt ${row.receipt_id} from ${show(row.supplier)} (${row.received_on})`).join(", ")}.</span>}
            {overview.can_manage && overview.next_run.unbilled.length === 0 && <button className="button button-dark" type="button" disabled={running} onClick={() => void runNext()}>Post {monthName(overview.next_run.month)} depreciation</button>}</>
          : <span>{overview.last_run ? `Depreciation is posted up to ${monthName(overview.last_run.month)}.` : "Nothing to depreciate yet."} Each month is run once it has ended.</span>}
      </div>
      <div className="bc-filters">
        <label>Show<select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All</option><option value="in_use">In use</option><option value="submitted">Waiting for approval</option>
          <option value="disposed">Sold or written off</option><option value="rejected">Rejected</option>
        </select></label>
        {overview.can_manage && !adding && <div className="bc-buttons">
          <button className="button button-dark" type="button" onClick={() => { setNotice(""); setAdding(true); }}>Register an asset</button>
        </div>}
      </div>
      {adding && <AssetForm overview={overview} csrfToken={csrfToken} onDone={done} onCancel={() => setAdding(false)} />}
      {open && <Schedule asset={open} unitId={overview.unit.id} onClose={() => setOpen(null)} />}
      {overview.rows.length === 0
        ? <p className="sp-empty">No fixed assets{status ? " match" : " registered yet"}.</p>
        : <div className="sp-table-wrap"><table className="sp-table bc-table fa-table" aria-label="Fixed assets">
          <thead><tr><th scope="col">Asset</th><th scope="col">Bought</th><th scope="col" className="bc-num">Cost</th><th scope="col" className="bc-num">Depreciated</th><th scope="col" className="bc-num">In the books</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{overview.rows.map((asset) => <tr key={asset.id}>
            <th scope="row"><button className="fa-link" type="button" onClick={() => setOpen(asset)}>{asset.number}</button><small>{show(asset.name)}</small><small>{asset.category_label} · {life(asset.life_months)}</small></th>
            <td>{asset.purchase_date}<small>{[show(asset.supplier), asset.paid_from ? `from ${show(asset.paid_from.name)}` : asset.from_stock ? `${asset.from_stock.quantity} × ${asset.from_stock.sku} from ${asset.from_stock.location}` : asset.source === "construction" ? "finished construction" : ""].filter(Boolean).join(" · ")}</small></td>
            <td className="pw-mono bc-num">{fmt(asset.cost)}<small>{fmt(asset.monthly)} a month</small></td>
            <td className="pw-mono bc-num">{fmt(asset.depreciated)}</td>
            <td className="pw-mono bc-num">{asset.status === "disposed" ? "" : fmt(asset.book_value)}{asset.fully_depreciated && asset.status === "in_use" && <small>fully depreciated</small>}</td>
            <td><span className={`ex-status ${STATUS_CLASS[asset.status]}`}>{asset.status_label}</span>
              <small>{asset.status === "rejected" ? `${asset.decided_by}: ${asset.rejection_reason}` : asset.status === "disposed" && asset.disposal ? `${asset.disposal.date} · ${asset.disposal.proceeds !== "0.00" ? `sold for ${fmt(asset.disposal.proceeds)}` : "written off"}` : asset.status === "submitted" ? `registered by ${asset.submitted_by}${!asset.can_decide && overview.can_approve ? " · needs someone else" : ""}` : `approved by ${asset.decided_by}`}</small></td>
            <td><Actions asset={asset} overview={overview} csrfToken={csrfToken} onDone={done} /></td>
          </tr>)}</tbody>
        </table></div>}
      {overview.runs.length > 0 && <><h3>Depreciation posted</h3><ul className="bg-history">
        {overview.runs.map((run) => <li key={run.month}>{monthName(run.month)}: QAR {fmt(run.total)} <span className="sp-muted">by {run.by}{run.journal_entry_id ? ` · journal #${run.journal_entry_id}` : ""}</span></li>)}
      </ul></>}
      <p className="sp-hint">{overview.policy_label}</p>
    </>}
  </section>;
}
