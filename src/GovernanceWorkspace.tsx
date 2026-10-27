import type { ReactNode } from "react";

export type GovernanceWorkspaceProps = {
  businessUnitName?: string;
  businessUnitCode?: string;
  isStaff: boolean;
  isSuperuser: boolean;
  canManageReportPanels: boolean;
  onManageReportPanels?: () => void;
  presentation?: ReactNode;
  reportPanelControls?: ReactNode;
  purchasingReview?: ReactNode;
  capacityAdministration?: ReactNode;
  capacityAudit?: ReactNode;
  transferPermissions?: ReactNode;
  focusedChild?: string;
};

export default function GovernanceWorkspace({
  businessUnitName,
  businessUnitCode,
  isStaff,
  isSuperuser,
  canManageReportPanels,
  onManageReportPanels,
  presentation,
  reportPanelControls,
  purchasingReview,
  capacityAdministration,
  capacityAudit,
  transferPermissions,
  focusedChild = "main",
}: GovernanceWorkspaceProps) {
  const workspaceTitle: Record<string, string> = {
    main: "Authority, clearly separated.",
    presentation: "Business-unit palettes",
    "purchasing-config": "Purchasing configuration review",
    reporting: "Reporting governance",
    "report-panels": "Manage report panels",
    "capacity-admin": "Capacity locations & assignments",
    "capacity-audit": "Warehouse capacity audit",
    "transfer-permissions": "Stock transfer permissions",
  };
  const showAuthority = focusedChild === "main" || focusedChild === "authority";
  const showPresentation = focusedChild === "presentation" && isStaff;
  const showPurchasing = focusedChild === "purchasing-config" && isStaff;
  const showReportingBoundary = focusedChild === "reporting" && isStaff;
  const showReportPanels = focusedChild === "report-panels" && canManageReportPanels;
  const showCapacity = focusedChild === "capacity-admin" && isSuperuser;
  const showCapacityAudit = focusedChild === "capacity-audit" && isSuperuser;
  const showTransferPermissions = focusedChild === "transfer-permissions" && isSuperuser;
  return (
    <div className="governance-workspace" data-workspace-section="governance" data-workspace-child={focusedChild} data-workspace-view={`governance/${focusedChild}`}>
      <header className="governance-hero">
        <div>
          <span className="eyebrow">GOVERNANCE / ADMINISTRATION</span>
          <h2 id="governance-workspace-title">{workspaceTitle[focusedChild] ?? "Governance function"}</h2>
          <p>Review only the administrative controls attached to this account. Presentation choices never grant access; each restricted API remains the authority.</p>
        </div>
        <div className="governance-hero-aside">
          <span className="governance-index">07</span>
          <span>{isSuperuser ? "SYSTEM ADMINISTRATOR" : canManageReportPanels ? "PANEL MANAGER" : isStaff ? "STAFF REVIEW" : "STANDARD ACCESS"}</span>
        </div>
      </header>
      <div className="governance-context-strip">
        <span>SELECTED BUSINESS UNIT</span>
        <strong>{businessUnitName ?? "Context unavailable"}</strong>
        <span className="governance-context-code">{businessUnitCode ?? "—"}</span>
        <span className="governance-context-note">Authority is checked independently for every control below.</span>
      </div>
      {showAuthority && <section className="governance-boundaries content-card" id="governance-pane-authority" data-governance-pane="authority" aria-labelledby="governance-boundaries-title">
        <div className="governance-boundary-title">
          <div><span className="eyebrow">ACCESS MAP</span><h2 id="governance-boundaries-title">Your controls are not interchangeable</h2></div>
          <p>Use the labels as a boundary, not a promise: denied or unavailable API responses remain authoritative.</p>
        </div>
        <div className="governance-authority-list">
          <div><span className="authority-marker presentation-marker">P</span><span><strong>Presentation</strong><small>Staff · business-unit palette only</small></span></div>
          <div><span className="authority-marker panels-marker">R</span><span><strong>Report panel assignment</strong><small>Existing panel manager permission · panel visibility only</small></span></div>
          <div><span className="authority-marker system-marker">S</span><span><strong>Physical capacity administration</strong><small>Superuser only · location designation and explicit assignment</small></span></div>
          <div><span className="authority-marker review-marker">V</span><span><strong>Configuration review</strong><small>Staff · selected-unit purchasing configuration, read only</small></span></div>
        </div>
      </section>}

      {showPresentation && (
        <section className="governance-section-block" id="governance-pane-palettes" data-governance-pane="palettes" aria-labelledby="governance-presentation-heading">
          <div className="governance-section-heading">
            <span className="governance-section-index">01</span>
            <div><span className="eyebrow">STAFF CONFIGURATION</span><h2 id="governance-presentation-heading">Presentation identity</h2><p>Palette assignment changes the visual treatment for a unit. It does not alter authorization.</p></div>
          </div>
          {presentation}
        </section>
      )}

      {(showReportingBoundary || showReportPanels) && (
        <section className="governance-section-block" id="governance-pane-reporting" data-governance-pane="reporting" aria-labelledby="governance-panel-heading">
          {showReportPanels ? (
            <div className="report-admin">
              <div className="governance-section-heading"><span className="governance-section-index">02</span><div><span className="eyebrow">EXISTING REPORT MANAGER PERMISSION</span><h2 id="governance-panel-heading">Report panel assignments</h2><p>Assign or revoke panel visibility only. This does not grant access to report data.</p></div></div>
              {reportPanelControls}
            </div>
          ) : showReportingBoundary ? (
            <div className="report-admin governance-report-boundary" aria-label="Reporting governance boundary">
              <span className="eyebrow">REPORTING AUTHORITY</span>
              <strong>{canManageReportPanels
                ? "This account can manage panel visibility."
                : "Panel management is not part of this account’s permissions."}</strong>
              <p>{canManageReportPanels
                ? "This account can manage panel visibility in its dedicated function. That permission does not grant report-data access or management of reporting capabilities."
                : "The Reports workspace remains available under its existing authorization. Staff status alone does not grant panel-management authority."}</p>
              {canManageReportPanels && onManageReportPanels && (
                <button className="button button-outline" type="button" onClick={onManageReportPanels}>
                  Manage report panels
                </button>
              )}
            </div>
          ) : null}
        </section>
      )}

      {showPurchasing && (
        <section className="governance-section-block" id="governance-pane-purchasing" data-governance-pane="purchasing" aria-labelledby="governance-config-heading">
          <div className="governance-section-heading"><span className="governance-section-index">03</span><div><span className="eyebrow">REVIEW ONLY</span><h2 id="governance-config-heading">Selected-unit purchasing setup</h2><p>Inspect the current server-returned configuration without editing it here.</p></div></div>
          {purchasingReview}
        </section>
      )}

      {(showCapacity || showCapacityAudit) && (
        <>
          {showCapacity && <section className="governance-section-block" id="governance-pane-capacity" data-governance-pane="capacity" aria-labelledby="governance-capacity-heading">
            <div className="governance-section-heading"><span className="governance-section-index">04</span><div><span className="eyebrow">SUPERUSER ONLY</span><h2 id="governance-capacity-heading">Physical capacity authority</h2><p>Designate a physical location, assign an existing account explicitly, or revoke that capacity assignment.</p></div></div>
            {capacityAdministration}
          </section>}
          {showCapacityAudit && <section className="governance-section-block" id="governance-pane-capacity-audit" data-governance-pane="audit" aria-labelledby="governance-audit-heading">
            <div className="governance-section-heading"><span className="governance-section-index">05</span><div><span className="eyebrow">SUPERUSER ONLY · READ ONLY</span><h2 id="governance-audit-heading">Capacity audit metadata</h2><p>Review physical-location profile and assignment events only. Capacity measurements are never shown here.</p></div></div>
            {capacityAudit}
          </section>}
        </>
      )}

      {showTransferPermissions && <section className="governance-section-block" id="governance-pane-transfer-permissions" data-governance-pane="transfer-permissions" aria-labelledby="governance-transfer-heading">
        <div className="governance-section-heading"><span className="governance-section-index">06</span><div><span className="eyebrow">SUPERUSER ONLY</span><h2 id="governance-transfer-heading">Stock transfer authority</h2><p>Grant or revoke the three transfer steps for the selected business unit. Every change is recorded.</p></div></div>
        {transferPermissions}
      </section>}

      {focusedChild === "main" && !isStaff && !isSuperuser && !canManageReportPanels && (
        <div className="governance-context-state governance-no-tools" role="status">
          <strong>No governance controls are available to this account.</strong>
          <p>Use the existing Reports and Favorites destinations available to you. This screen does not provide a path to request or infer additional authority.</p>
        </div>
      )}
    </div>
  );
}
