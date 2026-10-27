# Isolated sidebar and sales-return browser verification

Run from the project root:

```sh
npm run test:sidebar-browser
npm run test:sales-return-browser
```

Requires Node.js 24 (the repository's current runtime) and an installed Chromium
executable on `PATH`. An operator can instead provide its executable path:

```sh
CHROMIUM_PATH=/path/to/chromium npm run test:sidebar-browser
```

No new npm package, browser download or system installation is needed in the
current environment. Missing Chromium is a failure, not a silently skipped test.
The small adapter uses Node's native WebSocket and Chromium's standard DevTools
Protocol, with the existing `node:test` runner. Native input is dispatched through
Chromium, not React handlers or jsdom keyboard simulations.

## Isolation

- The command first runs the existing TypeScript/Vite production **build**, then
  serves only `dist/` using a temporary loopback HTTP server on an OS-assigned
  port. It does **not** run Vite's dev server, Django, `scripts/dev.sh`, migrations,
  seed commands, PostgreSQL or a workflow. There is no backend proxy.
- Authentication, organization, ledger and Favorites responses are strictly
  synthetic, in-memory test doubles in `browser-harness.mjs`. They are not a
  real session login, new app user, credential, assignment or auth bypass in the
  shipped app. The fixture code is outside `src/` and not in the frontend build.
- Recognized current-scope permission checks receive synthetic 403 responses.
  Unexpected API paths, cross-unit scope and mutations are blocked and fail the
  tests. Only the BU keyboard-selection test permits two mocked unit-selection
  POSTs by default. The return suite supplies a strictly in-memory fixture and
  explicitly allowlists only return-intake and inspection mock POST paths:
  validation failures and a Finance-pending response, never a backend connection.
  No real transaction, favorite mutation, role or grant write is allowed.
- Chromium page requests outside the loopback fixture origin are blocked and
  asserted absent. Temporary browser profiles, servers and owned browser
  processes are removed/closed after each test, including assertion failures.
- Chromium runs headless with `--no-sandbox` for this container; use only the
  trusted synthetic fixture, not untrusted supplier files or live websites.

## Coverage and evidence

Six tests cover native Tab/Shift+Tab order, Enter/Space activation, hidden-child
focus exclusion, actual computed focus contrast, Favorites/My Page and active
parent reveal, denied transaction controls, native BU selection and three BU
themes, and flyout text/geometry/hit-testing at 390×844, 320×844 and 320×568.

Screenshots are overwritten under `generated-artifacts/sidebar-browser/`
(already ignored by Git and deployment). They include keyboard focus, Favorites,
Natuzzi and the three narrow viewports. Inspect them alongside the assertions;
screenshots are supporting evidence, not pixel snapshots or a full WCAG audit.

The separate return suite contains five tests: native Tab/Shift+Tab order,
Space/Arrow/Enter select interaction and keyboard submission; Chromium
accessibility-tree labels and alert/status roles; visible focus with at least
3:1 computed outline contrast; client and mock-server validation; explicit
inspection retry retaining its payload/key; Finance-pending feedback; and
1280×844, 390×844 and 320×844 geometry/hit-testing without document-level
horizontal overflow. The wide register scrolls inside its own container.
Overflow compares document `scrollWidth` with `clientWidth`, including the space
occupied by a native vertical scrollbar, not only `window.innerWidth`. The
inspection heading must retain at least 100px of text width in these fixtures.
Inspection screenshots are generated at
`generated-artifacts/sales-return-browser/inspection-{width}.png`.

Browser tests are intentionally a separate explicit check from the portable
jsdom/frontend suite, which does not require Chromium:

```sh
npm test
npm run test:sidebar-browser  # includes typechecked production build
npm run test:sales-return-browser  # includes typechecked production build
npm run typecheck
npm run test:contrast
```

For protocol diagnosis only, `BROWSER_DEBUG=1` logs CDP events from these
synthetic fixtures. Do not use it with live credentials/data.

This verifies real Chromium rendering/input with mocked APIs, not the shared
preview's login, actual server authorization, screen-reader output, other browser
engines, real touch hardware or every viewport/zoom combination.
