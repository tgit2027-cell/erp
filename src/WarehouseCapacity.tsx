import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

type Capacity = {
  location: { id: number; code: string; name: string };
  capability: "view_capacity" | "edit_capacity";
  configured: boolean;
  total_floor_area_m2: string | null;
  allocated_storage_floor_area_m2: string | null;
  received_goods_volume_m3: string | null;
  available_floor_area_m2: string | null;
  updated_at: string | null;
};
type AdminLocation = {
  id: number;
  code: string;
  name: string;
  physical_capacity_configured: boolean;
  business_units: { id: number; code: string; name: string }[];
  assignments: {
    id: number;
    user_id: number;
    username: string;
    capability: "view_capacity" | "edit_capacity";
  }[];
};
type AdminConfiguration = {
  locations: AdminLocation[];
  synthetic_users: { id: number; username: string }[];
};

class CapacityRequestError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...init.headers },
  });
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string"
      ? data.detail
      : Object.values(data).flat().join(" ") || "The capacity request could not be completed.";
    throw new CapacityRequestError(String(detail), response.status);
  }
  return data as T;
}

function displayValue(value: string | null) {
  return value === null ? "Not configured" : value;
}

function subtractDecimalStrings(total: string, allocated: string): string | null {
  const scaled = (value: string) => {
    if (!/^\d+(?:\.\d{1,6})?$/.test(value)) return null;
    const [whole, fraction = ""] = value.split(".");
    return BigInt(whole) * 1_000_000n + BigInt(fraction.padEnd(6, "0"));
  };
  const totalScaled = scaled(total);
  const allocatedScaled = scaled(allocated);
  if (totalScaled === null || allocatedScaled === null || allocatedScaled > totalScaled) return null;
  const difference = totalScaled - allocatedScaled;
  const whole = difference / 1_000_000n;
  const fraction = String(difference % 1_000_000n).padStart(6, "0");
  return `${whole}.${fraction}`;
}

