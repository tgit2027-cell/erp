import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  downloadReportExport,
  useReportData,
} from "./reporting-api";
import type {
  ReportData,
  ReportFilters,
  ReportPanelCode,
} from "./reporting-api";
import "./personal-reporting-workspace.css";

type PanelChoice = { code: ReportPanelCode; label: string };
type SavedView = {
  id: number | string;
  name: string;
  panels: string[];
  filters: ReportFilters;
  updated_at: string;
};
type Availability = { panel: string; available: boolean; reason: string };
type ViewDetail = SavedView & { availability: Availability[] };

const allowedFilters = ["unit", "company", "location", "period", "start", "end", "page", "page_size"] as const;

function filterPreferences(filters: ReportFilters): ReportFilters {
  const result: ReportFilters = {};
  for (const key of allowedFilters) {
    const value = filters[key];
    if (value !== undefined && value !== "") {
      (result as Record<string, unknown>)[key] = value;
    }
  }
  return result;
}

function summarizeFilters(filters: ReportFilters) {
  const scope = filters.unit !== undefined && filters.unit !== ""
    ? `Business unit ${filters.unit}`
    : filters.company !== undefined && filters.company !== ""
      ? `Company ${filters.company}`
      : "No saved scope";
  const parts = [
    scope,
    filters.location ? `Location ${filters.location}` : "All locations",
    filters.period ? `${filters.period[0].toUpperCase()}${filters.period.slice(1)}` : "",
    filters.start && filters.end ? `${filters.start} — ${filters.end}` : "",
    filters.page_size ? `${filters.page_size} rows per page` : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

function requestError(data: unknown, fallback: string) {
  if (data && typeof data === "object" && "detail" in data && typeof data.detail === "string") return data.detail;
  return fallback;
}

async function savedRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    credentials: "same-origin",
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(requestError(body, `Request failed (${response.status}).`));
  return body as T;
}

function panelName(code: string, panels: PanelChoice[]) {
  return panels.find((item) => item.code === code)?.label ?? code.replaceAll("_", " ");
}

function ResultTable({ data }: { data: Pick<ReportData, "fields" | "rows"> }) {
  return (
    <div className="personal-view-table-scroll">
      <table className="personal-view-table">
        <thead><tr>{data.fields.map((field) => <th key={field} scope="col">{field.replaceAll("_", " ")}</th>)}</tr></thead>
        <tbody>
          {data.rows.map((row, index) => (
            <tr key={`${String(row.period ?? row.item_id ?? row.description ?? "row")}-${index}`}>
              {data.fields.map((field) => <td key={field}>{row[field] == null ? "—" : String(row[field])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WorkspacePanel({
  panel,
  filters,
  csrfToken,
  availability,
  onExportFailure,
}: {
  panel: ReportPanelCode;
  filters: ReportFilters;
  csrfToken: string;
  availability?: Availability;
  onExportFailure: (panel: ReportPanelCode) => void;
}) {
  const allowed = availability?.available === true;
  const [page, setPage] = useState(filters.page ?? 1);
  const report = useReportData(panel, { ...filters, page }, allowed);
  const [exporting, setExporting] = useState("");
  const [exportError, setExportError] = useState("");
  const [clearedAfterExportError, setClearedAfterExportError] = useState(false);
  useEffect(() => {
    if (clearedAfterExportError && !report.loading && report.data && !report.error) setClearedAfterExportError(false);
  }, [clearedAfterExportError, report.loading, report.data, report.error]);

  async function exportPanel(format: "xlsx" | "docx") {
    setExportError("");
    setExporting(format);
    try {
      await downloadReportExport(csrfToken, panel, filters, format);
    } catch (reason) {
      setClearedAfterExportError(true);
      setExportError(reason instanceof Error ? reason.message : "Export could not be completed.");
      onExportFailure(panel);
      report.refresh();
    } finally {
      setExporting("");
    }
  }

  if (!allowed) {
    return <article className="personal-view-panel is-denied" aria-labelledby={`personal-panel-${panel}`}>
      <header><div><span className="personal-view-index">PANEL</span><h4 id={`personal-panel-${panel}`}>{panel.replaceAll("_", " ")}</h4></div><span className="personal-view-denied">Unavailable</span></header>
      <p>{availability?.reason || "This panel is no longer available for your current access."}</p>
    </article>;
  }

  const displayed = clearedAfterExportError ? null : report.data;
  return (
    <article className="personal-view-panel" aria-labelledby={`personal-panel-${panel}`}>
      <header>
        <div><span className="personal-view-index">AUTHORIZED REPORT</span><h4 id={`personal-panel-${panel}`}>{displayed?.title ?? panel.replaceAll("_", " ")}</h4></div>
        <div className="personal-view-panel-actions">
          <button type="button" className="button button-outline" onClick={() => void exportPanel("xlsx")} disabled={!displayed || Boolean(exporting)}>Export Excel</button>
          <button type="button" className="button button-outline" onClick={() => void exportPanel("docx")} disabled={!displayed || Boolean(exporting)}>Export Word</button>
        </div>
      </header>
      {report.loading && <div className="personal-view-skeleton" role="status" aria-label={`Loading ${panel.replaceAll("_", " ")} report`}><i /><i /><i /></div>}
      {!report.loading && report.error && <div className="personal-view-message is-error" role="alert"><strong>Report data unavailable</strong><p>{report.error}</p><button className="button button-outline" type="button" onClick={report.refresh}>Retry panel</button></div>}
      {exportError && <p className="personal-view-message is-error" role="alert">{exportError}</p>}
      {!report.loading && !report.error && displayed && (
        <>
          <div className="personal-view-meta">
            <span>{displayed.scope.scope_type === "company" ? `Consolidated · ${displayed.scope.company?.name ?? "authorized company"}` : displayed.scope.business_unit?.name ?? `${displayed.scope.business_unit_ids.length} authorized units`}</span>
            <span>{displayed.filters.start} — {displayed.filters.end}</span>
            <span>{displayed.currency}</span>
          </div>
          <p className="personal-view-empty">{displayed.policy_label} · Synthetic only; not approved company accounting policy.</p>
          {displayed.note && <p className="personal-no-push-note">{displayed.note}</p>}
          {displayed.categories_permission_required && <p className="personal-no-push-note">Category breakdown requires its separate category-sales permission.</p>}
          {displayed.summary && <div className="personal-view-summary">{Object.entries(displayed.summary).map(([key, value]) => <div key={key}><small>{key.replaceAll("_", " ")}</small><strong>{value}</strong></div>)}</div>}
          {displayed.rows.length ? <ResultTable data={displayed} /> : <p className="personal-view-empty">No rows were returned for this authorized scope and period.</p>}
          {([
            ["trend", "Period summary"],
            ["best_selling_items", "Best-selling items"],
            ["best_selling_categories", "Best-selling categories"],
            ["expense_account_detail", "Expense account detail"],
          ] as const).map(([key, title]) => {
            const rows = displayed[key];
            return rows?.length ? (
              <details className="personal-view-breakdown" key={key}>
                <summary>{title}</summary>
                <ResultTable data={{ rows, fields: Object.keys(rows[0]) }} />
              </details>
            ) : null;
          })}
          <nav className="personal-view-panel-actions" aria-label={`${panel.replaceAll("_", " ")} workspace pagination`}>
            <button type="button" className="button button-outline" disabled={displayed.pagination.previous_page === null} onClick={() => setPage(displayed.pagination.previous_page ?? 1)}>Previous</button>
            <span role="status">Page {displayed.pagination.page} of {displayed.pagination.total_pages} · {displayed.pagination.total_count} rows</span>
            <button type="button" className="button button-outline" disabled={displayed.pagination.next_page === null} onClick={() => setPage(displayed.pagination.next_page ?? page)}>Next</button>
          </nav>
          <p className="personal-no-push-note">Exports include all authorized rows, not only this page.</p>
        </>
      )}
    </article>
  );
}

export function PersonalReportingWorkspace({
  csrfToken,
  panels,
  filters,
}: {
  csrfToken: string;
  panels: PanelChoice[];
  filters: ReportFilters;
}) {
  const [views, setViews] = useState<SavedView[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [listRevision, setListRevision] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ViewDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [name, setName] = useState("");
  const [assigned, setAssigned] = useState<ReportPanelCode[]>([]);
  const [draftFilters, setDraftFilters] = useState<ReportFilters | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saveMessage, setSaveMessage] = useState("");
  const [activeView, setActiveView] = useState<ViewDetail | null>(null);
  const [activeFilters, setActiveFilters] = useState<ReportFilters | null>(null);
  const [refreshRevision, setRefreshRevision] = useState(0);
  const detailRequest = useRef(0);
  const detailAbort = useRef<AbortController | null>(null);
  const chosenView = useMemo(() => views.find((view) => String(view.id) === selectedId) ?? null, [views, selectedId]);

  const refreshList = useCallback(() => setListRevision((revision) => revision + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    setListLoading(true);
    setListError("");
    savedRequest<{ views: SavedView[] }>("/api/report-views/", { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setViews(result.views ?? []); })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setViews([]);
          setActiveView(null);
          setActiveFilters(null);
          setDetail(null);
          setListError(reason instanceof Error ? reason.message : "Saved views could not be loaded.");
        }
      })
      .finally(() => { if (!controller.signal.aborted) setListLoading(false); });
    return () => controller.abort();
  }, [listRevision]);

  const openView = useCallback(async (view: SavedView, refresh = false) => {
    detailAbort.current?.abort();
    const controller = new AbortController();
    detailAbort.current = controller;
    const requestId = ++detailRequest.current;
    setSelectedId(String(view.id));
    setDetail(null);
    setActiveView(null);
    setActiveFilters(null);
    setDetailError("");
    setDetailLoading(true);
    try {
      const fresh = await savedRequest<ViewDetail>(`/api/report-views/${view.id}/`, { signal: controller.signal });
      if (controller.signal.aborted || requestId !== detailRequest.current) return;
      setDetail(fresh);
      setName(fresh.name);
      setAssigned(fresh.panels as ReportPanelCode[]);
      setDraftFilters(filterPreferences(fresh.filters));
      if (refresh) setRefreshRevision((value) => value + 1);
      setActiveView(fresh);
      setActiveFilters(filterPreferences(fresh.filters));
    } catch (reason) {
      if (controller.signal.aborted || requestId !== detailRequest.current) return;
      setDetail(null);
      setDetailError(reason instanceof Error ? reason.message : "Saved view could not be opened.");
    } finally {
      if (!controller.signal.aborted && requestId === detailRequest.current) setDetailLoading(false);
    }
  }, []);

  useEffect(() => () => {
    detailAbort.current?.abort();
    detailRequest.current += 1;
  }, []);

  useEffect(() => {
    const revalidate = () => {
      if (document.visibilityState === "visible" && chosenView) void openView(chosenView, true);
    };
    window.addEventListener("focus", revalidate);
    document.addEventListener("visibilitychange", revalidate);
    return () => {
      window.removeEventListener("focus", revalidate);
      document.removeEventListener("visibilitychange", revalidate);
    };
  }, [chosenView, openView]);

  function togglePanel(code: ReportPanelCode) {
    setAssigned((current) => current.includes(code) ? current.filter((item) => item !== code) : [...current, code]);
  }
  function movePanel(index: number, direction: -1 | 1) {
    setAssigned((current) => {
      const target = index + direction;
      if (target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }
  function useCurrentFilters() {
    setDraftFilters(filterPreferences(filters));
    setSaveMessage("Current report filters copied into this saved-view draft.");
  }
  function setFilter<K extends keyof ReportFilters>(key: K, value: ReportFilters[K] | "") {
    setDraftFilters((current) => ({ ...(current ?? {}), [key]: value === "" ? undefined : value }));
  }
  function setScopeFilter(key: "unit" | "company", value: string) {
    setDraftFilters((current) => ({
      ...(current ?? {}),
      [key]: value === "" ? undefined : Number(value),
      [key === "unit" ? "company" : "unit"]: undefined,
    }));
  }

  async function saveView(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const preferences = filterPreferences(draftFilters ?? {});
    if (saving) return;
    if (!assigned.length) { setSaveError("Choose at least one assigned panel."); return; }
    if (!(preferences.unit || preferences.company)) { setSaveError("Choose a business-unit or company scope before saving."); return; }
    setSaving(true);
    setSaveError("");
    setSaveMessage("");
    const payload = { name: name.trim(), panels: assigned, filters: preferences };
    try {
      const saved = selectedId
        ? await savedRequest<SavedView>(`/api/report-views/${selectedId}/`, { method: "PUT", headers: { "X-CSRFToken": csrfToken }, body: JSON.stringify(payload) })
        : await savedRequest<SavedView>("/api/report-views/", { method: "POST", headers: { "X-CSRFToken": csrfToken }, body: JSON.stringify(payload) });
      setSaveMessage("Saved-view preferences updated. Report rows are never saved.");
      setSelectedId(String(saved.id));
      setListRevision((value) => value + 1);
      setDetail(null);
      setActiveView(null);
      setActiveFilters(null);
      await openView(saved);
    } catch (reason) {
      setActiveView(null);
      setActiveFilters(null);
      setSaveError(reason instanceof Error ? reason.message : "Saved-view preferences could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function deleteView() {
    if (!chosenView || saving || !window.confirm(`Delete “${chosenView.name}”?`)) return;
    setSaving(true);
    setSaveError("");
    setSaveMessage("");
    try {
      await savedRequest(`/api/report-views/${chosenView.id}/`, { method: "DELETE", headers: { "X-CSRFToken": csrfToken } });
      setViews((current) => current.filter((item) => String(item.id) !== String(chosenView.id)));
      setSelectedId(null);
      setDetail(null);
      setActiveView(null);
      setActiveFilters(null);
      setName("");
      setAssigned([]);
      setDraftFilters(null);
      setSaveMessage("Saved view deleted.");
    } catch (reason) {
      setSaveError(reason instanceof Error ? reason.message : "Saved view could not be deleted.");
    } finally { setSaving(false); }
  }

  function startNew() {
    detailAbort.current?.abort();
    detailRequest.current += 1;
    setSelectedId(null);
    setDetail(null);
    setDetailLoading(false);
    setDetailError("");
    setActiveView(null);
    setActiveFilters(null);
    setName("");
    setAssigned([]);
    setDraftFilters(filterPreferences(filters));
    setSaveError("");
    setSaveMessage("");
  }

  const availability = new Map((activeView?.availability ?? []).map((item) => [item.panel, item]));
  return (
    <section className="personal-reporting-workspace" aria-labelledby="personal-reporting-title">
      <div className="personal-workspace-heading">
        <div><span className="eyebrow">PERSONAL REPORTING</span><h2 id="personal-reporting-title">Saved views</h2><p>Keep a panel arrangement and filter preferences. Access to report data is checked separately every time you open a view.</p></div>
        <button className="button button-outline" type="button" onClick={startNew} disabled={saving}>Create saved view</button>
      </div>
      <div className="personal-workspace-layout">
        <aside className="personal-view-library" aria-label="Saved report views">
          <div className="personal-library-heading"><strong>Your views</strong><button type="button" className="personal-text-button" onClick={refreshList} disabled={listLoading}>Refresh list</button></div>
          {listLoading && <div className="personal-view-skeleton" role="status" aria-label="Loading saved views"><i /><i /><i /></div>}
          {!listLoading && listError && <div className="personal-view-message is-error" role="alert"><p>{listError}</p><button type="button" className="button button-outline" onClick={refreshList}>Retry</button></div>}
          {!listLoading && !listError && !views.length && <div className="personal-library-empty"><span>01</span><strong>No saved views yet</strong><p>Create a private view of panel order and report filters.</p></div>}
          {!listLoading && !listError && views.map((view, index) => (
            <button key={view.id} type="button" className={`personal-view-choice ${selectedId === String(view.id) ? "is-active" : ""}`} onClick={() => void openView(view)} disabled={saving}>
              <span className="personal-choice-index">{String(index + 1).padStart(2, "0")}</span><span><strong>{view.name}</strong><small>{view.panels.length} {view.panels.length === 1 ? "panel" : "panels"} · {summarizeFilters(view.filters)}</small></span>
            </button>
          ))}
        </aside>
        <div className="personal-view-editor">
          {detailLoading && <div className="personal-view-loading" role="status"><div className="personal-view-skeleton"><i /><i /><i /></div><span>Rechecking report availability…</span></div>}
          {detailError && <div className="personal-view-message is-error" role="alert"><strong>Saved view unavailable</strong><p>{detailError}</p><p>Previous report data has been cleared. Retry to check current access.</p>{chosenView && <button type="button" className="button button-outline" onClick={() => void openView(chosenView, true)}>Retry saved view</button>}</div>}
          {!detailLoading && !detailError && (
            <>
              <form className="personal-view-form" onSubmit={saveView}>
                <div className="personal-editor-title"><div><span className="personal-view-index">{selectedId ? "VIEW PREFERENCES" : "NEW VIEW"}</span><h3>{selectedId ? "Edit saved view" : "Compose a view"}</h3></div>{chosenView && <button type="button" className="personal-delete-button" onClick={() => void deleteView()} disabled={saving}>Delete view</button>}</div>
                <label className="personal-view-field"><span>View name</span><input required maxLength={100} value={name} onChange={(event) => setName(event.target.value)} placeholder="For example, monthly unit review" /></label>
                <fieldset className="personal-panel-picker"><legend>Assigned panels <small>Choose and order panels for this view</small></legend>
                  {!panels.length ? <p className="personal-view-empty">No report panels are currently assigned.</p> : [...panels, ...assigned.filter((code) => !panels.some((choice) => choice.code === code)).map((code) => ({ code, label: panelName(code, panels) }))].map((panel) => (
                    <label key={panel.code} className="personal-panel-option"><input type="checkbox" checked={assigned.includes(panel.code)} onChange={() => togglePanel(panel.code)} /><span>{panel.label}{!panels.some((choice) => choice.code === panel.code) ? " — no longer assigned; remove before saving" : ""}</span></label>
                  ))}
                  {assigned.length > 0 && <ol className="personal-panel-order" aria-label="Panel order">{assigned.map((code, index) => <li key={code}><span>{String(index + 1).padStart(2, "0")}</span><strong>{panelName(code, panels)}</strong><div><button type="button" aria-label={`Move ${panelName(code, panels)} up`} onClick={() => movePanel(index, -1)} disabled={index === 0}>Move up</button><button type="button" aria-label={`Move ${panelName(code, panels)} down`} onClick={() => movePanel(index, 1)} disabled={index === assigned.length - 1}>Move down</button></div></li>)}</ol>}
                </fieldset>
                <div className="personal-filter-heading"><div><span className="personal-view-index">RETAINED FILTERS</span><h4>Report preference scope</h4></div><button type="button" className="personal-text-button" onClick={useCurrentFilters}>Use current filters</button></div>
                <p className="personal-filter-summary" aria-live="polite">{draftFilters ? summarizeFilters(draftFilters) : "No filter preferences loaded."}</p>
                <div className="personal-filter-fields">
                  <label className="personal-view-field"><span>Business unit ID</span><input type="number" min="1" value={draftFilters?.unit ?? ""} onChange={(event) => setScopeFilter("unit", event.target.value)} placeholder="Required if no company" /></label>
                  <label className="personal-view-field"><span>Company ID</span><input type="number" min="1" value={draftFilters?.company ?? ""} onChange={(event) => setScopeFilter("company", event.target.value)} placeholder="Required if no unit" /></label>
                  <label className="personal-view-field"><span>Location ID</span><input type="number" min="1" value={draftFilters?.location ?? ""} onChange={(event) => setFilter("location", event.target.value === "" ? "" : Number(event.target.value))} placeholder="Optional" /></label>
                  <label className="personal-view-field"><span>Period</span><select value={draftFilters?.period ?? ""} onChange={(event) => setFilter("period", event.target.value as ReportFilters["period"] | "")}><option value="">Unspecified</option><option value="daily">Daily</option><option value="monthly">Monthly</option><option value="quarterly">Quarterly</option></select></label>
                  <label className="personal-view-field"><span>From</span><input type="date" value={draftFilters?.start ?? ""} onChange={(event) => setFilter("start", event.target.value)} /></label>
                  <label className="personal-view-field"><span>Through</span><input type="date" value={draftFilters?.end ?? ""} onChange={(event) => setFilter("end", event.target.value)} /></label>
                  <label className="personal-view-field"><span>Page</span><input type="number" min="1" value={draftFilters?.page ?? ""} onChange={(event) => setFilter("page", event.target.value === "" ? "" : Number(event.target.value))} /></label>
                  <label className="personal-view-field"><span>Rows per page</span><input type="number" min="1" value={draftFilters?.page_size ?? ""} onChange={(event) => setFilter("page_size", event.target.value === "" ? "" : Number(event.target.value))} /></label>
                </div>
                <div className="personal-view-form-foot"><p>Only view name, panel order, and the listed filters are saved. Report rows are never stored here.</p><button className="button button-dark" type="submit" disabled={saving || !panels.length}>{saving ? "Saving preferences…" : selectedId ? "Update saved view" : "Save view"}</button></div>
                {saveError && <p className="personal-view-message is-error" role="alert">{saveError}</p>}
                {saveMessage && <p className="personal-view-message" role="status">{saveMessage}</p>}
              </form>
              {detail && <div className="personal-active-view">
                <div className="personal-active-heading"><div><span className="personal-view-index">LIVE AUTHORIZATION CHECK</span><h3>{detail.name}</h3><p>{summarizeFilters(activeFilters ?? detail.filters)}</p></div><button type="button" className="button button-outline" onClick={() => void openView(detail, true)} disabled={detailLoading}>Reload view</button></div>
                <p className="personal-no-push-note">Availability is checked on open, reload, focus, and when returning to this tab. Changes are not pushed in real time.</p>
                {activeView && activeFilters && <div className="personal-view-panels" key={`${activeView.id}-${refreshRevision}`}>
                  {activeView.panels.map((code) => (
                    <WorkspacePanel
                      key={code}
                      panel={code as ReportPanelCode}
                      filters={activeFilters}
                      csrfToken={csrfToken}
                      availability={availability.get(code)}
                      onExportFailure={() => {
                        setActiveView(null);
                        setActiveFilters(null);
                        void openView(detail, true);
                      }}
                    />
                  ))}
                </div>}
                {!activeView && !detailLoading && <button type="button" className="button button-dark" onClick={() => void openView(detail, true)}>Check access and load panels</button>}
                {activeView && <button type="button" className="button button-outline personal-refresh-data" onClick={() => {
                  setActiveView(null);
                  setActiveFilters(null);
                  void openView(detail, true);
                }}>Refresh authorization and reports</button>}
              </div>}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
