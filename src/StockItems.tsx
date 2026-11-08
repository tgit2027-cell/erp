import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";
import LabelSheet, { type LabelChoice } from "./LabelSheet";

type Place = { location_id: number; code: string; name: string; quantity: string; free: string };
type Row = {
  item_id: number; variant_id: number; item_code: string; sku: string; description: string; variant_description: string;
  brand: string; company: string; photo_url: string; unit: string; locations: Place[]; quantity: string;
  selling_price: string; is_active: boolean; barcode?: string; name_ar?: string;
  costs?: { stock_value: string; average_cost: string; unit_cost: string; landed_unit_cost: string; received: string; supplier_price?: string; supplier_price_currency?: string };
};
type List = { unit: { id: number; code: string; name: string }; rows: Row[]; total: number; shown: number; can_edit: boolean; can_see_costs: boolean; currency: string };
type Item = {
  id: number; item_code: string; description: string; name_en: string; name_ar: string; description_ar: string;
  category_label: string; category_ar: string; brand_id: number; brand: string; supplier_id: number | null; company: string;
  supplier_item_code: string; barcode: string; unit_of_measure: string; width_cm: string; depth_cm: string; height_cm: string;
  material: string; colour: string; weight_kg: string; volume_m3: string; hs_code: string; lead_time_days: number | "";
  is_active: boolean; photo_url: string; variants: { id: number; sku: string; description: string }[];
  updated_by: string; updated_at: string;
};
type Detail = {
  item: Item; can_edit: boolean; suppliers: { id: number; name: string; brand_ids: number[] }[];
  brands: { id: number; name: string }[]; units: { value: string; label: string }[];
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

const money = (value: string) => value ? Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "";
const qty = (value: string) => Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 });
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const PHOTO = /^\/api\/items\/\d+\/photo\/[0-9a-f]{16}\.(png|jpg|webp)$/;
const photo = (url: string) => PHOTO.test(url) ? url : "";
const TEXT: [keyof Item, string, number][] = [
  ["description", "Description", 240], ["name_ar", "Name in Arabic", 240], ["category_label", "Category", 80],
  ["supplier_item_code", "Company's item code", 60], ["barcode", "Barcode", 40], ["material", "Material", 120],
  ["colour", "Colour", 80], ["hs_code", "HS customs code", 12],
];
const NUMBERS: [keyof Item, string][] = [
  ["width_cm", "Width (cm)"], ["depth_cm", "Depth (cm)"], ["height_cm", "Height (cm)"], ["weight_kg", "Weight (kg)"],
  ["volume_m3", "Volume (m³)"], ["lead_time_days", "Lead time (days)"],
];

