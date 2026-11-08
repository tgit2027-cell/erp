import type { NavigationSection } from "./SectionNavigation";

// Function navigation is a client-side view choice. API authorization and
// business-unit scope remain independently enforced by existing server checks.
export const coreSections = [
  { id: "retail", name: "Retail & Sales", icon: "01" },
  { id: "procurement", name: "Procurement & Imports", icon: "02" },
  { id: "inventory", name: "Inventory & Warehouses", icon: "03" },
  { id: "delivery", name: "Delivery & After-Sales", icon: "04" },
  { id: "finance", name: "Finance & Accounting", icon: "05" },
  { id: "hr", name: "HR & Payroll", icon: "06" },
  { id: "governance", name: "Governance & Administration", icon: "07" },
  { id: "reports", name: "Reports", icon: "08" },
  { id: "purchasing", name: "Purchasing Workbench", icon: "09" },
];
// The HR Portal for all staff, shown only while the server has it switched on (ERP_HR_PORTAL=on).
export const portalSection = { id: "portal", name: "HR Portal", icon: "P" };

type Destination = { id: string; label: string; favoriteable?: boolean };

// Phase 2. Shown only when the sign-in says so: to the Master Admin while it is not live, then to factory staff.
export const factorySection = { id: "factory", name: "Factory", icon: "10" };
// Phase 2, the same way: the Master Admin before it is live, then people with a project permission.
export const projectsSection = { id: "projects", name: "Projects & Fit-Out", icon: "11" };
export type SidebarSection = Omit<NavigationSection, "children"> & { children: Destination[] };

