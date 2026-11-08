import { Fragment, useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Account = {
  id: number; name: string; kind: "bank" | "cash"; kind_label: string; bank_name: string; account_last4: string;
  account_type: string; account_type_label: string; is_facility: boolean;
  currency: string; opening_balance: string; opening_date: string; is_default: boolean; balance: string;
  opening: string; money_in: string; money_out: string;
};
type Movement = {
  line_id: number; date: string; journal_entry_id: number; source: string; source_kind: string; description: string;
  memo: string; money_in: string; money_out: string; balance: string; account_id: number | null; fixed: boolean;
};
type Overview = {
  unit: { id: number; code: string; name: string }; start: string; end: string; can_manage: boolean;
  accounts: Account[]; total: string; facilities_total: string; account_types: { value: string; label: string }[]; selected_id: number | null; movements: Movement[]; unplaced: Movement[];
  entry_kinds: { value: string; label: string }[]; policy_label: string;
};
type Panel = "account" | "transfer" | "bank_charge" | "interest" | null;

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
const monthStart = () => `${today().slice(0, 8)}01`;
const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto
  ? crypto.randomUUID() : `cash-entry-${Date.now()}-${Math.random().toString(36).slice(2)}`);
const fmt = (value: string) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const show = (name: string) => name.replace(/TEST ONLY\s*/g, "");
const detail = (account: Account) => account.kind === "bank"
  ? [account.account_type_label.replace(/ \(.*\)$/, ""), account.account_last4 && `···${account.account_last4}`].filter(Boolean).join(" ")
  : "Cash account";

