// Native Chromium, real session/CSRF/APIs, private disposable PostgreSQL only.
import test, { after } from "node:test";
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir, unlink } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { browserHarness } from "./browser-harness.mjs";
import { openFunction as activateFunction, isVisible } from "./workspace-browser-navigation.mjs";

assert.ok(process.env.ACCEPTANCE_ROOT?.startsWith("/tmp/tivoli-retail-acceptance."));
assert.equal(process.env.REAL_BACKEND_ORIGIN, "http://127.0.0.1:18064");
const fixture = JSON.parse(await readFile(`${process.env.ACCEPTANCE_ROOT}/browser-private.json`, "utf8"));
const output = "generated-artifacts/retail-real-browser";
await mkdir(output, { recursive: true });
// A generated test-only pixel proves actual PNG decoding, not product-photo fidelity.
// The isolated browser serves the build output; never replace a source/shared asset.
const testImagePath = "dist/assets/test-only-chair.png";
let ownsTestImage = false;
try {
  await writeFile(testImagePath, Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGN426T9HwAGlAKaVZyRNgAAAABJRU5ErkJggg==", "base64"), { flag: "wx" });
  ownsTestImage = true;
} catch (error) { if (error.code !== "EEXIST") throw error; }
after(async () => { if (ownsTestImage) await unlink(testImagePath); });
const navigationDecisions = [];
function registerQuotationAccessTests() {
for (const width of [1280, 320]) {
  test(`real current quotation revocation recovery retains drafts but clears denied detail at ${width}px`, async () => {
    control("quotation");
    const allowed = ["POST /api/auth/login/", "POST /api/purchasing/quotes/",
      `POST /api/units/${fixture.unit}/select/`, `POST /api/units/${fixture.roche}/select/`];
    const b = await browserHarness({
      width, height: width === 320 ? 1000 : 900,
      realBackendOrigin: process.env.REAL_BACKEND_ORIGIN, allowedRealWrites: allowed,
    });
    try {
      await login(b);
      const id = await createQuote(b);
      const prefix = `/api/purchasing/quotes/${id}`;
      await openFunction(b, "retail", "actions");
      const form = await actionForm(b, "Record simulated payment");
      await fill(b, `${form} input[type=number]`, "1.00");
      await b.evaluate(`window.__revocationForm=document.querySelector('${form}');
        window.__revocationTransactions=document.querySelector(".pw-transactions");
        window.__revocationWrites=[];
        const original=window.fetch.bind(window);
        window.fetch=(url,init)=>{
          if(url===${JSON.stringify(`${prefix}/payments/`)} && init?.method==="POST")
            window.__revocationWrites.push({body:JSON.parse(init.body),
              key:new Headers(init.headers).get("Idempotency-Key")});
          return original(url,init);
        };`);
      control("revoke");
      const revoked = snapshot();
      assert.equal((await api(b, `${prefix}/?business_unit_id=${fixture.unit}`)).status, 404);
      allowed.push(`POST ${prefix}/payments/`);
      await b.click(`${form} button`);
      await b.wait('document.querySelector(".pw-transactions .pw-access-chip")?.textContent==="Actions gated"');
      await b.wait('!document.querySelector(".pw-quote") && !!document.querySelector(".pw-quote-card [role=alert]")');
      assert.equal(await b.evaluate('document.querySelector("#pw-quote-id").value'), String(id));
      assert.equal(await b.evaluate("window.__revocationTransactions===document.querySelector('.pw-transactions')"), true);
      assert.equal(await b.evaluate('[...document.querySelectorAll(".pw-transactions button")].some(n=>n.textContent==="Record test payment")'), false);
      assert.deepEqual(snapshot(), revoked, "Denied collection and read rechecks create no business records");
      // Explicit current lookup is still a real denied backend request.
      await fill(b, "#pw-quote-id", id); await b.key("Enter");
      await b.wait('!!document.querySelector(".pw-quote-card [role=alert]")');
      assert.equal(await isVisible(b, ".pw-quote"), false);
      assert.equal((await api(b, `/api/stock-balances/?unit=${fixture.unit}`)).status, 403);
      const writes = await b.evaluate("window.__revocationWrites");
      assert.equal(writes.length, 1, "No automatic retry after access revocation");
      assert.ok(writes[0].key);
      assert.deepEqual(snapshot(), revoked);
      assert.equal(await b.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), true);
      await b.screenshot(`current-quote-revoked-${width}`, "retail-real-browser");
      await choose(b, '[aria-label="Business unit"]', fixture.roche);
      await b.wait('document.querySelector("#pw-quote-id").value===""');
      assert.equal(await isVisible(b, ".pw-quote"), false);
      assert.deepEqual(snapshot(), revoked);
      b.assertSafe();
      await writeFile(`${output}/current-quote-revoked-${width}.json`, JSON.stringify(b.calls, null, 2));
    } catch (error) {
      await writeFile(`${output}/current-quote-revocation-failure-${width}.txt`,
        await b.evaluate("document.body.innerText").catch(() => "Browser unavailable"));
      throw error;
    } finally { await b.close(); }
  });
}
test("real current quotation action-only role denial and lost read acknowledgement preserve authorized detail and draft", async () => {
  control("multiline");
  const author = await browserHarness({
    width: 1280, height: 900, realBackendOrigin: process.env.REAL_BACKEND_ORIGIN,
    allowedRealWrites: ["POST /api/auth/login/", `POST /api/units/${fixture.unit}/select/`, "POST /api/purchasing/quotes/"],
  });
  let id;
  try {
    await login(author);
    id = await createQuote(author, false, "15");
    author.assertSafe();
  } finally { await author.close(); }
  let loseRead = false;
  const prefix = `/api/purchasing/quotes/${id}`;
  const b = await browserHarness({
    width: 1280, height: 900, realBackendOrigin: process.env.REAL_BACKEND_ORIGIN,
    allowedRealWrites: ["POST /api/auth/login/", `POST /api/units/${fixture.unit}/select/`,
      `POST ${prefix}/discount-approval/`],
    beforeRealResponse: async call => {
      if (loseRead && call.method === "GET" && call.path === `${prefix}/` && call.status === 200) {
        loseRead = false;
        throw new Error("TEST ONLY lost authorized read response; backend did not deny access");
      }
    },
  });
  try {
    await login(b, fixture.manager);
    await lookup(b, "retail", id);
    const form = await actionForm(b, "Approve price decision");
    await fill(b, `${form} input`, "TEST ONLY preserved approval reason");
    await b.wait(`!document.querySelector('${form} button').disabled`);
    await b.evaluate(`window.__roleDenialForm=document.querySelector('${form}'); window.__roleDenialWrites=[];
      const original=window.fetch.bind(window);
      window.fetch=(url,init)=>{
        if(url===${JSON.stringify(`${prefix}/discount-approval/`)} && init?.method==="POST")
          window.__roleDenialWrites.push({body:JSON.parse(init.body),key:new Headers(init.headers).get("Idempotency-Key")});
        return original(url,init);
      };`);
    control("downgrade-manager-role"); // Preserve read prerequisites, not 15% approval authority.
    const baseline = snapshot();
    loseRead = true;
    await b.click(`${form} button`);
    await b.wait('document.body.innerText.includes("Quote access could not be rechecked")');
    assert.equal(loseRead, false);
    assert.equal(await b.evaluate(`document.querySelector(".pw-quote").textContent.includes("Quote #${id}")`), true);
    assert.equal(await b.evaluate("window.__roleDenialForm.isConnected"), true);
    assert.equal(await b.evaluate('window.__roleDenialForm.querySelector("input").value'), "TEST ONLY preserved approval reason");
    assert.deepEqual(snapshot(), baseline);
    const writes = await b.evaluate("window.__roleDenialWrites");
    assert.equal(writes.length, 1, "No automatic retry after role denial or lost read response");
    // Explicit read recovery is permitted although this approval role is not.
    await lookup(b, "retail", id);
    const recovered = await actionForm(b, "Approve price decision");
    assert.equal(await b.evaluate('document.body.innerText.includes("Quote access could not be rechecked")'), false);
    assert.equal(await b.evaluate(`document.querySelector('${recovered} input').value`), "TEST ONLY preserved approval reason");
    assert.equal((await api(b, `${prefix}/?business_unit_id=${fixture.unit}`)).status, 200);
    // Direct replay still rechecks actual authority and cannot post approval.
    assert.equal((await api(b, `${prefix}/discount-approval/`, writes[0].body, writes[0].key)).status, 403);
    assert.deepEqual(await b.evaluate("window.__roleDenialWrites"), [writes[0], writes[0]]);
    assert.deepEqual(snapshot(), baseline, "Role denial, read recovery and explicit denied replay write no business facts");
    b.assertSafe();
    await b.screenshot("current-quote-action-only-denial", "retail-real-browser");
    await writeFile(`${output}/current-quote-action-only-denial.json`, JSON.stringify(b.calls, null, 2));
  } finally { await b.close(); }
});
}
function registerCombinedJourneyTests() {
for (const width of [1280, 320]) {
  test(`real combined multi-item Retail-to-Delivery journey at ${width}px`, async () => {
    control("journey");
    const allowed = ["POST /api/auth/login/", "POST /api/purchasing/quotes/",
      `POST /api/units/${fixture.unit}/select/`, `POST /api/units/${fixture.roche}/select/`];
    const lost = new Set(["payments", "deliver"]);
    const b = await browserHarness({
      width, height: width === 320 ? 1000 : 900,
      realBackendOrigin: process.env.REAL_BACKEND_ORIGIN, allowedRealWrites: allowed,
      beforeRealResponse: async call => {
        const action = call.path.match(/\/(payments|deliver)\/$/)?.[1];
        if (action && call.status < 300 && lost.delete(action)) {
          throw new Error("TEST ONLY lost acknowledgement after real commit");
        }
      },
    });
    const privateFields = /"(?:unit_cost|landed_cost|gross_margin|supplier_id|supplier_unit_cost|cost_of_goods_sold|purchase_value|value_delta|inventory_value)"/;
    const assertMasked = value => assert.doesNotMatch(JSON.stringify(value), privateFields);
    const bucket = (facts, variant) => facts.stock_buckets.find(row =>
      row.business_unit_id === fixture.unit && row.location_id === fixture.location && row.variant_id === variant);
    const reservationRows = (facts, id) => facts.reservation_lines.filter(row => row.quote_line__quote_id === id);
    const visibleNotice = (role, text) => `[...document.querySelectorAll('.pw-notice[role=${role}]')]
      .some(n=>n.getClientRects().length && n.textContent.includes(${JSON.stringify(text)}))`;
    let quoteId;
    try {
      await login(b);
      assert.equal((await api(b, "/api/auth/me/")).body.is_staff, false);
      const initial = snapshot();
      await openFunction(b, "retail", "quote");
      await b.wait('!document.querySelector("#pw-quote-customer").disabled');
      await choose(b, "#pw-quote-customer", fixture.customer);
      await choose(b, "#pw-quote-variant", fixture.variant);
      await choose(b, "#pw-quote-location", fixture.location);
      await fill(b, "#pw-quote-quantity", "1.25");
      await fill(b, "#pw-quote-specifications", '{"finish":"TEST ONLY linen"}');
      await b.click("#pw-add-quote-line");
      await choose(b, "#pw-quote-line-2-variant", fixture.alternate_variant);
      await choose(b, "#pw-quote-line-2-location", fixture.location);
      await fill(b, "#pw-quote-line-2-quantity", "2.75");
      await fill(b, "#pw-quote-discount", "5.25");
      await fill(b, "#pw-notes_ar", "ملاحظة تجريبية");
      await fill(b, 'form:has(#pw-quote-kind) input[maxlength="240"]', "TEST ONLY existing salesperson authority");
      await b.click('form:has(#pw-quote-kind) button[type=submit]');
      await b.wait(visibleNotice("status", "Quote creation"));
      const created = snapshot();
      assert.equal(created.quotes.length, initial.quotes.length + 1);
      quoteId = created.quotes.at(-1).pk;
      const prefix = `/api/purchasing/quotes/${quoteId}`;
      allowed.push(`POST ${prefix}/payments/`, `POST ${prefix}/ready/`, `POST ${prefix}/deliver/`);
      const detail = async () => {
        const response = await api(b, `${prefix}/?business_unit_id=${fixture.unit}`);
        assert.equal(response.status, 200);
        assertMasked(response.body);
        return response.body;
      };
      let quote = await detail();
      assert.equal(quote.total_quote_value, "49.86");
      assert.equal(quote.lines.length, 2);
      assert.equal(quote.status, "approved");
      assert.deepEqual(created.prices, initial.prices);
      assert.deepEqual(created.stock_buckets, initial.stock_buckets);
      await openFunction(b, "retail", "actions");
      await b.wait(`document.querySelector(".pw-quote")?.textContent.includes("Quote #${quoteId}")`);
      // Capture operation identity only: never auth/session/CSRF material.
      await b.evaluate(`(() => {
        const original=window.fetch.bind(window); window.__journeyWrites=[]; window.__journeyResponses=[];
        window.fetch=(url,init)=>{
          if(init?.method==="POST" && /\\/(payments|ready|deliver)\\/$/.test(url))
            window.__journeyWrites.push({path:url,body:JSON.parse(init.body),
              key:new Headers(init.headers).get("Idempotency-Key")});
          return original(url,init).then(async response=>{
            if(typeof url==="string" && /\\/api\\/(purchasing|stock-balances)\\//.test(url)) {
              try { window.__journeyResponses.push(await response.clone().json()); }
              catch { /* A deliberately lost acknowledgement has no JSON body. */ }
            }
            return response;
          });
        };
      })()`);
      let form = await actionForm(b, "Record simulated payment");
      // Real validation failure, not a mocked response. Preserve the draft.
      await fill(b, `${form} input[type=number]`, "50.00");
      const beforeInvalid = snapshot();
      await b.click(`${form} button`);
      await b.wait(visibleNotice("alert", "exceeds"));
      assert.deepEqual(snapshot(), beforeInvalid, "Overpayment rolls back all facts");
      assert.equal(await b.evaluate(`document.querySelector('${form} input[type=number]').value`), "50.00");
      assert.equal(await b.evaluate(`document.querySelector('${form} button').disabled`), false);
      assert.equal(await b.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), true);
      await b.screenshot(`multiline-collection-error-${width}`, "retail-real-browser");
      await fill(b, `${form} input[type=number]`, "24.93");
      await b.click(`${form} button`);
      await b.wait(visibleNotice("alert", "502"));
      const deposited = snapshot();
      assert.equal(deposited.payments, initial.payments + 1);
      assert.deepEqual(reservationRows(deposited, quoteId).map(row =>
        [row.quote_line__variant_id, row.stock_reservation__quantity, row.stock_reservation__status]),
      [[fixture.variant, "1.25", "active"], [fixture.alternate_variant, "2.75", "active"]]);
      for (const [variant, hundredths] of [[fixture.variant, 125], [fixture.alternate_variant, 275]]) {
        const old = bucket(initial, variant), current = bucket(deposited, variant);
        assert.equal(current.on_hand_quantity, old.on_hand_quantity);
        assert.equal(Math.round(Number(current.reserved_quantity) * 100),
          Math.round(Number(old.reserved_quantity) * 100) + hundredths);
      }
      await b.click(`${form} button`);
      await b.wait(visibleNotice("status", "Simulated payment recorded"));
      await b.wait('document.querySelector(".pw-quote .pw-status")?.textContent==="confirmed"');
      assert.deepEqual(snapshot(), deposited, "Exact deposit replay duplicates no payment, reservation, audit or journal");
      await openFunction(b, "retail", "collections");
      await b.wait('!!document.querySelector(".rc-table")');
      const worklist = await api(b, `/api/purchasing/collections/?business_unit_id=${fixture.unit}&quote_id=${quoteId}`);
      assert.equal(worklist.status, 200);
      assertMasked(worklist.body);
      assert.equal(worklist.body.rows[0].recorded_collections, "24.93");
      assert.equal(worklist.body.rows[0].remaining_balance, "24.93");
      await lookup(b, "delivery", quoteId);
      await openFunction(b, "delivery", "preparation");
      await b.wait('document.querySelectorAll(".dp-line").length===2');
      assert.equal(await b.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), true);
      const beforeReview = snapshot();
      await b.evaluate('document.querySelector(".dp-review-check input").focus()');
      await b.key(" ");
      assert.equal(await b.evaluate('document.querySelector(".dp-review-check input").checked'), true);
      assert.deepEqual(snapshot(), beforeReview, "Local preparation review creates no business records");
      await b.screenshot(`multiline-preparation-${width}`, "retail-real-browser");
      await b.click(".dp-footer button");
      await b.wait('location.hash==="#/delivery/handover"');
      form = await actionForm(b, "Notify customer that goods are ready");
      await b.click(`${form} button`);
      await b.wait('document.querySelector(".pw-quote .pw-status")?.textContent==="ready"');
      const ready = snapshot();
      const readyAt = ready.quotes.find(row => row.pk === quoteId).ready_at;
      const deliver = async () => {
        await b.wait('[...document.querySelectorAll("button")].some(n=>n.textContent==="Record delivery"&&!n.disabled)');
        await b.evaluate(`document.querySelectorAll("[data-journey-deliver]").forEach(n=>n.removeAttribute("data-journey-deliver"));
          [...document.querySelectorAll("button")].find(n=>n.textContent==="Record delivery").setAttribute("data-journey-deliver","current")`);
        await b.click('[data-journey-deliver="current"]');
      };
      await deliver();
      await b.wait(visibleNotice("alert", "Full payment"));
      assert.deepEqual(snapshot(), ready, "Unpaid multi-line handover writes nothing");
      form = await actionForm(b, "Record simulated payment");
      await fill(b, `${form} input[type=number]`, "24.93");
      await b.click(`${form} button`);
      await b.wait(visibleNotice("status", "Simulated payment recorded"));
      const paid = snapshot();
      assert.equal(paid.payments, initial.payments + 2);
      assert.equal(paid.reservations, deposited.reservations, "Balance collection does not reserve lines twice");
      assert.deepEqual(paid.stock_buckets, deposited.stock_buckets);
      assert.equal(paid.quotes.find(row => row.pk === quoteId).ready_at, readyAt);
      await deliver();
      await b.wait(visibleNotice("alert", "502"));
      const committed = snapshot();
      assert.equal(committed.quotes.find(row => row.pk === quoteId).status, "delivered");
      assert.ok(committed.quotes.find(row => row.pk === quoteId).delivered_at);
      assert.equal(committed.quotes.find(row => row.pk === quoteId).ready_at, readyAt);
      for (const [variant, hundredths] of [[fixture.variant, 125], [fixture.alternate_variant, 275]]) {
        const old = bucket(initial, variant), current = bucket(committed, variant);
        assert.equal(Math.round(Number(current.on_hand_quantity) * 100),
          Math.round(Number(old.on_hand_quantity) * 100) - hundredths);
        assert.equal(current.reserved_quantity, old.reserved_quantity);
      }
      assert.ok(reservationRows(committed, quoteId).every(row => row.stock_reservation__status === "consumed"));
      assert.deepEqual(committed.delivery_lines.filter(row => row.quote_line__quote_id === quoteId)
        .map(row => [row.quote_line__variant_id, row.quantity]),
      [[fixture.variant, "1.25"], [fixture.alternate_variant, "2.75"]]);
      assert.equal(committed.delivery_movements.length, initial.delivery_movements.length + 2);
      await deliver();
      await b.wait('document.querySelector(".pw-quote .pw-status")?.textContent==="delivered"');
      assert.deepEqual(snapshot(), committed, "Handover replay duplicates no per-line stock, delivery, journal, audit or timestamp");
      const writes = await b.evaluate("window.__journeyWrites");
      const payments = writes.filter(row => row.path.endsWith("/payments/"));
      const handovers = writes.filter(row => row.path.endsWith("/deliver/"));
      assert.equal(payments.length, 4, "One rejected, two identical deposit attempts, one remaining collection");
      assert.notEqual(payments[0].key, payments[1].key, "Corrected amount is a new request identity");
      assert.deepEqual(payments[1], payments[2]);
      assert.equal(handovers.length, 3, "One rejected unpaid attempt, two unchanged fully-paid attempts");
      assert.deepEqual(handovers[1], handovers[2]);
      assert.equal(writes.filter(row => row.path.endsWith("/ready/")).length, 1);
      assert.equal((await api(b, `${prefix}/deliver/`, {}, `TEST-multiline-second-delivery-${width}`)).status, 400);
      assert.deepEqual(snapshot(), committed);
      quote = await detail();
      assert.deepEqual(quote.delivery_progress.lines.map(row => [row.delivered_quantity, row.remaining_quantity]),
        [["1.25", "0.00"], ["2.75", "0.00"]]);
      await b.screenshot(`multiline-delivered-${width}`, "retail-real-browser");
      await choose(b, '[aria-label="Business unit"]', fixture.roche);
      await b.wait('!document.querySelector(".pw-quote")');
      assert.equal((await api(b, `${prefix}/?business_unit_id=${fixture.roche}`)).status, 404);
      assert.deepEqual(snapshot(), committed, "BU switching and denied cross-unit reads do not change business facts");
      control("revoke");
      const revoked = snapshot();
      assert.equal((await api(b, `${prefix}/?business_unit_id=${fixture.unit}`)).status, 404);
      assert.equal((await api(b, `/api/stock-balances/?unit=${fixture.unit}`)).status, 403);
      assert.equal((await api(b, `${prefix}/ready/`, {}, `TEST-multiline-revoked-${width}`)).status, 404);
      assert.deepEqual(snapshot(), revoked);
      const responses = await b.evaluate("window.__journeyResponses");
      assert.ok(responses.length > 10, "Actual scoped GET/POST success, rejection and replay responses examined");
      responses.forEach(assertMasked);
      b.assertSafe();
      await writeFile(`${output}/multiline-journey-${width}.json`, JSON.stringify({quoteId, calls: b.calls}, null, 2));
    } catch (error) {
      await writeFile(`${output}/multiline-journey-failure-${width}.txt`, await b.evaluate("document.body.innerText").catch(() => "Browser unavailable"));
      throw error;
    } finally { await b.close(); }
  });
}
}
for (const width of [1280, 320]) {
  test(`real nonstaff quote rejection recovery retains a bilingual multi-line draft at ${width}px`, async () => {
    control("quotation");
    const b = await browserHarness({
      width, height: width === 320 ? 1000 : 900,
      realBackendOrigin: process.env.REAL_BACKEND_ORIGIN,
      allowedRealWrites: ["POST /api/auth/login/", `POST /api/units/${fixture.unit}/select/`,
        "POST /api/purchasing/quotes/"],
    });
    try {
      await login(b);
      const me = await api(b, "/api/auth/me/");
      assert.equal(me.body.is_staff ?? me.body.user?.is_staff, false);
      await openFunction(b, "retail", "quote");
      await b.wait('!document.querySelector("#pw-quote-customer").disabled');
      await b.evaluate(`(() => {
        window.__recoveryWrites = [];
        const original = window.fetch.bind(window);
        window.fetch = (url, options) => {
          if (url === "/api/purchasing/quotes/" && options?.method === "POST") {
            window.__recoveryWrites.push({payload: JSON.parse(options.body),
              key: new Headers(options.headers).get("Idempotency-Key")});
          }
          return original(url, options);
        };
      })()`);
      await choose(b, "#pw-quote-customer", fixture.customer);
      await choose(b, "#pw-quote-variant", fixture.variant);
      await choose(b, "#pw-quote-location", fixture.location);
      await fill(b, "#pw-quote-quantity", "1.25");
      await fill(b, "#pw-quote-specifications", '{"finish":"TEST ONLY linen"}');
      await b.click("#pw-add-quote-line");
      await choose(b, "#pw-quote-line-2-variant", fixture.alternate_variant);
      await choose(b, "#pw-quote-line-2-location", fixture.location);
      await fill(b, "#pw-quote-line-2-quantity", "2.75");
      await fill(b, "#pw-quote-discount", "5.25");
      await fill(b, "#pw-notes_ar", "ملاحظة تجريبية");
      const reason = 'form:has(#pw-quote-kind) input[maxlength="240"]';
      // Native required permits whitespace; the existing server rejects it.
      // No HTTP response is mocked or changed.
      await fill(b, reason, "   ");
      const before = snapshot();
      await b.click('form:has(#pw-quote-kind) button[type=submit]');
      await b.wait(`[...document.querySelectorAll('.pw-notice[role=alert]')]
        .some(n=>n.textContent.includes("Quote creation") && n.getClientRects().length)`);
      const error = await b.evaluate(`[...document.querySelectorAll('.pw-notice[role=alert]')]
        .find(n=>n.textContent.includes("Quote creation")).textContent`);
      assert.match(error, /reason/i);
      assert.equal(await b.evaluate("location.hash"), "#/retail/quote");
      assert.deepEqual(snapshot(), before, "Rejected quote creates no business/audit/stock/payment facts");
      assert.equal(await b.evaluate('document.querySelectorAll("[data-quote-line]").length'), 2);
      assert.equal(await b.evaluate('document.querySelector("#pw-notes_ar").value'), "ملاحظة تجريبية");
      assert.equal(await b.evaluate('document.querySelector("[data-quote-total]").textContent.includes("49.86")'), true);
      assert.equal(await b.evaluate('document.querySelector("form:has(#pw-quote-kind) button[type=submit]").disabled'), false);
      assert.equal(await b.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), true);
      await b.screenshot(`quote-rejection-recovery-${width}`, "retail-real-browser");
      await fill(b, reason, "TEST ONLY corrected existing price-decision reason");
      await b.click('form:has(#pw-quote-kind) button[type=submit]');
      await b.wait(`[...document.querySelectorAll('.pw-notice[role=status]')]
        .some(n=>n.textContent.includes("Quote creation") && n.getClientRects().length)`);
      assert.equal(await b.evaluate("location.hash"), "#/retail/quote", "Do not invent automatic navigation after creation");
      const writes = await b.evaluate("window.__recoveryWrites");
      assert.equal(writes.length, 2, "Exactly one rejected and one explicitly corrected UI submission");
      assert.notEqual(writes[0].key, writes[1].key, "Changed reason uses existing changed-payload identity");
      assert.deepEqual(writes[0].payload.lines, writes[1].payload.lines);
      assert.deepEqual(writes[0].payload.presentation, writes[1].payload.presentation);
      const committed = snapshot();
      assert.equal(committed.quotes.length, before.quotes.length + 1);
      const replay = await api(b, "/api/purchasing/quotes/", writes[1].payload, writes[1].key);
      assert.equal(replay.status, 201);
      const detail = await api(b, `/api/purchasing/quotes/${replay.body.quote_id}/?business_unit_id=${fixture.unit}`);
      assert.equal(detail.body.total_quote_value, "49.86");
      assert.equal(detail.body.presentation_document.company.name_en, "Tivoli Group L.L.C");
      assert.equal(detail.body.presentation_document.company.trademark_en, "Tivoli");
      assert.equal(detail.body.presentation_document.notes_ar, "ملاحظة تجريبية");
      await b.wait(`document.querySelector(".pw-quote")?.textContent.includes("Quote #${replay.body.quote_id}")`);
      assert.doesNotMatch(JSON.stringify(detail.body), /"unit_cost"|"landed_cost"|"gross_margin"|"supplier_unit_cost"/);
      assert.deepEqual(snapshot(), committed, "Explicit unchanged replay returns the original quote without another write");
      assert.equal(committed.payments, before.payments);
      assert.deepEqual(committed.balances, before.balances);
      assert.equal(committed.journal_count, before.journal_count);
      b.assertSafe();
    } finally {
      await b.close();
    }
  });
}
function registerBilingualQuotationTests() {
 for (const width of [1280, 320]) {
  test(`real nonstaff bilingual quotation choices and multi-item creation at ${width}px`, async () => {
    control("quotation");
    const b = await browserHarness({
      width, height: width === 320 ? 1000 : 900,
      realBackendOrigin: process.env.REAL_BACKEND_ORIGIN,
      allowedRealWrites: [
        "POST /api/auth/login/", `POST /api/units/${fixture.unit}/select/`,
        `POST /api/units/${fixture.roche}/select/`, "POST /api/purchasing/quotes/",
        "POST /api/items/", `POST /api/governance/purchasing/${fixture.unit}/selling-prices/`,
        "POST /api/reports/sales/export/",
      ],
    });
    try {
      await login(b);
      const me = await api(b, "/api/auth/me/");
      assert.equal(me.status, 200);
      assert.equal(me.body.is_staff ?? me.body.user?.is_staff, false);
      await openFunction(b, "retail", "quote");
      await b.wait('!document.querySelector("#pw-quote-customer").disabled');
      const choices = await api(b, `/api/purchasing/quotation-choices/?business_unit_id=${fixture.unit}`);
      assert.equal(choices.status, 200);
      assert.equal(choices.body.discount_ceiling, "10.00");
      assert.ok(choices.body.customers.every(row => row.party_type === "customer"));
      const first = choices.body.current_prices.find(row => row.variant_id === fixture.variant);
      assert.equal(first.item.name_ar, "كرسي تجريبي");
      const expectedQuantity = python(
        `from foundation.models import StockBalance; b=StockBalance.objects.get(business_unit_id=${fixture.unit},variant_id=${fixture.variant},location_id=${fixture.location}); print(format(b.on_hand_quantity-b.reserved_quantity,'f'))`,
      );
      assert.equal(first.stock.find(row => row.location_id === fixture.location).quantity, expectedQuantity);
      assert.equal(b.calls.some(call => call.path.startsWith("/api/governance/purchasing/")), false,
        "Normal quotation creation must not fetch staff configuration or suppliers");
      await choose(b, "#pw-quote-customer", fixture.customer);
      await choose(b, "#pw-quote-variant", fixture.variant);
      await choose(b, "#pw-quote-location", fixture.location);
      await fill(b, "#pw-quote-quantity", "1.25");
      await fill(b, "#pw-quote-specifications", '{"en":"TEST ONLY linen","ar":"نسيج تجريبي"}');
      await b.click("#pw-add-quote-line");
      await choose(b, "#pw-quote-line-2-variant", fixture.alternate_variant);
      await choose(b, "#pw-quote-line-2-location", fixture.location);
      await fill(b, "#pw-quote-line-2-quantity", "2.75");
      await fill(b, "#pw-quote-discount", "5.25");
      await fill(b, 'form:has(#pw-quote-kind) input[maxlength="240"]', "TEST ONLY approved salesperson discount");
      await fill(b, "#pw-payment_terms_en", "TEST ONLY display note; existing gates remain");
      await fill(b, "#pw-payment_terms_ar", "نص تجريبي");
      await fill(b, "#pw-department_en", "TEST ONLY retail");
      await fill(b, "#pw-department_ar", "قسم تجريبي");
      await fill(b, "#pw-contact_person_en", "TEST ONLY quotation contact");
      await fill(b, "#pw-project_ar", "مشروع تجريبي");
      await fill(b, "#pw-address_en", "TEST ONLY quotation address");
      await fill(b, "#pw-email", "synthetic@example.invalid");
      await fill(b, "#pw-notes_en", "TEST ONLY quotation note");
      await fill(b, "#pw-quote-unit-en", "TEST ONLY unit");
      await fill(b, "#pw-quote-unit-ar", "وحدة تجريبية");
      await b.wait('document.querySelector("[data-quote-total]").textContent.includes("49.86")');
      assert.equal(await b.evaluate('document.querySelector("#pw-quote-discount").max'), "10");
      assert.equal(await b.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'), true);
      // Real Tab focus on a labelled control, not a scripted React event.
      await b.click("#pw-department_ar");
      await b.key("Tab");
      assert.equal(await b.evaluate('!!document.activeElement && document.activeElement !== document.body'), true);
      await b.screenshot(`bilingual-entry-${width}`, "retail-real-browser");
      if (width === 320) {
        const layout = await b.evaluate(`(() => {
          const el=document.querySelector(".pw-quote-item-preview");
          return {columns:getComputedStyle(el).gridTemplateColumns.split(" ").length,
            copyWidth:el.querySelector(".pw-quote-item-copy").getBoundingClientRect().width,
            containers:[el,el.parentElement,el.parentElement.parentElement,el.closest("form")].map(n=>({
              className:n.className,width:n.getBoundingClientRect().width,columns:getComputedStyle(n).gridTemplateColumns
            }))};
        })()`);
        await b.evaluate('document.querySelector(".pw-quote-item-preview").scrollIntoView({block:"center"})');
        await b.screenshot(`english-item-card-${width}`, "retail-real-browser");
        assert.equal(layout.columns,1,"Mobile entry must not squeeze copy beside a fixed-width image");
        assert.ok(layout.copyWidth>=120,`Mobile item information needs a readable text column: ${JSON.stringify(layout)}`);
      }
      const before = snapshot();
      await b.click('form:has(#pw-quote-kind) button[type=submit]');
      await b.wait('!!document.querySelector(".pw-presentation-document")');
      await openFunction(b, "retail", "actions");
      await b.wait('document.querySelector(".pw-presentation-document")?.getClientRects().length > 0');
      await b.evaluate('document.querySelector(".pw-presentation-document").scrollIntoView({block:"start"})');
      if (width === 320) {
        const layout = await b.evaluate(`(() => {
          const el=document.querySelector(".pw-presentation-line");
          return {columns:getComputedStyle(el).gridTemplateColumns.split(" ").length,
            copyWidth:el.lastElementChild.getBoundingClientRect().width,
            metaColumns:getComputedStyle(document.querySelector(".pw-presentation-meta")).gridTemplateColumns.split(" ").length};
        })()`);
        assert.equal(layout.columns,1,"Mobile document must stack image above line details");
        assert.equal(layout.metaColumns,1,"Client metadata must retain a readable column");
        assert.ok(layout.copyWidth>=120,`Saved item information must not become a one-word column: ${JSON.stringify(layout)}`);
      }
      const after = snapshot();
      assert.equal(after.quotes.length, before.quotes.length + 1);
      const quote = after.quotes.at(-1);
      assert.equal(quote.total_quote_value, "49.86");
      for (const field of ["payments", "reservations", "balances", "deliveries", "delivery_movements", "journal_count"]) {
        assert.deepEqual(after[field], before[field], `Quoting cannot change ${field}`);
      }
      const doc = await api(b, `/api/purchasing/quotes/${quote.pk}/?business_unit_id=${fixture.unit}`);
      assert.equal(doc.status, 200);
      const display = doc.body.presentation_document;
      assert.equal(display.lines.length, 2);
      assert.equal(display.client.contact_ar, "جهة اتصال تجريبية");
      assert.equal(display.valid_until, "");
      assert.equal(display.delivery_time_en, "");
      assert.equal(display.terms_status, "pending_owner_text");
      assert.equal(display.payment_terms_ar, "نص تجريبي");
      assert.equal(display.client.contact_person_en, "TEST ONLY quotation contact");
      assert.equal(display.client.email, "synthetic@example.invalid");
      assert.equal(display.notes_en, "TEST ONLY quotation note");
      assert.equal(display.lines[0].unit_ar, "وحدة تجريبية");
      const visible = await b.evaluate('document.querySelector(".pw-presentation-document").innerText');
      for (const text of ["49.86", "كرسي تجريبي", "جهة اتصال تجريبية", "قسم تجريبي", "عرض سعر"]) {
        assert.ok(visible.includes(text), `Saved quotation presents ${text}`);
      }
      assert.equal(await b.evaluate('Array.from(document.querySelectorAll(".pw-presentation-document [lang=ar]")).every(n=>n.dir==="rtl")'), true);
      assert.equal(await b.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'), true);
      await b.screenshot(`bilingual-saved-${width}`, "retail-real-browser");
      await b.evaluate('document.querySelector(".pw-presentation-line").scrollIntoView({block:"start"})');
      await b.screenshot(`bilingual-items-${width}`, "retail-real-browser");
      if (process.env.RETAIL_BILINGUAL_CAPTURE === "1" && width === 1280) {
        const resume = `generated-artifacts/retail-real-browser/bilingual-${process.pid}.resume`;
        await writeFile("generated-artifacts/retail-real-browser/bilingual-capture.json", JSON.stringify({
          port: Number(new URL(b.origin).port), resume,
        }));
        for (let i = 0; i < 600; i++) {
          try { await readFile(resume); break; } catch {}
          await new Promise(resolve => setTimeout(resolve, 200));
        }
      }
      const payload = {
        business_unit_id: fixture.unit, customer_id: fixture.customer, kind: "stock",
        discount_percent: "10.01", discount_reason: "TEST ONLY denied authority", payment_plan: "full",
        lines: [{ variant_id: fixture.variant, location_id: fixture.location, quantity: "1.00", specifications: {} }],
      };
      const deniedBefore = snapshot();
      assert.equal((await api(b, "/api/purchasing/quotes/", payload, `TEST_ONLY_grade_${width}`)).status, 403);
      assert.equal((await api(b, "/api/purchasing/quotes/", {
        ...payload, discount_percent: "0", lines: [{ ...payload.lines[0], unit_price: "0.01" }],
      }, `TEST_ONLY_override_${width}`)).status, 400);
      assert.equal((await api(b, "/api/items/", {name:"TEST ONLY forbidden"}, `TEST_ONLY_item_${width}`)).status, 404);
      assert.equal((await api(b, `/api/governance/purchasing/${fixture.unit}/selling-prices/`,
        {variant_id:fixture.variant,amount:"0.01"}, `TEST_ONLY_price_${width}`)).status, 403);
      for (const format of ["xlsx", "docx"]) {
        assert.equal((await api(b, "/api/reports/sales/export/", {unit:fixture.unit,format}, `TEST_ONLY_export_${width}_${format}`)).status, 403);
      }
      assert.equal((await api(b, `/api/governance/purchasing/${fixture.unit}/`)).status, 403);
      assert.equal((await api(b, "/api/governance/purchasing/counterparties/")).status, 403);
      assert.deepEqual(snapshot(), deniedBefore);
      const confidential = /\"(?:unit_cost|supplier_unit_cost|landed_cost|gross_margin|on_hand_value|cost_of_goods_sold)\"/;
      assert.equal(confidential.test(JSON.stringify(choices.body)), false);
      assert.equal(confidential.test(JSON.stringify(doc.body)), false);
      await choose(b, '[aria-label="Business unit"]', fixture.roche);
      await b.wait('!document.querySelector(".pw-presentation-document")');
      assert.equal((await api(b, `/api/purchasing/quotation-choices/?business_unit_id=${fixture.roche}`)).status, 404);
      control("revoke");
      const revoked = snapshot();
      assert.equal((await api(b, `/api/purchasing/quotation-choices/?business_unit_id=${fixture.unit}`)).status, 404);
      assert.deepEqual(snapshot(), revoked);
      b.assertSafe();
    } catch (error) {
      await b.handleDialog(false).catch(() => {});
      await b.screenshot(`bilingual-failure-${width}`, "retail-real-browser").catch(() => {});
      await writeFile(`${output}/bilingual-failure-${width}.txt`, await b.evaluate("document.body.innerText")).catch(() => {});
      throw error;
    } finally {
      await b.close();
    }
  });
 }
}
async function openFunction(b, section, child) {
  let seen = b.dialogs.length;
  let result;
  const operation = activateFunction(b, section, child).then(
    () => { result = { ok:true }; }, error => { result = { error }; },
  );
  while (!result) {
    if (b.dialogs.length > seen) {
      const prompt = b.dialogs[seen++];
      assert.match(prompt.message, /unsaved|draft|pending/i, "Only the existing unsaved-work prompt may be accepted");
      navigationDecisions.push({ section, child, message:prompt.message, accepted:true });
      await b.handleDialog(true); // Native explicit decision, never stub window.confirm.
    }
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  await operation;
  if (result.error) throw result.error;
}
function python(code) {
  return execFileSync("python", ["manage.py", "shell", "--no-imports", "-c", code],
    { encoding: "utf8" }).trim();
}
const snapshot = () => JSON.parse(python("import json; from scripts.retail_acceptance_fixture import probe; print(json.dumps(probe(),default=str))"));
const control = phase => python(`from scripts.retail_acceptance_fixture import control; control(${JSON.stringify(phase)})`);
async function login(b, username = "demo_multi") {
  await b.wait('!!document.querySelector("#username")');
  await b.click("#username"); await b.text(username);
  await b.click("#password"); await b.text(fixture.credentials[username]);
  await b.click('button[type="submit"]');
  await b.wait('!!document.querySelector(".sidebar") && !!document.querySelector(\'[aria-label="Business unit"]\')?.value');
  const selected = await b.evaluate('document.querySelector(\'[aria-label="Business unit"]\').value');
  if (selected !== String(fixture.unit)) await choose(b, '[aria-label="Business unit"]', fixture.unit);
}
async function fill(b, selector, value) {
  await b.click(selector); await b.key("Home");
  // Native select-all: CDP key helper exposes no modifiers other than shift.
  await b.evaluate(`document.querySelector(${JSON.stringify(selector)}).select()`);
  await b.text(String(value));
}
async function choose(b, selector, value) {
  const index = await b.evaluate(`Array.from(document.querySelector(${JSON.stringify(selector)}).options).findIndex(o=>o.value===${JSON.stringify(String(value))})`);
  assert.ok(index >= 0, "Existing authorized choice is available");
  await b.click(selector); await b.key("Home");
  for (let i = 0; i < index; i++) await b.key("ArrowDown");
  await b.key("Enter");
  await b.wait(`document.querySelector(${JSON.stringify(selector)}).value===${JSON.stringify(String(value))}`);
}
async function api(b, path, body, key) {
  return b.evaluate(`(async()=>{
    const token=(await(await fetch("/api/auth/csrf/")).json()).csrfToken;
    const response=await fetch(${JSON.stringify(path)},${body ? `{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":token,"Idempotency-Key":${JSON.stringify(key)}},body:JSON.stringify(${JSON.stringify(body)})}` : "{}"});
    const text=await response.text();
    let body; try { body=JSON.parse(text); } catch { body={non_json:true}; }
    return {status:response.status,body};
  })()`);
}
async function actionForm(b, title) {
  await b.evaluate(`(() => {document.querySelectorAll("[data-retail-action]").forEach(n=>n.removeAttribute("data-retail-action"));
    const h=[...document.querySelectorAll("h4")].find(h=>h.textContent===${JSON.stringify(title)});
    if(h)h.closest("form").setAttribute("data-retail-action","current");})()`);
  await b.wait('!!document.querySelector(\'[data-retail-action="current"]\')');
  return '[data-retail-action="current"]';
}
async function createQuote(b, special = false, discount = special ? "0" : "5") {
  await openFunction(b, "retail", "quote");
  await b.wait('!document.querySelector("#pw-quote-customer").disabled && document.querySelector("#pw-quote-customer").options.length>1');
  await choose(b, "#pw-quote-kind", special ? "special_order" : "stock");
  await choose(b, "#pw-quote-customer", fixture.customer);
  await choose(b, "#pw-quote-variant", fixture.variant);
  if (!special) await choose(b, "#pw-quote-location", fixture.location);
  await fill(b, "#pw-quote-quantity", special ? "1" : "2");
  await fill(b, "#pw-quote-discount", discount);
  await fill(b, "#pw-quote-specifications", '{"finish":"TEST ONLY linen"}');
  await fill(b, 'form:has(#pw-quote-kind) input[maxlength="240"]', "TEST ONLY permitted salesperson decision");
  if (special) {
    await choose(b, "#pw-payment-plan", "full");
    await fill(b, "#pw-payment-terms-reason", "TEST ONLY existing explicit full-payment plan");
  }
  const count = snapshot().quotes.length;
  await b.click('form:has(#pw-quote-kind) button[type="submit"]');
  let quotes;
  const deadline = Date.now() + 12000;
  do {
    quotes = snapshot().quotes;
    if (quotes.length > count) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  } while (Date.now() < deadline);
  assert.equal(quotes.length, count + 1, "One quote is created by the explicit form submission");
  const id = quotes.at(-1).pk;
  await b.wait(`document.querySelector(".pw-quote")?.textContent.includes("Quote #${id}")`);
  return id;
}
async function lookup(b, section, quoteId) {
  await openFunction(b, section, section === "retail" ? "actions" : "quote");
  await fill(b, "#pw-quote-id", quoteId); await b.key("Enter");
  await b.wait(`document.querySelector(".pw-quote")?.textContent.includes("Quote #${quoteId}")`);
}

test("real cross-section ordinary-sales responses and rendered views keep private costs unavailable", async () => {
  const allowed = ["POST /api/auth/login/", `POST /api/units/${fixture.unit}/select/`,
    "POST /api/purchasing/receipts/", "POST /api/reports/sales/export/"];
  const options = { width:1280,height:900,realBackendOrigin:process.env.REAL_BACKEND_ORIGIN,
    allowedRealWrites:allowed };
  const protectedKeys = new Set(["unit_cost","supplier_unit_cost","purchase_value",
    "eligible_landed_charges","landed_cost_allocations","landed_cost_allocation",
    "landed_cost_rate_percent","inventory_value","on_hand_value","unit_landed_cost",
    "cost_of_goods_sold","gross_margin","gross_margin_percent","transferred_value","value_delta"]);
  const check = value => {
    if (Array.isArray(value)) return value.forEach(check);
    if (value && typeof value==="object") for (const [key,nested] of Object.entries(value)) {
      assert.equal(protectedKeys.has(key),false,`Private response field ${key}`);
      check(nested);
    }
  };
  let b;
  try {
    b = await browserHarness(options);
    await login(b);
    const before = snapshot();
    // Replay an existing fixture operation, not a new receipt. Staff alone
    // must not reveal the immutable operation's private costing result.
    const receipt = await api(b,"/api/purchasing/receipts/",{
      business_unit_id:fixture.unit,supplier_id:fixture.supplier,
      lines:[{variant_id:fixture.variant,location_id:fixture.location,
        quantity:"10.00",unit_cost:"1000.00"}],
      charges:[{category:"shipping",amount:"2000.00",eligible:true}],
    },"TEST-ONLY-retail-opening-stock");
    assert.equal(receipt.status,201);
    check(receipt.body);
    assert.deepEqual(snapshot(),before,"Real HTTP replay must not create or alter business facts");
    await writeFile(`${output}/receipt-public-fields.json`,JSON.stringify(Object.keys(receipt.body).sort(),null,2));
    b.assertSafe();
    await b.close(); b=null;
    control("ordinary");
    b=await browserHarness(options);
    await login(b);
    const apiMatrix=[];
    for (const path of [
      `/api/stock/?unit=${fixture.unit}`, `/api/stock-balances/?unit=${fixture.unit}`,
      `/api/purchasing/quotes/?business_unit_id=${fixture.unit}`,
      `/api/purchasing/storage-charges/?business_unit_id=${fixture.unit}`,
      `/api/reports/purchase_history/receipts/?unit=${fixture.unit}`,
      `/api/reports/inventory/reservations/?unit=${fixture.unit}`,
      `/api/reports/sales/deliveries/?unit=${fixture.unit}`,
      `/api/reports/gross_margin/?unit=${fixture.unit}`,
      "/api/governance/purchasing/counterparties/",
    ]) {
      const result=await api(b,path);
      assert.ok([200,403].includes(result.status),path);
      check(result.body); apiMatrix.push({path,status:result.status});
    }
    const beforeExports=snapshot();
    for (const format of ["xlsx","docx"]) {
      const result=await api(b,"/api/reports/sales/export/",{unit:fixture.unit,format});
      assert.equal(result.status,403);check(result.body);
    }
    assert.deepEqual(snapshot(),beforeExports,"Denied exports create no export audit or business writes");
    const routes=await b.evaluate(`[...document.querySelectorAll("[data-workspace-link]")]
      .map(n=>n.dataset.workspaceLink).filter(r=>/^(retail|procurement|inventory|delivery|report[^/]*|purchasing)\\//.test(r))`);
    assert.ok(new Set(routes.map(r=>r.split("/")[0])).size>=6,"All six requested sections are represented");
    const views=[];
    for (const route of [...new Set(routes)]) {
      const [section,child]=route.split("/");
      await openFunction(b,section,child);
      const cells=await b.evaluate('[...document.querySelectorAll(".pr-table td, .pw-quote strong")].filter(n=>n.getClientRects().length).map(n=>n.textContent)');
      assert.ok(!cells.some(value=>/^(?:12,?000\\.00|2,?400\\.00)$/.test(value.trim())),
        "Private inventory/COGS values must not appear in record cells");
      views.push({route,focused:true});
    }
    await b.screenshot("ordinary-sales-cross-section","retail-real-browser");
    await writeFile(`${output}/sensitive-surface-browser-matrix.json`,JSON.stringify({apiMatrix,views},null,2));
    b.assertSafe();
  } finally {
    await b?.close();
    control("staff");
  }
});

function registerMultilineTests() {
for (const width of [1280, 320]) {
  test(`real multi-line Retail quote entry, exact totals, replay, rollback and scope at ${width}px`, async () => {
    control("multiline");
    const b = await browserHarness({
      width, height: 900, realBackendOrigin: process.env.REAL_BACKEND_ORIGIN,
      allowedRealWrites: [
        "POST /api/auth/login/", "POST /api/purchasing/quotes/",
        `POST /api/units/${fixture.unit}/select/`, `POST /api/units/${fixture.roche}/select/`,
      ],
    });
    try {
      await login(b);
      await openFunction(b, "retail", "quote");
      await b.wait('!document.querySelector("#pw-add-quote-line").disabled');
      await b.click("#pw-add-quote-line");
      assert.equal(await b.evaluate('document.querySelectorAll("[data-quote-line]").length'), 2);

      // Button-only editing must participate in the existing native discard
      // prompt, even before any text input. Cancel preserves both draft lines.
      const link = '[data-workspace-link="inventory/locations"]';
      await b.evaluate(`document.querySelector(${JSON.stringify(link)}).closest(".section-navigation-group").querySelector(".nav-section-toggle").focus()`);
      await b.key("Enter");
      await b.evaluate(`document.querySelector(${JSON.stringify(link)}).focus()`);
      const dialogCount = b.dialogs.length;
      const leave = b.key("Enter");
      const deadline = Date.now() + 3000;
      while (b.dialogs.length === dialogCount && Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 20));
      }
      assert.equal(b.dialogs.length, dialogCount + 1, "Add-only draft has a real unsaved-work warning");
      assert.match(b.dialogs.at(-1).message, /discard|draft/i);
      await b.handleDialog(false);
      await leave;
      assert.equal(await b.evaluate("location.hash"), "#/retail/quote");
      assert.equal(await b.evaluate('document.querySelectorAll("[data-quote-line]").length'), 2);

      // Transparent request recorder only: every request still reaches Django,
      // with unmodified cookies, headers, payloads, status and response body.
      await b.evaluate(`(() => {
        window.__multilineWrites = [];
        const original = window.fetch.bind(window);
        window.fetch = (url, options) => {
          if (url === "/api/purchasing/quotes/" && options?.method === "POST") {
            window.__multilineWrites.push({
              payload: JSON.parse(options.body),
              key: new Headers(options.headers).get("Idempotency-Key"),
            });
          }
          return original(url, options);
        };
      })()`);
      await choose(b, "#pw-quote-customer", fixture.customer);
      await choose(b, "#pw-quote-variant", fixture.variant);
      await choose(b, "#pw-quote-location", fixture.location);
      await fill(b, "#pw-quote-quantity", "1.25");
      await fill(b, "#pw-quote-specifications", '{"finish":"TEST ONLY linen"}');
      await choose(b, "#pw-quote-line-2-variant", fixture.alternate_variant);
      await choose(b, "#pw-quote-line-2-location", fixture.location);
      await fill(b, "#pw-quote-line-2-quantity", "2.75");
      await fill(b, "#pw-quote-line-2-specifications", '{"finish":"TEST ONLY oak"}');
      await fill(b, "#pw-quote-discount", "5.25");
      await fill(b, 'form:has(#pw-quote-kind) input[maxlength="240"]', "TEST ONLY multi-item price decision");
      await b.wait('document.querySelector("[data-quote-total]").textContent.includes("49.86")');
      assert.equal(await b.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), true);
      await b.screenshot(`multiline-review-${width}`, "retail-real-browser");

      if (process.env.RETAIL_MULTILINE_CAPTURE === "1" && width === 1280) {
        const token = process.env.ACCEPTANCE_ROOT.split("/").at(-1);
        await writeFile(`${output}/multiline-capture.json`, JSON.stringify({
          port: Number(new URL(b.origin).port), resume: `${output}/${token}.resume`,
        }));
        console.log("MULTILINE_CAPTURE_READY");
        for (let i = 0; i < 120; i++) {
          if (await readFile(`${output}/${token}.resume`, "utf8").catch(() => null)) break;
          await new Promise(resolve => setTimeout(resolve, 1000));
        }
      }

      const before = snapshot();
      await b.click('form:has(#pw-quote-kind) button[type="submit"]');
      await b.wait('document.querySelector(".pw-quote")?.textContent.includes("49.86")');
      const after = snapshot();
      assert.equal(after.quotes.length, before.quotes.length + 1);
      for (const field of ["payments", "reservations", "balances", "deliveries", "delivery_movements", "journal_count"]) {
        assert.deepEqual(after[field], before[field], `${field} cannot change from quote creation`);
      }
      const initialWrites = await b.evaluate("window.__multilineWrites");
      assert.equal(initialWrites.length, 1, "One explicit submission, no automatic retry");
      const [submitted] = initialWrites;
      assert.ok(submitted.key);
      assert.deepEqual(submitted.payload.lines.map(line => line.quantity), ["1.25", "2.75"]);
      const replay = await api(b, "/api/purchasing/quotes/", submitted.payload, submitted.key);
      assert.equal(replay.status, 201);
      assert.equal(replay.body.total_quote_value, "49.86");
      assert.deepEqual(replay.body.lines.map(line => line.net_total), ["23.68", "26.18"]);
      assert.deepEqual(replay.body.lines.map(line => line.selling_price_snapshot), ["19.99", "10.05"]);
      assert.deepEqual(snapshot(), after, "Exact UI-key replay creates no second quote/audit/stock fact");
      const invalid = structuredClone(submitted.payload);
      invalid.lines[1].quantity = "1.001";
      const rejected = await api(b, "/api/purchasing/quotes/", invalid, `TEST_ONLY_multiline_invalid_${width}`);
      assert.equal(rejected.status, 400);
      assert.deepEqual(snapshot(), after, "Rejected later line creates no partial quote or other facts");
      const conflict = await api(b, "/api/purchasing/quotes/", invalid, submitted.key);
      assert.equal(conflict.status, 400, "Existing quantity validation precedes idempotency lookup");
      const changed = structuredClone(submitted.payload);
      changed.lines[1].quantity = "2.76";
      assert.equal((await api(b, "/api/purchasing/quotes/", changed, submitted.key)).status, 409);
      assert.deepEqual(snapshot(), after);

      // Whole-quote special_order remains the only supported alternate kind.
      await openFunction(b, "retail", "quote");
      await choose(b, "#pw-quote-kind", "special_order");
      await fill(b, "#pw-payment-terms-reason", "TEST ONLY existing full-payment terms");
      const count = snapshot().quotes.length;
      await b.click('form:has(#pw-quote-kind) button[type="submit"]');
      await b.wait('document.querySelector(".pw-quote")?.textContent.includes("special_order")');
      assert.equal(snapshot().quotes.length, count + 1);
      const writes = await b.evaluate("window.__multilineWrites");
      const special = writes.at(-1).payload;
      assert.equal(special.kind, "special_order");
      assert.equal(special.lines.length, 2);
      assert.ok(special.lines.every(line => !("location_id" in line)));

      await choose(b, '[aria-label="Business unit"]', fixture.roche);
      await b.wait('document.querySelector("form:has(#pw-quote-kind) button[type=submit]").disabled');
      assert.equal(await b.evaluate('document.querySelector(".pw-quote")?.textContent.includes("49.86") ?? false'), false);
      const scoped = snapshot();
      assert.equal((await api(b, "/api/purchasing/quotes/", {
        ...submitted.payload, business_unit_id: fixture.roche,
      }, `TEST_ONLY_multiline_other_bu_${width}`)).status, 403);
      assert.deepEqual(snapshot(), scoped);
      await choose(b, '[aria-label="Business unit"]', fixture.unit);
      control("ordinary");
      // A real reload must explicitly accept the browser's unsaved-work
      // prompt; do not bypass the application guard or leave CDP blocked.
      let reloadResult;
      const reload = b.evaluate("location.reload()").then(
        () => { reloadResult = { ok: true }; },
        error => { reloadResult = { error }; },
      );
      let handledDialogs = b.dialogs.length;
      while (!reloadResult) {
        if (b.dialogs.length > handledDialogs) {
          const prompt = b.dialogs.at(-1);
          assert.equal(prompt.type, "beforeunload");
          await b.handleDialog(true);
          handledDialogs = b.dialogs.length;
        }
        await new Promise(resolve => setTimeout(resolve, 20));
      }
      await reload;
      if (reloadResult.error && !/context.*destroyed/i.test(reloadResult.error.message)) throw reloadResult.error;
      await b.wait('!!document.querySelector(".sidebar")');
      const detail = await api(b, `/api/purchasing/quotes/${replay.body.quote_id}/?business_unit_id=${fixture.unit}`);
      assert.equal(detail.status, 200);
      const protectedKeys = new Set(["cost", "unit_cost", "landed_cost", "supplier_unit_cost",
        "inventory_value", "on_hand_value", "gross_margin", "gross_margin_percent", "cost_of_goods_sold"]);
      function noCosts(value) {
        if (Array.isArray(value)) value.forEach(noCosts);
        else if (value && typeof value === "object") for (const [key, child] of Object.entries(value)) {
          assert.equal(protectedKeys.has(key), false, `Unauthorized private field ${key}`);
          noCosts(child);
        }
      }
      noCosts(detail.body);
      await lookup(b, "retail", replay.body.quote_id);
      assert.equal(await b.evaluate('document.querySelector(".pw-quote").getClientRects().length > 0'), true);
      const visibleQuote = await b.evaluate('document.querySelector(".pw-quote").textContent');
      for (const value of ["49.86", "1.25", "2.75", "TEST ONLY linen", "TEST ONLY oak"]) {
        assert.ok(visibleQuote.includes(value), `Actual visible quote retains ${value}`);
      }
      assert.equal(await b.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), true);
      assert.equal(await b.evaluate('/landed cost|gross margin|supplier cost/i.test(document.querySelector(".pw-quote").textContent)'), false);
      control("revoke");
      const revoked = snapshot();
      assert.equal((await api(b, "/api/purchasing/quotes/", submitted.payload, submitted.key)).status, 403);
      assert.deepEqual(snapshot(), revoked);
      assert.equal(b.calls.filter(call => call.method === "POST" && call.path === "/api/purchasing/quotes/").length, 8);
      b.assertSafe();
    } catch (error) {
      console.error(`MULTILINE ${width}px: ${error.message}`);
      await b.handleDialog(false).catch(() => {});
      await writeFile(`${output}/multiline-failure-${width}.txt`, await b.evaluate("document.body.innerText")).catch(() => {});
      await b.screenshot(`multiline-failure-${width}`, "retail-real-browser").catch(() => {});
      throw error;
    } finally {
      await b.close();
    }
  });
}
}

