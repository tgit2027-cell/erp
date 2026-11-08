import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";
import "./catalogue-reader.css";

type ImportRow = {
  id: number; supplier_id: number; supplier: string; file_name: string; pages_read: string; code_prefix: string;
  status: "review" | "done" | "discarded"; status_label: string; summary: string; draft_count: number; created_count: number;
  uploaded_by: string; created_at: string; finished_by: string; finished_at: string;
};
type Overview = {
  unit: { id: number; code: string; name: string }; can_edit: boolean; ai_available: boolean; max_pages: number;
  imports: ImportRow[]; suppliers: { id: number; name: string }[];
};
type Draft = {
  index: number; page: number; brand: string; brand_id: number | null; brand_item_code: string; description: string;
  category: string; width_cm: string; depth_cm: string; height_cm: string; material: string; colour: string;
  unit_of_measure: string; barcode: string; cost?: string; currency?: string; item_code: string;
  photo_url: string; check: string[]; duplicates: string[];
};
type Created = { item_id: number; item_code: string; sku: string; description: string; brand: string; photo_url: string };
type Detail = {
  import: ImportRow; drafts: Draft[]; can_see_costs: boolean; created?: Created[];
  brands: { id: number; name: string; sold_by_company: boolean }[]; units: { value: string; label: string }[];
};
type Edit = {
  include: boolean; use_photo: boolean; item_code: string; brand: string; brand_name: string; brand_item_code: string;
  description: string; category: string; width_cm: string; depth_cm: string; height_cm: string; material: string;
  colour: string; unit_of_measure: string; barcode: string; cost: string; currency: string;
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

const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const DRAFT_PHOTO = /^\/api\/inventory\/catalogue-imports\/\d+\/photo\/\d+\/[0-9a-f]{16}\.jpg$/;
const ITEM_PHOTO = /^\/api\/items\/\d+\/photo\/[0-9a-f]{16}\.(png|jpg|webp)$/;
const day = (iso: string) => iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";
const ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp,.xlsx,.csv";

function editFor(draft: Draft): Edit {
  return {
    include: draft.duplicates.length === 0 && draft.description !== "", use_photo: draft.photo_url !== "",
    item_code: draft.item_code, brand: draft.brand_id ? String(draft.brand_id) : draft.brand ? "new" : "",
    brand_name: draft.brand_id ? "" : draft.brand, brand_item_code: draft.brand_item_code, description: draft.description,
    category: draft.category, width_cm: draft.width_cm, depth_cm: draft.depth_cm, height_cm: draft.height_cm,
    material: draft.material, colour: draft.colour, unit_of_measure: draft.unit_of_measure, barcode: draft.barcode,
    cost: draft.cost ?? "", currency: draft.currency ?? "",
  };
}

function Upload({ overview, csrfToken, onRead }: { overview: Overview; csrfToken: string; onRead: (detail: Detail) => void }) {
  const [supplier, setSupplier] = useState("");
  const [pages, setPages] = useState("");
  const [prefix, setPrefix] = useState("TVF");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const chosen = file.current?.files?.[0];
    if (!chosen) { setError("Choose a catalogue or price list."); return; }
    const body = new FormData();
    body.append("file", chosen);
    body.append("supplier_id", supplier);
    body.append("pages", pages);
    body.append("prefix", prefix);
    setBusy(true); setError("");
    try {
      onRead(await call<Detail>(`/api/inventory/catalogue-imports/?unit=${overview.unit.id}`, {
        method: "POST", headers: { "X-CSRFToken": csrfToken }, body,
      }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The file could not be read.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form cr-upload" onSubmit={submit} aria-label="Read a catalogue or price list">
    <h3>Upload a catalogue or price list</h3>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-fields">
      <label>Company it comes from<select value={supplier} onChange={(event) => setSupplier(event.target.value)} required>
        <option value="">Choose…</option>
        {overview.suppliers.map((each) => <option key={each.id} value={each.id}>{show(each.name)}</option>)}
      </select></label>
      <label>File (PDF, photo of a page, Excel or CSV)<input ref={file} type="file" accept={ACCEPT} /></label>
      <label>Pages (PDF, optional)<input value={pages} onChange={(event) => setPages(event.target.value)} placeholder={`e.g. 12-30, up to ${overview.max_pages}`} maxLength={9} /></label>
      <label>Code prefix<input value={prefix} onChange={(event) => setPrefix(event.target.value.toUpperCase())} maxLength={5} required /></label>
    </div>
    <p className="sp-hint">The AI lists each product with its brand, the brand's code, description, size, material, colour and price, and cuts its photo out of the page. Nothing is created until you tick and confirm.</p>
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy}>{busy ? "Reading…" : "Read with AI"}</button>
      {busy && <span className="sp-hint" role="status">Reading the file. A long catalogue can take a minute or two.</span>}
    </div>
  </form>;
}

function DraftCard({ draft, edit, detail, unitId, onChange }: {
  draft: Draft; edit: Edit; detail: Detail; unitId: number; onChange: (next: Edit) => void;
}) {
  const set = (field: keyof Edit) => (event: { target: { value: string } }) => onChange({ ...edit, [field]: event.target.value });
  const own = detail.brands.filter((brand) => brand.sold_by_company);
  const others = detail.brands.filter((brand) => !brand.sold_by_company);
  const photo = DRAFT_PHOTO.test(draft.photo_url) ? `${draft.photo_url}?unit=${unitId}` : "";
  const label = edit.description || `Item ${draft.index + 1}`;
  return <article className={`cr-card${edit.include ? "" : " cr-skipped"}`} aria-label={`Draft ${draft.index + 1}`}>
    <div className="cr-side">
      <label className="bc-check cr-include"><input type="checkbox" checked={edit.include} onChange={(event) => onChange({ ...edit, include: event.target.checked })} aria-label={`Create ${show(label)}`} /> Create</label>
      {photo ? <img src={photo} alt={`Photo of ${show(label)} cut from the catalogue`} /> : <span className="cr-nophoto">No photo</span>}
      {photo && <label className="bc-check"><input type="checkbox" checked={edit.use_photo} onChange={(event) => onChange({ ...edit, use_photo: event.target.checked })} /> Use photo</label>}
      {draft.page > 0 && <small>Page {draft.page}</small>}
    </div>
    <div className="cr-body">
      {(draft.duplicates.length > 0 || draft.check.length > 0) && <ul className="cr-notes">
        {draft.duplicates.map((note) => <li key={note} className="cr-duplicate">{show(note)}</li>)}
        {draft.check.map((note) => <li key={note} className="cr-check">{note}</li>)}
      </ul>}
      <div className="cr-fields">
        <label>Tivoli code<input value={edit.item_code} onChange={set("item_code")} maxLength={40} /></label>
        <label>Brand<select value={edit.brand} onChange={set("brand")}>
          <option value="">Choose…</option>
          {own.length > 0 && <optgroup label="Sold by this company">{own.map((brand) => <option key={brand.id} value={brand.id}>{show(brand.name)}</option>)}</optgroup>}
          <optgroup label={own.length ? "Other brands" : "Brands"}>{others.map((brand) => <option key={brand.id} value={brand.id}>{show(brand.name)}</option>)}</optgroup>
          <option value="new">New brand…</option>
        </select></label>
        {edit.brand === "new" && <label>New brand name<input value={edit.brand_name} onChange={set("brand_name")} maxLength={120} /></label>}
        <label>Brand item code<input value={edit.brand_item_code} onChange={set("brand_item_code")} maxLength={60} /></label>
        <label className="cr-wide">Description<input value={edit.description} onChange={set("description")} maxLength={240} /></label>
        <label>Category<input value={edit.category} onChange={set("category")} maxLength={80} /></label>
        <label>Width (cm)<input inputMode="decimal" value={edit.width_cm} onChange={set("width_cm")} /></label>
        <label>Depth (cm)<input inputMode="decimal" value={edit.depth_cm} onChange={set("depth_cm")} /></label>
        <label>Height (cm)<input inputMode="decimal" value={edit.height_cm} onChange={set("height_cm")} /></label>
        <label>Material<input value={edit.material} onChange={set("material")} maxLength={120} /></label>
        <label>Colour / finish<input value={edit.colour} onChange={set("colour")} maxLength={80} /></label>
        <label>Unit<select value={edit.unit_of_measure} onChange={set("unit_of_measure")}>
          {detail.units.map((unit) => <option key={unit.value} value={unit.value}>{unit.label}</option>)}
        </select></label>
        <label>Barcode<input value={edit.barcode} onChange={set("barcode")} maxLength={40} /></label>
        {detail.can_see_costs && <>
          <label>Supplier price<input inputMode="decimal" value={edit.cost} onChange={set("cost")} /></label>
          <label>Currency<input value={edit.currency} onChange={(event) => onChange({ ...edit, currency: event.target.value.toUpperCase() })} maxLength={3} placeholder="EUR" /></label>
        </>}
      </div>
    </div>
  </article>;
}

function Review({ detail, unitId, csrfToken, onDone, onBack }: {
  detail: Detail; unitId: number; csrfToken: string; onDone: (detail: Detail, message: string) => void; onBack: () => void;
}) {
  const [edits, setEdits] = useState<Edit[]>(() => detail.drafts.map(editFor));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const record = detail.import;
  const ticked = edits.filter((edit) => edit.include).length;
  const duplicates = detail.drafts.filter((draft) => draft.duplicates.length > 0).length;
  const reload = async () => call<Detail>(`/api/inventory/catalogue-imports/${record.id}/?unit=${unitId}`);
  const create = async () => {
    setBusy(true); setError("");
    const items = detail.drafts.flatMap((draft, position) => {
      const edit = edits[position];
      if (!edit.include) return [];
      const { include: _include, brand, brand_name, cost, currency, ...fields } = edit;
      return [{
        index: draft.index, ...fields, ...(brand === "new" ? { brand_name } : { brand_id: brand ? Number(brand) : null }),
        ...(detail.can_see_costs ? { cost, currency } : {}),
      }];
    });
    try {
      const result = await call<{ created: Created[] }>(`/api/inventory/catalogue-imports/${record.id}/create/?unit=${unitId}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify({ items }),
      });
      onDone(await reload(), `${result.created.length} ${result.created.length === 1 ? "item was" : "items were"} created from ${record.file_name}.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The items could not be created.");
    } finally { setBusy(false); }
  };
  const discard = async () => {
    setBusy(true); setError("");
    try {
      await call(`/api/inventory/catalogue-imports/${record.id}/?unit=${unitId}`, { method: "DELETE", headers: { "X-CSRFToken": csrfToken } });
      onDone(await reload(), `The upload ${record.file_name} was discarded. No items were created.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The upload could not be discarded.");
    } finally { setBusy(false); }
  };
  const all = (include: boolean) => setEdits(edits.map((edit) => ({ ...edit, include })));
  return <section className="cr-review" aria-label="Check the items the AI found">
    <div className="cr-review-head">
      <h3>{record.file_name}<small>{show(record.supplier)}{record.pages_read && ` · pages ${record.pages_read}`} · read by {record.uploaded_by} on {day(record.created_at)}</small></h3>
      <button className="button button-outline" type="button" onClick={onBack}>Back to uploads</button>
    </div>
    {record.summary && <p className="sp-hint">{show(record.summary)}</p>}
    {error && <div className="alert" role="alert">{error}</div>}
    {record.status === "review" ? <>
      <dl className="sp-totals">
        <div><dt>Products found</dt><dd>{detail.drafts.length}</dd></div>
        <div><dt>Ticked to create</dt><dd>{ticked}</dd></div>
        <div><dt>Possible duplicates</dt><dd className={duplicates ? "ls-low" : undefined}>{duplicates}</dd></div>
      </dl>
      <p className="sp-hint">Check each product, correct anything the AI misread, and untick what you do not want. Possible duplicates start unticked. {detail.can_see_costs ? "The supplier price is saved privately on the item." : "Prices are hidden because you cannot see item costs, and are not saved."}</p>
      <div className="bc-filters">
        <div className="bc-buttons">
          <button className="button button-outline" type="button" onClick={() => all(true)}>Tick all</button>
          <button className="button button-outline" type="button" onClick={() => all(false)}>Untick all</button>
        </div>
      </div>
      <div className="cr-cards">
        {detail.drafts.map((draft, position) => <DraftCard key={draft.index} draft={draft} edit={edits[position]} detail={detail} unitId={unitId}
          onChange={(next) => setEdits(edits.map((edit, at) => at === position ? next : edit))} />)}
      </div>
      <div className="sp-actions cr-actions">
        <button className="button button-dark" type="button" onClick={() => void create()} disabled={busy || ticked === 0}>Create {ticked} {ticked === 1 ? "item" : "items"}</button>
        <button className="button button-outline" type="button" onClick={() => void discard()} disabled={busy}>Discard this upload</button>
      </div>
    </> : record.status === "done" ? <>
      <p className="sp-notice">{record.created_count} {record.created_count === 1 ? "item was" : "items were"} created by {record.finished_by} on {day(record.finished_at)}. They are in Stock items, with the company set to {show(record.supplier)}.</p>
      <div className="sp-table-wrap"><table className="sp-table bc-table si-table" aria-label="Items created">
        <thead><tr><th scope="col"><span className="sr-only">Photo</span></th><th scope="col">Item</th><th scope="col">Brand</th></tr></thead>
        <tbody>{(detail.created ?? []).map((item) => <tr key={item.item_id}>
          <td className="si-thumb">{ITEM_PHOTO.test(item.photo_url) ? <img src={item.photo_url} alt="" /> : <span />}</td>
          <th scope="row">{item.item_code}<small>{show(item.description)} · {item.sku}</small></th>
          <td>{show(item.brand)}</td>
        </tr>)}</tbody>
      </table></div>
    </> : <p className="sp-empty">This upload was discarded by {record.finished_by} on {day(record.finished_at)}. No items were created.</p>}
  </section>;
}

export default function CatalogueReader({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    try {
      setOverview(await call<Overview>(`/api/inventory/catalogue-imports/?unit=${selectedUnitId}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Catalogue uploads could not be loaded.");
    }
  }, [selectedUnitId]);
  useEffect(() => { void load(); }, [load]);
  const open = async (id: number) => {
    if (!overview) return;
    setNotice(""); setError("");
    try {
      setDetail(await call<Detail>(`/api/inventory/catalogue-imports/${id}/?unit=${overview.unit.id}`));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The upload could not be opened.");
    }
  };

  return <section className="content-card supplier-payments catalogue-reader" aria-labelledby="catalogue-reader-title">
    <header className="sp-head">
      <span className="eyebrow">INVENTORY · SUPPLIER CATALOGUES · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="catalogue-reader-title">Read a supplier catalogue</h2>
      <p>Upload a supplier's catalogue or price list and let AI draft the items: photo, brand and brand code, a Tivoli code, description, size, material, colour and price. You check every draft before anything is created.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading…</p>}
    {overview && !overview.can_edit && <p className="sp-empty">You need the permission to edit items and photos in {overview.unit.name} to read catalogues.</p>}
    {overview && overview.can_edit && (detail
      ? <Review key={`${detail.import.id}-${detail.import.status}`} detail={detail} unitId={overview.unit.id} csrfToken={csrfToken}
          onDone={(next, message) => { setDetail(next); setNotice(message); void load(); }}
          onBack={() => { setDetail(null); setNotice(""); }} />
      : <>
        {!overview.ai_available && <div className="alert" role="status">Reading with AI is not switched on here yet. Items can still be added and edited by hand in Stock items.</div>}
        {overview.ai_available && <Upload overview={overview} csrfToken={csrfToken} onRead={(next) => { setDetail(next); setNotice(""); void load(); }} />}
        <h3 className="cr-subhead">Earlier uploads</h3>
        {overview.imports.length === 0
          ? <p className="sp-empty">No catalogues have been read for {overview.unit.name} yet.</p>
          : <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label="Catalogue uploads">
            <thead><tr><th scope="col">File</th><th scope="col">Company</th><th scope="col">Read</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Open</span></th></tr></thead>
            <tbody>{overview.imports.map((row) => <tr key={row.id}>
              <th scope="row">{row.file_name}<small>{row.pages_read && `Pages ${row.pages_read} · `}{row.draft_count} {row.draft_count === 1 ? "product" : "products"} found</small></th>
              <td>{show(row.supplier)}</td>
              <td>{day(row.created_at)}<small>{row.uploaded_by}</small></td>
              <td>{row.status_label}{row.status === "done" && <small>{row.created_count} created</small>}</td>
              <td><button className="button button-outline ls-small" type="button" onClick={() => void open(row.id)}>{row.status === "review" ? "Check items" : "View"}</button></td>
            </tr>)}</tbody>
          </table></div>}
      </>)}
  </section>;
}