function ItemPanel({ unitId, itemId, csrfToken, onClose, onSaved }: {
  unitId: number; itemId: number; csrfToken: string; onClose: () => void; onSaved: (message: string) => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [form, setForm] = useState<Record<string, string | boolean | number | null>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    call<Detail>(`/api/inventory/items/${itemId}/?unit=${unitId}`).then((data) => {
      setDetail(data);
      const { variants: _variants, ...fields } = data.item;
      setForm(fields);
    }).catch((caught) => setError(caught instanceof Error ? caught.message : "The item could not be loaded."));
  }, [unitId, itemId]);
  if (!detail) return <div className="sp-form" role="status">{error ? <div className="alert" role="alert">{error}</div> : "Loading the item…"}</div>;
  const item = detail.item;
  const edit = detail.can_edit;
  const value = (field: string) => String(form[field] ?? "");
  const set = (field: string) => (event: { target: { value: string } }) => setForm({ ...form, [field]: event.target.value });
  const supplier = detail.suppliers.find((each) => String(each.id) === String(form.supplier_id ?? ""));
  const brands = supplier
    ? [...detail.brands].sort((a, b) => Number(supplier.brand_ids.includes(b.id)) - Number(supplier.brand_ids.includes(a.id)))
    : detail.brands;
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    const body: Record<string, unknown> = { brand_id: Number(form.brand_id), supplier_id: form.supplier_id ? Number(form.supplier_id) : null,
      unit_of_measure: form.unit_of_measure, is_active: Boolean(form.is_active) };
    for (const [field] of TEXT) body[field] = form[field] ?? "";
    for (const [field] of NUMBERS) body[field] = form[field] ?? "";
    try {
      await call<Item>(`/api/inventory/items/${item.id}/?unit=${unitId}`, {
        method: "PATCH", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify(body),
      });
      onSaved(`${item.item_code} saved.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The item could not be saved.");
    } finally { setBusy(false); }
  };
  const upload = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true); setError("");
    const data = new FormData();
    data.append("file", file);
    try {
      const saved = await call<Item>(`/api/inventory/items/${item.id}/photo/?unit=${unitId}`, { method: "POST", headers: { "X-CSRFToken": csrfToken }, body: data });
      setDetail({ ...detail, item: { ...detail.item, photo_url: saved.photo_url } });
      onSaved(`Photo uploaded for ${item.item_code}. Quotations and invoices show it from now on.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The photo could not be uploaded.");
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };
  return <form className="sp-form si-panel" onSubmit={save} aria-label={`Item ${item.item_code}`}>
    <div className="si-panel-head">
      <div className="si-photo">{photo(item.photo_url) ? <img src={photo(item.photo_url)} alt={`${item.item_code} photo`} /> : <span>No photo yet</span>}
        {edit && <>
          <button className="button button-outline ls-small" type="button" disabled={busy} onClick={() => fileInput.current?.click()}>{item.photo_url ? "Replace photo" : "Upload photo"}</button>
          <input ref={fileInput} type="file" hidden accept=".png,.jpg,.jpeg,.webp" aria-label="Item photo" onChange={(event) => void upload(event.target.files?.[0])} />
        </>}
      </div>
      <div>
        <h3>{item.item_code} <small>{show(item.description)}</small></h3>
        <p className="sp-hint">Finishes: {item.variants.map((variant) => variant.sku).join(", ")}{item.updated_by && ` · last changed by ${item.updated_by}`}</p>
      </div>
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    <fieldset className="sp-fields si-fields" disabled={!edit || busy}>
      <label>Company (bought from)<select value={value("supplier_id")} onChange={set("supplier_id")}>
        <option value="">Not set</option>
        {detail.suppliers.map((each) => <option key={each.id} value={each.id}>{show(each.name)}</option>)}
      </select></label>
      <label>Brand<select value={value("brand_id")} onChange={set("brand_id")}>
        {brands.map((brand) => <option key={brand.id} value={brand.id}>{show(brand.name)}{supplier?.brand_ids.includes(brand.id) ? " (sold by this company)" : ""}</option>)}
      </select></label>
      <label>Unit<select value={value("unit_of_measure")} onChange={set("unit_of_measure")}>
        {detail.units.map((unit) => <option key={unit.value} value={unit.value}>{unit.label}</option>)}
      </select></label>
      {TEXT.map(([field, label, max]) => <label key={field}>{label}<input value={value(field)} maxLength={max} onChange={set(field)} required={field === "description"} /></label>)}
      {NUMBERS.map(([field, label]) => <label key={field}>{label}<input inputMode="decimal" value={value(field)} onChange={set(field)} /></label>)}
    </fieldset>
    <label className="bc-check"><input type="checkbox" checked={Boolean(form.is_active)} disabled={!edit || busy} onChange={(event) => setForm({ ...form, is_active: event.target.checked })} /> Active (untick when discontinued)</label>
    <div className="sp-actions">
      {edit && <button className="button button-dark" type="submit" disabled={busy}>Save item</button>}
      <button className="button button-outline" type="button" onClick={onClose} disabled={busy}>Close</button>
    </div>
  </form>;
}

