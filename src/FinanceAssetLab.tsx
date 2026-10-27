import { useEffect, useMemo, useRef, useState } from "react";
import type { LabContext } from "./finance-draft-lab-api";
import { LabError, labRequest } from "./finance-draft-lab-api";
import { blankAssetDraft, payloadFromAsset, validateAsset } from "./finance-asset";
import type { AssetDraft, AssetPayload } from "./finance-asset";
import "./finance-asset.css";
import { reopenFromQuery } from "./finance-lab-deep-link";

type AssetReview = {
  currency: "QAR";
  proposed_cost: string;
  capitalized: false;
  posted: false;
  approved: false;
};
type AssetSnapshot = AssetPayload & { id: number; revision: number; review: AssetReview };
type AssetDetail = AssetSnapshot & {
  history: { revision: number; created_at: string; snapshot: AssetSnapshot }[];
};
type AssetList = { assets: { id: number; asset_identifier: string; name: string; revision: number }[] };

function copyAsset(row: AssetPayload): AssetDraft {
  return {
    asset_identifier: row.asset_identifier,
    name: row.name,
    description: row.description,
    acquisition_date: row.acquisition_date,
    cost: row.cost,
    account_id: String(row.account_id),
    business_unit_id: row.business_unit_id === null ? "" : String(row.business_unit_id),
    dimension_value_ids: row.dimension_value_ids.map(String),
    reference: row.reference,
    location_details: row.location_details,
  };
}
const accountsFor = (context: LabContext) => context.accounts.map((item) => ({ id: item.id, code: item.code, name: item.name_en }));
const unitsFor = (context: LabContext) => context.business_units.map((item) => ({ id: item.id, code: item.code, name: item.name }));
const dimensionsFor = (context: LabContext) => context.dimension_values.map((item) => ({
  id: item.id, dimension: item.dimension_id, code: item.code, name: item.label_en,
}));

