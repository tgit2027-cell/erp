import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import PurchaseCurrencyFields, { ChargeCurrencyFields, chargeExchangePayload, exchangePayload, exchangeProblem, qarExchange } from "../src/PurchaseCurrencyFields";
import type { ExchangeDraft } from "../src/PurchaseCurrencyFields";

let root: ReturnType<typeof createRoot> | null = null;
let latest: ExchangeDraft = qarExchange;

afterEach(() => { if (root) act(() => root!.unmount()); root = null; document.body.innerHTML = ""; });

function Harness({ locked }: { locked?: string }) {
  const [value, setValue] = useState<ExchangeDraft>(qarExchange);
  latest = value;
  return <PurchaseCurrencyFields idPrefix="t" value={value} onChange={setValue} lockedCurrency={locked} />;
}

async function setValue(element: HTMLInputElement | HTMLSelectElement, value: string) {
  const prototype = element.tagName === "SELECT" ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, value);
  await act(async () => {
    element.dispatchEvent(new window.Event("input", { bubbles: true }));
    element.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
}

test("QAR sends nothing extra so existing QAR requests are unchanged", () => {
  assert.deepEqual(exchangePayload(qarExchange), {});
  assert.equal(exchangeProblem(qarExchange), null);
});

test("a foreign currency asks for rate, source and date and sends all four", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  act(() => root!.render(<Harness />));
  assert.equal(host.querySelector("#t-rate"), null);
  await setValue(host.querySelector<HTMLSelectElement>("#t-currency")!, "EUR");
  assert.ok(host.querySelector("#t-rate"));
  assert.match(exchangeProblem(latest) ?? "", /how many QAR one EUR/);
  await setValue(host.querySelector<HTMLInputElement>("#t-rate")!, "4.05");
  assert.match(exchangeProblem(latest) ?? "", /where the exchange rate comes from/);
  await setValue(host.querySelector<HTMLInputElement>("#t-source")!, "TEST ONLY bank rate");
  await setValue(host.querySelector<HTMLInputElement>("#t-date")!, "2026-10-21");
  assert.equal(exchangeProblem(latest), null);
  assert.deepEqual(exchangePayload(latest), {
    currency: "EUR", exchange_rate: "4.05", rate_source: "TEST ONLY bank rate", rate_date: "2026-10-21",
  });
  assert.match(host.textContent ?? "", /Stock and journals are recorded in QAR/);
});

test("a committed receipt shows the commitment currency and cannot change it", () => {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  act(() => root!.render(<Harness locked="USD" />));
  const select = host.querySelector<HTMLSelectElement>("#t-currency")!;
  assert.equal(select.value, "USD");
  assert.equal(select.disabled, true);
  assert.ok(host.querySelector("#t-rate"));
});

function ChargeHarness({ category }: { category: string }) {
  const [value, setValue] = useState<ExchangeDraft>(qarExchange);
  latest = value;
  return <ChargeCurrencyFields idPrefix="c" category={category} value={value} onChange={setValue} />;
}

test("a shipping charge can be billed in USD or EUR with its own rate", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  act(() => root!.render(<ChargeHarness category="shipping" />));
  const select = host.querySelector<HTMLSelectElement>("#c-currency")!;
  assert.deepEqual([...select.options].map((option) => option.value), ["QAR", "USD", "EUR"]);
  await setValue(select, "USD");
  await setValue(host.querySelector<HTMLInputElement>("#c-rate")!, "3.64");
  await setValue(host.querySelector<HTMLInputElement>("#c-source")!, "TEST ONLY freight invoice rate");
  await setValue(host.querySelector<HTMLInputElement>("#c-date")!, "2026-10-21");
  assert.equal(exchangeProblem(latest), null);
  assert.deepEqual(chargeExchangePayload("shipping", latest), {
    currency: "USD", exchange_rate: "3.64", rate_source: "TEST ONLY freight invoice rate", rate_date: "2026-10-21",
  });
  assert.deepEqual(chargeExchangePayload("clearance", latest), {});
});

test("customs clearance is fixed to QAR", () => {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  act(() => root!.render(<ChargeHarness category="clearance" />));
  const select = host.querySelector<HTMLSelectElement>("#c-currency")!;
  assert.equal(select.value, "QAR");
  assert.equal(select.disabled, true);
  assert.equal(host.querySelector("#c-rate"), null);
});
