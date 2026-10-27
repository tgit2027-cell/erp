# Retail & Sales — synthetic team acceptance

**Current language acceptance:** all internal menu/form/control/validation text
is English. Optional Arabic customer/item/document text remains valid content.
Only customer quotation/invoice document views must be bilingual English/Arabic.
Check these separately: English quotation-entry controls, then the unchanged
bilingual customer-document preview. Do not expect Arabic ERP navigation.

**Start here:** [Retail pilot review guide — English/Arabic](RETAIL_PILOT_REVIEW_GUIDE.md)
for the concise team walkthrough, role prerequisites, expected results, and
review checklist. This file retains the detailed implementation-specific
acceptance cases and evidence.

## Quotation document completeness review

### Recover a rejected quotation without re-entering it

**Verified in an isolated synthetic backend/browser; not yet shared-team
acceptance.** Use **Retail & Sales → Create a quote** with an existing authorized
sales account and approved item/customer/location choices. Do not add shared
grants or configure prices to make a test pass.

1. Enter two existing items, quantities **1.25** and **2.75**, their permitted
   specifications, and a discount within the current account's authority.
   With the disposable fixture's prices **QAR 19.99 / 10.05** and **5.25%**
   discount, the existing rounded total is **QAR 49.86**. These are test values,
   not a company price list or proposed terms.
2. Enter a whitespace-only discount-decision reason and submit. The server
   rejects it under the existing explicit-reason validation. **Request not
   completed · Quote creation** and its reason must be visible on this focused
   page, not hidden in Quote collection & holds.
3. Check both lines, quantities, specifications and any English/Arabic notes
   remain entered. No quote, payment, reservation, stock movement or journal
   may have been created by the failed request.
4. Replace the reason with a meaningful synthetic test reason and submit once.
   The page shows **Completed · Quote creation**, one quote is created and its
   existing preview loads. Navigation is not automatically changed.
5. The new preview must retain **Tivoli** and **Tivoli Group L.L.C** separately;
   confidential costs/history remain unavailable. A technical explicit replay
   of the successful request must return the original quote, not another one.

Rendered tests also verify a connection-error message is visible, with an
unchanged-request retry retaining its exact key/body. A corrected payload uses
the existing new-key behavior. Real-network failure injection was **not**
performed in the native browser. No automatic retries were added.

Team reviewer / result / feedback: _________________________________

**Identity correction:** for a newly created synthetic quote, every preview
page must retain **Tivoli** branding and separately show the registered company
exactly **Tivoli Group L.L.C**. Arabic registered name remains unavailable.
Historical quotations must continue showing their saved identity, not silently
adopt today's name. There is currently no invoice document view to review.
Retail acceptance remains pending approved T&C/content, output entitlement and
the other documented policy/access decisions; this correction does not close
the whole business section.

Use an owner-prepared **disposable synthetic** session, not real customer data.
The automated authenticated checks use temporary accounts only; shared demo
grants/configuration have not been populated by this batch.

1. Open **Retail & Sales → Create a quote** as an existing permitted fictional
   nonstaff salesperson. Select an existing customer, approved item/variant and
   permitted location; add multiple lines. Never create an item or override
   the approved price.
2. Review quantities/specifications and the existing grade-limited discount.
   Optionally enter an explicit display unit in English/Arabic for each line.
   This label does **not** convert quantity or alter stock.
3. Enter synthetic contact person, project, address, phone/mobile and an
   `example.invalid` email, plus department, displayed payment/delivery text
   and notes. Leave dates/terms blank if not approved. No 30-day default or
   legal/company wording should be invented beyond the confirmed identity above.
4. Submit once and open **Retail & Sales → Quote collection & holds** for the
   resulting reference. Review saved company/customer fields, items/images,
   exact fixed prices/discounts/totals and bilingual missing-content flags.
5. For a long synthetic quote, inspect each screen preview page: same
   reference, sequential bilingual page count, readable wrapped text; grand
   totals and blank signature spaces once at the end. These are not physical
   picking, approval, electronic signatures or print authorization.
6. T&C must say pending owner text. Neither conflicting legacy return/refund/
   reservation/storage clauses nor an invented legal company name should appear.
   No new print/download/export action should be offered.
7. With the existing test instructions, verify unchanged retry identity and
   BU/revoked-access denial; do not modify real/shared grants to make a test pass.

