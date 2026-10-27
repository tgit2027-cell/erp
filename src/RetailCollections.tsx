import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import "./retail-collections.css";

type CollectionRow = {
  quote_id: number;
  kind: string;
  status: string;
  currency: string;
  total_quote_value: string;
  recorded_collections: string;
  remaining_balance: string;
  ready_at: string | null;
  delivered_at: string | null;
};
type ResponseData = {
  business_unit: { id: number; code: string; name: string };
  rows: CollectionRow[];
  pagination: {
    page: number;
    page_size: number;
    total_count: number;
    total_pages: number;
    previous_page: number | null;
    next_page: number | null;
  };
  test_only_label: string;
};
type State =
  | { status: "idle" | "loading"; data: null; error: ""; denied: false }
  | { status: "ready"; data: ResponseData; error: ""; denied: false }
  | { status: "error"; data: null; error: string; denied: boolean };

async function requestCollections(url: string): Promise<ResponseData> {
  const response = await fetch(url, {
    method: "GET",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const reason = typeof body?.detail === "string" ? body.detail
      : typeof body?.message === "string" ? body.message
        : `Request unavailable (${response.status}).`;
    const error = new Error(reason) as Error & { status: number };
    error.status = response.status;
    throw error;
  }
  return body as ResponseData;
}

function formatDate(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleDateString();
}

export default function RetailCollections({
  selectedUnitId,
  selectedUnitName,
  username,
  refreshCounter,
  onOpenQuoteActions,
}: {
  selectedUnitId: number;
  selectedUnitName: string;
  username: string;
  refreshCounter: number;
  onOpenQuoteActions: (quoteId: number) => void;
}) {
  const scope = `${selectedUnitId}:${username}`;
  const [includeFullyCollected, setIncludeFullyCollected] = useState(false);
  const [quoteInput, setQuoteInput] = useState("");
  const [submittedQuoteId, setSubmittedQuoteId] = useState("");
  const [searchError, setSearchError] = useState("");
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<{ scope: string; state: State }>(() => ({
    scope,
    state: { status: "loading", data: null, error: "", denied: false },
  }));
  const currentVersion = useRef(0);
  const currentScope = useRef(scope);
  const state: State = result.scope === scope
    ? result.state
    : { status: "loading", data: null, error: "", denied: false };

  // Hide a previous unit or user synchronously on its first render.
  if (currentScope.current !== scope) {
    currentScope.current = scope;
    setResult({ scope, state: { status: "loading", data: null, error: "", denied: false } });
    setPage(1);
    setQuoteInput("");
    setSubmittedQuoteId("");
  }

  useEffect(() => {
    const version = ++currentVersion.current;
    let active = true;
    const query = new URLSearchParams({
      business_unit_id: String(selectedUnitId),
      page: String(page),
      page_size: "20",
      remaining_only: String(!includeFullyCollected),
    });
    if (submittedQuoteId) query.set("quote_id", submittedQuoteId);
    setResult({ scope, state: { status: "loading", data: null, error: "", denied: false } });
    requestCollections(`/api/purchasing/collections/?${query.toString()}`)
      .then((data) => {
        if (active && currentVersion.current === version && currentScope.current === scope) {
          setResult({ scope, state: { status: "ready", data, error: "", denied: false } });
        }
      })
      .catch((reason: unknown) => {
        if (!active || currentVersion.current !== version || currentScope.current !== scope) return;
        const error = reason as Error & { status?: number };
        setResult({
          scope,
          state: {
            status: "error",
            data: null,
            error: error.message || "Collection follow-up is unavailable.",
            denied: error.status === 401 || error.status === 403 || error.status === 404,
          },
        });
      });
    return () => { active = false; };
  }, [selectedUnitId, scope, page, includeFullyCollected, submittedQuoteId, refreshCounter, retry]);

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = quoteInput.trim();
    if (value && (!/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(Number(value)))) {
      setSearchError("Enter a positive whole-number quote ID.");
      return;
    }
    setSearchError("");
    setPage(1);
    setSubmittedQuoteId(value);
  }

  return (
    <section className="retail-collections" aria-labelledby="retail-collections-title">
      <header className="rc-heading">
        <div>
          <span className="rc-kicker">RETAIL · FOLLOW-UP · READ ONLY</span>
          <h2 id="retail-collections-title">Collection follow-up</h2>
          <p>Review server-calculated balances for authorized quotations in <strong>{selectedUnitName}</strong>.</p>
        </div>
        <span className="rc-stamp">TEST ONLY<br />SYNTHETIC</span>
      </header>
      <aside className="rc-caveat">
        <strong>Simulated recorded collections only.</strong>
        <span>These figures are not real payments, receivables, overdue amounts or new payment terms. Storage payments are excluded; balances are returned by the server and are not recalculated here.</span>
      </aside>

      <div className="rc-controls">
        <form onSubmit={search} className="rc-search">
          <label htmlFor="rc-quote-search">Exact quote ID</label>
          <div>
            <input
              id="rc-quote-search"
              inputMode="numeric"
              pattern="[0-9]*"
              value={quoteInput}
              onChange={(event) => {
                setQuoteInput(event.target.value);
                setSearchError("");
              }}
              placeholder="e.g. 1042"
              aria-invalid={searchError ? true : undefined}
              aria-describedby={searchError ? "rc-search-error" : "rc-search-help"}
            />
            <button type="submit" className="rc-button rc-button-primary">Search</button>
            {submittedQuoteId && <button type="button" className="rc-button" onClick={() => {
              setQuoteInput("");
              setSubmittedQuoteId("");
              setPage(1);
            }}>Clear search</button>}
          </div>
          {searchError ? <small className="rc-validation" id="rc-search-error" role="alert">{searchError}</small>
            : <small id="rc-search-help">Enter a positive whole-number ID to request an exact quote match.</small>}
        </form>
        <label className="rc-toggle">
          <input type="checkbox" checked={includeFullyCollected} onChange={(event) => {
            setIncludeFullyCollected(event.target.checked);
            setPage(1);
          }} />
          <span>Include fully collected quotations</span>
        </label>
        <button type="button" className="rc-button rc-refresh" onClick={() => setRetry((value) => value + 1)} disabled={state.status === "loading"}>
          Refresh list
        </button>
      </div>

      {state.status === "loading" && <div className="rc-loading" role="status" aria-label="Loading collection follow-up"><i /><i /><i /></div>}
      {state.status === "error" && (
        <div className="rc-message rc-error" role={state.denied ? "status" : "alert"}>
          <strong>{state.denied ? "Collection view not authorized" : "Collection data unavailable"}</strong>
          <p>{state.denied ? "The server did not authorize this read-only view. No collection rows are displayed." : state.error}</p>
          {!state.denied && <button type="button" className="rc-button" onClick={() => setRetry((value) => value + 1)}>Retry</button>}
        </div>
      )}
      {state.status === "ready" && state.data.rows.length === 0 && (
        <div className="rc-empty"><span>NO MATCHES</span><strong>No quotations returned</strong><p>{submittedQuoteId ? `No authorized collection row matched quote ${submittedQuoteId}.` : "No synthetic quotations match this unit and filter."}</p></div>
      )}
      {state.status === "ready" && state.data.rows.length > 0 && <>
        <div className="rc-scope"><span><small>BUSINESS UNIT</small><strong>{state.data.business_unit.name} · {state.data.business_unit.code}</strong></span><span><small>RETURNED</small><strong>{state.data.pagination.total_count} quotations</strong></span></div>
        <div className="rc-table-scroll">
          <table className="rc-table">
            <caption className="rc-visually-hidden">Read-only collection follow-up for {state.data.business_unit.name}</caption>
            <thead><tr>
              <th scope="col">Quote</th><th scope="col">Kind / status</th><th scope="col">Quote value</th>
              <th scope="col">Simulated recorded collections</th><th scope="col">Remaining balance</th>
              <th scope="col">Ready</th><th scope="col">Delivered</th><th scope="col">Action</th>
            </tr></thead>
            <tbody>{state.data.rows.map((row) => (
              <tr key={row.quote_id}>
                <th scope="row" className="rc-id">#{row.quote_id}</th>
                <td><strong>{row.kind}</strong><small>{row.status}</small></td>
                <td className="rc-number">{row.currency} {row.total_quote_value}</td>
                <td className="rc-number">{row.currency} {row.recorded_collections}</td>
                <td className="rc-number rc-balance">{row.currency} {row.remaining_balance}</td>
                <td>{formatDate(row.ready_at)}</td><td>{formatDate(row.delivered_at)}</td>
                <td><button type="button" className="rc-row-action" onClick={() => onOpenQuoteActions(row.quote_id)}>Open quote actions</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <nav className="rc-pager" aria-label="Collection follow-up pages">
          <span>Page <strong>{state.data.pagination.page}</strong> of <strong>{state.data.pagination.total_pages}</strong></span>
          <div>
            <button type="button" className="rc-button" aria-label="Previous page" disabled={!state.data.pagination.previous_page} onClick={() => state.data.pagination.previous_page && setPage(state.data.pagination.previous_page)}>Previous</button>
            <button type="button" className="rc-button" aria-label="Next page" disabled={!state.data.pagination.next_page} onClick={() => state.data.pagination.next_page && setPage(state.data.pagination.next_page)}>Next</button>
          </div>
        </nav>
        <small className="rc-test-label">{state.data.test_only_label || "Synthetic test data"} · read-only list</small>
      </>}
    </section>
  );
}
