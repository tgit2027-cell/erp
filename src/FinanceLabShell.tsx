import { FinanceEditorLoader } from "./FinanceEditorLoader";
import { FinanceLabNavigation } from "./FinanceLabNavigation";
import { FinanceLabRecoveryPanel } from "./FinanceLabRecoveryPanel";

// Static, analyzable imports let Vite emit independent editor chunks. No
// application endpoint, permission or payload is selected by this table.
export const financeEditors = {
  "/lab-ui/": { label: "journal editor", load: () => import("./FinanceDraftLab").then((m) => ({ default: m.JournalLab })) },
  "/lab-allocation-ui/": { label: "allocation editor", load: () => import("./FinanceAllocationLab").then((m) => ({ default: m.FinanceAllocationLab })) },
  "/lab-budget-ui/": { label: "budget editor", load: () => import("./FinanceBudgetLab").then((m) => ({ default: m.FinanceBudgetLab })) },
  "/lab-reconciliation-ui/": { label: "reconciliation editor", load: () => import("./FinanceReconciliationLab").then((m) => ({ default: m.FinanceReconciliationLab })) },
  "/lab-expense-ui/": { label: "expense editor", load: () => import("./FinanceExpenseLab").then((m) => ({ default: m.FinanceExpenseLab })) },
  "/lab-assets-ui/": { label: "asset editor", load: () => import("./FinanceAssetLab").then((m) => ({ default: m.FinanceAssetLab })) },
  "/lab-forecasts-ui/": { label: "forecast editor", load: () => import("./FinanceForecastLab").then((m) => ({ default: m.FinanceForecastLab })) },
  "/lab-invoices-ui/": { label: "supplier invoice editor", load: () => import("./FinanceInvoiceLab").then((m) => ({ default: m.FinanceInvoiceLab })) },
  "/lab-customer-invoices-ui/": { label: "customer invoice editor", load: () => import("./FinanceCustomerInvoiceLab").then((m) => ({ default: m.FinanceCustomerInvoiceLab })) },
  "/lab-adjustments-ui/": { label: "supplier adjustment editor", load: () => import("./FinanceAdjustmentLab").then((m) => ({ default: m.FinanceAdjustmentLab })) },
  "/lab-customer-credits-ui/": { label: "customer credit editor", load: () => import("./FinanceCustomerCreditLab").then((m) => ({ default: m.FinanceCustomerCreditLab })) },
  "/lab-supplier-advances-ui/": { label: "supplier advance editor", load: () => import("./FinanceSupplierAdvanceLab").then((m) => ({ default: m.FinanceSupplierAdvanceLab })) },
  "/lab-reversals-ui/": { label: "reversal editor", load: () => import("./FinanceReversalLab").then((m) => ({ default: m.FinanceReversalLab })) },
  "/lab-review-ui/": { label: "saved-draft review", load: () => import("./FinanceLabReview").then((m) => ({ default: m.EmbeddedFinanceLabReview })) },
} as const;

const sample = {
  label: "session-only sample",
  load: () => import("./FinanceSessionSample").then((m) => ({ default: m.FinanceSessionSample })),
};

export function FinanceDraftLab({ sessionSample = false }: { sessionSample?: boolean } = {}) {
  if (sessionSample) return <FinanceEditorLoader load={sample.load} label={sample.label} />;
  const selected = financeEditors[window.location.pathname as keyof typeof financeEditors] ?? financeEditors["/lab-ui/"];
  return <div className="finance-lab-shell">
    <FinanceLabNavigation />
    <FinanceLabRecoveryPanel />
    <FinanceEditorLoader load={selected.load} label={selected.label} />
  </div>;
}
