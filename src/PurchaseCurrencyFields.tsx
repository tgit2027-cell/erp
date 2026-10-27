// Owner decision P1 (provisional): purchases may be priced in QAR, USD, EUR or GBP.
// The server validates the currency, rate, source and date; stock stays valued in QAR.
export const PURCHASE_CURRENCIES = ["QAR", "USD", "EUR", "GBP"] as const;

export type ExchangeDraft = { currency: string; rate: string; source: string; date: string };

export const qarExchange: ExchangeDraft = { currency: "QAR", rate: "", source: "", date: "" };

export function exchangePayload(draft: ExchangeDraft): Record<string, string> {
  if (draft.currency === "QAR") return {};
  return { currency: draft.currency, exchange_rate: draft.rate.trim(), rate_source: draft.source.trim(), rate_date: draft.date };
}

export function exchangeProblem(draft: ExchangeDraft): string | null {
  if (draft.currency === "QAR") return null;
  if (!/^\d+(\.\d{1,8})?$/.test(draft.rate.trim()) || Number(draft.rate) <= 0) return `Enter how many QAR one ${draft.currency} is worth (up to 8 decimals).`;
  if (!draft.source.trim()) return "Say where the exchange rate comes from.";
  if (!draft.date) return "Enter the exchange-rate date.";
  return null;
}

export default function PurchaseCurrencyFields({ idPrefix, value, onChange, disabled, lockedCurrency }: {
  idPrefix: string;
  value: ExchangeDraft;
  onChange: (next: ExchangeDraft) => void;
  disabled?: boolean;
  lockedCurrency?: string;
}) {
  const currency = lockedCurrency ?? value.currency;
  return (
    <fieldset className="pw-currency-fields" disabled={disabled}>
      <legend>Purchase currency</legend>
      <label htmlFor={`${idPrefix}-currency`}>Currency
        <select id={`${idPrefix}-currency`} value={currency} disabled={disabled || Boolean(lockedCurrency)}
          onChange={(event) => onChange({ ...value, currency: event.target.value })}>
          {PURCHASE_CURRENCIES.map((code) => <option key={code} value={code}>{code}</option>)}
        </select>
      </label>
      {lockedCurrency && <p className="pw-action-note">The currency is fixed by the purchase commitment.</p>}
      {currency !== "QAR" && <>
        <label htmlFor={`${idPrefix}-rate`}>QAR per 1 {currency}
          <input id={`${idPrefix}-rate`} inputMode="decimal" required value={value.rate} placeholder="e.g. 3.64"
            onChange={(event) => onChange({ ...value, currency, rate: event.target.value })} />
        </label>
        <label htmlFor={`${idPrefix}-source`}>Rate source
          <input id={`${idPrefix}-source`} required maxLength={120} value={value.source} placeholder="e.g. bank rate on the invoice date"
            onChange={(event) => onChange({ ...value, currency, source: event.target.value })} />
        </label>
        <label htmlFor={`${idPrefix}-date`}>Rate date
          <input id={`${idPrefix}-date`} type="date" required value={value.date}
            onChange={(event) => onChange({ ...value, currency, date: event.target.value })} />
        </label>
        <p className="pw-action-note">Supplier prices are entered in {currency}. Stock and journals are recorded in QAR at this rate. Each landed charge has its own currency below.</p>
      </>}
    </fieldset>
  );
}

// Owner decision P1 (provisional): shipping may be billed in EUR or USD; customs clearance is always QAR.
export const CHARGE_CURRENCIES = ["QAR", "USD", "EUR"] as const;

export function chargeExchangePayload(category: string, draft: ExchangeDraft): Record<string, string> {
  return category === "clearance" ? {} : exchangePayload(draft);
}

export function ChargeCurrencyFields({ idPrefix, category, value, onChange, disabled }: {
  idPrefix: string;
  category: string;
  value: ExchangeDraft;
  onChange: (next: ExchangeDraft) => void;
  disabled?: boolean;
}) {
  const qarOnly = category === "clearance";
  const currency = qarOnly ? "QAR" : value.currency;
  return (
    <>
      <label htmlFor={`${idPrefix}-currency`}>Currency
        <select id={`${idPrefix}-currency`} value={currency} disabled={disabled || qarOnly}
          onChange={(event) => onChange({ ...value, currency: event.target.value })}>
          {CHARGE_CURRENCIES.map((code) => <option key={code} value={code}>{code}</option>)}
        </select>
      </label>
      {currency !== "QAR" && <>
        <label htmlFor={`${idPrefix}-rate`}>QAR per 1 {currency}
          <input id={`${idPrefix}-rate`} inputMode="decimal" required value={value.rate} placeholder="e.g. 3.64" disabled={disabled}
            onChange={(event) => onChange({ ...value, rate: event.target.value })} />
        </label>
        <label htmlFor={`${idPrefix}-source`}>Rate source
          <input id={`${idPrefix}-source`} required maxLength={120} value={value.source} placeholder="e.g. freight invoice rate" disabled={disabled}
            onChange={(event) => onChange({ ...value, source: event.target.value })} />
        </label>
        <label htmlFor={`${idPrefix}-date`}>Rate date
          <input id={`${idPrefix}-date`} type="date" required value={value.date} disabled={disabled}
            onChange={(event) => onChange({ ...value, date: event.target.value })} />
        </label>
      </>}
    </>
  );
}
