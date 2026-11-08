import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Warning = { what: string; date: string; days: number; expired: boolean };
type Row = {
  id: number; staff_number: string; full_name: string; arabic_name: string; job_title: string; department: string; grade: string;
  nationality: string; manager: { id: number; name: string } | null; joined_on: string; contract: "unlimited" | "limited";
  contract_label: string; contract_end: string | null; probation_end: string | null; status: "active" | "notice" | "left";
  status_label: string; last_day: string | null; warnings: Warning[];
};
type Document = { id: number; kind: string; kind_label: string; number: string; issued_by: string; expires_on: string | null; note: string; added_by: string };
type Person = Row & {
  date_of_birth: string | null; work_email: string; mobile: string; leaving_reason: string; user: { id: number; username: string } | null;
  documents: Document[]; reports: { id: number; name: string; job_title: string }[];
  history: { at: string; by: string; change: string; what: string }[]; created_by: string; created_at: string; can_edit: boolean;
  portal_reads?: { post_id: number; title: string; kind_label: string; read_at: string }[]; portal_enabled?: boolean;
};
type Overview = {
  unit: { id: number; code: string; name: string }; staff: Row[];
  totals: { working: number; notice: number; expiring: number; expired: number };
  warnings: (Warning & { employee: { id: number; name: string } })[]; departments: string[];
  managers: { id: number; name: string; job_title: string }[]; users: { id: number; username: string; name: string }[];
  document_kinds: { value: string; label: string }[]; can_manage: boolean; policy: string;
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
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const headers = (csrfToken: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken });
const STATUS_CLASS: Record<Row["status"], string> = { active: "ex-booked", notice: "ex-waiting", left: "ex-rejected" };
const when = (warning: Warning) => warning.expired ? `ended ${-warning.days} days ago`
  : warning.days === 0 ? "today" : `in ${warning.days} day${warning.days === 1 ? "" : "s"}`;

const EMPTY = {
  full_name: "", arabic_name: "", job_title: "", department: "", grade: "", nationality: "", date_of_birth: "", work_email: "",
  mobile: "", joined_on: today(), contract: "unlimited", contract_end: "", probation_end: "", manager_id: "", user_id: "",
};
type Draft = typeof EMPTY;

