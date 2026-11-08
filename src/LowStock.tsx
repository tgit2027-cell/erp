import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Row = {
  id: number; variant_id: number; sku: string; item_code: string; description: string; variant_description: string;
  location_id: number | null; location: string; minimum: string; reorder_quantity: string; on_hand: string; free: string;
  short_by: string; on_order: string; covered_by_order: boolean; suggested_order: string;
  requested: string; request_id: number | null; requested_at: string;
  status: "out" | "low" | "ok"; status_label: string; note: string; updated_by: string; updated_at: string;
};
type Overview = {
  unit: { id: number; code: string; name: string }; can_manage: boolean; rows: Row[];
  counts: { out: number; low: number; ok: number }; covered: number; policy_label: string;
  locations?: { id: number; name: string }[]; variants?: { id: number; sku: string; description: string }[];
  can_buy: boolean; requests: Request[];
};
type Request = {
  id: number; status: "sent" | "ordered" | "declined" | "cancelled"; status_label: string; sku: string; item_code: string;
  description: string; variant_description: string; quantity: string; location: string; supplier: string; supplier_id: number | null;
  supplier_item_code: string; note: string; requested_by: string; requested_at: string; decided_by: string; decided_at: string;
  reason: string; order_id: number | null; matching_order: { id: number; quantity: string; date: string } | null;
};
type Form = { variant_id: string; location_id: string; minimum_quantity: string; reorder_quantity: string; note: string };

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

const qty = (value: string) => Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 });
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const day = (value: string) => value ? new Date(value).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";
const empty: Form = { variant_id: "", location_id: "", minimum_quantity: "", reorder_quantity: "", note: "" };

/** A line on the overview when something is low; shows nothing otherwise or without stock access. */
export function LowStockAlert({ selectedUnitId, onOpen }: { selectedUnitId: number; onOpen: () => void }) {
  const [alerts, setAlerts] = useState(0);
  useEffect(() => {
    let live = true;
    call<{ alerts: number }>(`/api/inventory/low-stock/?unit=${selectedUnitId}&summary=1`)
      .then((data) => { if (live) setAlerts(data.alerts); })
      .catch(() => undefined);
    return () => { live = false; };
  }, [selectedUnitId]);
  if (!alerts) return null;
  return <div className="alert low-stock-alert" role="status">
    <span>{alerts} {alerts === 1 ? "item is" : "items are"} low or out of stock.</span>
    <button className="button button-outline" type="button" onClick={onOpen}>See low stock</button>
  </div>;
}

