import { useEffect, useMemo, useRef, useState } from "react";
import {
  emptyColumnMapping,
  isNonNegativeDecimal,
  mapPackingRows,
  parsePackingListFile,
  sumDecimalStrings,
} from "./packing-list-parser";
import type {
  ColumnMapping,
  PackingField,
  PackingSheet,
} from "./packing-list-parser";
import PackingReceiptPreparation from "./PackingReceiptPreparation";
import type { PackingPreparationOptions } from "./packing-receipt-types";

type Props = {
  selectedUnitId: number;
  selectedUnitName: string;
  preparation?: PackingPreparationOptions;
};

type MatchState = {
  key: string;
  matchedCodes: Set<string>;
  loading: boolean;
  error: string;
};

const MAX_CODES_PER_REQUEST = 80;
const MATCH_BATCHES_AT_ONCE = 4;

const mappingFields: { key: PackingField; label: string; unitHint?: string }[] = [
  { key: "itemCode", label: "Supplier / item code", unitHint: "Matched to item code or variant SKU" },
  { key: "description", label: "Item description" },
  { key: "quantity", label: "Quantity" },
  { key: "unit", label: "Quantity unit" },
  { key: "lineReference", label: "Supplier line reference" },
  { key: "packageDimensions", label: "Package dimensions (source text)" },
  { key: "sourceVolumeM3", label: "Supplier cubic volume (m³)" },
  { key: "sourceAreaM2", label: "Supplier area (m²)" },
];

function codeKey(code: string): string {
  return code.trim().toLocaleUpperCase("en");
}

function chunks<T>(items: T[], size: number): T[][] {
  const result: T[][] = [];
  for (let index = 0; index < items.length; index += size) {
    result.push(items.slice(index, index + size));
  }
  return result;
}

function errorText(data: unknown): string {
  if (data && typeof data === "object" && "detail" in data && typeof data.detail === "string") {
    return data.detail;
  }
  return "Authorized item matching is unavailable for the selected business unit.";
}

function numericCellError(warehouseVolume: string): string {
  if (warehouseVolume.trim() && !isNonNegativeDecimal(warehouseVolume)) {
    return "Warehouse-confirmed volume must be a non-negative decimal in m³.";
  }
  return "";
}

