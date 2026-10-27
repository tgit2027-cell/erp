import type { ChangeEvent } from "react";

export type RecurringInputs = { start: string; end: string; datesText: string };

export type RecurringPreview = {
  template_id: number;
  revision: number;
  occurrence_count: number;
  occurrences: {
    date: string;
    debit_total: string;
    credit_total: string;
    balanced: boolean;
  }[];
  entry_lines: {
    account_code: string;
    business_unit_code: string;
    dimensions: { code: string }[];
    debit: string;
    credit: string;
    memo_en: string;
    memo_ar: string;
  }[];
  total_proposed_debits: string;
  total_proposed_credits: string;
};

function EnglishText({ en }: { en: string; ar: string }) {
  return <span lang="en">{en}</span>;
}

export function FinanceRecurringProposal({
  value,
  onChange,
}: {
  value: RecurringInputs;
  onChange: (value: RecurringInputs) => void;
}) {
  function update(field: keyof RecurringInputs, nextValue: string) {
    onChange({ ...value, [field]: nextValue });
  }

  return (
    <section className="finance-draft-prototype" aria-labelledby="finance-recurring-title">
      <header className="finance-draft-banner">
        <p className="finance-draft-eyebrow">
          ISOLATED SYNTHETIC FINANCE LAB · PROPOSAL ONLY
        </p>
        <h2 id="finance-recurring-title">
          <EnglishText en="Recurring journal proposal" ar="مقترح قيد يومية متكرر" />
        </h2>
        <p>
          <strong>These are proposed dates only—not scheduled jobs or posted journals.</strong>
          {" "}No month-end or fiscal-calendar assumptions are applied.
        </p>
      </header>

      <div className="finance-draft-journal">
        <div className="finance-draft-grid">
          <label>
            <EnglishText en="Proposal range start" ar="بداية نطاق المقترح" />
            <input
              type="date"
              aria-label="Proposal range start"
              value={value.start}
              onChange={(event: ChangeEvent<HTMLInputElement>) => update("start", event.target.value)}
            />
          </label>
          <label>
            <EnglishText en="Proposal range end" ar="نهاية نطاق المقترح" />
            <input
              type="date"
              aria-label="Proposal range end"
              value={value.end}
              onChange={(event: ChangeEvent<HTMLInputElement>) => update("end", event.target.value)}
            />
          </label>
        </div>

        <label>
          <EnglishText en="Explicit occurrence dates" ar="تواريخ التكرار الصريحة" />
          <textarea
            aria-label="Explicit occurrence dates"
            value={value.datesText}
            onChange={(event: ChangeEvent<HTMLTextAreaElement>) => update("datesText", event.target.value)}
            rows={7}
            spellCheck={false}
            placeholder={"2026-01-15\n2026-02-03"}
          />
        </label>
        <div className="finance-draft-card" role="note">
          <p>
            Enter one <code>YYYY-MM-DD</code> date per line, in strictly increasing order.
            The technical limit is 100 dates. Common entered amounts and journal lines
            are reused unchanged on every supplied date.
          </p>
        </div>
      </div>
    </section>
  );
}

export function FinanceRecurringPreview({ preview }: { preview: RecurringPreview }) {
  return (
    <section className="finance-draft-review" aria-labelledby="finance-recurring-preview-title">
      <h2 id="finance-recurring-preview-title">
        <EnglishText en="Proposed occurrences" ar="مرات التكرار المقترحة" />
      </h2>
      <p className="finance-draft-eyebrow" role="note">
        PROPOSAL ONLY · NOT SCHEDULED · NOT POSTED
      </p>
      <p>Synthetic QAR test amounts only</p>
      <p>
        Template {preview.template_id} · revision {preview.revision} ·
        {" "}{preview.occurrence_count} proposed occurrence{preview.occurrence_count === 1 ? "" : "s"}
      </p>
      <div className="finance-draft-card" aria-label="Exact proposed totals">
        <p>
          <EnglishText en="Total proposed debits" ar="إجمالي المدين المقترح" />{" "}
          <strong>{preview.total_proposed_debits}</strong>
        </p>
        <p>
          <EnglishText en="Total proposed credits" ar="إجمالي الدائن المقترح" />{" "}
          <strong>{preview.total_proposed_credits}</strong>
        </p>
      </div>

      <h3><EnglishText en="Occurrence dates and totals" ar="تواريخ التكرار وإجمالياتها" /></h3>
      {preview.occurrences.length === 0 ? (
        <p className="finance-draft-card">
          No proposed occurrences to display.
        </p>
      ) : (
        <div className="finance-draft-table-scroll" tabIndex={0} role="region" aria-label="Proposed occurrence totals">
          <table>
            <caption>Proposed occurrence totals — not posted</caption>
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">Proposed debits</th>
                <th scope="col">Proposed credits</th>
                <th scope="col">Balance</th>
              </tr>
            </thead>
            <tbody>
              {preview.occurrences.map((occurrence, index) => (
                <tr key={`${occurrence.date}-${index}`}>
                  <th scope="row">{occurrence.date}</th>
                  <td>{occurrence.debit_total}</td>
                  <td>{occurrence.credit_total}</td>
                  <td>
                    {occurrence.balanced
                      ? "Balanced"
                      : "Not balanced"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3><EnglishText en="Common proposed entry lines" ar="سطور القيد المقترحة المشتركة" /></h3>
      <p>
        These entered lines and amounts are reused unchanged for each supplied date.
      </p>
      {preview.entry_lines.length === 0 ? (
        <p className="finance-draft-card">
          No entry-line details to display.
        </p>
      ) : (
        <div className="finance-draft-table-scroll" tabIndex={0} role="region" aria-label="Proposed entry-line details">
          <table>
            <caption>Common proposed entry-line detail — not a posted ledger</caption>
            <thead>
              <tr>
                <th scope="col">Line</th>
                <th scope="col">Account</th>
                <th scope="col">Business unit</th>
                <th scope="col">Dimensions</th>
                <th scope="col">Debit</th>
                <th scope="col">Credit</th>
                <th scope="col">Memo</th>
              </tr>
            </thead>
            <tbody>
              {preview.entry_lines.map((line, index) => (
                <tr key={`${line.account_code}-${index}`}>
                  <th scope="row">{index + 1}</th>
                  <td>{line.account_code || "Not specified"}</td>
                  <td>{line.business_unit_code || "Not specified"}</td>
                  <td>
                    {line.dimensions.length
                      ? line.dimensions.map((dimension) => dimension.code).join(", ")
                      : "None"}
                  </td>
                  <td>{line.debit}</td>
                  <td>{line.credit}</td>
                  <td>
                    {line.memo_en && <div lang="en">{line.memo_en}</div>}
                    {line.memo_ar && <div lang="ar" dir="rtl">{line.memo_ar}</div>}
                    {!line.memo_en && !line.memo_ar && "No memo"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
