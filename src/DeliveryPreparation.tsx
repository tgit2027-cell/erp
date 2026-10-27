import { useState } from "react";
import type { Quote } from "./PurchasingWorkbench";
import "./delivery-preparation.css";

type DeliveryLine = NonNullable<Quote["delivery_progress"]>["lines"][number];

function display(value: string | number | null | undefined): string {
  return value === null || value === undefined || value === "" ? "Not returned" : String(value);
}

function isZero(value: string): boolean {
  const normalized = value.trim().replace(/^[+-]/, "");
  if (!/^\d+(?:\.\d+)?$/.test(normalized)) return false;
  const [whole, fraction = ""] = normalized.split(".");
  return /^0+$/.test(whole) && (fraction === "" || /^0+$/.test(fraction));
}

function specificationsText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "No specifications returned";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function freshnessSnapshot(quote: Quote): string {
  try {
    return JSON.stringify(quote);
  } catch {
    return `${quote.quote_id}:${quote.lines.map((line) => line.quote_line_id).join(",")}`;
  }
}

export default function DeliveryPreparation({
  quote,
  selectedUnitId,
  onOpenHandover,
}: {
  quote: Quote | null;
  selectedUnitId: number;
  onOpenHandover: () => void;
}) {
  if (!quote) {
    return (
      <section className="delivery-preparation" aria-labelledby="delivery-preparation-title">
        <header className="dp-header">
          <div>
            <span className="dp-eyebrow">DELIVERY · REVIEW WORKSPACE</span>
            <h1 id="delivery-preparation-title">Delivery preparation</h1>
            <p>Review the returned progress for an already-authorized quote before opening handover.</p>
          </div>
          <span className="dp-readonly">REVIEW ONLY</span>
        </header>
        <div className="dp-empty" role="status">
          <span className="dp-empty-index">01</span>
          <h2>No authorized quote in view</h2>
          <p>Request an authorized quote above to review its lines.</p>
          <button className="dp-button dp-button-primary" type="button" onClick={onOpenHandover}>
            Open handover
            <span aria-hidden="true">→</span>
          </button>
        </div>
      </section>
    );
  }

  const snapshot = freshnessSnapshot(quote);
  const contextKey = JSON.stringify([selectedUnitId, quote.quote_id, snapshot]);
  return (
    <PreparationForQuote
      key={contextKey}
      quote={quote}
      selectedUnitId={selectedUnitId}
      onOpenHandover={onOpenHandover}
    />
  );
}

