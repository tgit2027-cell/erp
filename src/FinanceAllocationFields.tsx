import type { AllocationLineDraft, DestinationDraft } from "./finance-allocation";

type Choice = { id: number; code: string; name: string };
type DimensionValue = { id: number; code: string; label: string };

export function AllocationJournalFields({
  lines, accounts, units, values, update, add, remove,
}: {
  lines: AllocationLineDraft[];
  accounts: Choice[];
  units: Choice[];
  values: DimensionValue[];
  update: (index: number, patch: Partial<AllocationLineDraft>) => void;
  add: () => void;
  remove: (index: number) => void;
}) {
  return <section aria-label="Allocation journal lines">
    <h2>Separate journal review</h2>
    <p>Journal lines are reviewed independently. No offsets or account mappings are created from destinations.</p>
    {lines.map((line, index) => <fieldset className="finance-draft-line allocation-line" key={index}>
      <legend>Journal line {index + 1}</legend>
      <label>Journal account
        <select aria-label={`Allocation journal account ${index + 1}`} value={line.accountId} onChange={(e) => update(index, { accountId: e.target.value })}>
          <option value="">Select a saved account</option>
          {accounts.map((row) => <option key={row.id} value={row.id}>{row.code} · {row.name}</option>)}
        </select>
      </label>
      <label>Business unit — optional
        <select aria-label={`Allocation journal business unit ${index + 1}`} value={line.businessUnitId} onChange={(e) => update(index, { businessUnitId: e.target.value })}>
          <option value="">No business unit</option>
          {units.map((row) => <option key={row.id} value={row.id}>{row.code} · {row.name}</option>)}
        </select>
      </label>
      <label>Dimensions — optional
        <select multiple aria-label={`Allocation journal dimensions ${index + 1}`} value={line.dimensionIds} onChange={(e) => update(index, { dimensionIds: Array.from(e.target.selectedOptions).map((option) => option.value) })}>
          {values.map((row) => <option key={row.id} value={row.id}>{row.code} · {row.label}</option>)}
        </select>
      </label>
      <div className="finance-draft-amounts">
        <label>Debit (QAR)<input aria-label={`Allocation debit ${index + 1}`} inputMode="decimal" value={line.debit} onChange={(e) => update(index, { debit: e.target.value })} /></label>
        <label>Credit (QAR)<input aria-label={`Allocation credit ${index + 1}`} inputMode="decimal" value={line.credit} onChange={(e) => update(index, { credit: e.target.value })} /></label>
      </div>
      <label>English memo (optional)<input aria-label={`Allocation journal memo ${index + 1}`} value={line.memo} onChange={(e) => update(index, { memo: e.target.value })} /></label>
      <label>Arabic memo (optional)<input aria-label={`Allocation journal memo Arabic (optional) ${index + 1}`} value={line.memoAr} onChange={(e) => update(index, { memoAr: e.target.value })} /></label>
      {lines.length > 2 && <button type="button" onClick={() => remove(index)}>Remove journal line</button>}
    </fieldset>)}
    <button type="button" onClick={add}>Add journal line</button>
  </section>;
}

export function AllocationDestinationFields({
  rows, accounts, units, values, update, add, remove,
}: {
  rows: DestinationDraft[];
  accounts: Choice[];
  units: Choice[];
  values: DimensionValue[];
  update: (index: number, patch: Partial<DestinationDraft>) => void;
  add: () => void;
  remove: (index: number) => void;
}) {
  return <section aria-label="Explicit allocation destinations">
    <div className="allocation-section-heading">
      <div><h2>Proposed destinations</h2><p>Enter each amount and destination explicitly. These are not journal lines.</p></div>
      <button type="button" onClick={add}>Add destination</button>
    </div>
    {rows.map((row, index) => <fieldset className="finance-draft-line allocation-destination" key={index}>
      <legend>Destination {index + 1}</legend>
      <label>Amount (QAR)
        <input aria-label={`Destination amount ${index + 1}`} inputMode="decimal" value={row.amount} onChange={(e) => update(index, { amount: e.target.value })} />
      </label>
      <label>Account
        <select aria-label={`Destination account ${index + 1}`} value={row.accountId} onChange={(e) => update(index, { accountId: e.target.value })}>
          <option value="">Select a saved account</option>
          {accounts.map((choice) => <option key={choice.id} value={choice.id}>{choice.code} · {choice.name}</option>)}
        </select>
      </label>
      <label>Business unit — optional
        <select aria-label={`Destination business unit ${index + 1}`} value={row.businessUnitId} onChange={(e) => update(index, { businessUnitId: e.target.value })}>
          <option value="">No business unit</option>
          {units.map((choice) => <option key={choice.id} value={choice.id}>{choice.code} · {choice.name}</option>)}
        </select>
      </label>
      <label>Dimensions — optional
        <select multiple aria-label={`Destination dimensions ${index + 1}`} value={row.dimensionIds} onChange={(e) => update(index, { dimensionIds: Array.from(e.target.selectedOptions).map((option) => option.value) })}>
          {values.map((choice) => <option key={choice.id} value={choice.id}>{choice.code} · {choice.label}</option>)}
        </select>
      </label>
      {rows.length > 1 && <button type="button" onClick={() => remove(index)}>Remove destination</button>}
    </fieldset>)}
  </section>;
}