| Persona / BU | Quote reference | Desktop/mobile | Expected vs observed | Feedback |
| --- | --- | --- | --- | --- |
| | | | | |

See [scope and pending inputs](QUOTATION_DOCUMENT_COMPLETENESS.md). The invoice
reference is not an implemented invoice workflow. Shared team acceptance and
commercial/legal readiness remain distinct from isolated automated success.

For all **43 permission-dependent sidebar children**, use the
[all-sections function-page checklist](ALL_SECTIONS_TEAM_ACCEPTANCE.md).
Its navigation evidence does not replace the business steps below.

**Only use a disposable test session prepared by the owner. Never use real
customer details, supplier files, payment cards or the shared demo for these
transaction steps. “Payment” below means a recorded simulation, not money sent.**

## Shared review environment — upgrade verified 2026-10-20

Quotation metadata migration **0022** is now applied to the established
synthetic demo, following a verified private backup and clean isolated restore/
upgrade rehearsal. Normal preview startup was checked without seed/reset.
The configured HTTPS sign-in page and latest bilingual quotation assets
responded successfully at **06:26:25 UTC**; this is a session-bound observation,
not continuous availability or authenticated transaction acceptance.

**What can be reviewed now:** use your existing privately held account and
password to inspect navigation and the appropriate authorized/unavailable
state at **Retail & Sales → Create a quote**, then **Quote collection & holds**.
Do not reset credentials or add shared grants to work around a denied state.
The Agent did not sign in to a shared account.

**Important current limit:** the preserved shared demo has five demo-marked
accounts and nine active **read_stock** grants, but **no create_order grants,
quotation approval-role assignments, approved selling prices or customer
choices**. Therefore a complete quotation transaction in that shared demo is
currently unavailable by design. Upgrading the schema must not enable it by
inventing assignments/configuration. The full ordinary-sales journey below
remains a disposable, explicitly configured synthetic exercise; its real
authenticated browser evidence is recorded in the preceding implementation
handoff, not newly claimed for the shared demo.

## Ordinary sales quotation and bilingual review

**Navigation:** **Retail & Sales → Create a quote** (`#/retail/quote`), then
**Quote collection & holds** (`#/retail/actions`) to inspect the selected
quotation's saved bilingual client presentation. Existing function Favorites
use these same destinations.

Use an owner-prepared **disposable** synthetic session with an existing
nonstaff salesperson, existing BU/approval-role grants, customers and approved
prices. Do not add grants to shared demo users to run this exercise. Shared
schema migration `0022_quotation_presentation` was applied by the later review
upgrade described above, but existing missing configuration/grants remain
missing; the schema upgrade does not make an account a salesperson.

1. Sign in as the temporary nonstaff salesperson and select the intended BU.
   Customer and already-created item choices should work without staff access.
   There must be no item-creation or selling-price-edit control. Quantity
   information appears only with independent stock access; unavailable is not
   zero and the snapshot does not guarantee availability/reservation.
2. Use the two-line example below. Check supplied English/Arabic names,
   categories/descriptions, local image or explicit missing-image notice,
   source specifications, quantities and fixed selling prices. Missing Arabic
   must be flagged, not fabricated. Choose a discount within the displayed
   current role ceiling and enter the decision reason.
3. Leave validity/delivery wording blank unless explicitly supplied for this
   synthetic review. Optional English/Arabic department and displayed payment
   terms may contain clearly fictional text. These notes must not alter the
   existing payment, approval or delivery gates. Terms and Conditions must
   remain **pending owner text**.
4. Submit once, open **Quote collection & holds**, and inspect the saved client
   presentation: client/contact, salesperson, BU/date, optional validity,
   both item lines, discount/totals and Arabic direction. Inspect on desktop
   and a narrow screen; use Tab to reach labelled controls.
5. Above-grade discounts and direct price overrides must be rejected, with no
   partial quote. Switching to an unauthorized/unconfigured BU must clear the
   prior choices/presentation and disable creation. Supplier information,
   private costs/histories/margins, administration and unauthorized exports
   must remain unavailable. Exact retry must not create a duplicate quote.
   For the isolated Salesperson fixture, **5.25%** is within its existing
   **10%** ceiling; **10.01%** is rejected, not applied. Prices are approved
   read-only values, not editable inputs. A manipulated price/item override
   request must be denied by the server, not merely hidden in the UI.

Record expected/observed results and feedback in the existing table below.
This checks synthetic quotation scope, not approval of legal text, delivery
promises, customer ownership, production access or full Retail readiness.

