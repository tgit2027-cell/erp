import { useCallback, useEffect, useState } from "react";
import { FinanceLabNavigation } from "./FinanceLabNavigation";
import { FinanceLabRecoveryPanel } from "./FinanceLabRecoveryPanel";
import { LabError, labRequest } from "./finance-draft-lab-api";
import "./finance-lab-review.css";

export type ReviewItem = {
  id: number;
  revision: number;
  label: string;
  kind?: string;
  updated_at: string;
};
export type ReviewGroup = {
  resource: string;
  label: string;
  editor_path: string;
  items: ReviewItem[];
};
export type ReviewIndex = { groups: ReviewGroup[] };

export function EmbeddedFinanceLabReview() {
  return <FinanceLabReview embedded />;
}

export function FinanceLabReview({ embedded = false }: { embedded?: boolean } = {}) {
  const [index, setIndex] = useState<ReviewIndex | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [forbidden, setForbidden] = useState(false);
  const [resource, setResource] = useState("");
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    setForbidden(false);
    try {
      // Rebind recovery metadata to the server-verified actor/entity after a
      // full-page navigation/reload; never trust a browser-only scope.
      await labRequest("session");
      const result = await labRequest<ReviewIndex>("review");
      if (!result || !Array.isArray(result.groups)) throw new Error("The review register response was incomplete.");
      setIndex(result);
    } catch (cause) {
      setIndex(null);
      if (cause instanceof LabError && cause.status === 403) {
        setForbidden(true);
        setError("Synthetic review access is unavailable for this session or entity.");
      } else setError(cause instanceof Error ? cause.message : "The saved review register could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const groups = index?.groups ?? [];
  const count = groups.reduce((total, group) => total + group.items.length, 0);
  const query = search.trim().toLowerCase();
  const filtered = Boolean(resource || search);
  const unavailableType = Boolean(resource && !groups.some(group => group.resource === resource));
  const visibleGroups = groups.filter(group => !resource || group.resource === resource).map(group => ({
    ...group,
    items: group.items.filter(item => !query || (query.startsWith("#")
      ? String(item.id) === query.slice(1)
      : String(item.id) === query || (item.label || `Saved draft ${item.id}`).toLowerCase().includes(query))),
  })).filter(group => !filtered || group.items.length > 0);
  const visibleCount = visibleGroups.reduce((total, group) => total + group.items.length, 0);
  const clearFilters = () => { setResource(""); setSearch(""); };

  return <div className="lab-review-shell">
    {!embedded && <FinanceLabNavigation />}
    <main className="lab-review-page">
      <header className="lab-review-hero">
        <div>
          <p className="lab-review-eyebrow">TIVOLI FINANCE · ISOLATED REVIEW</p>
          <h1>Saved work,<br /><em>ready to reopen.</em></h1>
          <p className="lab-review-intro">One index for existing synthetic drafts and worksheets. Open an item in its original editor; no values are combined here.</p>
        </div>
        <div className="lab-review-stamp" aria-label="Review index">
          <span>REGISTER</span><strong>{loading || !index ? "—" : String(count).padStart(2, "0")}</strong><small>saved items</small>
        </div>
      </header>

      {!embedded && <FinanceLabRecoveryPanel />}

      <section className="lab-review-register" aria-labelledby="saved-review-title">
        <div className="lab-review-section-head">
          <div><p className="lab-review-eyebrow">EXISTING SAVED DRAFTS</p><h2 id="saved-review-title">Review register</h2></div>
          <button type="button" className="lab-review-button" onClick={() => void load()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh register"}
          </button>
        </div>
        {loading && <div className="lab-review-skeleton" aria-label="Loading saved drafts">
          <span /><span /><span />
        </div>}
        {!loading && error && <section className={`lab-review-message${forbidden ? " is-forbidden" : ""}`} role="alert">
          <span className="lab-review-message-mark">{forbidden ? "403" : "!"}</span>
          <div><h3>{forbidden ? "Access is not available" : "Register unavailable"}</h3>
            <p>{error}</p>
            {forbidden && <p>Return to the existing lab setup and verify its synthetic test session and entity context. No new access is created here.</p>}
          </div>
          <button type="button" className="lab-review-button" onClick={() => void load()}>Try again</button>
        </section>}
        {!loading && !error && index && <>
          <div className="lab-review-filters" aria-label="Find an existing saved draft">
            <label>Draft type<select aria-label="Draft type" value={resource} onChange={event => setResource(event.target.value)}>
              <option value="">All existing types</option>
              {unavailableType && <option value={resource}>Selected type unavailable</option>}
              {groups.map(group => <option key={group.resource} value={group.resource}>{group.label}</option>)}
            </select></label>
            <label>Saved label or ID<input type="search" aria-label="Search saved drafts"
              value={search} onChange={event => setSearch(event.target.value)}
              placeholder="Name, reference or exact #ID" /></label>
            <button type="button" className="lab-review-button" disabled={!filtered} onClick={clearFilters}>Clear filters</button>
          </div>
          <p role="status" aria-live="polite">{visibleCount} of {count} saved items shown.
            {" "}Filters apply only to this authorized listing; no financial totals are combined.</p>
          {unavailableType && <p role="alert">The selected draft type is unavailable in the current listing. Clear filters or choose an available type.</p>}
        </>}
        {!loading && !error && filtered && visibleCount === 0 && <div className="lab-review-empty">
          <div><h3>No saved drafts match these filters</h3><p>Change the type or search, or clear filters to review the existing listing.</p></div>
        </div>}
        {!loading && !error && !filtered && count === 0 && <div className="lab-review-empty">
          <span className="lab-review-empty-index">00</span>
          <div><h3>Nothing saved yet</h3><p>Only persisted drafts appear here. Start in an editor and return when a draft has been saved.</p></div>
          <a className="lab-review-link" href="/lab-ui/">Open journal editor</a>
        </div>}
        {!loading && !error && visibleCount > 0 && <div className="lab-review-groups">
          {visibleGroups.map((group, groupIndex) => <section className="lab-review-group" key={group.resource}
            aria-label={group.label}>
            <header><span className="lab-review-group-number">{String(groupIndex + 1).padStart(2, "0")}</span>
              <div><h3>{group.label}</h3><p>{group.items.length} saved {group.items.length === 1 ? "item" : "items"}</p></div>
              <a className="lab-review-editor-link" href={group.editor_path}>Open editor <span aria-hidden="true">↗</span></a>
            </header>
            <ul>{group.items.map((item) => <li key={item.id}>
              <div className="lab-review-item-id">#{item.id}</div>
              <div className="lab-review-item-copy"><strong>{item.label || `Saved draft ${item.id}`}</strong>
                <p>Revision {item.revision}{item.kind ? ` · ${item.kind.replaceAll("_", " ")}` : ""}</p>
              </div>
              <time dateTime={item.updated_at}>{item.updated_at}</time>
              <a className="lab-review-reopen" href={`${group.editor_path}?reopen=${encodeURIComponent(item.id)}`}>Reopen <span aria-hidden="true">→</span></a>
            </li>)}</ul>
          </section>)}
        </div>}
      </section>
      <footer className="lab-review-footer"><span>Disposable synthetic data · review only</span><span>No aggregate monetary totals are shown</span></footer>
    </main>
  </div>;
}

export default FinanceLabReview;
