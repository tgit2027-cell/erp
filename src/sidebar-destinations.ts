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

type Destination = { id: string; label: string; favoriteable?: boolean };
export type SidebarSection = Omit<NavigationSection, "children"> & { children: Destination[] };

export function sidebarSections(access: {
  isStaff: boolean; isSuperuser: boolean; canManageReportPanels: boolean;
}): SidebarSection[] {
  return coreSections.map((section) => {
    let children: Destination[] = [{ id: "main", label: section.name, ...(["finance", "hr"].includes(section.id) ? { favoriteable: false } : {}) }];
    if (section.id === "retail") children = [
      { id: "main", label: "Retail & Sales" },
      { id: "quote", label: "Create a quote" },
      { id: "actions", label: "Quote collection & holds" },
      { id: "register", label: "Authorized quote register" },
      { id: "collections", label: "Collection follow-up", favoriteable: true },
      { id: "invoices", label: "Customer invoices", favoriteable: true },
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
          { id: "capacity-audit", label: "Warehouse capacity audit" },
          { id: "transfer-permissions", label: "Stock transfer permissions" }]
        : []),
    ];
    return { ...section, children };
  });
}