export function sidebarSections(access: {
  isStaff: boolean; isSuperuser: boolean; canManageReportPanels: boolean;
  /** Master Admins and General Admins; defaults to system administrators. */
  canManageUsers?: boolean;
  /** People who prepare or approve go-live imports; defaults to system administrators. */
  canImport?: boolean;
  /** The Factory section (Phase 2): the Master Admin before it is live, factory staff after. */
  canSeeFactory?: boolean;
  /** The Projects & Fit-Out section (Phase 2): the Master Admin before it is live, project staff after. */
  canSeeProjects?: boolean;
  /** The HR Portal is switched on for this ERP; defaults to off. Everyone signed in sees it then. */
  hrPortalEnabled?: boolean;
  /** The sections of the person's areas (Sam, 5 Nov 2026). Left out, every section shows. */
  visibleSections?: string[];
}): SidebarSection[] {
  const visible = access.visibleSections ? new Set(access.visibleSections) : null;
  const withFactory = [...coreSections, ...(access.canSeeFactory ? [factorySection] : []),
    ...(access.canSeeProjects ? [projectsSection] : [])];
  const shown = access.hrPortalEnabled ? [portalSection, ...withFactory] : withFactory;
  return shown.filter((section) => !visible || visible.has(section.id)).map((section) => {
    let children: Destination[] = [{ id: "main", label: section.name, ...(["finance", "hr"].includes(section.id) ? { favoriteable: false } : {}) }];
    if (section.id === "retail") children = [
      { id: "main", label: "Retail & Sales" },
      { id: "quote", label: "Create a quote" },
      { id: "actions", label: "Quote collection & holds" },
      { id: "register", label: "Authorized quote register" },
      { id: "collections", label: "Collection follow-up", favoriteable: true },
      { id: "invoices", label: "Customer invoices", favoriteable: true },
      { id: "customers", label: "Customers", favoriteable: true },
      { id: "commission", label: "My commission", favoriteable: true },
    ];
    if (section.id === "procurement") children = [
      { id: "main", label: "Procurement & Imports" },
      { id: "commitments", label: "Create purchase commitment" },
      { id: "progress", label: "Commitment receipt progress" },
      { id: "receipt", label: "Prepare imported-goods receipt" },
      { id: "packing", label: "Packing-list mapping & review" },
      { id: "registers", label: "Receipt & landed-charge registers" },
      { id: "kyc", label: "Supplier onboarding (KYC)", favoriteable: true },
      ...(access.isStaff
        ? [{ id: "suppliers", label: "Staff counterparty directory" }]
        : []),
    ];
    if (section.id === "inventory") children = [
      { id: "main", label: "Inventory quantities" },
      { id: "locations", label: "Permitted locations" },
      { id: "receipt", label: "Prepare imported-goods receipt" },
      { id: "commitments", label: "Purchase commitments" },
      { id: "progress", label: "Commitment receipt progress" },
      { id: "packing", label: "Packing-list preview" },
      { id: "registers", label: "Inventory & transfer registers" },
      { id: "transfers", label: "Stock transfer requests" },
      { id: "capacity", label: "Warehouse capacity" },
      { id: "counts", label: "Stock counts", favoriteable: true },
      { id: "low-stock", label: "Low stock", favoriteable: true },
      { id: "stock-value", label: "Stock value", favoriteable: true },
      { id: "items", label: "Stock items", favoriteable: true },
      { id: "catalogue", label: "Read a catalogue", favoriteable: true },
    ];
    if (section.id === "delivery") children = [
      { id: "main", label: "Delivery & After-Sales" },
      { id: "quote", label: "Authorized quote lookup" },
      { id: "preparation", label: "Picking preparation" },
      { id: "handover", label: "Readiness & handover actions" },
      { id: "quotes", label: "Quote register" },
      { id: "storage", label: "Storage-charge register" },
      { id: "reservations", label: "Reservation register" },
      { id: "deliveries", label: "Delivery register" },
      { id: "returns", label: "Sales-return intake" },
    ];
    if (section.id === "finance") children = [
      { id: "main", label: section.name, favoriteable: false },
      { id: "supplier-payments", label: "Supplier payments", favoriteable: true },
      { id: "ledger", label: "General ledger", favoriteable: true },
      { id: "refunds", label: "Customer refunds", favoriteable: true },
      { id: "receivables", label: "Who owes us (receivables)", favoriteable: true },
      { id: "payables", label: "What we owe (payables)", favoriteable: true },
      { id: "supplier-bills", label: "Supplier bills", favoriteable: true },
      { id: "cash", label: "Bank and cash", favoriteable: true },
      { id: "bank-rec", label: "Bank reconciliation", favoriteable: true },
      { id: "expenses", label: "Expenses and petty cash", favoriteable: true },
      { id: "budgets", label: "Budgets", favoriteable: true },
      { id: "assets", label: "Fixed assets", favoriteable: true },
      { id: "journals", label: "Journal entries", favoriteable: true },
      { id: "notes", label: "Credit and debit notes", favoriteable: true },
      { id: "advances", label: "Supplier advances", favoriteable: true },
      { id: "old-balances", label: "Old system balances", favoriteable: true },
      { id: "forecast", label: "Cash-flow forecast", favoriteable: true },
      { id: "payment-plan", label: "Weekly payments", favoriteable: true },
      { id: "sharing", label: "Cost sharing", favoriteable: true },
      { id: "cheques", label: "Cheques", favoriteable: true },
    ];
    if (section.id === "hr") children = [
      { id: "main", label: section.name, favoriteable: false },
      { id: "leave", label: "Leave", favoriteable: true },
      { id: "attendance", label: "Attendance", favoriteable: true },
      { id: "overtime", label: "Overtime", favoriteable: true },
      { id: "pay", label: "Salaries", favoriteable: true },
      { id: "payroll", label: "Payroll", favoriteable: true },
      { id: "incentives", label: "Commission and rewards", favoriteable: true },
      { id: "end-of-service", label: "End of service", favoriteable: true },
      { id: "loans", label: "Loans and advances", favoriteable: true },
      { id: "letters", label: "Letters", favoriteable: true },
      ...(access.hrPortalEnabled ? [
        { id: "portal-posts", label: "HR Portal posts", favoriteable: false },
        { id: "portal-suggestions", label: "Staff suggestions", favoriteable: false },
      ] : []),
    ];
    if (section.id === "portal") children = [
      { id: "main", label: "Portal home", favoriteable: false },
      { id: "library", label: "Policies and procedures", favoriteable: false },
      { id: "news", label: "Newsletter and events", favoriteable: false },
      { id: "photos", label: "Events and photos", favoriteable: false },
      { id: "leave", label: "My leave", favoriteable: false },
      { id: "attendance", label: "My attendance", favoriteable: false },
      { id: "overtime", label: "My overtime", favoriteable: false },
      { id: "letters", label: "E-docs and letters", favoriteable: false },
      { id: "suggestions", label: "Suggestion box", favoriteable: false },
      { id: "password", label: "My password", favoriteable: false },
    ];
    if (section.id === "factory") children = [
      { id: "main", label: "Job orders", favoriteable: false },
      { id: "planning", label: "Production schedule", favoriteable: false },
      { id: "dashboard", label: "Factory dashboard", favoriteable: false },
      { id: "team", label: "Factory team", favoriteable: false },
      { id: "machines", label: "Machines and spare parts", favoriteable: false },
      { id: "learning", label: "What the factory learned", favoriteable: false },
      { id: "materials", label: "Raw materials", favoriteable: false },
      ...(access.isSuperuser ? [{ id: "setup", label: "Factory setup", favoriteable: false }] : []),
    ];
    if (section.id === "projects") children = [
      { id: "main", label: "Projects", favoriteable: false },
      { id: "tenders", label: "Tenders", favoriteable: false },
      { id: "bonds", label: "Bonds and guarantees", favoriteable: false },
      { id: "cash", label: "Projects cash flow", favoriteable: false },
      ...(access.isSuperuser ? [{ id: "setup", label: "Project setup", favoriteable: false }] : []),
    ];
    if (section.id === "purchasing") children = [
      { id: "main", label: "Purchasing Workbench" },
      { id: "quote", label: "Quote lookup" },
      { id: "transactions", label: "Transaction workbench" },
      { id: "registers", label: "Purchasing records" },
    ];
    if (section.id === "reports") children = [
      { id: "main", label: "Reports & Dashboards" },
      ...(access.isStaff
        ? [{ id: "reconciliation", label: "Scenario reconciliation", favoriteable: true }]
        : []),
      ...(access.isSuperuser
        ? [{ id: "audit", label: "Audit log", favoriteable: true }]
        : []),
    ];
    if (section.id === "governance") children = [
      { id: "main", label: "Governance & Administration" },
      ...(access.isStaff ? [
        { id: "presentation", label: "Business-unit palettes" },
        { id: "purchasing-config", label: "Purchasing configuration review" },
        { id: "reporting", label: "Reporting governance" },
      ] : []),
      ...(access.canManageReportPanels
        ? [{ id: "report-panels", label: "Manage report panels" }]
        : []),
      ...(access.isSuperuser
        ? [{ id: "capacity-admin", label: "Capacity locations & assignments" },
          { id: "capacity-audit", label: "Warehouse capacity audit" }]
        : []),
      ...((access.canManageUsers ?? access.isSuperuser)
        ? [{ id: "transfer-permissions", label: "Stock transfer permissions" },
          { id: "users", label: "Users and permissions", favoriteable: true },
          { id: "approval-limits", label: "Approval limits", favoriteable: true },
          { id: "alerts", label: "Alerts", favoriteable: true }]
        : []),
      ...((access.canImport ?? access.isSuperuser)
        ? [{ id: "imports", label: "Go-live imports", favoriteable: true }]
        : []),
    ];
    return { ...section, children };
  });
}