function CapacityCard({
  record,
  csrfToken,
  onRefresh,
  refreshing,
}: {
  record: Capacity;
  csrfToken: string;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const [values, setValues] = useState({
    total_floor_area_m2: record.total_floor_area_m2 ?? "",
    allocated_storage_floor_area_m2: record.allocated_storage_floor_area_m2 ?? "",
    received_goods_volume_m3: record.received_goods_volume_m3 ?? "",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [editUnverified, setEditUnverified] = useState(false);
  const draftVersion = useRef(0);
  const dirty = useRef(false);
  const active = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  // Only an authoritative fresh record, never a transient failure, can
  // reinstate the edit capability after a denied write.
  useEffect(() => { setEditUnverified(false); }, [record]);
  const canEdit = record.capability === "edit_capacity";
  const availableArea = subtractDecimalStrings(
    values.total_floor_area_m2,
    values.allocated_storage_floor_area_m2,
  );

  useEffect(() => {
    if (dirty.current) return;
    setValues({
      total_floor_area_m2: record.total_floor_area_m2 ?? "",
      allocated_storage_floor_area_m2: record.allocated_storage_floor_area_m2 ?? "",
      received_goods_volume_m3: record.received_goods_volume_m3 ?? "",
    });
  }, [
    record.total_floor_area_m2,
    record.allocated_storage_floor_area_m2,
    record.received_goods_volume_m3,
  ]);

  function changeValue(field: keyof typeof values, value: string) {
    dirty.current = true;
    draftVersion.current += 1;
    setSuccess("");
    setValues((current) => ({ ...current, [field]: value }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canEdit || saving || refreshing || editUnverified) return;
    setSaving(true);
    setError("");
    setSuccess("");
    const submittedVersion = draftVersion.current;
    try {
      await request(`/api/warehouse-capacity/${record.location.id}/`, {
        method: "PUT",
        headers: { "X-CSRFToken": csrfToken },
        body: JSON.stringify(values),
      });
      if (!active.current) return;
      const newerDraft = submittedVersion !== draftVersion.current;
      if (!newerDraft) dirty.current = false;
      setSuccess(newerDraft
        ? "Submitted measurements were saved and audited; newer edits remain unsaved."
        : "Capacity measurements saved and audited.");
      onRefresh();
    } catch (reason) {
      if (!active.current) return;
      setError(reason instanceof Error ? reason.message : "Capacity measurements could not be saved.");
      if (reason instanceof CapacityRequestError && (reason.status === 403 || reason.status === 404)) {
        setEditUnverified(true);
        onRefresh();
      }
    } finally {
      if (active.current) setSaving(false);
    }
  }

  return (
    <article className="capacity-location-card" aria-label={`Capacity for ${record.location.name}`}>
      <div className="capacity-location-heading">
        <div>
          <span className="eyebrow">{record.location.code}</span>
          <h3>{record.location.name}</h3>
        </div>
        <span className="capacity-access-tag">
          {canEdit ? "Assigned: view and edit" : "Assigned: view only"}
        </span>
      </div>
      {!record.configured && <p className="capacity-empty" role="status">No capacity measurements have been configured for this physical location.</p>}
      <p className="capacity-unit-note">m² floor area and m³ received-goods volume are separate measures. No conversion or volumetric capacity is inferred.</p>
      {!canEdit && error && <div className="alert" role="alert">{error}</div>}
      {canEdit ? (
        <form className="capacity-form" onSubmit={submit}>
          <label>
            Total floor area (m²)
            <input
              aria-label={`Total floor area in m² for ${record.location.name}`}
              type="number"
              min="0"
              step="0.000001"
              required
              value={values.total_floor_area_m2}
              onChange={(event) => changeValue("total_floor_area_m2", event.target.value)}
            />
          </label>
          <label>
            Allocated storage floor area (m²)
            <input
              aria-label={`Allocated storage floor area in m² for ${record.location.name}`}
              type="number"
              min="0"
              step="0.000001"
              required
              value={values.allocated_storage_floor_area_m2}
              onChange={(event) => changeValue("allocated_storage_floor_area_m2", event.target.value)}
            />
          </label>
          <label>
            Received-goods volume (m³)
            <input
              aria-label={`Received-goods volume in m³ for ${record.location.name}`}
              type="number"
              min="0"
              step="0.000001"
              required
              value={values.received_goods_volume_m3}
              onChange={(event) => changeValue("received_goods_volume_m3", event.target.value)}
            />
          </label>
          <div className="capacity-derived">
            <span>Available floor area</span>
            <strong>
              {availableArea ?? "—"}{availableArea !== null ? " m²" : ""}
            </strong>
          </div>
          {error && <div className="alert" role="alert">{error}</div>}
          {success && <div className="capacity-success" role="status">{success}</div>}
          {editUnverified && <p role="status">Editing is unavailable until capacity access is rechecked.</p>}
          <button className="button button-dark" type="submit" disabled={saving || refreshing || editUnverified}>
            {saving ? "Saving…" : refreshing ? "Checking access…" : "Save capacity"}
          </button>
        </form>
      ) : record.configured ? (
        <dl className="capacity-readings">
          <div><dt>Total floor area</dt><dd>{displayValue(record.total_floor_area_m2)} m²</dd></div>
          <div><dt>Allocated storage floor area</dt><dd>{displayValue(record.allocated_storage_floor_area_m2)} m²</dd></div>
          <div><dt>Available floor area</dt><dd>{displayValue(record.available_floor_area_m2)} m²</dd></div>
          <div><dt>Received-goods volume</dt><dd>{displayValue(record.received_goods_volume_m3)} m³</dd></div>
        </dl>
      ) : null}
    </article>
  );
}

export default function WarehouseCapacity({
  isSuperuser,
  csrfToken,
  administrationOnly = false,
}: {
  isSuperuser: boolean;
  csrfToken: string;
  administrationOnly?: boolean;
}) {
  const [records, setRecords] = useState<Capacity[]>([]);
  const [admin, setAdmin] = useState<AdminConfiguration | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [adminError, setAdminError] = useState("");
  const [locationToConfigure, setLocationToConfigure] = useState("");
  const [assignmentLocation, setAssignmentLocation] = useState("");
  const [assignmentUser, setAssignmentUser] = useState("");
  const [assignmentCapability, setAssignmentCapability] = useState<"view_capacity" | "edit_capacity">("view_capacity");
  const adminRequestId = useRef(0);
  const loadRequestId = useRef(0);
  const active = useRef(false);
  const mode = useRef(administrationOnly);
  mode.current = administrationOnly;
  const [recordsMode, setRecordsMode] = useState(administrationOnly);
  if (recordsMode !== administrationOnly) {
    // Invalidate before the first committed render in another presentation.
    setRecordsMode(administrationOnly);
    loadRequestId.current += 1;
    setRecords([]);
    setError("");
    setLoading(!administrationOnly);
  }

  const load = useCallback(async () => {
    if (!active.current || mode.current) return;
    const version = ++loadRequestId.current;
    const isCurrent = () => active.current && !mode.current && version === loadRequestId.current;
    setLoading(true);
    setError("");
    try {
      const result = await request<{ results: Capacity[] }>("/api/warehouse-capacity/");
      if (!isCurrent()) return;
      setRecords(Array.isArray(result.results) ? result.results : []);
    } catch (reason) {
      if (!isCurrent()) return;
      const message = reason instanceof Error ? reason.message : "Warehouse capacity is unavailable.";
      if (reason instanceof CapacityRequestError && (reason.status === 403 || reason.status === 404)) {
        setRecords([]);
        setError(message);
      } else {
        setError(`${message} Previously loaded measurements, if any, are retained; refresh capacity access to retry.`);
      }
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, []);

  const loadAdmin = useCallback(async () => {
    const requestId = ++adminRequestId.current;
    if (!isSuperuser) {
      setAdmin(null);
      setAdminError("");
      return;
    }
    setAdmin(null);
    setAdminError("");
    try {
      const result = await request<AdminConfiguration>("/api/governance/warehouse-capacity/");
      if (requestId !== adminRequestId.current) return;
      setAdmin(result);
      if (!administrationOnly) {
        const eligible = result.locations.filter((location) => location.physical_capacity_configured);
        setAssignmentLocation((current) => eligible.some((location) => String(location.id) === current)
          ? current
          : String(eligible[0]?.id ?? ""));
        setAssignmentUser((current) => result.synthetic_users.some((user) => String(user.id) === current)
          ? current
          : String(result.synthetic_users[0]?.id ?? ""));
      }
    } catch (reason) {
      if (requestId === adminRequestId.current) {
        setAdmin(null);
        setAdminError(reason instanceof Error ? reason.message : "Capacity administration is unavailable.");
      }
    }
  }, [isSuperuser, administrationOnly]);

  useEffect(() => {
    active.current = true;
    if (!administrationOnly) void load();
    void loadAdmin();
    return () => {
      active.current = false;
      loadRequestId.current += 1;
      adminRequestId.current += 1;
    };
  }, [administrationOnly, load, loadAdmin]);

  async function configurePhysicalLocation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!locationToConfigure || saving) return;
    setSaving(true);
    setAdminError("");
    try {
      await request("/api/governance/warehouse-capacity/", {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken },
        body: JSON.stringify({ location_id: Number(locationToConfigure) }),
      });
      setLocationToConfigure("");
      await loadAdmin();
      if (!administrationOnly) await load();
    } catch (reason) {
      setAdminError(reason instanceof Error ? reason.message : "Physical location could not be configured.");
    } finally {
      setSaving(false);
    }
  }

  async function assignPermission(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!assignmentLocation || !assignmentUser || saving) return;
    setSaving(true);
    setAdminError("");
    try {
      await request("/api/governance/warehouse-capacity/assignments/", {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken },
        body: JSON.stringify({
          location_id: Number(assignmentLocation),
          user_id: Number(assignmentUser),
          capability: assignmentCapability,
        }),
      });
      await loadAdmin();
      if (!administrationOnly) await load();
    } catch (reason) {
      setAdminError(reason instanceof Error ? reason.message : "Capacity permission could not be assigned.");
    } finally {
      setSaving(false);
    }
  }

  async function revokePermission(assignmentId: number) {
    if (saving) return;
    setSaving(true);
    setAdminError("");
    try {
      await request(`/api/governance/warehouse-capacity/assignments/${assignmentId}/`, {
        method: "DELETE",
        headers: { "X-CSRFToken": csrfToken },
      });
      await loadAdmin();
      if (!administrationOnly) await load();
    } catch (reason) {
      setAdminError(reason instanceof Error ? reason.message : "Capacity permission could not be revoked.");
    } finally {
      setSaving(false);
    }
  }

  const unconfiguredLocations = admin?.locations.filter((location) => !location.physical_capacity_configured) ?? [];
  const configuredLocations = admin?.locations.filter((location) => location.physical_capacity_configured) ?? [];

  return (
    <section className="capacity-section" aria-labelledby="capacity-title">
      <div className="capacity-section-heading">
        <div>
          <span className="eyebrow">LOCATION-SCOPED · SEPARATE PERMISSION</span>
          <h2 id="capacity-title">Warehouse capacity</h2>
          <p>Capacity values are separate from stock-reading access. Only explicitly assigned accounts can view or edit each physical location.</p>
        </div>
        {!administrationOnly && <button className="button button-outline" type="button"
          disabled={loading} onClick={() => void load()}>Refresh capacity access</button>}
      </div>
      {!administrationOnly && loading && <p role="status">Loading assigned warehouse capacities…</p>}
      {!administrationOnly && error && <div className="alert" role="alert">{error}</div>}
      {!administrationOnly && !loading && !error && records.length === 0 && (
        <p className="capacity-empty" role="status">No warehouse capacity locations are assigned to your account. An ERP/system administrator must assign a location explicitly.</p>
      )}
      {!administrationOnly && records.length > 0 && (
        <div className="capacity-location-list">
          {records.map((record) => (
            <CapacityCard key={record.location.id} record={record} csrfToken={csrfToken}
              refreshing={loading} onRefresh={() => void load()} />
          ))}
        </div>
      )}

      {isSuperuser && (
        <div className="capacity-admin" aria-labelledby="capacity-admin-title">
          <div className="capacity-admin-title">
            <h3 id="capacity-admin-title">ERP/system administrator · physical locations and assignments</h3>
            <p>Creating a profile explicitly marks the selected synthetic location as capacity-managed. This does not create a user permission.</p>
          </div>
          {adminError && <div className="alert" role="alert">{adminError}</div>}
          {adminError && <button type="button" className="button button-outline" onClick={() => void loadAdmin()}>Retry capacity administration</button>}
          {!admin && !adminError && <div className="capacity-empty" role="status">Loading authorized physical-location administration…</div>}
          {admin && <>
            <form className="capacity-admin-form" onSubmit={configurePhysicalLocation}>
              <label>
                Configure physical location
                <select
                  aria-label="Configure physical capacity location"
                  value={locationToConfigure}
                  onChange={(event) => setLocationToConfigure(event.target.value)}
                >
                  <option value="">Choose a synthetic location</option>
                  {unconfiguredLocations.map((location) => (
                    <option key={location.id} value={location.id}>{location.code} · {location.name}</option>
                  ))}
                </select>
              </label>
              <button className="button button-outline" type="submit" disabled={saving || !locationToConfigure}>
                {saving ? "Saving…" : "Configure location"}
              </button>
            </form>
            <form className="capacity-admin-form" onSubmit={assignPermission}>
              <label>
                Physical location
                  <select aria-label="Assignment physical location" value={assignmentLocation} onChange={(event) => setAssignmentLocation(event.target.value)}>
                    <option value="">Choose configured location</option>
                  {configuredLocations.map((location) => <option key={location.id} value={location.id}>{location.code} · {location.name}</option>)}
                </select>
              </label>
              <label>
                Synthetic account
                  <select aria-label="Capacity assignment synthetic account" value={assignmentUser} onChange={(event) => setAssignmentUser(event.target.value)}>
                    <option value="">Choose existing account</option>
                  {admin.synthetic_users.map((user) => <option key={user.id} value={user.id}>{user.username}</option>)}
                </select>
              </label>
              <label>
                Permission
                <select aria-label="Capacity permission" value={assignmentCapability} onChange={(event) => setAssignmentCapability(event.target.value as "view_capacity" | "edit_capacity")}>
                  <option value="view_capacity">View capacity only</option>
                  <option value="edit_capacity">View and edit capacity</option>
                </select>
              </label>
              <button className="button button-dark" type="submit" disabled={saving || !assignmentLocation || !assignmentUser}>
                {saving ? "Saving…" : "Assign capacity permission"}
              </button>
            </form>
            {configuredLocations.map((location) => (
              <div className="capacity-assignment-group" key={location.id}>
                <strong>{location.code} · {location.name}</strong>
                {location.assignments.length ? location.assignments.map((assignment) => (
                  <div className="capacity-assignment-row" key={assignment.id}>
                    <span>{assignment.username} — {assignment.capability === "edit_capacity" ? "view and edit" : "view only"}</span>
                    <button type="button" className="button button-quiet" disabled={saving} onClick={() => void revokePermission(assignment.id)}>Revoke</button>
                  </div>
                )) : <p className="capacity-empty">No active capacity assignments.</p>}
              </div>
            ))}
          </>}
        </div>
      )}
    </section>
  );
}
