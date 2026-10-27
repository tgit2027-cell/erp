---
name: Browser verification evidence boundaries
description: Keep isolated real-browser UI evidence separate from actual session/auth and shared-preview acceptance.
---

Use isolated synthetic fixtures for browser acceptance when the shared development runner would migrate/seed data. Never add an app-side authentication bypass or create/reset credentials merely to inspect a screen.

Real-backend acceptance must use an independently verified disposable cluster and
ephemeral test credentials, not mocked authentication. Preserve normal cookies,
Host/Origin and CSRF handling through the local test proxy. Session bootstrap
and document reloads must settle before treating a rendered shell as ready.

**Why:** A mocked harness can assume an already-authenticated Overview and
synchronous organization state; actual login and reload expose asynchronous
document/session transitions that make stale-DOM assertions unreliable.

**How to apply:** Wait for the new document and its authorized organization
context, verify actual login/status responses, and distinguish real response
delivery delays from fabricated API results. Never log test passwords or
cookies, and never present isolated success as shared-preview acceptance.

**Why:** The user requires UI verification without shared-preview data changes. Real Chromium operating against mocked auth/API responses proves browser interaction/rendering, but does not prove Django login, CSRF or current user permissions.

**How to apply:** State which layer was checked: rendered jsdom, real browser with mocked APIs, or actual authenticated session. Keep fixture profiles/API state ephemeral and outside the shipped app. Do not promote a fixture screenshot or test success into a claim that real-session access or the shared managed workflow was verified.

Native Chromium tests must explicitly open select popups before choosing an
option, and establish the intended focus before retrying a submit control that
was temporarily disabled. Disclosure checks must toggle the observed open state,
not assume that navigation left the disclosure closed.

**Why:** A native popup can consume Tab to close itself, and disabling a focused
submit button during a request can lose its focus. An assumed subsequent Enter
can then do nothing; these are harness assumptions, not proof of an app defect.
Similarly, a narrow-screen disclosure may already be open when a keyboard step
begins; Space correctly closes it, so waiting for it to open is a false failure.

**How to apply:** Check the actual focused element and selected value, use native
Space/Arrow/Enter for selects, and distinguish focus setup from the sequential
Tab behavior being tested. Do not replace browser input with React event calls.
Let navigation focus settle, observe disclosure state, and assert the actual
native toggle before continuing the user journey.

For page-level overflow assertions, compare document scroll width with
`document.documentElement.clientWidth`, not `innerWidth`.

**Why:** Native desktop Chromium can reserve scrollbar space inside a requested
320px viewport. Comparing against `innerWidth` can falsely pass approximately
15px of horizontal overflow, even when a screenshot shows a horizontal scrollbar.

**How to apply:** Report both requested viewport and usable client width.
Check every focused view, collect all failing routes in one pass, and fix
intrinsic sizing/wrapping rather than clipping controls with overflow hiding.

Narrow-screen acceptance must also check field legibility, not just overflow.
**Why:** A compact sidebar can leave content technically within the viewport
while multi-column fields split quantity digits into unreadable fragments.
Fixed image columns and accumulated nested padding can likewise squeeze
quotation copy into one-word columns even after page overflow checks pass.
**How to apply:** Inspect native screenshots; assert adequate field/font width
and readable numeric strings, and stack fields rather than hiding overflow.
Check the actual text-copy column in both entry and saved presentation after
production CSS loads; stacking the outer grid alone does not prove legibility.

For real-backend retry acceptance, observe the actual operation identity as
well as the request body, without recording authentication/CSRF headers.
Distinguish a deliberately lost acknowledgement after commit from a rejected
backend operation.

**Why:** Payload-only observation can miss a header-carried retry key and turn
an intended conflicting-replay test into a missing-key validation test. A
transport failure can also leave a legitimate committed receipt.

**How to apply:** Compare the unchanged body and non-secret retry identity,
verify persisted business/audit facts before and after explicit retry, and
record transport fault injection separately from server status. Derive denial
expectations from existing API contracts rather than changing the app to make
every denied scope return the same HTTP code.

