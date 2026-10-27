import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  downloadReportExport,
  saveReportPanelManagement,
  saveReportingConfiguration,
  useReportCatalog,
  useReportData,
  useReportPanelManagement,
  useSalesExamples,
  useReportingConfiguration,
} from "./reporting-api";
import type {
  ReportCapabilityCode,
  ReportConfiguration,
  ReportFilters,
  ReportPanelCode,
  ReportPanelManagement,
  ReportRow,
} from "./reporting-api";
import { PersonalReportingWorkspace } from "./PersonalReportingWorkspace";
import { VisualDashboard } from "./ReportVisuals";

type LocationChoice = { id: number; name: string; business_units: number[] };

const capabilitiesByPanel: Record<ReportPanelCode, ReportCapabilityCode[]> = {
  sales: ["sales"],
  best_sellers: ["sales"],
  costs_expenses: ["supplier_cost", "landed_cost", "expenses"],
  gross_margin: ["sales", "gross_margin", "supplier_cost"],
  inventory: ["inventory"],
  purchase_history: ["purchase_history"],
  transfer_history: ["transfer_history"],
  budgets_actuals: ["budgets"],
};

const periods = ["daily", "monthly", "quarterly"] as const;

function todayIso() {
  const now = new Date();
  return formatDateInput(now);
}