test("real Retail stock quote → permitted discount → hold → lost-ack deposit replay → Delivery readiness; authority and oversell enforced", async () => {
  let dropPayment = true;
  const allowed = ["POST /api/auth/login/", `POST /api/units/${fixture.unit}/select/`];
  const b = await browserHarness({
    width: 1280, height: 900, realBackendOrigin: process.env.REAL_BACKEND_ORIGIN,
    allowedRealWrites: allowed,
    beforeRealResponse: async call => {
      if (call.method === "POST" && /\/payments\/$/.test(call.path) && call.status === 201 && dropPayment) {
        dropPayment = false;
        throw new Error("TEST ONLY lost payment acknowledgement after actual commit");
      }
    },
  });
  try {
    await login(b);
    const baseline = snapshot();
    allowed.push("POST /api/purchasing/quotes/");
    const quoteId = await createQuote(b);
    const prefix = `/api/purchasing/quotes/${quoteId}`;
    const quote = (await api(b, `${prefix}/?business_unit_id=${fixture.unit}`)).body;
    assert.equal(quote.status, "approved");
    assert.equal(quote.total_quote_value, "3800.00");
    assert.equal(quote.lines[0].selling_price_snapshot, "2000.00");
    assert.equal(quote.discount_percent, "5.00");
    assert.deepEqual(snapshot().prices, baseline.prices, "Discount never rewrites approved prices");
    const beforeWorklist = snapshot();
    await openFunction(b, "retail", "collections");
    await b.wait('!!document.querySelector(".rc-table")');
    let collection = await api(b, `/api/purchasing/collections/?business_unit_id=${fixture.unit}&quote_id=${quoteId}`);
    assert.equal(collection.status, 200);
    assert.equal(collection.body.rows[0].recorded_collections, "0.00");
    assert.equal(collection.body.rows[0].remaining_balance, "3800.00");
    assert.doesNotMatch(JSON.stringify(collection.body), /unit_cost|landed_cost|gross_margin|supplier_id/);
    assert.deepEqual(snapshot(), beforeWorklist, "Opening the worklist creates no business facts");
    await b.evaluate(`(() => {
      const row=[...document.querySelectorAll(".rc-table tbody tr")].find(r=>r.querySelector("th").textContent===${JSON.stringify("#"+quoteId)});
      row.querySelector("button").click();
    })()`);
    await b.wait(`location.hash==="#/retail/actions" && document.querySelector(".pw-quote")?.textContent.includes(${JSON.stringify(String(quoteId))})`);
    let target = await actionForm(b, "Request temporary hold");
    await fill(b, `${target} input`, "3");
    allowed.push(`POST ${prefix}/holds/`);
    await b.click(`${target} button`);
    await b.wait('document.querySelector(".pw-quote")?.textContent.includes("held")');
    assert.equal(snapshot().balances[0].reserved_quantity, "0.00",
      "Existing temporary holds record intent; stock reservation waits for the deposit threshold");
    const held = (await api(b, `${prefix}/?business_unit_id=${fixture.unit}`)).body;
    assert.equal(held.holds[0].quantity, "2.00");
    assert.equal(held.holds[0].working_days, 3);
    // Observe only this operation's safe identity/body; no auth/CSRF material.
    await b.evaluate(`(() => {const original=window.fetch;window.retailPayments=[];
      window.fetch=function(input,init){const path=typeof input==="string"?input:input.url;
        if(init?.method==="POST"&&path.endsWith("/payments/"))window.retailPayments.push({
          body:JSON.parse(init.body),key:new Headers(init.headers).get("Idempotency-Key")});
        return original.apply(this,arguments);};})()`);
    target = await actionForm(b, "Record simulated payment");
    await fill(b, `${target} input[type="number"]`, "1900.00");
    allowed.push(`POST ${prefix}/payments/`);
    await b.click(`${target} button`);
    await b.wait('document.querySelector(".pw-notice[role=alert]")?.textContent.includes("502")');
    const committed = snapshot();
    assert.equal(committed.payments, 1);
    assert.equal(committed.reservations, 1);
    assert.equal(committed.balances[0].reserved_quantity, "2.00");
    await b.click(`${target} button`);
    await b.wait('document.querySelector(".pw-quote")?.textContent.includes("confirmed")');
    assert.deepEqual(snapshot(), committed, "Replay duplicates neither collection, reservation nor audit");
    const identities = await b.evaluate("window.retailPayments");
    assert.equal(identities.length, 2, "No automatic payment retries");
    assert.ok(identities[0].key);
    assert.deepEqual(identities[0], identities[1]);
    await openFunction(b, "retail", "collections");
    await b.wait('!!document.querySelector(".rc-table")');
    collection = await api(b, `/api/purchasing/collections/?business_unit_id=${fixture.unit}&quote_id=${quoteId}`);
    assert.equal(collection.body.rows[0].recorded_collections, "1900.00");
    assert.equal(collection.body.rows[0].remaining_balance, "1900.00");
    assert.deepEqual(snapshot(), committed, "Refreshing the list after retry creates no extra collection");
    await b.wait('document.querySelector(".rc-table")?.textContent.includes("1900.00")');
    await b.screenshot("retail-collection-follow-up", "retail-real-browser");
    await lookup(b, "delivery", quoteId);
    await openFunction(b, "delivery", "handover");
    target = await actionForm(b, "Notify customer that goods are ready");
    allowed.push(`POST ${prefix}/ready/`);
    await b.click(`${target} button`);
    await b.wait('document.querySelector(".pw-quote")?.textContent.includes("ready")');
    const ready = snapshot().quotes.find(q => q.pk === quoteId);
    assert.equal(ready.status, "ready");
    assert.ok(ready.ready_at);
    assert.equal(ready.delivered_at, null);
    await b.screenshot("stock-ready", "retail-real-browser");

    const makeBody = (quantity, discount) => ({
      business_unit_id:fixture.unit, customer_id:fixture.customer, kind:"stock",
      discount_percent:discount, discount_reason:"TEST ONLY boundary verification", payment_plan:"full",
      lines:[{variant_id:fixture.variant,location_id:fixture.location,quantity,specifications:{test:"TEST ONLY boundary"}}],
    });
    const pending = await api(b, "/api/purchasing/quotes/", makeBody("1.00", "20.00"), "TEST-ONLY-outside-sales-ceiling");
    assert.equal(pending.status, 201);
    assert.equal(pending.body.status, "pending_approval");
    allowed.push(`POST /api/purchasing/quotes/${pending.body.quote_id}/discount-approval/`);
    let before = snapshot();
    assert.equal((await api(b, `/api/purchasing/quotes/${pending.body.quote_id}/discount-approval/`,
      {reason:"TEST ONLY denied salesperson approval"}, "TEST-ONLY-denied-discount")).status, 403);
    assert.deepEqual(snapshot(), before);
    const excess = await api(b, "/api/purchasing/quotes/", makeBody("9.00", "0.00"), "TEST-ONLY-excess-quote");
    assert.equal(excess.status, 201);
    const excessPath = `/api/purchasing/quotes/${excess.body.quote_id}`;
    allowed.push(`POST ${excessPath}/payments/`);
    before = snapshot();
    assert.equal((await api(b, `${excessPath}/payments/`,
      {amount:"9000.00",method:"simulated_cash",purpose:"quote"}, "TEST-ONLY-excess-deposit")).status, 400);
    assert.deepEqual(snapshot(), before, "Oversell rolls back collection, reservations and audit");

    await createQuote(b, true);
    const special = snapshot().quotes.at(-1);
    assert.equal(special.kind, "special_order");
    const specialDetail = (await api(b, `/api/purchasing/quotes/${special.pk}/?business_unit_id=${fixture.unit}`)).body;
    assert.equal(specialDetail.payment_plan, "full");
    assert.equal(specialDetail.special_order_value_bands_configured, false);
    assert.equal(snapshot().payments, 1);
    assert.equal(snapshot().balances[0].reserved_quantity, "2.00");
    await b.screenshot("special-order", "retail-real-browser");
    b.assertSafe();
    await writeFile(`${output}/workflow-trace.json`, JSON.stringify(b.calls, null, 2));
    await writeFile(`${output}/navigation-decisions.json`, JSON.stringify(navigationDecisions,null,2));
    await writeFile(`${process.env.ACCEPTANCE_ROOT}/verified-quote.json`, JSON.stringify({quoteId}));
  } catch (error) {
    await writeFile(`${output}/workflow-failure.json`, JSON.stringify({calls:b.calls,dialogs:b.dialogs},null,2));
    try {
      if (b.dialogs.length > 0) await b.handleDialog(false);
    } catch { /* No open dialog; preserve the original assertion failure. */ }
    try {
      await writeFile(`${output}/workflow-failure-ui.txt`, await b.evaluate("document.body.innerText"));
      await b.screenshot("workflow-failure", "retail-real-browser");
    } catch { /* Browser failure must not obscure the original test error. */ }
    throw error;
  } finally { await b.close(); }
});

