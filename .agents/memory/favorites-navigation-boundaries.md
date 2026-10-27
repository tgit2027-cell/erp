---
name: Favorites navigation boundaries
description: Distinguish navigation to an existing workbench from authority to use its transactions or sensitive reads.
---

Favorites and My Page are navigation preferences, not authorization. An already-available workbench can remain pinnable when its transaction actions or data reads are denied or need configuration.

**Why:** The user explicitly requested Purchasing Workbench pin/open support while preserving all existing BU, transaction and sensitive-data permissions. Requiring a new transaction grant merely to pin the page would also change the approved behavior.

**How to apply:** Reuse the existing destination and workbench states. Do not add grants or bypass server checks when adding a navigation shortcut, and do not silently equate page availability with permission to execute its actions.