## Multi-line quotation review — newly implemented

**Menu:** **Retail & Sales → Create a quote** (`#/retail/quote`), also reachable
through its existing function Favorite. Use the safe temporary session below;
do not add permissions, customers or prices to the shared demo to enable it.
An unavailable configuration/role/price message is an expected boundary.

1. Select the intended BU, an existing synthetic customer and **Available stock**.
   Choose an already-approved item/location, quantity **1.25**, and synthetic
   specifications such as `{"finish":"TEST ONLY linen"}`.
2. **Add another quote line**. Select the second configured item and authorized
   location, quantity **2.75**, with `{"finish":"TEST ONLY oak"}`. Verify each
   item, quantity, specification, approved selling price and line total in
   **Review each line**. Edit/remove a line; the other values must remain.
   Removing the first row must promote the second, not erase it. One row remains.
3. Use only the discount/terms permitted by the current fixture's role. Record
   displayed prices rather than assuming real company prices. Automated
   decimal evidence uses **QAR 19.99 / 10.05**, quantities **1.25 / 2.75** and
   **5.25%** discount: rounded net units **18.94 / 9.52**, line totals
   **23.68 / 26.18**, quote **49.86**, list value **52.63**, discount **2.77**.
   These are artificial test values using existing per-unit/per-line rounding.
   The default manual fixture may show different approved prices; do not
   change them to make this example fit.
4. Enter the explicit decision reason and create **one** synthetic quote.
   Open its existing quote/actions view and compare **both** returned lines,
   specifications, quantities, selling-price snapshots and total. No stock,
   reservation, collection, delivery or journal should be created by quoting.
   A quote does not guarantee stock: do not interpret the second fixture item's
   approved price as available ledger quantity.
5. Try a visibly invalid quantity (e.g. **1.001**) on the second row: show an
   actionable line error, not a partial quote. Missing prices and denied
   contexts must not be worked around. Existing retry of an unacknowledged
   request retains its original key/body; after acknowledged success, pressing
   Create again can be a **new** operation, not the retry exercise.
6. Try leaving an edited draft; **Cancel** the normal warning and check values
   remain. Switch BU and verify an incompatible draft cannot submit there.
   Do not use real users or revoke shared grants; automated isolation tests
   cover denial/revocation with temporary personas.
7. For a separate **Special order** quote, use the same single kind for every
   row and only existing configured full-payment/approved terms. Locations
   must not be submitted. This batch does not allow mixed-order terms, invent
   value bands, grant access, or enable partial delivery.

| Team observation | Expected outcome | Pass/fail and feedback |
| --- | --- | --- |
| Add/edit/remove, including first row | Independent values/order retained; one row minimum | |
| Two-line review and one submission | Exact server snapshots/total; no partial quote on rejection | |
| Draft warning, BU switch and unavailable state | Safe retained/disabled draft; no cross-BU operation | |
| Desktop and narrow screen | Labeled usable controls, readable review, no page overflow | |

**Verified scope:** isolated real-session browser/API and PostgreSQL/rendered
regressions, not authenticated shared-preview or human team acceptance. No
schema/demo migration is needed. Shared records/accounts/grants and pending
hold/procurement policy are unchanged.

## Getting a safe session

On the operator's machine/workspace with the existing project dependencies:

```sh
# Repeat the automated real-browser acceptance and PostgreSQL checks:
bash scripts/verify-retail-acceptance.sh

# Instead, open a fresh, temporary manual team-review session:
RETAIL_TEAM_REVIEW=1 bash scripts/verify-retail-acceptance.sh
```

The second command prints a **loopback-only** review URL and the path to an
owner-only temporary credential file, not passwords. The owner opens that file
privately and supplies the fictional test login securely. Open the URL on the
same machine; it is not a published/shared-preview URL. If using another local
machine with the existing project installed, the operator can set
`RETAIL_PG_BIN` to its already-installed PostgreSQL **16.15** `bin` directory.
No tools/dependencies are installed by the command. Test data and credentials
are deleted on normal exit, Ctrl+C or after 30 minutes; an abrupt machine kill
may require owner cleanup of that exact temporary directory. Do not expose this server publicly,
modify privacy/port sharing, or substitute a shared/production database.