test("real collection function Favorite reload and narrow-screen keyboard search stay read-only", async t => {
  let quoteId;
  try { ({quoteId} = JSON.parse(await readFile(`${process.env.ACCEPTANCE_ROOT}/verified-quote.json`, "utf8"))); }
  catch (error) { if (error.code !== "ENOENT") throw error; t.skip("Prior stock journey failed"); return; }
  const b = await browserHarness({width:320, height:844,
    realBackendOrigin:process.env.REAL_BACKEND_ORIGIN,
    allowedRealWrites:["POST /api/auth/login/", `POST /api/units/${fixture.unit}/select/`, "POST /api/favorites/"],
  });
  try {
    await login(b);
    await openFunction(b, "retail", "collections");
    await b.wait('!!document.querySelector(".rc-table")');
    const before = snapshot();
    await fill(b, "#rc-quote-search", String(quoteId));
    await b.key("Enter");
    await b.wait('document.querySelectorAll(".rc-table tbody tr").length===1');
    assert.equal(await b.evaluate('document.querySelector(".rc-table tbody th").textContent'), `#${quoteId}`);
    assert.deepEqual(snapshot(), before, "Keyboard search creates no business facts");
    await b.key("Tab");
    assert.equal(await b.evaluate('document.activeElement?.tagName'), "BUTTON");
    assert.notEqual(await b.evaluate('getComputedStyle(document.activeElement).outlineStyle'), "none");
    assert.ok(await b.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'),
      "320px collection workspace has no page-level horizontal overflow");
    const pin = await api(b, "/api/favorites/", {destination:"retail.collections"});
    assert.equal(pin.status, 201, "Existing real Favorites handler accepts the function key");
    const pinned = snapshot();
    await b.reload();
    await b.wait('!!document.querySelector(".rc-table")');
    assert.ok(await b.evaluate('!!document.querySelector(\'button[aria-label*="Collection follow-up"][aria-label^="Remove"]\')'));
    assert.deepEqual(snapshot(), pinned, "Reload preserves the personal function pin without additional writes");
    assert.ok((await api(b, "/api/favorites/")).body.favorites.some(f => f.destination === "retail.collections"));
    await b.screenshot("collection-mobile-keyboard", "retail-real-browser");
    b.assertSafe();
  } finally { await b.close(); }
});

