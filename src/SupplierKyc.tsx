import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-kyc.css";

type Person = { id: number; username: string; name: string } | null;
type FileRow = {
  id: number;
  supplier: { id: number; code: string; name: string };
  legal_name: string;
  cr_number: string;
  vat_number: string;
  status: string;
  status_label: string;
  opened_by: Person;
  opened_at: string;
  submitted_at: string | null;
  decided_by: Person;
  decided_at: string | null;
};
type Overview = {
  permissions: { can_open: boolean; can_approve: boolean };
  counts: Record<string, number>;
  document_kinds: { kind: string; label: string }[];
  files: FileRow[];
};
type Detail = FileRow & {
  address: string;
  cr_expiry: string | null;
  vat_registered: boolean;
  signatory_name: string;
  signatory_title: string;
  contact_email: string;
  contact_phone: string;
  decision_note: string;
  missing_documents: { kind: string; label: string }[];
  documents: { id: number; kind: string; kind_label: string; name: string; size: number; uploaded_by: string; uploaded_at: string }[];
  history: { event: string; actor: string; note: string; at: string }[];
  can_edit: boolean;
  can_decide: boolean;
};
type Form = {
  code: string; legal_name: string; address: string; cr_number: string; cr_expiry: string;
  vat_registered: boolean; vat_number: string; signatory_name: string; signatory_title: string;
  contact_email: string; contact_phone: string;
};

