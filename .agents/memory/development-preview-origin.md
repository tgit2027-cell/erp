---
name: Development preview origin
description: Distinguish the exact preview origin from portless development-domain health checks.
---

Use the exact development preview origin, preserving its scheme and explicit port, when assessing preview health.

**Why:** The same healthy development instance returned HTTP 200 at the configured HTTPS origin with port `:5000` while the portless domain returned 502. A portless check can therefore incorrectly suggest an application failure even after a successful workflow recovery.

**How to apply:** Compare the URL actually checked with the configured/browser preview origin before changing code, CSRF trust, ports, privacy or sharing. Distinguish server health, managed workflow status and proxy routing evidence.