test("real Delivery preparation uses independent stock scope, local keyboard review, function Favorite and 320px layout without business writes", async () => {
  const {quoteId} = JSON.parse(await readFile(`${process.env.ACCEPTANCE_ROOT}/verified-quote.json`, "utf8"));
  for (const width of [1280, 320]) {
    const b = await browserHarness({width, height:900,
      realBackendOrigin:process.env.REAL_BACKEND_ORIGIN,
      allowedRealWrites:["POST /api/auth/login/", `POST /api/units/${fixture.unit}/select/`, "POST /api/favorites/"],
    });
    try {
      await login(b);
      await openFunction(b, "delivery", "preparation");
      const before = snapshot();
      await fill(b, "#pw-quote-id", quoteId); await b.key("Enter");
      await b.wait('!!document.querySelector(".dp-line")');
      assert.ok(b.calls.some(call => call.path.endsWith(`/${quoteId}/preparation/`) && call.status === 200));
      assert.equal(await b.evaluate('document.querySelector("h1").textContent'), "Picking preparation");
      assert.ok(await b.evaluate('document.querySelector(".dp-line").textContent.includes("TEST")'));
      assert.ok(await b.evaluate('!document.querySelector(".delivery-preparation").textContent.match(/unit_cost|gross_margin|landed_cost/)'));
      await b.click(".dp-specifications summary");
      await b.key("Tab");
      assert.equal(await b.evaluate('document.activeElement.type'), "checkbox");
      assert.equal(await b.evaluate('getComputedStyle(document.activeElement.nextElementSibling).outlineStyle'), "solid");
      await b.key(" ");
      assert.equal(await b.evaluate('document.querySelector(".dp-review-check input").checked'), true);
      assert.ok((await b.accessibility()).some(node => node.role?.value === "checkbox" && /local review only/.test(node.name?.value)));
      assert.deepEqual(snapshot(), before, "Preparation GET, disclosure and local review have no business writes");
      const geometry = await b.evaluate(`(() => {
        const w=document.documentElement.clientWidth;
        return {width:w, scroll:document.documentElement.scrollWidth,
          outside:[...document.querySelectorAll(".delivery-preparation *")].filter(n=>n.getBoundingClientRect().right>w+1)
            .map(n=>({tag:n.tagName, class:n.className, right:n.getBoundingClientRect().right, font:getComputedStyle(n).fontSize})).slice(0,12)};
      })()`);
      assert.ok(geometry.scroll <= geometry.width + 1,
        `${width}px preparation must have no page-level horizontal overflow: ${JSON.stringify(geometry)}`);
      if (width === 320) {
        assert.equal(await b.evaluate('getComputedStyle(document.querySelector(".dp-quantity-grid")).gridTemplateColumns.split(" ").length'), 1);
        assert.ok(await b.evaluate('[...document.querySelectorAll(".dp-quantity-grid dd")].every(n=>parseFloat(getComputedStyle(n).fontSize)>=12 && n.scrollWidth<=n.clientWidth+1)'),
          "Mobile quantities and identities must be legible, not merely hidden by overflow");
      }
      await b.screenshot(`delivery-preparation-${width}`, "delivery-preparation");
      const favorite = await api(b, "/api/favorites/", {destination:"delivery.preparation"});
      assert.ok([200,201].includes(favorite.status));
      await b.reload();
      await b.wait('!!document.querySelector(".delivery-preparation")');
      assert.ok((await api(b, "/api/favorites/")).body.favorites.some(row => row.destination === "delivery.preparation"));
      assert.equal(await b.evaluate('document.querySelectorAll(".dp-review-check input:checked").length'), 0);
      b.assertSafe();
    } catch (error) {
      await b.screenshot(`delivery-preparation-failure-${width}`, "delivery-preparation");
      throw error;
    } finally { await b.close(); }
  }
});