const EMPTY: Form = {
  code: "TEST-SUP-", legal_name: "", address: "", cr_number: "", cr_expiry: "", vat_registered: false,
  vat_number: "", signatory_name: "", signatory_title: "", contact_email: "", contact_phone: "",
};
const FILTERS = [
  { value: "", label: "All" },
  { value: "submitted", label: "Waiting for approval" },
  { value: "draft", label: "Draft" },
  { value: "rejected", label: "Rejected" },
  { value: "approved", label: "Approved" },
];
const EVENTS: Record<string, string> = {
  opened: "Opened", edited: "Edited", reopened: "Corrected after rejection", document_added: "Document attached",
  submitted: "Sent for approval", approved: "Approved", rejected: "Rejected",
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

const when = (value: string | null) => value ? new Date(value).toLocaleString() : "—";
const kilobytes = (size: number) => size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(size / 1024))} KB`;

function toForm(detail: Detail): Form {
  return {
    code: detail.supplier.code, legal_name: detail.legal_name, address: detail.address, cr_number: detail.cr_number,
    cr_expiry: detail.cr_expiry ?? "", vat_registered: detail.vat_registered, vat_number: detail.vat_number,
    signatory_name: detail.signatory_name, signatory_title: detail.signatory_title,
    contact_email: detail.contact_email, contact_phone: detail.contact_phone,
  };
}

function SupplierForm({ initial, isNew, busy, onSubmit, onCancel }: {
  initial: Form; isNew: boolean; busy: boolean; onSubmit: (form: Form) => void; onCancel: () => void;
}) {
  const [form, setForm] = useState(initial);
  const set = (field: keyof Form) => (event: { target: HTMLInputElement }) =>
    setForm({ ...form, [field]: event.target.type === "checkbox" ? event.target.checked : event.target.value });
  const submit = (event: FormEvent) => { event.preventDefault(); onSubmit(form); };
  return (
    <form className="kyc-form" onSubmit={submit} aria-label={isNew ? "Open a new supplier" : "Correct supplier details"}>
      <h3>{isNew ? "Open a new local supplier" : "Correct supplier details"}</h3>
      <fieldset>
        <legend>Company</legend>
        {isNew && <label>Supplier code<input value={form.code} onChange={set("code")} required /></label>}
        <label>Legal company name<input value={form.legal_name} onChange={set("legal_name")} required /></label>
        <label className="kyc-wide">Address<input value={form.address} onChange={set("address")} /></label>
        <label>CR number<input value={form.cr_number} onChange={set("cr_number")} required /></label>
        <label>CR expiry<input type="date" value={form.cr_expiry} onChange={set("cr_expiry")} /></label>
        <label className="kyc-check"><input type="checkbox" checked={form.vat_registered} onChange={set("vat_registered")} />VAT registered</label>
        {form.vat_registered && <label>VAT number<input value={form.vat_number} onChange={set("vat_number")} required /></label>}
      </fieldset>
      <fieldset>
        <legend>Authorized signatory and contact</legend>
        <label>Signatory name<input value={form.signatory_name} onChange={set("signatory_name")} required /></label>
        <label>Signatory title<input value={form.signatory_title} onChange={set("signatory_title")} /></label>
        <label>Contact email<input type="email" value={form.contact_email} onChange={set("contact_email")} /></label>
        <label>Contact phone<input value={form.contact_phone} onChange={set("contact_phone")} /></label>
      </fieldset>
      <div className="kyc-actions">
        <button className="button button-dark" type="submit" disabled={busy}>{isNew ? "Open supplier file" : "Save changes"}</button>
        <button className="button button-outline" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}

function SupplierFile({ id, kinds, csrfToken, onBack, onChanged }: {
  id: number; kinds: Overview["document_kinds"]; csrfToken: string; onBack: () => void; onChanged: () => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [kind, setKind] = useState("cr_certificate");
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState("");
  const [fileInputKey, setFileInputKey] = useState(0);

  const load = useCallback(async () => {
    try { setDetail(await call<Detail>(`/api/suppliers/kyc/${id}/`)); setError(""); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "The supplier file could not be loaded."); }
  }, [id]);
  useEffect(() => { void load(); }, [load]);

  const run = async (request: () => Promise<Detail>) => {
    setBusy(true); setError("");
    try { setDetail(await request()); onChanged(); return true; }
    catch (caught) { setError(caught instanceof Error ? caught.message : "The change could not be saved."); return false; }
    finally { setBusy(false); }
  };
  const json = (method: string, body: unknown) => ({
    method, headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify(body),
  });
  const upload = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) { setError("Choose a file to attach."); return; }
    const body = new FormData();
    body.append("kind", kind);
    body.append("file", file);
    if (await run(() => call<Detail>(`/api/suppliers/kyc/${id}/documents/`, { method: "POST", headers: { "X-CSRFToken": csrfToken }, body }))) {
      setFile(null); setFileInputKey((value) => value + 1);
    }
  };
  const decide = async (approve: boolean) => {
    if (await run(() => call<Detail>(`/api/suppliers/kyc/${id}/decision/`, json("POST", { approve, note })))) setNote("");
  };

  return (
    <section className="kyc-file" aria-labelledby="kyc-file-title">
      <header className="kyc-file-head">
        <div>
          <span className="eyebrow">SUPPLIER FILE{detail ? ` · ${detail.supplier.code}` : ""}</span>
          <h3 id="kyc-file-title">{detail?.legal_name ?? "Loading…"}</h3>
          {detail && <span className={`kyc-status kyc-${detail.status}`}>{detail.status_label}</span>}
        </div>
        <button className="button button-outline" type="button" onClick={onBack}>Back to all suppliers</button>
      </header>
      {error && <div className="alert" role="alert">{error}</div>}
      {detail && editing && <SupplierForm initial={toForm(detail)} isNew={false} busy={busy} onCancel={() => setEditing(false)}
        onSubmit={async (form) => { if (await run(() => call<Detail>(`/api/suppliers/kyc/${id}/`, json("PATCH", form)))) setEditing(false); }} />}
      {detail && !editing && <>
        {detail.status === "rejected" && <p className="kyc-rejection"><strong>Rejected by {detail.decided_by?.name}:</strong> {detail.decision_note}</p>}
        <dl className="kyc-facts">
          <div><dt>Legal name</dt><dd>{detail.legal_name}</dd></div>
          <div><dt>CR number</dt><dd className="pw-mono">{detail.cr_number}</dd></div>
          <div><dt>CR expiry</dt><dd>{detail.cr_expiry ?? "—"}</dd></div>
          <div><dt>VAT</dt><dd className="pw-mono">{detail.vat_registered ? detail.vat_number : "Not VAT registered"}</dd></div>
          <div><dt>Authorized signatory</dt><dd>{detail.signatory_name}{detail.signatory_title ? `, ${detail.signatory_title}` : ""}</dd></div>
          <div><dt>Contact</dt><dd>{[detail.contact_email, detail.contact_phone].filter(Boolean).join(" · ") || "—"}</dd></div>
          <div className="kyc-wide"><dt>Address</dt><dd>{detail.address || "—"}</dd></div>
          <div><dt>Opened by</dt><dd>{detail.opened_by?.name} · {when(detail.opened_at)}</dd></div>
          <div><dt>Decision</dt><dd>{detail.decided_by ? `${detail.decided_by.name} · ${when(detail.decided_at)}` : "—"}</dd></div>
        </dl>
        {detail.can_edit && <div className="kyc-actions">
          <button className="button button-outline" type="button" onClick={() => setEditing(true)} disabled={busy}>Correct details</button>
          {detail.status === "draft" && <button className="button button-dark" type="button" disabled={busy || detail.missing_documents.length > 0}
            onClick={() => void run(() => call<Detail>(`/api/suppliers/kyc/${id}/submit/`, json("POST", {})))}>Send for approval</button>}
        </div>}
        <h4>Documents</h4>
        {detail.missing_documents.length > 0 && <p className="kyc-missing" role="status">Still needed before approval: {detail.missing_documents.map((item) => item.label).join(", ")}.</p>}
        {detail.documents.length === 0 ? <p className="kyc-empty">No documents attached yet.</p> : <ul className="kyc-documents">
          {detail.documents.map((document) => <li key={document.id}>
            <a href={`/api/suppliers/kyc/${id}/documents/${document.id}/`}>{document.name}</a>
            <span>{document.kind_label} · {kilobytes(document.size)} · {document.uploaded_by} · {when(document.uploaded_at)}</span>
          </li>)}
        </ul>}
        {detail.can_edit && <form className="kyc-upload" onSubmit={upload} aria-label="Attach a document">
          <label>Document<select value={kind} onChange={(event) => setKind(event.target.value)}>
            {kinds.map((item) => <option key={item.kind} value={item.kind}>{item.label}</option>)}
          </select></label>
          <label>File (PDF, PNG or JPG, up to 10 MB)<input key={fileInputKey} type="file" accept=".pdf,.png,.jpg,.jpeg"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
          <button className="button button-outline" type="submit" disabled={busy || !file}>Attach</button>
        </form>}
        {detail.can_decide && <section className="kyc-decision" aria-label="Approval decision">
          <h4>Your decision</h4>
          <p className="kyc-hint">Check the company details against the attached documents. A rejection needs a reason, which IT sees.</p>
          <label>Note or reason<textarea value={note} onChange={(event) => setNote(event.target.value)} rows={2} maxLength={500} /></label>
          <div className="kyc-actions">
            <button className="button button-dark" type="button" disabled={busy} onClick={() => void decide(true)}>Approve supplier</button>
            <button className="button button-outline kyc-reject" type="button" disabled={busy || !note.trim()} onClick={() => void decide(false)}>Reject</button>
          </div>
        </section>}
        {detail.status === "submitted" && !detail.can_decide && <p className="kyc-hint">Waiting for a manager with supplier approval permission. The person who opened the file cannot approve it.</p>}
        <h4>History</h4>
        <ol className="kyc-history">
          {detail.history.map((event, index) => <li key={index}>{when(event.at)} · <strong>{EVENTS[event.event] ?? event.event}</strong> by {event.actor}{event.note ? `: ${event.note}` : ""}</li>)}
        </ol>
      </>}
    </section>
  );
}

export default function SupplierKyc({ csrfToken }: { csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setOverview(await call<Overview>(`/api/suppliers/kyc/${filter ? `?status=${filter}` : ""}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Supplier onboarding could not be loaded.");
    }
  }, [filter]);
  useEffect(() => { void load(); }, [load]);

  const create = async (form: Form) => {
    setBusy(true); setError("");
    try {
      const created = await call<Detail>("/api/suppliers/kyc/", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify(form),
      });
      setCreating(false); setSelected(created.id); void load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The supplier could not be opened.");
    } finally { setBusy(false); }
  };

  return (
    <section className="content-card supplier-kyc" aria-labelledby="supplier-kyc-title">
      <header className="kyc-head">
        <div>
          <span className="eyebrow">PROCUREMENT · SUPPLIER ONBOARDING</span>
          <h2 id="supplier-kyc-title">Supplier onboarding (KYC)</h2>
          <p>IT opens a new local supplier with its company details and documents. A manager with approval permission checks and approves it. Purchase orders and receipts accept the supplier only after approval.</p>
        </div>
        {overview?.permissions.can_open && !creating && selected === null && <button className="button button-dark" type="button" onClick={() => setCreating(true)}>Open a new supplier</button>}
      </header>
      {error && <div className="alert" role="alert">{error}</div>}
      {!overview && !error && <p className="kyc-empty" role="status">Loading supplier files…</p>}
      {overview && creating && <SupplierForm initial={EMPTY} isNew busy={busy} onSubmit={(form) => void create(form)} onCancel={() => setCreating(false)} />}
      {overview && !creating && selected !== null && <SupplierFile id={selected} kinds={overview.document_kinds} csrfToken={csrfToken}
        onBack={() => setSelected(null)} onChanged={() => void load()} />}
      {overview && !creating && selected === null && <>
        <p className="kyc-role">{[
          overview.permissions.can_open ? "You can open suppliers." : "",
          overview.permissions.can_approve ? "You can approve suppliers." : "",
        ].filter(Boolean).join(" ") || "You can view supplier files."}</p>
        <div className="kyc-filters" role="group" aria-label="Filter by status">
          {FILTERS.map((item) => <button key={item.value} type="button" aria-pressed={filter === item.value}
            className={`kyc-filter${filter === item.value ? " active" : ""}`} onClick={() => setFilter(item.value)}>
            {item.label}{item.value ? ` (${overview.counts[item.value] ?? 0})` : ""}
          </button>)}
        </div>
        {overview.files.length === 0 ? <p className="kyc-empty">No supplier files{filter ? " with this status" : " yet"}.</p> : <div className="kyc-table-wrap"><table className="kyc-table">
          <thead><tr><th scope="col">Supplier</th><th scope="col">CR number</th><th scope="col">VAT number</th><th scope="col">Status</th><th scope="col">Opened by</th><th scope="col">Decision by</th></tr></thead>
          <tbody>{overview.files.map((row) => <tr key={row.id}>
            <th scope="row"><button type="button" className="kyc-link" onClick={() => setSelected(row.id)}>{row.legal_name}</button><small className="pw-mono">{row.supplier.code}</small></th>
            <td className="pw-mono">{row.cr_number}</td>
            <td className="pw-mono">{row.vat_number || "—"}</td>
            <td><span className={`kyc-status kyc-${row.status}`}>{row.status_label}</span></td>
            <td>{row.opened_by?.name}<small>{when(row.opened_at)}</small></td>
            <td>{row.decided_by ? <>{row.decided_by.name}<small>{when(row.decided_at)}</small></> : "—"}</td>
          </tr>)}</tbody>
        </table></div>}
      </>}
    </section>
  );
}
