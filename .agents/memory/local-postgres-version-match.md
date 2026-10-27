---
name: Local PostgreSQL version match
description: Protect a workspace-local PostgreSQL data directory from Nix runtime version changes.
---

An ignored local PostgreSQL data directory can outlive a Replit Nix runtime refresh. Its `PG_VERSION` must match the selected PostgreSQL server binaries; do not initialize, upgrade, or delete the directory as a workaround for a version mismatch. For isolated tests, explicitly direct *every* PostgreSQL client (`psql`, `createdb`, `dropdb`, and similar) to the disposable cluster with host/port/user/database arguments or deliberate per-process environment overrides. Django's `DB_HOST`/`DB_PORT` settings do not configure shell clients, which may inherit a different ambient PostgreSQL target.

Do not assume the Unix login name is the database service role. Resolve the
verified Django connection's non-secret role privately when invoking backup
clients, and compare it with the normal runner's role selection.

**Why:** On this workspace the established local demo accepted its configured
Django role but rejected a backup invoked with the Unix login name. No role
creation or credential change was needed.

**How to apply:** Pass the existing configured role explicitly to local
operator tools without logging it or any connection credentials; keep all
host/port/database/data-directory provenance checks in place.

A PostgreSQL server started as a child of a normal foreground ShellExec may be terminated when that shell exits. Keep a disposable cluster under a supervised background shell while running tests; stop it explicitly with the matching `pg_ctl` and exact data directory, then remove only that temporary directory. When initializing with `--no-locale`, explicitly pass `--encoding=UTF8`; otherwise the cluster may use `SQL_ASCII`, which rejects escaped non-ASCII text in existing JSON-backed test fixtures.

For compatibility tests that pin a PostgreSQL major, select the matching installed server tools explicitly rather than trusting the newest `initdb` on PATH. In the current Replit Nix shell, PostgreSQL may also default its Unix socket to `/run/postgresql`, which is absent; a disposable loopback-only cluster can set `unix_socket_directories=/tmp`.

**Why:** The existing development cluster was PostgreSQL 16 while a refreshed PATH selected PostgreSQL 17; a restore-integrity test correctly rejects a different major. Separately, an unqualified admin client command created an empty auxiliary database on its ambient cluster while Django used the disposable test cluster. The packaged server's absent default socket directory also prevented an otherwise isolated temporary server from starting, and a foreground test server did not survive its ShellExec process. A disposable `SQL_ASCII` cluster also rejected Unicode synthetic labels during PostgreSQL-backed tests.

**How to apply:** Before starting or repairing the local DB, inspect its `PG_VERSION` and use a matching installed server major version. Before isolated test setup/cleanup, verify `SHOW server_version`, `SHOW data_directory`, host, and port through an explicitly directed connection; use explicit target arguments for every admin command. When initializing an isolated cluster, set a socket directory that exists, explicitly select UTF8 encoding, and keep the server process alive under the background task until all checks finish. Stop that exact cluster before removing its temporary directory. If the matching binaries or intended target cannot be verified, stop rather than initialize, upgrade, or clean up an existing database.

Do not infer that a previously stopped shared preview is still stopped when a
restoration request arrives. Inspect current workflow status and startup logs
before invoking any runner.

**Why:** A subsequent workspace wakeup had already started the managed runner,
applied its pending migration and run its seed before the agent's first
inspection. Restarting again would have repeated writes the owner prohibited.
A backup taken afterward cannot be represented as pre-migration evidence.

**How to apply:** If it is already healthy, leave it running, disclose observed
startup side effects separately from agent actions, and verify the current
synthetic target. Never promise a pre-change baseline retroactively or claim a
prior seed left grants/data unchanged without an actual before snapshot.

Keep recovery evidence inside an explicitly ignored/private workspace area
when it must survive workspace lifecycle changes; do not rely on `/tmp` or
outside-project home directories as persistent backup storage.

**Why:** After another workspace interruption, the prior outside-project backup
and temporary workflow logs were absent while the in-project PostgreSQL data
remained. PostgreSQL logs recorded interrupted shutdowns, not an app traceback.

**How to apply:** Preserve existing bundles, disclose missing prior artifacts,
and distinguish local workspace recovery evidence from approved durable/off-site
backups. Normal recovery must not initialize, migrate or seed; verify unchanged
database row fingerprints across controlled stop/start before claiming safety.

Tool termination is not proof that a disposable runner finished its cleanup.
Closed TCP ports can coexist with leftover temporary cluster/socket/PID files.

**Why:** Stopping a supervised review runner interrupted its exit cleanup and
left its owned temporary cluster directory after both service ports closed.

**How to apply:** Verify the exact owned temporary directory, PostgreSQL version
and postmaster identity; stop only that verified server if still active, then
remove only its temporary directory. Never infer cleanup or target another
cluster merely from a closed port or a stale PID file.
