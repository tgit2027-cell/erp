import { useCallback, useEffect, useState } from "react";
import { call, errorText, qar, short, show, TONE, useAction, type Bond, type Person } from "./projects-shared";

type TenderRow = {
  id: number; number: string; client_name: string; customer_id: number | null; project_name: string; contact_person: string; email: string; phone: string;
  assignee: Person | null; responsible: Person | null; start_date: string; due_date: string; overdue: boolean; quote_reference: string; revision: string;
  brands: string; exworks: string; rate: string; total: string; status: string; status_label: string; progress: string; progress_label: string;
  remarks: string; client_order: string; decided_on: string; updated_by: string; updated_at: string; projects: { id: number; number: string }[];
  bonds: Bond[]; can_edit: boolean;
};
type Board = {
  today: string; groups: { status: string; label: string; rows: TenderRow[] }[];
  tiles: { open: number; open_value: string; won_this_year: string; lost_this_year: string; win_rate: number | null; overdue: number; bonds_expiring: number };
  statuses: { id: string; label: string }[]; progress: { id: string; label: string }[]; people: Person[]; can_edit: boolean; can_bond: boolean;
};
type BondBoard = {
  today: string; reminder_days: number; bonds: Bond[];
  tiles: { active: number; active_amount: string; expiring: number; expired: number; claimed: number };
  by_kind: { kind: string; label: string; count: number; amount: string }[];
  kinds: { id: string; label: string }[]; statuses: { id: string; label: string }[];
  tenders: { id: number; label: string }[]; projects: { id: number; label: string }[]; can_bond: boolean;
};

const BLANK = { client_name: "", customer_id: "", project_name: "", contact_person: "", email: "", phone: "", assignee_id: "", responsible_id: "", start_date: "", due_date: "", quote_reference: "", revision: "", brands: "", exworks: "", rate: "", total: "", remarks: "", status: "submitted" };

function TenderForm({ board, unitId, csrfToken, customers, tender, onDone, onClose }: { board: Board; unitId: number; csrfToken: string; customers: { id: number; name: string }[]; tender?: TenderRow; onDone: (message: string) => void; onClose: () => void }) {
  const [form, setForm] = useState(tender ? {
    client_name: tender.client_name, customer_id: tender.customer_id ? String(tender.customer_id) : "", project_name: tender.project_name, contact_person: tender.contact_person,
    email: tender.email, phone: tender.phone, assignee_id: tender.assignee ? String(tender.assignee.id) : "", responsible_id: tender.responsible ? String(tender.responsible.id) : "",
    start_date: tender.start_date, due_date: tender.due_date, quote_reference: tender.quote_reference, revision: tender.revision, brands: tender.brands, exworks: tender.exworks,
    rate: tender.rate, total: tender.total, remarks: tender.remarks, status: tender.status,
  } : BLANK);
  const { busy, error, send } = useAction(csrfToken, unitId, onDone);
  const field = (key: keyof typeof form, label: string, extra: Record<string, unknown> = {}) => <label>{label}<input value={form[key]} onChange={(event) => setForm({ ...form, [key]: event.target.value })} {...extra} /></label>;
  const body = { ...form, customer_id: form.customer_id ? Number(form.customer_id) : null, assignee_id: form.assignee_id ? Number(form.assignee_id) : null, responsible_id: form.responsible_id ? Number(form.responsible_id) : null };
  return <form className="sp-form fac-form" aria-label={tender ? `Edit ${tender.number}` : "New tender"} onSubmit={(event) => {
    event.preventDefault();
    const { status, ...details } = body;
    void send(tender ? `/api/projects/tenders/${tender.id}/` : "/api/projects/tenders/", "POST", tender ? details : { ...details, status }, () => tender ? `${tender.number} is updated.` : "The tender is added to the list.").then((ok) => ok && onClose());
  }}>
    <h3>{tender ? `Edit ${tender.number}` : "New tender"}</h3>
    <div className="sp-fields fac-grid">
      {field("client_name", "Client (name)", { required: true, maxLength: 160 })}
      <label>Client in the ERP<select value={form.customer_id} onChange={(event) => setForm({ ...form, customer_id: event.target.value })}><option value="">Not yet a customer</option>{customers.map((row) => <option key={row.id} value={row.id}>{show(row.name)}</option>)}</select></label>
      {field("project_name", "Project name", { maxLength: 200 })}
      {field("contact_person", "Contact person", { maxLength: 120 })}
      {field("email", "Email", { type: "email", maxLength: 120 })}
      {field("phone", "Phone", { maxLength: 60 })}
      <label>Assignee (prepares it)<select value={form.assignee_id} onChange={(event) => setForm({ ...form, assignee_id: event.target.value })}><option value="">Choose…</option>{board.people.map((row) => <option key={row.id} value={row.id}>{show(row.name)}</option>)}</select></label>
      <label>Project responsible<select value={form.responsible_id} onChange={(event) => setForm({ ...form, responsible_id: event.target.value })}><option value="">Choose…</option>{board.people.map((row) => <option key={row.id} value={row.id}>{show(row.name)}</option>)}</select></label>
      {field("start_date", "Start date", { type: "date" })}
      {field("due_date", "Due date", { type: "date" })}
      {field("quote_reference", "Quote reference", { maxLength: 80 })}
      {field("revision", "Revision", { maxLength: 10 })}
      {field("brands", "Brands", { maxLength: 200 })}
      {field("exworks", "Ex-works value", { inputMode: "decimal" })}
      {field("rate", "Rate (exchange)", { inputMode: "decimal" })}
      {field("total", "Project total (QAR)", { inputMode: "decimal" })}
      {!tender && <label>Status<select value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value })}><option value="preparing">Being prepared</option><option value="submitted">Submitted</option></select></label>}
      <label className="fac-wide">Remarks<input value={form.remarks} maxLength={300} onChange={(event) => setForm({ ...form, remarks: event.target.value })} /></label>
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy}>{tender ? "Save" : "Add the tender"}</button><button className="button button-quiet" type="button" onClick={onClose}>Cancel</button></div>
  </form>;
}

