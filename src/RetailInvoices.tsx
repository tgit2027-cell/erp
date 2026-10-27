import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import "./retail-collections.css";
import "./retail-invoices.css";

type Money = string;
type InvoiceSummary = {
  invoice_id: number;
  number: string;
  quote_id: number;
  customer: { code: string; name: string };
  issued_at: string;
  currency: string;
  list_total: Money;
  discount_total: Money;
  net_total: Money;
  tax_rate_percent: Money;
  tax_total: Money;
  invoice_total: Money;
  paid_before_delivery: Money;
  balance_due: Money;
};
type InvoiceLine = {
  item_code: string;
  description: string;
  brand_name: string;
  quantity: string;
  list_unit_price: Money;
  discount_percent: string;
  net_unit_price: Money;
  line_total: Money;
  journal_entry_ids: number[];
};
type InvoiceDetail = InvoiceSummary & {
  business_unit: { id: number; code: string; name: string };
  issued_by: string;
  policy_label: string;
  lines: InvoiceLine[];
};
type ListData = {
  business_unit: { id: number; code: string; name: string };
  rows: InvoiceSummary[];
  pagination: { page: number; total_count: number; total_pages: number; previous_page: number | null; next_page: number | null };
  test_only_label: string;
};
type Load<T> =
  | { status: "loading"; data: null; error: ""; denied: false }
  | { status: "ready"; data: T; error: ""; denied: false }
  | { status: "error"; data: null; error: string; denied: boolean };

const loading = { status: "loading", data: null, error: "", denied: false } as const;

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { method: "GET", credentials: "same-origin", headers: { Accept: "application/json" } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof body?.detail === "string" ? body.detail : `Request unavailable (${response.status}).`) as Error & { status: number };
    error.status = response.status;
    throw error;
  }
  return body as T;
}

function failure(reason: unknown, fallback: string) {
  const error = reason as Error & { status?: number };
  return {
    status: "error" as const, data: null, error: error.message || fallback,
    denied: error.status === 401 || error.status === 403 || error.status === 404,
  };
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleDateString();
}

