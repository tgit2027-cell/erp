import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./customers.css";

type Row = {
  id: number; client_number: string | null; project_number: string | null; qid: string | null; qid_declined: boolean;
  title: string; title_label: string; title_ar: string; customer_type: string; customer_type_label: string; code: string; name: string; name_ar: string;
  contact_en: string; contact_ar: string; registered_at: string;
};
type Detail = Row & {
  quotes: { id: number; business_unit: string; kind: string; status: string; total: string; currency: string; created_at: string }[];
  quote_count: number;
  invoices: { id: number; number: string; business_unit: string; total: string; balance_due: string; currency: string; issued_at: string }[];
  invoice_count: number;
  credit_notes?: { id: number; number: string; business_unit: string; invoice_number: string; credit_total: string; refunded_amount: string; currency: string; issued_at: string }[];
  contact_changes: { changed_by: string; changed_at: string; fields: { field: string; before: string; after: string }[] }[];
};
type Title = { code: string; label: string; label_ar: string };
type TitleCount = { title: string; label: string; label_ar: string; customers: number; bought: number };
type RegisterFor = "client" | "project" | "both";
type Form = {
  qid: string; qid_declined: boolean; title: string; customer_type: string; register_for: RegisterFor;
  name: string; name_ar: string; contact_en: string; contact_ar: string;
};

const CUSTOMER_TYPES = [{ code: "individual", label: "Individual" }, { code: "company", label: "Company" }, { code: "government", label: "Government" }];
const EMPTY: Form = { qid: "", qid_declined: false, title: "", customer_type: "individual", register_for: "client", name: "", name_ar: "", contact_en: "", contact_ar: "" };
const FIELD_LABELS: Record<string, string> = {
  title: "Title", customer_type: "Customer type", name: "Name", name_ar: "Arabic name", contact_en: "Phone and contact", contact_ar: "Arabic contact", qid: "QID",
  client_number: "Client number", project_number: "Project number",
};
const REGISTER_FOR: { value: RegisterFor; label: string }[] = [
  { value: "client", label: "Retail (client number)" },
  { value: "project", label: "Projects & fitout (project number)" },
  { value: "both", label: "Both" },
];
const numbersOf = (row: Row) => [row.client_number, row.project_number].filter(Boolean).join(" · ");

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

const when = (value: string) => new Date(value).toLocaleString();
const day = (value: string) => new Date(value).toLocaleDateString();
const qidDigits = (value: string) => value.replace(/[\s-]/g, "");
const showName = (name: string) => name.replace(/^TEST ONLY\s*/, "");

