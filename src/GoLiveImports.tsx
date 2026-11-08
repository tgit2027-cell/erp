import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./go-live-imports.css";

type Kind = "customers" | "suppliers" | "items" | "opening_stock" | "trial_balance" | "customer_balances" | "supplier_balances" | "fixed_assets" | "staff_loans";
type Row = Record<string, string | boolean | number | null>;
type Batch = {
  id: number; kind: Kind; kind_label: string; status: "waiting" | "imported" | "rejected" | "withdrawn"; status_label: string;
  file_name: string; row_count: number; created: number; prepared_by: string; prepared_at: string; decided_by: string;
  decided_at: string; decision_note: string; balances_on: string; sample: Row[]; can_approve: boolean; can_withdraw: boolean;
};
type Overview = {
  kinds: { kind: Kind; label: string; needs_date: boolean; columns: { heading: string; required: boolean; help: string }[] }[];
  waiting: Batch[]; recent: Batch[]; can_prepare: boolean; can_approve: boolean; policy: string;
  opening_left: { business_unit: string; left: string }[];
};
type Preview = {
  kind: Kind; rows: number; good: number; bad: number; more_problems: number;
  problems: { row: number; problems: string[] }[]; sample: Row[];
};

const BASE = "/api/governance/imports/";
async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The request could not be completed.");
  return data as T;
}
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const count = (number: number, word: string) => `${number.toLocaleString("en-US")} ${word}${number === 1 ? "" : "s"}`;
const when = (value: string) => value ? new Date(value).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
const TONE: Record<Batch["status"], string> = { waiting: "waiting", imported: "approved", rejected: "rejected", withdrawn: "done" };
const SAMPLE: Record<Kind, [string, string][]> = {
  customers: [["name", "Name"], ["qid", "QID"], ["contact_en", "Contact"], ["register_for", "Registered for"], ["client_number", "Client no."], ["project_number", "Project no."]],
  suppliers: [["code", "Code"], ["legal_name", "Legal name"], ["cr_number", "CR number"], ["vat_number", "VAT number"], ["signatory_name", "Signatory"]],
  items: [["item_code", "Item code"], ["description", "Description"], ["brand", "Brand"], ["finish", "Finish"], ["sku", "Finish code"], ["selling_price", "Price QAR"]],
  opening_stock: [["business_unit", "Unit"], ["location", "Location"], ["sku", "Finish code"], ["quantity", "Quantity"], ["unit_cost", "Unit cost QAR"]],
  trial_balance: [["business_unit", "Unit"], ["account", "Account"], ["debit", "Debit"], ["credit", "Credit"], ["cash_account", "Bank or cash account"], ["note", "Note"]],
  customer_balances: [["business_unit", "Unit"], ["party", "Client no."], ["reference", "Invoice"], ["document_date", "Date"], ["amount", "Still owed QAR"]],
  supplier_balances: [["business_unit", "Unit"], ["party", "Supplier"], ["reference", "Bill"], ["document_date", "Date"], ["currency", "Currency"], ["amount", "Still owed"], ["value", "QAR"]],
  fixed_assets: [["business_unit", "Unit"], ["number", "Asset no."], ["name", "Name"], ["purchase_date", "Bought"], ["cost", "Cost QAR"], ["depreciation", "Depreciated QAR"], ["monthly", "Monthly QAR"]],
  staff_loans: [["business_unit", "Unit"], ["staff_number", "Staff no."], ["kind", "Kind"], ["amount", "Still owed QAR"], ["instalment", "Monthly QAR"], ["first_month", "From"]],
};
const THING: Record<Kind, string> = { customers: "customer", suppliers: "supplier", items: "item", opening_stock: "journal", trial_balance: "journal", customer_balances: "unpaid invoice", supplier_balances: "unpaid bill", fixed_assets: "fixed asset", staff_loans: "staff loan" };
const SENT: Record<Kind, string> = { customers: "customer", suppliers: "supplier", items: "row", opening_stock: "row", trial_balance: "row", customer_balances: "row", supplier_balances: "row", fixed_assets: "row", staff_loans: "row" };
const day = (value: string) => new Date(`${value}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const qar = (value: string) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function cell(value: Row[string]) {
  if (value === null || value === "" || value === undefined) return "–";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return show(String(value));
}

function SampleTable({ kind, rows, label }: { kind: Kind; rows: Row[]; label: string }) {
  return <div className="sp-table-wrap"><table className="sp-table gi-sample" aria-label={label}>
    <thead><tr><th scope="col">Row</th>{SAMPLE[kind].map(([key, heading]) => <th key={key} scope="col">{heading}</th>)}</tr></thead>
    <tbody>{rows.map((row) => <tr key={String(row.row)}><td className="pw-mono">{String(row.row)}</td>
      {SAMPLE[kind].map(([key]) => <td key={key}>{cell(row[key] ?? null)}</td>)}</tr>)}</tbody>
  </table></div>;
}

function Prepare({ overview, csrfToken, onDone }: { overview: Overview; csrfToken: string; onDone: (message: string) => void }) {
  const [kind, setKind] = useState<Kind>("customers");
  const [file, setFile] = useState<File | null>(null);
  const [balancesOn, setBalancesOn] = useState("");
  const [result, setResult] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const chosen = overview.kinds.find((row) => row.kind === kind);
  const columns = chosen?.columns ?? [];
  const needsDate = chosen?.needs_date ?? false;
  const body = () => { const form = new FormData(); form.append("kind", kind); form.append("file", file!); if (needsDate) form.append("balances_on", balancesOn); return form; };
  const check = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setBusy(true); setError(""); setResult(null);
    try {
      setResult(await call<Preview>(`${BASE}preview/`, { method: "POST", headers: { "X-CSRFToken": csrfToken }, body: body() }));
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "The file could not be checked.");
    } finally { setBusy(false); }
  };
  const send = async () => {
    setBusy(true); setError("");
    try {
      const batch = await call<Batch>(BASE, { method: "POST", headers: { "X-CSRFToken": csrfToken }, body: body() });
      onDone(`${count(batch.row_count, SENT[batch.kind])} from ${show(batch.file_name)} sent for approval. Nothing is created until someone else approves it.`);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "The file could not be sent.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form gi-form" onSubmit={(event) => void check(event)} aria-label="Check a file">
    <h3>1. Fill in the template, 2. check it, 3. send it for approval</h3>
    <div className="sp-fields gi-fields">
      <label>What are you bringing over<select value={kind} onChange={(event) => { setKind(event.target.value as Kind); setResult(null); }}>
        {overview.kinds.map((row) => <option key={row.kind} value={row.kind}>{row.label}</option>)}
      </select></label>
      {needsDate && <label>Balances as at<input type="date" value={balancesOn} onChange={(event) => { setBalancesOn(event.target.value); setResult(null); }} required /></label>}
      <label>Filled-in file (.xlsx or .csv)<input type="file" accept=".xlsx,.csv" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setResult(null); }} /></label>
    </div>
    <div className="gi-template">
      <a className="button button-outline" href={`${BASE}template/${kind}/`} download>Download the {(chosen?.label ?? kind).toLowerCase()} template</a>
      <details><summary>What goes in each column</summary>
        <ul>{columns.map((column) => <li key={column.heading}><strong>{column.heading}{column.required ? " (needed)" : ""}</strong>: {column.help}</li>)}</ul>
      </details>
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy || !file || (needsDate && !balancesOn)}>Check the file</button>
      {result && result.bad === 0 && <button className="button button-dark" type="button" disabled={busy} onClick={() => void send()}>Send {count(result.rows, "row")} for approval</button>}
    </div>
    {result && <div className="gi-result" aria-label="Check result">
      <p className={result.bad ? "gi-verdict gi-verdict-bad" : "gi-verdict gi-verdict-good"} role="status">
        {result.bad
          ? `${count(result.bad, "row")} of ${result.rows.toLocaleString("en-US")} ${result.bad === 1 ? "has" : "have"} problems. Fix them in the file and check it again. Nothing was saved.`
          : `All ${count(result.rows, "row")} pass the checks. Nothing is saved yet.`}
      </p>
      {result.bad > 0 && <div className="sp-table-wrap"><table className="sp-table gi-problems" aria-label="Problems by row">
        <thead><tr><th scope="col">Row</th><th scope="col">What to fix</th></tr></thead>
        <tbody>{result.problems.map((row) => <tr key={row.row}><td className="pw-mono">{row.row}</td>
          <td><ul>{row.problems.map((problem) => <li key={problem}>{show(problem)}</li>)}</ul></td></tr>)}</tbody>
      </table>{result.more_problems > 0 && <p className="sp-hint">And {count(result.more_problems, "more row")} with problems.</p>}</div>}
      <h4>How the first rows will come in</h4>
      <SampleTable kind={result.kind} rows={result.sample} label="First rows" />
    </div>}
  </form>;
}

function BatchRow({ batch, csrfToken, onDone }: { batch: Batch; csrfToken: string; onDone: (message: string) => void }) {
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const things = THING[batch.kind];
  const run = async (action: "approve" | "reject" | "withdraw") => {
    setBusy(true); setError("");
    try {
      const row = await call<Batch>(`${BASE}${batch.id}/`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ action, note }) });
      onDone({
        approve: `${count(row.created, things)} imported from ${show(row.file_name)}.`,
        reject: `${show(row.file_name)} is rejected. Nothing was created.`,
        withdraw: `${show(row.file_name)} is withdrawn. Nothing was created.`,
      }[action]);
    } catch (caught) {
      setError(caught instanceof Error ? show(caught.message) : "That could not be done.");
    } finally { setBusy(false); }
  };
  return <tr>
    <th scope="row">{show(batch.file_name)}<small>{batch.kind_label}{batch.balances_on && `, as at ${day(batch.balances_on)}`}</small></th>
    <td className="pw-mono bc-num">{batch.row_count.toLocaleString("en-US")}</td>
    <td>{batch.prepared_by}<small>{when(batch.prepared_at)}</small></td>
    <td>
      <span className={`sp-status sp-status-${TONE[batch.status]}`}>{batch.status_label}</span>
      {batch.decided_by && <small>{batch.status === "withdrawn" ? "Withdrawn" : batch.status === "rejected" ? "Rejected" : "Approved"} by {batch.decided_by}, {when(batch.decided_at)}
        {batch.status === "imported" && `, ${count(batch.created, things)} created`}{batch.decision_note && ` · ${show(batch.decision_note)}`}</small>}
      <details className="gi-peek"><summary>See the first rows</summary><SampleTable kind={batch.kind} rows={batch.sample} label={`First rows of ${show(batch.file_name)}`} /></details>
      {!rejecting && (batch.can_approve || batch.can_withdraw) && <span className="lv-actions">
        {batch.can_approve && <button className="button button-dark" type="button" disabled={busy} onClick={() => void run("approve")}>Approve and import</button>}
        {batch.can_approve && <button className="button button-outline" type="button" disabled={busy} onClick={() => { setRejecting(true); setNote(""); }}>Reject</button>}
        {batch.can_withdraw && <button className="button button-outline" type="button" disabled={busy} onClick={() => void run("withdraw")}>Withdraw</button>}
      </span>}
      {rejecting && <span className="mj-inline gi-inline">
        <input aria-label="Reason" value={note} maxLength={300} onChange={(event) => setNote(event.target.value)} placeholder="Why" />
        <button className="button button-dark" type="button" disabled={busy || !note.trim()} onClick={() => void run("reject")}>Confirm reject</button>
        <button className="button button-quiet" type="button" onClick={() => setRejecting(false)}>Back</button>
      </span>}
      {error && <div className="alert" role="alert">{error}</div>}
    </td>
  </tr>;
}

function BatchTable({ batches, label, csrfToken, onDone }: { batches: Batch[]; label: string; csrfToken: string; onDone: (message: string) => void }) {
  return <div className="sp-table-wrap"><table className="sp-table gi-table" aria-label={label}>
    <thead><tr><th scope="col">File</th><th scope="col" className="bc-num">Rows</th><th scope="col">Sent by</th><th scope="col">Status</th></tr></thead>
    <tbody>{batches.map((batch) => <BatchRow key={`${batch.id}-${batch.status}`} batch={batch} csrfToken={csrfToken} onDone={onDone} />)}</tbody>
  </table></div>;
}

export default function GoLiveImports({ csrfToken }: { csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [formKey, setFormKey] = useState(0);
  const load = useCallback(async () => {
    try {
      setOverview(await call<Overview>(BASE));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Go-live imports could not be loaded.");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); setFormKey((key) => key + 1); void load(); };
  return <section className="content-card supplier-payments go-live-imports" aria-labelledby="gi-title">
    <header className="sp-head">
      <span className="eyebrow">GOVERNANCE · GO-LIVE IMPORTS · TEST ONLY</span>
      <h2 id="gi-title">Go-live imports</h2>
      <p>Bring customers, suppliers, items and the opening balances over from the old system in one go. Every row is checked before anything is saved, and a second person approves the file before the records are created, all together or not at all.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading…</p>}
    {overview && <>
      {overview.can_prepare
        ? <Prepare key={formKey} overview={overview} csrfToken={csrfToken} onDone={done} />
        : <p className="sp-hint">You can approve files here. Preparing a file needs “Prepare go-live imports”.</p>}
      <h3 className="gi-heading">Waiting for approval</h3>
      {overview.waiting.length === 0
        ? <p className="sp-empty">No files are waiting.</p>
        : <BatchTable batches={overview.waiting} label="Waiting for approval" csrfToken={csrfToken} onDone={done} />}
      {overview.recent.length > 0 && <>
        <h3 className="gi-heading">Done</h3>
        <BatchTable batches={overview.recent} label="Done" csrfToken={csrfToken} onDone={done} />
      </>}
      {overview.opening_left.length > 0 && <div className="gi-left" role="status" aria-label="Opening balances still to bring in">
        <strong>Still to bring in:</strong> {overview.opening_left.map((row) => `${row.business_unit} QAR ${qar(String(Math.abs(Number(row.left))))}`).join(", ")}.
        <span> This is what the trial balance put on opening balances and the stock, customer, supplier, fixed asset and staff loan imports have not brought in yet. It is zero once they are all in.</span>
      </div>}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
