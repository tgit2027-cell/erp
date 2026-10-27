import { useEffect, useMemo, useState } from "react";
import { isNonNegativeDecimal, isPositiveDecimal } from "./packing-list-parser";
import type { PackingPreviewRow } from "./packing-list-parser";
import type {
  PackingCommitmentLine,
  PackingPreparationOptions,
  PreparedPackingLine,
} from "./packing-receipt-types";
import "./packing-receipt-preparation.css";

type Candidate = {
  variant_id: number;
  variant_sku: string;
  variant_description: string;
  item_code: string;
  item_description: string;
  brand_name: string;
};

type RowChoice = {
  lookupCode: string;
  variantId: number | null;
  commitmentLineId: number | null;
  locationId: string;
  quantity: string;
};

type LookupState = {
  key: string;
  candidates: Record<string, Candidate[]>;
  loading: boolean;
  error: string;
};

type Props = {
  selectedUnitId: number;
  rows: PackingPreviewRow[];
  warehouseVolumes: Record<number, string>;
  fileName: string;
  sourceGeneration: string;
  preparation: PackingPreparationOptions;
};

type ReceivingMode = "direct" | "commitment";

const MAX_LINES = 80;

function normalized(value: string): string {
  return value.trim().toLocaleUpperCase("en");
}

function validReceiptQuantity(value: string): boolean {
  return /^\d+(?:\.\d{1,2})?$/.test(value.trim()) && isPositiveDecimal(value);
}

function decimalParts(value: string): { integer: bigint; scale: number } | null {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!match) return null;
  const fraction = match[2] ?? "";
  return {
    integer: BigInt(`${match[1]}${fraction}`),
    scale: fraction.length,
  };
}

function decimalGreaterThan(left: string, right: string): boolean | null {
  const leftParts = decimalParts(left);
  const rightParts = decimalParts(right);
  if (!leftParts || !rightParts) return null;
  const scale = Math.max(leftParts.scale, rightParts.scale);
  const leftValue = leftParts.integer * 10n ** BigInt(scale - leftParts.scale);
  const rightValue = rightParts.integer * 10n ** BigInt(scale - rightParts.scale);
  return leftValue > rightValue;
}

function errorMessage(data: unknown): string {
  if (data && typeof data === "object" && "detail" in data && typeof data.detail === "string") {
    return data.detail;
  }
  return "Authorized receiving-item lookup is unavailable for this business unit.";
}

function isCandidate(value: unknown): value is Candidate {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return typeof item.variant_id === "number"
    && typeof item.variant_sku === "string"
    && typeof item.variant_description === "string"
    && typeof item.item_code === "string"
    && typeof item.item_description === "string"
    && typeof item.brand_name === "string";
}

function isUsableCommitmentLine(line: PackingCommitmentLine): boolean {
  return Number.isSafeInteger(line.commitment_id) && line.commitment_id > 0
    && Number.isSafeInteger(line.commitment_line_id) && line.commitment_line_id > 0
    && Number.isSafeInteger(line.supplier_id) && line.supplier_id > 0
    && Number.isSafeInteger(line.variant_id) && line.variant_id > 0
    && typeof line.variant_sku === "string" && Boolean(line.variant_sku.trim())
    && typeof line.item_description === "string"
    && isNonNegativeDecimal(line.ordered_quantity)
    && isNonNegativeDecimal(line.received_to_date)
    && isNonNegativeDecimal(line.remaining_quantity);
}