test("real Manager approval and denial → full-payment gate → final handover and lost-ack replay without duplicate stock/delivery facts", async () => {
  const authorAllowed = ["POST /api/auth/login/", `POST /api/units/${fixture.unit}/select/`, "POST /api/purchasing/quotes/"];
  const author = await browserHarness({ width:1280,height:900,
    realBackendOrigin:process.env.REAL_BACKEND_ORIGIN,allowedRealWrites:authorAllowed });
  let approvedId, deniedId;
  try {
    await login(author);
    approvedId = await createQuote(author, false, "15");
    await openFunction(author,"retail","actions");
    let form = await actionForm(author,"Approve price decision");
    assert.equal(await author.evaluate(`document.querySelector('${form} button').disabled`), true,
      "Salesperson cannot approve a 15% decision");
    assert.match(await author.evaluate(`document.querySelector('${form}').innerText`), /No active assigned role/i);
    deniedId = await createQuote(author, false, "25");
    assert.equal(snapshot().quotes.find(q=>q.pk===approvedId).status,"pending_approval");
    assert.equal(snapshot().quotes.find(q=>q.pk===deniedId).status,"pending_approval");
    author.assertSafe();
    await writeFile(`${output}/approval-author-trace.json`,JSON.stringify(author.calls,null,2));
  } finally { await author.close(); }

  let dropDelivery = true;
  const allowed = ["POST /api/auth/login/", `POST /api/units/${fixture.unit}/select/`];
  const browserOptions = { width:1280,height:900,
    realBackendOrigin:process.env.REAL_BACKEND_ORIGIN,allowedRealWrites:allowed,
    beforeRealResponse: async call => {
      if (call.method==="POST" && /\/deliver\/$/.test(call.path) && call.status===200 && dropDelivery) {
        dropDelivery = false;
        throw new Error("TEST ONLY lost handover acknowledgement after actual commit");
      }
    },
  };
  let b = await browserHarness(browserOptions);
  try {
    await login(b, fixture.manager);
    await lookup(b,"retail",deniedId);
    let form = await actionForm(b,"Approve price decision");
    assert.equal(await b.evaluate(`document.querySelector('${form} button').disabled`),true,
      "Manager's existing 20% ceiling cannot authorize 25%");
    assert.match(await b.evaluate(`document.querySelector('${form}').innerText`),/No active assigned role/i);
    let before = snapshot();
    allowed.push(`POST /api/purchasing/quotes/${deniedId}/discount-approval/`);
    assert.equal((await api(b,`/api/purchasing/quotes/${deniedId}/discount-approval/`,
      {reason:"TEST ONLY Manager cannot approve 25 percent"},"TEST-ONLY-manager-denied-25")).status,403);
    assert.deepEqual(snapshot(),before,"Denied approval writes no operation, approval or audit");
    await b.screenshot("manager-denied","retail-real-browser");

    await lookup(b,"retail",approvedId);
    form = await actionForm(b,"Approve price decision");
    await fill(b,`${form} input`,"TEST ONLY Manager approval within existing 20 percent ceiling");
    await b.wait(`!document.querySelector('${form} button').disabled`);
    const prefix = `/api/purchasing/quotes/${approvedId}`;
    allowed.push(`POST ${prefix}/discount-approval/`, `POST ${prefix}/payments/`,
      `POST ${prefix}/ready/`, `POST ${prefix}/deliver/`);
    const prices = snapshot().prices;
    await b.click(`${form} button`);
    await b.wait('document.querySelector(".pw-quote")?.textContent.includes("approved")');
    let facts = snapshot();
    assert.equal(facts.quotes.find(q=>q.pk===approvedId).total_quote_value,"3400.00");
    assert.equal(facts.approvals.filter(a=>a.quote_id===approvedId&&a.kind==="discount").length,1);
    assert.equal(facts.approvals.find(a=>a.quote_id===approvedId&&a.kind==="discount").role,"manager");
    assert.deepEqual(facts.prices,prices);
    await b.screenshot("manager-approved","retail-real-browser");
    b.assertSafe();
    await writeFile(`${output}/manager-trace.json`,JSON.stringify(b.calls,null,2));
    await b.close();
    b = null;
    b = await browserHarness(browserOptions);
    await login(b);
    await lookup(b,"retail",approvedId);

    form = await actionForm(b,"Record simulated payment");
    await fill(b,`${form} input[type=number]`,"1700.00");
    await b.click(`${form} button`);
    await b.wait('document.querySelector(".pw-quote")?.textContent.includes("confirmed")');
    await lookup(b,"delivery",approvedId);
    await openFunction(b,"delivery","handover");
    form = await actionForm(b,"Notify customer that goods are ready");
    await b.click(`${form} button`);
    await b.wait('document.querySelector(".pw-quote")?.textContent.includes("ready")');
    before = snapshot();
    const readyAt = before.quotes.find(q=>q.pk===approvedId).ready_at;
    const deliver = async () => {
      await b.wait('[...document.querySelectorAll("button")].some(n=>n.textContent==="Record delivery"&&!n.disabled)');
      await b.evaluate(`document.querySelectorAll("[data-handover-button]").forEach(n=>n.removeAttribute("data-handover-button"));
        [...document.querySelectorAll("button")].find(n=>n.textContent==="Record delivery")?.setAttribute("data-handover-button","current")`);
      await b.click('[data-handover-button="current"]');
    };
    await deliver();
    await b.wait('document.querySelector(".pw-notice[role=alert]")?.textContent.includes("Full payment")');
    assert.deepEqual(snapshot(),before,"Unpaid handover rolls back every business/audit/idempotency fact");

    form = await actionForm(b,"Record simulated payment");
    await fill(b,`${form} input[type=number]`,"1700.00");
    await b.click(`${form} button`);
    await b.wait('[...document.querySelectorAll(".pw-notice[role=status]")].some(n=>n.textContent.includes("Simulated payment recorded"))');
    await b.wait('[...document.querySelectorAll("button")].some(n=>n.textContent==="Record test payment"&&!n.disabled)');
    const settled = (await api(b,`${prefix}/?business_unit_id=${fixture.unit}`)).body;
    assert.equal(settled.payments.reduce((sum,p)=>sum+Number(p.amount),0),3400);
    assert.equal(snapshot().quotes.find(q=>q.pk===approvedId).status,"ready");
    assert.equal(snapshot().quotes.find(q=>q.pk===approvedId).ready_at,readyAt);
    await b.evaluate(`(() => {const original=window.fetch;window.retailHandovers=[];window.handoverPublicFields=[];
      window.fetch=function(input,init){const path=typeof input==="string"?input:input.url;
        if(init?.method==="POST"&&path.endsWith("/deliver/"))window.retailHandovers.push({
          body:JSON.parse(init.body),key:new Headers(init.headers).get("Idempotency-Key")});
        const response=original.apply(this,arguments);
        if(init?.method==="POST"&&path.endsWith("/deliver/"))return response.then(async r=>{
          if(r.ok){const result=await r.clone().json();window.handoverPublicFields.push(
            (result.delivery_results??[]).map(row=>Object.keys(row).sort()));}
          return r;});
        return response;};})()`);
    before = snapshot();
    await deliver();
    await b.wait('document.querySelector(".pw-notice[role=alert]")?.textContent.includes("502")');
    assert.equal(dropDelivery,false,"The existing HTTP 200 commit was acknowledged by the backend, then lost only in the test proxy");
    const committed = snapshot();
    const delivered = committed.quotes.find(q=>q.pk===approvedId);
    assert.equal(delivered.status,"delivered");
    assert.ok(delivered.delivered_at);
    assert.equal(delivered.ready_at,readyAt);
    assert.equal(committed.balances[0].on_hand_quantity,"8.00");
    assert.equal(committed.balances[0].reserved_quantity,"2.00","Only the earlier quote's reservation remains");
    assert.equal(committed.deliveries.length,before.deliveries.length+1);
    assert.equal(committed.deliveries.at(-1).quantity,"2.00");
    assert.equal(committed.deliveries.at(-1).quote_line__quote_id,approvedId);
    assert.equal(committed.delivery_movements.length,before.delivery_movements.length+1);
    assert.equal(committed.delivery_movements.at(-1).quantity_delta,"-2.00");
    assert.equal(committed.journal_count,before.journal_count+1);
    await deliver();
    await b.wait('document.querySelector(".pw-quote .pw-status")?.textContent==="delivered"');
    assert.deepEqual(snapshot(),committed,"Exact replay changes no delivery, ledger, payment, journal, audit or timestamp");
    const identities = await b.evaluate("window.retailHandovers");
    assert.equal(identities.length,2,"No automatic handover retries");
    assert.ok(identities[0].key);
    assert.deepEqual(identities[0],identities[1]);
    assert.deepEqual(await b.evaluate("window.handoverPublicFields"), [[[
      "journal_entry_id","movement_id","quantity_delivered","reservation_id","revenue",
    ]]],"The actual handover HTTP response must not disclose internal cost fields");
    assert.equal((await api(b,`${prefix}/deliver/`,{},"TEST-ONLY-second-handover-denied")).status,400);
    assert.deepEqual(snapshot(),committed,"A new key cannot perform a second handover either");
    assert.equal(await b.evaluate('[...document.querySelectorAll("button")].some(n=>n.textContent==="Record delivery"&&n.getClientRects().length)'),false);
    const detail = (await api(b,`${prefix}/?business_unit_id=${fixture.unit}`)).body;
    assert.equal(new Date(detail.delivery_progress.delivered_at).toISOString(),new Date(delivered.delivered_at).toISOString());
    assert.equal(detail.delivery_progress.lines[0].delivered_quantity,"2.00");
    assert.equal(detail.delivery_progress.lines[0].remaining_quantity,"0.00");
    await b.wait('document.body.innerText.includes("Delivery progress · read only")');
    await b.wait(`[...document.querySelectorAll(".pw-quote .pw-field")].some(n=>
      n.querySelector("span")?.textContent==="Delivered at" &&
      n.querySelector("strong")?.textContent===${JSON.stringify(detail.delivery_progress.delivered_at)})`);
    await b.screenshot("handover-delivered","retail-real-browser");
    const beforeRegister = snapshot();
    await openFunction(b,"delivery","deliveries");
    await b.wait('document.body.innerText.includes("The server did not authorize this delivered sales view")');
    assert.ok(b.calls.some(c=>c.path==="/api/reports/sales/deliveries/"&&c.status===403),
      "Purchasing/stock grants do not supply the independently required SALES report panel/data grants");
    assert.equal(await isVisible(b,".pr-table"),false);
    assert.deepEqual(snapshot(),beforeRegister,"Denied register reads do not alter delivery facts");
    await b.screenshot("delivery-register-denied","retail-real-browser");
    b.assertSafe();
    await writeFile(`${output}/handover-trace.json`,JSON.stringify(b.calls,null,2));
  } catch(error) {
    await writeFile(`${output}/handover-failure.json`,JSON.stringify({calls:b?.calls,dialogs:b?.dialogs},null,2));
    try {
      await writeFile(`${output}/handover-failure-ui.txt`,await b.evaluate("document.body.innerText"));
      await b.screenshot("handover-failure","retail-real-browser");
    } catch { /* Preserve original assertion. */ }
    throw error;
  } finally { await b?.close(); }
});

