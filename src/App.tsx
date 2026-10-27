import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { buildPaletteColors, customPaletteContrastIssues } from "./custom-palette-colors.mjs";
import GovernanceWorkspace from "./GovernanceWorkspace";
import FinanceStatusPage from "./FinanceStatusPage";
import { GovernanceReportPanelManager, ReportsArea } from "./ReportingArea";
import ScenarioReconciliation from "./ScenarioReconciliation";
import AuditLog from "./AuditLog";
import PurchasingWorkbench, { PurchasingGovernanceReview } from "./PurchasingWorkbench";
import WarehouseCapacity from "./WarehouseCapacity";
import WarehouseCapacityAuditRegister from "./WarehouseCapacityAuditRegister";
import InventoryStockDetail from "./InventoryStockDetail";
import InventoryRegisters from "./InventoryRegisters";
import StockTransferRequests from "./StockTransferRequests";
import TransferPermissions from "./TransferPermissions";
import SupplierKyc from "./SupplierKyc";
import SectionNavigation from "./SectionNavigation";
import { coreSections as sections, sidebarSections } from "./sidebar-destinations";
import { normalizedWorkspaceHash, parseWorkspaceHash, routeIsAvailable, workspaceHash } from "./workspace-hash";
import { useWorkspaceNavigationSafety } from "./useWorkspaceNavigationSafety";

type Palette = {
  id?: number;
  value: string;
  label: string;
  kind: "built_in" | "custom";
  accent_color?: string;
  colors?: Record<string, string>;
};
type Unit = {
  id: number;
  code: string;
  name: string;
  theme_palette: string;
  palette_value?: string;
  custom_palette?: Palette | null;
  company?: string;
};
type Location = { id: number; code: string; name: string; parent_id: number | null; kind?: string; area?: string };
const LOCATION_KINDS: Record<string, string> = { showroom: "Showroom", warehouse: "Warehouse", factory: "Factory", virtual: "Virtual" };
type Organization = {
  company: { name: string; code: string };
  units: Unit[];
  selected_unit: Unit;
  locations: Location[];
};
type FavoriteDestination = { destination: string; label: string };
type StockRow = {
  id: number;
  item_code: string;
  item_description: string;
  brand_name: string;
  brand_code: string;
  variant_sku: string;
  variant_description: string;
  location_code: string;
  location_name: string;
  unit_code: string;
  unit_name: string;
  quantity: string;
  is_synthetic: boolean;
};
type PageMetadata = {
  page: number;
  page_size: number;
  total_count: number;
  total_pages: number;
  previous_page: number | null;
  next_page: number | null;
};
type StockPage = { results: StockRow[]; pagination: PageMetadata };

const customThemeProperties = [
  "--brand", "--green", "--brand-deep", "--brand-mid", "--brand-soft",
  "--sidebar", "--sidebar-muted", "--focus", "--line", "--sand",
];

function destinationName(destination: string, fallback?: string) {
  if (destination === "dashboard") return "Overview";
  if (destination.includes(".")) {
    const [sectionId, childId] = destination.split(".");
    const child = sidebarSections({ isStaff: true, isSuperuser: true, canManageReportPanels: true })
      .find((item) => item.id === sectionId)?.children.find((item) => item.id === childId);
    return child?.label ?? fallback ?? destination;
  }
  return fallback ?? sections.find((item) => item.id === destination)?.name ?? destination;
}

function StarMark({ filled }: { filled: boolean }) {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" className="star-mark">
      <path
        d="m10 2.6 2.25 4.56 5.03.73-3.64 3.55.86 5.01L10 14.09l-4.5 2.36.86-5.01L2.72 7.9l5.03-.73L10 2.6Z"
        fill={filled ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="1.45"
        strokeLinejoin="round"
      />
    </svg>
  );
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message =
      typeof data.detail === "string"
        ? data.detail
        : Object.values(data).flat().join(" ") || "The request could not be completed.";
    throw new Error(String(message));
  }
  return data as T;
}

function LocationTree({ locations }: { locations: Location[] }) {
  const lookup = useMemo(() => {
    const map = new Map<number | null, Location[]>();
    for (const location of locations) {
      const children = map.get(location.parent_id) ?? [];
      children.push(location);
      map.set(location.parent_id, children);
    }
    return map;
  }, [locations]);
  const render = (parent: number | null, depth: number): React.ReactNode =>
    (lookup.get(parent) ?? []).map((location) => (
      <li key={location.id}>
        <div className="location-node" style={{ paddingLeft: `${depth * 18}px` }}>
          <span className="tree-mark" aria-hidden="true">{depth ? "↳" : "⌖"}</span>
          <strong className="location-code">{location.code}</strong>
          <span>{location.name}</span>
          {location.kind && <span className={`location-kind location-kind-${location.kind}`}>{LOCATION_KINDS[location.kind] ?? location.kind}</span>}
        </div>
        {lookup.has(location.id) && <ul>{render(location.id, depth + 1)}</ul>}
      </li>
    ));
  return <ul className="location-tree">{render(null, 0)}</ul>;
}

