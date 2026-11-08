import { useCallback, useEffect, useState } from "react";
import "./supplier-payments.css";

type Changed = { by: string; at: string } | null;
type Discount = { role: string; label: string; percent: string; default: string; changed: Changed };
type Seller = { id: number; name: string; roles: string[]; role_percent: string; percent: string | null; changed: Changed };
type Person = { id: number; name: string; limit: string | null; changed: Changed };
type Approval = { action: string; label: string; covers: string; people: Person[] };
type Overview = { unit: { id: number; code: string; name: string }; discounts: Discount[]; people_discounts: Seller[]; approvals: Approval[]; can_change?: boolean; policy: string };

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : Object.values(data).flat().join(" ") || "The request could not be completed.";
    throw new Error(String(detail));
  }
  return data as T;
}

const fmt = (value: string) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const show = (text: string) => text.replace(/TEST ONLY\s*/g, "");
const when = (changed: Changed) => changed
  ? `Changed by ${changed.by}, ${new Date(changed.at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}`
  : "";

function Edit({ value, label, suffix, placeholder, busy, onSave }: {
  value: string; label: string; suffix: string; placeholder: string; busy: boolean; onSave: (next: string) => void;
}) {
  const [text, setText] = useState(value);
  const changed = text.trim() !== value;
  return <span className="al-edit">
    <input aria-label={label} inputMode="decimal" value={text} placeholder={placeholder} onChange={(event) => setText(event.target.value)} />
    <span className="al-suffix">{suffix}</span>
    <button className="button button-outline" type="button" disabled={busy || !changed} onClick={() => onSave(text.trim())}>Save</button>
  </span>;
}

export default function ApprovalLimits({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    try {
      setOverview(await call<Overview>(`/api/governance/approval-limits/?unit=${selectedUnitId}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Approval limits could not be loaded.");
    }
  }, [selectedUnitId]);
  useEffect(() => { void load(); }, [load]);

  const save = async (body: Record<string, unknown>, message: string) => {
    if (!overview) return;
    setBusy(true); setError(""); setNotice("");
    try {
      setOverview(await call<Overview>(`/api/governance/approval-limits/?unit=${overview.unit.id}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify(body),
      }));
      setNotice(message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That could not be saved.");
    } finally { setBusy(false); }
  };

  return <section className="content-card supplier-payments approval-limits" aria-labelledby="approval-limits-title">
    <header className="sp-head">
      <span className="eyebrow">GOVERNANCE · APPROVAL LIMITS · TEST ONLY{overview ? ` · ${overview.unit.name}` : ""}</span>
      <h2 id="approval-limits-title">Approval limits</h2>
      <p>How much discount each role may give on its own, each person's own discount for selling stock items, and the most each approver may approve, for the selected business unit. Leave a limit empty for no limit. Every change is recorded.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading approval limits…</p>}
    {overview && <>
      {overview.can_change === false && <p className="sp-hint" role="note">Only the Master Admin can change these limits. You can see them here.</p>}
      <h3>Discounts each role may give</h3>
      <div className="sp-table-wrap"><table className="sp-table" aria-label="Discount ceilings">
        <thead><tr><th scope="col">Role</th><th scope="col">Largest discount on its own</th><th scope="col">Standard</th><th scope="col" /></tr></thead>
        <tbody>{overview.discounts.map((row) => <tr key={row.role}>
          <th scope="row">{row.label}</th>
          <td><Edit key={row.percent} value={Number(row.percent).toString()} label={`Discount ceiling for ${row.label}`} suffix="%" placeholder={Number(row.default).toString()}
            busy={busy || overview.can_change === false} onSave={(next) => void save({ role: row.role, percent: next }, `${row.label}: ${next === "" ? `back to the standard ${Number(row.default)}%` : `up to ${next}%`}.`)} /></td>
          <td>{Number(row.default)}%</td>
          <td><small>{when(row.changed)}</small></td>
        </tr>)}</tbody>
      </table></div>
      <h3>Discount per person, for selling stock items</h3>
      <p className="sp-hint">Give a person their own discount, from 0% to 50%. It replaces their role's discount when they sell stock items. Special orders keep the role's discount. Leave it empty to use the role's.</p>
      {overview.people_discounts.length === 0
        ? <p className="sp-hint">Nobody has a sales role in {overview.unit.name} yet.</p>
        : <div className="sp-table-wrap"><table className="sp-table" aria-label="Discount per person">
          <thead><tr><th scope="col">Person</th><th scope="col">Own discount (%)</th><th scope="col">Now</th><th scope="col" /></tr></thead>
          <tbody>{overview.people_discounts.map((person) => <tr key={person.id}>
            <th scope="row">{show(person.name)}<small>{person.roles.join(", ")}</small></th>
            <td><Edit key={person.percent ?? ""} value={person.percent ? Number(person.percent).toString() : ""} label={`Own discount for ${show(person.name)}`}
              suffix="%" placeholder={`Role: ${Number(person.role_percent)}`} busy={busy || overview.can_change === false}
              onSave={(next) => void save({ kind: "person_discount", user_id: person.id, percent: next },
                `${show(person.name)}: ${next === "" ? `back to the role's ${Number(person.role_percent)}%` : `up to ${next}%`} on stock sales.`)} /></td>
            <td>{person.percent ? `Up to ${Number(person.percent)}% (own)` : `Up to ${Number(person.role_percent)}% (role)`}</td>
            <td><small>{when(person.changed)}</small></td>
          </tr>)}</tbody>
        </table></div>}
      <h3>The most each approver may approve</h3>
      {overview.approvals.map((approval) => <div key={approval.action} className="al-group">
        <h4>{approval.label} <small>{approval.covers}</small></h4>
        {approval.people.length === 0
          ? <p className="sp-hint">Nobody in {overview.unit.name} has this permission.</p>
          : <div className="sp-table-wrap"><table className="sp-table" aria-label={`Limits for ${approval.label}`}>
            <thead><tr><th scope="col">Approver</th><th scope="col">Limit (QAR)</th><th scope="col">Now</th><th scope="col" /></tr></thead>
            <tbody>{approval.people.map((person) => <tr key={person.id}>
              <th scope="row">{show(person.name)}</th>
              <td><Edit key={person.limit ?? ""} value={person.limit ? Number(person.limit).toString() : ""} label={`Limit for ${show(person.name)}: ${approval.label}`}
                suffix="QAR" placeholder="No limit" busy={busy || overview.can_change === false}
                onSave={(next) => void save({ action: approval.action, user_id: person.id, max_amount: next },
                  `${show(person.name)} may now approve ${next === "" ? "any amount" : `up to QAR ${fmt(next.replace(/,/g, ""))}`}: ${approval.label.toLowerCase()}.`)} /></td>
              <td>{person.limit ? `Up to ${fmt(person.limit)}` : "No limit"}</td>
              <td><small>{when(person.changed)}</small></td>
            </tr>)}</tbody>
          </table></div>}
      </div>)}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
