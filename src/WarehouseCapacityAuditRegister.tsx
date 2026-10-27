import { useEffect, useRef, useState } from "react";

type AuditEvent = {
  id: number;
  kind: string;
  label: string;
  location: { id: number; code: string; name: string };
  actor: string;
  target_user: string | null;
  capability: string | null;
  created_at: string;
};

type AuditPage = {
  results: AuditEvent[];
  pagination: {
    page: number;
    page_size: number;
    total_count: number;
    total_pages: number;
    previous_page: number | null;
    next_page: number | null;
  };
};

function responseMessage(data: Record<string, unknown>) {
  if (typeof data.detail === "string") return data.detail;
  const messages = Object.values(data).flat().filter((value) => typeof value === "string");
  return messages.join(" ") || "The capacity audit register could not be loaded.";
}

export default function WarehouseCapacityAuditRegister({
  isSuperuser,
}: {
  isSuperuser: boolean;
}) {
  const [page, setPage] = useState(1);
  const [reloadKey, setReloadKey] = useState(0);
  const [data, setData] = useState<AuditPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requestVersion = useRef(0);
  const refresh = () => {
    setData(null);
    setError("");
    setLoading(true);
    setReloadKey((current) => current + 1);
  };

  useEffect(() => {
    if (!isSuperuser) {
      setData(null);
      setLoading(false);
      setError("");
      return;
    }

    const controller = new AbortController();
    const version = ++requestVersion.current;
    setData(null);
    setError("");
    setLoading(true);

    void fetch(`/api/governance/warehouse-capacity/audit/?page=${page}&page_size=20`, {
      credentials: "same-origin",
      signal: controller.signal,
    })
      .then(async (response) => {
        const body = await response.json().catch(() => ({})) as Record<string, unknown>;
        if (!response.ok) throw new Error(responseMessage(body));
        return body as unknown as AuditPage;
      })
      .then((result) => {
        if (version === requestVersion.current) setData(result);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted || version !== requestVersion.current) return;
        setData(null);
        setError(reason instanceof Error ? reason.message : "The capacity audit register could not be loaded.");
      })
      .finally(() => {
        if (!controller.signal.aborted && version === requestVersion.current) setLoading(false);
      });

    return () => {
      controller.abort();
    };
  }, [isSuperuser, page, reloadKey]);

  if (!isSuperuser) return null;

  return (
    <section className="content-card capacity-audit-register" aria-labelledby="capacity-audit-title">
      <div className="card-heading">
        <div>
          <span className="eyebrow">SYSTEM ADMINISTRATOR · READ ONLY</span>
          <h2 id="capacity-audit-title">Warehouse capacity audit</h2>
          <p>Review synthetic profile and access changes. Measurement values and capacity contents are not shown.</p>
        </div>
        <button
          className="button button-outline"
          type="button"
          onClick={refresh}
          disabled={loading}
        >
          {loading ? "Refreshing…" : "Refresh log"}
        </button>
      </div>

      {loading && <p className="capacity-audit-status" role="status">Loading capacity audit events…</p>}
      {error && <p className="capacity-audit-error" role="alert">{error}</p>}
      {!loading && !error && data?.results.length === 0 && (
        <p className="capacity-audit-status" role="status">No warehouse-capacity audit events are available.</p>
      )}

      {!loading && !error && data && data.results.length > 0 && (
        <>
          <p className="capacity-audit-count" role="status">
            {data.pagination.total_count} events · page {data.pagination.page} of {data.pagination.total_pages}
          </p>
          <div className="capacity-audit-table-wrap">
            <table className="capacity-audit-table">
              <caption className="visually-hidden">Synthetic warehouse-capacity profile and permission events</caption>
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Event</th>
                  <th scope="col">Physical location</th>
                  <th scope="col">Actor</th>
                  <th scope="col">Affected account</th>
                  <th scope="col">Permission</th>
                </tr>
              </thead>
              <tbody>
                {data.results.map((event) => (
                  <tr key={event.id}>
                    <td><time dateTime={event.created_at}>{event.created_at}</time></td>
                    <td>{event.label}</td>
                    <td>{event.location.code} · {event.location.name}</td>
                    <td>{event.actor}</td>
                    <td>{event.target_user ?? "—"}</td>
                    <td>{event.capability ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <nav className="capacity-audit-pagination" aria-label="Warehouse capacity audit pages">
            <button
              className="button button-outline"
              type="button"
              onClick={() => { setData(null); setLoading(true); setPage(data.pagination.previous_page ?? page); }}
              disabled={loading || data.pagination.previous_page === null}
            >
              Previous page
            </button>
            <button
              className="button button-outline"
              type="button"
              onClick={() => { setData(null); setLoading(true); setPage(data.pagination.next_page ?? page); }}
              disabled={loading || data.pagination.next_page === null}
            >
              Next page
            </button>
          </nav>
        </>
      )}
    </section>
  );
}
