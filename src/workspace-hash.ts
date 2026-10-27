import { sidebarSections } from "./sidebar-destinations";

export type WorkspaceRoute = { section: string; child: string };

const legacyRoutes: Record<string, WorkspaceRoute> = {
  "reports/panels": { section: "governance", child: "report-panels" },
  "governance/authority": { section: "governance", child: "main" },
  "inventory/receiving": { section: "inventory", child: "receipt" },
  "delivery/registers": { section: "delivery", child: "quotes" },
};

export function parseWorkspaceHash(hash: string): WorkspaceRoute | null {
  if (!hash.startsWith("#/")) return null;
  let parts: string[];
  try {
    parts = hash.slice(2).split("/").map((part) => decodeURIComponent(part));
  } catch {
    return { section: "", child: "" };
  }
  if (parts.length !== 2 || !parts[0] || !parts[1]) return { section: parts[0] ?? "", child: parts[1] ?? "" };
  return legacyRoutes[`${parts[0]}/${parts[1]}`] ?? { section: parts[0], child: parts[1] };
}

export function workspaceHash(section: string, child = "main") {
  return `#/${encodeURIComponent(section)}/${encodeURIComponent(child)}`;
}

export function normalizedWorkspaceHash(hash: string) {
  const route = parseWorkspaceHash(hash);
  return route ? workspaceHash(route.section, route.child) : hash;
}

export function routeIsAvailable(
  route: WorkspaceRoute,
  access: { isStaff: boolean; isSuperuser: boolean; canManageReportPanels: boolean },
) {
  return sidebarSections(access).some((section) => section.id === route.section
    && section.children.some((child) => child.id === route.child));
}

export function destinationKey(section: string, child = "main") {
  return child === "main" ? section : `${section}.${child}`;
}
