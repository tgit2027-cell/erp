import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Line = { unit: { id: number; name: string }; percent: string; amount: string; journal_entry_id: number | null };
type Share = {
  id: number; number: string; date: string; account_code: string; account: string; description: string; amount: string;
  moved: string; kept_percent: string; kept: string; from_unit: { id: number; name: string }; direction: "sent" | "received";
  received: string | null; lines: Line[]; status: "submitted" | "approved" | "rejected"; status_label: string;
  prepared_by: string; decided_by: string; decision_note: string; journal_entry_id: number | null; can_decide: boolean;
};
type Account = { code: string; name: string; available: string };
type Overview = {
  unit: { id: number; code: string; name: string }; month: string; month_label: string; shares: Share[];
  totals: { sent: string; received: string; waiting: number; waiting_for_me: number };
  accounts: Account[]; units: { id: number; name: string }[];
  can_prepare: boolean; can_approve: boolean; currency: string; policy: string;
};
type Split = { unitId: string; percent: string };
type Draft = { account: string; description: string; amount: string; split: Split[] };

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
const thisMonth = () => today().slice(0, 7);
const fmt = (value: string | number) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const headers = (csrfToken: string) => ({ "Content-Type": "application/json", "X-CSRFToken": csrfToken });
const STATUS_CLASS: Record<Share["status"], string> = { submitted: "ex-waiting", approved: "ex-booked", rejected: "ex-rejected" };
const round = (value: number) => Math.round(value * 100) / 100;

/** The split as the server will book it: each unit rounded, rounding kept by the unit that booked the cost. */
function preview(amount: number, split: Split[]) {
  const rows = split.map((row) => ({ ...row, value: round(amount * (Number(row.percent) || 0) / 100) }));
  const given = split.reduce((sum, row) => sum + (Number(row.percent) || 0), 0);
  if (rows.length && Math.abs(given - 100) < 0.001) rows[rows.length - 1].value = round(amount - rows.slice(0, -1).reduce((sum, row) => sum + row.value, 0));
  const moved = round(rows.reduce((sum, row) => sum + row.value, 0));
  return { rows, given: round(given), moved, kept: round(amount - moved) };
}

