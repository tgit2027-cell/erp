import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import "./labels.css";
import "./stock-counts.css";

type Person = { id: number; username: string } | null;
type Place = { id: number; code: string; name: string };
type CountSummary = {
  id: number; location: Place; status: string; status_label: string; note: string;
  opened_by: Person; opened_at: string; submitted_at: string | null; decided_by: Person; decided_at: string | null;
  line_count: number; counted_lines: number; open_differences: number; waiting_for_finance: number;
};
type Overview = {
  unit: { id: number; code: string; name: string };
  permissions: { can_count: boolean; can_approve: boolean; can_approve_write_off: boolean };
  locations: (Place & { has_open_count: boolean })[];
  counts: CountSummary[];
};
type Line = {
  id: number; sku: string; item: string; brand: string; counted_quantity: string | null; counted_by: string | null;
  found_during_count: boolean; expected_quantity?: string; erp_quantity?: string; difference?: string | null;
  moved_to_missing?: string; surplus_quantity?: string; surplus_value?: string;
  written_off_value?: string; action?: string; action_label?: string; action_status?: string; action_status_label?: string;
  action_note?: string; action_requested_by?: Person; action_decided_by?: Person; action_decision_note?: string;
  allowed_actions?: string[]; can_decide_action?: boolean;
};
type Detail = CountSummary & {
  decision_note: string; blind: boolean; lines: Line[]; can_count: boolean; can_decide: boolean; is_involved: boolean;
  can_approve_write_off: boolean;
  history: { event: string; actor: string; note: string; at: string }[];
};

const EVENTS: Record<string, string> = {
  started: "Started", submitted: "Sent for review", returned: "Returned for recount", cancelled: "Cancelled",
  confirmed: "Differences confirmed", closed: "Closed", found: "Found and moved back", action_requested: "Action requested",
  action_rejected: "Action rejected by Finance", written_off: "Written off", accepted: "Surplus accepted",
};
const ACTION_BUTTONS: Record<string, string> = { write_off: "Write off", found: "Found, move back", accept: "Accept into stock" };