Check actual visibility after production CSS loads, not just the `hidden`
attribute or a rendered DOM test.

**Why:** A real Procurement acceptance screenshot exposed forms whose author
grid styles overrode the browser's native hidden presentation, despite correct
function-selection attributes and passing DOM-oriented tests.

**How to apply:** Assert rendered rectangles/visibility and usable chosen-panel
width in native browser acceptance. Keep hidden forms mounted when drafts and
pending operations must survive navigation; correct presentation rather than
replacing component identity.

Apply that check across previously visited workspaces, not just the active
component in isolation. A correctly focused route can still have an unrelated
legacy heading, while mounted inactive workspaces can match the same selector.
Use visible content witnesses and assert unrelated content is absent.

Explicitly select the intended business unit after authenticating an acceptance
persona; do not assume a fixture's target BU is the browser's initial context.

**Why:** A multi-unit fixture can start in another authorized unit, correctly
showing unavailable controls despite valid configuration for the test's target.
Interpreting this as a missing feature leads to unnecessary permission changes.

**How to apply:** Use the native selector, wait for the selected context and
scoped requests, then exercise the workflow. Handle real unsaved-work dialogs
with an explicit decision, including native before-unload prompts during test
reloads. These can suspend renderer evaluation while HTTP/backend work remains
healthy. Never stub the protection or interpret a secondary dialog-blocked
evaluation timeout as independent evidence of a server outage.

For disposable real-backend browser runs, verify the HTTP listener belongs to
the exact Django child PID as well as verifying the ORM's database target.

**Why:** A failed bind plus an unrelated service's successful health response
could otherwise direct browser POSTs to a different database despite a valid
fixture/ORM preflight.

**How to apply:** Before authenticating or submitting, confirm the expected
loopback listening socket is owned by the supervised test process; abort on
collision rather than treating a generic HTTP 200 as proof of isolation.

Wait for the actual backend response and completed context refresh before
calling an optimistic control change an accepted operation.

**Why:** A selected-BU label can update while organization loading is still
in progress. That proves local intent, not successful scoped backend acceptance.

**How to apply:** Assert the real selection response and settled context as well
as the final label/content. Screenshot cleanup must also assert the expected
route; a coordinate click can hit an overlapping narrow sidebar flyout and
silently capture a different function.

Describe the actual browser interaction method, not just that Chromium ran.

**Why:** Native select-key automation can fail to commit the intended option;
that setup failure is not evidence of an application scope defect. Browser DOM
select/change interaction still exercises the rendered app and real backend,
but does not prove native keyboard traversal of that select.

**How to apply:** Check actual selected value and request/status evidence before
classifying a failure. Label DOM change, native key activation, mocked responses
and real authenticated APIs separately in acceptance results.

A wrapped inline link's bounding-box centre may land on its parent rather than
on either clickable text fragment. A navigation timeout alone does not identify
an authentication or route failure.

**Why:** Narrow-screen native acceptance observed a healthy authenticated page,
no dialog/error and a two-fragment link whose central hit target was its paragraph.
Desktop activation had succeeded.

**How to apply:** Observe the actual native hit target and document transition
before classifying a failure. Keep genuine pointer/keyboard acceptance; do not
substitute programmatic navigation to conceal hit-area problems. Use a coherent
clickable target for wrapped function links.

Failed ES-module retrieval can remain cached for the lifetime of a browser
document. Creating a new import promise does not guarantee another network
fetch; distinguish import retry from explicit page reload and never promise
reload preserves unsaved form bodies.

**Why:** Isolated desktop/mobile Chromium acceptance both retained an intentionally
failed503 module despite explicit import retry. Reload recovered the original
destination and available request metadata, not in-memory request payloads.

**How to apply:** Keep navigation/recovery independent of editor imports, retain
existing unload safeguards, and verify actual fetch/reload behavior rather than
assuming promise retries prove network recovery. This is a browser constraint,
not authority to automatically reload an edited form or resubmit its save.
