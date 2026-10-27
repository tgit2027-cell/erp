# Synthetic preview recovery and existing Finance review finder

## Outcome — 21 October 2026

The explicitly authorized recovery completed. The normal `Start application`
workflow runs unchanged. Its previous readiness failure was the five pending
foundation Finance migrations, not a port defect. Startup's migration check was
preserved, not bypassed. Neither recovery nor startup ran `seed_demo`.

### Target and upgrade evidence

- Effective `APP_ENV=demo`; database `tivoli_erp_demo`, loopback port 55432,
  established workspace `.runtime/postgres`; PostgreSQL **16.15**.
- Before upgrade: all **5** existing users covered by demo markers; all **52**
  present synthetic-flag models checked. Seven missing Finance tables matched
  the pending migration plan exactly. No external/production target was used.
- Actual pending/applied subset: foundation **0023–0027**, respectively draft
  foundation, draft revisions, recurring proposals, opening worksheet and
  adjustment proposals. No other upgrade was applied.
- Exact effects: **7 new empty tables**, **5 migration records**, **7 content
  types**. Existing **63 non-metadata tables** retained exact row digests,
  including credentials, grants, Favorites, business and historical records.
  Existing migration/content-type rows were preserved separately.
- Final read-only comparison: **72 tables / 502 rows**, **59 synthetic-flag
  models**, **5 marked users**, **0 missing models / pending migrations**.
  The additional 12 rows are migration/content-type bookkeeping, not proposals,
  users, permissions or transactions. No default Finance grants were created.

### Backup, restore rehearsal and comparison limits

Private bundle, outside the repository and HTTP root:
`/home/runner/tivoli-erp-backups/finance-recovery-20261021T060307Z-624e1694`.
Directory permissions 0700; private files 0600. Do not publish or attach it.

One exported repeatable-read, read-only snapshot supplied the custom-format
database dump and manifests/fingerprints: **65 tables / 490 rows**. All three
required artifacts passed the existing checksum verifier. Media was empty.
Source schema/data/sequence evidence, code/assets and reviewed migration hashes
were retained privately. Existing backups were not removed or replaced.

An owned disposable PostgreSQL 16.15 cluster restored this backup and rehearsed
the exact five upgrades. Existing rows, visible schema, indexes, triggers,
functions and relevant sequences were compared. The shared target was rechecked
against its original backup immediately before applying the rehearsed subset.
Final shared schema matched the rehearsed schema.

Initial strict comparisons stopped on PostgreSQL's equivalent deparsing of
literal `varchar` arrays coerced to `text[]` in CHECK constraints. No data was
repaired and no constraint was disabled. Eight fail-closed comparison assertions
and read-only semantic probes of all ten affected constraints established the
specific equivalence; other casts/expressions and changed literals remain
different. Raw source evidence was retained. Do not describe those initial
comparison stops as failed migrations or discarded business data.

Recovery procedure: preserve the current database; verify the private bundle
and operator evidence, restore only into a fresh explicitly owned isolated
target, compare it, then obtain explicit target/cutover authorization. The
private bundle includes operation scripts/state; **do not rerun apply mode**
against the upgraded database. Never migrate backwards, fake migration records,
drop/reset or restore over the running database as an automatic rollback.

The old-schema database restore was proved, but the captured pre-upgrade code
already expected these migrations. Pairing that code with the old schema would
restore the previous blocked readiness state, not a proved healthy-service
rollback. Compatible code/runtime selection and any cutover remain separate.
Local restore evidence is not a production recovery/RPO/RTO certification;
checksum integrity alone does not establish recoverability.

## Actual preview observations

At **2026-10-21 06:16:15 UTC**, the configured HTTPS development origin on
**port 5000** returned:

| Request | Result |
|---|---|
| `/` and `/signin` SPA entry | 200 |
| `/api/auth/csrf/` | 200 |
| `/api/auth/me/` | 200, exactly `authenticated: false` |
| `/api/stock-balances/` without authentication | 403 |
| `/src/App.tsx` current development asset | 200 |
| Real Django `127.0.0.1:8000/lab/session/` | 404 |

The entry screenshot shows the actual English sign-in form. `/signin` alone
is a SPA fallback health check, not evidence of a distinct server sign-in route.
No owner/shared account was authenticated and no credentials were entered or
reset. The anonymous-session endpoint intentionally returns 200, not 403; an
initial probe's incorrect expectation was corrected without changing the app.
Workflow logs show both services ready and zero Django system issues. These are
time-stamped session-bound observations, not a continuous-availability claim.
At **06:35:17 UTC**, the configured HTTPS entry/sign-in still returned 200,
anonymous stock remained 403, both local services responded, and the Django lab
route remained 404. The served App.tsx source map matched the working-tree
source exactly. Details are in `final-health.log`; this is a second bounded
observation, not keepalive traffic or proof of persistent availability.

## Continued independent development — existing review/reopen workflow