export default function PackingListPreview({ selectedUnitId, selectedUnitName, preparation }: Props) {
  const [sheets, setSheets] = useState<PackingSheet[]>([]);
  const [sheetIndex, setSheetIndex] = useState(0);
  const [mapping, setMapping] = useState<ColumnMapping>(emptyColumnMapping);
  const [warehouseVolumes, setWarehouseVolumes] = useState<Record<number, string>>({});
  const [fileName, setFileName] = useState("");
  const [fileError, setFileError] = useState("");
  const [fileLoading, setFileLoading] = useState(false);
  const [matchState, setMatchState] = useState<MatchState>({
    key: "",
    matchedCodes: new Set(),
    loading: false,
    error: "",
  });
  const matchRequestId = useRef(0);
  const sheet = sheets[sheetIndex] ?? null;
  const rows = useMemo(
    () => sheet ? mapPackingRows(sheet, mapping) : [],
    [sheet, mapping],
  );
  const itemCodes = useMemo(
    () => [...new Set(rows.map((row) => row.itemCode).filter(Boolean))],
    [rows],
  );
  const matchKey = `${selectedUnitId}:${JSON.stringify(itemCodes.map(codeKey).sort())}`;
  const preparationKey = JSON.stringify({
    unit: selectedUnitId,
    context: preparation?.contextKey ?? "",
    enabled: preparation?.enabled ?? false,
    reason: preparation?.reason ?? "",
    locations: preparation?.locations ?? [],
    commitments: preparation?.commitments ?? null,
    fileName,
    sheet: sheet?.name ?? "",
    mapping,
    rows,
    warehouseVolumes,
  });

  useEffect(() => {
    const requestId = ++matchRequestId.current;
    const uniqueCodes = [...new Set(itemCodes)];
    setMatchState({
      key: matchKey,
      matchedCodes: new Set(),
      loading: uniqueCodes.length > 0,
      error: "",
    });
    if (!uniqueCodes.length) return;

    const fetchMatches = async () => {
      const found = new Set<string>();
      try {
        const batches = chunks(uniqueCodes, MAX_CODES_PER_REQUEST);
        for (let index = 0; index < batches.length; index += MATCH_BATCHES_AT_ONCE) {
          const currentBatches = batches.slice(index, index + MATCH_BATCHES_AT_ONCE);
          const responses = await Promise.all(currentBatches.map(async (batch) => {
            const params = new URLSearchParams({ unit: String(selectedUnitId) });
            batch.forEach((code) => params.append("code", code));
            const response = await fetch(`/api/stock-catalog-matches/?${params.toString()}`, {
              credentials: "same-origin",
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(errorText(data));
            return Array.isArray(data.matched_codes) ? data.matched_codes as string[] : [];
          }));
          if (matchRequestId.current !== requestId) return;
          responses.flat().forEach((code) => found.add(codeKey(code)));
        }
        if (matchRequestId.current === requestId) {
          setMatchState({ key: matchKey, matchedCodes: found, loading: false, error: "" });
        }
      } catch (reason) {
        if (matchRequestId.current === requestId) {
          setMatchState({
            key: matchKey,
            matchedCodes: new Set(),
            loading: false,
            error: reason instanceof Error
              ? reason.message
              : "Authorized item matching is unavailable for the selected business unit.",
          });
        }
      }
    };
    void fetchMatches();
    return () => {
      if (matchRequestId.current === requestId) matchRequestId.current += 1;
    };
  }, [itemCodes, matchKey, selectedUnitId]);

  useEffect(() => () => {
    matchRequestId.current += 1;
  }, []);

  async function loadFile(file?: File) {
    if (!file) return;
    preparation?.onSourceChange?.();
    setFileLoading(true);
    setFileError("");
    setSheets([]);
    setSheetIndex(0);
    setMapping(emptyColumnMapping);
    setWarehouseVolumes({});
    setFileName(file.name);
    setMatchState({ key: "", matchedCodes: new Set(), loading: false, error: "" });
    try {
      const workbook = await parsePackingListFile(file);
      setSheets(workbook.sheets);
    } catch (reason) {
      setFileError(reason instanceof Error ? reason.message : "The selected file could not be previewed.");
      setFileName("");
    } finally {
      setFileLoading(false);
    }
  }

  function changeMapping(field: PackingField, value: string) {
    preparation?.onSourceChange?.();
    const selected = value === "" ? null : Number(value);
    setMapping((current) => ({ ...current, [field]: selected }));
  }

  const matchIsCurrent = matchState.key === matchKey;
  const validRows = rows.filter((row) => {
    const volumeError = numericCellError(warehouseVolumes[row.sourceRow] ?? "");
    return !row.errors.length && !volumeError;
  });
  const invalidCount = rows.length - validRows.length;
  const matchedCount = matchIsCurrent && !matchState.loading && !matchState.error
    ? validRows.filter((row) => matchState.matchedCodes.has(codeKey(row.itemCode))).length
    : 0;
  const unmatchedCount = matchIsCurrent && !matchState.loading && !matchState.error
    ? validRows.length - matchedCount
    : 0;

  const supplierVolumeValues = rows
    .filter((row) => row.sourceVolumeM3 && isNonNegativeDecimal(row.sourceVolumeM3))
    .map((row) => row.sourceVolumeM3);
  const supplierAreaValues = rows
    .filter((row) => row.sourceAreaM2 && isNonNegativeDecimal(row.sourceAreaM2))
    .map((row) => row.sourceAreaM2);
  const warehouseVolumeValues = Object.values(warehouseVolumes)
    .filter((value) => value.trim() && isNonNegativeDecimal(value));

  return (
    <section className="content-card packing-preview" aria-labelledby="packing-preview-title">
      <div className="packing-preview-heading">
        <div>
          <span className="eyebrow">SYNTHETIC DEMO · LOCAL PREVIEW ONLY</span>
          <h3 id="packing-preview-title">Supplier packing-list preview</h3>
          <p>Choose a CSV or .xlsx file, then map its columns. No supplier template is assumed.</p>
        </div>
        <span className="packing-preview-badge">NO STOCK WRITES</span>
      </div>

      <p className="packing-preview-privacy">
        Use synthetic examples only; do not select real supplier documents. The file is parsed in this browser
        and is never uploaded or saved. Only mapped item codes are sent
        to the read-only, selected-unit match check. This preview cannot create a receipt or commitment,
        change stock, or save warehouse measurements.
      </p>

      <div className="packing-file-row">
        <label className="packing-file-label">
          <span>Choose packing-list file</span>
          <input
            aria-label="Choose supplier packing list"
            type="file"
            accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={(event) => void loadFile(event.currentTarget.files?.[0])}
            disabled={fileLoading}
          />
        </label>
        {fileLoading && <span role="status">Reading file locally…</span>}
        {fileName && !fileError && <span className="packing-file-name">{fileName}</span>}
      </div>
      {fileError && <div className="packing-feedback packing-feedback-error" role="alert">{fileError}</div>}

      {sheets.length > 0 && (
        <>
          {sheets.length > 1 && (
            <label className="packing-sheet-select">
              <span>Worksheet</span>
              <select
                aria-label="Packing list worksheet"
                value={sheetIndex}
                onChange={(event) => {
                  preparation?.onSourceChange?.();
                  setSheetIndex(Number(event.currentTarget.value));
                  setMapping(emptyColumnMapping);
                  setWarehouseVolumes({});
                }}
              >
                {sheets.map((entry, index) => <option key={`${entry.name}-${index}`} value={index}>{entry.name}</option>)}
              </select>
            </label>
          )}

          {sheet && sheet.headers.length > 0 ? (
            <>
              <fieldset className="packing-mapping">
                <legend>Map source columns</legend>
                <p>Required for a valid row: supplier/item code, quantity, and quantity unit. Measurements are never converted.</p>
                <div className="packing-mapping-grid">
                  {mappingFields.map(({ key, label, unitHint }) => (
                    <label key={key}>
                      <span>{label}{unitHint && <small>{unitHint}</small>}</span>
                      <select
                        aria-label={`Map ${label}`}
                        value={mapping[key] === null ? "" : String(mapping[key])}
                        onChange={(event) => changeMapping(key, event.currentTarget.value)}
                      >
                        <option value="">Not mapped</option>
                        {sheet.headers.map((header, index) => (
                          <option key={`${index}-${header}`} value={index}>{header} · column {index + 1}</option>
                        ))}
                      </select>
                    </label>
                  ))}
                </div>
              </fieldset>

              <div className="packing-summary" aria-label="Packing list preview summary">
                <div><strong>{rows.length}</strong><span>Source rows</span></div>
                <div><strong>{validRows.length}</strong><span>Valid rows</span></div>
                <div><strong>{invalidCount}</strong><span>Invalid rows</span></div>
                <div><strong>{matchedCount}</strong><span>Authorized matches</span></div>
                <div><strong>{unmatchedCount}</strong><span>Unmatched codes</span></div>
              </div>

              {matchIsCurrent && matchState.error && (
                <div className="packing-feedback packing-feedback-error" role="alert">
                  {matchState.error} The file rows remain local; no catalog data is shown.
                </div>
              )}
              {matchIsCurrent && matchState.loading && (
                <div className="packing-feedback" role="status">
                  Checking codes against {selectedUnitName}&apos;s authorized current inventory…
                </div>
              )}
              <div className="packing-volume-summary" aria-label="Separate area and volume summaries">
                <div><span>Supplier cubic volume</span><strong>{supplierVolumeValues.length ? `${sumDecimalStrings(supplierVolumeValues)} m³` : "Not provided"}</strong></div>
                <div><span>Warehouse-confirmed volume</span><strong>{warehouseVolumeValues.length ? `${sumDecimalStrings(warehouseVolumeValues)} m³` : "Not entered"}</strong></div>
                <div><span>Supplier area</span><strong>{supplierAreaValues.length ? `${sumDecimalStrings(supplierAreaValues)} m²` : "Not provided"}</strong></div>
              </div>
              <p className="packing-unit-note">
                Supplier volume and area are separate source values. Package dimensions are shown as entered;
                no dimensions, area, or volume are converted or derived from one another.
              </p>

              <div className="table-scroll packing-table-scroll">
                <table className="packing-table">
                  <thead>
                    <tr>
                      <th scope="col">Source row</th>
                      <th scope="col">Supplier / item code</th>
                      <th scope="col">Description</th>
                      <th scope="col">Quantity</th>
                      <th scope="col">Unit</th>
                      <th scope="col">Line reference</th>
                      <th scope="col">Package dimensions</th>
                      <th scope="col">Supplier volume (m³)</th>
                      <th scope="col">Supplier area (m²)</th>
                      <th scope="col">Warehouse-confirmed volume (m³)</th>
                      <th scope="col">Preview status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const warehouseVolume = warehouseVolumes[row.sourceRow] ?? "";
                      const warehouseError = numericCellError(warehouseVolume);
                      const errors = warehouseError ? [...row.errors, warehouseError] : row.errors;
                      const codeIsMatched = matchIsCurrent
                        && matchState.matchedCodes.has(codeKey(row.itemCode));
                      let status = "Waiting for selected-unit code check";
                      if (errors.length) status = "Invalid";
                      else if (matchIsCurrent && matchState.error) status = "Match unavailable";
                      else if (matchIsCurrent && matchState.loading) status = "Checking code";
                      else if (matchIsCurrent && codeIsMatched) status = "Authorized inventory match";
                      else if (matchIsCurrent) status = "No authorized inventory match";
                      return (
                        <tr key={row.sourceRow}>
                          <td>{row.sourceRow}</td>
                          <td>{row.itemCode || "—"}</td>
                          <td>{row.description || "—"}</td>
                          <td>{row.quantity || "—"}</td>
                          <td>{row.unit || "—"}</td>
                          <td>{row.lineReference || "—"}</td>
                          <td>{row.packageDimensions || "Not provided"}</td>
                          <td>{row.sourceVolumeM3 || "—"}</td>
                          <td>{row.sourceAreaM2 || "—"}</td>
                          <td>
                            <label className="sr-only" htmlFor={`warehouse-volume-${row.sourceRow}`}>
                              Warehouse-confirmed volume in m³ for source row {row.sourceRow}
                            </label>
                            <input
                              id={`warehouse-volume-${row.sourceRow}`}
                              aria-label={`Warehouse-confirmed volume in m³ for source row ${row.sourceRow}`}
                              inputMode="decimal"
                              value={warehouseVolume}
                              onChange={(event) => {
                                preparation?.onSourceChange?.();
                                const value = event.currentTarget.value;
                                setWarehouseVolumes((current) => ({
                                  ...current,
                                  [row.sourceRow]: value,
                                }));
                              }}
                              aria-invalid={Boolean(warehouseError)}
                              aria-describedby={warehouseError ? `warehouse-volume-error-${row.sourceRow}` : undefined}
                            />
                            {warehouseError && <small id={`warehouse-volume-error-${row.sourceRow}`} className="packing-row-error">{warehouseError}</small>}
                          </td>
                          <td>
                            <span className={`packing-row-status ${errors.length ? "is-invalid" : codeIsMatched ? "is-matched" : ""}`}>
                              {status}
                            </span>
                            {errors.map((error) => <small className="packing-row-error" key={error}>{error}</small>)}
                          </td>
                        </tr>
                      );
                    })}
                    {!rows.length && (
                      <tr><td colSpan={11} className="empty-table">No data rows in this worksheet.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="packing-feedback" role="status">
              This worksheet has no header row or data columns to map.
            </div>
          )}
        </>
      )}
      {preparation && sheet && (
        <PackingReceiptPreparation
          key={preparationKey}
          selectedUnitId={selectedUnitId}
          rows={rows}
          warehouseVolumes={warehouseVolumes}
          fileName={fileName}
          sourceGeneration={preparationKey}
          preparation={preparation}
        />
      )}
    </section>
  );
}
