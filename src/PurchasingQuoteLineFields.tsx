import { useEffect, useState } from "react";
import type { QuoteLineInput } from "./purchasing-quote-lines";

export type PriceOption = {
  id: number; variant_id: number; sku: string; amount: string | number; currency: string;
  item?: { item_code?: string; name_en?: string; name_ar?: string; category_en?: string; category_ar?: string; description_en?: string; description_ar?: string; image_path?: string | null };
  stock?: Array<{ location_id: number; quantity: string }> | null;
};
type LocationOption = { id: number; code: string; name: string; parent_id: number | null };

type Props = {
  line: QuoteLineInput;
  index: number;
  first?: boolean;
  kind: "stock" | "special_order";
  prices: PriceOption[];
  locations: LocationOption[];
  disabled: boolean;
  onChange: (field: keyof QuoteLineInput, value: string) => void;
  onRemove?: () => void;
  /** Server search over the whole approved catalog; the list above it holds only the first page. */
  searchItems?: (term: string) => Promise<{ rows: PriceOption[]; more: boolean }>;
  onPick?: (row: PriceOption) => void;
};

function safeSameOriginImage(path: string | null | undefined) {
  if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//") || /[\u0000-\u001f]/.test(path)) return "";
  try {
    return typeof window === "undefined" || new URL(path, window.location.origin).origin === window.location.origin
      ? path : "";
  } catch {
    return "";
  }
}