function TenderLine({ tender, board, unitId, csrfToken, customers, onDone }: { tender: TenderRow; board: Board; unitId: number; csrfToken: string; customers: { id: number; name: string }[]; onDone: (message: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [open, setOpen] = useState(false);
  const [move, setMove] = useState({ status: tender.status, client_order: tender.client_order, remarks: tender.remarks, progress: tender.progress });
  const { busy, error, send } = useAction(csrfToken, unitId, onDone);
  const save = (body: Record<string, unknown>, message: string) => void send(`/api/projects/tenders/${tender.id}/`, "POST", body, () => message);
  return <>
    <tr className={open ? "fac-open" : undefined}>
      <th scope="row"><button type="button" className="fac-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>{show(tender.client_name)}</button><small>{tender.number}</small></th>
      <td>{show(tender.project_name) || "–"}<small>{show(tender.contact_person)}</small></td>
      <td>{tender.email ? <a href={`mailto:${tender.email}`}>{tender.email}</a> : "–"}<small>{tender.phone}</small></td>
      <td>{tender.assignee ? show(tender.assignee.name) : "–"}<small>{tender.responsible ? show(tender.responsible.name) : ""}</small></td>
      <td>{tender.start_date || "–"}<small className={tender.overdue ? "fac-late" : undefined}>due {tender.due_date || "–"}</small></td>
      <td className="pw-mono">{tender.quote_reference || "–"}<small>{tender.revision && `rev ${tender.revision}`}</small></td>
      <td>{show(tender.brands) || "–"}</td>
      <td className="bc-num">{tender.exworks ? qar(tender.exworks) : "–"}<small>{tender.rate && `rate ${tender.rate}`}</small></td>
      <td className="bc-num">{qar(tender.total)}</td>
      <td><span className={`sp-status sp-status-${TONE[tender.status]}`}>{tender.status_label}</span>{tender.progress_label && <small>{tender.progress_label}</small>}{tender.projects.map((row) => <small key={row.id}>{row.number}</small>)}</td>
      <td>{show(tender.remarks) || "–"}<small>{tender.updated_at.slice(0, 10)} · {tender.updated_by}</small></td>
    </tr>
    {open && <tr className="fac-detail-row"><td colSpan={11}><div className="fac-detail">
      {editing ? <TenderForm board={board} unitId={unitId} csrfToken={csrfToken} customers={customers} tender={tender} onDone={onDone} onClose={() => setEditing(false)} /> : <>
        <p className="fac-meta">{tender.client_order && `Client order ${tender.client_order} · `}{tender.decided_on && `decided ${tender.decided_on} · `}{tender.bonds.length} bond{tender.bonds.length === 1 ? "" : "s"}{tender.bonds.map((bond) => ` · ${bond.kind_label} ${bond.reference} QAR ${qar(bond.amount)} to ${bond.expires_on || "–"} (${bond.status_label.toLowerCase()})`).join("")}</p>
        {tender.can_edit && <div className="fac-inline" aria-label={`Update ${tender.number}`}>
          <select aria-label="Status" value={move.status} onChange={(event) => setMove({ ...move, status: event.target.value })}>{board.statuses.map((row) => <option key={row.id} value={row.id}>{row.label}</option>)}</select>
          {move.status === "won" && <><input aria-label="Client order" placeholder="Client order or LOA" value={move.client_order} onChange={(event) => setMove({ ...move, client_order: event.target.value })} />
            <select aria-label="Internal status" value={move.progress} onChange={(event) => setMove({ ...move, progress: event.target.value })}><option value="">Internal status…</option>{board.progress.map((row) => <option key={row.id} value={row.id}>{row.label}</option>)}</select></>}
          <input aria-label="Remarks" placeholder="Remarks" value={move.remarks} onChange={(event) => setMove({ ...move, remarks: event.target.value })} />
          <button className="button button-dark" type="button" disabled={busy} onClick={() => save({ ...move, progress: move.status === "won" ? move.progress : "" }, `${tender.number} is updated.`)}>Save</button>
          <button className="button button-outline" type="button" onClick={() => setEditing(true)}>Edit details</button>
        </div>}
        {error && <div className="alert" role="alert">{error}</div>}
      </>}
    </div></td></tr>}
  </>;
}

export function TendersView({ unitId, csrfToken, customers }: { unitId: number; csrfToken: string; customers: { id: number; name: string }[] }) {
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [adding, setAdding] = useState(false);
  const [filter, setFilter] = useState("");
  const load = useCallback(async () => {
    try { setBoard(await call<Board>(`/api/projects/tenders/?unit=${unitId}`)); setError(""); }
    catch (caught) { setError(errorText(caught, "Tenders could not be loaded.")); }
  }, [unitId]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); void load(); };
  if (!board) return error ? <div className="alert" role="alert">{error}</div> : <p className="sp-empty" role="status">Loading…</p>;
  const text = filter.trim().toLowerCase();
  const match = (row: TenderRow) => !text || `${row.client_name} ${row.project_name} ${row.quote_reference} ${row.contact_person} ${row.brands} ${row.number}`.toLowerCase().includes(text);
  return <>
    {notice && <p className="sp-notice" role="status">{show(notice)}</p>}
    <dl className="sp-totals" aria-label="Tenders at a glance">
      <div><dt>Open tenders</dt><dd>{board.tiles.open} · QAR {short(board.tiles.open_value)}</dd></div>
      <div><dt>Won this year</dt><dd>QAR {short(board.tiles.won_this_year)}</dd></div>
      <div><dt>Lost this year</dt><dd>QAR {short(board.tiles.lost_this_year)}</dd></div>
      <div><dt>Win rate</dt><dd>{board.tiles.win_rate === null ? "–" : `${board.tiles.win_rate}%`}</dd></div>
      <div><dt>Past due, not submitted</dt><dd className={board.tiles.overdue ? "fac-late" : undefined}>{board.tiles.overdue}</dd></div>
      <div><dt>Bonds expiring soon</dt><dd className={board.tiles.bonds_expiring ? "fac-late" : undefined}>{board.tiles.bonds_expiring}</dd></div>
    </dl>
    <div className="fac-inline">
      <input aria-label="Find a tender" placeholder="Find by client, project, quote or brand" value={filter} onChange={(event) => setFilter(event.target.value)} />
      {board.can_edit && !adding && <button className="button button-dark" type="button" onClick={() => setAdding(true)}>New tender</button>}
    </div>
    {adding && <TenderForm board={board} unitId={unitId} csrfToken={csrfToken} customers={customers} onDone={done} onClose={() => setAdding(false)} />}
    {board.groups.filter((group) => group.rows.length || group.status === "submitted").map((group) => {
      const rows = group.rows.filter(match);
      return <section key={group.status} className="prj-group" aria-label={group.label}>
        <h3><span className={`sp-status sp-status-${TONE[group.status]}`}>{group.label}</span> <small>{rows.length} · QAR {qar(rows.reduce((sum, row) => sum + Number(row.total), 0))}</small></h3>
        {rows.length === 0 ? <p className="sp-empty">None.</p> : <div className="sp-table-wrap"><table className="sp-table bc-table fac-table prj-tenders" aria-label={`${group.label} tenders`}>
          <thead><tr><th scope="col">Name</th><th scope="col">Project · contact</th><th scope="col">Email · phone</th><th scope="col">Assignee · responsible</th><th scope="col">Start · due</th><th scope="col">Quote · rev</th><th scope="col">Brands</th><th scope="col" className="bc-num">Ex-works · rate</th><th scope="col" className="bc-num">Project total</th><th scope="col">Status</th><th scope="col">Remarks · last update</th></tr></thead>
          <tbody>{rows.map((row) => <TenderLine key={`${row.id}-${row.updated_at}`} tender={row} board={board} unitId={unitId} csrfToken={csrfToken} customers={customers} onDone={done} />)}</tbody>
        </table></div>}
      </section>;
    })}
  </>;
}

export function BondsView({ unitId, csrfToken }: { unitId: number; csrfToken: string }) {
  const [board, setBoard] = useState<BondBoard | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [show_, setShow] = useState("active");
  const blank = { for: "", kind: "bid_bond", reference: "", issuer: "", amount: "", issued_on: "", expires_on: "", note: "" };
  const [form, setForm] = useState(blank);
  const load = useCallback(async () => {
    try { setBoard(await call<BondBoard>(`/api/projects/bonds/?unit=${unitId}`)); setError(""); }
    catch (caught) { setError(errorText(caught, "Bonds could not be loaded.")); }
  }, [unitId]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); void load(); };
  const { busy, error: actionError, send } = useAction(csrfToken, unitId, done);
  if (!board) return error ? <div className="alert" role="alert">{error}</div> : <p className="sp-empty" role="status">Loading…</p>;
  const rows = board.bonds.filter((bond) => show_ === "all" || (show_ === "active" ? bond.status === "active" : bond.expiring || bond.expired));
  const [owner, id] = form.for.split(":");
  return <>
    {notice && <p className="sp-notice" role="status">{show(notice)}</p>}
    <dl className="sp-totals" aria-label="Bonds at a glance">
      <div><dt>Active bonds and guarantees</dt><dd>{board.tiles.active} · QAR {short(board.tiles.active_amount)}</dd></div>
      {board.by_kind.map((row) => <div key={row.kind}><dt>{row.label}</dt><dd>{row.count} · QAR {short(row.amount)}</dd></div>)}
      <div><dt>Expiring in {board.reminder_days} days</dt><dd className={board.tiles.expiring ? "fac-late" : undefined}>{board.tiles.expiring}</dd></div>
      <div><dt>Past their date, not returned</dt><dd className={board.tiles.expired ? "fac-late" : undefined}>{board.tiles.expired}</dd></div>
    </dl>
    <div className="an-tabs" role="tablist" aria-label="Which bonds">
      {[["active", "Active"], ["attention", "Need attention"], ["all", "All"]].map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={show_ === key} className={`an-tab${show_ === key ? " an-tab-on" : ""}`} onClick={() => setShow(key)}>{label}</button>)}
    </div>
    {rows.length === 0 ? <p className="sp-empty">No bonds here.</p> : <div className="sp-table-wrap"><table className="sp-table bc-table fac-table" aria-label="Bonds">
      <thead><tr><th scope="col">Bond</th><th scope="col">For</th><th scope="col">Bank</th><th scope="col" className="bc-num">Amount</th><th scope="col">Valid</th><th scope="col">Status</th></tr></thead>
      <tbody>{rows.map((bond) => <tr key={bond.id}>
        <th scope="row">{bond.kind_label}<small className="pw-mono">{bond.reference}</small></th>
        <td>{show(bond.for ?? "")}</td>
        <td>{show(bond.issuer) || "–"}</td>
        <td className="bc-num">{qar(bond.amount)}</td>
        <td>{bond.issued_on || "–"} → {bond.expires_on || "open"}{bond.expiring && <small className="fac-late">{bond.days_left} days left</small>}{bond.expired && <small className="fac-late">past its date</small>}</td>
        <td><span className={`sp-status sp-status-${TONE[bond.status]}`}>{bond.status_label}</span>
          {board.can_bond && bond.status === "active" && <span className="lv-actions">
            <button className="button button-outline" type="button" disabled={busy} onClick={() => void send(`/api/projects/bonds/${bond.id}/`, "POST", { status: "released" }, () => `${bond.reference} is released back to us.`)}>Mark released</button>
            <button className="button button-quiet" type="button" disabled={busy} onClick={() => void send(`/api/projects/bonds/${bond.id}/`, "POST", { status: "claimed" }, () => `${bond.reference} is marked claimed by the client.`)}>Mark claimed</button>
          </span>}</td>
      </tr>)}</tbody>
    </table></div>}
    {board.can_bond && <form className="sp-form fac-form" aria-label="Add a bond" onSubmit={(event) => {
      event.preventDefault();
      void send("/api/projects/bonds/", "POST", { ...form, for: undefined, [owner === "p" ? "project_id" : "tender_id"]: Number(id) }, () => "The bond is added.").then((ok) => ok && setForm(blank));
    }}>
      <h3>Add a bond, guarantee cheque or warranty</h3>
      <div className="sp-fields fac-grid">
        <label>For<select required value={form.for} onChange={(event) => setForm({ ...form, for: event.target.value })}><option value="">Choose a tender or project…</option>
          <optgroup label="Tenders">{board.tenders.map((row) => <option key={`t${row.id}`} value={`t:${row.id}`}>{show(row.label)}</option>)}</optgroup>
          <optgroup label="Projects">{board.projects.map((row) => <option key={`p${row.id}`} value={`p:${row.id}`}>{show(row.label)}</option>)}</optgroup></select></label>
        <label>Kind<select value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}>{board.kinds.map((row) => <option key={row.id} value={row.id}>{row.label}</option>)}</select></label>
        <label>Bond or cheque number<input required value={form.reference} onChange={(event) => setForm({ ...form, reference: event.target.value })} /></label>
        <label>Bank or issuer<input value={form.issuer} onChange={(event) => setForm({ ...form, issuer: event.target.value })} /></label>
        <label>Amount (QAR)<input inputMode="decimal" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} /></label>
        <label>Issued on<input type="date" value={form.issued_on} onChange={(event) => setForm({ ...form, issued_on: event.target.value })} /></label>
        <label>Valid until<input type="date" value={form.expires_on} onChange={(event) => setForm({ ...form, expires_on: event.target.value })} /></label>
        <label className="fac-wide">Note<input value={form.note} maxLength={300} onChange={(event) => setForm({ ...form, note: event.target.value })} /></label>
      </div>
      {actionError && <div className="alert" role="alert">{actionError}</div>}
      <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy}>Add</button></div>
    </form>}
  </>;
}
