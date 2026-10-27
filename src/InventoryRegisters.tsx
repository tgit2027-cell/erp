import { useMemo, useState } from "react";
import { useReportCatalog, useReportData } from "./reporting-api";
import type { ReportPanelCode, ReportRow } from "./reporting-api";
import "./inventory-workspace.css";

const panelLabels: Record<"inventory" | "transfer_history", string> = {
  inventory: "Inventory",
  transfer_history: "Transfer history",
};
const safeFieldNames = new Set([
  "business_unit", "period", "item_code", "item_id", "item_description", "description", "sku", "variant_sku",
  "brand_name", "brand_code", "location", "location_code", "location_name", "warehouse_location",
  "unit_code", "unit_name", "quantity", "on_hand_quantity", "reference", "reference_number", "document_reference",
  "transfer_id", "transfer_reference", "created_at", "date", "transfer_date", "occurred_at",
  "source_location", "destination_location", "from_location", "to_location",
]);
const sensitiveName = /(cost|value|amount|price|margin|profit|reserved|supplier|landed|expense|currency|valuation)/i;

function isoDate(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
function defaultStart() {
  const now = new Date();
  return isoDate(new Date(now.getFullYear(), now.getMonth(), 1));
}
function title(field: string) {
  return field.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
function display(value: ReportRow[string]) {
  return value === null || value === undefined ? "—" : String(value);
}

export default function InventoryRegisters({
  selectedUnitId,
}: {
  selectedUnitId: number;
}) {
  const catalog = useReportCatalog(true);
  const [panel, setPanel] = useState<"inventory" | "transfer_history">("inventory");
  const [location, setLocation] = useState("");
  const [start, setStart] = useState(defaultStart);
  const [end, setEnd] = useState(() => isoDate(new Date()));
  const [page, setPage] = useState(1);
  const panelAssigned = catalog.data?.panels.some((choice) => choice.code === panel) ?? false;
  const capability = panel;
  const scope = catalog.data?.scopes?.[capability];
  const unitAssigned = scope?.business_units.some((unit) => unit.id === selectedUnitId) ?? false;
  const permittedLocations = useMemo(
    () => (scope?.locations ?? []).filter((item) => item.business_units.includes(selectedUnitId)),
    [scope, selectedUnitId],
  );
  const locationAllowed = !location || permittedLocations.some((item) => String(item.id) === location);
  const selectionUnavailable = Boolean(catalog.data && panelAssigned && unitAssigned && location && !locationAllowed);
  const validRange = Boolean(start && end && start <= end);
  const authorizedToRequest = Boolean(catalog.data && panelAssigned && unitAssigned && locationAllowed && validRange);
  const filters = useMemo(() => ({
    unit: selectedUnitId,
    ...(location ? { location } : {}),
    period: "daily" as const,
    start,
    end,
    page,
    page_size: 20,
  }), [selectedUnitId, location, start, end, page]);
  const report = useReportData(authorizedToRequest ? (panel as ReportPanelCode) : null, filters, authorizedToRequest);
  const reportData = report.data;
  const explicitUnitReport = reportData?.scope.scope_type === "business_unit"
    && reportData.scope.business_unit?.id === selectedUnitId
    && reportData.filters.unit_id === selectedUnitId;
  const shownFields = reportData?.fields.filter((field) => {
    const normalized = field.toLowerCase();
    return safeFieldNames.has(normalized) && !sensitiveName.test(normalized);
  }) ?? [];

  return (
    <section className="inventory-registers" aria-labelledby="inventory-registers-title">
      <div className="inventory-registers-heading">
          <div><span className="eyebrow">ASSIGNED REPORT SCOPE · READ ONLY</span><h2 id="inventory-registers-title">{panelLabels[panel]} register</h2><p>Only the selected assigned report is requested. Report permissions are separate from stock and capacity permissions.</p></div>
        <span className="inventory-readonly-mark">GET ONLY</span>
      </div>
      <div className="inventory-register-controls">
        <label>Register
          <select aria-label="Inventory report panel" value={panel} onChange={(event) => {
            setPanel(event.target.value as "inventory" | "transfer_history");
            setPage(1);
            setLocation("");
          }}>
            {(["inventory", "transfer_history"] as const).map((code) => <option key={code} value={code}>{panelLabels[code]}</option>)}
          </select>
        </label>
        <label>Permitted report location
          <select aria-label="Report location filter" aria-describedby={location && !locationAllowed ? "inventory-register-location-state" : undefined}
            value={location} onChange={(event) => { setLocation(event.target.value); setPage(1); }} disabled={!scope}>
            {location && !locationAllowed && <option value={location} disabled>
              {selectionUnavailable ? `Location ${location} — unavailable selection` : `Location ${location} — selection retained; checking scope`}
            </option>}
            <option value="">All permitted locations in this report scope</option>
            {permittedLocations.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
          </select>
        </label>
        <label>Calendar start date
          <input aria-label="Report calendar start date" type="date" value={start} max={end} onChange={(event) => { setStart(event.target.value); setPage(1); }} />
        </label>
        <label>Calendar end date
          <input aria-label="Report calendar end date" type="date" value={end} min={start} onChange={(event) => { setEnd(event.target.value); setPage(1); }} />
        </label>
      </div>
      <p className="inventory-register-scope">Business unit explicitly selected: <strong>{selectedUnitId}</strong> · dates are inclusive calendar dates · server page size 20</p>
      <p className="inventory-register-scope">Inventory on-hand is a current snapshot, not a historical balance reconstruction. The date range filters the transfer created-at calendar only.</p>
      <button type="button" className="button button-outline inventory-register-refresh" aria-label="Refresh current inventory register"
        disabled={catalog.loading || report.loading}
        onClick={() => { catalog.refresh(); report.refresh(); }}>Refresh current register</button>
      {catalog.loading && <div className="inventory-detail-skeleton" role="status" aria-label="Loading assigned report panels"><i /><i /><i /></div>}
      {catalog.error && <p className="inventory-register-error" role="alert">Report assignments are unavailable. No report data is displayed until scope is verified. {catalog.error}</p>}
      {!catalog.loading && !catalog.error && (!panelAssigned || !unitAssigned) && (
        <p className="inventory-register-notice" role="status">{!panelAssigned
          ? `${panelLabels[panel]} panel is not assigned to this account.`
          : `No ${panelLabels[panel].toLowerCase()} data scope is assigned for the selected business unit. Company-wide and other-unit fallback is not used.`}</p>
      )}
      {location && !locationAllowed && <div id="inventory-register-location-state" className="inventory-register-notice" role="status">
        {selectionUnavailable ? <>
          <p>The selected report location is unavailable in the current authorized scope. No results are shown. Select a permitted location or explicitly clear this filter to view the currently authorized locations. The report will not widen automatically.</p>
          <button type="button" className="button button-outline" aria-label="Clear unavailable location filter"
            onClick={() => { setLocation(""); setPage(1); }}>Clear unavailable location filter</button>
        </> : <p>The selected location filter is retained. Verify report assignments before choosing a location; no results are shown while scope is unavailable.</p>}
      </div>}
      {!validRange && <p className="inventory-register-notice" role="status">Choose a valid calendar date range.</p>}
      {report.loading && authorizedToRequest && <div className="inventory-detail-skeleton" role="status" aria-label="Loading inventory report"><i /><i /><i /></div>}
      {report.error && authorizedToRequest && <p className="inventory-register-notice" role="status">The server denied or could not return this assigned report scope. No report rows are displayed. {report.error}</p>}
      {reportData && !explicitUnitReport && <p className="inventory-register-notice" role="status">The returned report scope did not confirm the explicitly selected business unit. No rows are displayed.</p>}
      {reportData && explicitUnitReport && shownFields.length === 0 && <p className="inventory-register-notice" role="status">The report returned no approved identity, quantity, reference or date fields to display.</p>}
      {reportData && explicitUnitReport && shownFields.length > 0 && <>
        <div className="table-scroll">
          <table>
            <thead><tr>{shownFields.map((field) => <th key={field} scope="col">{title(field)}</th>)}</tr></thead>
            <tbody>{reportData.rows.map((row, index) => <tr key={`${String(row.reference ?? row.transfer_id ?? row.item_id ?? "row")}-${index}`}>
              {shownFields.map((field) => <td key={field}>{display(row[field])}</td>)}
            </tr>)}
            {reportData.rows.length === 0 && <tr><td className="empty-table" colSpan={shownFields.length}>No records in this explicitly scoped report page.</td></tr>}
            </tbody>
          </table>
        </div>
        <nav className="inventory-report-pagination" aria-label="Inventory report pagination">
          <span role="status">Page {reportData.pagination.page} of {reportData.pagination.total_pages} · {reportData.pagination.total_count} records</span>
          <button type="button" className="button button-outline" disabled={report.loading || reportData.pagination.previous_page === null}
            onClick={() => setPage(reportData.pagination.previous_page ?? page)} aria-label="Previous report page">Previous</button>
          <button type="button" className="button button-outline" disabled={report.loading || reportData.pagination.next_page === null}
            onClick={() => setPage(reportData.pagination.next_page ?? page)} aria-label="Next report page">Next</button>
        </nav>
      </>}
      <p className="inventory-transfer-limit">Transfer posting is unavailable here: the existing transfer service has no REST endpoint or approved warehouse write contract.</p>
    </section>
  );
}
