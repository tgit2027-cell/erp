export type QuoteLineInput = {
  variantId: string;
  locationId: string;
  quantity: string;
  specifications: string;
  unit_en?: string;
  unit_ar?: string;
};

export type PresentationLineUnit = { en: string; ar: string };

export function presentationLineUnits(lines: QuoteLineInput[]): PresentationLineUnit[] | undefined {
  if (!lines.some((line) => Boolean(line.unit_en?.trim() || line.unit_ar?.trim()))) return undefined;
  return lines.map((line) => ({
    en: (line.unit_en ?? "").trim(),
    ar: (line.unit_ar ?? "").trim(),
  }));
}

export type QuoteLineEstimate = {
  listTotalCents: bigint;
  netUnitCents: bigint;
  netTotalCents: bigint;
  discountAmountCents: bigint;
};

export type QuoteLineValidation = {
  ok: true;
  lines: QuoteLineEstimate[];
  totalCents: bigint;
} | {
  ok: false;
  error: string;
};

function decimalUnits(value: string, label: string, lineNumber: number, maxIntegerDigits: number): bigint {
  const text = value.trim();
  const match = text.match(/^(\d+)(?:\.(\d{1,2}))?$/);
  if (!match) {
    throw new Error(`Line ${lineNumber}: ${label} must be a non-negative number with no more than two decimal places.`);
  }
  const integer = match[1].replace(/^0+/, "") || "0";
  if (integer.length > maxIntegerDigits) {
    throw new Error(`Line ${lineNumber}: ${label} exceeds the ${maxIntegerDigits}-digit whole-number storage limit.`);
  }
  return BigInt(integer) * 100n + BigInt((match[2] ?? "").padEnd(2, "0"));
}

function roundedDivide(numerator: bigint, denominator: bigint): bigint {
  return (numerator + denominator / 2n) / denominator;
}

function assertFitsCents(cents: bigint, maxIntegerDigits: number, label: string, lineNumber?: number) {
  if (cents >= 10n ** BigInt(maxIntegerDigits + 2)) {
    const prefix = lineNumber ? `Line ${lineNumber}: ` : "";
    throw new Error(`${prefix}${label} exceeds its ${maxIntegerDigits}-digit whole-number storage limit.`);
  }
}

export function formatQarCents(cents: bigint): string {
  const absolute = cents < 0n ? -cents : cents;
  const formatted = `${absolute / 100n}.${String(absolute % 100n).padStart(2, "0")}`;
  return cents < 0n ? `-${formatted}` : formatted;
}

export function validateQuoteLineEstimates(
  lines: QuoteLineInput[],
  prices: Array<{ variant_id: number; amount: string | number; currency: string }>,
  discountPercent: string,
  kind: "stock" | "special_order",
  authorizedLocationIds: number[],
): QuoteLineValidation {
  if (lines.length < 1 || lines.length > 40) {
    return { ok: false, error: "A quote must contain between one and 40 lines." };
  }
  try {
    const discountHundredths = decimalUnits(discountPercent, "Discount", 1, 3);
    if (discountHundredths > 5000n) {
      return { ok: false, error: "Discount must be between 0.00% and 50.00%." };
    }
    const authorized = new Set(authorizedLocationIds);
    const estimates = lines.map((line, index) => {
      const number = index + 1;
      const variantId = Number(line.variantId);
      if (!Number.isSafeInteger(variantId) || variantId <= 0) {
        throw new Error(`Line ${number}: select a current approved variant / price snapshot.`);
      }
      const price = prices.find((candidate) => candidate.variant_id === variantId);
      if (!price || price.currency !== "QAR") {
        throw new Error(`Line ${number}: this variant has no current QAR price snapshot. Select a currently configured variant.`);
      }
      const quantity = decimalUnits(line.quantity, "Quantity", number, 10);
      if (quantity <= 0n) throw new Error(`Line ${number}: quantity must be greater than 0.00.`);
      if (kind === "stock") {
        const locationId = Number(line.locationId);
        if (!Number.isSafeInteger(locationId) || locationId <= 0 || !authorized.has(locationId)) {
          throw new Error(`Line ${number}: select an authorized stock location available in the current business unit.`);
        }
      }
      let specifications: unknown;
      try {
        specifications = JSON.parse(line.specifications);
      } catch {
        throw new Error(`Line ${number}: specifications must be valid JSON.`);
      }
      if (!specifications || typeof specifications !== "object" || Array.isArray(specifications)) {
        throw new Error(`Line ${number}: specifications must be a JSON object.`);
      }
      if (typeof price.amount !== "string") {
        throw new Error(`Line ${number}: the current price snapshot is not an exact decimal string. Refresh configuration before quoting.`);
      }
      const priceCents = decimalUnits(price.amount, "Current unit price", number, 12);
      if (priceCents <= 0n) throw new Error(`Line ${number}: the current approved unit price must be greater than 0.00.`);
      const listTotalCents = roundedDivide(priceCents * quantity, 100n);
      const netUnitCents = roundedDivide(priceCents * (10000n - discountHundredths), 10000n);
      const netTotalCents = roundedDivide(netUnitCents * quantity, 100n);
      const discountAmountCents = listTotalCents - netTotalCents;
      assertFitsCents(netUnitCents, 12, "Net unit price", number);
      assertFitsCents(listTotalCents, 14, "Line list total", number);
      assertFitsCents(netTotalCents, 14, "Line net total", number);
      assertFitsCents(discountAmountCents, 12, "Line discount amount", number);
      return { listTotalCents, netUnitCents, netTotalCents, discountAmountCents };
    });
    const totals = estimates.reduce((total, estimate, index) => {
      const lineNumber = index + 1;
      const next = {
        list: total.list + estimate.listTotalCents,
        discount: total.discount + estimate.discountAmountCents,
        net: total.net + estimate.netTotalCents,
      };
      assertFitsCents(next.list, 14, "Quote list total", lineNumber);
      assertFitsCents(next.discount, 14, "Quote discount total", lineNumber);
      assertFitsCents(next.net, 14, "Quote total", lineNumber);
      return next;
    }, { list: 0n, discount: 0n, net: 0n });
    return {
      ok: true,
      lines: estimates,
      totalCents: totals.net,
    };
  } catch (reason) {
    return { ok: false, error: reason instanceof Error ? reason.message : "Review each quote line and try again." };
  }
}
