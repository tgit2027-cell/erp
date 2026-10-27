import "./finance-lab-review.css";

const destinations = [
  ["Review index", "/lab-review-ui/"],
  ["Journals", "/lab-ui/"],
  ["Allocation", "/lab-allocation-ui/"],
  ["Budget", "/lab-budget-ui/"],
  ["Reconciliation", "/lab-reconciliation-ui/"],
  ["Expenses", "/lab-expense-ui/"],
  ["Assets", "/lab-assets-ui/"],
  ["Forecasts", "/lab-forecasts-ui/"],
  ["Supplier invoices", "/lab-invoices-ui/"],
  ["Supplier adjustments", "/lab-adjustments-ui/"],
  ["Customer invoices", "/lab-customer-invoices-ui/"],
  ["Customer credits", "/lab-customer-credits-ui/"],
  ["Supplier advances", "/lab-supplier-advances-ui/"],
  ["Reversals", "/lab-reversals-ui/"],
] as const;

export function FinanceLabNavigation() {
  return <nav className="lab-nav" aria-label="Finance lab navigation">
    <a className="lab-nav-brand" href="/lab-ui/" aria-label="Finance lab home">T / F</a>
    <div className="lab-nav-links">
      {destinations.map(([label, href]) => <a href={href} key={href}
        aria-current={window.location.pathname === href ? "page" : undefined}>{label}</a>)}
    </div>
  </nav>;
}

export default FinanceLabNavigation;
