---
name: PostgreSQL restore constraint comparison
description: Distinguish equivalent PostgreSQL constraint deparsing from actual schema drift during restore verification.
---

Dump/restore can change `pg_get_constraintdef` formatting for literal varchar
arrays coerced to text arrays even when CHECK semantics are unchanged.
Do not infer corruption from raw definition strings alone, but do not broadly
ignore casts or constraint differences.

**Why:** A synthetic restore produced equivalent whole-array versus per-literal
text coercions. Strict comparison correctly stopped recovery until the difference
was explained; business data did not need repair.

**How to apply:** Retain raw evidence. Prove the specific equivalence using
read-only probes including null, invalid and permitted values. Any canonical
comparison must fail closed for other expressions, typmods, types or changed
literals. Continue exact comparisons for rows and all unaffected schema facts.
