---
name: Rendered DOM assertion diagnostics
description: Avoid expensive React-fiber inspection when reporting failed DOM visibility assertions in Node tests.
---

Assert a boolean for DOM absence/identity instead of comparing a React-backed
DOM element directly with null in a failure expected during tests-first work.

**Why:** A failed direct element/null assertion in the Node/jsdom harness can
spend a long time formatting the element's attached React graph, consuming CPU
without printing the failure. This can look like an application render loop.
The equivalent boolean assertion reports the expected failure immediately.

**How to apply:** Prefer
`assert.equal(element.closest("[hidden]") === null, true, descriptiveMessage)`
for visibility ownership. Do not weaken the assertion or change application
logic to resolve an error-formatter delay.
