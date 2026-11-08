import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Letter = {
  id: number; employee: { id: number; name: string; staff_number: string }; kind: string; kind_label: string; addressee: string;
  purpose: string; include_salary: boolean; include_iban: boolean; status: "requested" | "issued" | "rejected"; status_label: string;
  number: string; requested_by: string; requested_at: string; decided_by: string; decided_at: string | null; rejection_reason: string;
  downloads: number; missing: string[]; can_decide: boolean; can_download: boolean; note?: string;
};
type Kind = { value: string; label: string; needs_addressee: boolean; addressee: string; iban: boolean };
type Settings = {
  company_name: string; address: string; signatory_name: string; signatory_title: string; has_signature: boolean;
  hr_checks_first: boolean; set_by: string; set_at: string; signature?: string;
};
type Overview = {
  unit: { id: number; name: string } | null; me: { id: number; name: string; staff_number: string; job_title: string; missing: string[] } | null;
  mine: Letter[]; to_issue: Letter[]; recent: Letter[]; people: { id: number; name: string; staff_number: string }[];
  kinds: Kind[]; settings: Settings | null; can_issue: boolean; policy: string;
};

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : Object.values(data).flat().join(" ") || "The request could not be completed.";
    throw new Error(String(detail));
  }
  return data as T;
}

const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const headers = (csrfToken: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken });
const STATUS_CLASS: Record<Letter["status"], string> = { requested: "ex-waiting", issued: "ex-booked", rejected: "ex-rejected" };
const day = (iso: string | null) => (iso ?? "").slice(0, 10);
const PLACEHOLDER: Record<string, string> = {
  bank: "e.g. QNB, Doha", loan: "e.g. Doha Bank, Doha", embassy: "e.g. Embassy of India, Doha", home_bank: "e.g. BDO Unibank, Manila",
};

async function download(letter: Letter, unitId: number | null) {
  const response = await fetch(`/api/hr/letters/${letter.id}/pdf/${unitId !== null ? `?unit=${unitId}` : ""}`, { credentials: "same-origin" });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : "The letter could not be downloaded.");
  }
  const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") ?? "")?.[1] ?? "letter.pdf";
  const blob = await response.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
  return name;
}