The existing isolated Finance register now supports **local draft-type and
label/reference/exact-ID filters**, matching-versus-authorized-list counts,
clear/no-match states and unchanged reopen links/revisions. IDs may repeat
across draft types; an explicit type disambiguates. Refresh removing a selected
type shows an unavailable state rather than silently broadening the filter.
Denied refresh removes cached results. Filters make no API requests, aggregate
no financial amounts and persist no new business data.

This improves the already-approved consolidated review experience, not a new
worksheet/register or financial behavior. All 13 existing families and 14
navigation destinations remain available only in the isolated lab. Normal
shared Finance permissions, endpoints and operational features remain absent;
applying foundation schema did **not** authorize or expose the lab.

## New checks in this batch

Sanitized logs/screenshots: `generated-artifacts/preview-finance-recovery/`
(ignored, not a public asset directory).

| Command/check | Final result |
|---|---|
| Recovery preflight, consistent backup, isolated restore/upgrade and shared preservation comparisons | Passed, counts above |
| Narrow SQL constraint comparison checks | 8 assertions; 10 affected constraints semantically probed; passed |
| `npm run test:finance-lab-review-ui` | **11 passed, 0 failed** |
| `npm run test:finance-editor-loader-ui` | **6 passed, 0 failed** |
| `npm run test:finance-recovery-ui` | **7 passed, 0 failed** |
| `npm run test:purchasing-quote-lines` | **23 passed, 0 failed** |
| Focused UI total | **47 passed, 0 failed** |
| `FINANCE_LOADING_ASSETS=<current ignored build> FINANCE_LOADING_EVIDENCE=preview-finance-recovery FINANCE_LAB_SKIP_BACKEND=1 FINANCE_LAB_LOADING_BROWSER_ONLY=1 bash scripts/verify-finance-draft-workflow.sh` | **4 passed, 0 failed** real-backend Chromium cases |
| `npm run typecheck`; `npm run build` | Passed |
| `npm run build:finance-draft -- --manifest --outDir <current ignored build>` | Passed |
| `npm run test:contrast` | **349 passed** |
| `python manage.py check --database default` | 0 issues, under read-only transaction default |
| `python manage.py migrate --check` | Passed, no pending migrations |
| `python manage.py makemigrations --check --dry-run` | No changes |
| `git diff --check` | Passed |

Real browser cases used authenticated disposable synthetic PostgreSQL data at
1280px and 390px: all 14 destinations, finder/no-match/clear and scoped reopen,
Tab/Enter and visible finder focus, no page overflow, selected-editor transport
failure/recovery, existing source-linked reversal persistence, lost
acknowledgement/exact replay and conflicting edits. Each loading case made
exactly three intentional fixture POSTs; filtering/navigation made no financial
POSTs. Operational foundation rows and test-actor authority remained unchanged.
Both owned PostgreSQL test clusters were cleaned up by their harnesses.

The first desktop browser fixture received 403 during setup (initial result
3/4). The fixture now waits for anonymous startup to finish before its login/
CSRF rotation. The final four-case run passes; no application auth rule changed.
Initial and final logs are retained separately.

Current measured initial lab shell: **233,540 raw JS bytes / 7,829 CSS bytes**
(gzip **72,558 / 2,170**). Review editor adds **8,996 JS bytes** (gzip 3,445).
The new filter CSS adds 655 raw bytes against the earlier loading-batch shell;
its earlier 7,174-byte CSS measurement remains historical, not current.
No new speed/latency claim is made.

No full backend/frontend suite was rerun: financial/backend logic and migration
files were unchanged; current evidence is the focused checks, real browser
flows and actual upgrade/restore comparisons above. Earlier full-suite results
remain earlier evidence, not newly executed results. No screen-reader, Office,
production-security, shared signed-in acceptance or sustained-uptime claim.
One shell-result retrieval reported “No process” after the read-only schema
command completed; its retained log contains the full preservation result and
all final system/schema success messages. No upgrade was repeated.
The main build retains its existing large-chunk warning (561.81 kB raw JS,
151.21 kB gzip); it was not suppressed or described as a new speed improvement.

## Available now versus remaining decisions

The recovered shared preview serves the current implemented ERP/sign-in under
existing permissions, including English internal screens and bilingual client
documents. The isolated Finance review finder and existing drafts are **not**
shared-team Finance or operational accounting.

Next is review/acceptance of these bounded experiences with the existing team
checklists, and an explicitly approved Finance access/source/integration slice.
Independent technical follow-ups are a working-application restore rehearsal
with compatible code/schema, and measured selected-workspace loading for the
main ERP; neither is implemented or required to claim this bounded recovery.
No fourth draft worksheet is inferred. Finance posting/mappings/approvals and
unapproved hold blocking, procurement authority, transfer authority, document
output rights and final T&C remain dependent only for their own features.
They did not block this recovery or independent review usability improvement.
