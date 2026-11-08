import { useEffect, useRef, useState } from "react";
import {
  createReportShareLink,
  revokeReportShareLink,
  useReportShareLinks,
} from "./reporting-api";
import type { ReportFilters, ReportPanelCode, ReportShareLink, SharedReport } from "./reporting-api";
import "./report-share.css";

function shortDate(value: string) {
  return new Date(value).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

const stateLabel = { active: "Active", expired: "Expired", revoked: "Withdrawn" } as const;

/** Create a share link for the report on screen. The link is shown once. */
export function ReportShareControl({
  csrfToken,
  panel,
  filters,
  onCreated,
}: {
  csrfToken: string;
  panel: ReportPanelCode;
  filters: ReportFilters;
  onCreated: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<ReportShareLink | null>(null);
  const [copied, setCopied] = useState(false);

  async function create() {
    setBusy(true);
    setError("");
    try {
      const link = await createReportShareLink(csrfToken, panel, filters, note);
      setCreated(link);
      setNote("");
      onCreated();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The share link could not be created.");
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!created?.url) return;
    try {
      await navigator.clipboard.writeText(created.url);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  if (!open) {
    return <button className="button button-outline" type="button" onClick={() => setOpen(true)}>Share link</button>;
  }
  return (
    <div className="rs-panel" role="dialog" aria-label="Share this report">
      <div className="rs-panel-head">
        <strong>Share this report</strong>
        <button className="rs-text-button" type="button" onClick={() => { setOpen(false); setCreated(null); setCopied(false); }}>Close</button>
      </div>
      {!created ? <>
        <p>The link opens this report with the same scope, period and location. Each person sees it only if their own report permissions cover it. Links last 7 days.</p>
        <label className="rs-field"><span>Note for the person you send it to (optional)</span>
          <input value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} placeholder="For example: daily sales for the team meeting" />
        </label>
        {error && <p className="rs-error" role="alert">{error}</p>}
        <button className="button" type="button" onClick={() => void create()} disabled={busy}>{busy ? "Creating link…" : "Create link"}</button>
      </> : <>
        <p className="rs-once"><strong>Copy the link now.</strong> It is shown only once. You can withdraw it at any time from your shared links.</p>
        <div className="rs-url">
          <input readOnly value={created.url ?? ""} aria-label="Share link" onFocus={(event) => event.target.select()} />
          <button className="button button-outline" type="button" onClick={() => void copy()}>{copied ? "Copied" : "Copy"}</button>
        </div>
        <small>Works until {shortDate(created.expires_at)}.</small>
      </>}
    </div>
  );
}

/** The signed-in user's own links, with open counts and a way to withdraw them. */
export function MyShareLinks({ csrfToken, revision }: { csrfToken: string; revision: number }) {
  const [expanded, setExpanded] = useState(false);
  const links = useReportShareLinks(expanded);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const seenRevision = useRef(revision);
  const { refresh } = links;
  useEffect(() => {
    if (seenRevision.current === revision) return;
    seenRevision.current = revision;
    if (expanded) refresh();
  }, [expanded, refresh, revision]);

  async function revoke(id: number) {
    setBusyId(id);
    setError("");
    try {
      await revokeReportShareLink(csrfToken, id);
      links.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The link could not be withdrawn.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <details className="rs-mine" open={expanded} onToggle={(event) => setExpanded((event.target as HTMLDetailsElement).open)}>
      <summary>My shared links</summary>
      {links.loading && <p className="rs-muted" role="status">Loading your links…</p>}
      {links.error && <p className="rs-error" role="alert">{links.error}</p>}
      {error && <p className="rs-error" role="alert">{error}</p>}
      {links.data && !links.data.links.length && <p className="rs-muted">You have not shared any reports yet.</p>}
      {links.data && links.data.links.length > 0 && (
        <table className="rs-table">
          <thead><tr><th>Report</th><th>Period</th><th>Note</th><th>Opened</th><th>Status</th><th><span className="sr-only">Action</span></th></tr></thead>
          <tbody>
            {links.data.links.map((link) => (
              <tr key={link.id}>
                <td>{link.panel_label}</td>
                <td>{link.filters.start} to {link.filters.end}</td>
                <td>{link.note || "—"}</td>
                <td>{link.open_count} {link.open_count === 1 ? "time" : "times"}</td>
                <td><span className={`rs-state rs-state-${link.state}`}>{stateLabel[link.state]}</span>{link.state === "active" && <small> until {shortDate(link.expires_at)}</small>}</td>
                <td>{link.state === "active" && <button className="rs-text-button" type="button" disabled={busyId === link.id} onClick={() => void revoke(link.id)}>{busyId === link.id ? "Withdrawing…" : "Withdraw"}</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </details>
  );
}

/** Shown above the report when it was opened from someone's share link. */
export function SharedReportNotice({ shared, error, onDismiss }: { shared: SharedReport | null; error: string; onDismiss: () => void }) {
  if (error) {
    return (
      <div className="report-denial rs-notice" role="alert">
        <strong>The shared report could not be opened</strong>
        <p>{error}</p>
        <button className="button button-outline" type="button" onClick={onDismiss}>Dismiss</button>
      </div>
    );
  }
  if (!shared) return null;
  return (
    <div className="rs-notice rs-notice-ok" role="status">
      <div>
        <strong>Shared by {shared.shared_by}</strong>
        <p>{shared.panel_label}, {shared.filters.start} to {shared.filters.end}.{shared.note ? ` “${shared.note}”` : ""} You are seeing it through your own report permissions.</p>
      </div>
      <button className="rs-text-button" type="button" onClick={onDismiss}>Dismiss</button>
    </div>
  );
}