function InvoiceDocument({ invoice, onClose }: { invoice: InvoiceDetail; onClose: () => void }) {
  const money = (value: Money) => `${invoice.currency} ${value}`;
  return (
    <article className="ri-document" aria-labelledby="ri-document-title">
      <header className="ri-document-head">
        <div>
          <span className="rc-kicker">{invoice.business_unit.name.toUpperCase()} · CUSTOMER INVOICE</span>
          <h3 id="ri-document-title">{invoice.number}</h3>
          <p>Issued {formatDate(invoice.issued_at)} by {invoice.issued_by} · quote #{invoice.quote_id}</p>
        </div>
        <div className="ri-document-actions">
          <button type="button" className="rc-button" onClick={() => window.print()}>Print invoice</button>
          <button type="button" className="rc-button" onClick={onClose}>Back to list</button>
        </div>
      </header>
      <dl className="ri-parties">
        <div><dt>Bill to</dt><dd><strong>{invoice.customer.name}</strong><small>{invoice.customer.code}</small></dd></div>
        <div><dt>Seller</dt><dd><strong>{invoice.business_unit.name}</strong><small>{invoice.business_unit.code}</small></dd></div>
      </dl>
      <div className="rc-table-scroll">
        <table className="rc-table ri-lines">
          <caption className="rc-visually-hidden">Invoice lines for {invoice.number}</caption>
          <thead><tr>
            <th scope="col">Item</th><th scope="col">Quantity</th><th scope="col">List price</th>
            <th scope="col">Discount</th><th scope="col">Unit price</th><th scope="col">Line total</th>
          </tr></thead>
          <tbody>{invoice.lines.map((line) => (
            <tr key={`${line.item_code}-${line.journal_entry_ids.join("-")}`}>
              <td><strong>{line.description}</strong><small>{line.item_code} · {line.brand_name}</small></td>
              <td className="rc-number">{line.quantity}</td>
              <td className="rc-number">{money(line.list_unit_price)}</td>
              <td className="rc-number">{line.discount_percent}%</td>
              <td className="rc-number">{money(line.net_unit_price)}</td>
              <td className="rc-number">{money(line.line_total)}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <dl className="ri-totals">
        <div><dt>List value</dt><dd>{money(invoice.list_total)}</dd></div>
        <div><dt>Discount</dt><dd>− {money(invoice.discount_total)}</dd></div>
        <div><dt>Net</dt><dd>{money(invoice.net_total)}</dd></div>
        <div><dt>Tax ({invoice.tax_rate_percent}%)</dt><dd>{money(invoice.tax_total)}</dd></div>
        <div className="ri-total"><dt>Invoice total</dt><dd>{money(invoice.invoice_total)}</dd></div>
        <div><dt>Paid before delivery</dt><dd>− {money(invoice.paid_before_delivery)}</dd></div>
        <div className="ri-due"><dt>Balance due</dt><dd>{money(invoice.balance_due)}</dd></div>
      </dl>
      <small className="rc-test-label">{invoice.policy_label}</small>
    </article>
  );
}

export default function RetailInvoices({
  selectedUnitId,
  selectedUnitName,
  username,
  refreshCounter,
}: {
  selectedUnitId: number;
  selectedUnitName: string;
  username: string;
  refreshCounter: number;
}) {
  const scope = `${selectedUnitId}:${username}`;
  const [numberInput, setNumberInput] = useState("");
  const [submittedNumber, setSubmittedNumber] = useState("");
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState(0);
  const [list, setList] = useState<{ scope: string; state: Load<ListData> }>(() => ({ scope, state: loading }));
  const [openId, setOpenId] = useState<number | null>(null);
  const [detail, setDetail] = useState<{ key: string; state: Load<InvoiceDetail> } | null>(null);
  const currentScope = useRef(scope);
  const listVersion = useRef(0);
  const listState: Load<ListData> = list.scope === scope ? list.state : loading;
  const detailKey = `${scope}:${openId}`;
  const detailState: Load<InvoiceDetail> | null = openId === null ? null : detail?.key === detailKey ? detail.state : loading;

  // Hide a previous unit or user synchronously on its first render.
  if (currentScope.current !== scope) {
    currentScope.current = scope;
    setList({ scope, state: loading });
    setPage(1);
    setNumberInput("");
    setSubmittedNumber("");
    setOpenId(null);
  }

  useEffect(() => {
    const version = ++listVersion.current;
    let active = true;
    const query = new URLSearchParams({ business_unit_id: String(selectedUnitId), page: String(page), page_size: "20" });
    if (submittedNumber) query.set("number", submittedNumber);
    setList({ scope, state: loading });
    getJson<ListData>(`/api/purchasing/invoices/?${query.toString()}`)
      .then((data) => {
        if (active && listVersion.current === version) setList({ scope, state: { status: "ready", data, error: "", denied: false } });
      })
      .catch((reason: unknown) => {
        if (active && listVersion.current === version) setList({ scope, state: failure(reason, "Customer invoices are unavailable.") });
      });
    return () => { active = false; };
  }, [selectedUnitId, scope, page, submittedNumber, refreshCounter, retry]);

  useEffect(() => {
    if (openId === null) return;
    let active = true;
    const key = `${scope}:${openId}`;
    getJson<InvoiceDetail>(`/api/purchasing/invoices/${openId}/?business_unit_id=${selectedUnitId}`)
      .then((data) => { if (active) setDetail({ key, state: { status: "ready", data, error: "", denied: false } }); })
      .catch((reason: unknown) => { if (active) setDetail({ key, state: failure(reason, "This invoice is unavailable.") }); });
    return () => { active = false; };
  }, [openId, scope, selectedUnitId]);

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPage(1);
    setSubmittedNumber(numberInput.trim());
  }

  return (
    <section className="retail-collections retail-invoices" aria-labelledby="retail-invoices-title">
      <header className="rc-heading">
        <div>
          <span className="rc-kicker">RETAIL · INVOICES · READ ONLY</span>
          <h2 id="retail-invoices-title">Customer invoices</h2>
          <p>Invoices issued when quotations were delivered in <strong>{selectedUnitName}</strong>.</p>
        </div>
        <span className="rc-stamp">TEST ONLY<br />PROVISIONAL</span>
      </header>
      <aside className="rc-caveat">
        <strong>Provisional invoice rules.</strong>
        <span>An invoice is issued automatically at delivery. Payments taken before delivery are shown as applied, and no tax is charged until Finance and legal confirm the rules.</span>
      </aside>

      {detailState && openId !== null ? <>
        {detailState.status === "loading" && <div className="rc-loading" role="status" aria-label="Loading invoice"><i /><i /><i /></div>}
        {detailState.status === "error" && <div className="rc-message rc-error" role="alert">
          <strong>{detailState.denied ? "Invoice not authorized" : "Invoice unavailable"}</strong>
          <p>{detailState.denied ? "The server did not authorize this invoice." : detailState.error}</p>
          <button type="button" className="rc-button" onClick={() => setOpenId(null)}>Back to list</button>
        </div>}
        {detailState.status === "ready" && <InvoiceDocument invoice={detailState.data} onClose={() => setOpenId(null)} />}
      </> : <>
        <div className="rc-controls">
          <form onSubmit={search} className="rc-search">
            <label htmlFor="ri-number-search">Invoice number</label>
            <div>
              <input id="ri-number-search" value={numberInput} onChange={(event) => setNumberInput(event.target.value)} placeholder="e.g. TVL-INV-2026-00001" />
              <button type="submit" className="rc-button rc-button-primary">Search</button>
              {submittedNumber && <button type="button" className="rc-button" onClick={() => { setNumberInput(""); setSubmittedNumber(""); setPage(1); }}>Clear search</button>}
            </div>
            <small>Enter the full invoice number for an exact match.</small>
          </form>
          <button type="button" className="rc-button rc-refresh" onClick={() => setRetry((value) => value + 1)} disabled={listState.status === "loading"}>Refresh list</button>
        </div>
        {listState.status === "loading" && <div className="rc-loading" role="status" aria-label="Loading customer invoices"><i /><i /><i /></div>}
        {listState.status === "error" && <div className="rc-message rc-error" role={listState.denied ? "status" : "alert"}>
          <strong>{listState.denied ? "Invoice view not authorized" : "Invoice data unavailable"}</strong>
          <p>{listState.denied ? "The server did not authorize this read-only view. No invoices are displayed." : listState.error}</p>
          {!listState.denied && <button type="button" className="rc-button" onClick={() => setRetry((value) => value + 1)}>Retry</button>}
        </div>}
        {listState.status === "ready" && listState.data.rows.length === 0 && (
          <div className="rc-empty"><span>NO INVOICES</span><strong>No invoices returned</strong><p>{submittedNumber ? `No invoice numbered ${submittedNumber} in this unit.` : "Invoices appear here once a quotation is delivered."}</p></div>
        )}
        {listState.status === "ready" && listState.data.rows.length > 0 && <>
          <div className="rc-table-scroll">
            <table className="rc-table">
              <caption className="rc-visually-hidden">Customer invoices for {listState.data.business_unit.name}</caption>
              <thead><tr>
                <th scope="col">Invoice</th><th scope="col">Issued</th><th scope="col">Customer</th><th scope="col">Quote</th>
                <th scope="col">Invoice total</th><th scope="col">Paid before delivery</th><th scope="col">Balance due</th><th scope="col">Action</th>
              </tr></thead>
              <tbody>{listState.data.rows.map((row) => (
                <tr key={row.invoice_id}>
                  <th scope="row" className="rc-id">{row.number}</th>
                  <td>{formatDate(row.issued_at)}</td>
                  <td><strong>{row.customer.name}</strong><small>{row.customer.code}</small></td>
                  <td className="rc-id">#{row.quote_id}</td>
                  <td className="rc-number">{row.currency} {row.invoice_total}</td>
                  <td className="rc-number">{row.currency} {row.paid_before_delivery}</td>
                  <td className="rc-number rc-balance">{row.currency} {row.balance_due}</td>
                  <td><button type="button" className="rc-row-action" onClick={() => setOpenId(row.invoice_id)}>Open invoice</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <nav className="rc-pager" aria-label="Customer invoice pages">
            <span>Page <strong>{listState.data.pagination.page}</strong> of <strong>{listState.data.pagination.total_pages}</strong></span>
            <div>
              <button type="button" className="rc-button" aria-label="Previous page" disabled={!listState.data.pagination.previous_page} onClick={() => listState.data.pagination.previous_page && setPage(listState.data.pagination.previous_page)}>Previous</button>
              <button type="button" className="rc-button" aria-label="Next page" disabled={!listState.data.pagination.next_page} onClick={() => listState.data.pagination.next_page && setPage(listState.data.pagination.next_page)}>Next</button>
            </div>
          </nav>
          <small className="rc-test-label">{listState.data.test_only_label}</small>
        </>}
      </>}
    </section>
  );
}
