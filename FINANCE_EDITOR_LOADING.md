# Finance editor loading — measured scope and acceptance

This batch changes packaging/loading only, not financial semantics, APIs,
permissions, histories or posting. Internal controls remain English; customer
quotation/invoice presentation and stored Arabic data are unchanged.
Finance drafts remain lab-only and bounded; operational/shared/team readiness
is separate: [readiness matrix](FINANCE_READINESS_MATRIX.md).

## Implementation

- Standalone entry imports only `FinanceLabShell`, shared navigation/recovery
  and loader. Each existing editor/review is a statically analyzable dynamic
  import.14 actual destinations remain: journals (with its existing five
  kinds),12 other resource editors and review. Same separate offline sample
  remains at non-lab standalone paths; it makes no lab setup request.
- Journal implementation stays in `FinanceDraftLab.tsx` with existing import
  compatibility; entry imports shell directly, avoiding eager journal/workspace
  inclusion. All financial form logic remains unchanged.
- Loading has announced status; failed import has an explicit retry and page
  reload fallback. No financial request is retried. Navigation remains usable.
  Only module-load errors are handled, not runtime/API errors inside a form.
  Loaded component identity is stable on shell rerenders; obsolete load
  success/failure cannot mount/replace the current view.
- Recovery module/panel remains eager and unremounted across in-place loader
  retry. Failed-module loading changes no metadata. Current authorized status
  is shown only after a working view verifies the lab session; loading failure
  itself cannot authorize stored metadata. Hard reload retains available
  sessionStorage metadata, not form bodies or in-memory replay callbacks.
  No automatic reload; the explicit reload preserves the URL/reopen query and
  uses existing browser unload handling.
- Native Chromium retained a failed module in its document cache: a new
  in-place import attempt did **not** refetch it. Both real tests used the
  explicit reload fallback successfully. Do not claim in-place retries always
  recover module fetches, or that reload durably persists unsaved inputs.
- Review embedded mode avoids duplicate navigation/recovery; its standalone
  component/test entry retains original presentation. No permission/UI gate
  changes, client-side route authority or new worksheet.

## Measurement method

Before build captured prior to edits:
`npm run build:finance-draft -- --manifest --outDir "$PWD/generated-artifacts/finance-editor-load/before"`.
After, equivalent build into `after`. Reproduce report:

```
node scripts/finance-editor-load-size.mjs \
  generated-artifacts/finance-editor-load/before \
  generated-artifacts/finance-editor-load/after \
  generated-artifacts/finance-editor-load/sizes.json
```

Static dependency closure from Vite manifests; each file counted once per
load, including shared JS/CSS, excluding dynamic imports until selected.
gzip is calculated separately per emitted file with Node's default gzip,
**not observed HTTP transfer** and not necessarily Vite's reported estimator.
HTML/API payloads, browser cache and timing are excluded. No latency/speed gain
was measured or claimed. The500kB warning threshold was not changed.

| Scope | Before bytes | After bytes |
| --- | ---: | ---: |
| Initial shell JS |522,524|233,540|
| Initial shell CSS |44,340|7,174|
| Initial JS+CSS |566,864|240,714|
| Initial gzip JS / CSS estimates |118,476 /7,931|72,550 /2,042|
| All emitted JS (all views) |522,524|532,398|
| All emitted CSS |44,340|44,348|
| All emitted gzip JS / CSS estimates |118,476 /7,931|158,909 /13,538|

Initial JS reduced **55.3%**; JS+CSS reduced **57.5%**. Total JS grows9,874
bytes (~1.9%); separately compressed complete inventory grows too.
First view needs additional requests; shared chunks may cache. Do not interpret
shell savings as eliminating editor load cost, or total-inventory growth as
bytes needed for one view.

Before: every selected view already contained all editor JS in522,524 bytes;
incremental editor JS was0. After each row below includes all statically needed
helpers, excluding files already in the shell:

| Selected view | Additional JS | Additional CSS | Shell + selected JS | Shell + selected CSS |
| --- | ---: | ---: | ---: | ---: |
| Journals / recurring / opening / accrual / prepayment |40,421|3,459|273,961|10,633|
| Allocation |25,468|2,699|259,008|9,873|
| Budget |23,538|5,959|257,078|13,133|
| Reconciliation |29,870|6,507|263,410|13,681|
| Expense/petty cash |23,614|8,711|257,154|15,885|
| Assets |20,556|5,239|254,096|12,413|
| Forecast |23,217|6,725|256,757|13,899|
| Supplier invoice |22,863|5,972|256,403|13,146|
| Supplier adjustment |27,830|5,972|261,370|13,146|
| Customer invoice |24,129|5,972|257,669|13,146|
| Customer credit |27,199|5,972|260,739|13,146|
| Supplier advance |23,755|5,972|257,295|13,146|
| Reversal |26,416|5,972|259,956|13,146|
| Review index |7,151|0|240,691|7,174|
| Existing offline sample |20,461|3,459|254,001|10,633|

The graph check asserts15 dynamic destinations and no editor entry in the
initial closure. Native resource observations verify the selected view plus its
static dependencies, **not unrelated editor chunks**, for all14 lab destinations
at1280/390px. Original full-page links/back-navigation/unload semantics retained;
no new SPA router or draft persistence policy.

## Verification boundary

See [latest handoff](REVIEW_HANDOFF.md) for exact new commands/counts,
retained failures and screenshot limits. Browser APIs/authentication were real
Django/disposable PostgreSQL16.15; only one JS asset response was deferred/
failed to test transport. A browser-metadata fixture paired with a real
committed supplier-advance request tested preservation through failure/reload
and actual authorized status lookup. It does not replace the separate actual
UI create/lost-acknowledgement/replay journeys.

No shared data/credentials/grants/migrations/services, production, posting,
publishing, privacy or purchases. Recovery decision is separate and **not
executed**: [updated assessment](FINANCE_PREVIEW_RECOVERY_ASSESSMENT.md).