The script verifies its own PostgreSQL 16.15 directory/port before setup and
that the HTTP listener belongs to its own Django process before browser use.
It creates a fresh synthetic fixture, never resets/reseeds the shared demo.
The configured fictional author combines existing staff-only customer/pricing
visibility with the existing TVL salesperson/order grants. This is a **test
persona only**, not an assignment to a real employee or a new company role.
Ordinary non-staff users must retain their current unavailable states.
The extended fixture also reuses the existing fictional `synthetic_manager`
test-role helper (20% ceiling), with fixture-only staff configuration visibility.
Its password is in the same private file, never in console/checklist output.
No shared role assignments or reporting grants are added.

## Current private demo review entry

The existing private preview was restored and migration
`0020_collection_function_favorite` was applied after an existing-data rehearsal
and a restore-verified backup. Observed development origin:

`https://a4c96d33-2378-4ad9-981e-34ad51db3300-00-3ofpyzwgxkcia.sisko.replit.dev:5000`

Use the owner's existing private workspace access and existing credentials;
none are supplied here. After signing in, select the intended authorized BU,
expand **Retail & Sales**, then choose **Collection follow-up**.
The direct function path is **`/#/retail/collections`**. Its function Favorite
is `retail.collections`; existing Retail section Favorites still open Retail.

**What is ready versus unavailable:** current assets, sign-in rendering and
unauthenticated protection were checked, but no shared account was signed in
during this restoration. The preserved shared database has **five demo-marked
accounts, nine existing BU grants, zero purchasing approval-role assignments,
zero quotes/collections and zero Favorites**. A visible navigation link is not
collection authority. Existing shared users must retain denied/unavailable
states rather than receiving new grants, prices, roles or sample transactions.
The shared preview is suitable for owner sign-in/navigation/unavailable-state
review, **not a prepopulated collection journey or completed team acceptance**.

For the populated C1–C8 business journey, use the disposable manual-review
command above and its existing fictional configuration. This loopback URL
must be opened on the operator's machine; it is not remotely available merely
because the shared preview is running. Do not forward/expose it or replace
the shared database. Coordinate a local operator-led review if needed.
Choose only the fixture-provided customer/item/location and approved price:
the existing example uses **two chairs at QAR 2,000**, a permitted **5%**
discount giving **QAR 3,800**, then a **QAR 1,900 simulated collection** leaving
**QAR 1,900**. Do not type real customer/card details, invent missing prices or
commercial terms, or make a real payment. Missing fixture choices mean stop
and record the unavailable state, not grant access or fabricate configuration.

Record feedback using the checklist's review log. Automated evidence and an
unsigned health check are not human sign-off. Development availability remains
session-bound; there is no publishing, uptime promise or keepalive service.

## Example and steps

### Collection follow-up — read-only list

Use the same owner-prepared disposable synthetic session described above.
Open **Retail & Sales → Collection follow-up**, or `#/retail/collections`.
This list records **prototype simulated collections, not real payments,
accounting receivables, overdue status or approved credit terms**.

| Step | Review action | Expected outcome |
| --- | --- | --- |
| C1 | Find an authorized quote with no recorded collection, using its exact quote ID. | Quote value is unchanged; recorded collections are `0.00`; remaining quote balance equals the quote value. Default list shows only quotes with a positive remaining balance. |
| C2 | Choose **Open quote actions**. Use only an existing permitted collection action in this isolated session, then return to Collection follow-up. | The chosen quote opens through existing authorization. Recorded merchandise collections and remaining balance refresh; no new payment terms or approval authority are implied. |
| C3 | Review the existing lost-acknowledgement retry scenario, or use its automated evidence rather than inducing a shared-preview failure. | An unchanged retry has one payment/reservation outcome; the list counts that payment once. Automation verified QAR 3,800 quote → QAR 1,900 simulated collection → QAR 1,900 remaining. |
| C4 | Include fully collected quotations; review an existing fully collected quote. | Remaining balance is `0.00`. Fully collected quotes are excluded by the default filter. This is not an accounting settlement certificate. |
| C5 | Where the isolated fixture already has an approved deferred balance after delivery, collect only that valid remaining amount through the existing action. | Status stays delivered and readiness/handover timestamps stay unchanged; retry adds no duplicate facts. This case is PostgreSQL/API verified, not a newly claimed native-browser exception-approval scenario. |
| C6 | Review a quote with separately recorded storage collection. | Storage does not reduce the merchandise balance. The dedicated PostgreSQL case verifies QAR 2,000 merchandise collection plus QAR 12.34 storage leaves QAR 2,000 of a QAR 4,000 quote. |
| C7 | Change BU, refresh after revoked access, or use an ordinary account without order access. | Prior rows clear; denied/unavailable contexts show no collection rows. A Favorite cannot grant access. Do not add grants to make the list appear. |
| C8 | Pin **Collection follow-up**, reload, use the function Favorite/My Page, then unpin. At a narrow width, use exact-ID search with the keyboard. | Function pin is personal and survives reload; existing section pins remain. Controls remain keyboard-operable; the wide table scrolls within its own container, not the entire page. |

