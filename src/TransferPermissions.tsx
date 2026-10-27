import { useCallback, useEffect, useState } from "react";
import "./stock-transfers.css";

type Overview = {
  unit: { id: number; code: string; name: string };
  actions: { action: string; label: string; scope?: string }[];
  users: { id: number; username: string; name: string; actions: string[] }[];
  history: { actor: string; target: string; action: string; change: string; at: string }[];
};

const SHORT_LABELS: Record<string, string> = {
  request_transfer: "Request (showroom admin)",
  approve_transfer: "Approve (showroom manager)",
  execute_transfer: "Schedule & move (warehouse)",
  open_supplier: "Open suppliers (IT)",
  approve_supplier: "Approve suppliers (manager)",
};

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...init.headers },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string"
      ? data.detail
      : Object.values(data).flat().join(" ") || "The permission change could not be completed.";
    throw new Error(String(detail));
  }
  return data as T;
}

export default function TransferPermissions({ selectedUnitId, csrfToken }: { selectedUnitId: number | null; csrfToken: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState("");

  const load = useCallback(async () => {
    if (selectedUnitId === null) return;
    try {
      setOverview(await call<Overview>(`/api/governance/transfer-permissions/?unit=${selectedUnitId}`));
      setError("");
    } catch (caught) {
      setOverview(null);
      setError(caught instanceof Error ? caught.message : "Transfer permissions could not be loaded.");
    }
  }, [selectedUnitId]);

  useEffect(() => { void load(); }, [load]);

  const toggle = async (userId: number, action: string, enabled: boolean) => {
    setSaving(`${userId}:${action}`);
    setError("");
    try {
      await call("/api/governance/transfer-permissions/", {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken },
        body: JSON.stringify({ unit_id: selectedUnitId, user_id: userId, action, enabled }),
      });
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The permission change could not be saved.");
    } finally {
      setSaving("");
    }
  };

  return (
    <section className="transfer-section transfer-permissions" aria-labelledby="transfer-permissions-title">
      <div>
        <span className="eyebrow">SYSTEM ADMINISTRATOR · PER BUSINESS UNIT</span>
        <h3 id="transfer-permissions-title">User permissions: stock transfers and suppliers{overview ? ` · ${overview.unit.name}` : ""}</h3>
        <p className="transfer-meta">Tick who may request, approve, or schedule and carry out stock moves in this business unit. Nobody can approve their own request, even with both permissions.</p>
        <p className="transfer-meta">Supplier permissions (marked “all units”) apply company-wide: IT opens new suppliers with their KYC documents, and the people ticked under “Approve suppliers” check and approve them. Nobody can approve a supplier they opened.</p>
      </div>
      {error && <div className="alert" role="alert">{error}</div>}
      {!overview && !error && <p className="capacity-empty" role="status">Loading people and permissions…</p>}
      {overview && <div className="transfer-permission-table" role="table" aria-label="Transfer permissions by person">
        <div className="transfer-permission-row transfer-permission-head" role="row">
          <span role="columnheader">Person</span>
          {overview.actions.map((item) => <span role="columnheader" key={item.action}>{SHORT_LABELS[item.action] ?? item.label}{item.scope === "company" ? " · all units" : ""}</span>)}
        </div>
        {overview.users.map((user) => (
          <div className="transfer-permission-row" role="row" key={user.id}>
            <span role="cell"><strong>{user.name}</strong> <small className="pw-mono">{user.username}</small></span>
            {overview.actions.map((item) => {
              const on = user.actions.includes(item.action);
              return <span role="cell" key={item.action}><label className="transfer-permission-toggle">
                <input type="checkbox" checked={on} disabled={saving !== ""} aria-label={`${SHORT_LABELS[item.action] ?? item.label} for ${user.username}`}
                  onChange={() => void toggle(user.id, item.action, !on)} />
                {on ? "Yes" : "No"}
              </label></span>;
            })}
          </div>
        ))}
      </div>}
      {overview && <>
        <h4 className="transfer-group-title">Recent changes</h4>
        {overview.history.length === 0 ? <p className="capacity-empty">No changes yet.</p> : <ul className="transfer-lines">
          {overview.history.map((event, index) => <li key={index}>{new Date(event.at).toLocaleString()}: {event.actor} {event.change} “{SHORT_LABELS[event.action] ?? event.action}” for {event.target}</li>)}
        </ul>}
      </>}
    </section>
  );
}