function Prepare({ overview, csrfToken, start, onDone, onCancel }: {
  overview: Overview; csrfToken: string; start: Draft | null; onDone: (share: Share, message: string) => void; onCancel: () => void;
}) {
  const [date, setDate] = useState(overview.month === thisMonth() ? today() : `${overview.month}-${new Date(Number(overview.month.slice(0, 4)), Number(overview.month.slice(5)), 0).getDate()}`);
  const [accounts, setAccounts] = useState(overview.accounts);
  const [account, setAccount] = useState(start?.account ?? "");
  const [description, setDescription] = useState(start?.description ?? "");
  const [amount, setAmount] = useState(start?.amount ?? "");
  const [split, setSplit] = useState<Split[]>(start?.split ?? [{ unitId: "", percent: "" }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const month = date.slice(0, 7);
  useEffect(() => {
    if (!month || month === overview.month) { setAccounts(overview.accounts); return; }
    let live = true;
    call<Overview>(`/api/finance/cost-sharing/?unit=${overview.unit.id}&month=${month}`)
      .then((data) => { if (live) setAccounts(data.accounts); })
      .catch(() => { if (live) setAccounts([]); });
    return () => { live = false; };
  }, [month, overview]);
  const chosen = accounts.find((row) => row.code === account);
  const plan = preview(Number(amount) || 0, split);
  const tooMuch = Boolean(chosen) && plan.moved > Number(chosen!.available);
  const unitName = (id: string) => overview.units.find((row) => String(row.id) === id)?.name ?? "";
  const change = (index: number, patch: Partial<Split>) => setSplit(split.map((row, at) => at === index ? { ...row, ...patch } : row));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    const body = {
      account_code: account, date, description, amount,
      units: split.filter((row) => row.unitId).map((row) => ({ business_unit_id: Number(row.unitId), percent: row.percent })),
    };
    try {
      const share = await call<Share>(`/api/finance/cost-sharing/?unit=${overview.unit.id}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify(body) });
      onDone(share, `${share.number} is waiting for someone else to approve it. Nothing has moved yet.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The share could not be saved.");
    } finally { setBusy(false); }
  };
  return <form className="sp-form an-form" onSubmit={(event) => void submit(event)} aria-label="Share a cost">
    <h3>Share a cost of {overview.unit.name}</h3>
    <div className="sp-fields an-grid">
      <label>Kind of cost<select value={account} onChange={(event) => setAccount(event.target.value)} required>
        <option value="">Choose…</option>
        {accounts.map((row) => <option key={row.code} value={row.code}>{row.name} · {fmt(row.available)} QAR unshared</option>)}
      </select></label>
      <label>Date<input type="date" value={date} max={today()} onChange={(event) => setDate(event.target.value)} required /></label>
      <label>Cost to share (QAR)<input inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} required /></label>
      <label className="an-wide">Description<input value={description} maxLength={200} onChange={(event) => setDescription(event.target.value)} required placeholder="e.g. October rent for the Salwa Road building" /></label>
    </div>
    {chosen && <p className="sp-hint">{overview.unit.name} has {fmt(chosen.available)} QAR of {chosen.name.toLowerCase()} in this month that is not already shared.</p>}
    <div className="sp-table-wrap"><table className="sp-table bc-table cs-split" aria-label="Split between units">
      <thead><tr><th scope="col">Unit</th><th scope="col" className="bc-num">Share %</th><th scope="col" className="bc-num">Amount</th><th scope="col"><span className="sr-only">Remove</span></th></tr></thead>
      <tbody>
        {split.map((row, index) => <tr key={index}>
          <td><select aria-label={`Unit ${index + 1}`} value={row.unitId} onChange={(event) => change(index, { unitId: event.target.value })}>
            <option value="">Choose…</option>
            {overview.units.map((unit) => <option key={unit.id} value={unit.id} disabled={unit.id !== Number(row.unitId) && split.some((other) => other.unitId === String(unit.id))}>{unit.name}</option>)}
          </select></td>
          <td className="bc-num"><input aria-label={`Share % for unit ${index + 1}`} inputMode="decimal" value={row.percent} onChange={(event) => change(index, { percent: event.target.value })} /></td>
          <td className="pw-mono bc-num">{row.unitId && Number(amount) ? fmt(plan.rows[index].value) : ""}</td>
          <td><button className="button button-quiet" type="button" disabled={split.length === 1} onClick={() => setSplit(split.filter((_, at) => at !== index))}>Remove</button></td>
        </tr>)}
        <tr className="cs-kept"><th scope="row">{overview.unit.name} keeps</th><td className="pw-mono bc-num">{round(100 - plan.given)}%</td><td className="pw-mono bc-num">{Number(amount) ? fmt(plan.kept) : ""}</td><td /></tr>
      </tbody>
    </table></div>
    {split.length < overview.units.length && <button className="button button-outline" type="button" onClick={() => setSplit([...split, { unitId: "", percent: "" }])}>Add a unit</button>}
    {plan.given > 100 && <p className="alert" role="alert">The percentages add up to {plan.given}%, more than 100%.</p>}
    {tooMuch && <p className="alert" role="alert">That moves {fmt(plan.moved)} QAR, more than the {fmt(chosen!.available)} QAR not already shared.</p>}
    {Number(amount) > 0 && plan.moved > 0 && !tooMuch && plan.given <= 100 && <p className="sp-hint">
      On approval {fmt(plan.moved)} QAR moves out of {overview.unit.name}: {plan.rows.filter((row) => row.unitId).map((row) => `${unitName(row.unitId)} ${fmt(row.value)}`).join(", ")}.
    </p>}
    {error && <div className="alert" role="alert">{error}</div>}
    <div className="sp-actions">
      <button className="button button-dark" type="submit" disabled={busy || !account || plan.given > 100 || tooMuch || !split.some((row) => row.unitId)}>Send for approval</button>
      <button className="button button-quiet" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </form>;
}

function Detail({ share, overview, csrfToken, onChanged, onCopy, onClose }: {
  share: Share; overview: Overview; csrfToken: string; onChanged: (message: string) => void; onCopy: () => void; onClose: () => void;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (body: Record<string, unknown>, message: string) => {
    setBusy(true); setError("");
    try {
      await call(`/api/finance/cost-sharing/${share.id}/?unit=${overview.unit.id}`, { method: "POST", headers: headers(csrfToken), body: JSON.stringify(body) });
      onChanged(message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That could not be done.");
    } finally { setBusy(false); }
  };
  return <article className="sp-form an-detail" aria-label={`Share ${share.number}`}>
    <header className="mj-detail-head">
      <h3>{share.number} <span className={`ex-status ${STATUS_CLASS[share.status]}`}>{share.status_label}</span></h3>
      <button className="button button-quiet" type="button" onClick={onClose}>Close</button>
    </header>
    <dl className="an-facts">
      <div><dt>Cost</dt><dd>{share.account}</dd></div>
      <div><dt>Booked by</dt><dd>{share.from_unit.name}</dd></div>
      <div><dt>Date</dt><dd>{share.date}</dd></div>
      <div><dt>Total cost</dt><dd className="pw-mono">{fmt(share.amount)} QAR</dd></div>
      <div><dt>Moved to other units</dt><dd className="pw-mono">{fmt(share.moved)} QAR</dd></div>
      <div><dt>{share.from_unit.name} keeps</dt><dd className="pw-mono">{fmt(share.kept)} QAR ({Number(share.kept_percent)}%)</dd></div>
      <div className="an-wide"><dt>Description</dt><dd>{show(share.description)}</dd></div>
    </dl>
    <div className="sp-table-wrap"><table className="sp-table bc-table" aria-label={`Split of ${share.number}`}>
      <thead><tr><th scope="col">Unit</th><th scope="col" className="bc-num">Share</th><th scope="col" className="bc-num">Amount</th><th scope="col">In its accounts</th></tr></thead>
      <tbody>{share.lines.map((line) => <tr key={line.unit.id}>
        <th scope="row">{line.unit.name}</th><td className="pw-mono bc-num">{Number(line.percent)}%</td><td className="pw-mono bc-num">{fmt(line.amount)}</td>
        <td>{line.journal_entry_id ? `Journal ${line.journal_entry_id}` : share.status === "rejected" ? "Not posted" : "After approval"}</td>
      </tr>)}</tbody>
    </table></div>
    <p className="sp-hint">Prepared by {share.prepared_by}{share.decided_by ? `; ${share.status === "approved" ? "approved" : "rejected"} by ${share.decided_by}` : ""}{share.decision_note ? `: ${show(share.decision_note)}` : ""}.</p>
    {share.can_decide && <div className="sp-actions">
      <button className="button button-dark" type="button" disabled={busy} onClick={() => void run({ action: "approve" }, `${share.number} is approved. Each unit's accounts now carry its part.`)}>Approve and share</button>
      <button className="button button-outline" type="button" disabled={busy} onClick={() => setRejecting(!rejecting)}>Reject</button>
    </div>}
    {rejecting && <div className="mj-inline">
      <input aria-label="Why it is rejected" value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder="Why it is rejected" />
      <button className="button button-dark" type="button" disabled={busy || !reason.trim()} onClick={() => void run({ action: "reject", reason }, `${share.number} is rejected.`)}>Reject the share</button>
    </div>}
    {share.direction === "sent" && overview.can_prepare && <div className="sp-actions">
      <button className="button button-outline" type="button" onClick={onCopy}>Share again with the same split</button>
    </div>}
    {error && <div className="alert" role="alert">{error}</div>}
  </article>;
}

export default function CostSharing({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [month, setMonth] = useState(thisMonth());
  const [status, setStatus] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [preparing, setPreparing] = useState<{ draft: Draft | null } | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    const query = new URLSearchParams({ unit: String(selectedUnitId), month, ...(status ? { status } : {}) });
    try {
      setOverview(await call<Overview>(`/api/finance/cost-sharing/?${query}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Cost sharing could not be loaded.");
    }
  }, [selectedUnitId, month, status]);
  useEffect(() => { void load(); }, [load]);
  const open = overview?.shares.find((row) => row.id === openId) ?? null;
  const copy = (share: Share) => {
    setOpenId(null); setNotice("");
    setPreparing({ draft: { account: share.account_code, description: share.description, amount: "",
      split: share.lines.map((line) => ({ unitId: String(line.unit.id), percent: String(Number(line.percent)) })) } });
  };

  return <section className="content-card supplier-payments bank-cash cost-sharing" aria-labelledby="cost-sharing-title">
    <header className="sp-head">
      <span className="eyebrow">FINANCE · COST SHARING · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="cost-sharing-title">Cost sharing</h2>
      <p>Move part of a cost from the unit that paid it to the other units that use it, such as rent for a shared building. Each unit's profit then carries its own part. A second person approves each share.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading cost sharing…</p>}
    {overview && <>
      <ul className="bc-accounts ex-tiles" aria-label="Cost sharing">
        <li className="bc-total"><span>Shared out in {overview.month_label}</span><strong>{fmt(overview.totals.sent)}</strong><small>QAR moved to other units</small></li>
        <li className="bc-total"><span>Received in {overview.month_label}</span><strong>{fmt(overview.totals.received)}</strong><small>QAR from other units</small></li>
        <li className="bc-total"><span>Waiting for approval</span><strong>{overview.totals.waiting}</strong><small>{overview.totals.waiting_for_me ? `${overview.totals.waiting_for_me} for you` : "shares of this unit"}</small></li>
      </ul>
      <div className="bc-filters">
        <label>Month<input type="month" value={month} max={thisMonth()} onChange={(event) => event.target.value && setMonth(event.target.value)} /></label>
        <label>Show<select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All</option><option value="submitted">Waiting for approval</option>
          <option value="approved">Shared</option><option value="rejected">Rejected</option>
        </select></label>
        {overview.can_prepare && !preparing && <div className="bc-buttons">
          <button className="button button-dark" type="button" disabled={overview.units.length === 0} onClick={() => { setNotice(""); setOpenId(null); setPreparing({ draft: null }); }}>Share a cost</button>
        </div>}
      </div>
      {overview.can_prepare && overview.units.length === 0 && <p className="sp-hint">{overview.unit.name} is the only active unit of its company, so there is no one to share with.</p>}
      {preparing && <Prepare key={JSON.stringify(preparing.draft)} overview={overview} csrfToken={csrfToken} start={preparing.draft} onCancel={() => setPreparing(null)}
        onDone={(share, message) => { setPreparing(null); setMonth(share.date.slice(0, 7)); setOpenId(share.id); setNotice(message); void load(); }} />}
      {!preparing && open && <Detail key={`${open.id}-${open.status}`} share={open} overview={overview} csrfToken={csrfToken}
        onChanged={(message) => { setNotice(message); void load(); }} onCopy={() => copy(open)} onClose={() => setOpenId(null)} />}
      {overview.shares.length === 0
        ? <p className="sp-empty">No shared costs in {overview.month_label}{status ? " match" : ""}.</p>
        : <div className="sp-table-wrap"><table className="sp-table bc-table cs-table" aria-label="Shared costs">
          <thead><tr><th scope="col">Share</th><th scope="col">Date</th><th scope="col">Cost</th><th scope="col">Between</th>
            <th scope="col" className="bc-num">Amount</th><th scope="col">Status</th></tr></thead>
          <tbody>{overview.shares.map((share) => <tr key={share.id} className={openId === share.id ? "mj-open" : undefined}>
            <th scope="row"><button className="fa-link" type="button" onClick={() => { setNotice(""); setPreparing(null); setOpenId(share.id); }}>{share.number}</button>
              <small>{show(share.description)}</small></th>
            <td>{share.date}</td>
            <td>{share.account}</td>
            <td>{share.direction === "sent"
              ? <>To {share.lines.map((line) => line.unit.name).join(", ")}</>
              : <>From {share.from_unit.name}</>}</td>
            <td className="pw-mono bc-num">{share.direction === "sent" ? `−${fmt(share.moved)}` : `+${fmt(share.received ?? "0")}`}
              <small>{share.direction === "sent" ? `of ${fmt(share.amount)}` : "received"}</small></td>
            <td><span className={`ex-status ${STATUS_CLASS[share.status]}`}>{share.status_label}</span>
              <small>{share.decided_by ? `by ${share.decided_by}` : `prepared by ${share.prepared_by}`}{share.can_decide ? " · for you to approve" : ""}</small></td>
          </tr>)}</tbody>
        </table></div>}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