Opening/searching/paging/refreshing the list creates **no business records**.
Pinning is an explicit personal-preference write, not a collection or grant.
The real-backend browser covered list → existing collection → retry → refreshed
balance and a separate 320px keyboard-search/Favorite-persistence scenario.
Storage and approved post-delivery balance combinations have isolated
PostgreSQL coverage; no human team sign-off or shared-preview test is implied.

Choose **Tivoli**, `TEST-CUST-RETAIL-01`, item **TVF-002-LIN**, and location
**STIV-G**. Opening stock is **10 chairs**. The approved synthetic
selling price is **QAR 2,000 each**.

| Step | Where / action | Expected result |
| --- | --- | --- |
| 1 | Retail & Sales → Create a quote. Available stock; quantity **2**; discount **5%**; enter a test-only reason and `{"finish":"TEST ONLY linen"}` specifications. Create once. | Approved quote: list **4,000**, discount **200**, total **3,800 QAR**. Original approved price stays **2,000**. Note the quote ID. |
| 2 | Quote collection & holds. Request **3 working days**. | Held status and a temporary hold record for 2 chairs. Existing holds do **not** reserve ledger stock; reservation waits for the deposit threshold. **Whether held quantities should block other sales is awaiting Samer's clarification; current behavior is not changed or accepted as the final availability rule.** Friday is closed in the synthetic calendar; Saturday/Sunday work. A 4-day request is not allowed. |
| 3 | Record simulated payment **1,900 QAR**, simulated cash, quote balance. | A 50% deposit confirms/reserves 2 chairs; on-hand remains 10, not 8. No real payment is taken. |
| 4 | If the app reports an uncertain/failed response, retry the **unchanged** action once. Do not edit or use a new payment key to “try again.” | One collection and one reservation result, not two. Automation deliberately loses one successful reply and checks exact retry identity. Do not induce failures in the shared preview. |
| 5 | After checking the collection is confirmed, open Delivery & After-Sales → Authorized quote lookup: load the noted ID → Readiness & handover actions. If prompted about retained drafts, review before explicitly choosing to leave. Record readiness. | Status **ready**, readiness time present, no delivered time. This records a dry-run notification intent only; no email/SMS/WhatsApp. Ordinary handover still needs the existing full-payment gate. |
| 6 | Retail → Create a quote: Special order, same approved item, quantity **1**, discount **0%**, **100% payment**, explicit test-only terms reason/specifications. | Special-order quote **2,000 QAR**. Value bands stay unconfigured; no invented thresholds or downpayment terms. Creation alone takes no payment/reservation. |
| 7 | Switch to Roche Bobois and attempt the Tivoli quote lookup. | Tivoli detail clears; cross-unit request is denied. Roche's own current label/theme remains selected. |
| 8 | Have the owner run the automated ordinary-access/revocation check, not edit shared grants manually. | Ordinary stock fields contain no cost, landed cost, supplier or margin. Revoked TVL reads/actions are denied and no stale quote returns. |

The automated checks also request a 20% discount with the salesperson persona:
it stays pending approval and self-approval is denied. After 2 chairs are
reserved, an attempt to reserve 9 more through a deposit is rejected with no collection,
reservation, stock or audit partial writes. Existing PostgreSQL tests cover
independent concurrent reservation requests and all established role ceilings.

## Continue: Manager decision and final handover

Use **separate quotes** for this run. The first 5% quote above stays ready with
2 chairs reserved; do not hand it over as part of these additional steps.
Select **Tivoli** again. Use only the owner's temporary fictional accounts.

