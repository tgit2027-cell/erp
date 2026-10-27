import { useState } from "react";
import type { FormEvent } from "react";
import "./finance-draft-workspace.css";

export type DraftAccount = { code: string; nameEn: string; nameAr: string };
export type DraftDimension = { code: string; labelEn: string; labelAr: string };
export type DraftDimensionValue = { dimensionCode: string; code: string; labelEn: string; labelAr: string };
export type DraftLine = {
  accountCode: string;
  businessUnitCode: string;
  dimensionValueCode: string;
  dimensionValueCodes?: string[];
  debit: string;
  credit: string;
  memoEn: string;
  memoAr: string;
};
export type DraftSnapshot = {
  titleEn: string;
  titleAr: string;
  lines: DraftLine[];
  debitTotal: string;
  creditTotal: string;
  asOfDate?: string;
  adjustmentProposal?: { date: string; explanation: string };
};
type ReviewIssue = { code: string; en: string; ar: string };

function EnglishText({ en }: { en: string; ar: string }) {
  return <span lang="en">{en}</span>;
}

function exactCents(value: string): bigint | null {
  const normalized = value.trim();
  if (!/^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(normalized)) return null;
  const [whole, fraction = ""] = normalized.split(".");
  try {
    const cents = BigInt(whole) * 100n + BigInt((fraction + "00").slice(0, 2));
    return cents < 100000000000000n ? cents : null;
  } catch {
    return null;
  }
}

function moneyText(cents: bigint): string {
  if (cents < 0n) return `-${moneyText(-cents)}`;
  const whole = cents / 100n;
  const fraction = String(cents % 100n).padStart(2, "0");
  return `${whole}.${fraction}`;
}

function validateLines(lines: DraftLine[], accounts: DraftAccount[]) {
  const issues: ReviewIssue[] = [];
  if (lines.length < 2) {
    issues.push({
      code: "at_least_two_lines",
      en: "Add at least two lines before review.",
      ar: "أضف سطرين على الأقل قبل المراجعة.",
    });
  }
  let debits = 0n;
  let credits = 0n;
  lines.forEach((line, index) => {
    const debit = exactCents(line.debit);
    const credit = exactCents(line.credit);
    if (!line.accountCode || !accounts.some((account) => account.code === line.accountCode)) {
      issues.push({
        code: `line_${index + 1}_account_required`,
        en: `Choose a saved draft account for line ${index + 1}.`,
        ar: `اختر حساب مسودة محفوظاً للسطر ${index + 1}.`,
      });
    }
    if (debit === null || credit === null) {
      issues.push({
        code: `line_${index + 1}_amount_invalid`,
        en: `Line ${index + 1} needs non-negative amounts with at most two decimal places.`,
        ar: `يجب أن تكون مبالغ السطر ${index + 1} غير سالبة وبحد أقصى منزلتين عشريتين.`,
      });
      return;
    }
    if ((debit > 0n) === (credit > 0n)) {
      issues.push({
        code: `line_${index + 1}_one_side_only`,
        en: `Line ${index + 1} must contain a positive debit or credit, not both.`,
        ar: `يجب أن يحتوي السطر ${index + 1} على مدين أو دائن موجب، وليس كليهما.`,
      });
    }
    debits += debit;
    credits += credit;
  });
  if (debits !== credits) {
    issues.push({
      code: "totals_not_balanced",
      en: "Draft totals do not balance.",
      ar: "إجمالي المسودة غير متوازن.",
    });
  }
  return { issues, debitTotal: moneyText(debits), creditTotal: moneyText(credits), difference: moneyText(debits - credits) };
}

function emptyLine(): DraftLine {
  return { accountCode: "", businessUnitCode: "", dimensionValueCode: "", debit: "", credit: "", memoEn: "", memoAr: "" };
}