function PreparationForQuote({
  quote,
  selectedUnitId,
  onOpenHandover,
}: {
  quote: Quote;
  selectedUnitId: number;
  onOpenHandover: () => void;
}) {
  const [reviewed, setReviewed] = useState<Record<number, boolean>>({});
  const progressByLine = new Map<number, DeliveryLine>(
    (quote.delivery_progress?.lines ?? []).map((line) => [line.quote_line_id, line]),
  );
  const lineCount = quote.lines?.length ?? 0;
  const reviewedCount = quote.lines?.filter((line) => reviewed[line.quote_line_id]).length ?? 0;
  const readyAt = quote.delivery_progress?.ready_at;

  return (
    <section className="delivery-preparation" aria-labelledby="delivery-preparation-title">
      <header className="dp-header">
        <div>
          <span className="dp-eyebrow">DELIVERY · AUTHORIZED QUOTE · REVIEW WORKSPACE</span>
          <h1 id="delivery-preparation-title">Delivery preparation</h1>
          <p>Check the returned line progress, note what you have reviewed, then continue to handover.</p>
        </div>
        <span className="dp-readonly">NO STOCK POSTING</span>
      </header>

      <div className="dp-quote-banner">
        <div className="dp-quote-mark" aria-hidden="true">D</div>
        <div className="dp-quote-identity">
          <span className="dp-label">AUTHORIZED QUOTE</span>
          <h2>Quote <span className="dp-mono">#{quote.quote_id}</span></h2>
          <p>Business unit <span className="dp-mono">{selectedUnitId}</span></p>
        </div>
        <div className="dp-quote-status">
          <span className="dp-label">QUOTE STATUS</span>
          <strong>{quote.status || "Not returned"}</strong>
        </div>
        <div className="dp-review-counter" aria-live="polite">
          <span className="dp-label">REVIEWED</span>
          <strong><span className="dp-mono">{reviewedCount}</span> / {lineCount}</strong>
          <small>line checks · this view only</small>
        </div>
      </div>

      <div className="dp-scope-note">
        <span className="dp-scope-mark" aria-hidden="true">i</span>
        <p>
          <strong>Checklist only.</strong> A checkmark records that a line was reviewed in this browser view.
          It does not confirm physical picking, move stock, reserve inventory, or dispatch a delivery.
        </p>
      </div>

      <section className="dp-progress-panel" aria-labelledby="dp-progress-title">
        <div className="dp-panel-heading">
          <div>
            <span className="dp-eyebrow">QUOTE RESPONSE · DELIVERY PROGRESS</span>
            <h2 id="dp-progress-title">Line-by-line review</h2>
            <p>Quantities are shown as returned. No quantity is recalculated or converted.</p>
          </div>
          <div className={`dp-ready-flag ${readyAt ? "is-recorded" : ""}`}>
            <span className="dp-ready-dot" aria-hidden="true" />
            <span>{readyAt ? "Readiness timestamp recorded" : "No readiness timestamp returned"}</span>
          </div>
        </div>

        {lineCount === 0 ? (
          <div className="dp-lines-empty" role="status">
            <strong>No quote lines returned</strong>
            <p>There are no line items available to review for this quote response.</p>
          </div>
        ) : (
          <div className="dp-line-list">
            {quote.lines.map((line, index) => {
              const progress = progressByLine.get(line.quote_line_id);
              const complete = progress ? isZero(progress.remaining_quantity) : false;
              const readiness = !progress
                ? "Progress unavailable"
                : complete
                  ? "Delivered in full"
                  : readyAt
                    ? "Readiness timestamp recorded"
                    : "Not marked ready";
              const stateClass = !progress ? "is-unknown" : complete ? "is-complete" : readyAt ? "is-ready" : "is-pending";
              const checkboxId = `dp-reviewed-${quote.quote_id}-${line.quote_line_id}`;
              return (
                <article className="dp-line" key={line.quote_line_id}>
                  <div className="dp-line-top">
                    <div className="dp-line-identity">
                      <span className="dp-line-index">{String(index + 1).padStart(2, "0")}</span>
                      <div>
                        <span className="dp-label">QUOTE LINE {line.quote_line_id}</span>
                        <h3>Variant <span className="dp-mono">{line.variant_id}</span></h3>
                      </div>
                    </div>
                    <span className={`dp-line-readiness ${stateClass}`}>{readiness}</span>
                  </div>

                  <dl className="dp-quantity-grid">
                    <div>
                      <dt>Ordered</dt>
                      <dd className="dp-mono">{display(line.quantity)}</dd>
                    </div>
                    <div>
                      <dt>Delivered</dt>
                      <dd className="dp-mono">{display(progress?.delivered_quantity)}</dd>
                    </div>
                    <div>
                      <dt>Remaining</dt>
                      <dd className="dp-mono">{display(progress?.remaining_quantity)}</dd>
                    </div>
                  </dl>

                  {line.preparation_identity ? (
                    <dl className="dp-quantity-grid" aria-label="Authorized stock identity">
                      <div><dt>Item</dt><dd>{line.preparation_identity.item_code} · {line.preparation_identity.description}</dd></div>
                      <div><dt>Variant / brand</dt><dd>{line.preparation_identity.sku} · {line.preparation_identity.brand}</dd></div>
                      <div><dt>Location</dt><dd>{line.preparation_identity.location_code} · {line.preparation_identity.location_name}</dd></div>
                    </dl>
                  ) : <p className="dp-unavailable">Inventory and location details unavailable under current access or without an authorized ledger match.</p>}

                  <details className="dp-specifications">
                    <summary>Variant specifications</summary>
                    <pre>{specificationsText(line.specifications)}</pre>
                  </details>

                  <label className="dp-review-check" htmlFor={checkboxId}>
                    <input
                      id={checkboxId}
                      type="checkbox"
                      aria-label={`Reviewed quote ${quote.quote_id}, line ${line.quote_line_id} — local review only`}
                      checked={Boolean(reviewed[line.quote_line_id])}
                      onChange={(event) => {
                        const checked = event.currentTarget.checked;
                        setReviewed((current) => ({ ...current, [line.quote_line_id]: checked }));
                      }}
                    />
                    <span className="dp-check-box" aria-hidden="true" />
                    <span className="dp-check-copy">
                      <strong>Reviewed</strong>
                      <small>Local checklist mark only</small>
                    </span>
                  </label>
                </article>
              );
            })}
          </div>
        )}
      </section>

      <section className="dp-location-note" aria-labelledby="dp-location-title">
        <div className="dp-location-copy">
          <span className="dp-eyebrow">SCOPE LIMIT</span>
          <h2 id="dp-location-title">Inventory and locations</h2>
          <p>
            Item and location details appear only when independently authorized by stock access
            and matched to this quote line's existing ledger bucket. They do not confirm current
            availability, allocation or permission to pick. Stored reservation coverage is historical,
            not current available stock.
          </p>
        </div>
      </section>

      <footer className="dp-footer">
        <p>Review marks reset when the business unit, quote, or returned quote snapshot changes.</p>
        <button className="dp-button dp-button-primary" type="button" onClick={onOpenHandover}>
          Continue to handover
          <span aria-hidden="true">→</span>
        </button>
      </footer>
    </section>
  );
}