export default function PackingReceiptPreparation({
  selectedUnitId,
  rows,
  warehouseVolumes,
  fileName,
  sourceGeneration,
  preparation,
}: Props) {
  const [mode, setMode] = useState<ReceivingMode>("direct");
  const [commitmentId, setCommitmentId] = useState<number | null>(null);
  const [choices, setChoices] = useState<Record<number, RowChoice>>(() => Object.fromEntries(
    rows.map((row) => [row.sourceRow, {
      lookupCode: row.itemCode,
      variantId: null,
      commitmentLineId: null,
      locationId: "",
      quantity: "",
    }]),
  ));
  const [acknowledged, setAcknowledged] = useState(false);
  const [lookup, setLookup] = useState<LookupState>({
    key: "",
    candidates: {},
    loading: false,
    error: "",
  });

  const lookupCodes = useMemo(
    () => [...new Set(rows.map((row) => choices[row.sourceRow]?.lookupCode.trim() ?? "").filter(Boolean))],
    [choices, rows],
  );
  const lookupKey = `${selectedUnitId}:${preparation.contextKey}:${sourceGeneration}:${mode}:${JSON.stringify(lookupCodes)}`;

  useEffect(() => {
    const controller = new AbortController();
    if (mode !== "direct") {
      setLookup({ key: lookupKey, candidates: {}, loading: false, error: "" });
      return () => controller.abort();
    }
    setLookup({ key: lookupKey, candidates: {}, loading: lookupCodes.length > 0, error: "" });
    if (!preparation.enabled || !lookupCodes.length || rows.length > MAX_LINES) {
      setLookup({ key: lookupKey, candidates: {}, loading: false, error: "" });
      return () => controller.abort();
    }
    let active = true;
    const params = new URLSearchParams({ unit: String(selectedUnitId) });
    lookupCodes.slice(0, MAX_LINES).forEach((code) => params.append("code", code));
    void fetch(`/api/stock-receipt-candidates/?${params.toString()}`, {
      credentials: "same-origin",
      signal: controller.signal,
    }).then(async (response) => {
      const data: unknown = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorMessage(data));
      const candidates = data && typeof data === "object" && "candidates" in data
        && Array.isArray((data as { candidates?: unknown }).candidates)
        ? (data as { candidates: unknown[] }).candidates.filter(isCandidate)
        : [];
      const byCode: Record<string, Candidate[]> = {};
      lookupCodes.forEach((code) => {
        const codeKey = normalized(code);
        byCode[codeKey] = candidates.filter((candidate) =>
          normalized(candidate.item_code) === codeKey || normalized(candidate.variant_sku) === codeKey,
        );
      });
      if (active) setLookup({ key: lookupKey, candidates: byCode, loading: false, error: "" });
    }).catch((reason: unknown) => {
      if (!active || controller.signal.aborted) return;
      setLookup({
        key: lookupKey,
        candidates: {},
        loading: false,
        error: reason instanceof Error ? reason.message : "Authorized item lookup failed.",
      });
    });
    return () => {
      active = false;
      controller.abort();
    };
  }, [lookupKey, lookupCodes, mode, preparation.enabled, rows.length, selectedUnitId]);

  const progress = preparation.commitments;
  const progressLines = progress?.lines ?? [];
  const commitmentIds = [...new Set(progressLines
    .filter((line) => Number.isSafeInteger(line.commitment_id) && line.commitment_id > 0)
    .map((line) => line.commitment_id))].sort((a, b) => a - b);
  const selectedCommitmentLines = commitmentId === null
    ? []
    : progressLines.filter((line) => line.commitment_id === commitmentId);
  const selectedSupplierIds = [...new Set(selectedCommitmentLines.map((line) => line.supplier_id))];
  const commitmentSelectionError = mode === "commitment"
    ? progress?.state !== "ready"
      ? progress?.state === "loading"
        ? "Authorized purchase-commitment progress is loading."
        : progress?.state === "error"
          ? progress.error || "Authorized purchase-commitment progress is unavailable."
          : "Authorized purchase-commitment progress was not supplied."
      : !commitmentId
        ? "Choose an existing authorized purchase commitment."
        : !selectedCommitmentLines.length
          ? "The selected commitment is stale or has no supplied progress lines."
          : selectedSupplierIds.length !== 1
            ? "The selected commitment has inconsistent supplier metadata."
            : ""
    : "";

  const lookupIsCurrent = lookup.key === lookupKey;
  const rowErrors = rows.map((row) => {
    const choice = choices[row.sourceRow];
    const code = choice?.lookupCode.trim() ?? "";
    const candidates = mode === "direct" && lookupIsCurrent ? lookup.candidates[normalized(code)] ?? [] : [];
    const selectedCandidate = candidates.find((candidate) => candidate.variant_id === choice?.variantId);
    const selectedCommitmentLine = mode === "commitment"
      ? selectedCommitmentLines.find((line) => line.commitment_line_id === choice?.commitmentLineId)
      : undefined;
    const volume = warehouseVolumes[row.sourceRow] ?? "";
    const errors: string[] = [];
    if (row.errors.length) errors.push(...row.errors);
    if (mode === "direct") {
      if (!code) errors.push("Enter an exact catalog code or variant SKU.");
      if (!lookupIsCurrent || lookup.loading) errors.push("Authorized item lookup is pending.");
      else if (!lookup.error && !candidates.length) errors.push("No authorized variant found; enter an exact catalog code or SKU.");
      else if (candidates.length > 1 && !selectedCandidate) errors.push("Choose one variant; this code is ambiguous.");
      else if (candidates.length === 1 && !selectedCandidate) errors.push("Choose the authorized variant explicitly.");
      if (!selectedCandidate) errors.push("Select an authorized variant.");
    } else {
      if (!commitmentSelectionError && !choice?.commitmentLineId) errors.push("Choose a commitment line explicitly.");
      if (choice?.commitmentLineId && !selectedCommitmentLine) {
        errors.push("The selected commitment line is stale or unavailable in supplied progress.");
      }
      if (selectedCommitmentLine && !isUsableCommitmentLine(selectedCommitmentLine)) {
        errors.push("The selected commitment line has invalid or incomplete progress metadata.");
      }
      if (selectedCommitmentLine && selectedCommitmentLines.filter(
        (line) => line.commitment_line_id === selectedCommitmentLine.commitment_line_id,
      ).length !== 1) {
        errors.push("The supplied progress contains an ambiguous commitment line identifier.");
      }
      if (selectedCommitmentLine && selectedCommitmentLine.commitment_id !== commitmentId) {
        errors.push("Choose a line from the explicitly selected commitment.");
      }
      if (selectedCommitmentLine && decimalGreaterThan(
        choice?.quantity ?? "",
        selectedCommitmentLine.remaining_quantity,
      ) === true) {
        errors.push("Confirmed quantity cannot exceed the selected commitment line's remaining quantity.");
      }
    }
    if (commitmentSelectionError && mode === "commitment") errors.push(commitmentSelectionError);
    if (!preparation.locations.some((location) => String(location.id) === choice?.locationId)) {
      errors.push("Choose an existing receiving location.");
    }
    if (!validReceiptQuantity(choice?.quantity ?? "")) {
      errors.push("Enter a positive confirmed quantity with no more than 2 decimal places.");
    }
    if (volume.trim() && !isNonNegativeDecimal(volume)) {
      errors.push("Warehouse-confirmed volume must be a non-negative decimal in m³.");
    }
    return { row, choice, candidates, selectedCandidate, selectedCommitmentLine, errors };
  });

  const selectedCommitmentLineIds = rowErrors
    .filter(({ choice }) => choice?.commitmentLineId !== null && choice?.commitmentLineId !== undefined)
    .map(({ choice }) => choice!.commitmentLineId!);
  const duplicateCommitmentLineIds = mode === "commitment"
    ? new Set(selectedCommitmentLineIds.filter((id, index) => selectedCommitmentLineIds.indexOf(id) !== index))
    : new Set<number>();
  if (duplicateCommitmentLineIds.size) {
    rowErrors.forEach(({ choice, errors }) => {
      if (choice?.commitmentLineId !== null && choice?.commitmentLineId !== undefined
        && duplicateCommitmentLineIds.has(choice.commitmentLineId)) {
        errors.push("This commitment line is already mapped to another source row. Consolidate rows manually.");
      }
    });
  }

  const overLineLimit = rows.length > MAX_LINES;
  const hasBlockingRows = overLineLimit || rowErrors.some(({ errors }) => errors.length > 0);
  const canReview = preparation.enabled
    && Boolean(fileName)
    && rows.length > 0
    && !hasBlockingRows
    && acknowledged
    && (mode === "commitment" || (!lookup.loading && !lookup.error));

  function invalidateDraft() {
    preparation.onSourceChange?.();
    setAcknowledged(false);
  }

  function updateChoice(sourceRow: number, patch: Partial<RowChoice>) {
    invalidateDraft();
    setChoices((current) => ({
      ...current,
      [sourceRow]: { ...current[sourceRow], ...patch },
    }));
  }

  function changeMode(nextMode: ReceivingMode) {
    if (nextMode === mode) return;
    invalidateDraft();
    setMode(nextMode);
    setCommitmentId(null);
    setChoices((current) => Object.fromEntries(Object.entries(current).map(([sourceRow, choice]) => [
      sourceRow,
      { ...choice, commitmentLineId: null, variantId: null },
    ])));
  }

  function changeCommitment(value: string) {
    invalidateDraft();
    setCommitmentId(value ? Number(value) : null);
    setChoices((current) => Object.fromEntries(Object.entries(current).map(([sourceRow, choice]) => [
      sourceRow,
      { ...choice, commitmentLineId: null },
    ])));
  }

  function useReviewedLines() {
    if (!canReview) return;
    const lines: PreparedPackingLine[] = rowErrors.map(({
      row, choice, selectedCandidate, selectedCommitmentLine,
    }) => {
      const location = preparation.locations.find((entry) => String(entry.id) === choice!.locationId)!;
      if (mode === "commitment") {
        const line = selectedCommitmentLine!;
        return {
          commitmentLineId: line.commitment_line_id,
          sourceRow: row.sourceRow,
          sourceCode: row.itemCode,
          sourceDescription: row.description,
          sourceQuantity: row.quantity,
          sourceUnit: row.unit,
          lineReference: row.lineReference,
          warehouseVolumeM3: warehouseVolumes[row.sourceRow] ?? "",
          variantId: line.variant_id,
          variantSku: line.variant_sku,
          itemCode: line.variant_sku,
          itemDescription: line.item_description,
          locationId: location.id,
          locationCode: location.code,
          quantity: choice!.quantity.trim(),
        };
      }
      return {
        sourceRow: row.sourceRow,
        sourceCode: row.itemCode,
        sourceDescription: row.description,
        sourceQuantity: row.quantity,
        sourceUnit: row.unit,
        lineReference: row.lineReference,
        warehouseVolumeM3: warehouseVolumes[row.sourceRow] ?? "",
        variantId: selectedCandidate!.variant_id,
        variantSku: selectedCandidate!.variant_sku,
        itemCode: selectedCandidate!.item_code,
        itemDescription: selectedCandidate!.item_description,
        locationId: location.id,
        locationCode: location.code,
        quantity: choice!.quantity.trim(),
      };
    });
    preparation.onReviewed({
      ...(mode === "commitment" ? {
        commitmentId: commitmentId!,
        supplierId: selectedSupplierIds[0],
      } : {}),
      unitId: selectedUnitId,
      fileName,
      lines,
    });
  }

  const progressStatus = mode === "commitment" && progress?.state === "loading"
    ? "Loading authorized purchase-commitment progress…"
    : mode === "commitment" && progress?.state === "error"
      ? progress.error || "Authorized purchase-commitment progress is unavailable."
      : mode === "commitment" && progress?.state === "ready" && !progressLines.length
        ? "No authorized commitment progress lines are available."
        : "";

  return (
    <section className="packing-preparation" aria-labelledby="packing-preparation-title">
      <div className="packing-preparation-heading">
        <div>
          <span className="eyebrow">SYNTHETIC DEMO · LOCAL PREPARATION</span>
          <h4 id="packing-preparation-title">Prepare receiving lines</h4>
          <p>Match each source row to an authorized variant, location, and warehouse-confirmed quantity.</p>
        </div>
        <span className="packing-preparation-badge">DOES NOT POST</span>
      </div>

      <p className="packing-preparation-note">
        This review is local only and does not create a receipt. The existing receipt form still requires supplier,
        costs, and an explicit submission. Source quantities, units, references, and the separate m² / m³ preview
        remain unchanged; no unit conversion or base unit is inferred.
      </p>

      {!preparation.enabled && (
        <div className="packing-preparation-denied" role="status">
          Preparation handoff is unavailable. {preparation.reason || "Authorization was not provided."}
        </div>
      )}
      <div className="packing-preparation-mode">
        <label htmlFor="prep-receiving-mode">Receiving mode</label>
        <select
          id="prep-receiving-mode"
          aria-label="Receiving mode"
          value={mode}
          onChange={(event) => changeMode(event.currentTarget.value as ReceivingMode)}
          disabled={!preparation.enabled}
        >
          <option value="direct">Direct receiving</option>
          <option value="commitment">Existing purchase commitment</option>
        </select>
      </div>

      {mode === "commitment" && (
        <div className="packing-preparation-commitment">
          <label htmlFor="prep-commitment">Existing authorized purchase commitment</label>
          <select
            id="prep-commitment"
            aria-label="Existing authorized purchase commitment"
            value={commitmentId === null ? "" : String(commitmentId)}
            onChange={(event) => changeCommitment(event.currentTarget.value)}
            disabled={!preparation.enabled || progress?.state !== "ready"}
          >
            <option value="">Choose commitment explicitly</option>
            {commitmentIds.map((id) => {
              const suppliers = [...new Set(progressLines
                .filter((line) => line.commitment_id === id)
                .map((line) => line.supplier_id))];
              return (
                <option key={id} value={id}>
                  Commitment {id}{suppliers.length === 1 ? ` · Supplier ${suppliers[0]}` : " · Inconsistent supplier data"}
                </option>
              );
            })}
          </select>
          {progressStatus && (
            <div className={`packing-feedback ${progress?.state === "error" ? "packing-feedback-error" : ""}`} role={progress?.state === "error" ? "alert" : "status"}>
              {progressStatus}
            </div>
          )}
          {progress?.state === "ready" && commitmentId !== null && (
            <p className="packing-preparation-note">
              Select one supplied commitment line for each source row. Ordered, received, and remaining quantities
              are shown from authorized progress; line matching is never inferred.
            </p>
          )}
        </div>
      )}

      {overLineLimit && (
        <div className="packing-feedback packing-feedback-error" role="alert">
          Receipt preparation supports at most {MAX_LINES} source rows. No rows will be omitted.
        </div>
      )}
      {mode === "direct" && lookupIsCurrent && lookup.loading && (
        <div className="packing-feedback" role="status">Checking selected-unit authorized variants…</div>
      )}
      {mode === "direct" && lookupIsCurrent && lookup.error && (
        <div className="packing-feedback packing-feedback-error" role="alert">{lookup.error}</div>
      )}

      <div className="table-scroll packing-preparation-scroll">
        <table className="packing-table packing-preparation-table">
          <thead>
            <tr>
              <th scope="col">Source row</th>
              <th scope="col">Source item / quantity</th>
              {mode === "direct" ? (
                <>
                  <th scope="col">Exact catalog code or SKU</th>
                  <th scope="col">Authorized variant choice</th>
                </>
              ) : (
                <th scope="col">Explicit commitment line</th>
              )}
              <th scope="col">Receiving location</th>
              <th scope="col">Confirmed quantity</th>
              <th scope="col">Preparation status</th>
            </tr>
          </thead>
          <tbody>
            {rowErrors.map(({ row, choice, candidates, selectedCandidate, selectedCommitmentLine, errors }) => {
              const code = choice?.lookupCode ?? "";
              const busy = mode === "direct" && lookupIsCurrent && lookup.loading;
              const locationLabel = (location: PackingPreparationOptions["locations"][number]) =>
                `${location.code} · ${location.name}`;
              let status = "Needs review";
              if (!preparation.enabled) status = "Handoff unavailable";
              else if (busy) status = "Checking authorized variants";
              else if (mode === "direct" && lookupIsCurrent && lookup.error) status = "Lookup denied or unavailable";
              else if (mode === "direct" && candidates.length > 1 && !selectedCandidate) status = "Ambiguous";
              else if (errors.length) status = "Blocked";
              else status = "Ready for review";
              return (
                <tr key={row.sourceRow}>
                  <td>{row.sourceRow}</td>
                  <td>
                    <strong>{row.itemCode || "No source code"}</strong>
                    <small>{row.description || "No source description"}</small>
                    <small>Source: {row.quantity || "—"} {row.unit || "—"}</small>
                    {row.lineReference && <small>Reference: {row.lineReference}</small>}
                  </td>
                  {mode === "direct" ? (
                    <>
                      <td>
                        <label className="sr-only" htmlFor={`prep-code-${row.sourceRow}`}>
                          Exact catalog code or variant SKU for source row {row.sourceRow}
                        </label>
                        <input
                          id={`prep-code-${row.sourceRow}`}
                          value={code}
                          onChange={(event) => updateChoice(row.sourceRow, {
                            lookupCode: event.currentTarget.value,
                            variantId: null,
                          })}
                          aria-label={`Exact catalog code or variant SKU for source row ${row.sourceRow}`}
                          autoComplete="off"
                        />
                      </td>
                      <td>
                        <label className="sr-only" htmlFor={`prep-variant-${row.sourceRow}`}>
                          Select authorized variant for source row {row.sourceRow}
                        </label>
                        <select
                          id={`prep-variant-${row.sourceRow}`}
                          value={selectedCandidate ? String(selectedCandidate.variant_id) : ""}
                          onChange={(event) => updateChoice(row.sourceRow, {
                            variantId: event.currentTarget.value ? Number(event.currentTarget.value) : null,
                          })}
                          disabled={!preparation.enabled || busy || Boolean(lookup.error) || !candidates.length}
                          aria-label={`Select authorized variant for source row ${row.sourceRow}`}
                        >
                          <option value="">Choose variant explicitly</option>
                          {candidates.map((candidate) => (
                            <option key={candidate.variant_id} value={candidate.variant_id}>
                              {candidate.variant_sku} · {candidate.variant_description}
                              {candidate.brand_name ? ` · ${candidate.brand_name}` : ""}
                            </option>
                          ))}
                        </select>
                      </td>
                    </>
                  ) : (
                    <td>
                      <label className="sr-only" htmlFor={`prep-commitment-line-${row.sourceRow}`}>
                        Select commitment line for source row {row.sourceRow}
                      </label>
                      <select
                        id={`prep-commitment-line-${row.sourceRow}`}
                        value={choice?.commitmentLineId === null || choice?.commitmentLineId === undefined
                          ? ""
                          : String(choice.commitmentLineId)}
                        onChange={(event) => updateChoice(row.sourceRow, {
                          commitmentLineId: event.currentTarget.value ? Number(event.currentTarget.value) : null,
                        })}
                        disabled={!preparation.enabled || progress?.state !== "ready" || commitmentId === null}
                        aria-label={`Select commitment line for source row ${row.sourceRow}`}
                      >
                        <option value="">Choose commitment line explicitly</option>
                        {selectedCommitmentLines.map((line, index) => (
                          <option
                            key={`${line.commitment_line_id}-${index}`}
                            value={line.commitment_line_id}
                            disabled={!isUsableCommitmentLine(line) || selectedCommitmentLines.filter(
                              (entry) => entry.commitment_line_id === line.commitment_line_id,
                            ).length !== 1}
                          >
                            Line {line.commitment_line_id} · {line.variant_sku || "SKU unavailable"}
                            {" · "}Ordered {line.ordered_quantity} · Received {line.received_to_date}
                            {" · "}Remaining {line.remaining_quantity}
                          </option>
                        ))}
                      </select>
                      {selectedCommitmentLine && (
                        <small>
                          Ordered {selectedCommitmentLine.ordered_quantity} · Received {selectedCommitmentLine.received_to_date}
                          {" · "}Remaining {selectedCommitmentLine.remaining_quantity}
                        </small>
                      )}
                    </td>
                  )}
                  <td>
                    <label className="sr-only" htmlFor={`prep-location-${row.sourceRow}`}>
                      Existing receiving location for source row {row.sourceRow}
                    </label>
                    <select
                      id={`prep-location-${row.sourceRow}`}
                      value={choice?.locationId ?? ""}
                      onChange={(event) => updateChoice(row.sourceRow, { locationId: event.currentTarget.value })}
                      disabled={!preparation.enabled}
                      aria-label={`Existing receiving location for source row ${row.sourceRow}`}
                    >
                      <option value="">Choose location</option>
                      {preparation.locations.map((location) => (
                        <option key={location.id} value={location.id}>{locationLabel(location)}</option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <label className="sr-only" htmlFor={`prep-quantity-${row.sourceRow}`}>
                      Warehouse-confirmed quantity for source row {row.sourceRow}
                    </label>
                    <input
                      id={`prep-quantity-${row.sourceRow}`}
                      type="text"
                      inputMode="decimal"
                      value={choice?.quantity ?? ""}
                      onChange={(event) => updateChoice(row.sourceRow, { quantity: event.currentTarget.value })}
                      aria-label={`Warehouse-confirmed quantity for source row ${row.sourceRow}`}
                      aria-invalid={Boolean(choice?.quantity) && !validReceiptQuantity(choice.quantity)}
                      disabled={!preparation.enabled}
                    />
                    <small>Enter the quantity for the existing receipt API. Source unit: {row.unit || "unit not mapped"}; no conversion is performed.</small>
                  </td>
                  <td>
                    <span className={`packing-row-status ${errors.length ? "is-invalid" : "is-matched"}`}>{status}</span>
                    {errors.slice(0, 3).map((error) => (
                      <small className="packing-row-error" key={error}>{error}</small>
                    ))}
                  </td>
                </tr>
              );
            })}
            {!rows.length && (
              <tr><td colSpan={mode === "direct" ? 7 : 6} className="empty-table">Map a packing-list worksheet with source rows to prepare a local receipt draft.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="packing-preparation-review">
        <label>
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => {
              setAcknowledged(event.currentTarget.checked);
              if (!event.currentTarget.checked) preparation.onSourceChange?.();
            }}
            disabled={!preparation.enabled || hasBlockingRows || !rows.length}
          />
          <span>I reviewed the source units and confirmed each quantity is suitable for the existing receipt API; no unit conversion is performed.</span>
        </label>
        <button type="button" className="button button-dark" onClick={useReviewedLines} disabled={!canReview}>
          Use reviewed lines in receipt draft
        </button>
      </div>
      <p className="packing-preparation-foot">
        {hasBlockingRows
          ? "Resolve every row before handoff. Invalid, unmatched, ambiguous, pending, or denied rows are never dropped."
          : "The handoff passes reviewed lines only to the existing receipt-draft flow; it does not submit or save them."}
      </p>
    </section>
  );
}