export default function App() {
  const [username, setUsername] = useState<string | null>(null);
  const [isStaff, setIsStaff] = useState(false);
  const [isSuperuser, setIsSuperuser] = useState(false);
  const [canManageReportPanels, setCanManageReportPanels] = useState(false);
  const [csrfToken, setCsrfToken] = useState("");
  const [organization, setOrganization] = useState<Organization | null>(null);
  const [paletteOptions, setPaletteOptions] = useState<Palette[]>([]);
  const [paletteUnits, setPaletteUnits] = useState<Unit[]>([]);
  const [paletteUnitId, setPaletteUnitId] = useState("");
  const [paletteValue, setPaletteValue] = useState("");
  const [paletteLoading, setPaletteLoading] = useState(false);
  const [paletteSaving, setPaletteSaving] = useState(false);
  const [paletteLoadKey, setPaletteLoadKey] = useState(0);
  const [paletteContextUnitId, setPaletteContextUnitId] = useState<number | null>(null);
  const [paletteError, setPaletteError] = useState("");
  const [paletteSuccess, setPaletteSuccess] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingPaletteId, setEditingPaletteId] = useState<number | null>(null);
  const [customPaletteName, setCustomPaletteName] = useState("");
  const [customAccent, setCustomAccent] = useState("#B24F00");
  const [stock, setStock] = useState<StockPage | null>(null);
  const [stockLoading, setStockLoading] = useState(false);
  const [stockError, setStockError] = useState("");
  const [stockLocationFilter, setStockLocationFilter] = useState<{ unitId: number; locationId: string }>({ unitId: -1, locationId: "" });
  const [selectedStockBucket, setSelectedStockBucket] = useState<{ row: StockRow; unitId: number; locationId: string } | null>(null);
  const [receivingOpened, setReceivingOpened] = useState(false);
  const [registersOpened, setRegistersOpened] = useState(false);
  const [capacityOpened, setCapacityOpened] = useState(false);
  const [transfersOpened, setTransfersOpened] = useState(false);
  const [availableDestinations, setAvailableDestinations] = useState<FavoriteDestination[]>([]);
  const [favorites, setFavorites] = useState<FavoriteDestination[]>([]);
  const [favoriteBusy, setFavoriteBusy] = useState<string | null>(null);
  const [favoritesError, setFavoritesError] = useState("");
  const initialRoute = typeof window === "undefined" ? null : parseWorkspaceHash(window.location.hash);
  const [section, setSectionState] = useState(initialRoute?.section ?? "dashboard");
  const { canLeaveSection, navigationWarning } = useWorkspaceNavigationSafety(section);
  const lastWorkspaceLocation = useRef(window.location.pathname + window.location.search + window.location.hash);
  const [navigationChild, setNavigationChild] = useState<string | null>(initialRoute?.child ?? null);
  const [navigationRevision, setNavigationRevision] = useState(0);
  const [hashWasPresent, setHashWasPresent] = useState(Boolean(initialRoute));
  const access = { isStaff, isSuperuser, canManageReportPanels };
  const routeAvailable = !hashWasPresent || (
    Boolean(navigationChild) && routeIsAvailable({ section, child: navigationChild ?? "" }, access)
  );
  const routeError = hashWasPresent && !routeAvailable
    ? "This workspace function is unavailable or the link is not recognized. No restricted content was opened."
    : "";
  const navigateTo = useCallback((next: string, child = "main", history: "push" | "replace" = "push") => {
    if (!canLeaveSection(next)) return;
    const hash = workspaceHash(next, child);
    if (window.location.hash !== hash) {
      if (history === "replace") window.history.replaceState(null, "", hash);
      else window.location.hash = hash;
    }
    setSectionState(next);
    setNavigationChild(child);
    setHashWasPresent(true);
    setNavigationRevision((revision) => revision + 1);
    lastWorkspaceLocation.current = window.location.pathname + window.location.search + hash;
  }, [canLeaveSection]);
  const setSection = useCallback((next: string) => {
    if (sidebarSections({ isStaff, isSuperuser, canManageReportPanels }).some((item) => item.id === next)) {
      navigateTo(next, "main");
    } else {
      if (!canLeaveSection(next)) return;
      window.history.pushState(null, "", `${window.location.pathname}${window.location.search}`);
      lastWorkspaceLocation.current = window.location.pathname + window.location.search;
      setSectionState(next);
      setNavigationChild(null);
      setHashWasPresent(false);
      setNavigationRevision((revision) => revision + 1);
    }
  }, [isStaff, isSuperuser, canManageReportPanels, navigateTo, canLeaveSection]);
  const navigationSections = sidebarSections({ isStaff, isSuperuser, canManageReportPanels });
  useEffect(() => {
    if (!routeAvailable || !organization) return;
    const selector = section === "purchasing" && navigationChild
      ? ({ quote: ".pw-quote-form", transactions: ".pw-transactions", registers: ".purchasing-readside" } as Record<string, string>)[navigationChild]
      : undefined;
    if (!selector) return;
    const target = document.querySelector<HTMLElement>(selector);
    if (!target || target.closest("[hidden], [inert]")) return;
    target.tabIndex = -1;
    target.focus({ preventScroll: true });
    target.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [section, navigationChild, routeAvailable, organization?.selected_unit.id]);
  useEffect(() => {
    if (section !== "inventory") return;
    if (["receipt", "commitments", "progress", "packing"].includes(navigationChild ?? "")) setReceivingOpened(true);
    if (navigationChild === "registers") setRegistersOpened(true);
    if (navigationChild === "capacity") setCapacityOpened(true);
    if (navigationChild === "transfers") setTransfersOpened(true);
  }, [section, navigationChild]);
  useEffect(() => {
    const canonicalHash = normalizedWorkspaceHash(window.location.hash);
    if (canonicalHash !== window.location.hash) window.history.replaceState(null, "", canonicalHash);
  }, []);
  useEffect(() => {
    const onHashChange = () => {
      const route = parseWorkspaceHash(window.location.hash);
      if (!canLeaveSection(route?.section ?? "dashboard")) {
        window.history.replaceState(null, "", lastWorkspaceLocation.current);
        return;
      }
      const canonicalHash = normalizedWorkspaceHash(window.location.hash);
      if (canonicalHash !== window.location.hash) window.history.replaceState(null, "", canonicalHash);
      setHashWasPresent(Boolean(route));
      setSectionState(route?.section ?? "dashboard");
      setNavigationChild(route?.child ?? null);
      setNavigationRevision((revision) => revision + 1);
      lastWorkspaceLocation.current = window.location.pathname + window.location.search + window.location.hash;
    };
    window.addEventListener("hashchange", onHashChange);
    window.addEventListener("popstate", onHashChange);
    return () => {
      window.removeEventListener("hashchange", onHashChange);
      window.removeEventListener("popstate", onHashChange);
    };
  }, [canLeaveSection]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const favoritesRequestId = useRef(0);
  const stockRequestId = useRef(0);
  const stockUnitId = useRef<number | null>(null);
  const stockPageSize = useRef(50);
  const unitSwitchQueue = useRef<Promise<void>>(Promise.resolve());
  const unitSwitchRevision = useRef(0);
  const [unitSwitching, setUnitSwitching] = useState(false);

  useEffect(() => {
    const theme = organization?.selected_unit.theme_palette || (username ? "slate" : "tivoli");
    document.documentElement.dataset.unitTheme = theme;
    const root = document.documentElement;
    if (theme === "custom" && organization?.selected_unit.custom_palette?.colors) {
      Object.entries(organization.selected_unit.custom_palette.colors).forEach(([property, value]) => {
        root.style.setProperty(property, value);
      });
    } else {
      customThemeProperties.forEach((property) => root.style.removeProperty(property));
    }
    return () => {
      delete root.dataset.unitTheme;
      customThemeProperties.forEach((property) => root.style.removeProperty(property));
    };
  }, [organization?.selected_unit.theme_palette, organization?.selected_unit.custom_palette, username]);

  const refreshCsrf = useCallback(async () => {
    const data = await api<{ csrfToken: string }>("/api/auth/csrf/");
    setCsrfToken(data.csrfToken);
    return data.csrfToken;
  }, []);

  const invalidateStock = useCallback(() => {
    stockRequestId.current += 1;
    stockUnitId.current = null;
    setStock(null);
    setStockLoading(false);
    setStockError("");
  }, []);

  useEffect(() => () => {
    stockRequestId.current += 1;
    stockUnitId.current = null;
  }, []);

  const loadOrganization = useCallback(async (): Promise<Organization> => {
    const data = await api<Organization>("/api/organization/");
    invalidateStock();
    stockUnitId.current = data.selected_unit.id;
    setOrganization(data);
    return data;
  }, [invalidateStock]);

  const loadFavorites = useCallback(async (unitId?: number) => {
    const requestId = ++favoritesRequestId.current;
    try {
      const path = unitId ? `/api/favorites/?unit=${encodeURIComponent(unitId)}` : "/api/favorites/";
      const data = await api<{ available_destinations: FavoriteDestination[]; favorites: FavoriteDestination[] }>(path);
      if (favoritesRequestId.current !== requestId) return;
      const available = Array.isArray(data.available_destinations) ? data.available_destinations : [];
      const availableIds = new Set(available.map((item) => item.destination));
      setAvailableDestinations(available);
      setFavorites((Array.isArray(data.favorites) ? data.favorites : []).filter((item) => availableIds.has(item.destination)));
      setFavoritesError("");
    } catch (reason) {
      if (favoritesRequestId.current === requestId) {
        setAvailableDestinations([]);
        setFavorites([]);
        setFavoritesError(reason instanceof Error ? reason.message : "Favorites could not be loaded.");
      }
    }
  }, []);

  const toggleFavorite = useCallback(async (destination: string) => {
    if (!availableDestinations.some((item) => item.destination === destination) || favoriteBusy) return;
    setFavoriteBusy(destination);
    setFavoritesError("");
    try {
      const isFavorite = favorites.some((item) => item.destination === destination);
      if (isFavorite) {
        await api<void>(`/api/favorites/${encodeURIComponent(destination)}/`, {
          method: "DELETE",
          headers: { "X-CSRFToken": csrfToken },
        });
      } else {
        await api<{ favorite: FavoriteDestination }>("/api/favorites/", {
          method: "POST",
          headers: { "X-CSRFToken": csrfToken },
          body: JSON.stringify({ destination }),
        });
      }
      await loadFavorites();
    } catch (reason) {
      setFavoritesError(reason instanceof Error ? reason.message : "Could not update favorites.");
    } finally {
      setFavoriteBusy(null);
    }
  }, [availableDestinations, favoriteBusy, favorites, csrfToken, loadFavorites]);

  const selectedUnitId = organization?.selected_unit.id;
  const activeStockLocation = stockLocationFilter.unitId === selectedUnitId ? stockLocationFilter.locationId : "";
  const loadStock = useCallback(async (term = "", page = 1, pageSize = 50, locationId = activeStockLocation) => {
    if (!selectedUnitId || stockUnitId.current !== selectedUnitId) return;
    const requestId = ++stockRequestId.current;
    const isCurrent = () => stockRequestId.current === requestId && stockUnitId.current === selectedUnitId;
    const params = new URLSearchParams({
      unit: String(selectedUnitId),
      page: String(page),
      page_size: String(pageSize),
    });
    if (term.trim()) params.set("search", term.trim());
    if (locationId) params.set("location", locationId);
    setStockLoading(true);
    try {
      const data = await api<StockPage>(`/api/stock-balances/?${params.toString()}`);
      if (isCurrent()) {
        stockPageSize.current = data.pagination.page_size;
        setStock(data);
        setStockError("");
      }
    } catch (reason) {
      if (isCurrent()) {
        setStock(null);
        setStockError(reason instanceof Error ? reason.message : "Search failed.");
      }
    } finally {
      if (isCurrent()) setStockLoading(false);
    }
  }, [selectedUnitId, activeStockLocation]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        await refreshCsrf();
        const current = await api<{
          authenticated: boolean;
          username?: string;
          is_staff?: boolean;
          is_superuser?: boolean;
          can_manage_report_panels?: boolean;
        }>("/api/auth/me/");
        if (!alive) return;
        if (current.authenticated && current.username) {
          setUsername(current.username);
          setIsStaff(Boolean(current.is_staff));
          setIsSuperuser(Boolean(current.is_superuser));
          setCanManageReportPanels(Boolean(current.can_manage_report_panels));
          try {
            const currentOrganization = await loadOrganization();
            await loadFavorites(currentOrganization.selected_unit.id);
          } catch {
            if (alive) {
              setOrganization(null);
              invalidateStock();
            }
          }
        } else {
          setUsername(null);
        }
        setError("");
      } catch {
        if (alive) setUsername(null);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [refreshCsrf, loadOrganization, loadFavorites]);

  useEffect(() => {
    if (username && organization) {
      void loadStock(search, 1, stockPageSize.current, activeStockLocation);
    }
    // A same-BU organization refresh also invalidates stock (e.g. palette edits).
  }, [username, organization, loadStock, activeStockLocation]);

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      const token = csrfToken || await refreshCsrf();
      const result = await api<{
        username: string;
        csrfToken: string;
        is_staff: boolean;
        is_superuser: boolean;
        can_manage_report_panels: boolean;
      }>("/api/auth/login/", {
        method: "POST",
        headers: { "X-CSRFToken": token },
        body: JSON.stringify({
          username: String(form.get("username") ?? ""),
          password: String(form.get("password") ?? ""),
        }),
      });
      setCsrfToken(result.csrfToken);
      setUsername(result.username);
      setIsStaff(result.is_staff);
      setIsSuperuser(result.is_superuser);
      setCanManageReportPanels(result.can_manage_report_panels);
      const currentOrganization = await loadOrganization();
      await loadFavorites(currentOrganization.selected_unit.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sign in failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleLogout() {
    setBusy(true);
    setError("");
    favoritesRequestId.current += 1;
    try {
      await api("/api/auth/logout/", { method: "POST", headers: { "X-CSRFToken": csrfToken } });
      await refreshCsrf();
      invalidateStock();
      setUsername(null);
      setIsStaff(false);
      setIsSuperuser(false);
      setCanManageReportPanels(false);
      setOrganization(null);
      setPaletteOptions([]);
      setPaletteUnits([]);
      setPaletteUnitId("");
      setPaletteValue("");
      setPaletteError("");
      setPaletteSuccess("");
      setFavorites([]);
      setAvailableDestinations([]);
      setFavoritesError("");
      setSearch("");
      setSection("dashboard");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sign out failed.");
    } finally {
      setBusy(false);
    }
  }

  function refreshUnitContext(id?: number) {
    const revision = ++unitSwitchRevision.current;
    setUnitSwitching(true);
    // Serialize the session mutation AND its scoped refresh. Merely ignoring a
    // late response cannot undo an out-of-order server-side session selection.
    unitSwitchQueue.current = unitSwitchQueue.current.then(async () => {
      let selectionSucceeded = id === undefined;
      setError("");
      try {
        if (id !== undefined) {
          await api(`/api/units/${id}/select/`, {
            method: "POST",
            headers: { "X-CSRFToken": csrfToken },
            body: JSON.stringify({}),
          });
          selectionSucceeded = true;
        }
        invalidateStock();
        const currentOrganization = await loadOrganization();
        await loadFavorites(currentOrganization.selected_unit.id);
      } catch (reason) {
        if (selectionSucceeded) {
          // The session may have changed. Do not label old rows as its context.
          setOrganization(null);
          invalidateStock();
          favoritesRequestId.current += 1;
          setFavorites([]);
          setAvailableDestinations([]);
          setFavoritesError("");
        }
        setError(reason instanceof Error ? reason.message : "Could not switch unit.");
      } finally {
        if (revision === unitSwitchRevision.current) setUnitSwitching(false);
      }
    });
  }

  function selectUnit(event: FormEvent<HTMLSelectElement>) {
    refreshUnitContext(Number(event.currentTarget.value));
  }

  function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStockError("");
    void loadStock(search, 1, stock?.pagination.page_size ?? stockPageSize.current, activeStockLocation);
  }

  useEffect(() => {
    if (!isStaff || section !== "governance" || navigationChild !== "presentation" || !routeAvailable) {
      setPaletteOptions((current) => current.length ? [] : current);
      setPaletteUnits((current) => current.length ? [] : current);
      setPaletteUnitId((current) => current ? "" : current);
      setPaletteValue((current) => current ? "" : current);
      setPaletteContextUnitId((current) => current === null ? current : null);
      setPaletteLoading((current) => current ? false : current);
      return;
    }
    let active = true;
    setPaletteLoading(true);
    setPaletteError("");
    setPaletteSuccess("");
    setPaletteOptions([]);
    setPaletteUnits([]);
    setPaletteUnitId("");
    setPaletteValue("");
    api<{ palettes: Palette[]; units: Unit[] }>("/api/governance/business-unit-palettes/")
      .then((data) => {
        if (!active) return;
        const palettes = Array.isArray(data.palettes) ? data.palettes : [];
        const units = Array.isArray(data.units) ? data.units : [];
        setPaletteOptions(palettes);
        setPaletteUnits(units);
        const preferredUnit = String(organization?.selected_unit.id ?? units[0]?.id ?? "");
        const currentUnitId = preferredUnit;
        const chosenUnit = units.find((unit) => String(unit.id) === currentUnitId);
        setPaletteContextUnitId(organization?.selected_unit.id ?? null);
        setPaletteUnitId(currentUnitId);
        setPaletteValue(chosenUnit?.palette_value ?? chosenUnit?.theme_palette ?? palettes[0]?.value ?? "");
      })
      .catch((reason: unknown) => {
        if (active) {
          setPaletteOptions([]);
          setPaletteUnits([]);
          setPaletteUnitId("");
          setPaletteValue("");
          setPaletteContextUnitId(organization?.selected_unit.id ?? null);
          setPaletteError(reason instanceof Error ? reason.message : "Palette settings could not be loaded.");
        }
      })
      .finally(() => { if (active) setPaletteLoading(false); });
    return () => { active = false; };
  }, [isStaff, section, navigationChild, routeAvailable, organization?.selected_unit.id, paletteLoadKey]);

  useEffect(() => {
    if (isStaff) return;
    setPaletteOptions([]);
    setPaletteUnits([]);
    setPaletteUnitId("");
    setPaletteValue("");
    setPaletteContextUnitId(null);
    setPaletteLoading(false);
    setPaletteError("");
    setPaletteSuccess("");
    setEditorOpen(false);
  }, [isStaff]);

  const previewColors = useMemo(() => {
    if (!/^#[0-9a-fA-F]{6}$/.test(customAccent)) return null;
    return buildPaletteColors(customAccent);
  }, [customAccent]);

  const contrastIssues = useMemo(() => {
    return customPaletteContrastIssues(previewColors);
  }, [previewColors]);

  function openPaletteEditor(palette?: Palette) {
    setPaletteError("");
    setPaletteSuccess("");
    setEditingPaletteId(palette?.id ?? null);
    setCustomPaletteName(palette?.label ?? "");
    setCustomAccent(palette?.accent_color ?? "#B24F00");
    setEditorOpen(true);
  }

  async function refreshPaletteSettings(preferredValue?: string) {
    setPaletteLoading(true);
    setPaletteError("");
    setPaletteOptions([]);
    setPaletteUnits([]);
    setPaletteValue("");
    try {
      const data = await api<{ palettes: Palette[]; units: Unit[] }>("/api/governance/business-unit-palettes/");
      const palettes = Array.isArray(data.palettes) ? data.palettes : [];
      const units = Array.isArray(data.units) ? data.units : [];
      setPaletteOptions(palettes);
      setPaletteUnits(units);
      setPaletteContextUnitId(organization?.selected_unit.id ?? null);
      setPaletteValue(preferredValue ?? units.find((unit) => String(unit.id) === paletteUnitId)?.palette_value ?? "");
    } finally {
      setPaletteLoading(false);
    }
  }

  async function saveCustomPalette(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!previewColors || contrastIssues.length || !customPaletteName.trim() || paletteSaving) return;
    setPaletteSaving(true);
    setPaletteError("");
    setPaletteSuccess("");
    try {
      const path = editingPaletteId
        ? `/api/governance/business-unit-palettes/custom/${editingPaletteId}/`
        : "/api/governance/business-unit-palettes/";
      const result = await api<{ palette: Palette }>(path, {
        method: editingPaletteId ? "PATCH" : "POST",
        headers: { "X-CSRFToken": csrfToken },
        body: JSON.stringify({ name: customPaletteName.trim(), accent_color: customAccent.toUpperCase() }),
      });
      await refreshPaletteSettings(result.palette.value);
      if (editingPaletteId) await loadOrganization();
      setEditorOpen(false);
      setPaletteSuccess(`“${result.palette.label}” ${editingPaletteId ? "updated" : "created"}. Assign it to a unit and save to apply.`);
    } catch (reason) {
      setPaletteOptions([]);
      setPaletteUnits([]);
      setPaletteUnitId("");
      setPaletteValue("");
      setPaletteError(reason instanceof Error ? reason.message : "Custom palette could not be saved.");
    } finally {
      setPaletteSaving(false);
    }
  }

  async function savePalette(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!paletteUnitId || !paletteValue || paletteSaving) return;
    setPaletteSaving(true);
    setPaletteError("");
    setPaletteSuccess("");
    try {
      const result = await api<{ unit: Unit }>(`/api/governance/business-unit-palettes/${encodeURIComponent(paletteUnitId)}/`, {
        method: "PATCH",
        headers: { "X-CSRFToken": csrfToken },
        body: JSON.stringify({ theme_palette: paletteValue }),
      });
      const updated = result.unit;
      setPaletteUnits((units) => units.map((unit) => unit.id === updated.id ? updated : unit));
      setPaletteValue(updated.palette_value ?? updated.theme_palette);
      setOrganization((current) => current && current.selected_unit.id === updated.id
        ? { ...current, selected_unit: { ...current.selected_unit, ...updated } }
        : current);
      setPaletteSuccess(`${updated.name} palette updated.`);
    } catch (reason) {
      setPaletteOptions([]);
      setPaletteUnits([]);
      setPaletteUnitId("");
      setPaletteValue("");
      setPaletteError(reason instanceof Error ? reason.message : "Palette could not be saved.");
    } finally {
      setPaletteSaving(false);
    }
  }

  if (loading) return <div className="loading-screen" role="status">Loading prototype…</div>;

  if (!username) {
    return (
      <main className="login-page">
        <div className="login-brand">
          <div className="brand-monogram">T</div>
          <span>TIVOLI <small>ERP FOUNDATION</small></span>
        </div>
        <section className="login-card">
          <div className="eyebrow">MILESTONE 01 · PRIVATE PROTOTYPE</div>
          <h1>Sign in to your workspace</h1>
          <p className="muted">Use a synthetic demo account configured by the project owner.</p>
          {error && <div className="alert" role="alert">{error}</div>}
          <form onSubmit={handleLogin} className="login-form">
            <label htmlFor="username">Username</label>
            <input id="username" name="username" autoComplete="username" required />
            <label htmlFor="password">Password</label>
            <input id="password" name="password" type="password" autoComplete="current-password" required />
            <button className="button button-primary" type="submit" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"} <span aria-hidden="true">→</span>
            </button>
          </form>
          <div className="login-foot"><span className="status-dot" /> Synthetic data only</div>
        </section>
        <p className="login-caption">Independent custom ERP · Not for production use</p>
      </main>
    );
  }

  const selectedSection = sections.find((item) => item.id === section);
  const activeWorkspaceChild = navigationChild ?? "main";
  const activeChildLabel = navigationSections.find((item) => item.id === section)
    ?.children.find((item) => item.id === activeWorkspaceChild)?.label;
  const title = section === "dashboard" ? "Overview" : section === "my-page" ? "My Page"
    : activeChildLabel ?? selectedSection?.name ?? "Overview";
  const availableIds = new Set(availableDestinations.map((item) => item.destination));
  const favoriteIds = new Set(favorites.map((item) => item.destination));
  const favoriteControl = (destination: string, label?: string) => availableIds.has(destination) ? (
    <button
      type="button"
      className={`favorite-toggle ${favoriteIds.has(destination) ? "is-favorite" : ""}`}
      onClick={() => void toggleFavorite(destination)}
      disabled={favoriteBusy !== null}
      aria-label={`${favoriteIds.has(destination) ? "Remove" : "Add"} ${destinationName(destination, label)} ${favoriteIds.has(destination) ? "from" : "to"} favorites`}
      aria-pressed={favoriteIds.has(destination)}
      title={favoriteIds.has(destination) ? "Remove from favorites" : "Add to favorites"}
    >
      <StarMark filled={favoriteIds.has(destination)} />
    </button>
  ) : null;
  const openDestination = (destination: string) => {
    if (destination.includes(".")) {
      const [sectionId, childId] = destination.split(".");
      navigateTo(sectionId, childId);
    } else setSection(destination);
  };
  const favoriteButton = (item: FavoriteDestination) => (
    <div className="nav-row favorite-shortcut" key={item.destination}>
      <button
        className={`nav-item ${item.destination.includes(".")
          ? section === item.destination.split(".")[0] && navigationChild === item.destination.split(".")[1] ? "active" : ""
          : section === item.destination && (!navigationChild || navigationChild === "main") ? "active" : ""}`}
        onClick={() => openDestination(item.destination)}
        aria-current={item.destination.includes(".")
          ? section === item.destination.split(".")[0] && navigationChild === item.destination.split(".")[1] ? "page" : undefined
          : section === item.destination && (!navigationChild || navigationChild === "main") ? "page" : undefined}
        title={item.label}
      >
        <span className="nav-icon nav-number">{item.destination === "dashboard" ? "⌂" : sections.find((entry) => entry.id === item.destination.split(".")[0])?.icon ?? "•"}</span>
        <span>{item.label}</span>
      </button>
      {favoriteControl(item.destination)}
    </div>
  );

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="brand-monogram">T</div>
          <div><strong>TIVOLI</strong><span>ERP FOUNDATION</span></div>
        </div>
        <div className="workspace-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          <div className="nav-row">
            <button className={`nav-item ${section === "dashboard" ? "active" : ""}`} onClick={() => setSection("dashboard")} aria-current={section === "dashboard" ? "page" : undefined}>
              <span className="nav-icon">⌂</span><span>Overview</span>
            </button>
            {favoriteControl("dashboard")}
          </div>
          <div className="nav-divider" />
          <div className="nav-group-label">CORE SECTIONS</div>
          <SectionNavigation
            sections={navigationSections}
            activeSection={section}
            activeChild={navigationChild}
            navigationRevision={navigationRevision}
            renderFavorite={favoriteControl}
            onNavigate={(next, child) => navigateTo(next, child)}
          />
          <div className="nav-divider favorites-divider" />
          <div className="nav-group-label favorites-label">FAVORITES</div>
          <button className={`nav-item my-page-link ${section === "my-page" ? "active" : ""}`} onClick={() => setSection("my-page")} aria-current={section === "my-page" ? "page" : undefined}>
            <span className="nav-icon">M</span><span>My Page</span>
          </button>
          {favorites.length ? favorites.map(favoriteButton) : (
            <p className="favorites-empty">{favoritesError ? "Favorites are unavailable right now." : "Pin a destination to keep it here."}</p>
          )}
        </nav>
        <div className="sidebar-bottom">
          <span className="status-dot" />
          <div><strong>Synthetic environment</strong><span>Milestone 1 foundation</span></div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb"><span>Workspace</span><span className="crumb-slash">/</span><strong>{title}</strong></div>
          <div className="topbar-actions">
              <label className="unit-select-label">
                <span>Business unit</span>
                <select
                  aria-label="Business unit"
                  value={organization?.selected_unit.id ?? ""}
                  disabled={!organization || busy}
                  onChange={selectUnit}
                >
                  {!organization && <option value="">Business-unit context unavailable</option>}
                  {organization?.units.map((unit) => <option key={unit.id} value={unit.id}>{unit.name}</option>)}
                </select>
              </label>
            <div className="user-menu"><span className="avatar">{username.slice(0, 1).toUpperCase()}</span><span className="user-name">{username}</span></div>
            <button className="button button-quiet signout" onClick={handleLogout} disabled={busy || unitSwitching}>Sign out</button>
          </div>
        </header>

        <div className="page">
          <div className="page-heading">
            <div>
              <div className="eyebrow">TIVOLI · ERP FOUNDATION</div>
              <h1>{title}</h1>
              <p className="muted">{organization?.company.name} · {organization?.selected_unit.name}</p>
            </div>
            <div className="prototype-badge"><span className="badge-dot" /> Synthetic prototype</div>
          </div>

          {error && <div className="alert inline-alert" role="alert">{error}</div>}
          {routeError && <section className="content-card workspace-route-unavailable" role="alert" data-workspace-unavailable data-workspace-route-state="unavailable">
            <span className="eyebrow">FUNCTION UNAVAILABLE</span>
            <h2>That workspace function is unavailable</h2>
            <p>{routeError} Choose an available function from the section navigation.</p>
          </section>}
          {navigationWarning && <div className="alert inline-alert" role="alert" data-navigation-warning>{navigationWarning}</div>}
          {stockError && <div className="alert inline-alert" role="alert">{stockError}</div>}
          {favoritesError && <div className="alert inline-alert favorites-alert" role="alert">{favoritesError}</div>}
          {unitSwitching && <p className="muted" role="status">Updating business-unit context…</p>}
          {!organization && routeAvailable && section !== "procurement" && section !== "governance" && (
            <section className="content-card">
              <h2>Business-unit context unavailable</h2>
              <p>The current business-unit context could not be verified. Scoped data is not displayed until it is reloaded.</p>
              <button className="button button-outline" type="button" disabled={unitSwitching} onClick={() => refreshUnitContext()}>Reload business-unit context</button>
            </section>
          )}

          {organization && section !== "governance" && <>
          {section === "dashboard" && (
            <>
              <section className="welcome-panel">
                <div className="welcome-copy">
                  <span className="welcome-kicker">FOUNDATION · MILESTONE 1</span>
                  <h2>A clear view of the<br />business foundation.</h2>
                  <p>Organization, scoped demo access and catalog examples are ready for review. Retail quotes and delivery workflows remain synthetic and authorization-gated.</p>
                </div>
                <div className="welcome-stamp"><span>TV</span><small>ONE COMPANY<br />THREE BUSINESS UNITS</small></div>
              </section>
              <div className="section-title-row">
                <div><h2>Core sections</h2><p>Nine work areas, shown with their current implementation status.</p></div>
                <span className="small-label">01 / 09 FOUNDATION</span>
              </div>
              <div className="module-grid">
                {sections.map((item, index) => (
                  <button
                    key={item.id}
                    className="module-card"
                    onClick={() => setSection(item.id)}
                  >
                    <span className="module-index">{String(index + 1).padStart(2, "0")}</span>
                    <span className="module-name">{item.name}</span>
                    <span className="planned-tag">{item.id === "finance" ? "STATUS" : ["retail", "procurement", "inventory", "reports", "purchasing", "delivery", "governance"].includes(item.id) ? "FOUNDATION" : "PLANNED"}</span>
                    <span className="module-arrow" aria-hidden="true">↗</span>
                  </button>
                ))}
              </div>
              <div className="bottom-grid">
                <section className="content-card organization-card">
                  <div className="card-heading"><div><span className="eyebrow">ORGANIZATION</span><h2>One legal company</h2></div><span className="card-symbol">⌘</span></div>
                  <div className="company-row"><span className="company-mark">T</span><div><strong>{organization?.company.name}</strong><small>Legal entity · {organization?.company.code}</small></div></div>
                  <div className="unit-list">
                    {organization?.units.map((unit) => (
                      <div key={unit.id} className="unit-row"><span className={`unit-marker marker-${unit.code.toLowerCase()}`}>{unit.code.slice(0, 1)}</span><span>{unit.name}</span><span className="unit-code">{unit.code}</span></div>
                    ))}
                  </div>
                </section>
                <section className="content-card locations-card">
                  <div className="card-heading"><div><span className="eyebrow">PHYSICAL LOCATIONS</span><h2>Scoped location view</h2></div><span className="card-symbol">⌖</span></div>
                  <p className="card-intro">Visible locations for your selected business-unit context.</p>
                  {organization?.locations.length ? <LocationTree locations={organization.locations} /> : <p className="empty-state">No permitted locations.</p>}
                </section>
              </div>
            </>
          )}
          </>}

          {section === "governance" && routeAvailable && (
            <GovernanceWorkspace
              focusedChild={navigationChild ?? "main"}
              businessUnitName={organization?.selected_unit.name}
              businessUnitCode={organization?.selected_unit.code}
              isStaff={isStaff}
              isSuperuser={isSuperuser}
              canManageReportPanels={canManageReportPanels}
              onManageReportPanels={() => navigateTo("governance", "report-panels")}
              presentation={isStaff && navigationChild === "presentation" ? (
                <section className="content-card palette-admin">
                  <div className="card-heading">
                    <div><span className="eyebrow">PALETTE CONTROLS</span><h2>Business unit presentation</h2><p>Choose a palette for an existing unit or define a custom color set.</p></div>
                    <span className="palette-admin-mark" aria-hidden="true">Aa</span>
                  </div>
                  {paletteLoading || paletteContextUnitId !== (organization?.selected_unit.id ?? null) ? (
                    <div className="palette-skeleton" role="status" aria-label="Loading palette settings"><span /><span /><span /></div>
                  ) : paletteError && !paletteOptions.length ? (
                    <div className="palette-error" role="alert"><p>{paletteError}</p><button className="button button-outline" type="button" onClick={() => setPaletteLoadKey((key) => key + 1)}>Retry</button></div>
                  ) : (
                    <>
                      <form className="palette-form" onSubmit={savePalette}>
                        <label><span>Business unit</span>
                          <select aria-label="Business unit to configure" value={paletteUnitId} onChange={(event) => {
                            const id = event.currentTarget.value;
                            setPaletteUnitId(id);
                            const chosen = paletteUnits.find((unit) => String(unit.id) === id);
                            setPaletteValue(chosen?.palette_value ?? chosen?.theme_palette ?? "");
                            setPaletteSuccess("");
                          }} disabled={!paletteUnits.length || paletteSaving}>
                            {paletteUnits.map((unit) => <option key={unit.id} value={unit.id}>{unit.name} · {unit.code}</option>)}
                          </select>
                        </label>
                        <label><span>Presentation palette</span>
                          <select aria-label="Presentation palette" value={paletteValue} onChange={(event) => { setPaletteValue(event.currentTarget.value); setPaletteSuccess(""); }} disabled={!paletteOptions.length || paletteSaving}>
                            {paletteOptions.map((palette) => <option key={palette.value} value={palette.value}>{palette.label}</option>)}
                          </select>
                        </label>
                        <div className="palette-preview" aria-live="polite">
                          <span className="palette-swatch" data-preview-palette={paletteValue} style={paletteOptions.find((palette) => palette.value === paletteValue)?.colors as React.CSSProperties | undefined} aria-hidden="true" />
                          <span><strong>{paletteOptions.find((palette) => palette.value === paletteValue)?.label ?? "Palette preview"}</strong><small>{paletteValue.startsWith("custom:") ? "Custom color set · previewed before applying" : "Applied across this workspace’s interface"}</small></span>
                        </div>
                        {paletteError && <div className="palette-feedback palette-feedback-error" role="alert">{paletteError}</div>}
                        {paletteSuccess && <div className="palette-feedback palette-feedback-success" role="status">{paletteSuccess}</div>}
                        <div className="palette-form-foot">
                          <div className="palette-actions">
                            <button className="button button-outline" type="button" onClick={() => openPaletteEditor()} disabled={paletteSaving}>Create custom palette</button>
                            {paletteOptions.find((palette) => palette.value === paletteValue)?.kind === "custom" && <button className="button button-outline" type="button" onClick={() => openPaletteEditor(paletteOptions.find((palette) => palette.value === paletteValue))} disabled={paletteSaving}>Edit selected</button>}
                          </div>
                          <p>Presentation only. Existing grants and Favorites are unchanged.</p>
                          <button className="button button-primary palette-save" type="submit" disabled={!paletteUnitId || !paletteValue || paletteSaving}>{paletteSaving ? "Saving…" : "Save palette"}</button>
                        </div>
                      </form>
                      {editorOpen && (
                        <section className="custom-palette-editor" aria-labelledby="custom-palette-title">
                          <div className="custom-editor-heading"><div><span className="eyebrow">{editingPaletteId ? "EDIT CUSTOM PALETTE" : "NEW CUSTOM PALETTE"}</span><h3 id="custom-palette-title">{editingPaletteId ? "Refine the color set" : "Define a unit identity"}</h3></div>
                            <button className="editor-close" type="button" onClick={() => setEditorOpen(false)} aria-label="Close editor">Close</button>
                          </div>
                          <form className="custom-palette-form" onSubmit={saveCustomPalette}>
                            <label className="custom-name-field"><span>Palette name</span><input value={customPaletteName} onChange={(event) => setCustomPaletteName(event.target.value)} maxLength={80} required placeholder="e.g. Studio Collection" /></label>
                            <div className="accent-field"><label htmlFor="custom-accent-hex">Main / accent color</label><div className="accent-inputs">
                              <input className="native-color" type="color" aria-label="Choose main accent color" value={/^#[0-9a-fA-F]{6}$/.test(customAccent) ? customAccent : "#000000"} onChange={(event) => setCustomAccent(event.target.value.toUpperCase())} />
                              <input id="custom-accent-hex" className={`hex-input ${customAccent && !/^#[0-9a-fA-F]{6}$/.test(customAccent) ? "is-invalid" : ""}`} value={customAccent} onChange={(event) => setCustomAccent(event.target.value)} spellCheck={false} autoCapitalize="characters" aria-describedby="custom-palette-validation" aria-invalid={!/^#[0-9a-fA-F]{6}$/.test(customAccent)} placeholder="#B24F00" />
                            </div></div>
                            <div className="custom-live-preview" style={previewColors as React.CSSProperties | undefined} aria-live="polite">
                              <div className="custom-preview-sidebar"><span className="preview-mini-mark">T</span><span>UNIT PANEL</span><b>Presentation</b><i>Identity preview</i></div>
                              <div className="custom-preview-content"><div className="custom-preview-label">LIVE PREVIEW</div><h4>{customPaletteName.trim() || "Your palette name"}</h4><p>Generated tones derived from the selected accent.</p><button type="button" tabIndex={-1}>Sample action</button>
                                <div className="preview-swatches" aria-label="Generated theme colors">{previewColors && Object.entries(previewColors).slice(0, 5).map(([property, value]) => <span key={property} title={`${property}: ${value}`} style={{ backgroundColor: value }} />)}</div>
                              </div>
                            </div>
                            <div className={`palette-validation ${contrastIssues.length ? "has-issues" : "is-valid"}`} id="custom-palette-validation" role={contrastIssues.length ? "alert" : "status"}>
                              {contrastIssues.length ? <><strong>Not ready to save</strong><ul>{contrastIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul></> : <><strong>Contrast checks passed</strong><span> All six foreground and focus checks meet their minimum ratios.</span></>}
                            </div>
                            <div className="custom-editor-actions"><button className="button button-outline" type="button" onClick={() => setEditorOpen(false)}>Cancel</button><button className="button button-primary" type="submit" disabled={!customPaletteName.trim() || contrastIssues.length > 0 || paletteSaving}>{paletteSaving ? "Saving…" : editingPaletteId ? "Save changes" : "Create palette"}</button></div>
                          </form>
                        </section>
                      )}
                    </>
                  )}
                </section>
              ) : undefined}
              reportPanelControls={canManageReportPanels && navigationChild === "report-panels" ? <GovernanceReportPanelManager csrfToken={csrfToken} /> : undefined}
              purchasingReview={isStaff && navigationChild === "purchasing-config" ? <PurchasingGovernanceReview selectedUnitId={organization?.selected_unit.id ?? null} selectedUnitName={organization?.selected_unit.name ?? ""} isStaff={isStaff} /> : undefined}
              capacityAdministration={isSuperuser && navigationChild === "capacity-admin" ? <WarehouseCapacity isSuperuser={isSuperuser} csrfToken={csrfToken} administrationOnly /> : undefined}
              capacityAudit={isSuperuser && navigationChild === "capacity-audit" ? <WarehouseCapacityAuditRegister isSuperuser={isSuperuser} /> : undefined}
              transferPermissions={isSuperuser && navigationChild === "transfer-permissions" ? <TransferPermissions key={organization?.selected_unit.id ?? 0} selectedUnitId={organization?.selected_unit.id ?? null} csrfToken={csrfToken} /> : undefined}
            />
          )}

          {section === "inventory" && routeAvailable && (
            <div className="inventory-workspace" data-workspace-view={`inventory/${activeWorkspaceChild}`}>
            {organization ? (
            <section className="content-card inventory-view">
              <div className="card-heading inventory-heading" hidden={activeWorkspaceChild !== "main"}>
                <div><span className="eyebrow">SYNTHETIC LEDGER · READ ONLY</span><h2>Inventory quantities</h2><p>Unreserved quantity = on hand minus reserved. Test-only ledger, not available-to-promise. Illustrative examples are not included.</p></div>
                <span className="example-count">{stock?.pagination.total_count ?? 0}<small>STOCK BUCKETS</small></span>
              </div>
              <form className="search-form" hidden={activeWorkspaceChild !== "main"} onSubmit={handleSearch} aria-busy={stockLoading}>
                <label className="search-box">
                  <span aria-hidden="true">⌕</span>
                  <input aria-label="Search inventory items" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search item, variant, brand, location code or name" />
                </label>
                <button className="button button-dark" type="submit" disabled={stockLoading}>{stockLoading ? "Loading…" : "Search"}</button>
              </form>
              <div className="table-scroll" hidden={activeWorkspaceChild !== "main"}>
                <table>
                  <thead><tr><th>ITEM</th><th>BRAND</th><th>VARIANT</th><th>LOCATION</th><th>BUSINESS UNIT</th><th className="quantity-col">Unreserved quantity</th></tr></thead>
                  <tbody>
                    {stock?.results.map((row) => (
                    <tr key={row.id}>
                        <td><strong>{row.item_code}</strong><button type="button" className="inventory-item-link" onClick={() => setSelectedStockBucket({ row, unitId: organization.selected_unit.id, locationId: activeStockLocation })} aria-label={`View stock bucket details for ${row.item_code}`}>View bucket</button><small>{row.item_description}</small></td>
                        <td><span className="brand-cell">{row.brand_name}</span></td>
                        <td><strong className="variant-code">{row.variant_sku}</strong><small>{row.variant_description}</small></td>
                        <td><strong>{row.location_code}</strong><small>{row.location_name}</small></td>
                        <td><span className="unit-pill">{row.unit_code}</span>{row.unit_name}</td>
                        <td className="quantity-col">{row.quantity}</td>
                      </tr>
                    ))}
                    {!stockLoading && !stock?.results.length && <tr><td colSpan={6} className="empty-table">No authorized synthetic ledger stock matches this search.</td></tr>}
                    {stockLoading && <tr><td colSpan={6} className="empty-table" role="status">Loading authorized inventory…</td></tr>}
                  </tbody>
                </table>
              </div>
              <div className="inventory-scope-controls" hidden={activeWorkspaceChild !== "main"}>
                <label>
                  <span>Receiving / stock location</span>
                  <select aria-label="Inventory location filter" value={activeStockLocation} onChange={(event) => {
                    const locationId = event.target.value;
                    stockRequestId.current += 1;
                    setStock(null);
                    setStockLoading(true);
                    setStockLocationFilter({ unitId: selectedUnitId ?? -1, locationId });
                    setSelectedStockBucket(null);
                    setStockError("");
                  }}>
                    <option value="">All permitted locations</option>
                    {organization.locations.map((location) => <option key={location.id} value={location.id}>{location.code} · {location.name}</option>)}
                    {activeStockLocation && !organization.locations.some((location) => String(location.id) === activeStockLocation) &&
                      <option value={activeStockLocation}>Previously selected location · access recheck required</option>}
                  </select>
                </label>
              </div>
              {activeWorkspaceChild === "main" && selectedStockBucket?.unitId === organization.selected_unit.id && selectedStockBucket.locationId === activeStockLocation && <InventoryStockDetail
                bucketId={selectedStockBucket.row.id}
                unitId={organization.selected_unit.id}
                locationId={activeStockLocation}
                onClose={() => setSelectedStockBucket(null)}
              />}
              <div className="inventory-pagination" hidden={activeWorkspaceChild !== "main"}>
                <label>
                  <span>Rows per page</span>
                  <select
                    aria-label="Inventory rows per page"
                    value={stock?.pagination.page_size ?? stockPageSize.current}
                    onChange={(event) => {
                      setStockError("");
                      void loadStock(search, 1, Number(event.target.value), activeStockLocation);
                    }}
                  >
                    {[25, 50, 100].map((size) => <option key={size} value={size}>{size}</option>)}
                  </select>
                </label>
                <nav className="pagination-nav" aria-label="Inventory pagination">
                  <button
                    className="button button-outline"
                    type="button"
                    onClick={() => void loadStock(search, stock?.pagination.previous_page ?? 1, stock?.pagination.page_size ?? 50)}
                    disabled={stockLoading || stock?.pagination.previous_page == null}
                    aria-label="Previous inventory page"
                  >
                    Previous
                  </button>
                    <span role="status" aria-live="polite">
                    {stock?.pagination
                      ? `Page ${stock.pagination.page} of ${stock.pagination.total_pages} · ${
                          stock.pagination.total_count
                            ? `showing ${(stock.pagination.page - 1) * stock.pagination.page_size + 1}–${Math.min(stock.pagination.page * stock.pagination.page_size, stock.pagination.total_count)} of ${stock.pagination.total_count} rows`
                            : "0 rows"
                        }`
                      : "Inventory page not loaded"}
                  </span>
                  <button
                    className="button button-outline"
                    type="button"
                    onClick={() => void loadStock(search, stock?.pagination.next_page ?? stock?.pagination.page ?? 1, stock?.pagination.page_size ?? 50)}
                    disabled={stockLoading || stock?.pagination.next_page == null}
                    aria-label="Next inventory page"
                  >
                    Next
                  </button>
                </nav>
              </div>
              <div className="table-note" hidden={activeWorkspaceChild !== "main"}><span className="status-dot" /> All displayed records are synthetic and read-only</div>
              <section className="inventory-locations-area" hidden={activeWorkspaceChild !== "locations"} aria-labelledby="inventory-locations-title">
                <div className="card-heading"><div><span className="eyebrow">CURRENT BUSINESS UNIT</span><h2 id="inventory-locations-title">Permitted locations</h2><p>Location scope is explicit. A location filter is never widened automatically if its access changes.</p></div></div>
                {organization.locations.length
                  ? <LocationTree locations={organization.locations} />
                  : <p className="empty-state">No permitted locations are available in this business unit.</p>}
              </section>
              <details className="inventory-receiving-area" hidden={!["receipt", "commitments", "progress", "packing"].includes(activeWorkspaceChild)} open={receivingOpened} onToggle={(event) => setReceivingOpened(event.currentTarget.open)}>
                <summary className="inventory-area-summary"><span className="eyebrow">INVENTORY FUNCTION</span><strong>{navigationSections.find((item) => item.id === "inventory")?.children.find((item) => item.id === activeWorkspaceChild)?.label}</strong></summary>
                {receivingOpened && <PurchasingWorkbench
                  mode="warehouse"
                  selectedUnitId={organization.selected_unit.id}
                  selectedUnitName={organization.selected_unit.name}
                  isStaff={isStaff}
                  username={username ?? ""}
                  csrfToken={csrfToken}
                  locations={organization.locations}
                  focusedView={activeWorkspaceChild}
                  onOpenReports={() => setSection("reports")}
                  onOpenProcurementReceipt={() => navigateTo("procurement", "receipt")}
                  onOpenProcurementPacking={() => navigateTo("procurement", "packing")}
                  onReceiptSuccess={() => {
                    const currentUnitId = organization.selected_unit.id;
                    if (stockUnitId.current === currentUnitId) {
                      setSelectedStockBucket(null);
                      void loadStock(search, 1, stock?.pagination.page_size ?? stockPageSize.current, activeStockLocation);
                    }
                  }}
                />}
              </details>
              <details className="inventory-registers-disclosure" hidden={activeWorkspaceChild !== "registers"} open={registersOpened} onToggle={(event) => setRegistersOpened(event.currentTarget.open)}>
                <summary className="inventory-area-summary"><span className="eyebrow">ASSIGNED REPORT SCOPE</span><strong>Inventory &amp; transfer registers</strong></summary>
                {registersOpened && <InventoryRegisters selectedUnitId={organization.selected_unit.id} />}
              </details>
            </section>
            ) : (
              <section className="content-card inventory-context-unavailable">
                <span className="eyebrow">BUSINESS-UNIT DATA UNAVAILABLE</span>
                <h2>Inventory quantities are unavailable</h2>
                <p>No authorized business-unit context is available. Stock, receiving and report data remain unavailable; warehouse capacity is checked independently.</p>
              </section>
            )}
            {capacityOpened && <div hidden={activeWorkspaceChild !== "capacity"} data-workspace-function="inventory/capacity">
              <WarehouseCapacity isSuperuser={isSuperuser} csrfToken={csrfToken} />
            </div>}
            {transfersOpened && organization && <div hidden={activeWorkspaceChild !== "transfers"} data-workspace-function="inventory/transfers">
              <StockTransferRequests key={organization.selected_unit.id} selectedUnitId={organization.selected_unit.id} csrfToken={csrfToken} />
            </div>}
            </div>
          )}

          {organization && <>
          {section === "purchasing" && routeAvailable && organization && (
            <PurchasingWorkbench
              focusedView={activeWorkspaceChild}
              selectedUnitId={organization.selected_unit.id}
              selectedUnitName={organization.selected_unit.name}
              isStaff={isStaff}
              username={username ?? ""}
              csrfToken={csrfToken}
              locations={organization.locations}
              onOpenReports={() => setSection("reports")}
              onNavigateFunction={(child) => navigateTo("purchasing", child)}
              onOpenInventoryReceipt={() => navigateTo("inventory", "receipt")}
              onOpenInventoryPacking={() => navigateTo("inventory", "packing")}
            />
          )}
          {section === "procurement" && routeAvailable && activeWorkspaceChild === "kyc" && <div data-workspace-view="procurement/kyc"><SupplierKyc csrfToken={csrfToken} /></div>}
          {section === "procurement" && routeAvailable && organization && activeWorkspaceChild !== "kyc" && (
            <PurchasingWorkbench
              mode="procurement"
              focusedView={activeWorkspaceChild}
              selectedUnitId={organization.selected_unit.id}
              selectedUnitName={organization.selected_unit.name}
              isStaff={isStaff}
              username={username ?? ""}
              csrfToken={csrfToken}
              locations={organization.locations}
              onOpenReports={() => setSection("reports")}
              onNavigateFunction={(child) => navigateTo("procurement", child)}
            />
          )}
          {section === "retail" && routeAvailable && organization && (
            <PurchasingWorkbench
              mode="retail"
              focusedView={activeWorkspaceChild}
              selectedUnitId={organization.selected_unit.id}
              selectedUnitName={organization.selected_unit.name}
              isStaff={isStaff}
              username={username ?? ""}
              csrfToken={csrfToken}
              locations={organization.locations}
              onOpenReports={() => setSection("reports")}
              onOpenDelivery={() => navigateTo("delivery", "quote")}
              onNavigateFunction={(child) => navigateTo("retail", child)}
            />
          )}
          {section === "delivery" && routeAvailable && organization && (
            <PurchasingWorkbench
              mode="delivery"
              focusedView={activeWorkspaceChild}
              selectedUnitId={organization.selected_unit.id}
              selectedUnitName={organization.selected_unit.name}
              isStaff={isStaff}
              username={username ?? ""}
              csrfToken={csrfToken}
              locations={organization.locations}
              onOpenReports={() => setSection("reports")}
              onNavigateFunction={(child) => navigateTo("delivery", child)}
              onOpenRetailQuote={() => navigateTo("retail", "quote")}
            />
          )}

          {section === "my-page" && (
            <section className="my-page-view">
              <div className="my-page-intro">
                <span className="eyebrow">YOUR WORKSPACE</span>
                <h2>Everything you’ve pinned, in one place.</h2>
                <p>Favorites reflect destinations available to you in {organization?.selected_unit.name}.</p>
              </div>
              {favorites.length ? (
                <div className="my-favorites-list">
                  {favorites.map((item, index) => (
                    <div className="my-favorite-row" key={item.destination}>
                      <span className="my-favorite-index">{String(index + 1).padStart(2, "0")}</span>
                        <button className="my-favorite-open" onClick={() => openDestination(item.destination)}>
                        <span className="my-favorite-mark">{item.destination === "dashboard" ? "⌂" : sections.find((entry) => entry.id === item.destination.split(".")[0])?.icon ?? "•"}</span>
                        <span><strong>{item.label}</strong><small>{item.destination === "inventory" || item.destination === "inventory.main"
                          ? "Current inventory ledger snapshot · read only"
                          : item.destination.includes(".") ? `Function · ${item.destination}` : item.destination === "dashboard" ? "Company foundation" : "Section default"}</small></span>
                      </button>
                      {favoriteControl(item.destination)}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="my-page-empty">
                  <span className="empty-star"><StarMark filled={false} /></span>
                  <h3>No favorites yet</h3>
                  <p>Use the star beside an available destination to add it to your personal page.</p>
                  <button className="button button-outline" onClick={() => setSection("dashboard")}>Browse destinations</button>
                </div>
              )}
            </section>
          )}

          {section === "reports" && organization && (
            <div data-workspace-view={`reports/${activeWorkspaceChild}`}>
              {isSuperuser && activeWorkspaceChild === "audit" ? <AuditLog /> : isStaff && activeWorkspaceChild === "reconciliation"
                ? <ScenarioReconciliation selectedUnitId={organization.selected_unit.id} selectedUnitName={organization.selected_unit.name} />
                : <ReportsArea
                  csrfToken={csrfToken}
                  selectedUnitId={organization.selected_unit.id}
                  canManagePanels={canManageReportPanels}
                  onManagePanels={() => navigateTo("governance", "report-panels")}
                />}
            </div>
          )}

          {section === "procurement" && routeAvailable && !organization && activeWorkspaceChild !== "kyc" && (
            <section className="content-card inventory-context-unavailable">
              <span className="eyebrow">BUSINESS-UNIT DATA UNAVAILABLE</span>
              <h2>Procurement workspace unavailable</h2>
              <p>Commitments, receipts, supplier references and their scoped registers remain unavailable until the current business-unit context is verified.</p>
            </section>
          )}

          {section === "finance" && <FinanceStatusPage canManageReportPanels={canManageReportPanels} onNavigate={navigateTo} />}
          {section !== "dashboard" && section !== "finance" && section !== "inventory" && section !== "purchasing" && section !== "procurement" && section !== "retail" && section !== "delivery" && section !== "my-page" && section !== "reports" && section !== "governance" && selectedSection && (
            <section className="planned-panel" data-workspace-view={`${section}/${activeWorkspaceChild}`}>
              <div className="planned-icon">{selectedSection.icon}</div>
              <div className="eyebrow">PLANNED · NOT IMPLEMENTED</div>
              <h2>{selectedSection.name}</h2>
              <p>This section is visible as a navigation boundary only. No transactions, records, success actions, or analytics are implemented here.</p>
              <button className="button button-outline" onClick={() => setSection("dashboard")}>Back to overview</button>
            </section>
          )}
          </>}
          <footer className="page-footer"><span>TIVOLI ERP · MILESTONE 1</span><span>Prototype only — no production records or transactions</span></footer>
        </div>
      </main>
    </div>
  );
}