export function FinanceDraftWorkspace({
  legalEntityLabel = "Synthetic legal entity — no production setup",
  businessUnits = [],
  initialState,
  onSave,
  onSetup,
  onDraftChange,
  saveUnavailableReason,
  worksheetKind = "journal",
  initialAsOfDate = "",
  adjustmentKind,
  initialAdjustmentProposal,
}: {
  legalEntityLabel?: string;
  businessUnits?: { code: string; nameEn: string; nameAr?: string }[];
  initialState?: {
    accounts: DraftAccount[]; dimensions: DraftDimension[]; dimensionValues: DraftDimensionValue[];
    draft?: DraftSnapshot;
  };
  onSave?: (draft: DraftSnapshot) => Promise<void>;
  onSetup?: (kind: "accounts" | "dimensions" | "dimension-values", data: Record<string, string>) => Promise<void>;
  onDraftChange?: () => void;
  saveUnavailableReason?: string;
  worksheetKind?: "journal" | "opening_balance" | "accrual_adjustment" | "prepayment_adjustment";
  initialAsOfDate?: string;
  adjustmentKind?: "accrual_adjustment" | "prepayment_adjustment";
  initialAdjustmentProposal?: { date: string; explanation: string };
}) {
  const [accounts, setAccounts] = useState<DraftAccount[]>(initialState?.accounts ?? []);
  const [dimensions, setDimensions] = useState<DraftDimension[]>(initialState?.dimensions ?? []);
  const [dimensionValues, setDimensionValues] = useState<DraftDimensionValue[]>(initialState?.dimensionValues ?? []);
  const [accountCode, setAccountCode] = useState("");
  const [accountNameEn, setAccountNameEn] = useState("");
  const [accountNameAr, setAccountNameAr] = useState("");
  const [dimensionCode, setDimensionCode] = useState("");
  const [dimensionLabelEn, setDimensionLabelEn] = useState("");
  const [dimensionLabelAr, setDimensionLabelAr] = useState("");
  const [valueCode, setValueCode] = useState("");
  const [valueLabelEn, setValueLabelEn] = useState("");
  const [valueLabelAr, setValueLabelAr] = useState("");
  const [selectedDimensionCode, setSelectedDimensionCode] = useState("");
  const [titleEn, setTitleEn] = useState(initialState?.draft?.titleEn ?? "");
  const [titleAr, setTitleAr] = useState(initialState?.draft?.titleAr ?? "");
  const [asOfDate, setAsOfDate] = useState(initialAsOfDate);
  const [adjustmentDate, setAdjustmentDate] = useState(initialAdjustmentProposal?.date ?? "");
  const [adjustmentExplanation, setAdjustmentExplanation] = useState(initialAdjustmentProposal?.explanation ?? "");
  const [lines, setLines] = useState<DraftLine[]>(initialState?.draft?.lines ?? [emptyLine(), emptyLine()]);
  const [formError, setFormError] = useState("");
  const [review, setReview] = useState<ReturnType<typeof validateLines> | null>(null);
  const [savedDrafts, setSavedDrafts] = useState<DraftSnapshot[]>([]);

  async function addAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = accountCode.trim();
    if (!/^TEST-DRAFT-ACCT-[A-Z0-9-]{1,23}$/.test(code)) {
      setFormError("Use a TEST-DRAFT-ACCT- code.");
      return;
    }
    if (!accountNameEn.trim()) {
      setFormError("Enter the English draft account name.");
      return;
    }
    if (accounts.some((account) => account.code === code)) {
      setFormError("That draft code already exists in this local prototype.");
      return;
    }
    try {
      await onSetup?.("accounts", { code, name_en: accountNameEn.trim(), name_ar: accountNameAr.trim() });
    } catch (error) { setFormError(`${error instanceof Error ? error.message : "Setup failed"} Not saved; inputs remain in the form.`); return; }
    setAccounts((current) => [...current, { code, nameEn: accountNameEn.trim(), nameAr: accountNameAr.trim() }]);
    setAccountCode("");
    setAccountNameEn("");
    setAccountNameAr("");
    setFormError("");
    setReview(null);
  }

  async function addDimension(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = dimensionCode.trim();
    if (!/^TEST-DRAFT-DIM-[A-Z0-9-]{1,20}$/.test(code)) {
      setFormError("Use a TEST-DRAFT-DIM- code.");
      return;
    }
    if (!dimensionLabelEn.trim()) {
      setFormError("Enter the English draft dimension label.");
      return;
    }
    if (dimensions.some((dimension) => dimension.code === code)) {
      setFormError("That draft dimension code already exists.");
      return;
    }
    try {
      await onSetup?.("dimensions", { code, label_en: dimensionLabelEn.trim(), label_ar: dimensionLabelAr.trim() });
    } catch (error) { setFormError(`${error instanceof Error ? error.message : "Setup failed"} Not saved; inputs remain in the form.`); return; }
    setDimensions((current) => [...current, {
      code, labelEn: dimensionLabelEn.trim(), labelAr: dimensionLabelAr.trim(),
    }]);
    setSelectedDimensionCode(code);
    setDimensionCode("");
    setDimensionLabelEn("");
    setDimensionLabelAr("");
    setFormError("");
  }

  async function addDimensionValue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedDimensionCode || !dimensionLabelExists(selectedDimensionCode, dimensions)) {
      setFormError("Create/select a draft dimension first.");
      return;
    }
    const code = valueCode.trim();
    if (!/^TEST-DRAFT-VAL-[A-Z0-9-]{1,21}$/.test(code)) {
      setFormError("Use a TEST-DRAFT-VAL- code.");
      return;
    }
    if (!valueLabelEn.trim()) {
      setFormError("Enter the English dimension value.");
      return;
    }
    if (dimensionValues.some((value) => value.code === code)) {
      setFormError("That dimension value code already exists.");
      return;
    }
    try {
      await onSetup?.("dimension-values", { dimension_code: selectedDimensionCode, code, label_en: valueLabelEn.trim(), label_ar: valueLabelAr.trim() });
    } catch (error) { setFormError(`${error instanceof Error ? error.message : "Setup failed"} Not saved; inputs remain in the form.`); return; }
    setDimensionValues((current) => [...current, {
      dimensionCode: selectedDimensionCode, code,
      labelEn: valueLabelEn.trim(), labelAr: valueLabelAr.trim(),
    }]);
    setValueCode("");
    setValueLabelEn("");
    setValueLabelAr("");
    setFormError("");
  }

  function updateLine(index: number, changes: Partial<DraftLine>) {
    setLines((current) => current.map((line, lineIndex) =>
      lineIndex === index ? { ...line, ...changes } : line));
    setReview(null);
    setFormError("");
  }

  function reviewDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (worksheetKind === "opening_balance" && !asOfDate) {
      setReview(null);
      setFormError("Enter the required as-of date for this opening-balance worksheet.");
      return;
    }
    if (adjustmentKind && (!/^\d{4}-\d{2}-\d{2}$/.test(adjustmentDate) ||
      Number.isNaN(Date.parse(`${adjustmentDate}T00:00:00Z`)) ||
      new Date(`${adjustmentDate}T00:00:00Z`).toISOString().slice(0, 10) !== adjustmentDate)) {
      setReview(null);
      setFormError("Enter a valid proposed date in YYYY-MM-DD format.");
      return;
    }
    if (adjustmentKind && (!adjustmentExplanation.trim() || adjustmentExplanation.length > 2000)) {
      setReview(null);
      setFormError(adjustmentExplanation.length > 2000
        ? "The explicit explanation must be 2,000 characters or fewer."
        : "Enter the required explicit explanation for this proposal.");
      return;
    }
    if (!titleEn.trim()) {
      setReview(null);
      setFormError("Enter an English draft title.");
      return;
    }
    const result = validateLines(lines, accounts);
    setReview(result);
    setFormError("");
  }

  async function saveLocalDraft() {
    if (!review || review.issues.length) return;
    const snapshot = {
      titleEn: titleEn.trim(), titleAr: titleAr.trim(),
      lines: lines.map((line) => ({ ...line })),
      debitTotal: review.debitTotal, creditTotal: review.creditTotal,
      ...(worksheetKind === "opening_balance" ? { asOfDate } : {}),
      ...(adjustmentKind ? { adjustmentProposal: { date: adjustmentDate, explanation: adjustmentExplanation } } : {}),
    };
    if (onSave) {
      try { await onSave(snapshot); setFormError(""); }
      catch (error) { setFormError(`${error instanceof Error ? error.message : "Save failed"} Not saved; your changes remain in the form.`); }
    } else setSavedDrafts((current) => [...current, snapshot]);
  }

  return (
    <main className="finance-draft-prototype" aria-labelledby="finance-draft-title">
      <header className="finance-draft-banner">
        <p className="finance-draft-eyebrow">ISOLATED SYNTHETIC PROTOTYPE · NOT AVAILABLE TO SHARED USERS</p>
        <h1 id="finance-draft-title"><EnglishText en="Finance draft foundation" ar="أساس مسودات المالية" /></h1>
        <p>
          Draft-only account and dimension proposals, plus a balanced-entry review.
          {onSave ? "Persistence uses only the disposable synthetic lab API. No shared Finance access is configured." : "No Finance permission or user-facing API is configured."} No ledger posting,
          approval, payment, tax or accounting mapping is performed.
        </p>
        <p><strong>One synthetic legal entity:</strong> {legalEntityLabel}</p>
        <p>BU is an optional draft dimension, not another company. Arabic content is entered explicitly; missing translations are not generated. {onSave ? "Saved drafts survive page reload only while the disposable lab is running; the database is deleted when the runner exits." : "Local-session drafts disappear on reload."}</p>
      </header>

      {formError && <p className="finance-draft-alert" role="alert">{formError}</p>}

      <section aria-labelledby="draft-setup-title">
        <h2 id="draft-setup-title"><EnglishText en="Draft account and dimension setup" ar="إعداد الحسابات والأبعاد كمسودات" /></h2>
        <div className="finance-draft-grid">
          <form className="finance-draft-card" onSubmit={addAccount}>
            <h3><EnglishText en="Proposed account" ar="حساب مقترح" /></h3>
            <label>
              <EnglishText en="Draft account code" ar="رمز الحساب التجريبي" />
              <input aria-label="Draft account code" value={accountCode} onChange={(event) => setAccountCode(event.target.value)} placeholder="TEST-DRAFT-ACCT-..." required />
            </label>
            <label>
              <EnglishText en="Account name — English" ar="اسم الحساب — الإنجليزية" />
              <input aria-label="Account name English" value={accountNameEn} onChange={(event) => setAccountNameEn(event.target.value)} required />
            </label>
            <label>
              <EnglishText en="Account name — Arabic (optional)" ar="اسم الحساب — العربية (اختياري)" />
              <input aria-label="Account name Arabic (optional)" value={accountNameAr} onChange={(event) => setAccountNameAr(event.target.value)} dir="auto" />
            </label>
            <button type="submit">Add draft account</button>
            {accounts.length > 0 && (
              <ul aria-label="Local draft accounts">
                {accounts.map((account) => <li key={account.code}>{account.code} · {account.nameEn}{account.nameAr && <> · <span lang="ar" dir="rtl">{account.nameAr}</span></>}</li>)}
              </ul>
            )}
          </form>

          <div className="finance-draft-card">
            <form className="finance-draft-form" onSubmit={addDimension}>
            <h3><EnglishText en="Proposed dimension" ar="بُعد مقترح" /></h3>
            <label>
              <EnglishText en="Draft dimension code" ar="رمز البُعد التجريبي" />
              <input aria-label="Draft dimension code" value={dimensionCode} onChange={(event) => setDimensionCode(event.target.value)} placeholder="TEST-DRAFT-DIM-..." required />
            </label>
            <label>
              <EnglishText en="Dimension label — English" ar="اسم البُعد — الإنجليزية" />
              <input aria-label="Dimension label English" value={dimensionLabelEn} onChange={(event) => setDimensionLabelEn(event.target.value)} required />
            </label>
            <label>
              <EnglishText en="Dimension label — Arabic (optional)" ar="اسم البُعد — العربية (اختياري)" />
              <input aria-label="Dimension label Arabic (optional)" value={dimensionLabelAr} onChange={(event) => setDimensionLabelAr(event.target.value)} dir="auto" />
            </label>
            <button type="submit">Add draft dimension</button>
            {dimensions.length > 0 && (
              <ul aria-label="Local draft dimensions">
                {dimensions.map((dimension) => <li key={dimension.code}>{dimension.code} · {dimension.labelEn}{dimension.labelAr && <> · <span lang="ar" dir="rtl">{dimension.labelAr}</span></>}</li>)}
              </ul>
            )}
            </form>
            <form className="finance-draft-form" onSubmit={addDimensionValue}>
            <h3><EnglishText en="Proposed dimension value" ar="قيمة بُعد مقترحة" /></h3>
            <label>
              <EnglishText en="Dimension for a value" ar="البُعد للقيمة" />
              <select aria-label="Dimension for value" value={selectedDimensionCode} onChange={(event) => setSelectedDimensionCode(event.target.value)}>
                <option value="">Select a draft dimension</option>
                {dimensions.map((dimension) => <option key={dimension.code} value={dimension.code}>{dimension.labelEn}</option>)}
              </select>
            </label>
            <label>
              <EnglishText en="Draft dimension value code" ar="رمز قيمة البُعد التجريبية" />
              <input aria-label="Draft dimension value code" value={valueCode} onChange={(event) => setValueCode(event.target.value)} placeholder="TEST-DRAFT-VAL-..." required />
            </label>
            <label>
              <EnglishText en="Dimension value — English" ar="قيمة البُعد — الإنجليزية" />
              <input aria-label="Dimension value English" value={valueLabelEn} onChange={(event) => setValueLabelEn(event.target.value)} required />
            </label>
            <label>
              <EnglishText en="Dimension value — Arabic (optional)" ar="قيمة البُعد — العربية (اختياري)" />
              <input aria-label="Dimension value Arabic (optional)" value={valueLabelAr} onChange={(event) => setValueLabelAr(event.target.value)} dir="auto" />
            </label>
            <button type="submit" disabled={dimensions.length === 0}>Add draft dimension value</button>
            {dimensionValues.length > 0 && (
              <ul aria-label="Local draft dimension values">
                {dimensionValues.map((value) => <li key={value.code}>{value.code} · {value.labelEn}{value.labelAr && <> · <span lang="ar" dir="rtl">{value.labelAr}</span></>}</li>)}
              </ul>
            )}
            </form>
          </div>
        </div>
      </section>

      <section aria-labelledby="draft-journal-title">
        <h2 id="draft-journal-title"><EnglishText en="Draft journal entry review" ar="مراجعة مسودة قيد يومية" /></h2>
        {worksheetKind === "opening_balance" && <aside className="finance-draft-boundary">
          <strong>Opening-balance worksheet · synthetic proposal only</strong>
          <p>This worksheet is not an import, opening journal, approval, or ledger posting. It is saved only in the disposable Finance lab.</p>
        </aside>}
        {adjustmentKind && <aside className="finance-draft-boundary">
          <strong>{adjustmentKind === "accrual_adjustment" ? "Accrual adjustment" : "Prepayment adjustment"} · explicit proposal only</strong>
          <p>This is an explicitly dated and explained proposal for review only. It does not recognize periods, calculate allocations, map accounts, schedule, approve, import, or post anything.</p>
        </aside>}
        <p>QAR is used only as the existing synthetic test invariant here; this is not production currency configuration.</p>
        <form className="finance-draft-journal" onSubmit={reviewDraft}>
          {worksheetKind === "opening_balance" && <label>
            <EnglishText en="As-of date (required)" ar="التاريخ المرجعي (مطلوب)" />
            <input aria-label="Opening balance as-of date" type="date" value={asOfDate}
              onChange={(event) => { setAsOfDate(event.target.value); setReview(null); onDraftChange?.(); }} required />
          </label>}
          {adjustmentKind && <>
            <label>
              Proposed date (required)
              <input aria-label="Proposed adjustment date" type="date" value={adjustmentDate}
                onChange={(event) => { setAdjustmentDate(event.target.value); setReview(null); onDraftChange?.(); }} required />
            </label>
            <label>
              Explicit explanation (required, up to 2,000 characters)
              <textarea aria-label="Adjustment explanation" value={adjustmentExplanation} maxLength={2000}
                onChange={(event) => { setAdjustmentExplanation(event.target.value); setReview(null); onDraftChange?.(); }}
                required />
            </label>
          </>}
          <label>
            <EnglishText en="Draft title — English" ar="عنوان المسودة — الإنجليزية" />
            <input aria-label="Draft journal title English" value={titleEn} onChange={(event) => { setTitleEn(event.target.value); setReview(null); }} required />
          </label>
          <label>
            <EnglishText en="Draft title — Arabic (optional)" ar="عنوان المسودة — العربية (اختياري)" />
            <input aria-label="Draft journal title Arabic (optional)" value={titleAr} onChange={(event) => { setTitleAr(event.target.value); setReview(null); }} dir="auto" />
          </label>
          <div className="finance-draft-lines">
            {lines.map((line, index) => (
              <fieldset className="finance-draft-line" key={index}>
                <legend><EnglishText en={`Draft line ${index + 1}`} ar={`سطر المسودة ${index + 1}`} /></legend>
                <label>
                  <EnglishText en="Draft account" ar="حساب المسودة" />
                  <select aria-label={`Draft account line ${index + 1}`} value={line.accountCode} onChange={(event) => updateLine(index, { accountCode: event.target.value })}>
                    <option value="">Select a proposed account</option>
                    {accounts.map((account) => <option key={account.code} value={account.code}>{account.code} · {account.nameEn}</option>)}
                  </select>
                </label>
                <label>
                  <EnglishText en="Optional business-unit dimension" ar="بُعد وحدة أعمال اختياري" />
                  <select aria-label={`Business unit line ${index + 1}`} value={line.businessUnitCode} onChange={(event) => updateLine(index, { businessUnitCode: event.target.value })}>
                    <option value="">No BU dimension</option>
                    {businessUnits.map((unit) => <option key={unit.code} value={unit.code}>{unit.code} · {unit.nameEn}</option>)}
                  </select>
                </label>
                <label>
                  <EnglishText en="Optional draft dimension value" ar="قيمة بُعد مسودة اختيارية" />
                  <select multiple aria-label={`Dimension value line ${index + 1}`} value={line.dimensionValueCodes ?? (line.dimensionValueCode ? [line.dimensionValueCode] : [])} onChange={(event) => {
                    const codes = Array.from(event.target.selectedOptions).map((option) => option.value).filter(Boolean);
                    updateLine(index, { dimensionValueCode: codes[0] ?? "", dimensionValueCodes: codes });
                  }}>
                    <option value="">No draft dimension</option>
                    {dimensionValues.map((value) => <option key={value.code} value={value.code}>{value.labelEn}</option>)}
                  </select>
                </label>
                <div className="finance-draft-amounts">
                  <label>
                    <EnglishText en="Debit (QAR test)" ar="مدين (اختبار ر.ق)" />
                    <input aria-label={`Debit line ${index + 1}`} inputMode="decimal" value={line.debit} onChange={(event) => updateLine(index, { debit: event.target.value })} />
                  </label>
                  <label>
                    <EnglishText en="Credit (QAR test)" ar="دائن (اختبار ر.ق)" />
                    <input aria-label={`Credit line ${index + 1}`} inputMode="decimal" value={line.credit} onChange={(event) => updateLine(index, { credit: event.target.value })} />
                  </label>
                </div>
                <label>
                  <EnglishText en="Line memo — English" ar="وصف السطر — الإنجليزية" />
                  <input aria-label={`Line memo English ${index + 1}`} value={line.memoEn} onChange={(event) => updateLine(index, { memoEn: event.target.value })} />
                </label>
                <label>
                  <EnglishText en="Line memo — Arabic (optional)" ar="وصف السطر — العربية (اختياري)" />
                  <input aria-label={`Line memo Arabic (optional) ${index + 1}`} value={line.memoAr} onChange={(event) => updateLine(index, { memoAr: event.target.value })} dir="auto" />
                </label>
                {lines.length > 2 && <button type="button" onClick={() => { setLines((current) => current.filter((_, lineIndex) => lineIndex !== index)); setReview(null); onDraftChange?.(); }}>Remove line</button>}
              </fieldset>
            ))}
          </div>
          <div className="finance-draft-actions">
            <button type="button" onClick={() => { setLines((current) => [...current, emptyLine()]); setReview(null); onDraftChange?.(); }}>Add draft line</button>
            <button type="submit">Review draft balance</button>
            <button type="button" disabled={!!saveUnavailableReason || !review || review.issues.length > 0} onClick={saveLocalDraft}>{onSave ? "Save draft to disposable database" : "Save in this local session"}</button>
          </div>
          {saveUnavailableReason && <p role="alert">{saveUnavailableReason}</p>}
        </form>
        {review && (
          <div className="finance-draft-review" role="status" aria-live="polite">
            <h3><EnglishText en="Read-only draft review" ar="مراجعة مسودة للقراءة فقط" /></h3>
            <p>Debit total: <strong>{review.debitTotal}</strong> QAR · Credit total: <strong>{review.creditTotal}</strong> QAR</p>
            {(worksheetKind === "opening_balance" || adjustmentKind) && <p>Difference (debit − credit): <strong>{review.difference}</strong> QAR</p>}
            <p>{review.issues.length === 0
              ? "Balanced draft — not approved or posted."
              : "Draft needs correction; it remains unapproved and unposted."}</p>
            {review.issues.length > 0 && <ul>{review.issues.map((issue) => <li key={issue.code}>{issue.en}</li>)}</ul>}
            <div className="finance-draft-table-scroll" tabIndex={0} role="region" aria-label="Draft line review">
              <table>
                <caption>Draft line detail — not a posted ledger</caption>
                <thead><tr>
                  <th scope="col">Line</th><th scope="col">Proposed account</th>
                  <th scope="col">BU</th><th scope="col">Draft dimension</th>
                  <th scope="col">Debit</th><th scope="col">Credit</th><th scope="col">Memo</th>
                </tr></thead>
                <tbody>{lines.map((line, index) => {
                  const account = accounts.find((item) => item.code === line.accountCode);
                  const unit = businessUnits.find((item) => item.code === line.businessUnitCode);
                  const values = dimensionValues.filter((item) => (line.dimensionValueCodes ?? [line.dimensionValueCode]).includes(item.code));
                  return <tr key={index}>
                    <th scope="row">{index + 1}</th>
                    <td>{account ? `${account.code} · ${account.nameEn}` : "Not selected"}{account?.nameAr && <div lang="ar" dir="rtl">{account.nameAr}</div>}</td>
                    <td>{unit ? `${unit.code} · ${unit.nameEn}` : "Not selected"}{unit?.nameAr && <div lang="ar" dir="rtl">{unit.nameAr}</div>}</td>
                    <td>{values.length ? values.map((value) => <div key={value.code}>{value.labelEn}{value.labelAr && <span lang="ar" dir="rtl"> · {value.labelAr}</span>}</div>) : "Not selected"}</td>
                    <td>{line.debit}</td><td>{line.credit}</td>
                    <td>{line.memoEn}{line.memoAr && <div lang="ar" dir="rtl">{line.memoAr}</div>}</td>
                  </tr>;
                })}</tbody>
              </table>
            </div>
          </div>
        )}
        {savedDrafts.length > 0 && (
          <section className="finance-draft-saved" aria-label="Session-only synthetic drafts">
            <h3>Local session drafts only</h3>
            <ul>{savedDrafts.map((draft, index) => <li key={`${draft.titleEn}-${index}`}>{draft.titleEn}{draft.titleAr && <> · <span lang="ar" dir="rtl">{draft.titleAr}</span></>} · {draft.debitTotal} QAR = {draft.creditTotal} QAR · not posted</li>)}</ul>
          </section>
        )}
      </section>
    </main>
  );
}

function dimensionLabelExists(code: string, dimensions: DraftDimension[]) {
  return dimensions.some((dimension) => dimension.code === code);
}