function AccountForm({ overview, csrfToken, onDone, onCancel }: { overview: Overview; csrfToken: string; onDone: (message: string) => void; onCancel: () => void }) {
  const unitId = overview.unit.id;
  const hasCash = overview.accounts.some((account) => account.kind === "cash");
  const banks = [...new Set(overview.accounts.filter((account) => account.kind === "bank").map((account) => account.bank_name))];
  const [form, setForm] = useState({ name: "", kind: "bank", account_type: "current", bank_name: "", account_last4: "", opening_balance: "0.00", opening_date: monthStart(), is_default: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (field: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [field]: event.target.value });
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const account = await call<Account>(`/api/finance/cash/accounts/?unit=${unitId}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify(form),
      });
      onDone(`${show(account.name)} added${account.is_default ? ` as the default ${account.kind === "bank" ? "bank account" : "cash account"}` : ""}.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The account could not be added.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form" onSubmit={submit} aria-label="Add an account">
    <h3>Add an account</h3>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-fields">
      <label>Kind<select value={form.kind} onChange={set("kind")}><option value="bank">Bank account</option>{!hasCash && <option value="cash">Cash account</option>}</select></label>
      {form.kind === "bank" && <>
        <label>Bank<input value={form.bank_name} onChange={set("bank_name")} maxLength={80} list="bc-banks" placeholder="e.g. CBQ" required />
          <datalist id="bc-banks">{banks.map((bank) => <option key={bank} value={show(bank)} />)}</datalist></label>
        <label>Account type<select value={form.account_type} onChange={set("account_type")}>
          {overview.account_types.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
      </>}
      <label>Name<input value={form.name} onChange={set("name")} maxLength={80} placeholder={form.kind === "bank" ? "e.g. CBQ current account" : "e.g. Cash"} required /></label>
      {form.kind === "bank" && <label>Last four digits<input value={form.account_last4} onChange={set("account_last4")} maxLength={4} inputMode="numeric" /></label>}
      <label>{form.kind === "bank" && form.account_type === "ltr" ? "Amount owed at opening (QAR)" : "Opening balance (QAR)"}<input inputMode="decimal" value={form.opening_balance} onChange={set("opening_balance")} required /></label>
      <label>Opening date<input type="date" value={form.opening_date} onChange={set("opening_date")} required /></label>
    </div>
    <label className="bc-check"><input type="checkbox" checked={form.is_default} onChange={(event) => setForm({ ...form, is_default: event.target.checked })} />
      Make this the default {form.kind === "bank" ? "bank account (card, transfer and supplier payments)" : "cash account (cash taken and refunded)"}</label>
    {hasCash && <p className="sp-hint">{overview.unit.name} already has its cash account; a unit has one. One bank can hold several accounts: current, savings, LTR and LG.</p>}
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy}>Add account</button>
      <button className="button button-outline" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
    </div>
  </form>;
}

function EntryForm({ kind, overview, csrfToken, onDone, onCancel }: {
  kind: "transfer" | "bank_charge" | "interest"; overview: Overview; csrfToken: string; onDone: (message: string) => void; onCancel: () => void;
}) {
  const banks = kind === "transfer" ? overview.accounts : overview.accounts.filter((account) => account.kind === "bank");
  const first = overview.accounts.find((account) => account.id === overview.selected_id && banks.includes(account)) ?? banks[0];
  const [form, setForm] = useState({
    account_id: String(first?.id ?? ""), to_account_id: String(banks.find((account) => account.id !== first?.id)?.id ?? ""),
    amount: "", entry_date: today(), description: "", reference: "",
  });
  const [key] = useState(newKey);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (field: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [field]: event.target.value });
  const title = { transfer: "Move money between accounts", bank_charge: "Record bank charges", interest: "Record interest received" }[kind];
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await call(`/api/finance/cash/entries/?unit=${overview.unit.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken, "Idempotency-Key": key },
        body: JSON.stringify({
          kind, account_id: Number(form.account_id), amount: form.amount, entry_date: form.entry_date,
          description: form.description, reference: form.reference,
          ...(kind === "transfer" ? { to_account_id: Number(form.to_account_id) } : {}),
        }),
      });
      onDone({ transfer: "Transfer recorded and posted.", bank_charge: "Bank charges recorded and posted.", interest: "Interest recorded and posted." }[kind]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The entry could not be recorded.");
    } finally { setBusy(false); }
  };
  const options = banks.map((account) => <option key={account.id} value={account.id}>{show(account.name)}</option>);
  return <form className="sp-form" onSubmit={submit} aria-label={title}>
    <h3>{title}</h3>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-fields">
      <label>{kind === "transfer" ? "From" : "Bank account"}<select value={form.account_id} onChange={set("account_id")}>{options}</select></label>
      {kind === "transfer" && <label>To<select value={form.to_account_id} onChange={set("to_account_id")}>{options}</select></label>}
      <label>Amount (QAR)<input inputMode="decimal" value={form.amount} onChange={set("amount")} required /></label>
      <label>Date<input type="date" value={form.entry_date} onChange={set("entry_date")} required /></label>
      <label>Description (optional)<input value={form.description} onChange={set("description")} maxLength={160} /></label>
      <label>Reference (optional)<input value={form.reference} onChange={set("reference")} maxLength={80} placeholder="e.g. deposit slip or bank advice" /></label>
    </div>
    <p className="sp-hint">{kind === "transfer" ? "Posted as money out of one account and into the other; the total does not change." : kind === "bank_charge" ? "Posted to bank charges (provisional account)." : "Posted to interest received (provisional account)."}</p>
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy}>Record</button>
      <button className="button button-outline" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
    </div>
  </form>;
}

function Movements({ rows, overview, csrfToken, onMoved, label }: {
  rows: Movement[]; overview: Overview; csrfToken: string; onMoved: (message: string) => void; label: string;
}) {
  const [error, setError] = useState("");
  const move = async (row: Movement, accountId: number) => {
    setError("");
    try {
      await call(`/api/finance/cash/move/?unit=${overview.unit.id}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ line_id: row.line_id, account_id: accountId }),
      });
      onMoved(`Moved to ${show(overview.accounts.find((account) => account.id === accountId)?.name ?? "the account")}.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The movement could not be moved.");
    }
  };
  return <>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label={label}>
      <thead><tr><th scope="col">Date</th><th scope="col">What</th><th scope="col" className="bc-num">Money in</th><th scope="col" className="bc-num">Money out</th><th scope="col" className="bc-num">Balance</th>{overview.can_manage && <th scope="col">Belongs to</th>}</tr></thead>
      <tbody>{rows.map((row) => <tr key={row.line_id}>
        <th scope="row">{row.date}</th>
        <td>{show(row.description)}<small>{row.source} · journal #{row.journal_entry_id}</small></td>
        <td className="pw-mono bc-num">{row.money_in ? fmt(row.money_in) : ""}</td>
        <td className="pw-mono bc-num">{row.money_out ? fmt(row.money_out) : ""}</td>
        <td className="pw-mono bc-num">{fmt(row.balance)}</td>
        {overview.can_manage && <td>{row.fixed
          ? <span className="sp-muted">Recorded here</span>
          : <select aria-label={`Account for ${row.description}`} value={row.account_id ?? ""} onChange={(event) => void move(row, Number(event.target.value))}>
            {row.account_id === null && <option value="">Choose…</option>}
            {overview.accounts.filter((account) => account.opening_date <= row.date).map((account) => <option key={account.id} value={account.id}>{show(account.name)}</option>)}
          </select>}</td>}
      </tr>)}</tbody>
    </table></div>
  </>;
}

export default function BankCash({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [panel, setPanel] = useState<Panel>(null);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [period, setPeriod] = useState({ start: monthStart(), end: today() });

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    const account = accountId ? `&account=${accountId}` : "";
    try {
      setOverview(await call<Overview>(`/api/finance/cash/?unit=${selectedUnitId}${account}&start=${period.start}&end=${period.end}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Bank and cash could not be loaded.");
    }
  }, [selectedUnitId, accountId, period]);
  useEffect(() => { void load(); }, [load]);
  const done = (message: string) => { setPanel(null); setNotice(message); void load(); };
  const selected = overview?.accounts.find((account) => account.id === overview.selected_id) ?? null;
  const hasBank = overview?.accounts.some((account) => account.kind === "bank");

  return <section className="content-card supplier-payments bank-cash" aria-labelledby="bank-cash-title">
    <header className="sp-head">
      <span className="eyebrow">FINANCE · BANK AND CASH · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="bank-cash-title">Bank and cash</h2>
      <p>The unit's cash account and every bank account, grouped by bank, with the money that went in and out of each. Customer payments, refunds and supplier payments arrive here on their own; record transfers between accounts, bank charges and interest below. If money landed in a different account, move it.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading bank and cash…</p>}
    {overview && <>
      <div className="bc-filters">
        <label>From<input type="date" value={period.start} onChange={(event) => setPeriod({ ...period, start: event.target.value || monthStart() })} /></label>
        <label>To<input type="date" value={period.end} onChange={(event) => setPeriod({ ...period, end: event.target.value || today() })} /></label>
        {overview.can_manage && !panel && <div className="bc-buttons">
          <button className="button button-outline" type="button" onClick={() => { setNotice(""); setPanel("account"); }}>Add account</button>
          {overview.accounts.length > 1 && <button className="button button-outline" type="button" onClick={() => { setNotice(""); setPanel("transfer"); }}>Transfer</button>}
          {hasBank && <button className="button button-outline" type="button" onClick={() => { setNotice(""); setPanel("bank_charge"); }}>Bank charges</button>}
          {hasBank && <button className="button button-outline" type="button" onClick={() => { setNotice(""); setPanel("interest"); }}>Interest</button>}
        </div>}
      </div>
      {panel === "account" && <AccountForm overview={overview} csrfToken={csrfToken} onDone={done} onCancel={() => setPanel(null)} />}
      {panel && panel !== "account" && <EntryForm kind={panel} overview={overview} csrfToken={csrfToken} onDone={done} onCancel={() => setPanel(null)} />}
      {overview.accounts.length === 0
        ? <p className="sp-empty">{overview.unit.name} has no cash or bank account yet.{overview.can_manage ? " Add one to start." : ""}</p>
        : <ul className="bc-accounts" aria-label="Accounts">
          <li className="bc-total"><span>Money available</span><strong>{fmt(overview.total)}</strong><small>QAR at {overview.end}, cash, current and savings</small></li>
          {overview.accounts.some((account) => account.is_facility) && <li className="bc-total"><span>LTR and LG accounts</span><strong>{fmt(overview.facilities_total)}</strong><small>QAR; not money available</small></li>}
          {overview.accounts.map((account, index) => <Fragment key={account.id}>
          {(index === 0 || account.kind !== overview.accounts[index - 1].kind || account.bank_name !== overview.accounts[index - 1].bank_name) &&
            <li className="bc-group">{account.kind === "cash" ? "Cash" : show(account.bank_name) || "Other bank accounts"}</li>}
          <li>
            <button type="button" aria-pressed={account.id === overview.selected_id} onClick={() => setAccountId(account.id)}>
              <span>{show(account.name)}{account.is_default && <em>Default</em>}</span>
              <strong>{fmt(account.balance)}</strong>
              <small>{detail(account)} · in {fmt(account.money_in)} · out {fmt(account.money_out)}</small>
            </button>
          </li></Fragment>)}
        </ul>}
      {selected && <>
        <h3>{show(selected.name)} <small>{overview.start} to {overview.end} · opening {fmt(selected.opening)} · closing {fmt(selected.balance)} QAR</small></h3>
        {overview.movements.length === 0
          ? <p className="sp-empty">No money went in or out of {show(selected.name)} in this period.</p>
          : <Movements rows={overview.movements} overview={overview} csrfToken={csrfToken} onMoved={done} label={`Movements on ${show(selected.name)}`} />}
      </>}
      {overview.unplaced.length > 0 && <>
        <h3>Not in any account yet <small>{overview.unplaced.length}</small></h3>
        <p className="sp-hint">There is no default {overview.accounts.some((account) => account.kind === "cash") ? "bank account" : "account"} for this money yet. Add one, or choose where each movement belongs.</p>
        <Movements rows={overview.unplaced} overview={overview} csrfToken={csrfToken} onMoved={done} label="Movements not in any account" />
      </>}
      <p className="sp-hint">{overview.policy_label}</p>
    </>}
  </section>;
}
