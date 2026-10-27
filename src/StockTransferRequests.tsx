import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import "./stock-transfers.css";

type Place = { id: number; name: string };
type TransferLine = { variant_id: number; sku: string; description: string; quantity: string };
type Step = "approve" | "reject" | "schedule" | "dispatch" | "receive";
export type TransferRequest = {
  id: number;
  status: string;
  status_label: string;
  source_location: Place;
  destination_location: Place;
  note: string;
  requested_by: string;
  requested_at: string;
  decided_by: string | null;
  rejection_reason: string;
  transfer_date: string | null;
  received_by: string | null;
  received_at: string | null;
  is_mine: boolean;
  lines: TransferLine[];
  next_steps: Step[];
};
type Listing = {
  permissions: { can_request: boolean; can_approve: boolean; can_execute: boolean };
  locations: Place[];
  results: TransferRequest[];
};
type StockItem = { variant_id: number; sku: string; description: string; free_quantity: string };
type DraftLine = { variantId: string; quantity: string };

class TransferRequestError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function newKey() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `transfer-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...init.headers },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string"
      ? data.detail
      : Object.values(data).flat().join(" ") || "The transfer request could not be completed.";
    throw new TransferRequestError(String(detail), response.status);
  }
  return data as T;
}

function localDay(value: string) {
  return new Date(value).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function todayIso() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

const STEP_LABELS: Record<Step, string> = {
  approve: "Approve",
  reject: "Reject",
  schedule: "Set transfer date",
  dispatch: "Mark as sent",
  receive: "Confirm arrival",
};

function RequestCard({ record, onStep, busy }: {
  record: TransferRequest;
  onStep: (record: TransferRequest, step: Step, body: Record<string, string>) => Promise<boolean>;
  busy: boolean;
}) {
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [date, setDate] = useState(record.transfer_date ?? todayIso());
  const steps = record.next_steps;
  return (
    <article className="transfer-card" aria-label={`Transfer request ${record.id}`} data-status={record.status}>
      <header className="transfer-card-heading">
        <div>
          <span className="eyebrow">REQUEST #{record.id}</span>
          <h4>{record.source_location.name} → {record.destination_location.name}</h4>
        </div>
        <span className="transfer-status">{record.status_label}</span>
      </header>
      <ul className="transfer-lines">
        {record.lines.map((line) => <li key={line.variant_id}><span className="pw-mono">{line.sku}</span> {line.description} <strong>× {line.quantity}</strong></li>)}
      </ul>
      <p className="transfer-meta">
        Requested by {record.is_mine ? "you" : record.requested_by} on {localDay(record.requested_at)}
        {record.decided_by && record.status !== "rejected" ? ` · approved by ${record.decided_by}` : ""}
        {record.transfer_date ? ` · transfer date ${localDay(`${record.transfer_date}T12:00:00`)}` : ""}
        {record.received_at ? ` · received by ${record.received_by} on ${localDay(record.received_at)}` : ""}
      </p>
      {record.note && <p className="transfer-meta">Note: {record.note}</p>}
      {record.status === "rejected" && <p className="transfer-rejection" role="note">Rejected by {record.decided_by}: {record.rejection_reason}</p>}
      {record.status === "in_transit" && <p className="transfer-meta">In transit: these items cannot be sold from either location until arrival is confirmed.</p>}
      {steps.length > 0 && <div className="transfer-actions">
        {steps.includes("approve") && <button type="button" className="button button-dark" disabled={busy} onClick={() => void onStep(record, "approve", {})}>{STEP_LABELS.approve}</button>}
        {steps.includes("reject") && !rejecting && <button type="button" className="button button-outline" disabled={busy} onClick={() => setRejecting(true)}>{STEP_LABELS.reject}</button>}
        {rejecting && <form className="transfer-inline-form" onSubmit={(event) => { event.preventDefault(); void onStep(record, "reject", { reason }).then((done) => done && setRejecting(false)); }}>
          <label>Reason for the requester<input value={reason} maxLength={240} required onChange={(event) => setReason(event.target.value)} /></label>
          <button type="submit" className="button button-dark" disabled={busy || !reason.trim()}>Send rejection</button>
          <button type="button" className="button button-quiet" onClick={() => setRejecting(false)}>Cancel</button>
        </form>}
        {steps.includes("schedule") && <form className="transfer-inline-form" onSubmit={(event) => { event.preventDefault(); void onStep(record, "schedule", { transfer_date: date }); }}>
          <label>Transfer date<input type="date" min={todayIso()} value={date} required onChange={(event) => setDate(event.target.value)} /></label>
          <button type="submit" className="button button-outline" disabled={busy || !date}>{record.status === "scheduled" ? "Change date" : STEP_LABELS.schedule}</button>
        </form>}
        {steps.includes("dispatch") && <button type="button" className="button button-dark" disabled={busy} onClick={() => void onStep(record, "dispatch", {})}>{STEP_LABELS.dispatch}</button>}
        {steps.includes("receive") && <button type="button" className="button button-dark" disabled={busy} onClick={() => void onStep(record, "receive", {})}>{STEP_LABELS.receive}</button>}
      </div>}
    </article>
  );
}

function RequestForm({ unitId, locations, csrfToken, onCreated }: {
  unitId: number;
  locations: Place[];
  csrfToken: string;
  onCreated: () => void;
}) {
  const [source, setSource] = useState("");
  const [destination, setDestination] = useState("");
  const [items, setItems] = useState<StockItem[] | null>(null);
  const [lines, setLines] = useState<DraftLine[]>([{ variantId: "", quantity: "1" }]);
  const [note, setNote] = useState("");
  const [key, setKey] = useState(newKey);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    setItems(null);
    setLines([{ variantId: "", quantity: "1" }]);
    if (!source) return;
    const controller = new AbortController();
    call<{ items: StockItem[] }>(`/api/transfer-requests/stock/?unit=${unitId}&location=${source}`, { signal: controller.signal })
      .then((data) => setItems(data.items))
      .catch((caught: unknown) => { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Items could not be loaded."); });
    return () => controller.abort();
  }, [source, unitId]);

  const updateLine = (index: number, change: Partial<DraftLine>) => {
    setKey(newKey());
    setLines((current) => current.map((line, position) => position === index ? { ...line, ...change } : line));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      await call(`/api/transfer-requests/?unit=${unitId}`, {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken, "Idempotency-Key": key },
        body: JSON.stringify({
          source_location_id: Number(source),
          destination_location_id: Number(destination),
          lines: lines.map((line) => ({ variant_id: Number(line.variantId), quantity: line.quantity })),
          note,
        }),
      });
      setSuccess("Request sent to the showroom manager.");
      setLines([{ variantId: "", quantity: "1" }]);
      setNote("");
      setKey(newKey());
      onCreated();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The request could not be sent.");
    } finally {
      setSaving(false);
    }
  };

  const chosen = new Set(lines.map((line) => line.variantId));
  const ready = source && destination && source !== destination && lines.every((line) => line.variantId && Number(line.quantity) > 0);
  return (
    <form className="transfer-form" onSubmit={submit} aria-labelledby="transfer-form-title">
      <h4 id="transfer-form-title">Request a stock move</h4>
      <div className="transfer-form-row">
        <label>From<select value={source} required onChange={(event) => { setSource(event.target.value); setKey(newKey()); }}>
          <option value="">Choose a location</option>
          {locations.map((place) => <option key={place.id} value={place.id}>{place.name}</option>)}
        </select></label>
        <label>To<select value={destination} required onChange={(event) => { setDestination(event.target.value); setKey(newKey()); }}>
          <option value="">Choose a location</option>
          {locations.filter((place) => String(place.id) !== source).map((place) => <option key={place.id} value={place.id}>{place.name}</option>)}
        </select></label>
      </div>
      {source && items === null && !error && <p className="transfer-meta" role="status">Loading items at this location…</p>}
      {items?.length === 0 && <p className="transfer-meta" role="status">No items with free stock at this location.</p>}
      {items && items.length > 0 && lines.map((line, index) => {
        const item = items.find((candidate) => String(candidate.variant_id) === line.variantId);
        return (
          <div className="transfer-form-row" key={index}>
            <label>Item<select value={line.variantId} required onChange={(event) => updateLine(index, { variantId: event.target.value })}>
              <option value="">Choose an item</option>
              {items.filter((candidate) => candidate.variant_id === Number(line.variantId) || !chosen.has(String(candidate.variant_id))).map((candidate) => (
                <option key={candidate.variant_id} value={candidate.variant_id}>{candidate.sku} · {candidate.description} ({candidate.free_quantity} free)</option>
              ))}
            </select></label>
            <label>Quantity<input type="number" min="0.01" step="0.01" max={item?.free_quantity} required value={line.quantity} onChange={(event) => updateLine(index, { quantity: event.target.value })} /></label>
            {lines.length > 1 && <button type="button" className="button button-quiet" onClick={() => { setKey(newKey()); setLines((current) => current.filter((_, position) => position !== index)); }}>Remove</button>}
          </div>
        );
      })}
      {items && items.length > lines.length && <button type="button" className="button button-quiet" onClick={() => { setKey(newKey()); setLines((current) => [...current, { variantId: "", quantity: "1" }]); }}>Add another item</button>}
      <label>Note for the manager (optional)<input value={note} maxLength={240} onChange={(event) => { setNote(event.target.value); setKey(newKey()); }} /></label>
      {error && <div className="alert" role="alert">{error}</div>}
      {success && <div className="capacity-success" role="status">{success}</div>}
      <button type="submit" className="button button-dark" disabled={saving || !ready}>{saving ? "Sending…" : "Send request"}</button>
    </form>
  );
}

export default function StockTransferRequests({ selectedUnitId, csrfToken }: { selectedUnitId: number; csrfToken: string }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      setListing(await call<Listing>(`/api/transfer-requests/?unit=${selectedUnitId}`));
      setDenied(false);
      setError("");
    } catch (caught) {
      if (caught instanceof TransferRequestError && caught.status === 403) setDenied(true);
      else setError(caught instanceof Error ? caught.message : "Transfer requests could not be loaded.");
      setListing(null);
    }
  }, [selectedUnitId]);

  useEffect(() => { void load(); }, [load]);

  const step = async (record: TransferRequest, action: Step, body: Record<string, string>) => {
    setBusy(true);
    setNotice("");
    setError("");
    try {
      await call(`/api/transfer-requests/${record.id}/${action}/?unit=${selectedUnitId}`, {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken, "Idempotency-Key": newKey() },
        body: JSON.stringify(body),
      });
      setNotice(`Request #${record.id}: ${STEP_LABELS[action].toLowerCase()} done.`);
      await load();
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The step could not be completed.");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const waiting = listing?.results.filter((record) => record.next_steps.length > 0) ?? [];
  const others = listing?.results.filter((record) => record.next_steps.length === 0) ?? [];
  return (
    <section className="transfer-section" aria-labelledby="transfer-title">
      <div className="capacity-section-heading">
        <div>
          <span className="eyebrow">SHOWROOM → MANAGER → WAREHOUSE</span>
          <h3 id="transfer-title">Stock transfer requests</h3>
        </div>
        <button className="button button-outline" type="button" onClick={() => void load()} disabled={busy}>Refresh</button>
      </div>
      {denied && <p className="capacity-empty" role="status">You have no stock transfer permission for this business unit. An administrator can grant request, approve or warehouse permission.</p>}
      {error && <div className="alert" role="alert">{error}</div>}
      {notice && <div className="capacity-success" role="status">{notice}</div>}
      {!listing && !denied && !error && <p className="capacity-empty" role="status">Loading transfer requests…</p>}
      {listing?.permissions.can_request && <RequestForm unitId={selectedUnitId} locations={listing.locations} csrfToken={csrfToken} onCreated={() => void load()} />}
      {listing && <>
        <h4 className="transfer-group-title">Waiting for you ({waiting.length})</h4>
        {waiting.length === 0 ? <p className="capacity-empty">Nothing needs your action.</p> : <div className="transfer-list">{waiting.map((record) => <RequestCard key={record.id} record={record} onStep={step} busy={busy} />)}</div>}
        <h4 className="transfer-group-title">All requests ({others.length})</h4>
        {others.length === 0 ? <p className="capacity-empty">No other requests.</p> : <div className="transfer-list">{others.map((record) => <RequestCard key={record.id} record={record} onStep={step} busy={busy} />)}</div>}
      </>}
    </section>
  );
}
