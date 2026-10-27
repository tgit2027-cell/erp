export type AssetDraft = {
  asset_identifier: string;
  name: string;
  description: string;
  acquisition_date: string;
  cost: string;
  account_id: string;
  business_unit_id: string;
  dimension_value_ids: string[];
  reference: string;
  location_details: string;
};
export type AssetPayload = {
  asset_identifier: string;
  name: string;
  description: string;
  acquisition_date: string;
  currency: "QAR";
  cost: string;
  account_id: number;
  business_unit_id: number | null;
  dimension_value_ids: number[];
  reference: string;
  location_details: string;
};
export type AssetChoiceContext = {
  accounts: { id: number }[];
  business_units: { id: number }[];
  dimension_values: { id: number; dimension_id: number }[];
};

export const blankAssetDraft = (): AssetDraft => ({
  asset_identifier: "", name: "", description: "", acquisition_date: "", cost: "",
  account_id: "", business_unit_id: "", dimension_value_ids: [], reference: "", location_details: "",
});
export function validAssetDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000-")) return false;
  const [year, month, day] = value.split("-").map(Number);
  if (month < 1 || month > 12 || day < 1) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  return day <= [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
}
export function validAssetCost(value: string): boolean {
  return /^(?:0|[1-9]\d{0,11})(?:\.\d{1,2})?$/.test(value);
}
export function validateAsset(draft: AssetDraft, context: AssetChoiceContext | null): string[] {
  const errors: string[] = [];
  if (!draft.asset_identifier.trim()) errors.push("Enter an asset identifier.");
  if (draft.asset_identifier.length > 100) errors.push("Asset identifier must be 100 characters or fewer.");
  if (!draft.name.trim()) errors.push("Enter an asset name.");
  if (draft.name.length > 200) errors.push("Asset name must be 200 characters or fewer.");
  if (!draft.description.trim()) errors.push("Enter an asset description.");
  if (draft.description.length > 2000) errors.push("Asset description must be 2,000 characters or fewer.");
  if (!validAssetDate(draft.acquisition_date)) errors.push("Enter an explicit valid acquisition date.");
  if (!validAssetCost(draft.cost)) errors.push("Enter a non-negative proposed cost up to 999999999999.99 with at most two decimal places.");
  const account = Number(draft.account_id);
  if (!draft.account_id || !Number.isSafeInteger(account) || !context?.accounts.some((item) => item.id === account)) {
    errors.push("Choose an available saved account.");
  }
  const unit = Number(draft.business_unit_id);
  if (draft.business_unit_id && (!Number.isSafeInteger(unit) || !context?.business_units.some((item) => item.id === unit))) {
    errors.push("Choose an available business unit or leave it not proposed.");
  }
  const ids = draft.dimension_value_ids.map(Number);
  if (new Set(ids).size !== ids.length) errors.push("Dimension values must be distinct.");
  if (ids.some((id) => !Number.isSafeInteger(id) || !context?.dimension_values.some((item) => item.id === id))) {
    errors.push("One or more saved dimension values are unavailable.");
  }
  if (draft.reference.length > 200) errors.push("Asset reference must be 200 characters or fewer.");
  if (draft.location_details.length > 2000) errors.push("Asset location details must be 2,000 characters or fewer.");
  return errors;
}
export function payloadFromAsset(draft: AssetDraft, context: AssetChoiceContext): AssetPayload {
  const errors = validateAsset(draft, context);
  if (errors.length) throw new Error(errors.join(" "));
  return {
    asset_identifier: draft.asset_identifier, name: draft.name, description: draft.description,
    acquisition_date: draft.acquisition_date, currency: "QAR", cost: draft.cost,
    account_id: Number(draft.account_id),
    business_unit_id: draft.business_unit_id ? Number(draft.business_unit_id) : null,
    dimension_value_ids: draft.dimension_value_ids.map(Number),
    reference: draft.reference, location_details: draft.location_details,
  };
}