test("real ordinary-sales masking, BU switching and subsequent grant revocation clear and deny stale quote/stock access", async t => {
  let quoteId;
  try { ({quoteId} = JSON.parse(await readFile(`${process.env.ACCEPTANCE_ROOT}/verified-quote.json`, "utf8"))); }
  catch (error) { if (error.code !== "ENOENT") throw error; t.skip("Prior real stock journey failed; no verified quote to reuse"); return; }
  control("ordinary");
  const allowed = ["POST /api/auth/login/", `POST /api/units/${fixture.unit}/select/`, `POST /api/units/${fixture.roche}/select/`];
  const b = await browserHarness({ width:1280,height:900,
    realBackendOrigin:process.env.REAL_BACKEND_ORIGIN,allowedRealWrites:allowed });
  try {
    await login(b);
    await lookup(b, "retail", quoteId);
    const before = snapshot();
    const stock = await api(b, `/api/stock-balances/?unit=${fixture.unit}`);
    assert.equal(stock.status, 200);
    const rows = stock.body.results ?? stock.body.rows;
    assert.ok(rows.length > 0);
    const fields = ["id","item_code","item_description","brand_name","brand_code","variant_sku","variant_description",
      "location_code","location_name","unit_code","unit_name","quantity","is_synthetic"].sort();
    assert.deepEqual(Object.keys(rows[0]).sort(), fields);
    assert.equal(rows[0].quantity, "6.00", "After handover ordinary sales see exact unreserved quantity, not costs or reserved amount");
    const quote = await api(b, `/api/purchasing/quotes/${quoteId}/?business_unit_id=${fixture.unit}`);
    assert.equal(quote.status, 200);
    assert.doesNotMatch(JSON.stringify(quote.body), /unit_cost|landed_cost|gross_margin|purchase_value|supplier_id/);
    assert.equal((await api(b, `/api/governance/purchasing/${fixture.unit}/`)).status, 403);
    assert.equal((await api(b, "/api/governance/purchasing/counterparties/?party_type=customer")).status, 403);
    assert.deepEqual(snapshot(), before, "Ordinary authorized reads create no business facts");
    await choose(b, '[aria-label="Business unit"]', fixture.roche);
    assert.equal(await b.evaluate("document.documentElement.dataset.unitTheme"), "roche");
    await b.wait('!document.querySelector(".pw-quote")');
    assert.equal((await api(b, `/api/purchasing/quotes/${quoteId}/?business_unit_id=${fixture.roche}`)).status, 404);
    assert.deepEqual(snapshot(), before);
    control("revoke");
    const revoked = snapshot();
    assert.equal((await api(b, `/api/stock-balances/?unit=${fixture.unit}`)).status, 403);
    assert.equal((await api(b, `/api/purchasing/quotes/${quoteId}/?business_unit_id=${fixture.unit}`)).status, 404);
    allowed.push(`POST /api/purchasing/quotes/${quoteId}/ready/`);
    assert.equal((await api(b, `/api/purchasing/quotes/${quoteId}/ready/`, {},
      "TEST-ONLY-after-revocation")).status, 404);
    await b.reload();
    await b.wait('!!document.querySelector(".sidebar")');
    await openFunction(b,"retail","main");
    assert.equal(await isVisible(b, ".pw-quote"), false);
    assert.deepEqual(snapshot(), revoked, "Revoked reads/actions cannot mutate business state");
    b.assertSafe();
    await b.screenshot("ordinary-denied", "retail-real-browser");
    await writeFile(`${output}/scope-trace.json`, JSON.stringify(b.calls,null,2));
  } catch (error) {
    await writeFile(`${output}/scope-failure.json`, JSON.stringify({calls:b.calls,dialogs:b.dialogs},null,2));
    try {
      await writeFile(`${output}/scope-failure-ui.txt`, await b.evaluate("document.body.innerText"));
      await b.screenshot("scope-failure", "retail-real-browser");
    } catch { /* Preserve the original failure. */ }
    throw error;
  } finally { await b.close(); }
});