export function FinanceAssetLab() {
  const [authenticated, setAuthenticated] = useState(false);
  const [context, setContext] = useState<LabContext | null>(null);
  const [assets, setAssets] = useState<AssetList["assets"]>([]);
  const [current, setCurrent] = useState<AssetDetail | null>(null);
  const [draft, setDraft] = useState<AssetDraft>(blankAssetDraft);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [dirty, setDirty] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [listInspected, setListInspected] = useState(false);
  const busyRef = useRef(false);
  const dirtyRef = useRef(false);
  const alive = useRef(true);
  const accounts = useMemo(() => context ? accountsFor(context) : [], [context]);
  const units = useMemo(() => context ? unitsFor(context) : [], [context]);
  const dimensions = useMemo(() => context ? dimensionsFor(context) : [], [context]);
  const errors = validateAsset(draft, context);

  function change(next: AssetDraft) {
    setDraft(next);
    dirtyRef.current = true;
    setDirty(true);
    setReviewed(false);
  }
  function clearProtectedData() {
    setAuthenticated(false);
    setContext(null);
    setAssets([]);
    setCurrent(null);
    setDraft(blankAssetDraft());
    dirtyRef.current = false;
    setDirty(false);
    setReviewed(false);
    setConflict(false);
    setUncertain(false);
    setListInspected(false);
    setStatus("Synthetic test access is unavailable.");
    setError("Synthetic test access is unavailable. Protected asset inputs were cleared.");
  }
  useEffect(() => {
    alive.current = true;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current || busyRef.current) event.preventDefault();
    };
    window.addEventListener("beforeunload", beforeUnload);
    void labRequest<{ authenticated: boolean }>("session").then(async (session) => {
      if (!alive.current) return;
      setAuthenticated(session.authenticated);
      if (!session.authenticated) return;
      const [choices, list] = await Promise.all([
        labRequest<LabContext>("context"), labRequest<AssetList>("assets"),
      ]);
      if (alive.current) {
        setContext(choices);
        setAssets(list.assets);
        reopenFromQuery((id) => { void openAsset(id); }, setError);
      }
    }).catch((cause: Error) => {
      if (!alive.current) return;
      if (cause instanceof LabError && cause.status === 403) clearProtectedData();
      else setError(cause.message);
    });
    return () => {
      alive.current = false;
      window.removeEventListener("beforeunload", beforeUnload);
    };
  }, []);
  async function run(action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (cause) {
      if (!alive.current) return;
      if (cause instanceof LabError && cause.status === 403) clearProtectedData();
      else setError(`${cause instanceof Error ? cause.message : "Asset lab unavailable."} Inputs remain unchanged.`);
    } finally {
      busyRef.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function refreshList() {
    const result = await labRequest<AssetList>("assets");
    if (!alive.current) return;
    setAssets(result.assets);
    if (uncertain) {
      setListInspected(true);
      setStatus("Saved asset list refreshed. Inspect it and explicitly reopen a saved record before continuing after the unknown save outcome.");
    } else {
      setStatus("Saved asset list refreshed.");
    }
  }
  function mayReplace() {
    return (!dirtyRef.current && !busyRef.current) ||
      (!busyRef.current && window.confirm("Discard unsaved fixed-asset record edits and open the selected saved version?"));
  }
  function newAsset() {
    if (uncertain) {
      setError("Unknown save outcome: refresh and inspect the saved list, then explicitly reopen a saved asset before starting or saving another.");
      return;
    }
    if (!mayReplace()) return;
    setCurrent(null);
    setDraft(blankAssetDraft());
    dirtyRef.current = false;
    setDirty(false);
    setReviewed(false);
    setConflict(false);
    setStatus("New fixed-asset record worksheet. Proposed input only; no approval, capitalization, or posting is performed.");
  }
  async function openAsset(id: number, confirmed = false) {
    if (uncertain && !listInspected) {
      setError("Refresh and inspect the saved asset list before reopening a record after an unknown save outcome.");
      return;
    }
    if (!confirmed && !mayReplace()) return;
    await run(async () => {
      const [row, choices] = await Promise.all([
        labRequest<AssetDetail>(`assets/${id}`), labRequest<LabContext>("context"),
      ]);
      if (!alive.current) return;
      setContext(choices);
      setCurrent(row);
      setDraft(copyAsset(row));
      dirtyRef.current = false;
      setDirty(false);
      setReviewed(true);
      setConflict(false);
      setUncertain(false);
      setListInspected(false);
      setStatus(`Opened fixed-asset record ${row.id}, revision ${row.revision}. Proposed only — not approved, capitalized, or posted.`);
    });
  }
  function reviewAsset() {
    if (errors.length) {
      setReviewed(false);
      setError(errors.join(" "));
      return;
    }
    setReviewed(true);
    setError("");
    setStatus(`Reviewed explicit asset record inputs. Proposed cost ${draft.cost} QAR. No approval, capitalization, or posting is implied.`);
  }
  async function saveAsset() {
    if (!reviewed || errors.length || uncertain || conflict) {
      setError(errors.join(" ") || "Review the explicit asset record before saving.");
      return;
    }
    await run(async () => {
      let writeAttempted = false;
      try {
        const freshContext = await labRequest<LabContext>("context");
        if (!alive.current) return;
        setContext(freshContext);
        const freshErrors = validateAsset(draft, freshContext);
        if (freshErrors.length) {
          setReviewed(false);
          throw new Error(`Saved choices or inputs need correction after context refresh. Original choices remain visible and are not replaced. ${freshErrors.join(" ")}`);
        }
        const payload = payloadFromAsset(draft, freshContext);
        const body = { ...payload, ...(current ? { expected_revision: current.revision } : {}) };
        writeAttempted = true;
        const saved = await labRequest<AssetDetail>(
          current ? `assets/${current.id}` : "assets", current ? "PUT" : "POST", body,
        );
        if (!saved || !Number.isSafeInteger(saved.id) || !Number.isSafeInteger(saved.revision) || !Array.isArray(saved.history)) {
          throw new Error("The save response was incomplete.");
        }
        if (!alive.current) return;
        setCurrent(saved);
        setDraft(copyAsset(saved));
        dirtyRef.current = false;
        setDirty(false);
        setConflict(false);
        setUncertain(false);
        setListInspected(false);
        setReviewed(true);
        setStatus(`Saved fixed-asset record ${saved.id}, revision ${saved.revision}. Proposed only — not approved, capitalized, or posted.`);
        try {
          await refreshList();
          if (alive.current) setStatus(`Saved fixed-asset record ${saved.id}, revision ${saved.revision}. Saved list refreshed. Proposed only — not approved, capitalized, or posted.`);
        } catch (cause) {
          if (cause instanceof LabError && cause.status === 403) throw cause;
          if (alive.current) setError("Asset saved successfully, but the saved-list refresh failed. The save was acknowledged; refresh the list when available.");
        }
      } catch (cause) {
        if (alive.current && cause instanceof LabError && cause.status === 409) setConflict(true);
        if (alive.current && writeAttempted && (!(cause instanceof LabError) || cause.status >= 500)) {
          setUncertain(true);
          setListInspected(false);
          setStatus("Save response unavailable; the database may have committed. Refresh and inspect the saved list, then explicitly reopen a saved asset before another save.");
        }
        throw cause;
      }
    });
  }
  function setField<K extends keyof AssetDraft>(key: K, value: AssetDraft[K]) {
    change({ ...draft, [key]: value });
  }
  function unavailable(id: string, available: { id: number }[]) {
    return id && !available.some((item) => String(item.id) === id);
  }

  return <main className="asset-lab finance-lab allocation-lab" aria-labelledby="asset-title" data-testid="finance-asset-lab">
    <header className="finance-draft-banner">
      <p className="finance-draft-eyebrow">TIVOLI FINANCE · ISOLATED REVIEW WORKSHEET</p>
      <h1 id="asset-title">Fixed-asset records</h1>
      <p>Capture one explicit proposed asset record at a time, using only saved Finance choices. QAR is fixed; user-entered descriptions and references stay as entered.</p>
      <p><strong>Proposal only.</strong> This worksheet does not approve, capitalize, or post an asset. Cost is a proposed input, not book value.</p>
      <a href="/lab-ui/">Return to Finance draft lab</a>
    </header>
    {error && <p className="asset-alert" role="alert">{error}</p>}
    <p className="asset-live" role="status" aria-live="polite">{busy ? "Working with the isolated asset lab…" : status}</p>
    {!authenticated && <section className="finance-draft-card">
      <h2>Isolated synthetic access</h2>
      <p>Use the existing Finance draft lab to open the synthetic test session and configure saved accounts, business units, and dimension values. This worksheet creates no session or setup.</p>
      <a href="/lab-ui/">Open Finance draft lab setup</a>
    </section>}
    {authenticated && <>
      <section className="finance-draft-card" aria-label="Saved fixed-asset records">
        <div className="asset-section-heading">
          <div><p className="finance-draft-eyebrow">SAVED WORKSHEETS</p><h2>Asset register</h2></div>
          <div className="asset-toolbar">
            <button type="button" className="asset-secondary" disabled={busy} onClick={() => void run(refreshList)}>Refresh saved assets</button>
            <button type="button" disabled={busy || !context || uncertain} onClick={newAsset}>New asset draft</button>
          </div>
        </div>
        {!assets.length ? <p>No saved asset records yet. Add synthetic setup choices in the existing Finance draft lab before creating a record.</p> :
          <ul className="asset-register-list">{assets.map((item) => <li key={item.id}>
            <button type="button" className="asset-secondary" disabled={busy}
              aria-label={`Open asset draft ${item.id}: ${item.name}`} onClick={() => void openAsset(item.id)}>
              {item.asset_identifier} · {item.name} · revision {item.revision}
            </button>
          </li>)}</ul>}
        {uncertain && <p role="alert">Unknown save outcome blocks another save or new record. Refresh and inspect this saved list, then explicitly reopen a saved record to recover.</p>}
      </section>
      {!context && <section className="finance-draft-card">
        <h2>Saved setup is unavailable</h2>
        <p>Saved account, business-unit, or dimension choices are unavailable. Configure synthetic choices in the existing lab; none are created here.</p>
        <a href="/lab-ui/">Open draft lab setup</a>
      </section>}
      {context && <fieldset className="asset-editor" disabled={busy} aria-busy={busy} data-dirty={dirty} data-testid="asset-editor">
        <legend>{current ? `Editing asset record ${current.id}, revision ${current.revision}` : "New asset draft"}</legend>
        <aside className="asset-boundary">
          <strong>Explicit record boundary</strong>
          <p>This worksheet saves one proposed record only. No default name, date, cost, account, or business unit is supplied. A blank business unit means “Not proposed,” not companywide allocation. Zero is permitted as a technical draft input; no threshold or eligibility rule is applied.</p>
          <p>References and identifiers are user-entered proposal text; duplicate draft identifiers are allowed. Location details are optional text only—not a warehouse lookup, custody assignment, or location record.</p>
        </aside>
        {!accounts.length && <p role="note">No saved accounts are available. Configure synthetic choices in the <a href="/lab-ui/">draft lab setup</a>; none are created here.</p>}
        <section className="asset-fields" aria-label="Asset record details">
          <label>Asset identifier · required
            <input aria-label="Asset identifier" maxLength={100} value={draft.asset_identifier} onChange={(event) => setField("asset_identifier", event.target.value)} />
          </label>
          <label>Asset name · required
            <input aria-label="Asset name" maxLength={200} value={draft.name} onChange={(event) => setField("name", event.target.value)} />
          </label>
          <label className="asset-wide">Asset description · required
            <textarea aria-label="Asset description" maxLength={2000} value={draft.description} onChange={(event) => setField("description", event.target.value)} />
          </label>
          <label>Acquisition date · required
            <input type="date" aria-label="Acquisition date" value={draft.acquisition_date} onChange={(event) => setField("acquisition_date", event.target.value)} />
          </label>
          <label>Proposed acquisition cost · required
            <input inputMode="decimal" aria-label="Acquisition cost" data-testid="asset-proposed-cost" value={draft.cost} onChange={(event) => setField("cost", event.target.value)} />
          </label>
          <div className="asset-currency"><span>Currency</span><strong>QAR</strong><span>Fixed · no conversion</span></div>
          <label>Saved account · required
            <select aria-label="Asset account" value={draft.account_id} onChange={(event) => setField("account_id", event.target.value)}>
              <option value="">Choose an existing saved account</option>
              {unavailable(draft.account_id, accounts) && <option value={draft.account_id}>Unavailable saved choice ({draft.account_id}) — choose another</option>}
              {accounts.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name}</option>)}
            </select>
          </label>
          <label>Business unit · optional
            <select aria-label="Asset business unit" value={draft.business_unit_id} onChange={(event) => setField("business_unit_id", event.target.value)}>
              <option value="">Not proposed</option>
              {unavailable(draft.business_unit_id, units) && <option value={draft.business_unit_id}>Unavailable saved choice ({draft.business_unit_id}) — choose another</option>}
              {units.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name}</option>)}
            </select>
          </label>
          <label className="asset-wide">Dimensions · optional; select saved values
            <select multiple aria-label="Asset dimensions" value={draft.dimension_value_ids} onChange={(event) =>
              setField("dimension_value_ids", [...event.target.selectedOptions].map((option) => option.value))}>
              {draft.dimension_value_ids.filter((id) => unavailable(id, dimensions)).map((id) =>
                <option key={`unavailable-${id}`} value={id}>Unavailable saved choice ({id}) — remove or replace</option>)}
              {dimensions.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name}</option>)}
            </select>
            <small>Distinct saved values may be selected, including multiple values from one dimension. Values must belong to the current entity.</small>
          </label>
          <label>Asset reference · optional text
            <input aria-label="Asset reference" maxLength={200} value={draft.reference} onChange={(event) => setField("reference", event.target.value)} />
          </label>
          <label className="asset-wide">Asset location details · optional text
            <textarea aria-label="Asset location details" maxLength={2000} value={draft.location_details} onChange={(event) => setField("location_details", event.target.value)} />
          </label>
        </section>
        <section className="asset-cost" aria-label="Proposed cost">
          <span>Proposed cost · not book value</span><strong>{draft.cost ? `${draft.cost} QAR` : "Not entered"}</strong>
        </section>
        <p className="asset-note">This proposal does not calculate depreciation or set useful life, residual value, capitalization threshold, custody, approval, disposal, or posting.</p>
        {errors.length > 0 && <div className="asset-note" role="note" aria-label="Asset validation">
          <strong>Complete or correct these explicit inputs before saving</strong>
          <ul>{errors.map((issue) => <li key={issue}>{issue}</li>)}</ul>
        </div>}
        {current && <p className="asset-note">Saved review at revision {current.revision}: {current.review.proposed_cost} QAR proposed cost · approved: no · capitalized: no · posted: no.</p>}
        <div className="asset-actions">
          <button type="button" className="asset-secondary" disabled={busy} onClick={reviewAsset}>Review asset draft</button>
          <button type="button" disabled={busy || !reviewed || errors.length > 0 || uncertain || conflict}
            onClick={() => void saveAsset()}>{current ? "Save asset revision" : "Save asset draft"}</button>
        </div>
      </fieldset>}
      {conflict && current && <section className="finance-draft-card" role="alert">
        <h2>Saved revision changed</h2>
        <p>Your local inputs remain unchanged. Reload only after explicit confirmation.</p>
        <button type="button" className="asset-secondary" disabled={busy} onClick={() => {
          if (window.confirm("Discard local fixed-asset record edits and reload the saved version?")) void openAsset(current.id, true);
        }}>Reload saved asset draft</button>
      </section>}
      {current && <section className="finance-draft-card asset-history" aria-label="Asset revision history" data-testid="asset-revision-history">
        <h2>Saved revision history</h2>
        <p>Each snapshot contains proposed record inputs and review metadata only. Nothing is approved, capitalized, or posted.</p>
        {current.history.map((item) => <details key={item.revision}>
          <summary>Revision {item.revision} — {item.snapshot.asset_identifier} · {item.snapshot.name} · {item.snapshot.review.proposed_cost} QAR</summary>
          <p>{item.created_at} · {item.snapshot.acquisition_date} · account ID {item.snapshot.account_id}
            {item.snapshot.business_unit_id === null ? " · business unit not proposed" : ` · business unit ID ${item.snapshot.business_unit_id}`}</p>
          <p>Description: {item.snapshot.description || "None entered"}</p>
          <p>Reference: {item.snapshot.reference || "None entered"} · Location details: {item.snapshot.location_details || "None entered"}</p>
          <p>Dimension value IDs: {item.snapshot.dimension_value_ids.join(", ") || "None proposed"}</p>
        </details>)}
      </section>}
    </>}
  </main>;
}
