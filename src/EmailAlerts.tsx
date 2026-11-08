import { useCallback, useEffect, useState } from "react";
import "./supplier-payments.css";
import "./bank-cash.css";

type Email = {
  id: number; kind: "approvals" | "expiring"; kind_label: string; user: string; to: string; subject: string;
  items: { key: string; title: string; link: string }[]; status: "sent" | "failed" | "no_email"; status_label: string; error: string; at: string;
};
type AlertType = { key: string; label: string; kind: "approvals" | "expiring" };
type Person = { id: number; name: string; account_email: string; email: string; sends_to: string; offered: string[]; types: string[] };
type Overview = { types: AlertType[]; emails: Email[]; people: Person[]; waiting: number; expiring: number; mail_configured: boolean; policy: string; just_sent?: number };

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
const when = (value: string) => new Date(value).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
function Address({ person, busy, onSave }: { person: Person; busy: boolean; onSave: (next: string) => void }) {
  const [text, setText] = useState(person.email);
  const label = `Alert email for ${show(person.name)}`;
  return <span className="al-edit">
    <input aria-label={label} type="email" value={text} placeholder={person.account_email || "No address on the account"} onChange={(event) => setText(event.target.value)} />
    <button className="button button-outline" type="button" disabled={busy || text.trim() === person.email} onClick={() => onSave(text.trim())}>Save</button>
  </span>;
}

const TONE: Record<Email["status"], string> = { sent: "ex-booked", failed: "ex-rejected", no_email: "ex-waiting" };

export default function EmailAlerts({ csrfToken }: { csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setOverview(await call<Overview>("/api/governance/alerts/"));
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Alerts could not be loaded.");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const post = async (body: Record<string, unknown>, message: (data: Overview) => string) => {
    setBusy(true); setError(""); setNotice("");
    try {
      const data = await call<Overview>("/api/governance/alerts/", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken }, body: JSON.stringify(body),
      });
      setOverview(data);
      setNotice(message(data));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That could not be done.");
    } finally { setBusy(false); }
  };

  return <section className="content-card supplier-payments email-alerts" aria-labelledby="email-alerts-title">
    <header className="sp-head">
      <span className="eyebrow">GOVERNANCE · ALERTS · TEST ONLY</span>
      <h2 id="email-alerts-title">Alerts</h2>
      <p>Choose who receives alerts, the email address they go to, and which alerts each person receives: things waiting for their approval, and supplier or staff documents about to expire. Nobody receives alerts until they are assigned here. Each thing is emailed once.</p>
    </header>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p className="sp-notice" role="status">{notice}</p>}
    {!overview && !error && <p className="sp-empty" role="status">Loading email alerts…</p>}
    {overview && <>
      <ul className="bc-accounts ex-tiles" aria-label="Alerts">
        <li className="bc-total"><span>Waiting for approval</span><strong>{overview.waiting}</strong><small>across the ERP now</small></li>
        <li className="bc-total"><span>Documents to watch</span><strong>{overview.expiring}</strong><small>expiring soon or expired</small></li>
        <li className="bc-total"><span>Email server</span><strong>{overview.mail_configured ? "Connected" : "Not set up"}</strong>
          <small>{overview.mail_configured ? "emails are sent" : "emails are written to the server log for now"}</small></li>
      </ul>
      <div className="sp-actions">
        <button className="button button-dark" type="button" disabled={busy}
          onClick={() => void post({ action: "send" }, (data) => data.just_sent ? `${data.just_sent} alert ${data.just_sent === 1 ? "email" : "emails"} recorded.` : "Nothing new to send.")}>Send what is new now</button>
      </div>
      <h3>Who receives which alerts</h3>
      <p className="sp-hint">Only people who can act on at least one alert are listed, and each person is only offered the alerts they can act on. Leave the email empty to use the address on their account.</p>
      {overview.people.length === 0
        ? <p className="sp-empty">Nobody can approve anything or look after documents yet.</p>
        : <div className="sp-table-wrap"><table className="sp-table" aria-label="Who receives alerts">
          <thead><tr><th scope="col">Person</th><th scope="col">Alerts go to</th><th scope="col">Alerts they receive</th></tr></thead>
          <tbody>{overview.people.map((person) => <tr key={person.id}>
            <th scope="row">{show(person.name)}{person.types.length === 0 && <small>Not assigned</small>}</th>
            <td><Address key={person.email} person={person} busy={busy}
              onSave={(next) => void post({ user_id: person.id, email: next },
                () => `${show(person.name)}: alerts now go to ${next || person.account_email || "the account address"}.`)} />
              {!person.sends_to && <small>No email address, so nothing can be sent</small>}</td>
            <td><ul className="ea-types">{overview.types.filter((type) => person.offered.includes(type.key)).map((type) => {
              const on = person.types.includes(type.key);
              return <li key={type.key}><label className="ea-toggle"><input type="checkbox" checked={on} disabled={busy} aria-label={`${type.label} for ${show(person.name)}`}
                onChange={() => { const next = on ? person.types.filter((key) => key !== type.key) : [...person.types, type.key];
                  void post({ user_id: person.id, types: next }, () => `${show(person.name)} ${on ? "no longer receives" : "now receives"} alerts for ${type.label.toLowerCase()}.`); }} />
                {type.label}</label></li>;
            })}</ul></td>
          </tr>)}</tbody>
        </table></div>}
      <h3>Recent alerts</h3>
      {overview.emails.length === 0
        ? <p className="sp-empty">No alerts have gone out yet.</p>
        : <div className="sp-table-wrap"><table className="sp-table" aria-label="Recent alerts">
          <thead><tr><th scope="col">When</th><th scope="col">To</th><th scope="col">What</th><th scope="col">Status</th></tr></thead>
          <tbody>{overview.emails.map((email) => <tr key={email.id}>
            <td>{when(email.at)}</td>
            <td>{show(email.user)}{email.to && <small>{email.to}</small>}</td>
            <td><details><summary>{email.subject.replace("Tivoli ERP: ", "")}</summary>
              <ul className="ea-items">{email.items.map((item) => <li key={item.key}><a href={item.link}>{show(item.title)}</a></li>)}</ul>
            </details></td>
            <td><span className={`ex-status ${TONE[email.status]}`}>{email.status_label}</span>{email.error && <small>{email.error}</small>}</td>
          </tr>)}</tbody>
        </table></div>}
      <p className="sp-hint">{overview.policy}</p>
    </>}
  </section>;
}