export default function PurchasingQuoteLineFields({
  line, index, first = false, kind, prices, locations, disabled, onChange, onRemove, searchItems, onPick,
}: Props) {
  const [term, setTerm] = useState("");
  const [found, setFound] = useState<{ term: string; rows: PriceOption[]; more: boolean } | null>(null);
  const [searchError, setSearchError] = useState("");
  useEffect(() => {
    const wanted = term.trim();
    if (!searchItems || wanted.length < 2) { setFound(null); setSearchError(""); return undefined; }
    let active = true;
    const timer = setTimeout(() => {
      searchItems(wanted).then((result) => {
        if (active) { setFound({ term: wanted, ...result }); setSearchError(""); }
      }).catch((reason: unknown) => {
        if (active) setSearchError(reason instanceof Error ? reason.message : "Item search failed.");
      });
    }, 250);
    return () => { active = false; clearTimeout(timer); };
  }, [term, searchItems]);
  const lineNumber = index + 1;
  const prefix = first ? "pw-quote" : `pw-quote-line-${index + 1}`;
  const variantId = first ? "pw-quote-variant" : `${prefix}-variant`;
  const locationId = first ? "pw-quote-location" : `${prefix}-location`;
  const quantityId = first ? "pw-quote-quantity" : `${prefix}-quantity`;
  const specificationsId = first ? "pw-quote-specifications" : `${prefix}-specifications`;
  const unitEnId = first ? "pw-quote-unit-en" : `${prefix}-unit-en`;
  const unitArId = first ? "pw-quote-unit-ar" : `${prefix}-unit-ar`;
  const selectedPrice = prices.find((price) => String(price.variant_id) === line.variantId);
  const listed = found ? found.rows : prices;
  const options = selectedPrice && !listed.some((price) => price.variant_id === selectedPrice.variant_id)
    ? [selectedPrice, ...listed] : listed;
  const searchId = first ? "pw-quote-item-search" : `${prefix}-item-search`;
  const selectedStock = selectedPrice?.stock?.find((stock) => String(stock.location_id) === line.locationId);
  const safeImage = safeSameOriginImage(selectedPrice?.item?.image_path);
  return (
    <fieldset className="pw-quote-line" data-quote-line={lineNumber}>
      <legend>{first ? "Line 1" : `Line ${lineNumber}`}</legend>
      {searchItems && <label htmlFor={searchId}>Find item (SKU, item code or name)
        <input id={searchId} type="search" value={term} maxLength={80} placeholder="Type at least 2 characters"
          onChange={(event) => setTerm(event.target.value)} disabled={disabled} />
      </label>}
      {searchItems && <small className="pw-item-search-status" aria-live="polite">{searchError
        || (found ? `${found.rows.length}${found.more ? "+" : ""} match${found.rows.length === 1 ? "" : "es"} for “${found.term}”${found.more ? ". Refine the search to see more." : ""}`
          : term.trim().length >= 2 ? "Searching…" : "The list shows the first approved prices. Search to find any item.")}</small>}
      <label htmlFor={variantId}>Approved variant / price snapshot</label>
      <select id={variantId} value={line.variantId} onChange={(event) => {
        const row = options.find((price) => String(price.variant_id) === event.target.value);
        if (row) onPick?.(row);
        onChange("variantId", event.target.value);
      }} required disabled={disabled}>
        <option value="">Select a current approved price</option>
        {options.map((price) => <option key={price.id} value={price.variant_id}>{price.sku} · variant {price.variant_id} · {price.amount} {price.currency}</option>)}
      </select>
      {kind === "stock" && <label htmlFor={locationId}>Authorized stock location
        <select id={locationId} value={line.locationId} onChange={(event) => onChange("locationId", event.target.value)} required disabled={disabled}>
          <option value="">Select location</option>
          {locations.map((location) => <option key={location.id} value={location.id}>{location.code} · {location.name}</option>)}
        </select>
      </label>}
      <div className="pw-form-grid">
        <label htmlFor={quantityId}>Quantity
          <input id={quantityId} type="text" inputMode="decimal" required value={line.quantity}
            onChange={(event) => onChange("quantity", event.target.value)} disabled={disabled} />
        </label>
        <label htmlFor={specificationsId}>Item / variant specifications (JSON object)
          <textarea id={specificationsId} rows={3} maxLength={4000} value={line.specifications}
            onChange={(event) => onChange("specifications", event.target.value)} disabled={disabled} />
        </label>
      </div>
      <div className="pw-form-grid pw-unit-fields">
        <label htmlFor={unitEnId}>Display unit (English)
          <input id={unitEnId} type="text" maxLength={80} value={line.unit_en ?? ""}
            onChange={(event) => onChange("unit_en", event.target.value)} disabled={disabled} />
        </label>
        <label htmlFor={unitArId}>Display unit (Arabic)
          <input id={unitArId} type="text" dir="rtl" lang="ar" maxLength={80} value={line.unit_ar ?? ""}
            onChange={(event) => onChange("unit_ar", event.target.value)} disabled={disabled} />
        </label>
      </div>
      {selectedPrice && (
        <div className="pw-quote-item-preview" aria-live="polite">
          {safeImage
            ? <img src={safeImage} alt={selectedPrice.item?.name_en || selectedPrice.sku} loading="lazy" />
            : <div className="pw-image-missing" role="img" aria-label="Product image unavailable">Image unavailable</div>}
          <div className="pw-quote-item-copy">
            <strong>{selectedPrice.item?.name_en || selectedPrice.item?.item_code || selectedPrice.sku}</strong>
            {selectedPrice.item?.name_ar
              ? <span lang="ar" dir="rtl">{selectedPrice.item.name_ar}</span>
              : <span>Arabic name unavailable</span>}
            {!selectedPrice.item?.name_ar && <small>Arabic content not supplied</small>}
            <span>{selectedPrice.item?.category_en || "Category unavailable"} · {selectedPrice.item?.category_ar
              ? <span lang="ar" dir="rtl">{selectedPrice.item.category_ar}</span>
              : <span>Category unavailable</span>}</span>
            <p>{selectedPrice.item?.description_en || "Description unavailable"}</p>
            {selectedPrice.item?.description_ar
              ? <p lang="ar" dir="rtl">{selectedPrice.item.description_ar}</p>
              : <p>Arabic description unavailable</p>}
            {!selectedPrice.item?.description_ar && <small>Arabic description not supplied</small>}
            <b>{selectedPrice.amount} {selectedPrice.currency} per unit</b>
            {kind === "stock" && <>
              <span>Unreserved quantity: {selectedPrice.stock === null || selectedPrice.stock === undefined
                ? "Unavailable — stock read permission was not returned"
                : selectedStock?.quantity || (selectedPrice.stock.length ? "Not returned for selected location" : "Unavailable — no authorized ledger match returned")}</span>
              <small>Independent stock snapshot only; this quote entry does not reserve inventory.</small>
            </>}
          </div>
        </div>
      )}
      {onRemove && <button type="button" data-quote-draft-action className="pw-button pw-button-muted" onClick={onRemove} disabled={disabled}>Remove line {lineNumber}</button>}
    </fieldset>
  );
}
