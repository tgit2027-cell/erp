---
name: Verification evidence retention
description: Preserve exact final verification evidence across workspace interruptions without checking generated logs into source control.
---

For long verification runs requiring exact results, retain sanitized final logs
in ignored workspace artifacts rather than relying solely on `/tmp`.
Summarize exact completed commands/counts and limits in the review handoff.
Never preserve credentials, real payloads or connection secrets in evidence.

**Why:** An execution-environment interruption cleared temporary logs while
source and workspace captures survived. An earlier success exit alone did not
provide the missing exact suite totals.

**How to apply:** Save bounded final output before leaving a verification run.
Treat interrupted/partial runs as having no final result, and distinguish any
necessary rerun from prior historical checks. Keep generated evidence ignored
by source control and deployment.
