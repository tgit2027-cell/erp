/** Local preparation only; source metadata is never a receipt API payload. */
export type PreparedPackingLine = {
  commitmentLineId?: number;
  sourceRow: number;
  sourceCode: string;
  sourceDescription: string;
  sourceQuantity: string;
  sourceUnit: string;
  lineReference: string;
  warehouseVolumeM3: string;
  variantId: number;
  variantSku: string;
  itemCode: string;
  itemDescription: string;
  locationId: number;
  locationCode: string;
  quantity: string;
};

export type ReviewedPackingReceipt = {
  commitmentId?: number;
  supplierId?: number;
  unitId: number;
  fileName: string;
  lines: PreparedPackingLine[];
};

export type PackingPreparationOptions = {
  commitments?: {
    state: "idle" | "loading" | "ready" | "error";
    error: string;
    lines: PackingCommitmentLine[];
  };
  /** User plus business unit; changes invalidate all local mapping/review state. */
  contextKey: string;
  locations: { id: number; code: string; name: string; parent_id: number | null }[];
  enabled: boolean;
  reason: string;
  onReviewed: (draft: ReviewedPackingReceipt) => void;
  /** Editing the source/enrichment invalidates any previously copied receipt draft. */
  onSourceChange?: () => void;
};

/** Existing authorized progress response; deliberately excludes prices. */
export type PackingCommitmentLine = {
  commitment_id: number;
  commitment_line_id: number;
  supplier_id: number;
  variant_id: number;
  variant_sku: string;
  item_description: string;
  ordered_quantity: string;
  received_to_date: string;
  remaining_quantity: string;
};
