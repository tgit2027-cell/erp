// Each area of the ERP has its own colours (Sam, 6 Nov 2026).
// Showrooms keep their business unit's colours: Tivoli orange, Roche Bobois
// blue, Natuzzi green. These sections switch to their area's colours instead,
// whichever showroom is selected. Finance, Purchasing, Reports, Governance and
// the Overview keep following the selected showroom (provisional default).
export const AREA_THEME_BY_SECTION: Record<string, string> = {
  factory: "factory", // brown
  projects: "projects", // crimson: Projects & Fit-Out
  inventory: "warehouse", // yellow: Warehouse
  delivery: "warehouse",
  logistics: "warehouse", // yellow: Logistics & Imports
  procurement: "warehouse",
  portal: "hr", // purple: HR portal
  hr: "hr", // purple: HR & Payroll (provisional, same family as the portal)
};

export function areaThemeFor(section: string | null | undefined): string | null {
  return (section && AREA_THEME_BY_SECTION[section]) || null;
}