function newKey() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `stock-count-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const when = (value: string | null) => value ? new Date(value).toLocaleString() : "—";
const qty = (value: string | null | undefined) => value == null ? "—" : Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 });
const qar = (value: string) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function Difference({ value }: { value: string | null | undefined }) {
  if (value == null) return <span className="sc-muted">—</span>;
  const number = Number(value);
  if (number < 0) return <span className="sc-short">{qty(value)} short</span>;
  if (number > 0) return <span className="sc-over">+{qty(value)} over</span>;
  return <span className="sc-even">Matches</span>;
}

export default function StockCounts({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const unit = selectedUnitId ? `?unit=${selectedUnitId}` : "";
  const [overview, setOverview] = useState<Overview | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [entries, setEntries] = useState<Record<number, string>>({});
  const [found, setFound] = useState({ sku: "", counted: "" });
  const [scan, setScan] = useState("");
  const [scanNote, setScanNote] = useState("");
  const [lastScanned, setLastScanned] = useState<number | null>(null);
  const scanInput = useRef<HTMLInputElement>(null);
  const [startForm, setStartForm] = useState({ location_id: "", note: "" });
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [approvalKey, setApprovalKey] = useState(newKey);
  const [lineNotes, setLineNotes] = useState<Record<number, string>>({});

  const call = useCallback(async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
    const response = await fetch(path, {
      ...init, credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(init.method ? { "X-CSRFToken": csrfToken } : {}), ...init.headers },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : Object.values(data).flat().join(" ") || "The stock count could not be updated.");
    return data as T;
  }, [csrfToken]);

  const loadOverview = useCallback(() => {
    call<Overview>(`/api/inventory/stock-counts/${unit}`).then((data) => { setOverview(data); setError(""); }).catch((caught: Error) => setError(caught.message));
  }, [call, unit]);
  useEffect(loadOverview, [loadOverview]);

  const show = (data: Detail) => {
    setDetail(data);
    setEntries(Object.fromEntries(data.lines.map((line) => [line.id, line.counted_quantity ?? ""])));
  };
  const open = (id: number) => {
    setNotice(""); setReason(""); setLineNotes({});
    call<Detail>(`/api/inventory/stock-counts/${id}/${unit}`).then(show).catch((caught: Error) => setError(caught.message));
  };
  const act = async (step: string, body: object, message: string, headers: Record<string, string> = {}) => {
    if (!detail) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await call<Detail | { count_id: number }>(`/api/inventory/stock-counts/${detail.id}/${step}/${unit}`, { method: "POST", body: JSON.stringify(body), headers });
      if ("lines" in result) show(result); else open(detail.id);
      setNotice(message); setReason(""); loadOverview();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const start = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const created = await call<Detail>(`/api/inventory/stock-counts/${unit}`, { method: "POST", body: JSON.stringify({ location_id: Number(startForm.location_id), note: startForm.note }) });
      show(created); setStartForm({ location_id: "", note: "" }); setNotice(`Count started at ${created.location.code}.`); loadOverview();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const saveCounts = () => {
    if (!detail) return;
    const changed = detail.lines.filter((line) => (entries[line.id] ?? "") !== "" && entries[line.id] !== line.counted_quantity)
      .map((line) => ({ line_id: line.id, counted: entries[line.id] }));
    if (!changed.length) { setNotice("Nothing new to save."); return; }
    void act("counts", { entries: changed }, `Saved ${changed.length} count${changed.length === 1 ? "" : "s"}.`);
  };
  const scanned = async (event: FormEvent) => {
    event.preventDefault();
    const code = scan.trim();
    setScan("");
    scanInput.current?.focus();
    if (!code || !detail) return;
    let skus: string[] = [];
    try {
      const response = await fetch(`/api/inventory/scan/?code=${encodeURIComponent(code)}${selectedUnitId ? `&unit=${selectedUnitId}` : ""}`, { credentials: "same-origin" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The code could not be looked up.");
      skus = (data.rows as { sku: string }[]).map((row) => row.sku);
    } catch (caught) {
      setScanNote((caught as Error).message);
      return;
    }
    const lines = detail.lines.filter((line) => skus.some((sku) => sku.toUpperCase() === line.sku.toUpperCase()));
    if (skus.length > 1) {
      setScanNote(`${code} matches ${skus.length} finishes. Scan the Tivoli label on the item instead.`);
    } else if (lines.length === 1) {
      const line = lines[0];
      const next = String((Number(entries[line.id] || 0) || 0) + 1);
      setEntries((current) => ({ ...current, [line.id]: next }));
      setLastScanned(line.id);
      setScanNote(`${line.sku}: ${next} counted. Save counts when you finish.`);
    } else {
      const same = found.sku.toUpperCase() === skus[0].toUpperCase();
      const next = String((same ? Number(found.counted || 0) || 0 : 0) + 1);
      setFound({ sku: skus[0], counted: next });
      setLastScanned(null);
      setScanNote(`${skus[0]} is not on this count's list: ${next} so far. Press Add item when you have scanned them all.`);
    }
  };
  const addFound = (event: FormEvent) => {
    event.preventDefault();
    void act("counts", { entries: [{ sku: found.sku, counted: found.counted }] }, `Added ${found.sku.toUpperCase()}.`).then(() => setFound({ sku: "", counted: "" }));
  };
  const approve = () => act("approve", { note: reason }, "Differences confirmed. Each one now needs an action before the count closes.", { "Idempotency-Key": approvalKey }).then(() => setApprovalKey(newKey()));
  const lineStep = (line: Line, step: "action" | "decision", body: object, message: string) =>
    act(`lines/${line.id}/${step}`, { ...body, note: lineNotes[line.id] ?? "" }, message, { "Idempotency-Key": newKey() })
      .then(() => setLineNotes({ ...lineNotes, [line.id]: "" }));

  if (!overview && !error) return <section className="stock-counts"><p className="sc-muted" role="status">Loading stock counts…</p></section>;
  const freeLocations = overview?.locations.filter((location) => !location.has_open_count) ?? [];
  const approved = detail ? ["differences", "closed"].includes(detail.status) : false;
  return <section className="stock-counts" aria-labelledby="stock-counts-title">
    <div className="sc-head">
      <span className="eyebrow">INVENTORY · TEST ONLY SYNTHETIC STOCK</span>
      <h2 id="stock-counts-title">Stock counts{overview ? ` · ${overview.unit.name}` : ""}</h2>
      <p>Count one location at a time while deliveries carry on. Counters do not see the ERP quantity. The warehouse manager then checks the live differences, enters any manual deliveries made during the count, and confirms. Shortages move to <strong>XX</strong> and surpluses are held on the count. The count closes once every difference is written off, found, or accepted into stock; write-offs and accepted surpluses need Finance approval.</p>
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sc-notice" role="status">{notice}</p>}

    {detail ? <section className="sc-detail" aria-labelledby="sc-detail-title">
      <button className="sc-back" type="button" onClick={() => { setDetail(null); setNotice(""); }}>← All counts</button>
      <h3 id="sc-detail-title">Count #{detail.id} · {detail.location.code} <small>{detail.location.name}</small></h3>
      <p className="sc-meta"><span className={`sc-status sc-status-${detail.status}`}>{detail.status_label}</span> Started by {detail.opened_by?.username} on {when(detail.opened_at)}. {detail.counted_lines} of {detail.line_count} lines counted.{detail.decision_note ? ` Note: ${detail.decision_note}` : ""}</p>
      {detail.can_count && <form className="scan-box sc-scan" onSubmit={scanned} aria-label="Scan to count">
        <label>Scan each piece<input ref={scanInput} value={scan} onChange={(event) => setScan(event.target.value)} placeholder="Scan a label, or type a SKU and press Enter" autoComplete="off" /></label>
        <button className="button button-outline" type="submit">Count one</button>
        {scanNote && <span className="sc-scan-note" role="status">{scanNote}</span>}
      </form>}
      <div className="sc-table-wrap"><table className="sc-table">
        <thead><tr>
          <th scope="col">Item</th>
          {!detail.blind && <th scope="col" className="sc-num">{approved ? "ERP at confirmation" : "In the ERP now"}</th>}
          <th scope="col" className="sc-num">Counted</th>
          {!detail.blind && <th scope="col">Difference</th>}
          {approved && <th scope="col">Where the difference is</th>}
          {approved && <th scope="col">Action</th>}
        </tr></thead>
        <tbody>{detail.lines.map((line) => <tr key={line.id} className={lastScanned === line.id ? "sc-just-scanned" : undefined}>
          <th scope="row">{line.sku}<small>{line.brand} · {line.item}{line.found_during_count ? " · found during the count" : ""}</small></th>
          {!detail.blind && <td className="sc-num">{qty(line.erp_quantity)}</td>}
          <td className="sc-num">{detail.can_count
            ? <input aria-label={`Counted quantity for ${line.sku}`} inputMode="decimal" value={entries[line.id] ?? ""} onChange={(event) => setEntries({ ...entries, [line.id]: event.target.value })} />
            : qty(line.counted_quantity)}</td>
          {!detail.blind && <td><Difference value={line.difference} /></td>}
          {approved && <td className="sc-posted">{[
            Number(line.moved_to_missing) ? `${qty(line.moved_to_missing)} moved to XX` : "",
            Number(line.surplus_quantity) ? (line.action_status === "done"
              ? `${qty(line.surplus_quantity)} added (QAR ${qar(line.surplus_value ?? "0")})`
              : `${qty(line.surplus_quantity)} held on the count`) : "",
          ].filter(Boolean).join(" · ") || "No change"}</td>}
          {approved && <td className="sc-action">
            {line.action_status_label && <span className={`sc-action-status sc-action-${line.action_status || "open"}`}>{line.action_label ? `${line.action_label}: ` : ""}{line.action_status_label}</span>}
            {line.action_status === "done" && line.action === "write_off" && <small>QAR {qar(line.written_off_value ?? "0")} written off</small>}
            {line.action_note && <small>Reason: {line.action_note}{line.action_requested_by ? ` (${line.action_requested_by.username})` : ""}</small>}
            {line.action_decision_note && <small>Finance: {line.action_decision_note}</small>}
            {(line.allowed_actions?.length || line.can_decide_action) ? <div className="sc-line-step">
              <input aria-label={`Reason for ${line.sku}`} value={lineNotes[line.id] ?? ""} placeholder="Reason"
                onChange={(event) => setLineNotes({ ...lineNotes, [line.id]: event.target.value })} />
              {line.allowed_actions?.map((choice) => <button key={choice} className="button button-outline" type="button" disabled={busy}
                onClick={() => void lineStep(line, "action", { action: choice }, choice === "found" ? `${line.sku} moved back from XX.` : `${line.sku} sent to Finance.`)}>{ACTION_BUTTONS[choice]}</button>)}
              {line.can_decide_action && <>
                <button className="button button-dark" type="button" disabled={busy} onClick={() => void lineStep(line, "decision", { approve: true }, `${line.sku}: approved and posted.`)}>Approve</button>
                <button className="button button-outline" type="button" disabled={busy} onClick={() => void lineStep(line, "decision", { approve: false }, `${line.sku}: rejected and returned to the warehouse manager.`)}>Reject</button>
              </>}
            </div> : null}
          </td>}
        </tr>)}</tbody>
      </table></div>
      {detail.lines.length === 0 && <p className="sc-muted">The ERP holds nothing at this location. Add anything you find below.</p>}

      {detail.can_count && <>
        <div className="sc-actions">
          <button className="button button-dark" type="button" disabled={busy} onClick={saveCounts}>Save counts</button>
          <button className="button button-outline" type="button" disabled={busy || detail.counted_lines < detail.line_count || detail.line_count === 0} onClick={() => act("submit", {}, "Sent for review.")}>Send for review</button>
        </div>
        <form className="sc-found" onSubmit={addFound} aria-label="Add an item found here">
          <strong>Found something not on the list?</strong>
          <label>SKU<input value={found.sku} onChange={(event) => setFound({ ...found, sku: event.target.value })} required /></label>
          <label>Counted<input inputMode="decimal" value={found.counted} onChange={(event) => setFound({ ...found, counted: event.target.value })} required /></label>
          <button className="button button-outline" type="submit" disabled={busy}>Add item</button>
        </form>
      </>}

      {detail.can_decide && <div className="sc-decide">
        <p>Before confirming, enter any deliveries or transfers made by hand during the count. The ERP column and the differences update as soon as they are in.</p>
        <label>Note or reason<input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Needed to return for a recount" /></label>
        <div className="sc-actions">
          <button className="button button-outline" type="button" disabled={busy} onClick={() => open(detail.id)}>Refresh figures</button>
          <button className="button button-dark" type="button" disabled={busy} onClick={() => void approve()}>Confirm differences</button>
          <button className="button button-outline" type="button" disabled={busy} onClick={() => act("return", { note: reason }, "Returned for a recount.")}>Return for recount</button>
        </div>
      </div>}
      {detail.status === "submitted" && detail.is_involved && <p className="sc-muted">You took part in this count, so someone else must confirm it.</p>}
      {["counting", "submitted"].includes(detail.status) && (detail.can_count || detail.can_decide) && <details className="sc-cancel">
        <summary>Cancel this count</summary>
        <label>Reason<input value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <button className="button button-outline" type="button" disabled={busy} onClick={() => act("cancel", { note: reason }, "Count cancelled.")}>Cancel count</button>
      </details>}

      <h4>History</h4>
      <ol className="sc-history">{detail.history.map((event, index) => <li key={index}><strong>{EVENTS[event.event] ?? event.event}</strong> by {event.actor} · {when(event.at)}{event.note ? ` · ${event.note}` : ""}</li>)}</ol>
    </section> : overview && <>
      {overview.permissions.can_count && <form className="sc-start" onSubmit={start} aria-label="Start a stock count">
        <label>Location<select value={startForm.location_id} onChange={(event) => setStartForm({ ...startForm, location_id: event.target.value })} required>
          <option value="">Choose a location</option>
          {freeLocations.map((location) => <option key={location.id} value={location.id}>{location.code} · {location.name}</option>)}
        </select></label>
        <label>Note<input value={startForm.note} onChange={(event) => setStartForm({ ...startForm, note: event.target.value })} placeholder="For example, monthly count" /></label>
        <button className="button button-dark" type="submit" disabled={busy || !startForm.location_id}>Start count</button>
      </form>}
      {overview.counts.length === 0 ? <p className="sc-muted">No stock counts yet in {overview.unit.name}.</p>
        : <div className="sc-table-wrap"><table className="sc-table">
          <thead><tr><th scope="col">Count</th><th scope="col">Location</th><th scope="col">Status</th><th scope="col">Started</th><th scope="col" className="sc-num">Counted</th><th scope="col">Differences</th></tr></thead>
          <tbody>{overview.counts.map((count) => <tr key={count.id}>
            <td><button className="sc-link" type="button" onClick={() => open(count.id)}>#{count.id}</button></td>
            <td><strong>{count.location.code}</strong><small>{count.location.name}</small></td>
            <td><span className={`sc-status sc-status-${count.status}`}>{count.status_label}</span></td>
            <td>{count.opened_by?.username}<small>{when(count.opened_at)}</small></td>
            <td className="sc-num">{count.counted_lines} / {count.line_count}</td>
            <td>{count.status === "differences" ? <>{count.open_differences} open{count.waiting_for_finance ? <small>{count.waiting_for_finance} waiting for Finance</small> : null}</> : "—"}</td>
          </tr>)}</tbody>
        </table></div>}
    </>}
  </section>;
}