function CustomerForm({ initial, isNew, hasQid, titles, busy, onSubmit, onCancel }: {
  initial: Form; isNew: boolean; hasQid: boolean; titles: Title[]; busy: boolean; onSubmit: (form: Form) => void; onCancel: () => void;
}) {
  const [form, setForm] = useState(initial);
  const set = (field: keyof Form) => (event: { target: HTMLInputElement | HTMLSelectElement }) => setForm({ ...form, [field]: event.target.value });
  const submit = (event: FormEvent) => { event.preventDefault(); onSubmit(form); };
  const qidReady = /^[23]\d{10}$/.test(qidDigits(form.qid));
  const hasContact = Boolean(form.contact_en.trim() || form.contact_ar.trim());
  const declined = isNew ? form.qid_declined : !hasQid && !form.qid.trim();
  const ready = isNew
    ? (form.qid_declined ? hasContact : qidReady)
    : (hasQid || (form.qid.trim() ? qidReady : hasContact));
  const qidHint = form.qid && !qidReady
    ? "A QID has 11 digits and starts with 2 or 3."
    : isNew ? "The QID is permanent. It cannot be changed after registration." : "Only if the customer has now agreed to give it. It cannot be changed afterwards.";
  return (
    <form className="cust-form" onSubmit={submit} aria-label={isNew ? "Register a new customer" : "Change customer details"}>
      <h3>{isNew ? "Register a new customer" : "Change name or contact"}</h3>
      {isNew && <fieldset className="cust-register-for"><legend>Register for</legend>
        {REGISTER_FOR.map((option) => <label key={option.value}><input type="radio" name="register_for" value={option.value}
          checked={form.register_for === option.value} onChange={() => setForm({ ...form, register_for: option.value })} />{option.label}</label>)}
        <small>A customer can have both numbers. Retail and project finances stay separate.</small>
      </fieldset>}
      {isNew && <p className="cust-hint">Numbers are given when the customer is registered. Giving a QID is the customer's choice.</p>}
      {hasQid
        ? <p className="cust-qid-locked">QID <strong>{form.qid}</strong> cannot be changed.</p>
        : <>
          <label>{isNew ? "Qatar ID (QID)" : "Add the QID (optional)"}<input value={form.qid} onChange={set("qid")} inputMode="numeric" maxLength={14}
            placeholder={form.qid_declined && isNew ? "Not given" : "11 digits"} disabled={isNew && form.qid_declined} aria-describedby="cust-qid-hint" />
            {!(isNew && form.qid_declined) && <small id="cust-qid-hint">{qidHint}</small>}</label>
          {isNew && <label className="cust-decline"><input type="checkbox" checked={form.qid_declined}
            onChange={(event) => setForm({ ...form, qid_declined: event.target.checked, qid: event.target.checked ? "" : form.qid })} />
            The customer declined to give a QID</label>}
          {declined && <p className="cust-decline-note" role="note">Without a QID the customer is found by customer number, name or phone, so a phone number or other contact detail is required.</p>}
        </>}
      <div className="cust-fields">
        <label>Title<select value={form.title} onChange={set("title")}>
          <option value="">No title</option>
          {titles.map((title) => <option key={title.code} value={title.code}>{title.label} · {title.label_ar}</option>)}
        </select></label>
        <label>Customer type<select value={form.customer_type} onChange={set("customer_type")}>
          {CUSTOMER_TYPES.map((kind) => <option key={kind.code} value={kind.code}>{kind.label}</option>)}
        </select></label>
        <label>Name<input value={form.name} onChange={set("name")} required maxLength={110} /></label>
        <label>Arabic name<input value={form.name_ar} onChange={set("name_ar")} dir="rtl" lang="ar" maxLength={120} /></label>
        <label>Phone and contact<input value={form.contact_en} onChange={set("contact_en")} maxLength={500} placeholder="+974 …" /></label>
        <label>Arabic contact<input value={form.contact_ar} onChange={set("contact_ar")} dir="rtl" lang="ar" maxLength={500} /></label>
      </div>
      <div className="cust-actions">
        <button className="button button-dark" type="submit" disabled={busy || !ready}>{isNew ? "Register customer" : "Save changes"}</button>
        <button className="button button-outline" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}

export default function Customers({ csrfToken }: { csrfToken: string }) {
  const [search, setSearch] = useState("");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [more, setMore] = useState(false);
  const [selected, setSelected] = useState<Detail | null>(null);
  const [mode, setMode] = useState<"none" | "new" | "edit">("none");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [titleFilter, setTitleFilter] = useState("");
  const [titles, setTitles] = useState<Title[]>([]);
  const [titleCounts, setTitleCounts] = useState<TitleCount[]>([]);

  const load = useCallback(async (term: string, title: string) => {
    try {
      const data = await call<{ customers: Row[]; more: boolean; titles: Title[]; title_counts: TitleCount[] }>(
        `/api/customers/?search=${encodeURIComponent(term)}${title ? `&title=${encodeURIComponent(title)}` : ""}`,
      );
      setRows(data.customers);
      setMore(data.more);
      setTitles(data.titles ?? []);
      setTitleCounts(data.title_counts ?? []);
      setError("");
    } catch (failure) {
      setRows([]);
      setError((failure as Error).message);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(search.trim(), titleFilter), search ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [search, titleFilter, load]);

  const open = async (id: number) => {
    setBusy(true);
    try {
      setSelected(await call<Detail>(`/api/customers/${id}/`));
      setMode("none");
      setError("");
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const addNumber = async (kind: "client" | "project") => {
    if (!selected) return;
    setBusy(true);
    setNotice("");
    try {
      const detail = await call<Detail>(`/api/customers/${selected.id}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ add_number: kind }),
      });
      setSelected(detail);
      setError("");
      setNotice(`${showName(detail.name)} now has ${kind} number ${kind === "client" ? detail.client_number : detail.project_number}. Both numbers stay linked to this customer.`);
      void load(search.trim(), titleFilter);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const save = async (form: Form) => {
    setBusy(true);
    setNotice("");
    try {
      const isNew = mode === "new";
      const contact = { title: form.title, customer_type: form.customer_type, name: form.name, name_ar: form.name_ar, contact_en: form.contact_en, contact_ar: form.contact_ar };
      const body = isNew
        ? { ...contact, register_for: form.register_for, ...(form.qid_declined ? { qid_declined: true } : { qid: form.qid }) }
        : { ...contact, ...(!selected?.qid && form.qid.trim() ? { qid: form.qid } : {}) };
      const detail = await call<Detail>(isNew ? "/api/customers/" : `/api/customers/${selected?.id}/`, {
        method: isNew ? "POST" : "PATCH",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify(body),
      });
      setSelected(detail);
      setMode("none");
      setError("");
      setNotice(isNew
        ? `Registered ${showName(detail.name)} as ${numbersOf(detail)}${detail.qid ? ` with QID ${detail.qid}` : ", without a QID"}.`
        : "Customer details saved. The earlier details are kept in the change history.");
      void load(search.trim(), titleFilter);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="customers" aria-label="Customers">
      <div className="cust-head">
        <label className="cust-search">Find a customer by client or project number, QID, name or phone
          <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} maxLength={80} placeholder="e.g. C-000123, P-000045, 28463400123 or +974 5555" />
        </label>
        <label className="cust-title-filter">Title
          <select value={titleFilter} onChange={(event) => setTitleFilter(event.target.value)}>
            <option value="">All titles</option>
            <option value="none">No title</option>
            {titles.map((title) => <option key={title.code} value={title.code}>{title.label}</option>)}
          </select>
        </label>
        <button className="button button-dark" type="button" onClick={() => { setMode("new"); setSelected(null); setNotice(""); }}>Register a customer</button>
      </div>
      {error && <p className="cust-error" role="alert">{error}</p>}
      {notice && <p className="cust-notice" role="status">{notice}</p>}

      {titleCounts.some((row) => row.customers > 0) && <div className="cust-title-counts" aria-label="Customers by title">
        <span className="eyebrow">CUSTOMERS BY TITLE</span>
        <ul>{titleCounts.filter((row) => row.customers > 0).map((row) => (
          <li key={row.title || "none"}><strong>{row.label}</strong><span>{row.customers} registered</span><span>{row.bought} bought</span></li>
        ))}</ul>
        <small>"Bought" means at least one invoice in your business units.</small>
      </div>}

      {mode === "new" && <CustomerForm initial={{ ...EMPTY, qid: /^\d+$/.test(qidDigits(search)) ? qidDigits(search) : "" }} isNew hasQid={false} titles={titles} busy={busy}
        onSubmit={save} onCancel={() => setMode("none")} />}

      <div className="cust-layout">
        <div className="cust-table-wrap">
          <table className="cust-table">
            <thead><tr><th scope="col">Client no.</th><th scope="col">Project no.</th><th scope="col">QID</th><th scope="col">Name</th><th scope="col">Phone and contact</th><th scope="col">Registered</th></tr></thead>
            <tbody>
              {rows === null && <tr><td colSpan={6}>Loading customers…</td></tr>}
              {rows?.length === 0 && <tr><td colSpan={6}>{search ? "No customer matches. Check the number, QID or phone, or register a new customer." : "No customers yet."}</td></tr>}
              {rows?.map((row) => (
                <tr key={row.id} className={selected?.id === row.id ? "is-selected" : undefined}>
                  <td>{row.client_number ? <button type="button" className="cust-link" onClick={() => void open(row.id)}>{row.client_number}</button> : "—"}</td>
                  <td>{row.project_number ? <button type="button" className="cust-link" onClick={() => void open(row.id)}>{row.project_number}</button> : "—"}</td>
                  <td className="cust-mono">{row.qid ?? <span className="cust-declined">Declined</span>}</td>
                  <td>{row.title_label && <span className="cust-title">{row.title_label} </span>}{showName(row.name)}{row.name_ar && <small lang="ar" dir="rtl">{row.name_ar}</small>}</td>
                  <td>{row.contact_en || "—"}</td>
                  <td>{day(row.registered_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {more && <p className="cust-hint">Showing the first 50. Type more of the number, QID, name or phone to narrow the list.</p>}
        </div>

        {selected && (
          <article className="cust-file" aria-label={`Customer ${numbersOf(selected)}`}>
            <div className="cust-file-head">
              <div><span className="eyebrow">CUSTOMER FILE</span><h3>{selected.title_label && `${selected.title_label} `}{showName(selected.name)}</h3>
                {selected.name_ar && <p lang="ar" dir="rtl">{selected.name_ar}</p>}</div>
              {mode !== "edit" && <button className="button button-outline" type="button" onClick={() => setMode("edit")}>{selected.qid ? "Change name or contact" : "Change details or add QID"}</button>}
            </div>
            <dl className="cust-facts">
              <div><dt>Client number (retail)</dt><dd className="cust-mono">{selected.client_number
                ?? <button type="button" className="cust-link" disabled={busy} onClick={() => void addNumber("client")}>Give a client number</button>}</dd></div>
              <div><dt>Project number (projects & fitout)</dt><dd className="cust-mono">{selected.project_number
                ?? <button type="button" className="cust-link" disabled={busy} onClick={() => void addNumber("project")}>Give a project number</button>}</dd></div>
              <div><dt>QID{selected.qid ? " (permanent)" : ""}</dt><dd className="cust-mono">{selected.qid ?? <span className="cust-declined">Not given. The customer declined.</span>}</dd></div>
              <div><dt>Customer type</dt><dd>{selected.customer_type_label || "Individual"}</dd></div>
              <div><dt>Phone and contact</dt><dd>{selected.contact_en || "—"}</dd></div>
              <div><dt>Arabic contact</dt><dd lang="ar" dir="rtl">{selected.contact_ar || "—"}</dd></div>
              <div><dt>Registered</dt><dd>{day(selected.registered_at)}</dd></div>
            </dl>
            {mode === "edit" && <CustomerForm key={selected.id} isNew={false} hasQid={Boolean(selected.qid)} titles={titles} busy={busy} onSubmit={save} onCancel={() => setMode("none")}
              initial={{ qid: selected.qid ?? "", qid_declined: selected.qid_declined, title: selected.title, customer_type: selected.customer_type ?? "individual", register_for: "client", name: showName(selected.name), name_ar: selected.name_ar, contact_en: selected.contact_en, contact_ar: selected.contact_ar }} />}

            <h4>Quotes ({selected.quote_count})</h4>
            {selected.quotes.length === 0 ? <p className="cust-hint">No quotes in your business units yet.</p> : (
              <table className="cust-table cust-history"><thead><tr><th scope="col">Quote</th><th scope="col">Unit</th><th scope="col">Type</th><th scope="col">Status</th><th scope="col">Total</th><th scope="col">Date</th></tr></thead>
                <tbody>{selected.quotes.map((quote) => <tr key={quote.id}><td>#{quote.id}</td><td>{quote.business_unit}</td><td>{quote.kind}</td><td>{quote.status}</td><td>{quote.currency} {quote.total}</td><td>{day(quote.created_at)}</td></tr>)}</tbody></table>
            )}
            <h4>Invoices ({selected.invoice_count})</h4>
            {selected.invoices.length === 0 ? <p className="cust-hint">No invoices in your business units yet.</p> : (
              <table className="cust-table cust-history"><thead><tr><th scope="col">Invoice</th><th scope="col">Unit</th><th scope="col">Total</th><th scope="col">Balance due</th><th scope="col">Issued</th></tr></thead>
                <tbody>{selected.invoices.map((invoice) => <tr key={invoice.id}><td>{invoice.number}</td><td>{invoice.business_unit}</td><td>{invoice.currency} {invoice.total}</td><td>{invoice.currency} {invoice.balance_due}</td><td>{day(invoice.issued_at)}</td></tr>)}</tbody></table>
            )}
            {(selected.credit_notes?.length ?? 0) > 0 && <>
              <h4>Credit notes for returns</h4>
              <table className="cust-table cust-history"><thead><tr><th scope="col">Credit note</th><th scope="col">Unit</th><th scope="col">Invoice</th><th scope="col">Credit</th><th scope="col">Refunded</th><th scope="col">Issued</th></tr></thead>
                <tbody>{selected.credit_notes!.map((note) => <tr key={note.id}><td>{note.number}</td><td>{note.business_unit}</td><td>{note.invoice_number}</td><td>{note.currency} {note.credit_total}</td><td>{note.currency} {note.refunded_amount}</td><td>{day(note.issued_at)}</td></tr>)}</tbody></table>
            </>}
            <h4>Contact changes</h4>
            {selected.contact_changes.length === 0 ? <p className="cust-hint">No changes since registration.</p> : (
              <ul className="cust-changes">{selected.contact_changes.map((change, index) => (
                <li key={index}><strong>{when(change.changed_at)} · {change.changed_by}</strong>
                  {change.fields.map((field) => <span key={field.field}>{FIELD_LABELS[field.field] ?? field.field}: {field.before || "—"} → {field.after || "—"}</span>)}</li>
              ))}</ul>
            )}
          </article>
        )}
      </div>
    </section>
  );
}