| Step | Where / action | Expected result |
| --- | --- | --- |
| B1 | As the salesperson, create two Available stock quotes, each for 2 chairs from the same approved price: one **15%** and one **25%**, with explicit test reasons. Note both IDs. Open Quote collection & holds. | Both await approval. Salesperson cannot self-approve 15%; the approval button is disabled. Neither quote creates payment/reservation/stock changes. |
| B2 | In another private test-browser session, sign in as the supplied fictional Manager. Load the 25% quote, then the 15% quote. Enter an explicit approval reason for the 15% quote and record its approval. | **25% stays pending:** above the existing Manager 20% ceiling, button disabled. Automation also checks direct API **403** with no writes. **15% becomes approved**, one Manager approval: list 4,000 − discount 600 = **QAR 3,400**, source price still 2,000. No new authority/threshold is implied. |
| B3 | Return to the salesperson session, load the approved **15%** quote. Record **QAR 1,700 simulated cash**, then use Delivery → Authorized quote lookup → Readiness & handover actions to record readiness. Try Record delivery while only half is collected. | 2 chairs reserved; status ready, readiness timestamp present. Handover is **rejected: full payment required**. Delivery time, stock, payments, journals and audit facts are unchanged by rejection. No exception is selected or created. |
| B4 | Record the remaining **QAR 1,700 simulated cash**. Then Record delivery. | Still ready after collection, same readiness time, paid total **3,400**. Handover becomes **delivered**, delivered timestamp present; progress ordered **2**, delivered **2**, remaining **0**. One delivered-sale fact and one −2 stock movement, with the existing test-only journal effect. Operator checks on-hand **8** and original quote's reservation **2**; ordinary Inventory shows only unreserved **6**, if all earlier steps were followed in this fresh session. Internal costs are not returned in the handover acknowledgement. |
| B5 | Only if the response is uncertain, explicitly retry the **unchanged** handover. Do not create a new key/action or repeat an acknowledged delivery. | Automation deliberately loses one genuine HTTP 200 acknowledgement after commit. Exact retry returns the original result with no second delivery, payment, stock movement, journal/audit or timestamp change. After acknowledgement the delivery button disappears. A separate new-key attempt is also rejected without writes. |
| B6 | Review the quote's Delivery progress and the Inventory unreserved quantity. Open Delivery → Delivery register. | Quote and ledger quantities above reconcile. **These purchasing fixtures do not have the independent SALES report-panel/data grants:** the register correctly shows denied/no records. Automation verifies the delivered record through an isolated operator snapshot and the authorized quote view, **not** an authorized register listing. Do not grant access just to complete a checklist. |

No human should manufacture a network failure. Lost-reply checks are performed
by the bounded automated harness only. Manual review uses normal responses.
Record expected denials as passes, not requests to give the test user more access.

## Supported versus unavailable

Supported here: existing approved price snapshots, configured discounts, holds,
simulated collection/reservation, readiness, the existing Manager price decision,
full-payment handover and exact replay, full-payment special-order creation,
scoped registers and current access/masking rules.

Not approved or added here: automatic ordinary-user access to staff-only
configuration; new role assignments; value-band thresholds; new commercial terms;
real payment/refund, invoice or Finance posting; partial-delivery rules; return
disposition or HR/legal policy. Missing prices, customer choices, calendars,
roles or grants must show unavailable/configuration-needed, not guessed values.
Manager approval UI still requires the existing staff configuration gate;
non-staff manager exposure is not broadened. An authorized delivery-register
listing needs the existing SALES panel assignment and scoped SALES data access.
Those are separate from purchasing/stock access and were not supplied here.
The rare approved 5%-10% post-handover exception path remains covered by existing
PostgreSQL tests, not this new browser run. Hold availability still awaits Samer.
Tested prototype paths do **not** establish full Retail/Delivery section readiness.

Only owner-provided disposable fixtures may be changed for access/revocation
checks. No shared-preview testing is part of this acceptance. See
`REVIEW_HANDOFF.md` for actual outcomes and remaining verification limits.

## Team feedback — do not include passwords or real customer information

| Tester / date (optional) | Step / quote ID | Expected versus observed | Pass / issue | Screenshot or note |
| --- | --- | --- | --- | --- |
| | | | | |
| | | | | |
| | | | | |

## Current quotation access changes — isolated acceptance only

**Verified technical behavior, not shared team sign-off.** Do not revoke or
change shared accounts/grants to run these checks. The automated fixture uses
only temporary synthetic accounts and disposable PostgreSQL.

- **Actual loss of quote-read access:** after a real previously loaded quote,
  a denied transaction triggers a fresh read check. Confirmed read **403/404**
  clears the quote/document and quote-dependent action controls; the lookup ID
  remains with a clear error. Repeating lookup does not regain access. No
  collection or stock/accounting change occurs.