function formatDateInput(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function calendarWindow(granularity: ReportFilters["period"]) {
  const now = new Date();
  const end = formatDateInput(now);
  if (granularity === "daily") return { start: end, end };
  const month = granularity === "quarterly"
    ? Math.floor(now.getMonth() / 3) * 3
    : now.getMonth();
  return {
    start: formatDateInput(new Date(now.getFullYear(), month, 1)),
    end,
  };
}

function formatHeader(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function ReportTable({ rows, fields }: { rows: ReportRow[]; fields: string[] }) {
  return (
    <div className="report-table-scroll">
      <table className="report-table">
        <thead><tr>{fields.map((field) => <th scope="col" key={field}>{formatHeader(field)}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${String(row.period ?? row.item_id ?? row.description ?? "row")}-${index}`}>
              {fields.map((field) => <td key={field}>{row[field] === null || row[field] === undefined ? "—" : String(row[field])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ReportsArea({
  csrfToken,
  selectedUnitId,
  canManagePanels,
  onManagePanels,
}: {
  csrfToken: string;
  selectedUnitId: number;
  canManagePanels: boolean;
  onManagePanels?: () => void;
}) {
  const catalog = useReportCatalog(true);
  const [panel, setPanel] = useState<ReportPanelCode | null>(null);
  const [period, setPeriod] = useState<ReportFilters["period"]>("monthly");
  const [start, setStart] = useState(() => calendarWindow("monthly").start);
  const [end, setEnd] = useState(todayIso);
  const [scopeKey, setScopeKey] = useState("");
  const [scopeContextUnitId, setScopeContextUnitId] = useState(selectedUnitId);
  const [location, setLocation] = useState("");
  const [locationScopeKey, setLocationScopeKey] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [exporting, setExporting] = useState("");
  const [exportError, setExportError] = useState("");
  const [exportSuccess, setExportSuccess] = useState("");
  const [view, setView] = useState<"standard" | "visual">("standard");
  const visual = view === "visual";

  useEffect(() => {
    if (catalog.loading) return;
    if (!catalog.data?.panels.length) {
      setPanel(null);
      return;
    }
    if (!panel || !catalog.data.panels.some((item) => item.code === panel)) {
      setPanel(catalog.data.panels[0].code);
    }
  }, [catalog.data, catalog.loading, panel]);

  const requiredCapabilities = useMemo(() => (visual
    // The dashboard shows every assigned panel, so offer every scope any of them can use.
    ? [...new Set((catalog.data?.panels ?? []).flatMap((item) => capabilitiesByPanel[item.code]))]
    : panel ? capabilitiesByPanel[panel] : capabilitiesByPanel.sales), [visual, catalog.data, panel]);
  const scopeOptions = useMemo(() => [
    ...(() => {
      const scopes = requiredCapabilities.map((code) => catalog.data?.scopes[code]);
      const permitted = (kind: "business_units" | "companies") => {
        const records = scopes.map((item) => item?.[kind] ?? []);
        const indexed = new Map<number, { id: number; code: string; name: string }>();
        records.flat().forEach((item) => indexed.set(item.id, item));
        if (panel === "gross_margin" && !visual) {
          return [...indexed.values()].filter((item) => records.every((list) => list.some((entry) => entry.id === item.id)));
        }
        return [...indexed.values()];
      };
      return [
        ...permitted("business_units").map((item) => ({ key: `unit:${item.id}`, label: `${item.name} · ${item.code}` })),
        ...permitted("companies").map((item) => ({ key: `company:${item.id}`, label: `${item.name} · consolidated company` })),
      ];
    })(),
  ], [catalog.data, panel, requiredCapabilities, visual]);

  const preferredScopeKey = scopeOptions.find((item) => item.key === `unit:${selectedUnitId}`)?.key
    ?? scopeOptions[0]?.key
    ?? "";
  const selectedUnitChanged = scopeContextUnitId !== selectedUnitId;
  const effectiveScopeKey = selectedUnitChanged
    ? preferredScopeKey
    : scopeOptions.some((item) => item.key === scopeKey)
      ? scopeKey
      : preferredScopeKey;

  useEffect(() => {
    if (catalog.loading) return;
    if (selectedUnitChanged) {
      setScopeKey(preferredScopeKey);
      setScopeContextUnitId(selectedUnitId);
      return;
    }
    if (scopeKey === effectiveScopeKey) return;
    setScopeKey(effectiveScopeKey);
  }, [catalog.loading, effectiveScopeKey, preferredScopeKey, scopeContextUnitId, scopeKey, selectedUnitChanged, selectedUnitId]);

  const chosenUnitId = effectiveScopeKey.startsWith("unit:") ? Number(effectiveScopeKey.slice(5)) : null;
  const chosenCompanyId = effectiveScopeKey.startsWith("company:") ? Number(effectiveScopeKey.slice(8)) : null;
  const locationOptions = useMemo(() => {
    if (!catalog.data || !panel) return [] as LocationChoice[];
    const scoped = requiredCapabilities.map(
      (code) => catalog.data?.scopes[code]?.locations ?? [],
    );
    const all = new Map<number, LocationChoice>();
    scoped.flat().forEach((item) => all.set(item.id, item));
    const companyUnitIds = chosenCompanyId
      ? requiredCapabilities
          .flatMap((code) => catalog.data?.scopes[code]?.companies ?? [])
          .find((item) => item.id === chosenCompanyId)?.business_unit_ids ?? []
      : [];
    return [...all.values()].filter((item) => {
      const unitIds = chosenUnitId ? [chosenUnitId] : companyUnitIds;
      if (unitIds.length && !item.business_units.some((id) => unitIds.includes(id))) return false;
      if (panel === "gross_margin" && !visual) {
        return scoped.every((list) => list.some((entry) => entry.id === item.id));
      }
      return true;
    });
  }, [catalog.data, panel, requiredCapabilities, chosenUnitId, chosenCompanyId, visual]);
  const effectiveLocation = locationScopeKey === effectiveScopeKey
    && locationOptions.some((item) => String(item.id) === location)
    ? location
    : "";
  useEffect(() => {
    if (catalog.loading) return;
    if (locationScopeKey === effectiveScopeKey && (!location || effectiveLocation === location)) return;
    setLocation("");
    setLocationScopeKey(effectiveScopeKey);
  }, [catalog.loading, effectiveLocation, effectiveScopeKey, location, locationScopeKey]);
  useEffect(() => {
    setPage(1);
  }, [panel, effectiveScopeKey, period, start, end, effectiveLocation, pageSize]);

  const filters: ReportFilters = {
    period: period ?? "monthly",
    start,
    end,
    ...(chosenUnitId ? { unit: chosenUnitId } : {}),
    ...(chosenCompanyId ? { company: chosenCompanyId } : {}),
    ...(effectiveLocation ? { location: Number(effectiveLocation) } : {}),
  };
  const pageFilters: ReportFilters = { ...filters, page, page_size: pageSize };
  const report = useReportData(
    panel,
    pageFilters,
    Boolean(!visual && panel && effectiveScopeKey && !catalog.loading && !catalog.error),
  );
  const salesExamples = useSalesExamples(
    filters,
    Boolean(!visual && panel === "sales" && effectiveScopeKey && !catalog.loading && !catalog.error),
  );

  async function exportFile(format: "xlsx" | "docx") {
    if (!panel || exporting) return;
    setExportError("");
    setExportSuccess("");
    setExporting(format);
    try {
      await downloadReportExport(csrfToken, panel, filters, format);
      setExportSuccess(`${format.toUpperCase()} export downloaded.`);
    } catch (reason) {
      report.refresh();
      catalog.refresh();
      setExportError(reason instanceof Error ? reason.message : "The report export could not be completed.");
    } finally {
      setExporting("");
    }
  }

  return (
    <section className="reports-area">
      <div className="report-intro">
        <div>
          <span className="eyebrow">AUTHORIZED SYNTHETIC REPORTING</span>
          <h2>Reporting, within your grant.</h2>
          <p>Only server-assigned panels are listed. Panel assignment does not grant access to underlying data.</p>
        </div>
        <span className="report-test-stamp">TEST ONLY<br />SYNTHETIC DATA</span>
      </div>

      {catalog.loading && <div className="report-skeleton" role="status" aria-label="Loading authorized reports"><span /><span /><span /></div>}
      {!catalog.loading && catalog.error && (
        <div className="report-denial" role="alert"><strong>Reporting catalog unavailable</strong><p>{catalog.error}</p><button className="button button-outline" onClick={catalog.refresh}>Retry catalog</button></div>
      )}
      {!catalog.loading && !catalog.error && !catalog.data?.panels.length && (
        <div className="report-empty"><span className="report-empty-index">00</span><h3>No report panels assigned</h3><p>Ask a synthetic staff administrator to assign a report panel. Data access is granted separately.</p></div>
      )}

      {canManagePanels && <div className="report-manage-link">
        <span>Panel visibility is managed separately from report data access.</span>
        <button className="button button-outline" type="button" onClick={onManagePanels}>Manage report panels</button>
      </div>}

      <PersonalReportingWorkspace
        csrfToken={csrfToken}
        panels={catalog.data?.panels ?? []}
        filters={pageFilters}
      />
      {!!catalog.data?.panels.length && (
        <>
          <div className="report-view-toggle" role="group" aria-label="Report view">
            <button type="button" aria-pressed={!visual} onClick={() => setView("standard")}>Standard report</button>
            <button type="button" aria-pressed={visual} onClick={() => setView("visual")}>Visual dashboard</button>
          </div>
          <div className="report-controls">
            {!visual && <label className="report-field report-panel-select"><span>Assigned panel</span>
              <select value={panel ?? ""} onChange={(event) => setPanel(event.target.value as ReportPanelCode)}>
                {catalog.data.panels.map((item) => <option key={item.code} value={item.code}>{item.label}</option>)}
              </select>
            </label>}
            <label className="report-field"><span>Reporting scope</span>
              <select value={effectiveScopeKey} onChange={(event) => setScopeKey(event.target.value)}>
                {!scopeOptions.length && <option value="">No scopes listed for this capability</option>}
                {scopeOptions.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
              </select>
            </label>
            <label className="report-field"><span>Granularity</span>
              <select value={period} onChange={(event) => {
                const nextPeriod = event.target.value as NonNullable<ReportFilters["period"]>;
                const window = calendarWindow(nextPeriod);
                setPeriod(nextPeriod);
                setStart(window.start);
                setEnd(window.end);
              }}>
                {periods.map((value) => <option key={value} value={value}>{formatHeader(value)}</option>)}
              </select>
            </label>
            <label className="report-field"><span>Location</span>
              <select
                value={effectiveLocation}
                onChange={(event) => {
                  setLocation(event.target.value);
                  setLocationScopeKey(effectiveScopeKey);
                }}
                disabled={!locationOptions.length}
              >
                <option value="">All authorized locations</option>
                {locationOptions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </label>
            <label className="report-field"><span>From</span><input type="date" value={start} max={end} onChange={(event) => setStart(event.target.value)} /></label>
            <label className="report-field"><span>Through</span><input type="date" value={end} min={start} onChange={(event) => setEnd(event.target.value)} /></label>
          </div>
          {!scopeOptions.length && <div className="report-denial" role="status"><strong>No report-data scope assigned</strong><p>A panel assignment does not grant data access. Ask synthetic staff to grant an applicable capability and business-unit/company scope.</p></div>}
          {visual && scopeOptions.length > 0 && <VisualDashboard panels={catalog.data.panels} filters={filters} />}
          {!visual && <>
          <div className="report-output">
            <div className="report-output-head">
              <div><span className="eyebrow">SERVER RESPONSE</span><h3>{report.data?.title ?? catalog.data.panels.find((item) => item.code === panel)?.label}</h3></div>
              <div className="report-export-actions">
                <button className="button button-outline" onClick={() => void exportFile("xlsx")} disabled={!report.data || Boolean(exporting)}>{exporting === "xlsx" ? "Preparing Excel…" : "Export Excel"}</button>
                <button className="button button-outline" onClick={() => void exportFile("docx")} disabled={!report.data || Boolean(exporting)}>{exporting === "docx" ? "Preparing Word…" : "Export Word"}</button>
                <small className="report-export-hint">Exports include all authorized rows, not only this page.</small>
              </div>
            </div>
            {report.loading && <div className="report-skeleton report-data-skeleton" role="status" aria-label="Loading report data"><span /><span /><span /></div>}
            {!report.loading && report.error && <div className="report-denial" role="alert"><strong>Report data was not returned</strong><p>{report.error}</p><small>Access denials are shown without displaying report rows.</small><button className="button button-outline" type="button" onClick={report.refresh}>Retry report</button></div>}
            {!report.loading && !report.error && report.data && (
              <>
                <div className="report-meta">
                  <span>{report.data.scope.scope_type === "company" ? `Consolidated · ${report.data.scope.company?.name ?? "authorized company"}` : report.data.scope.business_unit?.name ?? `${report.data.scope.business_unit_ids.length} authorized units`}</span>
                 <span>{report.data.filters.start} — {report.data.filters.end} · {formatHeader(report.data.granularity)} calendar periods</span>
                  <span>{report.data.currency}</span>
                </div>
                {report.data.panel === "sales" || report.data.panel === "best_sellers" ? (
                  <div className="report-caveat report-caveat-operational" role="note" aria-label="Operational sales source">
                    <strong>Operational sales only</strong>
                    <span>Rows, totals, trends, pagination, exports, and saved report results use transactional SalesReportLine records only. Report-only examples appear below in a separately filtered and counted section.</span>
                  </div>
                ) : (
                  <div className="report-caveat"><strong>Synthetic test data only</strong><span>Values are not approved Tivoli accounting policy or a statement of complete financial coverage.</span></div>
                )}
                <div className="report-policy"><span className="report-policy-label">API policy label</span>{report.data.policy_label || "No policy label returned."}</div>
                {report.data.note && <div className="report-note report-api-note"><strong>API note</strong><span>{report.data.note}</span></div>}
                {report.data.panel === "budgets_actuals" && report.data.budgets_configured !== undefined && <p className="report-budget-status" role="status">{report.data.budgets_configured ? "Synthetic budget fixtures are configured for this response." : "No synthetic budget fixtures are configured for this response."}</p>}
                <p className="report-calendar-note">Daily, monthly, and quarterly groupings use configurable calendar periods. They are not an approved fiscal calendar.</p>
                {report.data.summary && Object.keys(report.data.summary).length > 0 && (
                  <div
                    className="report-summary"
                    aria-label={report.data.panel === "sales" ? "Operational sales summary" : "Report summary"}
                  >
                    {Object.entries(report.data.summary).map(([key, value]) => <div key={key}><small>{formatHeader(key)}</small><strong>{value}</strong></div>)}
                  </div>
                )}
                {report.data.categories_permission_required && <p className="report-note">Category breakdown is not available without its separate category-sales permission.</p>}
                {report.data.rows.length ? <ReportTable rows={report.data.rows} fields={report.data.fields} /> : (
                  <div className="report-empty report-no-rows"><span className="report-empty-index">—</span><h3>No rows returned</h3><p>The authorized report has no synthetic records for this scope and date range.</p></div>
                )}
                <div className="report-pagination">
                  <label className="report-page-size">
                    <span>Rows per page</span>
                    <select
                      aria-label="Report rows per page"
                      value={pageSize}
                      onChange={(event) => setPageSize(Number(event.target.value))}
                    >
                      {[25, 50, 100].map((size) => <option key={size} value={size}>{size}</option>)}
                    </select>
                  </label>
                  <nav className="pagination-nav" aria-label="Report pagination">
                    <button
                      className="button button-outline"
                      type="button"
                      onClick={() => setPage(report.data?.pagination.previous_page ?? 1)}
                      disabled={report.loading || report.data.pagination.previous_page === null}
                      aria-label="Previous report page"
                    >
                      Previous
                    </button>
                    <span role="status" aria-live="polite">
                      Page {report.data.pagination.page} of {report.data.pagination.total_pages}
                      {" · "}
                      {report.data.pagination.total_count
                        ? `showing ${(report.data.pagination.page - 1) * report.data.pagination.page_size + 1}–${Math.min(report.data.pagination.page * report.data.pagination.page_size, report.data.pagination.total_count)} of ${report.data.pagination.total_count} rows`
                        : "0 rows"}
                    </span>
                    <button
                      className="button button-outline"
                      type="button"
                      onClick={() => setPage(report.data?.pagination.next_page ?? report.data!.pagination.page)}
                      disabled={report.loading || report.data.pagination.next_page === null}
                      aria-label="Next report page"
                    >
                      Next
                    </button>
                  </nav>
                </div>
                {report.data.trend?.length ? <details className="report-detail"><summary>View returned period summary ({report.data.trend.length})</summary><ReportTable rows={report.data.trend} fields={Object.keys(report.data.trend[0])} /></details> : null}
                {report.data.best_selling_items?.length ? <details className="report-detail"><summary>Best-selling items ({report.data.best_selling_items.length})</summary><ReportTable rows={report.data.best_selling_items} fields={Object.keys(report.data.best_selling_items[0])} /></details> : null}
                {report.data.best_selling_categories?.length ? <details className="report-detail"><summary>Best-selling categories ({report.data.best_selling_categories.length})</summary><ReportTable rows={report.data.best_selling_categories} fields={Object.keys(report.data.best_selling_categories[0])} /></details> : null}
                {report.data.expense_account_detail?.length ? <details className="report-detail"><summary>Expense account detail ({report.data.expense_account_detail.length})</summary><ReportTable rows={report.data.expense_account_detail} fields={Object.keys(report.data.expense_account_detail[0])} /></details> : null}
              </>
            )}
            {exportError && <div className="report-feedback is-error" role="alert">{exportError}</div>}
            {exportSuccess && <div className="report-feedback" role="status">{exportSuccess}</div>}
          </div>
          {panel === "sales" && (
            <section
              className="report-example-panel"
              aria-label="Synthetic examples — not operational sales"
            >
              <div className="report-example-heading">
                <span className="report-example-kicker">ILLUSTRATIVE DATA · TEST ONLY</span>
                <h3>Synthetic examples — not operational sales</h3>
                <p>
                  DemoReportSalesFact examples are scoped separately. They never enter operational SalesReportLine
                  rows, counts, summaries, trends, pagination, exports, or saved report results.
                </p>
              </div>
              {!scopeOptions.length && !catalog.loading && (
                <div className="report-denial" role="status">
                  <strong>Examples unavailable</strong>
                  <p>No authorized Sales panel scope is available for this user.</p>
                </div>
              )}
              {salesExamples.loading && (
                <div className="report-skeleton report-data-skeleton" role="status" aria-label="Loading synthetic sales examples">
                  <span /><span /><span />
                </div>
              )}
              {!salesExamples.loading && salesExamples.error && (
                <div className="report-denial" role="alert">
                  <strong>Synthetic examples unavailable</strong>
                  <p>{salesExamples.error}</p>
                </div>
              )}
              {!salesExamples.loading && !salesExamples.error && salesExamples.data && (
                <>
                  <div className="report-example-meta">
                    <span>{salesExamples.data.scope.business_unit?.name ?? "Authorized synthetic scope"}</span>
                    <span>{salesExamples.data.filters.start} — {salesExamples.data.filters.end}</span>
                  </div>
                  <div className="report-example-summary" aria-label="Illustrative example summary">
                    <div>
                      <small>Filtered example count</small>
                      <strong>{salesExamples.data.example_count}</strong>
                    </div>
                    <div>
                      <small>Illustrative revenue · examples only</small>
                      <strong>{salesExamples.data.summary.illustrative_revenue} {salesExamples.data.currency}</strong>
                    </div>
                    <div>
                      <small>Illustrative quantity</small>
                      <strong>{salesExamples.data.summary.illustrative_quantity}</strong>
                    </div>
                  </div>
                </>
              )}
            </section>
          )}
          </>}
        </>
      )}
    </section>
  );
}

export function ReportPanelManager({
  csrfToken,
  state,
  onSaved,
}: {
  csrfToken: string;
  state: ReturnType<typeof useReportPanelManagement>;
  onSaved: () => void;
}) {
  const [targetType, setTargetType] = useState<"user" | "role">("user");
  const [targetId, setTargetId] = useState("");
  const [panelCode, setPanelCode] = useState<ReportPanelCode | "">("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    if (!state.data) return;
    if (!panelCode && state.data.panels.length) setPanelCode(state.data.panels[0].code);
    const targets = targetType === "user" ? state.data.users : state.data.roles;
    if (!targets.some((target) => String(target.id) === targetId)) {
      setTargetId(targets[0] ? String(targets[0].id) : "");
    }
  }, [state.data, targetType, panelCode, targetId]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!panelCode || !targetId || busy) return;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await saveReportPanelManagement(csrfToken, {
        action: "assign_panel",
        panel: panelCode,
        ...(targetType === "user" ? { user_id: Number(targetId) } : { role_id: Number(targetId) }),
      });
      setSuccess("Report panel assignment saved. Data access remains separate.");
      onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Panel assignment could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(grantId: number) {
    if (busy || !window.confirm("Revoke this report panel assignment? This does not change data access.")) return;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await saveReportPanelManagement(csrfToken, { action: "revoke_panel", grant_id: grantId });
      setSuccess("Report panel assignment revoked. Data access is unchanged.");
      onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Panel assignment could not be revoked.");
    } finally {
      setBusy(false);
    }
  }

  if (state.loading) return <section className="report-panel-manager" aria-label="Panel assignment management"><div className="report-skeleton" role="status" aria-label="Loading panel assignment controls"><span /><span /><span /></div></section>;
  if (state.error || !state.data) return (
    <section className="report-panel-manager">
      <div className="report-manager-heading"><div><span className="eyebrow">RESTRICTED CONTROLS</span><h3>Panel assignment</h3></div></div>
      <div className="report-denial" role="alert"><strong>Panel management unavailable</strong><p>{state.error || "No panel management response was returned."}</p><button className="button button-outline" type="button" onClick={state.refresh}>Retry panel controls</button></div>
    </section>
  );

  const data: ReportPanelManagement = state.data;
  const targetOptions = targetType === "user"
    ? data.users.map((user) => ({ id: user.id, label: user.username }))
    : data.roles.map((role) => ({ id: role.id, label: role.name }));
  return (
    <section className="report-panel-manager" aria-labelledby="report-manager-title">
      <div className="report-manager-heading">
        <div><span className="eyebrow">RESTRICTED · PANEL ACCESS ONLY</span><h3 id="report-manager-title">Manage report panels</h3><p>Assign or revoke which report panels appear for a user or role. This does not grant data access; do not use this control to infer scope or permission.</p></div>
        <span className="report-test-stamp">SYNTHETIC<br />CONTROL</span>
      </div>
      {error && <div className="report-feedback is-error" role="alert">{error}</div>}
      {success && <div className="report-feedback" role="status">{success}</div>}
      <form className="report-panel-form" onSubmit={submit}>
        <label className="report-field"><span>Assign to</span><select value={targetType} onChange={(event) => { setTargetType(event.target.value as "user" | "role"); setTargetId(""); }}><option value="user">Synthetic user</option><option value="role">Reporting role</option></select></label>
        <label className="report-field"><span>{targetType === "user" ? "User" : "Role"}</span><select required value={targetId} onChange={(event) => setTargetId(event.target.value)}><option value="">Select a {targetType}</option>{targetOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>
        <label className="report-field"><span>Report panel</span><select required value={panelCode} onChange={(event) => setPanelCode(event.target.value as ReportPanelCode)}><option value="">Select a panel</option>{data.panels.map((item) => <option key={item.code} value={item.code}>{item.label}</option>)}</select></label>
        <button className="button button-dark" type="submit" disabled={busy || !targetId || !panelCode || !data.panels.length}>{busy ? "Saving…" : "Assign panel only"}</button>
      </form>
      {!targetOptions.length && <p className="report-admin-note">No {targetType === "user" ? "synthetic users" : "reporting roles"} are available to assign.</p>}
      <div className="report-managed-grants">
        <div className="grant-list-heading"><h4>Panel assignments</h4><span>{data.panel_grants.filter((grant) => !grant.revoked).length} active</span></div>
        {data.panel_grants.length ? data.panel_grants.map((grant) => (
          <div className={`grant-row ${grant.revoked ? "is-revoked" : ""}`} key={grant.id}>
            <div><strong>{grant.username ?? grant.role_name ?? "Unknown principal"}</strong><small>{data.panels.find((item) => item.code === grant.panel)?.label ?? grant.panel}</small></div>
            <span className="grant-status">{grant.revoked ? "Revoked" : "Active"}</span>
            {!grant.revoked && <button className="button button-outline" type="button" disabled={busy} onClick={() => void revoke(grant.id)}>Revoke</button>}
          </div>
        )) : <p className="empty-state">No report panel assignments have been returned.</p>}
      </div>
      <p className="report-manager-caveat"><strong>Separate controls:</strong> report-panel assignments do not grant data access. Data grants and server-side report scopes remain governed separately.</p>
    </section>
  );
}

export function GovernanceReportPanelManager({ csrfToken }: { csrfToken: string }) {
  const state = useReportPanelManagement(true);
  return (
    <ReportPanelManager
      csrfToken={csrfToken}
      state={state}
      onSaved={() => state.refresh()}
    />
  );
}

type Target = { user_id?: number; role_id?: number };

function targetPayload(targetType: string, targetId: string): Target {
  return targetType === "role" ? { role_id: Number(targetId) } : { user_id: Number(targetId) };
}

export function ReportingAdmin({ csrfToken }: { csrfToken: string }) {
  const config = useReportingConfiguration(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [roleCode, setRoleCode] = useState("test-");
  const [roleName, setRoleName] = useState("");
  const [memberRole, setMemberRole] = useState("");
  const [memberUser, setMemberUser] = useState("");
  const [panelTargetType, setPanelTargetType] = useState("user");
  const [panelTargetId, setPanelTargetId] = useState("");
  const [dataTargetType, setDataTargetType] = useState("user");
  const [dataTargetId, setDataTargetId] = useState("");
  const [panelCode, setPanelCode] = useState<ReportPanelCode | "">("");
  const [capability, setCapability] = useState<ReportCapabilityCode | "">("");
  const [scopeType, setScopeType] = useState("unit");
  const [scopeId, setScopeId] = useState("");
  const [budgetUnit, setBudgetUnit] = useState("");
  const [categoryItem, setCategoryItem] = useState("");
  const [categoryLabel, setCategoryLabel] = useState("");

  useEffect(() => {
    const data = config.data;
    if (!data) return;
    if (!panelCode) setPanelCode(data.panels[0]?.code ?? "");
    if (!capability) setCapability(data.capabilities[0]?.code ?? "");
    if (!panelTargetId) setPanelTargetId(data.users[0] ? String(data.users[0].id) : "");
    if (!dataTargetId) setDataTargetId(data.users[0] ? String(data.users[0].id) : "");
    if (!memberRole) setMemberRole(data.roles[0] ? String(data.roles[0].id) : "");
    if (!memberUser) setMemberUser(data.users[0] ? String(data.users[0].id) : "");
    if (!scopeId) setScopeId(data.units[0] ? String(data.units[0].id) : "");
    if (!budgetUnit) setBudgetUnit(data.units[0] ? String(data.units[0].id) : "");
    if (!categoryItem) setCategoryItem(data.items[0] ? String(data.items[0].id) : "");
  }, [config.data, panelCode, capability, panelTargetId, dataTargetId, memberRole, memberUser, scopeId, budgetUnit, categoryItem]);

  const locationOptions = useMemo(() => (config.data?.locations ?? []).filter((item) => item.business_units.includes(Number(budgetUnit))), [config.data?.locations, budgetUnit]);

  async function commit(payload: Record<string, unknown>, message: string) {
    if (busy) return;
    setBusy(true); setError(""); setSuccess("");
    try {
      await saveReportingConfiguration(csrfToken, payload);
      setSuccess(message);
      config.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Reporting configuration could not be saved.");
    } finally { setBusy(false); }
  }

  function submitRole(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void commit({ action: "create_role", code: roleCode.trim().toLowerCase(), name: roleName.trim() }, "Synthetic reporting role saved.");
    setRoleName("");
  }

  function grantPanel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!panelCode || !panelTargetId) return;
    void commit({ action: "assign_panel", panel: panelCode, ...targetPayload(panelTargetType, panelTargetId) }, "Report panel assignment saved separately from data access.");
  }

  function grantData(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!capability || !dataTargetId || !scopeId) return;
    void commit({
      action: "grant_data",
      capability,
      ...targetPayload(dataTargetType, dataTargetId),
      ...(scopeType === "unit" ? { business_unit_id: Number(scopeId) } : { legal_entity_id: Number(scopeId) }),
    }, "Explicit report-data capability saved.");
  }

  function saveBudget(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void commit({
      action: "set_budget",
      business_unit_id: Number(budgetUnit),
      location_id: form.get("location") || null,
      metric: form.get("metric"),
      granularity: form.get("granularity"),
      period_start: form.get("period_start"),
      period_end: form.get("period_end"),
      amount: form.get("amount"),
    }, "Synthetic budget saved.");
    event.currentTarget.reset();
  }

  function saveCategory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void commit({ action: "set_category", item_id: Number(categoryItem), category_label: categoryLabel.trim() }, "Synthetic item category label saved.");
  }

  if (config.loading) return <section className="report-admin card content-card"><div className="report-skeleton" role="status" aria-label="Loading reporting administration"><span /><span /><span /></div></section>;
  if (config.error || !config.data) return <section className="report-admin card content-card"><div className="report-denial" role="alert"><strong>Reporting configuration unavailable</strong><p>{config.error || "No configuration response was returned."}</p><button className="button button-outline" onClick={config.refresh}>Retry configuration</button></div></section>;

  const data: ReportConfiguration = config.data;
  return (
    <section className="report-admin content-card">
      <div className="report-admin-heading">
        <div><span className="eyebrow">SYNTHETIC REPORTING CONTROLS</span><h2>Reporting governance</h2><p>Panel assignment and report-data capability are distinct grants. These settings never alter operational access.</p></div>
        <span className="report-test-stamp">TEST ONLY<br />STAFF AREA</span>
      </div>
      {error && <div className="report-feedback is-error" role="alert">{error}</div>}
      {success && <div className="report-feedback" role="status">{success}</div>}
      <div className="report-admin-grid">
        <form className="report-admin-block" onSubmit={submitRole}>
          <span className="report-block-index">01 / ROLES</span><h3>Create reporting role</h3>
          <label>Role code<input required pattern="test-[a-z0-9-]+" maxLength={40} value={roleCode} onChange={(event) => setRoleCode(event.target.value)} /></label>
          <label>Role name<input required maxLength={100} value={roleName} onChange={(event) => setRoleName(event.target.value)} /></label>
          <button className="button button-dark" disabled={busy}>Create synthetic role</button>
          <p className="report-admin-note">Users shown below are existing synthetic demo accounts. This API supports assigning those users to roles; it does not expose demo-account creation.</p>
        </form>
        <form className="report-admin-block" onSubmit={(event) => { event.preventDefault(); if (memberRole && memberUser) void commit({ action: "add_member", role_id: Number(memberRole), user_id: Number(memberUser) }, "Synthetic demo user added to reporting role."); }}>
          <span className="report-block-index">02 / MEMBERS</span><h3>Add synthetic user to role</h3>
          <label>Reporting role<select required value={memberRole} onChange={(event) => setMemberRole(event.target.value)}>{data.roles.map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}</select></label>
          <label>Existing demo user<select required value={memberUser} onChange={(event) => setMemberUser(event.target.value)}>{data.users.map((user) => <option key={user.id} value={user.id}>{user.username}</option>)}</select></label>
          <button className="button button-dark" disabled={busy || !data.roles.length || !data.users.length}>Add role member</button>
          {!data.users.length && <p className="report-admin-note">No synthetic demo users are available to assign.</p>}
        </form>
        <form className="report-admin-block" onSubmit={grantPanel}>
          <span className="report-block-index">03 / PANEL ACCESS</span><h3>Assign report panel</h3>
          <TargetFields targetType={panelTargetType} setTargetType={setPanelTargetType} targetId={panelTargetId} setTargetId={setPanelTargetId} userId={data.users[0]?.id} roleId={data.roles[0]?.id} options={panelTargetType === "role" ? data.roles.map((role) => ({ id: role.id, name: role.name })) : data.users.map((user) => ({ id: user.id, name: user.username }))} />
          <label>Panel<select required value={panelCode} onChange={(event) => setPanelCode(event.target.value as ReportPanelCode)}>{data.panels.map((item) => <option key={item.code} value={item.code}>{item.label}</option>)}</select></label>
          <button className="button button-dark" disabled={busy || !panelCode || !panelTargetId || (panelTargetType === "role" ? !data.roles.length : !data.users.length)}>Assign panel only</button>
        </form>
        <form className="report-admin-block" onSubmit={grantData}>
          <span className="report-block-index">04 / DATA ACCESS</span><h3>Grant data capability</h3>
          <TargetFields targetType={dataTargetType} setTargetType={setDataTargetType} targetId={dataTargetId} setTargetId={setDataTargetId} userId={data.users[0]?.id} roleId={data.roles[0]?.id} options={dataTargetType === "role" ? data.roles.map((role) => ({ id: role.id, name: role.name })) : data.users.map((user) => ({ id: user.id, name: user.username }))} />
          <label>Capability<select required value={capability} onChange={(event) => setCapability(event.target.value as ReportCapabilityCode)}>{data.capabilities.map((item) => <option key={item.code} value={item.code}>{item.label}</option>)}</select></label>
          <label>Data scope<select value={scopeType} onChange={(event) => { setScopeType(event.target.value); setScopeId(event.target.value === "unit" ? String(data.units[0]?.id ?? "") : String(data.companies[0]?.id ?? "")); }}><option value="unit">Business unit</option><option value="company">Consolidated company</option></select></label>
          <label>{scopeType === "unit" ? "Business unit" : "Company-wide scope"}<select required value={scopeId} onChange={(event) => setScopeId(event.target.value)}>{(scopeType === "unit" ? data.units : data.companies).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.code}</option>)}</select></label>
          <button className="button button-dark" disabled={busy || !capability || !scopeId || !dataTargetId || (dataTargetType === "role" ? !data.roles.length : !data.users.length)}>Grant data capability</button>
        </form>
        <form className="report-admin-block" onSubmit={saveBudget}>
          <span className="report-block-index">05 / TEST BUDGETS</span><h3>Configure synthetic budget</h3>
          <label>Business unit<select required value={budgetUnit} onChange={(event) => setBudgetUnit(event.target.value)}>{data.units.map((unit) => <option key={unit.id} value={unit.id}>{unit.name}</option>)}</select></label>
          <label>Location<select name="location"><option value="">Business-unit wide</option>{locationOptions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <div className="report-inline-fields"><label>Metric<select name="metric"><option value="sales">Sales</option><option value="supplier_purchase">Supplier purchase value</option><option value="landed_cost">Landed charges</option><option value="expenses">Posted expense accounts</option><option value="gross_margin">Synthetic gross margin</option></select></label><label>Granularity<select name="granularity"><option value="monthly">Monthly</option><option value="quarterly">Quarterly</option><option value="daily">Daily</option></select></label></div>
          <div className="report-inline-fields"><label>Period start<input name="period_start" type="date" required /></label><label>Period end<input name="period_end" type="date" required /></label></div>
          <label>Amount (QAR)<input name="amount" inputMode="decimal" pattern="[0-9]+(\.[0-9]{1,2})?" required /></label>
          <button className="button button-dark" disabled={busy || !data.units.length}>Save synthetic budget</button>
        </form>
        <form className="report-admin-block" onSubmit={saveCategory}>
          <span className="report-block-index">06 / ITEM CATEGORIES</span><h3>Set synthetic category label</h3>
          <label>Synthetic catalog item<select required value={categoryItem} onChange={(event) => setCategoryItem(event.target.value)}>{data.items.map((item) => <option key={item.id} value={item.id}>{item.item_code} · {item.description}</option>)}</select></label>
          <label>Category label<input maxLength={80} value={categoryLabel} onChange={(event) => setCategoryLabel(event.target.value)} /></label>
          <button className="button button-dark" disabled={busy || !categoryItem}>Save category label</button>
        </form>
      </div>

      <div className="report-grant-lists">
        <GrantList title="Panel assignments" grants={data.panel_grants.map((grant) => ({ id: grant.id, subject: grant.username ?? grant.role_name ?? "Unknown principal", detail: data.panels.find((item) => item.code === grant.panel)?.label ?? grant.panel, revoked: grant.revoked, action: "revoke_panel" }))} busy={busy} revoke={(id, action) => void commit({ action, grant_id: id }, "Report panel assignment revoked.")} />
        <GrantList title="Data capabilities" grants={data.data_grants.map((grant) => ({ id: grant.id, subject: grant.username ?? grant.role_name ?? "Unknown principal", detail: `${data.capabilities.find((item) => item.code === grant.capability)?.label ?? grant.capability} · ${grant.business_unit ?? grant.company ?? "unscoped"}`, revoked: grant.revoked, action: "revoke_data" }))} busy={busy} revoke={(id, action) => void commit({ action, grant_id: id }, "Report-data capability revoked.")} />
        <GrantList title="Role memberships" grants={data.memberships.map((membership) => ({ id: membership.id, subject: membership.username, detail: membership.role_name, revoked: membership.revoked, action: "revoke_member" }))} busy={busy} revoke={(id) => void commit({ action: "revoke_member", membership_id: id }, "Synthetic reporting-role membership revoked.")} />
      </div>
      <details className="report-admin-details"><summary>Current synthetic budgets and item labels</summary>
        <div className="report-config-list">
          <div><h4>Budgets</h4>{data.budgets.length ? data.budgets.map((budget) => <p key={budget.id}>{data.units.find((unit) => unit.id === budget.business_unit_id)?.name} · {formatHeader(budget.metric)} · {budget.period_start} to {budget.period_end} · {budget.amount} QAR</p>) : <p>No synthetic budgets configured.</p>}</div>
          <div><h4>Item category labels</h4>{data.items.length ? data.items.map((item) => <p key={item.id}>{item.item_code} · {item.category_label || "Unlabeled"}</p>) : <p>No synthetic items returned.</p>}</div>
        </div>
      </details>
    </section>
  );
}

function TargetFields({ targetType, setTargetType, targetId, setTargetId, userId, roleId, options }: {
  targetType: string;
  setTargetType: (value: string) => void;
  targetId: string;
  setTargetId: (value: string) => void;
  userId?: number;
  roleId?: number;
  options: Array<{ id: number; name: string }>;
}) {
  return (
    <>
      <label>Grant to<select value={targetType} onChange={(event) => {
        const next = event.target.value;
        setTargetType(next);
        setTargetId(String(next === "role" ? roleId ?? "" : userId ?? ""));
      }}><option value="user">Synthetic user</option><option value="role">Reporting role</option></select></label>
      <label>{targetType === "user" ? "User" : "Role"}<select required value={targetId} onChange={(event) => setTargetId(event.target.value)}>{options.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    </>
  );
}

function GrantList({ title, grants, busy, revoke }: {
  title: string;
  grants: Array<{ id: number; subject: string; detail: string; revoked: boolean; action: string }>;
  busy: boolean;
  revoke: (id: number, action: string) => void;
}) {
  return (
    <section className="grant-list"><div className="grant-list-heading"><h3>{title}</h3><span>{grants.filter((grant) => !grant.revoked).length} active</span></div>
      {grants.length ? grants.map((grant) => <div className={`grant-row ${grant.revoked ? "is-revoked" : ""}`} key={grant.id}>
        <div><strong>{grant.subject}</strong><small>{grant.detail}</small></div><span className="grant-status">{grant.revoked ? "Revoked" : "Active"}</span>
        {!grant.revoked && <button className="button button-outline" disabled={busy} onClick={() => revoke(grant.id, grant.action)}>Revoke</button>}
      </div>) : <p className="empty-state">No grants have been recorded.</p>}
    </section>
  );
}
