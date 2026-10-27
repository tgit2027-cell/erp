---
name: Notification delivery boundary
description: Approval boundary for readiness and storage-charge notification work.
---

Persisted readiness and storage-charge notification events may create durable synthetic outbox intents, but no real recipients, delivery channels, message templates, schedules, or external delivery are approved.

**Why:** The user explicitly required a no-network dry-run worker and prohibited choosing recipients, channels, message wording policy, or timing rules.

**How to apply:** Keep workers limited to synthetic intents and clearly label an in-memory dry-run as an intent record, never as a delivered notification. Wait for explicit approval before adding any real delivery integration or its recipient/channel/timing configuration.

Treat stored failure strings as potentially sensitive even in metadata-only
monitoring; allowlist known codes rather than echoing arbitrary strings.

**Why:** A future worker or imported diagnostic can store exception text in an
error field. Calling that field “metadata” does not make its content safe.

**How to apply:** Aggregate only approved code literals, redact unknown codes
before retrieving them, and never fetch message/details payloads for a count-only
health summary. Stored dry-run status is not proof of external delivery.