function Ask({ overview, unitId, csrfToken, onDone }: { overview: Overview; unitId: number | null; csrfToken: string; onDone: (message: string) => void }) {
  const [employeeId, setEmployeeId] = useState(overview.me ? "" : String(overview.people[0]?.id ?? ""));
  const [kindValue, setKindValue] = useState("to_whom");
  const [addressee, setAddressee] = useState("");
  const [purpose, setPurpose] = useState("");
  const [salary, setSalary] = useState(true);
  const [iban, setIban] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const kind = overview.kinds.find((row) => row.value === kindValue)!;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const letter = await call<Letter>(`/api/hr/letters/${unitId !== null ? `?unit=${unitId}` : ""}`, { method: "POST", headers: headers(csrfToken),
        body: JSON.stringify({ kind: kindValue, addressee, purpose, include_salary: salary, include_iban: kind.iban && iban,
          ...(employeeId ? { employee_id: Number(employeeId) } : {}) }) });
      setAddressee(""); setPurpose("");
      onDone(letter.status === "issued"
        ? `${letter.kind_label} ${letter.number} for ${show(letter.employee.name)} is ready. Download it below.`
        : `${letter.kind_label} for ${show(letter.employee.name)} is waiting for HR.${letter.note ? ` ${letter.note}` : ""}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The letter could not be asked for.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form an-form" onSubmit={(event) => void submit(event)} aria-label="Ask for a letter">
    <h3>Ask for a letter</h3>
    <div className="sp-fields an-grid">
      {overview.can_issue && overview.people.length > 0 && <label>For<select value={employeeId} onChange={(event) => setEmployeeId(event.target.value)}>
        {overview.me && <option value="">Myself</option>}
        {overview.people.filter((person) => person.id !== overview.me?.id).map((person) => <option key={person.id} value={person.id}>{show(person.name)}</option>)}
      </select></label>}
      <label>Letter<select value={kindValue} onChange={(event) => { setKindValue(event.target.value); setIban(false); }}>
        {overview.kinds.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
      </select></label>
      <label>Addressed to{kind.needs_addressee ? "" : " (optional)"}<input value={addressee} maxLength={120} required={kind.needs_addressee}
        onChange={(event) => setAddressee(event.target.value)} placeholder={PLACEHOLDER[kindValue] ?? show(kind.addressee)} /></label>
      <label className="an-wide">What it is for (optional)<input value={purpose} maxLength={200} onChange={(event) => setPurpose(event.target.value)} placeholder="e.g. a visit visa for my mother" /></label>
      <label className="lt-check"><input type="checkbox" checked={salary} onChange={(event) => setSalary(event.target.checked)} /> Show the salary</label>
      {kind.iban && <label className="lt-check"><input type="checkbox" checked={iban} onChange={(event) => setIban(event.target.checked)} /> Show the bank account (IBAN)</label>}
    </div>
    <p className="sp-hint">The letter is filled from the staff record: name, QID, passport, joining date, job title and current pay, and signed by HR. Bank and loan letters say the company gives no guarantee and carries no liability for any loan.</p>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy || (kind.needs_addressee && !addressee.trim())}>Ask for the letter</button></div>
  </form>;
}

function Table({ title, rows, unitId, csrfToken, onDone }: { title: string; rows: Letter[]; unitId: number | null; csrfToken: string; onDone: (message: string) => void }) {
  const [rejecting, setRejecting] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const act = async (work: () => Promise<string>) => {
    setBusy(true); setError("");
    try { onDone(await work()); setRejecting(null); setReason(""); } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That could not be done.");
    } finally { setBusy(false); }
  };
  const decide = (letter: Letter, action: "issue" | "reject") => act(async () => {
    await call(`/api/hr/letters/${letter.id}/${unitId !== null ? `?unit=${unitId}` : ""}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({ action, reason }) });
    return action === "issue" ? `${show(letter.employee.name)}'s ${letter.kind_label.toLowerCase()} is issued.` : `${show(letter.employee.name)}'s ${letter.kind_label.toLowerCase()} is not issued.`;
  });
  return <div className="lv-block">
    <h3>{title}</h3>
    <div className="sp-table-wrap"><table className="sp-table bc-table lt-table" aria-label={title}>
      <thead><tr><th scope="col">Who</th><th scope="col">Letter</th><th scope="col">Asked on</th><th scope="col">Status</th></tr></thead>
      <tbody>{rows.map((letter) => <tr key={letter.id}>
        <th scope="row">{show(letter.employee.name)}<small>{letter.employee.staff_number}</small></th>
        <td>{letter.kind_label}<small>{letter.addressee !== letter.kind_label ? show(letter.addressee) : ""}{letter.purpose ? ` · for ${show(letter.purpose)}` : ""}{letter.include_iban ? " · with IBAN" : ""}{letter.include_salary ? "" : " · no salary"}</small></td>
        <td>{day(letter.requested_at)}</td>
        <td><span className={`ex-status ${STATUS_CLASS[letter.status]}`}>{letter.status_label}</span>
          {letter.status === "issued" && <small>{letter.number} · {letter.decided_by}{letter.downloads ? ` · downloaded ${letter.downloads}×` : ""}</small>}
          {letter.status === "rejected" && <small>{letter.decided_by}: {show(letter.rejection_reason)}</small>}
          {letter.missing.length > 0 && <small className="lt-missing">The record has no {letter.missing.join(", ")}.</small>}
          <span className="lv-actions">
            {letter.can_download && <button className="button button-dark" type="button" disabled={busy} onClick={() => void act(async () => `${await download(letter, unitId)} is downloaded.`)}>Download PDF</button>}
            {letter.can_decide && <button className="button button-dark" type="button" disabled={busy || letter.missing.length > 0} onClick={() => void decide(letter, "issue")}>Issue</button>}
            {letter.can_decide && <button className="button button-outline" type="button" disabled={busy} onClick={() => { setRejecting(rejecting === letter.id ? null : letter.id); setReason(""); }}>Do not issue</button>}
          </span>
          {rejecting === letter.id && <span className="mj-inline">
            <input aria-label="Why it is not issued" value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder="Why, e.g. passport on the record has expired" />
            <button className="button button-dark" type="button" disabled={busy || !reason.trim()} onClick={() => void decide(letter, "reject")}>Confirm</button>
          </span>}
        </td>
      </tr>)}</tbody>
    </table></div>
    {error && <div className="alert" role="alert">{error}</div>}
  </div>;
}