export default function StockItems({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [list, setList] = useState<List | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [inactive, setInactive] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [labels, setLabels] = useState<LabelChoice[]>([]);
  const [printing, setPrinting] = useState(false);
  const [scan, setScan] = useState("");
  const [scanned, setScanned] = useState<{ code: string; matched_by: string; rows: Row[] } | null>(null);
  const [scanError, setScanError] = useState("");
  const scanInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    try {
      setList(await call<List>(`/api/inventory/items/?unit=${selectedUnitId}&search=${encodeURIComponent(query)}${inactive ? "&inactive=1" : ""}`));
      setError("");
    } catch (caught) {
      setList(null);
      setError(caught instanceof Error ? caught.message : "Stock items could not be loaded.");
    }
  }, [selectedUnitId, query, inactive]);
  useEffect(() => { void load(); }, [load]);
  const saved = (message: string) => { setNotice(message); void load(); };
  const chosen = (row: Row) => labels.some((choice) => choice.item.variant_id === row.variant_id);
  const toggleLabel = (row: Row) => setLabels((current) => current.some((choice) => choice.item.variant_id === row.variant_id)
    ? current.filter((choice) => choice.item.variant_id !== row.variant_id)
    : [...current, { item: row, copies: Math.max(1, Math.round(Number(row.quantity) || 1)) }]);
  const lookUp = async (event: FormEvent) => {
    event.preventDefault();
    const code = scan.trim();
    if (!code || selectedUnitId === null) return;
    setScanError("");
    try {
      setScanned(await call<{ code: string; matched_by: string; rows: Row[] }>(`/api/inventory/scan/?unit=${selectedUnitId}&code=${encodeURIComponent(code)}`));
    } catch (caught) {
      setScanned(null);
      setScanError(caught instanceof Error ? caught.message : "The code could not be looked up.");
    }
    setScan("");
    scanInput.current?.focus();
  };

  return <section className="content-card supplier-payments stock-items" aria-labelledby="stock-items-title">
    <header className="sp-head">
      <span className="eyebrow">INVENTORY · STOCK ITEMS · TEST ONLY{list ? ` · ${list.unit.name}` : ""}</span>
      <h2 id="stock-items-title">Stock items</h2>
      <p>Every item and finish this unit stocks or sells: photo, code, description, the company we buy it from, brand, where it is, how many, and the selling price. {list?.can_see_costs ? "You can also see costs." : "Costs are shown only to people allowed to see them."} Tick items to print barcode labels, or scan a label to find an item.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!list && !error && <p className="sp-empty" role="status">Loading stock items…</p>}
    {list && <>
      <form className="bc-filters" onSubmit={(event) => { event.preventDefault(); setQuery(search.trim()); }}>
        <label>Search<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Code, description, brand, company or barcode" /></label>
        <button className="button button-outline" type="submit">Search</button>
        <label className="bc-check"><input type="checkbox" checked={inactive} onChange={(event) => setInactive(event.target.checked)} /> Include discontinued</label>
      </form>
      <form className="scan-box" onSubmit={lookUp} aria-label="Scan a label">
        <label>Scan a label or barcode<input ref={scanInput} value={scan} onChange={(event) => setScan(event.target.value)}
          placeholder="Scan, or type a SKU and press Enter" autoComplete="off" /></label>
        <button className="button button-outline" type="submit">Find</button>
        {labels.length > 0 && !printing && <button className="button button-dark" type="button" onClick={() => setPrinting(true)}>Print labels ({labels.length} {labels.length === 1 ? "item" : "items"})</button>}
      </form>
      {scanError && <div className="alert" role="alert">{scanError}</div>}
      {scanned && <div className="scan-result" role="status" aria-label="Scanned item">
        <span>Scanned {scanned.code}: {scanned.matched_by === "sku" ? "our label" : scanned.matched_by === "barcode" ? "the maker's barcode" : "item code"}{scanned.rows.length > 1 ? `, ${scanned.rows.length} finishes` : ""}</span>
        {scanned.rows.map((row) => <div key={row.variant_id}>
          <strong>{row.item_code} · {row.sku}</strong> {show(row.description)} · {show(row.variant_description)}
          <div>{row.locations.length ? row.locations.map((place) => `${place.code}: ${qty(place.quantity)} (${qty(place.free)} free)`).join(" · ") : "None in stock in this unit"}{row.selling_price && ` · ${list.currency} ${money(row.selling_price)}`}</div>
          <div className="scan-actions">
            <button className="button button-outline ls-small" type="button" onClick={() => { setNotice(""); setOpen(row.item_id); }}>{list.can_edit ? "Edit" : "Details"}</button>
            {!chosen(row) && <button className="button button-outline ls-small" type="button" onClick={() => toggleLabel(row)}>Add to labels</button>}
          </div>
        </div>)}
      </div>}
      {printing && <LabelSheet choices={labels} onChange={setLabels} onClose={() => setPrinting(false)} />}
      {open !== null && <ItemPanel key={open} unitId={list.unit.id} itemId={open} csrfToken={csrfToken} onClose={() => setOpen(null)} onSaved={saved} />}
      {list.rows.length === 0
        ? <p className="sp-empty">No items match.</p>
        : <div className="sp-table-wrap"><table className="sp-table bc-table si-table" aria-label="Stock items">
          <thead><tr>
            <th scope="col"><span className="sr-only">Label</span></th><th scope="col"><span className="sr-only">Photo</span></th><th scope="col">Item</th><th scope="col">Company</th>
            <th scope="col">Where</th><th scope="col" className="bc-num">Quantity</th><th scope="col" className="bc-num">Unit price ({list.currency})</th>
            {list.can_see_costs && <><th scope="col" className="bc-num">Landed cost</th><th scope="col" className="bc-num">Average cost</th><th scope="col" className="bc-num">Stock value</th></>}
            <th scope="col"><span className="sr-only">Open</span></th>
          </tr></thead>
          <tbody>{list.rows.map((row) => <tr key={row.variant_id} className={row.is_active ? undefined : "si-inactive"}>
            <td><input type="checkbox" aria-label={`Label for ${row.sku}`} checked={chosen(row)} onChange={() => toggleLabel(row)} /></td>
            <td className="si-thumb">{photo(row.photo_url) ? <img src={photo(row.photo_url)} alt="" loading="lazy" /> : <span aria-hidden="true" />}</td>
            <th scope="row">{row.item_code} · {row.sku}<small>{show(row.description)} · {show(row.variant_description)}</small><small>{show(row.brand)}{!row.is_active && " · discontinued"}</small></th>
            <td>{row.company ? show(row.company) : <span className="sp-muted">Not set</span>}</td>
            <td>{row.locations.length ? row.locations.map((place) => <small key={place.location_id}>{place.code}: {qty(place.quantity)}</small>) : <span className="sp-muted">None in stock</span>}</td>
            <td className="pw-mono bc-num">{qty(row.quantity)}<small>{row.unit}</small></td>
            <td className="pw-mono bc-num">{money(row.selling_price)}</td>
            {list.can_see_costs && <>
              <td className="pw-mono bc-num">{money(row.costs?.landed_unit_cost ?? "")}{row.costs?.received && <small>{row.costs.received}</small>}
                {row.costs?.supplier_price && <small>Price list {Number(row.costs.supplier_price).toLocaleString("en-US", { minimumFractionDigits: 2 })} {row.costs.supplier_price_currency}</small>}</td>
              <td className="pw-mono bc-num">{money(row.costs?.average_cost ?? "")}</td>
              <td className="pw-mono bc-num">{money(row.costs?.stock_value ?? "")}</td>
            </>}
            <td><button className="button button-outline ls-small" type="button" onClick={() => { setNotice(""); setOpen(row.item_id); }}>{list.can_edit ? "Edit" : "Details"}</button></td>
          </tr>)}</tbody>
        </table></div>}
      {list.total > list.shown && <p className="sp-hint">Showing {list.shown} of {list.total}. Search to narrow the list.</p>}
    </>}
  </section>;
}
