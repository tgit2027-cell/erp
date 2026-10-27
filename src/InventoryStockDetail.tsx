import { useEffect, useMemo, useState } from "react";
import "./inventory-workspace.css";

type BucketDetail = {
  item_code?: string;
  item_description?: string;
  variant_sku?: string;
  variant_description?: string;
  brand_name?: string;
  location_code?: string;
  location_name?: string;
  unit_code?: string;
  unit_name?: string;
  quantity?: string;
  held_quantity?: string;
  sellable_quantity?: string;
};
type DetailState = { key: string; status: "loading" | "ready" | "denied" | "error"; data: BucketDetail | null; message: string };

async function getBucket(path: string, signal: AbortSignal) {
  const response = await fetch(path, {
    method: "GET",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof body?.detail === "string" ? body.detail : "The stock bucket was not returned.") as Error & { status: number };
    error.status = response.status;
    throw error;
  }
  return body as BucketDetail;
}

export default function InventoryStockDetail({
  bucketId,
  unitId,
  locationId,
  onClose,
}: {
  bucketId: number;
  unitId: number;
  locationId: string;
  onClose: () => void;
}) {
  const contextKey = useMemo(() => JSON.stringify([bucketId, unitId, locationId]), [bucketId, unitId, locationId]);
  const [state, setState] = useState<DetailState>({ key: "", status: "loading", data: null, message: "" });
  const visible = state.key === contextKey ? state : null;

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ unit: String(unitId) });
    if (locationId) params.set("location", locationId);
    setState({ key: contextKey, status: "loading", data: null, message: "" });
    getBucket(`/api/stock-balances/${encodeURIComponent(String(bucketId))}/?${params}`, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ key: contextKey, status: "ready", data, message: "" });
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        const status = (reason as { status?: number }).status;
        setState({
          key: contextKey,
          status: status === 403 || status === 404 ? "denied" : "error",
          data: null,
          message: reason instanceof Error ? reason.message : "Stock detail could not be loaded.",
        });
      });
    return () => controller.abort();
  }, [bucketId, unitId, locationId, contextKey]);

  const fields: [string, keyof BucketDetail][] = [
    ["Item", "item_code"],
    ["Description", "item_description"],
    ["SKU", "variant_sku"],
    ["Variant", "variant_description"],
    ["Brand", "brand_name"],
    ["Location", "location_name"],
    ["Location code", "location_code"],
    ["Business unit", "unit_name"],
    ["Business-unit code", "unit_code"],
    ["Unreserved quantity", "quantity"],
    ["Held on quotes", "held_quantity"],
    ["Free to sell", "sellable_quantity"],
  ];

  return (
    <aside className="inventory-bucket-detail" aria-label="Stock bucket identity and quantity">
      <div className="inventory-bucket-detail-heading">
        <div><span className="eyebrow">LEDGER DETAIL · GET ONLY</span><h3>Stock identity &amp; quantity</h3></div>
        <button type="button" className="button button-quiet" onClick={onClose} aria-label="Close stock bucket details">Close</button>
      </div>
      {!visible || visible.status === "loading" ? <div className="inventory-detail-skeleton" role="status" aria-label="Loading stock bucket detail"><i /><i /><i /></div> : null}
      {visible?.status === "denied" && <p className="inventory-register-notice" role="status">This bucket is not available in the current business-unit and location scope.</p>}
      {visible?.status === "error" && <p className="inventory-register-error" role="alert">{visible.message}</p>}
      {visible?.status === "ready" && visible.data && <dl className="inventory-bucket-fields">
        {fields.map(([label, key]) => {
          const value = visible.data?.[key];
          return value === undefined || value === null ? null : <div key={key}><dt>{label}</dt><dd>{value}</dd></div>;
        })}
      </dl>}
    </aside>
  );
}