function SetUp({ settings, unitId, csrfToken, onDone }: { settings: Settings | null; unitId: number; csrfToken: string; onDone: (message: string) => void }) {
  const [company, setCompany] = useState(settings?.company_name ?? "");
  const [address, setAddress] = useState(settings?.address ?? "");
  const [signatory, setSignatory] = useState(settings?.signatory_name ?? "");
  const [title, setTitle] = useState(settings?.signatory_title ?? "");
  const [checks, setChecks] = useState(settings?.hr_checks_first ?? false);
  const [signature, setSignature] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pick = (file: File | undefined) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setSignature(String(reader.result));
    reader.readAsDataURL(file);
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await call(`/api/hr/letters/settings/?unit=${unitId}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify({
        company_name: company, address, signatory_name: signatory, signatory_title: title, hr_checks_first: checks,
        ...(signature ? { signature } : {}) }) });
      onDone("The letter set-up is saved. New letters use it.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The set-up could not be saved.");
    } finally { setBusy(false); }
  };
  const preview = signature ?? settings?.signature;
  return <form className="sp-form an-form" onSubmit={(event) => void save(event)} aria-label="Letter set-up">
    <h3>Letter set-up</h3>
    <div className="sp-fields an-grid">
      <label>Company name<input value={company} maxLength={120} onChange={(event) => setCompany(event.target.value)} required /></label>
      <label className="an-wide">Address line<input value={address} maxLength={200} onChange={(event) => setAddress(event.target.value)} placeholder="e.g. P.O. Box 1234, Salwa Road, Doha, Qatar" /></label>
      <label>Signed by<input value={signatory} maxLength={120} onChange={(event) => setSignatory(event.target.value)} required /></label>
      <label>Job title<input value={title} maxLength={80} onChange={(event) => setTitle(event.target.value)} required /></label>
      <label>Signature (PNG)<input type="file" accept="image/png" onChange={(event) => pick(event.target.files?.[0])} /></label>
      {preview && <span className="lt-signature"><img src={preview} alt="Signature on the letters" /></span>}
      <label className="lt-check an-wide"><input type="checkbox" checked={checks} onChange={(event) => setChecks(event.target.checked)} /> HR checks each letter before it can be downloaded</label>
    </div>
    {settings && <p className="sp-hint">Last set by {settings.set_by} on {day(settings.set_at)}.</p>}
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions"><button className="button button-dark" type="submit" disabled={busy}>Save set-up</button></div>
  </form>;
}

/** "employee" is the HR Portal view: only the person's own letters, no HR work. */
export default function Letters({ selectedUnitId, csrfToken, mode = "hr" }: { selectedUnitId: number | null; csrfToken: string; mode?: "hr" | "employee" }) {
  const employee = mode === "employee";
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [setUp, setSetUp] = useState(false);
  const load = useCallback(async () => {
    try {
      setOverview(await call<Overview>(`/api/hr/letters/${selectedUnitId !== null ? `?unit=${selectedUnitId}` : ""}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Letters could not be loaded.");
    }
  }, [selectedUnitId]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setNotice(message); setSetUp(false); void load(); };

  return <section className="content-card supplier-payments bank-cash letters-page" aria-labelledby="letters-title">
    <header className="sp-head">
      <span className="eyebrow">{employee ? "HR PORTAL · MY LETTERS · TEST ONLY" : `HR · LETTERS · TEST ONLY${overview?.unit ? ` · ${overview.unit.name}` : ""}`}</span>
      <h2 id="letters-title">Letters</h2>
      <p>Salary certificates and other letters about your job, filled in from your staff record and signed by HR. Download them as PDF.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading letters…</p>}
    {overview && <>
      {!employee && overview.can_issue && !overview.settings && !setUp && <p className="lt-warn">Letters are not set up yet for this unit. Add the company name and who signs, so letters can be issued.</p>}
      {!employee && overview.can_issue && overview.unit && <div className="sp-actions lt-setup-toggle"><button className="button button-outline" type="button" onClick={() => setSetUp(!setUp)}>{setUp ? "Close set-up" : "Letter set-up"}</button></div>}
      {setUp && overview.unit && <SetUp settings={overview.settings} unitId={overview.unit.id} csrfToken={csrfToken} onDone={done} />}
      {overview.me && overview.me.missing.length > 0 && <p className="lt-warn">Your staff record has no {overview.me.missing.join(" or ")}. HR needs to add it before letters can be issued.</p>}
      {(overview.me || (!employee && overview.can_issue && overview.people.length > 0)) && <Ask key={notice} overview={employee ? { ...overview, can_issue: false } : overview} unitId={selectedUnitId} csrfToken={csrfToken} onDone={done} />}
      {!employee && overview.to_issue.length > 0 && <Table title="Waiting for HR" rows={overview.to_issue} unitId={selectedUnitId} csrfToken={csrfToken} onDone={done} />}
      {overview.mine.length > 0 && <Table title="My letters" rows={overview.mine} unitId={selectedUnitId} csrfToken={csrfToken} onDone={done} />}
      {!employee && overview.recent.length > 0 && <Table title="Letters issued in this unit" rows={overview.recent} unitId={selectedUnitId} csrfToken={csrfToken} onDone={done} />}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
