import type { DraftSnapshot, DraftLine } from "./FinanceDraftWorkspace";
import { acknowledgeReopen, protectedCreate, setRecoverySession } from "./finance-lab-recovery";

export type LabContext = {
  legal_entity_label: string;
  business_units: { id: number; code: string; name: string }[];
  accounts: { id: number; code: string; name_en: string; name_ar: string }[];
  dimensions: { id: number; code: string; label_en: string; label_ar: string }[];
  dimension_values: { id: number; dimension_id: number; code: string; label_en: string; label_ar: string }[];
};
export type SavedDraft = {
  id: number; revision: number; title_en: string; title_ar: string;
  kind?: "journal" | "recurring_template" | "opening_balance" | "accrual_adjustment" | "prepayment_adjustment";
  as_of_date?: string | null;
  recurrence_proposal?: { start: string; end: string; dates: string[] };
  adjustment_proposal?: { date: string; explanation: string };
  lines: { account_code: string; business_unit_code: string; dimensions: { code: string }[];
    debit: string; credit: string; memo_en: string; memo_ar: string }[];
  review: { debit_total: string; credit_total: string; balanced: boolean };
   history: { revision: number; created_at: string; snapshot: Omit<SavedDraft, "history"> & { as_of_date?: string | null } }[];
};
export class LabError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}
let csrf = "";
export async function labRequest<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  // Serialize once: retries cannot pick up a mutated form object.
  const encoded = body === undefined ? undefined : JSON.stringify(body);
  const send = async (identity = ""): Promise<T> => {
  const response = await fetch(`/lab/${path}/`, {
    method, credentials: "same-origin", cache: "no-store",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf,
      ...(identity ? {"X-Finance-Request-ID": identity} : {}) },
    body: encoded,
  });
  const data = await response.json().catch(() => {
    if (response.ok && method === "POST") throw new Error("Create acknowledgement unreadable; check request outcome.");
    return { detail: "Invalid lab response." };
  });
  if (!response.ok) {
    if (response.status === 403) setRecoverySession("", "");
    if (identity && response.status >= 500) {
      throw new Error("Create outcome is uncertain; check the request status or explicitly retry the original request.");
    }
    throw new LabError(data.detail ?? `Lab request failed (${response.status}).`, response.status);
  }
  if (data.csrf_token) csrf = data.csrf_token;
  if (path === "session") setRecoverySession(data.recovery_scope ?? "", csrf);
  if (method === "GET") acknowledgeReopen(path);
  return data as T;
  };
  return method === "POST" ? protectedCreate(path, encoded ? JSON.parse(encoded) : undefined, send) : send();
}
export function editorState(context: LabContext, draft?: SavedDraft) {
  return {
    accounts: context.accounts.map((a) => ({ code: a.code, nameEn: a.name_en, nameAr: a.name_ar })),
    dimensions: context.dimensions.map((d) => ({ code: d.code, labelEn: d.label_en, labelAr: d.label_ar })),
    dimensionValues: context.dimension_values.map((v) => ({
      code: v.code, dimensionCode: context.dimensions.find((d) => d.id === v.dimension_id)?.code ?? "",
      labelEn: v.label_en, labelAr: v.label_ar,
    })),
    draft: draft ? {
      titleEn: draft.title_en, titleAr: draft.title_ar,
      debitTotal: draft.review.debit_total, creditTotal: draft.review.credit_total,
      lines: draft.lines.map((line): DraftLine => ({
        accountCode: line.account_code, businessUnitCode: line.business_unit_code,
        dimensionValueCode: line.dimensions[0]?.code ?? "", dimensionValueCodes: line.dimensions.map((d) => d.code),
        debit: line.debit, credit: line.credit, memoEn: line.memo_en, memoAr: line.memo_ar,
      })),
    } : undefined,
  };
}
export function journalPayload(draft: DraftSnapshot, context: LabContext) {
  function id(code: string, rows: { id: number; code: string }[]) {
    const row = rows.find((item) => item.code === code);
    if (!row) throw new Error("A saved choice is unavailable; refresh the lab before saving. / خيار محفوظ غير متاح.");
    return row.id;
  }
  return {
    title_en: draft.titleEn, title_ar: draft.titleAr,
    lines: draft.lines.map((line) => ({
      account_id: id(line.accountCode, context.accounts),
      business_unit_id: line.businessUnitCode ? id(line.businessUnitCode, context.business_units) : null,
      dimension_value_ids: (line.dimensionValueCodes ?? (line.dimensionValueCode ? [line.dimensionValueCode] : []))
        .map((code) => id(code, context.dimension_values)),
      debit: line.debit, credit: line.credit, memo_en: line.memoEn, memo_ar: line.memoAr,
    })),
  };
}
