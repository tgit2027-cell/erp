import { useEffect, useRef, useState } from "react";
import "./retail-collections.css";
import "./scenario-reconciliation.css";
import "./audit-log.css";

type AuditEvent = {
  id: number;
  occurred_at: string;
  kind: string;
  label: string;
  username: string;
  user_id: number | null;
  ip_address: string | null;
  user_agent: string;
  method: string;
  path: string;
  status_code: number | null;
  detail: Record<string, unknown>;
};
type UserRow = {
  user_id: number;
  username: string;
  name: string;
  is_active: boolean;
  sessions: number;
  active_seconds: number;
  actions: number;
  failed_sign_ins: number;
  access_denied: number;
  last_sign_in: string | null;
  last_address: string | null;
};
type Overview = {
  security: Record<string, number> & { failed_by_address: { ip_address: string | null; attempts: number }[]; recent_alerts: AuditEvent[]; recent_jobs: AuditEvent[] };
  users: UserRow[];
};
type UserEvents = {
  user: { id: number; username: string; name: string; is_active: boolean; is_staff: boolean; is_superuser: boolean };
  events: AuditEvent[];
  pagination: { page: number; total_pages: number; total_count: number };
};
type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: string; denied: boolean };

const WINDOWS: [number, string][] = [[1, "Last 24 hours"], [7, "Last 7 days"], [30, "Last 30 days"], [90, "Last 90 days"]];
const SECURITY: [string, string][] = [
  ["sign_ins", "Sign-ins"],
  ["failed_sign_ins", "Failed sign-ins"],
  ["unknown_account_attempts", "Attempts on unknown accounts"],
  ["lockouts", "Blocked after repeated failures"],
  ["refused_sign_ins", "Refused: no access granted"],
  ["sign_ins_from_new_address", "Sign-ins from a new address"],
  ["access_denied", "Access denied"],
  ["accounts_created", "Accounts created"],
  ["accounts_changed", "Account access changes"],
  ["background_jobs", "Background jobs that changed data"],
];
const KINDS: [string, string][] = [
  ["", "All activity"],
  ["sign_in", "Sign-ins"],
  ["sign_out", "Sign-outs"],
  ["sign_in_failed", "Failed sign-ins"],
  ["action", "Data changes"],
  ["access_denied", "Access denied"],
  ["account_changed", "Account changes"],
  ["system_job", "Background jobs"],
];
const ALERTING = new Set(["sign_in_failed", "sign_in_refused", "sign_in_locked", "access_denied"]);

function fromDate(days: number) {
  const start = new Date(Date.now() - (days - 1) * 86400000);
  return start.toISOString().slice(0, 10);
}

export function formatDuration(seconds: number) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours ? `${hours} h ${minutes} min` : `${minutes} min`;
}

