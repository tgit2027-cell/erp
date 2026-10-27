import { useCallback, useEffect, useState } from "react";

export type ReportPanelCode =
  | "sales"
  | "best_sellers"
  | "costs_expenses"
  | "gross_margin"
  | "inventory"
  | "purchase_history"
  | "transfer_history"
  | "budgets_actuals";

export type ReportCapabilityCode =
  | "sales"
  | "category_sales"
  | "supplier_cost"
  | "landed_cost"
  | "gross_margin"
  | "expenses"
  | "inventory"
  | "purchase_history"
  | "transfer_history"
  | "budgets";

export type ReportFilters = {
  unit?: number | string;
  company?: number | string;
  location?: number | string;
  period?: "daily" | "monthly" | "quarterly";
  start?: string;
  end?: string;
  page?: number;
  page_size?: number;
};

export type ReportRow = Record<string, string | number | boolean | null | undefined>;
export type ReportPagination = {
  page: number;
  page_size: number;
  total_count: number;
  total_pages: number;
  previous_page: number | null;
  next_page: number | null;
};
export type ReportScope = {
  scope_type: "business_unit" | "company" | "authorized_units";
  business_unit: { id: number; code: string; name: string } | null;
  business_unit_ids: number[];
  company: { id: number; code: string; name: string } | null;
};

export type ReportData = {
  panel: ReportPanelCode;
  title: string;
  granularity: "daily" | "monthly" | "quarterly";
  currency: "QAR";
  filters: {
    start: string;
    end: string;
    location_id: number | null;
    unit_id: number | null;
    company_id: number | null;
  };
  scope: ReportScope;
  fields: string[];
  rows: ReportRow[];
  pagination: ReportPagination;
  policy_label: string;
  summary?: Record<string, string>;
  trend?: ReportRow[];
  best_selling_items?: ReportRow[];
  best_selling_categories?: ReportRow[] | null;
  expense_account_detail?: ReportRow[];
  note?: string;
  budgets_configured?: boolean;
  categories_permission_required?: boolean;
};

export type SalesExamplesData = {
  panel: "sales_examples";
  title: "Synthetic examples — not operational sales";
  source: "DemoReportSalesFact";
  example_count: number;
  summary: {
    illustrative_revenue: string;
    illustrative_quantity: string;
  };
  currency: "QAR";
  filters: {
    start: string;
    end: string;
    location_id: number | null;
    unit_id: number | null;
    company_id: number | null;
  };
  scope: ReportScope;
  policy_label: string;
  note: string;
};

export type ReportUnitChoice = { id: number; code: string; name: string };
export type ReportCompanyChoice = {
  id: number;
  code: string;
  name: string;
  business_unit_ids?: number[];
};
export type ReportCatalog = {
  panels: Array<{ code: ReportPanelCode; label: string }>;
  scopes: Record<
    ReportCapabilityCode,
    {
      business_units: ReportUnitChoice[];
      companies: ReportCompanyChoice[];
      locations: Array<{ id: number; name: string; business_units: number[] }>;
    }
  >;
};

export type ReportConfiguration = {
  users: Array<{ id: number; username: string }>;
  roles: Array<{ id: number; code: string; name: string; members: string[] }>;
  memberships: Array<{
    id: number;
    role_id: number;
    role_name: string;
    user_id: number;
    username: string;
    revoked: boolean;
  }>;
  units: ReportUnitChoice[];
  companies: ReportCompanyChoice[];
  panels: Array<{ code: ReportPanelCode; label: string }>;
  capabilities: Array<{ code: ReportCapabilityCode; label: string }>;
  panel_grants: Array<{
    id: number;
    user_id: number | null;
    username: string | null;
    role_id: number | null;
    role_name: string | null;
    panel: ReportPanelCode;
    revoked: boolean;
  }>;
  data_grants: Array<{
    id: number;
    user_id: number | null;
    username: string | null;
    role_id: number | null;
    role_name: string | null;
    capability: ReportCapabilityCode;
    business_unit_id: number | null;
    business_unit: string | null;
    legal_entity_id: number | null;
    company: string | null;
    revoked: boolean;
  }>;
  budgets: Array<{
    id: number;
    business_unit_id: number;
    location_id: number | null;
    metric: string;
    granularity: string;
    period_start: string;
    period_end: string;
    amount: string;
  }>;
  locations: Array<{ id: number; name: string; business_units: number[] }>;
  items: Array<{
    id: number;
    item_code: string;
    description: string;
    brand_name: string;
    category_label: string;
  }>;
};

export type ManagedReportPanelGrant = {
  id: number;
  user_id: number | null;
  username: string | null;
  role_id: number | null;
  role_name: string | null;
  panel: ReportPanelCode;
  revoked: boolean;
};

export type ReportPanelManagement = {
  users: Array<{ id: number; username: string }>;
  roles: Array<{ id: number; code: string; name: string }>;
  panels: Array<{ code: ReportPanelCode; label: string }>;
  panel_grants: ManagedReportPanelGrant[];
};

export type RemoteState<T> = {
  data: T | null;
  loading: boolean;
  error: string;
  refresh: () => void;
};