- **Action-only denial:** an existing lower-grade user may still read the quote
  but cannot approve a higher discount. The quote and draft reason stay visible;
  reading a quote does not authorize that approval. No new roles/thresholds.
- **Connection/temporary verification failure:** prior authorized content and
  drafts remain with “Quote access could not be rechecked” guidance. Explicit
  lookup retries current authorization; the UI does not mislabel this as
  confirmed revocation. No automatic transaction retries.
- **Navigation:** switching BU, selecting another quote or making a fresh lookup
  prevents older verification responses from restoring/clearing the new screen.
  Denied read access intentionally hides action forms; draft/retry state remains
  local and is not a permission or guarantee of successful later submission.

Real-backend native checks passed at **1280px/320px** for read loss and desktop
for action-only downgrade plus a lost authorized GET acknowledgement. Thirteen
rendered cases cover additional transient/out-of-order/selection cases; full
frontend and focused PostgreSQL checks also passed. Exact commands/counts and
limits are in the latest `REVIEW_HANDOFF.md`. This is reactive checking on
denial/lookup events, not immediate push revocation while an idle tab is open.

## Combined multi-item Retail-to-Delivery review

**Technically verified in an isolated synthetic environment, not yet shared
team sign-off or full Retail/Delivery readiness.** This is an additional
combined journey, not replacement evidence for all earlier checklist items.
Use only an owner-provided synthetic environment with existing authorized
choices/prices/stock. Do not recreate automation accounts or change grants.
The automated fixtures below are test values, not an approved company price
list or permission assignment; shared-demo availability is not assumed.

| Step | Navigation/action | Expected outcome |
| --- | --- | --- |
| M1 | **Retail & Sales → Create a quote**. Select existing synthetic customer and two authorized stocked items. Automated example: **1.25 × 19.99**, **2.75 × 10.05 QAR**, **5.25%** existing authorized discount; enter specifications/Arabic notes and the reason. | Two lines, fixed approved prices, **49.86 QAR** using existing rounding. No stock or price changes. Missing choices/configuration remain unavailable; do not invent them. |
| M2 | **Retail & Sales → Quote collection & holds**. On the new quote, try **50.00 simulated** collection, then correct to **24.93**. | Overpayment displays an actionable error and retains the entered draft/quote without writes. Corrected collection records once; both lines reserve **1.25 / 2.75**, on-hand unchanged. No real payment is taken. |
| M3 | If an acknowledgement is genuinely uncertain, explicitly retry the **unchanged** request. Then open **Collection follow-up**. | One collection, not two; **24.93 recorded / 24.93 remaining**. Automation safely injects a lost response after commit; do not deliberately interrupt a shared service to reproduce it. |
| M4 | **Delivery & After-Sales → Quote lookup**, enter the same quote, then **Picking preparation**. Review both lines; optionally mark Reviewed with keyboard Space. | Both lines/specifications/progress shown; marks mean local review only. No picking, stock or business record change; no claim that historical reservation proves current availability. |
| M5 | **Continue to handover**; record existing readiness and try handover before the final collection. | **Full payment required**, with no delivery/stock/journal/audit change from rejection. Quote remains ready. |
| M6 | Record remaining **24.93 simulated** collection, then handover. | Recorded merchandise collections **49.86**; no extra reservation. Delivered **1.25 / 2.75**, remaining **0 / 0**; each reservation consumed and each stock bucket reduced once. Readiness time preserved, delivery time visible. |
| M7 | Review the acknowledged result; do not create a second delivery. Switch BU only through the authorized selector. | Delivered status; no duplicate handover on exact uncertain-response retry. Cross-BU/revoked direct requests remain denied and private cost fields are absent from actual responses. Team does not revoke shared grants to test this; automation uses disposable data only. |

Verified by **2 actual browser journeys**, desktop **1280px** and mobile
**320px**, with actual session authentication/CSRF/Django/PostgreSQL 16.15.
The separate focused backend test also verifies second-line stock shortage
rolls back the entire deposit/first reservation before a legitimate retry.
Exact fresh counts, tests and limits are in the latest `REVIEW_HANDOFF.md`.
No application defect or runtime code correction was needed in this batch.
No partial-delivery, hold-blocking, refund, procurement/transfer authority or
new payment/approval rule is implied. Mobile is tested for these interactions
and no page overflow, not a general accessibility certification.