function when(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

function useJson<T>(url: string | null, retry: number): Load<T> {
  const [state, setState] = useState<Load<T>>({ status: "loading" });
  const version = useRef(0);
  useEffect(() => {
    if (!url) return;
    const current = ++version.current;
    setState({ status: "loading" });
    fetch(url, { method: "GET", credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(async (response) => {
        const body = await response.json().catch(() => ({}));
        if (current !== version.current) return;
        if (!response.ok) {
          setState({ status: "error", error: typeof body?.detail === "string" ? body.detail : `Request unavailable (${response.status}).`, denied: [401, 403].includes(response.status) });
          return;
        }
        setState({ status: "ready", data: body as T });
      })
      .catch(() => { if (current === version.current) setState({ status: "error", error: "The audit log is unavailable.", denied: false }); });
  }, [url, retry]);
  return state;
}

type Touched = Record<string, { created: number; updated: number; deleted: number; ids: number[] }>;
type FieldChange = { model: string; id: number; action: string; changed?: Record<string, [unknown, unknown]>; values?: Record<string, unknown>; linked?: string; ids?: number[] };

function shortModel(label: string) {
  return label.split(".").pop() ?? label;
}

export function touchedSummary(records: Touched | undefined) {
  if (!records) return "";
  return Object.entries(records).map(([label, row]) => {
    const parts = (["created", "updated", "deleted"] as const).filter((key) => row[key]).map((key) => `${row[key]} ${key}`);
    return `${shortModel(label)} ${parts.join(", ")}`;
  }).join(" · ");
}

function show(value: unknown) {
  return value === null || value === undefined || value === "" ? "empty" : String(value);
}

export function changeLines(changes: FieldChange[] | undefined) {
  return (changes ?? []).map((change) => {
    const head = `${shortModel(change.model)} #${change.id}`;
    if (change.changed) return `${head}: ${Object.entries(change.changed).map(([field, [before, after]]) => `${field} ${show(before)} → ${show(after)}`).join("; ")}`;
    if (change.linked) return `${head}: ${change.action} ${shortModel(change.linked)} ${change.ids?.join(", ") ?? ""}`.trim();
    return `${head} ${change.action}`;
  });
}

function Changes({ event }: { event: AuditEvent }) {
  const lines = changeLines(event.detail.changes as FieldChange[] | undefined);
  const summary = touchedSummary(event.detail.records as Touched | undefined);
  if (!summary && !lines.length) return null;
  return <>
    {summary && <small className="al-touched">{summary}</small>}
    {lines.length > 0 && <details className="al-changes"><summary>What changed</summary><ul>{lines.map((line, index) => <li key={index}>{line}</li>)}</ul>
      {Number(event.detail.more_changes ?? 0) > 0 && <p>and {String(event.detail.more_changes)} more</p>}</details>}
  </>;
}

function describe(event: AuditEvent) {
  if (event.kind === "system_job") return `${String(event.detail.command ?? "job")} · ${event.detail.succeeded ? "finished" : `failed${event.detail.error ? ` (${String(event.detail.error)})` : ""}`}`;
  if (event.kind === "action") return `${event.method} ${event.path}${event.status_code ? ` · ${event.status_code}` : ""}${event.detail.succeeded === false ? " · failed" : ""}`;
  if (event.kind === "access_denied") return `${event.method} ${event.path}${event.status_code ? ` · ${event.status_code}` : ""}`;
  if (event.kind === "account_changed") return `Changed: ${(event.detail.changed as string[] | undefined)?.join(", ") ?? "access"} · by ${String(event.detail.by ?? "system")}`;
  if (event.kind === "account_created") return `Created by ${String(event.detail.by ?? "system")}`;
  if (event.kind === "sign_in" && event.detail.new_address) return "From an address not seen before for this account";
  if (event.kind === "sign_in_failed" && event.detail.unknown_account) return "No account has this username";
  return "";
}

function EventTable({ events, showUser }: { events: AuditEvent[]; showUser: boolean }) {
  if (!events.length) return <p className="al-empty">No activity in this period.</p>;
  return (
    <div className="rc-table-scroll">
      <table className="rc-table al-table">
        <thead><tr><th scope="col">When</th>{showUser && <th scope="col">Account</th>}<th scope="col">Activity</th><th scope="col">Details</th><th scope="col">Address</th></tr></thead>
        <tbody>{events.map((event) => (
          <tr key={event.id} className={ALERTING.has(event.kind) ? "al-alert" : ""}>
            <td>{when(event.occurred_at)}</td>
            {showUser && <td>{event.username || "—"}</td>}
            <td><span className={`al-kind al-${event.kind}`}>{event.label}</span></td>
            <td>{describe(event)}<Changes event={event} /></td>
            <td className="rc-number">{event.ip_address ?? "—"}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function UserActivity({ userId, from, onClose }: { userId: number; from: string; onClose: () => void }) {
  const [kind, setKind] = useState("");
  const [page, setPage] = useState(1);
  const params = new URLSearchParams({ from, page: String(page), page_size: "25" });
  if (kind) params.set("kind", kind);
  const state = useJson<UserEvents>(`/api/audit/users/${userId}/?${params}`, 0);
  return (
    <section className="al-user" aria-labelledby="al-user-title">
      <header>
        <div>
          <span className="rc-kicker">ACTIVITY OF ONE ACCOUNT</span>
          <h3 id="al-user-title">{state.status === "ready" ? (state.data.user.name ? `${state.data.user.name} (${state.data.user.username})` : state.data.user.username) : "Loading account"}</h3>
          {state.status === "ready" && <p>{state.data.user.is_active ? "Active account" : "Disabled account"}{state.data.user.is_superuser ? " · system administrator" : state.data.user.is_staff ? " · staff" : ""}</p>}
        </div>
        <button type="button" className="rc-button" onClick={onClose}>Back to all users</button>
      </header>
      <label className="al-filter">Show
        <select value={kind} onChange={(event) => { setKind(event.target.value); setPage(1); }}>
          {KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      {state.status === "loading" && <div className="rc-loading" role="status" aria-label="Loading activity"><i /><i /><i /></div>}
      {state.status === "error" && <div className="rc-message rc-error" role="alert"><p>{state.error}</p></div>}
      {state.status === "ready" && <>
        <EventTable events={state.data.events} showUser={false} />
        <div className="al-pages">
          <span>{state.data.pagination.total_count} events · page {state.data.pagination.page} of {state.data.pagination.total_pages}</span>
          <button type="button" className="rc-button" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
          <button type="button" className="rc-button" disabled={page >= state.data.pagination.total_pages} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      </>}
    </section>
  );
}

export default function AuditLog() {
  const [days, setDays] = useState(30);
  const [retry, setRetry] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const from = fromDate(days);
  const state = useJson<Overview>(`/api/audit/?from=${from}`, retry);

  return (
    <section className="retail-collections audit-log" aria-labelledby="audit-log-title">
      <header className="rc-heading">
        <div>
          <span className="rc-kicker">REPORTS · SYSTEM ADMINISTRATORS · READ ONLY</span>
          <h2 id="audit-log-title">Audit log</h2>
          <p>Sign-ins, failed and refused sign-ins, account changes, data changes with the records they touched, background jobs and refused requests across the ERP.</p>
        </div>
        <div className="al-controls">
          <label>Period
            <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
              {WINDOWS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <button type="button" className="rc-button" onClick={() => setRetry((value) => value + 1)} disabled={state.status === "loading"}>Refresh</button>
        </div>
      </header>

      {selected !== null ? <UserActivity userId={selected} from={from} onClose={() => setSelected(null)} /> : <>
        {state.status === "loading" && <div className="rc-loading" role="status" aria-label="Loading audit log"><i /><i /><i /></div>}
        {state.status === "error" && <div className="rc-message rc-error" role={state.denied ? "status" : "alert"}>
          <strong>{state.denied ? "Audit log not authorized" : "Audit log unavailable"}</strong>
          <p>{state.denied ? "Only system administrators can read the audit log." : state.error}</p>
        </div>}
        {state.status === "ready" && <>
          <dl className="sr-summary al-security">
            {SECURITY.map(([key, label]) => (
              <div key={key} className={ALERT_KEYS.has(key) && state.data.security[key] > 0 ? "al-flag" : ""}><dt>{label}</dt><dd>{state.data.security[key]}</dd></div>
            ))}
          </dl>
          <section className="sr-area" aria-labelledby="al-users">
            <h3 id="al-users">Usage by user</h3>
            {state.data.users.length ? <div className="rc-table-scroll">
              <table className="rc-table al-table">
                <thead><tr><th scope="col">Account</th><th scope="col">Time signed in</th><th scope="col">Sessions</th><th scope="col">Data changes</th><th scope="col">Failed sign-ins</th><th scope="col">Access denied</th><th scope="col">Last sign-in</th><th scope="col">Last address</th></tr></thead>
                <tbody>{state.data.users.map((row) => (
                  <tr key={row.user_id}>
                    <th scope="row"><button type="button" className="al-user-link" onClick={() => setSelected(row.user_id)} aria-label={`Show activity of ${row.username}`}>{row.name ? `${row.name} (${row.username})` : row.username}</button>{row.is_active ? "" : <span className="al-disabled"> disabled</span>}</th>
                    <td className="rc-number">{formatDuration(row.active_seconds)}</td>
                    <td className="rc-number">{row.sessions}</td>
                    <td className="rc-number">{row.actions}</td>
                    <td className={`rc-number ${row.failed_sign_ins ? "al-bad" : ""}`}>{row.failed_sign_ins}</td>
                    <td className={`rc-number ${row.access_denied ? "al-bad" : ""}`}>{row.access_denied}</td>
                    <td>{when(row.last_sign_in)}</td>
                    <td className="rc-number">{row.last_address ?? "—"}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div> : <p className="al-empty">Nobody signed in during this period.</p>}
          </section>
          {state.data.security.failed_by_address.length > 0 && <section className="sr-area" aria-labelledby="al-addresses">
            <h3 id="al-addresses">Failed sign-ins by address</h3>
            <ul className="al-addresses">{state.data.security.failed_by_address.map((row) => <li key={row.ip_address ?? "unknown"}><span className="rc-number">{row.ip_address ?? "unknown address"}</span> · {row.attempts} attempts</li>)}</ul>
          </section>}
          {state.data.security.recent_jobs.length > 0 && <section className="sr-area" aria-labelledby="al-jobs">
            <h3 id="al-jobs">Background jobs</h3>
            <EventTable events={state.data.security.recent_jobs} showUser={false} />
          </section>}
          <section className="sr-area" aria-labelledby="al-alerts">
            <h3 id="al-alerts">Security alerts</h3>
            <EventTable events={state.data.security.recent_alerts} showUser />
          </section>
          <small className="rc-test-label">Usage time counts from sign-in to the last request in each session (activity is sampled once a minute). Events cannot be edited or deleted.</small>
        </>}
      </>}
    </section>
  );
}

const ALERT_KEYS = new Set(["failed_sign_ins", "unknown_account_attempts", "lockouts", "refused_sign_ins", "sign_ins_from_new_address", "access_denied"]);