registerMultilineTests();
registerBilingualQuotationTests();
registerCombinedJourneyTests();
registerQuotationAccessTests();

for (const width of [1280, 320]) {
  test(`real nonstaff document completeness and long quotation preview at ${width}px`, async () => {
    control("quotation");
    const b = await browserHarness({
      width, height: width === 320 ? 1000 : 900,
      realBackendOrigin: process.env.REAL_BACKEND_ORIGIN,
      allowedRealWrites: [
        "POST /api/auth/login/", `POST /api/units/${fixture.unit}/select/`,
        "POST /api/purchasing/quotes/",
      ],
    });
    try {
      await login(b);
      const me = await api(b, "/api/auth/me/");
      assert.equal(me.body.is_staff ?? me.body.user?.is_staff, false);
      const before = snapshot();
      const lines = Array.from({ length: 20 }, (_, i) => ({
        variant_id: i % 2 ? fixture.alternate_variant : fixture.variant,
        location_id: fixture.location, quantity: i % 2 ? "2.75" : "1.25",
        specifications: { en: `TEST ONLY line ${i + 1}. Explicit synthetic specification.`,
          ar: "مواصفات تجريبية فقط" },
      }));
      const payload = {
        business_unit_id: fixture.unit, customer_id: fixture.customer,
        kind: "stock", discount_percent: "5.25", discount_reason: "TEST ONLY existing authority",
        payment_plan: "full", lines, presentation: {
          contact_person_en: "TEST ONLY document contact", contact_person_ar: "جهة اتصال تجريبية",
          project_en: "TEST ONLY project", project_ar: "مشروع تجريبي",
          address_en: "TEST ONLY address with long readable synthetic text", address_ar: "عنوان تجريبي فقط",
          phone: "TEST ONLY phone", mobile: "TEST ONLY mobile", email: "synthetic@example.invalid",
          notes_en: "TEST ONLY notes; not an operational promise", notes_ar: "ملاحظات تجريبية",
          department_en: "TEST ONLY department", department_ar: "قسم تجريبي",
          payment_terms_en: "TEST ONLY displayed terms, no gate override", payment_terms_ar: "نص تجريبي",
          delivery_time_en: "TEST ONLY estimate supplied explicitly", delivery_time_ar: "تقدير تجريبي",
          line_units: lines.map((_, i) => ({ en: `TEST ONLY unit ${i + 1}`, ar: "وحدة تجريبية" })),
        },
      };
      const key = `TEST_ONLY_document_preview_${width}`;
      const created = await api(b, "/api/purchasing/quotes/", payload, key);
      assert.equal(created.status, 201, JSON.stringify(created.body));
      const id = created.body.quote_id;
      const after = snapshot();
      assert.equal(after.quotes.length, before.quotes.length + 1);
      for (const field of ["payments", "reservations", "balances", "deliveries", "delivery_movements", "journal_count"]) {
        assert.deepEqual(after[field], before[field], `Quotation cannot post ${field}`);
      }
      const replay = await api(b, "/api/purchasing/quotes/", payload, key);
      assert.deepEqual(replay.body, created.body);
      assert.deepEqual(snapshot(), after, "Exact replay creates no duplicate document or facts");
      await openFunction(b, "retail", "actions");
      await fill(b, "#pw-quote-id", id);
      await b.key("Enter");
      await b.wait('document.querySelectorAll(".pw-preview-page").length === 5');
      const read = await api(b, `/api/purchasing/quotes/${id}/?business_unit_id=${fixture.unit}`);
      assert.equal(read.status, 200);
      assert.equal(read.body.presentation_document.total_quote_value, "498.60");
      assert.equal(read.body.presentation_document.company.name_en, "Tivoli Group L.L.C");
      assert.equal(read.body.presentation_document.company.trademark_en, "Tivoli");
      assert.equal(read.body.presentation_document.company.name_ar, "");
      assert.equal(read.body.presentation_document.client.project_ar, "مشروع تجريبي");
      assert.equal(read.body.presentation_document.valid_until, "", "No proposed validity default is imposed");
      const visible = await b.evaluate('document.querySelector(".pw-presentation-document").innerText');
      for (const text of ["TEST ONLY document contact", "مشروع تجريبي", "synthetic@example.invalid",
        "TEST ONLY address", "TEST ONLY phone", "TEST ONLY mobile", "TEST ONLY unit 20",
        "498.60", "Customer signature", "Salesperson signature", "Pending owner text"]) {
        assert.ok(visible.includes(text), `Authorized document must show ${text}`);
      }
      assert.equal(await b.evaluate('document.querySelectorAll(".pw-presentation-line").length'), 20);
      await b.evaluate('document.querySelector(".pw-presentation-line img").scrollIntoView({block:"center"})');
      await b.wait('document.querySelector(".pw-presentation-line img")?.naturalWidth > 0');
      assert.equal(await b.evaluate('document.querySelectorAll(".pw-presentation-totals").length'), 1);
      assert.equal(await b.evaluate(`(() => {
        const amount=document.querySelector(".pw-presentation-totals > span:last-child b");
        const range=document.createRange();
        range.selectNodeContents(amount.firstChild);
        return range.getClientRects().length;
      })()`), 1, "The exact saved decimal total must not split across visual lines");
      const pages = await b.evaluate('Array.from(document.querySelectorAll(".pw-preview-page")).map(n=>({text:n.innerText, footer:n.querySelector("footer")?.innerText, lines:n.querySelectorAll(".pw-presentation-line").length}))');
      assert.deepEqual(pages.map(p => p.lines), [4, 4, 4, 4, 4]);
      pages.forEach((page, i) => {
        assert.ok(page.text.includes(`#${id}`), "Every preview page keeps the same existing reference");
        assert.ok(page.text.includes("Tivoli Group L.L.C"), "Registered company repeats on every page");
        assert.match(page.footer ?? "", new RegExp(`Page ${i + 1} of 5`));
      });
      assert.equal(await b.evaluate('Array.from(document.querySelectorAll(".pw-presentation-document [lang=ar]")).every(n=>n.dir==="rtl")'), true);
      assert.equal(await b.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'), true);
      assert.equal(await b.evaluate('Array.from(document.querySelectorAll(".pw-presentation-document button, .pw-presentation-document a")).some(n=>/print|download|export/i.test(n.innerText))'), false);
      assert.equal(b.calls.some(c => c.path.includes("/export/")), false);
      assert.deepEqual(snapshot(), after, "Preview/navigation/reads create no business records");
      await b.evaluate('document.querySelector(".pw-presentation-document").scrollIntoView({block:"start"})');
      await b.screenshot(`document-complete-${width}`, "retail-real-browser");
      await b.evaluate('document.querySelectorAll(".pw-preview-page")[4].scrollIntoView({block:"end"})');
      await b.screenshot(`document-final-page-${width}`, "retail-real-browser");
      await writeFile(`${output}/document-complete-${width}-requests.json`,
        JSON.stringify(b.calls.map(({ method, path }) => ({ method, path })), null, 2));
      b.assertSafe();
    } catch (error) {
      await b.screenshot(`document-complete-failure-${width}`, "retail-real-browser").catch(() => {});
      throw error;
    } finally { await b.close(); }
  });
}