**Reviewer / environment / quote reference:** __________________________
**Passed steps / actual result / feedback:** ___________________________

## Delivery picking preparation — review only

Use only the owner-controlled **disposable synthetic session**, with the
existing configured quote persona. Do not change shared users/grants to enable
a positive path. Take the quote ID from the preceding supported Retail journey
or the owner-provided isolated quote register; `101`/`303` in rendered tests
are not promised demo IDs. Multi-line service fixtures use separate synthetic
line specifications; no new commercial terms are required.

| Step | Action | Expected outcome |
| --- | --- | --- |
| P1 | Open **Delivery & After-Sales → Picking preparation**, or `#/delivery/preparation`. Request the existing authorized quote ID. | A focused preparation page, not the aggregate workbench. Exact ordered/delivered/remaining quantities, specifications and recorded readiness are grouped per line. No rounding, unit conversion or available-to-promise claim. |
| P2 | Review item/SKU/brand/location details. | Details appear only where independently authorized by existing stock scope and an exact ledger match. Otherwise the page clearly says unavailable. No costs, history or capacity contents appear. |
| P3 | Open specifications with the keyboard; Tab to a **Reviewed** checkbox and press Space. | Visible focus; a local reviewed count changes. This is **reviewed only**, not physical picking, reservation, stock movement or dispatch. No business record is saved. |
| P4 | Request another quote, refresh the lookup or change BU. Try an already-denied quote with the existing denied persona. | Old lines/identities and review marks clear; denied/new-context responses never show another unit's data. Revoked access is checked on the next lookup; this is not push notification of permission changes. |
| P5 | Choose **Continue to handover**. | The existing readiness/handover view receives the selected quote. Existing configuration, authority and payment gates still apply; checking every line grants no action or bypass. Do not submit a handover merely to test local review. |
| P6 | Optionally pin **Picking preparation**, reload, then open it from My Page. | Only the user's navigation preference persists. Review marks and the selected quote do not persist. Existing section Favorites remain supported. Migration 0021 is now applied to the verified shared synthetic demo, following the controlled upgrade rehearsal and backup/restore proof. A Favorite never grants quote or stock access. |
| P7 | Repeat P1–P3 on desktop and a narrow phone viewport. | Labels/quantities remain readable, fields stack rather than splitting digits into tiny columns, no page-level horizontal overflow, and native keyboard controls work. |

### Shared synthetic review availability — 2026-10-20

Open the existing private Replit development preview and sign in only with your
already configured synthetic account using privately held credentials. The menu
path is **Delivery & After-Sales → Picking preparation**; the direct fragment
is `/#/delivery/preparation`. No credentials are included here or reset by this
rollout. Root/sign-in, current assets and anonymous denial were checked; this
does **not** mean a shared account was used for authenticated acceptance.

The observed shared database contains **5 marked synthetic users, 9 existing
stock-read grants, zero quote approval-role assignments and zero quotes**.
Existing Favorites were also zero; the nonempty preservation proof used the
separate disposable fixture. Expect the focused empty/configuration-needed/
denied state rather than enter an invented quote number or add grants/prices
to force a positive shared demonstration. Use the existing isolated review
procedure earlier in this document for populated P1–P7 review with fictional
configured personas. No shared test receipt, payment or handover was submitted.

**Reviewed means “I reviewed this line in this browser view” only.** It is not a
saved warehouse acknowledgement, physical pick, stock allocation, dispatch,
delivery or approval. Marks clear on quote/BU changes, fresh lookup, leaving/
returning to the function or reload. They never enable handover or bypass its
existing payment/authorization gates. Record team feedback separately below.
Preview health is session-bound; the handoff records observation times, not
continuous availability.

**Verified scope:** isolated real sign-in/cookies/APIs, stock quote → readiness
→ preparation, local keyboard review and function-Favorite reload at desktop
and 320px; rendered and PostgreSQL multi-line/scope/revocation checks. See the
latest `REVIEW_HANDOFF.md` for exact final outcomes and caveats.
**Not included:** physical pick/dispatch confirmation, stock allocation,
partial delivery, transport/hardware acceptance, new refund/accounting rules,
hold blocking or procurement-access policy. Shared-demo/team acceptance is
not established by automated fixture success.

| Tester / optional date | P step / synthetic quote | Expected versus observed | Pass / issue / feedback |
| --- | --- | --- | --- |
| | | | |
| | | | |