function LevelForm({ overview, initial, csrfToken, onDone, onCancel }: {
  overview: Overview; initial: Form; csrfToken: string; onDone: (message: string) => void; onCancel: () => void;
}) {
  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const editing = initial.variant_id !== "";
  const set = (field: keyof Form) => (event: { target: { value: string } }) => setForm({ ...form, [field]: event.target.value });
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const row = await call<Row>(`/api/inventory/low-stock/?unit=${overview.unit.id}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ ...form, variant_id: Number(form.variant_id), location_id: form.location_id ? Number(form.location_id) : null }),
      });
      onDone(`Level saved for ${row.sku} at ${row.location}: ${row.status_label.toLowerCase()} (${qty(row.free)} free, minimum ${qty(row.minimum)}).`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The level could not be saved.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form" onSubmit={submit} aria-label={editing ? "Change a low-stock level" : "Set a low-stock level"}>
    <h3>{editing ? "Change the level" : "Set a low-stock level"}</h3>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-fields">
      <label>Item<select value={form.variant_id} onChange={set("variant_id")} disabled={editing} required>
        <option value="">Choose…</option>
        {(overview.variants ?? []).map((variant) => <option key={variant.id} value={variant.id}>{variant.sku} · {show(variant.description)}</option>)}
      </select></label>
      <label>Where<select value={form.location_id} onChange={set("location_id")} disabled={editing}>
        <option value="">All locations together</option>
        {(overview.locations ?? []).map((location) => <option key={location.id} value={location.id}>{show(location.name)}</option>)}
      </select></label>
      <label>Alert at or below<input inputMode="decimal" value={form.minimum_quantity} onChange={set("minimum_quantity")} required /></label>
      <label>Usual order quantity (optional)<input inputMode="decimal" value={form.reorder_quantity} onChange={set("reorder_quantity")} /></label>
      <label>Note (optional)<input value={form.note} onChange={set("note")} maxLength={160} placeholder="e.g. 10 weeks from the factory" /></label>
    </div>
    <p className="sp-hint">The alert compares this with stock free to sell: on hand, less what is reserved for orders or held on open quotes.</p>
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy}>Save level</button>
      <button className="button button-outline" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
    </div>
  </form>;
}

function Requests({ overview, busy, onAct }: {
  overview: Overview; busy: boolean; onAct: (request: Request, action: "ordered" | "declined" | "cancel", reason?: string) => void;
}) {
  const [declining, setDeclining] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const waiting = overview.requests.filter((request) => request.status === "sent");
  const done = overview.requests.filter((request) => request.status !== "sent");
  const groups = new Map<string, Request[]>();
  for (const request of waiting) groups.set(request.supplier || "No supplier set on the item", [...(groups.get(request.supplier || "No supplier set on the item") ?? []), request]);
  return <section className="ls-requests" aria-labelledby="ls-requests-title">
    <h3 id="ls-requests-title">Sent to purchasing <small>{waiting.length} waiting</small></h3>
    {waiting.length === 0 ? <p className="sp-empty">Nothing is waiting for purchasing.</p> : [...groups].map(([supplier, items]) => <div key={supplier} className="ls-supplier">
      <h4>{show(supplier)}</h4>
      <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label={`Waiting for purchasing: ${show(supplier)}`}>
        <thead><tr><th scope="col">Item</th><th scope="col" className="bc-num">Quantity</th><th scope="col">Sent</th><th scope="col">Purchase order</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>{items.map((request) => <tr key={request.id}>
          <th scope="row">{request.sku}<small>{show(request.description)} · {show(request.variant_description)}</small>
            {request.supplier_item_code && <small>Supplier's code {request.supplier_item_code}</small>}</th>
          <td className="pw-mono bc-num">{qty(request.quantity)}</td>
          <td>{day(request.requested_at)}<small>{request.requested_by}{request.note ? ` · ${request.note}` : ""}</small></td>
          <td>{request.matching_order ? <>Order {request.matching_order.id}<small>{qty(request.matching_order.quantity)} ordered on {day(request.matching_order.date)}</small></> : <span className="sp-muted">None entered yet</span>}</td>
          <td>{declining === request.id ? <div className="sp-actions ls-decline">
            <input aria-label={`Why ${request.sku} is not being ordered`} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Why not, e.g. discontinued" />
            <button className="button button-dark ls-small" type="button" disabled={busy || reason.trim().length < 5} onClick={() => { onAct(request, "declined", reason); setDeclining(null); setReason(""); }}>Confirm</button>
            <button className="button button-outline ls-small" type="button" onClick={() => setDeclining(null)}>Cancel</button>
          </div> : <div className="sp-actions">
            {overview.can_buy && <button className="button button-dark ls-small" type="button" disabled={busy} onClick={() => onAct(request, "ordered")}>Ordered</button>}
            {overview.can_buy && <button className="button button-outline ls-small" type="button" disabled={busy} onClick={() => { setDeclining(request.id); setReason(""); }}>Not ordering</button>}
            {overview.can_manage && <button className="button button-outline ls-small" type="button" disabled={busy} onClick={() => onAct(request, "cancel")}>Withdraw</button>}
          </div>}</td>
        </tr>)}</tbody>
      </table></div>
    </div>)}
    {done.length > 0 && <details className="ls-history"><summary>Earlier requests ({done.length})</summary>
      <ul>{done.map((request) => <li key={request.id}><strong>{request.sku}</strong> · {qty(request.quantity)} · {request.status_label}
        {request.order_id ? ` on order ${request.order_id}` : ""} · {request.decided_by}, {day(request.decided_at)}{request.reason ? ` · ${request.reason}` : ""}</li>)}</ul>
    </details>}
  </section>;
}

export default function LowStock({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState<Form | null>(null);
  const [all, setAll] = useState(false);
  const [picked, setPicked] = useState<Record<number, string>>({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    try {
      setOverview(await call<Overview>(`/api/inventory/low-stock/?unit=${selectedUnitId}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Low stock could not be loaded.");
    }
  }, [selectedUnitId]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setForm(null); setNotice(message); void load(); };
  const remove = async (row: Row) => {
    if (!overview) return;
    setError(""); setNotice("");
    try {
      await call(`/api/inventory/low-stock/${row.id}/?unit=${overview.unit.id}`, { method: "DELETE", headers: { "X-CSRFToken": csrfToken } });
      done(`Level removed for ${row.sku} at ${row.location}.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The level could not be removed.");
    }
  };
  const post = async (path: string, body: object, message: (data: Overview & { sent?: number }) => string) => {
    if (!overview) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const data = await call<Overview & { sent?: number }>(`/api/inventory/${path}?unit=${overview.unit.id}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify(body),
      });
      setOverview(data);
      setNotice(message(data));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That could not be saved.");
    } finally { setBusy(false); }
  };
  const send = () => {
    const lines = Object.entries(picked).map(([id, quantity]) => ({ level_id: Number(id), quantity }));
    void post("reorder-requests/", { lines, note }, (data) => `Sent ${data.sent} ${data.sent === 1 ? "item" : "items"} to purchasing.`)
      .then(() => { setPicked({}); setNote(""); });
  };
  const act = (request: Request, action: "ordered" | "declined" | "cancel", reason = "") => void post(`reorder-requests/${request.id}/`, { action, reason },
    () => action === "ordered" ? `${request.sku} marked as ordered.` : action === "declined" ? `${request.sku}: not being ordered.` : `${request.sku} withdrawn from purchasing.`);
  const togglePick = (row: Row) => setPicked((current) => {
    const next = { ...current };
    if (next[row.id] !== undefined) delete next[row.id]; else next[row.id] = row.suggested_order;
    return next;
  });
  const pickedCount = Object.keys(picked).length;
  const rows = overview ? overview.rows.filter((row) => all || row.status !== "ok") : [];
  const attention = overview ? overview.counts.out + overview.counts.low : 0;

  return <section className="content-card supplier-payments low-stock" aria-labelledby="low-stock-title">
    <header className="sp-head">
      <span className="eyebrow">INVENTORY · LOW STOCK · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="low-stock-title">Low stock</h2>
      <p>Items that have fallen to or below the level you set, at one location or across all of them, with what is already on order from suppliers and a suggested order. Tick the suggestions you want and send them to purchasing, who order them or say why not. The list updates on its own as stock moves.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading low stock…</p>}
    {overview && <>
      <dl className="sp-totals">
        <div><dt>Out of stock</dt><dd className={overview.counts.out ? "ls-out" : undefined}>{overview.counts.out}</dd></div>
        <div><dt>Low</dt><dd className={overview.counts.low ? "ls-low" : undefined}>{overview.counts.low}</dd></div>
        <div><dt>Already covered by an order</dt><dd>{overview.covered}</dd></div>
        <div><dt>Levels set</dt><dd>{overview.rows.length}</dd></div>
      </dl>
      <div className="bc-filters">
        <label className="bc-check"><input type="checkbox" checked={all} onChange={(event) => setAll(event.target.checked)} /> Show items with enough stock too</label>
        {overview.can_manage && !form && <div className="bc-buttons">
          <button className="button button-dark" type="button" onClick={() => { setNotice(""); setForm(empty); }}>Set a level</button>
        </div>}
      </div>
      {form && <LevelForm overview={overview} initial={form} csrfToken={csrfToken} onDone={done} onCancel={() => setForm(null)} />}
      {overview.rows.length === 0
        ? <p className="sp-empty">No low-stock levels are set for {overview.unit.name} yet.{overview.can_manage ? " Set one to start getting alerts." : ""}</p>
        : rows.length === 0
          ? <p className="sp-empty">Nothing is low. {attention === 0 && "Every item is above its level."}</p>
          : <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label="Low-stock levels">
            <thead><tr>{overview.can_manage && <th scope="col"><span className="sr-only">Send</span></th>}<th scope="col">Item</th><th scope="col">Where</th><th scope="col">Status</th><th scope="col" className="bc-num">Free</th><th scope="col" className="bc-num">Alert at</th><th scope="col" className="bc-num">On order</th><th scope="col" className="bc-num">Suggested order</th>{overview.can_manage && <th scope="col"><span className="sr-only">Change</span></th>}</tr></thead>
            <tbody>{rows.map((row) => <tr key={row.id}>
              {overview.can_manage && <td>{Number(row.suggested_order) > 0 && <input type="checkbox" aria-label={`Send ${row.sku} to purchasing`}
                checked={picked[row.id] !== undefined} onChange={() => togglePick(row)} />}</td>}
              <th scope="row">{row.sku}<small>{show(row.description)} · {show(row.variant_description)}</small></th>
              <td>{show(row.location)}{row.note && <small>{row.note}</small>}</td>
              <td><span className={row.status === "ok" ? "sp-gain" : row.status === "out" ? "sp-loss" : "ls-low-tag"}>{row.status_label}</span>
                <small>{row.status !== "ok" && (row.covered_by_order ? "An order already covers it · " : `Short by ${qty(row.short_by)} · `)}{qty(row.on_hand)} on hand</small>
                {Number(row.requested) > 0 && <small className="ls-requested">{qty(row.requested)} sent to purchasing on {day(row.requested_at)}</small>}</td>
              <td className="pw-mono bc-num">{qty(row.free)}</td>
              <td className="pw-mono bc-num">{qty(row.minimum)}</td>
              <td className="pw-mono bc-num">{Number(row.on_order) ? qty(row.on_order) : ""}</td>
              <td className="pw-mono bc-num">{picked[row.id] !== undefined
                ? <input className="ls-qty" aria-label={`Quantity of ${row.sku} to order`} inputMode="decimal" value={picked[row.id]} onChange={(event) => setPicked({ ...picked, [row.id]: event.target.value })} />
                : Number(row.suggested_order) ? qty(row.suggested_order) : ""}</td>
              {overview.can_manage && <td><div className="sp-actions">
                <button className="button button-outline ls-small" type="button" onClick={() => { setNotice(""); setForm({ variant_id: String(row.variant_id), location_id: row.location_id ? String(row.location_id) : "", minimum_quantity: row.minimum, reorder_quantity: row.reorder_quantity, note: row.note }); }}>Change</button>
                <button className="button button-outline ls-small" type="button" onClick={() => void remove(row)}>Remove</button>
              </div></td>}
            </tr>)}</tbody>
          </table></div>}
      {pickedCount > 0 && <div className="ls-send" role="group" aria-label="Send to purchasing">
        <label>Note for purchasing (optional)<input value={note} onChange={(event) => setNote(event.target.value)} maxLength={200} placeholder="e.g. needed for the showroom by month end" /></label>
        <button className="button button-dark" type="button" disabled={busy} onClick={send}>Send {pickedCount} {pickedCount === 1 ? "item" : "items"} to purchasing</button>
      </div>}
      {(overview.requests.length > 0 || overview.can_buy) && <Requests overview={overview} busy={busy} onAct={act} />}
      <p className="sp-hint">{overview.policy_label}</p>
    </>}
  </section>;
}
