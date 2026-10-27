import { workspaceHash } from "./workspace-hash";

export const financeReviewDestinations = [
  { section: "retail", child: "quote", label: "Quotation entry" },
  { section: "retail", child: "collections", label: "Collection follow-up" },
  { section: "procurement", child: "progress", label: "Commitment receipt progress" },
  { section: "inventory", child: "main", label: "Inventory quantities" },
  { section: "delivery", child: "handover", label: "Readiness & handover" },
  { section: "reports", child: "main", label: "Reports & Dashboards" },
];

type Props = {
  canManageReportPanels: boolean;
  onNavigate: (section: string, child: string) => void;
};

export default function FinanceStatusPage({ canManageReportPanels, onNavigate }: Props) {
  const link = ({ section, child, label }: typeof financeReviewDestinations[number]) => (
    <a className="button button-outline" key={`${section}/${child}`} href={workspaceHash(section, child)} onClick={event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      onNavigate(section, child);
    }}>{label}</a>
  );
  return <section className="governance-workspace" data-workspace-view="finance/main" aria-labelledby="finance-status-title">
    <div className="governance-section-block">
      <p className="eyebrow">STATUS ONLY · NOT OPERATIONAL FINANCE</p>
      <h2 id="finance-status-title">Finance availability and integration status</h2>
      <p>The shared application provides existing operational workspaces. Finance draft workflows are implemented separately in an isolated synthetic lab, not activated here.</p>
      <p>Viewing this status does not load Finance records, create proposals or grant access.</p>
    </div>
    <section className="governance-section-block" aria-labelledby="finance-shared-heading">
      <h3 id="finance-shared-heading">Available in this shared application</h3>
      <p>Open an existing function below. Its data and actions still require their existing business-unit, location and capability permissions; a link does not grant them.</p>
      <ul>{financeReviewDestinations.map(destination => <li key={destination.section + destination.child}>{link(destination)}</li>)}</ul>
      {canManageReportPanels && <p>{link({ section: "governance", child: "report-panels", label: "Manage report panels" })} — panel visibility only, not Finance or report-data authority.</p>}
      <p>Recorded prototype collections are not real payments or accounting receivables. Inventory quantities are not valuation. Authorized operational Excel/Word exports are not Finance statements or draft export rights.</p>
    </section>
    <section className="governance-section-block" aria-labelledby="finance-isolated-heading">
      <h3 id="finance-isolated-heading">Implemented only in the isolated Finance lab</h3>
      <ul>
        <li>Draft account/dimension setup and balanced journal review; recurring, opening-balance, accrual/prepayment, allocation and source-linked reversal proposals.</li>
        <li>Budget scenarios, manual reconciliation, expense/petty-cash, fixed-asset and cash-forecast proposals.</li>
        <li>Supplier invoice, credit/debit and advance proposals; customer invoice and credit proposals.</li>
        <li>Saved-draft review/reopen, immutable revisions, competing-edit recovery and request-status recovery within the tested scope.</li>
      </ul>
      <p>These use disposable PostgreSQL and explicit synthetic test access. They do not post, pay, capitalize, reconcile operational accounts or issue client documents.</p>
      <p>The owner/operator review procedure is in INTEGRATION_REVIEW_GUIDE.md in this project. The isolated lab is not launched or linked from this shared page.</p>
    </section>
    <section className="governance-section-block" aria-labelledby="finance-pending-heading">
      <h3 id="finance-pending-heading">Not available as operational Finance</h3>
      <p>Live GL/AP/AR, bank integration, financial posting, Finance approvals, tax activation, period close and financial statement issuance remain incomplete. Shared Finance access and authoritative source/mapping rules are not configured.</p>
      <p>Schema readiness is not permission or operational readiness. Existing staff, report-manager and system-administrator powers do not establish Finance authority. No whole-section team acceptance or production readiness is claimed.</p>
    </section>
  </section>;
}