async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    credentials: "same-origin",
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message =
      typeof data.detail === "string"
        ? data.detail
        : Object.values(data).flat().join(" ") || `Request failed (${response.status}).`;
    throw new Error(String(message));
  }
  return data as T;
}

function useReportResource<T>(
  path: string | null,
  dependencies: string,
): RemoteState<T> {
  const [result, setResult] = useState<{
    key: string; data: T | null; loading: boolean; error: string;
  }>({ key: "", data: null, loading: Boolean(path), error: "" });
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => {
    setResult({ key: "", data: null, loading: true, error: "" });
    setRevision((value) => value + 1);
  }, []);
  const key = JSON.stringify([path, dependencies, revision]);

  useEffect(() => {
    if (!path) {
      setResult({ key, data: null, loading: false, error: "" });
      return;
    }
    const controller = new AbortController();
    setResult({ key, data: null, loading: true, error: "" });
    requestJson<T>(path, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setResult({ key, data, loading: false, error: "" });
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setResult({
          key, data: null, loading: false,
          error: reason instanceof Error ? reason.message : "The report request failed.",
        });
      });
    return () => controller.abort();
  }, [path, key]);

  useEffect(() => {
    const revalidate = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("focus", revalidate);
    document.addEventListener("visibilitychange", revalidate);
    return () => {
      window.removeEventListener("focus", revalidate);
      document.removeEventListener("visibilitychange", revalidate);
    };
  }, [refresh]);

  const current = result.key === key;
  return {
    data: current && !result.loading && !result.error ? result.data : null,
    loading: Boolean(path) && (!current || result.loading),
    error: current ? result.error : "",
    refresh,
  };
}

function queryString(filters: ReportFilters = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return params.toString();
}

export function useReportCatalog(enabled = true): RemoteState<ReportCatalog> {
  return useReportResource<ReportCatalog>(enabled ? "/api/reports/" : null, String(enabled));
}

export function useReportData(
  panel: ReportPanelCode | null,
  filters: ReportFilters,
  enabled = true,
): RemoteState<ReportData> {
  const query = queryString(filters);
  const path = panel && enabled
    ? `/api/reports/${panel}/${query ? `?${query}` : ""}`
    : null;
  return useReportResource<ReportData>(path, query);
}

export type VisualSeries = { key: string; label: string; values: string[] };
export type VisualChart = {
  id: string;
  title: string;
  kind: "column" | "bar" | "line";
  unit: string;
  categories: string[];
  series: VisualSeries[];
  stacked: boolean;
  note: string;
};
export type ReportVisual = {
  panel: ReportPanelCode;
  title: string;
  granularity: "daily" | "monthly" | "quarterly";
  currency: "QAR";
  filters: ReportData["filters"];
  scope: ReportScope;
  row_count: number;
  kpis: Array<{ label: string; value: string; unit: string }>;
  charts: VisualChart[];
  policy_label: string;
};

export function useReportVisual(
  panel: ReportPanelCode | null,
  filters: ReportFilters,
  enabled = true,
): RemoteState<ReportVisual> {
  const query = queryString({ ...filters, page: undefined, page_size: undefined });
  const path = panel && enabled
    ? `/api/reports/${panel}/visual/${query ? `?${query}` : ""}`
    : null;
  return useReportResource<ReportVisual>(path, query);
}

export function useSalesExamples(
  filters: ReportFilters,
  enabled = true,
): RemoteState<SalesExamplesData> {
  const query = queryString({
    unit: filters.unit,
    company: filters.company,
    location: filters.location,
    period: filters.period,
    start: filters.start,
    end: filters.end,
  });
  const path = enabled
    ? `/api/reports/sales-examples/${query ? `?${query}` : ""}`
    : null;
  return useReportResource<SalesExamplesData>(path, query);
}

export function useReportingConfiguration(
  enabled: boolean,
): RemoteState<ReportConfiguration> {
  return useReportResource<ReportConfiguration>(
    enabled ? "/api/governance/reports/" : null,
    String(enabled),
  );
}

export function useReportPanelManagement(enabled: boolean): RemoteState<ReportPanelManagement> {
  return useReportResource<ReportPanelManagement>(
    enabled ? "/api/governance/report-panels/" : null,
    String(enabled),
  );
}

export async function saveReportingConfiguration(
  csrfToken: string,
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>("/api/governance/reports/", {
    method: "POST",
    headers: { "X-CSRFToken": csrfToken },
    body: JSON.stringify(payload),
  });
}

export async function saveReportPanelManagement(
  csrfToken: string,
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>("/api/governance/report-panels/", {
    method: "POST",
    headers: { "X-CSRFToken": csrfToken },
    body: JSON.stringify(payload),
  });
}

export async function downloadReportExport(
  csrfToken: string,
  panel: ReportPanelCode,
  filters: ReportFilters,
  format: "xlsx" | "docx",
): Promise<void> {
  const response = await fetch(`/api/reports/${panel}/export/`, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken,
    },
    body: JSON.stringify({ ...filters, format }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const message =
      typeof data.detail === "string"
        ? data.detail
        : Object.values(data).flat().join(" ") || `Export failed (${response.status}).`;
    throw new Error(String(message));
  }
  const file = await response.blob();
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = `synthetic-${panel}-report.${format}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
