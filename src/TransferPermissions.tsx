import { useCallback, useEffect, useState } from "react";
import "./stock-transfers.css";

type Overview = {
  unit: { id: number; code: string; name: string };
  actions: { action: string; label: string; scope?: string; can_change?: boolean }[];
  users: { id: number; username: string; name: string; actions: string[]; can_change?: boolean }[];
  history: { actor: string; target: string; action: string; change: string; at: string }[];
};

const SHORT_LABELS: Record<string, string> = {
  request_transfer: "Request (showroom admin)",
  approve_transfer: "Approve (showroom manager)",
  execute_transfer: "Schedule & move (warehouse)",
  open_supplier: "Open suppliers (IT)",
  approve_supplier: "Approve suppliers (manager)",
  import_data: "Prepare go-live imports",
  approve_import: "Approve go-live imports",
  count_stock: "Count stock (warehouse)",
  approve_count: "Review counts (warehouse manager)",
  approve_write_off: "Approve write-offs (Finance)",
  record_expense: "Enter expenses (any staff)",
  approve_expense: "Approve expenses (manager / finance)",
  prepare_budget: "Prepare budgets (finance)",
  approve_budget: "Approve budgets (owner / manager)",
  manage_assets: "Register fixed assets, run depreciation (finance)",
  approve_asset: "Approve fixed asset purchases and disposals (owner / manager)",
  prepare_journal: "Prepare manual journal entries (finance)",
  approve_journal: "Approve and post manual journal entries (finance manager)",
  prepare_note: "Prepare credit and debit notes (finance)",
  approve_note: "Approve credit and debit notes (finance manager)",
  view_staff: "See staff records (HR / managers)",
  manage_staff: "Add and change staff records (HR)",
  post_portal: "Post on the HR Portal (HR team)",
  view_pay: "See salaries and payroll (payroll / owner)",
  manage_pay: "Set salaries and prepare payroll (payroll)",
  approve_pay: "Approve and post payroll (owner / finance manager)",
  read_stock: "Use this unit and see its stock",
  create_order: "Make quotations and orders",
  manage_reorder: "Set low-stock levels (warehouse / buying)",
  manage_items: "Edit items and photos (buying)",
  view_costs: "See item costs (buying / finance)",
  pay_supplier: "Prepare payments (finance)",
  approve_payment: "Approve payments (finance manager)",
  view_ledger: "View general ledger (finance)",
  approve_refund: "Approve refunds (finance)",
  close_month: "Close months (finance manager)",
  manage_cash: "Bank and cash (finance)",
  approve_bank_rec: "Approve bank reconciliations (finance manager)",
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
        <span className="eyebrow">MASTER ADMIN AND GENERAL ADMIN · PER BUSINESS UNIT</span>
        <h3 id="transfer-permissions-title">User permissions: stock transfers, counts, suppliers and finance{overview ? ` · ${overview.unit.name}` : ""}</h3>
        <p className="transfer-meta">Tick who may request, approve, or schedule and carry out stock moves in this business unit. Nobody can approve their own request, even with both permissions.</p>
        <p className="transfer-meta">Supplier permissions (marked “all units”) apply company-wide: IT opens new suppliers with their KYC documents, and the people ticked under “Approve suppliers” check and approve them. Nobody can approve a supplier they opened.</p>
        <p className="transfer-meta">Stock count permissions are per business unit: counters count a location, and a manager who did not take part approves the differences.</p>
        <p className="transfer-meta">“Pay suppliers” is per business unit: it lets Finance record payments against this unit's purchase receipts, including the exchange gain or loss on foreign-currency purchases. “View general ledger” lets them read this unit's trial balance, journal entries and financial statements; it changes nothing. “Close months” lets a finance manager close an ended month so nothing more can be posted into it, and reopen it with a reason. “Approve refunds” lets Finance approve or reject customer returns after inspection, which issues the credit note and refund.</p>
      </div>
      {overview && overview.actions.some((item) => item.can_change === false) && <p className="sp-hint" role="note">You can change the stock transfer permissions for the people you manage. Only the Master Admin can change the others.</p>}
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
                <input type="checkbox" checked={on} disabled={saving !== "" || item.can_change === false || user.can_change === false} aria-label={`${SHORT_LABELS[item.action] ?? item.label} for ${user.username}`}
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