function PersonForm({ overview, csrfToken, person, onDone, onCancel }: {
  overview: Overview; csrfToken: string; person: Person | null; onDone: (person: Person, message: string) => void; onCancel: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(person ? {
    full_name: person.full_name, arabic_name: person.arabic_name, job_title: person.job_title, department: person.department,
    grade: person.grade, nationality: person.nationality, date_of_birth: person.date_of_birth ?? "", work_email: person.work_email,
    mobile: person.mobile, joined_on: person.joined_on, contract: person.contract, contract_end: person.contract_end ?? "",
    probation_end: person.probation_end ?? "", manager_id: person.manager ? String(person.manager.id) : "",
    user_id: person.user ? String(person.user.id) : "",
  } : EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (field: keyof Draft) => (event: { target: { value: string } }) => setDraft({ ...draft, [field]: event.target.value });
  const users = person?.user ? [{ id: person.user.id, username: person.user.username, name: person.user.username }, ...overview.users] : overview.users;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    const body = {
      ...draft,
      manager_id: draft.manager_id ? Number(draft.manager_id) : null, user_id: draft.user_id ? Number(draft.user_id) : null,
      ...(person ? { action: "save" } : {}),
    };
    try {
      const saved = await call<Person>(person ? `/api/hr/staff/${person.id}/?unit=${overview.unit.id}` : `/api/hr/staff/?unit=${overview.unit.id}`,
        { method: "POST", headers: headers(csrfToken), body: JSON.stringify(body) });
      onDone(saved, person ? `${show(saved.full_name)}'s record is updated.` : `${show(saved.full_name)} is added as ${saved.staff_number}.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The record could not be saved.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form an-form" onSubmit={(event) => void submit(event)} aria-label={person ? "Change the record" : "Add a person"}>
    <h3>{person ? `Change ${show(person.full_name)}'s record` : "Add a person"}</h3>
    <div className="sp-fields an-grid">
      <label>Full name<input value={draft.full_name} maxLength={120} onChange={set("full_name")} required /></label>
      <label>Name in Arabic (optional)<input dir="rtl" lang="ar" value={draft.arabic_name} maxLength={120} onChange={set("arabic_name")} /></label>
      <label>Job title<input value={draft.job_title} maxLength={80} onChange={set("job_title")} required /></label>
      <label>Department<input list="hr-departments" value={draft.department} maxLength={60} onChange={set("department")} required /></label>
      <datalist id="hr-departments">{overview.departments.map((name) => <option key={name} value={name} />)}</datalist>
      <label>Grade (optional)<input value={draft.grade} maxLength={20} onChange={set("grade")} /></label>
      <label>Manager<select value={draft.manager_id} onChange={set("manager_id")}>
        <option value="">No manager</option>
        {overview.managers.filter((row) => row.id !== person?.id).map((row) => <option key={row.id} value={row.id}>{show(row.name)} · {row.job_title}</option>)}
      </select></label>
      <label>Nationality<input value={draft.nationality} maxLength={60} onChange={set("nationality")} required /></label>
      <label>Date of birth (optional)<input type="date" value={draft.date_of_birth} onChange={set("date_of_birth")} /></label>
      <label>Work email (optional)<input type="email" value={draft.work_email} maxLength={254} onChange={set("work_email")} /></label>
      <label>Mobile (optional)<input inputMode="tel" value={draft.mobile} maxLength={20} onChange={set("mobile")} placeholder="+974 5555 1234" /></label>
      <label>Joined on<input type="date" value={draft.joined_on} onChange={set("joined_on")} required /></label>
      <label>Contract<select value={draft.contract} onChange={set("contract")}>
        <option value="unlimited">Open-ended</option><option value="limited">Fixed term</option>
      </select></label>
      {draft.contract === "limited" && <label>Contract ends<input type="date" value={draft.contract_end} onChange={set("contract_end")} required /></label>}
      <label>Probation ends (optional)<input type="date" value={draft.probation_end} onChange={set("probation_end")} /></label>
      <label>ERP sign-in (optional)<select value={draft.user_id} onChange={set("user_id")}>
        <option value="">Not linked</option>
        {users.map((row) => <option key={row.id} value={row.id}>{row.username}{row.name !== row.username ? ` · ${show(row.name)}` : ""}</option>)}
      </select></label>
    </div>
    <p className="sp-hint">Pay is not kept here. It will live under payroll, with its own permission.</p>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy}>{person ? "Save changes" : "Add the person"}</button>
      <button className="button button-quiet" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </form>;
}

function Detail({ id, overview, csrfToken, onChanged, onEdit, onClose }: {
  id: number; overview: Overview; csrfToken: string; onChanged: (message: string) => void; onEdit: (person: Person) => void; onClose: () => void;
}) {
  const [person, setPerson] = useState<Person | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [doc, setDoc] = useState({ kind: "qid", number: "", issued_by: "", expires_on: "", note: "" });
  const [addingDoc, setAddingDoc] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [lastDay, setLastDay] = useState(today());
  const [reason, setReason] = useState("");
  const [signIn, setSignIn] = useState<{ username: string; password: string } | null>(null);
  const base = `/api/hr/staff/${id}/?unit=${overview.unit.id}`;
  const portalSignIn = async (action: "give" | "reset") => {
    if (!signIn || !person) return;
    setBusy(true); setError("");
    try {
      await call(`/api/portal/sign-ins/${person.id}/?unit=${overview.unit.id}`, { method: "POST", headers: headers(csrfToken),
        body: JSON.stringify({ action, username: signIn.username, password: signIn.password }) });
      setSignIn(null);
      setPerson(await call<Person>(base));
      onChanged(action === "give" ? `${show(person.full_name)} can now sign in to the HR Portal with ${signIn.username || person.work_email}. Give them the temporary password; they change it under My password.`
        : `${show(person.full_name)}'s temporary password is set. Give it to them; they change it under My password.`);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "That could not be done."); } finally { setBusy(false); }
  };
  useEffect(() => {
    let live = true;
    call<Person>(base).then((data) => { if (live) setPerson(data); })
      .catch((caught) => { if (live) setError(caught instanceof Error ? caught.message : "The record could not be loaded."); });
    return () => { live = false; };
  }, [base]);
  const run = async (body: Record<string, unknown>, message: string, after?: () => void) => {
    setBusy(true); setError("");
    try {
      setPerson(await call<Person>(base, { method: "POST", headers: headers(csrfToken), body: JSON.stringify(body) }));
      after?.();
      onChanged(message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That could not be done.");
    } finally { setBusy(false); }
  };
  if (!person) return <article className="sp-form an-detail" aria-label="Staff record">{error ? <div className="alert" role="alert">{error}</div> : <p className="sp-empty" role="status">Loading…</p>}</article>;
  const name = show(person.full_name);
  return <article className="sp-form an-detail hr-detail" aria-label={`Staff record of ${name}`}>
    <header className="mj-detail-head">
      <h3>{name}{person.arabic_name && <span className="hr-arabic" dir="rtl" lang="ar"> {person.arabic_name}</span>} <span className={`ex-status ${STATUS_CLASS[person.status]}`}>{person.status_label}</span></h3>
      <button className="button button-quiet" type="button" onClick={onClose}>Close</button>
    </header>
    <dl className="an-facts">
      <div><dt>Employee code</dt><dd className="pw-mono">{person.staff_number}</dd></div>
      <div><dt>Job</dt><dd>{person.job_title}{person.grade ? ` · grade ${person.grade}` : ""}</dd></div>
      <div><dt>Department</dt><dd>{person.department}</dd></div>
      <div><dt>Manager</dt><dd>{person.manager ? show(person.manager.name) : "None"}</dd></div>
      <div><dt>Joined</dt><dd>{person.joined_on}</dd></div>
      <div><dt>Contract</dt><dd>{person.contract_label}{person.contract_end ? ` to ${person.contract_end}` : ""}</dd></div>
      <div><dt>Probation ends</dt><dd>{person.probation_end ?? "None"}</dd></div>
      <div><dt>Nationality</dt><dd>{person.nationality}</dd></div>
      <div><dt>Date of birth</dt><dd>{person.date_of_birth ?? "Not given"}</dd></div>
      <div><dt>Contact</dt><dd>{[person.work_email, person.mobile].filter(Boolean).join(" · ") || "Not given"}</dd></div>
      <div><dt>ERP sign-in</dt><dd>{person.user?.username ?? "Not linked"}</dd></div>
      {person.last_day && <div><dt>Last day</dt><dd>{person.last_day} · {show(person.leaving_reason)}</dd></div>}
    </dl>
    {person.warnings.length > 0 && <ul className="hr-warnings" aria-label={`What ends soon for ${name}`}>
      {person.warnings.map((warning) => <li key={warning.what} className={warning.expired ? "hr-expired" : undefined}><strong>{warning.what}</strong> {warning.expired ? "ended" : "ends"} {warning.date} ({when(warning)})</li>)}
    </ul>}
    <h4>Documents</h4>
    {person.documents.length === 0 ? <p className="sp-hint">No documents recorded.</p>
      : <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label={`Documents of ${name}`}>
        <thead><tr><th scope="col">Document</th><th scope="col">Number</th><th scope="col">Issued by</th><th scope="col">Expires</th>{person.can_edit && <th scope="col"><span className="sr-only">Remove</span></th>}</tr></thead>
        <tbody>{person.documents.map((row) => <tr key={row.id}>
          <th scope="row">{row.kind_label}{row.note && <small>{show(row.note)}</small>}</th>
          <td className="pw-mono">{row.number}</td><td>{row.issued_by}</td><td>{row.expires_on ?? "No expiry"}</td>
          {person.can_edit && <td><button className="button button-quiet" type="button" disabled={busy} onClick={() => void run({ action: "remove_document", document_id: row.id }, `${row.kind_label} removed from ${name}'s record.`)}>Remove</button></td>}
        </tr>)}</tbody>
      </table></div>}
    {person.can_edit && person.status !== "left" && (addingDoc
      ? <div className="sp-fields an-grid hr-doc-form">
        <label>Kind<select value={doc.kind} onChange={(event) => setDoc({ ...doc, kind: event.target.value })}>
          {overview.document_kinds.map((kind) => <option key={kind.value} value={kind.value}>{kind.label}</option>)}
        </select></label>
        <label>Number<input value={doc.number} maxLength={40} onChange={(event) => setDoc({ ...doc, number: event.target.value })} /></label>
        <label>Issued by (optional)<input value={doc.issued_by} maxLength={60} onChange={(event) => setDoc({ ...doc, issued_by: event.target.value })} /></label>
        <label>Expires on<input type="date" value={doc.expires_on} onChange={(event) => setDoc({ ...doc, expires_on: event.target.value })} /></label>
        <div className="sp-actions">
          <button className="button button-dark" type="button" disabled={busy || !doc.number.trim()} onClick={() => void run({ action: "add_document", ...doc }, `Document saved on ${name}'s record.`, () => { setAddingDoc(false); setDoc({ kind: "qid", number: "", issued_by: "", expires_on: "", note: "" }); })}>Save the document</button>
          <button className="button button-quiet" type="button" onClick={() => setAddingDoc(false)}>Cancel</button>
        </div>
        <p className="sp-hint an-wide">A new document of the same kind replaces the old one, which stays in the history.</p>
      </div>
      : <div className="sp-actions"><button className="button button-outline" type="button" onClick={() => setAddingDoc(true)}>Add or renew a document</button></div>)}
    {person.reports.length > 0 && <><h4>Team</h4><p>{person.reports.map((row) => `${show(row.name)} (${row.job_title})`).join(", ")}</p></>}
    {person.can_edit && <div className="sp-actions">
      {person.status !== "left" && <button className="button button-dark" type="button" onClick={() => onEdit(person)}>Change the record</button>}
      {person.status === "active" && <button className="button button-outline" type="button" onClick={() => setLeaving(!leaving)}>Record leaving</button>}
      {person.status === "notice" && <button className="button button-outline" type="button" disabled={busy} onClick={() => void run({ action: "leaving", cancel: true }, `${name}'s notice is withdrawn.`)}>Withdraw notice</button>}
    </div>}
    {leaving && person.status === "active" && <div className="sp-fields an-grid hr-doc-form">
      <label>Last working day<input type="date" value={lastDay} onChange={(event) => setLastDay(event.target.value)} /></label>
      <label className="an-wide">Reason<input value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder="e.g. resigned, end of contract" /></label>
      <div className="sp-actions"><button className="button button-dark" type="button" disabled={busy || !reason.trim()} onClick={() => void run({ action: "leaving", last_day: lastDay, reason }, `${name}'s leaving is recorded.`, () => setLeaving(false))}>Record leaving</button></div>
    </div>}
    {person.can_edit && person.portal_enabled && person.status !== "left" && <div className="hr-portal-sign-in">
      <h4>HR Portal sign-in</h4>
      <p className="sp-hint">{person.user ? `Signs in as ${person.user.username}.` : "No sign-in yet. Give one so they can use the HR Portal with their company email."}</p>
      {!signIn ? <div className="sp-actions"><button className="button button-outline" type="button" onClick={() => setSignIn({ username: person.work_email, password: "" })}>
        {person.user ? "Set a temporary password" : "Give a portal sign-in"}</button></div>
        : <div className="sp-fields an-grid hr-doc-form">
          {!person.user && <label>Username (company email)<input value={signIn.username} maxLength={150} onChange={(event) => setSignIn({ ...signIn, username: event.target.value })} /></label>}
          <label>Temporary password<input type="text" autoComplete="off" value={signIn.password} onChange={(event) => setSignIn({ ...signIn, password: event.target.value })} /></label>
          <div className="sp-actions">
            <button className="button button-dark" type="button" disabled={busy || !signIn.password} onClick={() => void portalSignIn(person.user ? "reset" : "give")}>Save</button>
            <button className="button button-quiet" type="button" onClick={() => setSignIn(null)}>Cancel</button>
          </div>
        </div>}
    </div>}
    {(person.portal_reads?.length ?? 0) > 0 && <details className="hr-history"><summary>Procedures and memos read ({person.portal_reads!.length})</summary>
      <ul>{person.portal_reads!.map((row) => <li key={row.post_id}><span className="pw-mono">{row.read_at.slice(0, 16).replace("T", " ")}</span> confirmed reading the {row.kind_label.toLowerCase()} “{show(row.title)}”</li>)}</ul>
    </details>}
    <details className="hr-history"><summary>History ({person.history.length})</summary>
      <ul>{person.history.map((event, index) => <li key={index}><span className="pw-mono">{event.at.slice(0, 16).replace("T", " ")}</span> {event.by} {event.change}{event.what ? `: ${show(event.what)}` : ""}</li>)}</ul>
    </details>
    {error && <div className="alert" role="alert">{error}</div>}
  </article>;
}

export default function StaffRecords({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [status, setStatus] = useState("current");
  const [department, setDepartment] = useState("");
  const [search, setSearch] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<{ person: Person | null } | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [version, setVersion] = useState(0);

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    const query = new URLSearchParams({ unit: String(selectedUnitId), status, ...(department ? { department } : {}), ...(search.trim() ? { search: search.trim() } : {}) });
    try {
      setOverview(await call<Overview>(`/api/hr/staff/?${query}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Staff records could not be loaded.");
    }
  }, [selectedUnitId, status, department, search]);
  useEffect(() => {
    if (!search) { void load(); return; }
    const timer = setTimeout(() => void load(), 250);
    return () => clearTimeout(timer);
  }, [load, search]);
  const open = (id: number) => { setNotice(""); setEditing(null); setOpenId(id); };

  return <section className="content-card supplier-payments bank-cash staff-records" aria-labelledby="staff-records-title">
    <header className="sp-head">
      <span className="eyebrow">HR · STAFF RECORDS · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="staff-records-title">Staff records</h2>
      <p>Who works here, in which job and on what contract, with their QID, passport and other documents. The screen warns before a document or contract runs out. Only people with the staff records permission can open it, and every change is recorded.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading staff records…</p>}
    {overview && <>
      <ul className="bc-accounts ex-tiles" aria-label="Staff">
        <li className="bc-total"><span>Working</span><strong>{overview.totals.working}</strong><small>people in {overview.unit.name}</small></li>
        <li className="bc-total"><span>Serving notice</span><strong>{overview.totals.notice}</strong><small>leaving soon</small></li>
        <li className="bc-total"><span>Ending soon</span><strong>{overview.totals.expiring}</strong><small>documents, contracts and probation</small></li>
        <li className={`bc-total${overview.totals.expired ? " hr-alert" : ""}`}><span>Already expired</span><strong>{overview.totals.expired}</strong><small>documents and contracts</small></li>
      </ul>
      {overview.warnings.length > 0 && <div className="hr-coming" aria-label="Coming up">
        <h3>Coming up</h3>
        <ul>{overview.warnings.slice(0, 8).map((warning, index) => <li key={index} className={warning.expired ? "hr-expired" : undefined}>
          <button className="fa-link" type="button" onClick={() => open(warning.employee.id)}>{show(warning.employee.name)}</button>
          <span>{warning.what}</span><span className="pw-mono">{warning.date}</span><strong>{when(warning)}</strong>
        </li>)}</ul>
      </div>}
      <div className="bc-filters">
        <label>Show<select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="current">Current staff</option><option value="left">People who left</option><option value="all">Everyone</option>
        </select></label>
        <label>Department<select value={department} onChange={(event) => setDepartment(event.target.value)}>
          <option value="">All</option>{overview.departments.map((name) => <option key={name} value={name}>{name}</option>)}
        </select></label>
        <label>Search<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Name, employee code, job or document" /></label>
        {overview.can_manage && !editing && <div className="bc-buttons">
          <button className="button button-dark" type="button" onClick={() => { setNotice(""); setOpenId(null); setEditing({ person: null }); }}>Add a person</button>
        </div>}
      </div>
      {editing && <PersonForm key={editing.person?.id ?? "new"} overview={overview} csrfToken={csrfToken} person={editing.person} onCancel={() => setEditing(null)}
        onDone={(person, message) => { setEditing(null); setOpenId(person.id); setVersion(version + 1); setNotice(message); void load(); }} />}
      {!editing && openId !== null && <Detail key={`${openId}-${version}`} id={openId} overview={overview} csrfToken={csrfToken}
        onChanged={(message) => { setNotice(message); void load(); }} onEdit={(person) => setEditing({ person })} onClose={() => setOpenId(null)} />}
      {overview.staff.length === 0
        ? <p className="sp-empty">No one {search || department ? "matches" : status === "left" ? "has left yet" : "is recorded yet"}.</p>
        : <div className="sp-table-wrap"><table className="sp-table bc-table hr-table" aria-label="Staff">
          <thead><tr><th scope="col">Name</th><th scope="col">Employee code</th><th scope="col">Department</th><th scope="col">Manager</th>
            <th scope="col">Joined</th><th scope="col">Contract</th><th scope="col">Status</th></tr></thead>
          <tbody>{overview.staff.map((row) => <tr key={row.id} className={openId === row.id ? "mj-open" : undefined}>
            <th scope="row"><button className="fa-link" type="button" onClick={() => open(row.id)}>{show(row.full_name)}</button><small>{row.job_title}{row.grade ? ` · ${row.grade}` : ""}</small></th>
            <td className="pw-mono">{row.staff_number}</td>
            <td>{row.department}</td>
            <td>{row.manager ? show(row.manager.name) : ""}</td>
            <td>{row.joined_on}</td>
            <td>{row.contract_label}{row.contract_end && <small>to {row.contract_end}</small>}</td>
            <td><span className={`ex-status ${STATUS_CLASS[row.status]}`}>{row.status_label}</span>
              {row.last_day && <small>last day {row.last_day}</small>}
              {row.warnings.length > 0 && <small className={row.warnings.some((warning) => warning.expired) ? "hr-expired" : "hr-soon"}>{row.warnings.length} to check</small>}</td>
          </tr>)}</tbody>
        </table></div>}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
