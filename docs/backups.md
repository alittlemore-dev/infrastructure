# Infrastructure backups

The persistent `backup` worker collects a complete recovery set every six hours in UTC. It starts
after backend initialization and readiness. PostgreSQL, Valkey, application files and recovery
configuration are collected independently; this is not an atomic snapshot of the distributed system.
An incomplete source set cannot replace a successful backup.

## Operations and status

```bash
make backup-run       # explicit run in the already running worker; busy exits 2
make backup-status    # safe JSON, without decrypting secrets
make backup-health    # exits nonzero for unhealthy or incomplete backup state
make backup-smoke     # isolated fixture backup, restore and failure checks
```

`backup-run` exits 0 after every required destination succeeds, even if an optional destination is
pending. A failed run exits 1. Manual and scheduled runs share the same interprocess lock. Failed
scheduled runs wait for their next deadline; pending deliveries retry every five minutes using the
existing payload. A restart performs at most one overdue collection, rather than catching up every
missed interval. Development scheduling is disabled unless `BACKUP_SCHEDULE_ENABLED=true`.

The owner can open `dashboard?tab=healthcheck`. The tab polls every 30 seconds while active and
visible; times use the user's timezone. Reader failures clear the current health display. A worker
heartbeat is independent of lengthy collection/delivery, and a live worker can still have a stale,
failed or degraded backup. Freshness expires after seven hours. Optional delivery errors, cleanup
errors and evicted pending deliveries remain visible. A previous successful copy does not make a
failed attempt healthy.

`last_restore_verified_at` remains empty until an operator verifies a specific production set.
Passing fixture smoke tests does not verify production recovery.

## Inventory and retention

`infra/backup/sources.json` collects three PostgreSQL databases (custom dumps and globals without
role passwords), four Valkey RDBs, and objects in `media`, `knowledge-private`, `resume-private` and
`auth-avatars`. File indexes preserve original bucket/key, content type, user metadata, useful
headers, versions when present, size and SHA-256. Object keys are never used as filesystem paths.
Concurrent changes that cannot be read consistently fail the source.

Recovery configuration is an explicit allowlist of env/JSON configuration, encrypted SOPS,
Compose, runtime manifests and MinIO policies. Deployment metadata records the infra revision and
running application image digests. Local development records `local-built@sha256:…` when a locally
built image has no registry digest; it must be rebuilt from its source rather than pulled by that
identifier. Age private keys, plaintext secrets, registry/SSH credentials and private TLS material
are excluded. Keep an independent recovery age key outside the server, together with access to the
encrypted configuration. Role passwords must be restored from the appropriate SOPS secrets.

`config/platform/backup.destinations.json` currently selects only internal MinIO,
`database-backups/system/v1`. The dedicated backup identity is reconciled to the backup policy;
other identities and their assignments are preserved. Each destination owns its own commit manifests and retention refs:

| Reference | Meaning |
| --- | --- |
| `latest` | Newest collected snapshot whose delivery has been verified |
| `weekly_current` | First successful delivery in the current ISO week, pinned for that week |
| `weekly_previous` | Previous weekly checkpoint |

There are at most three distinct successful sets per destination. One payload can hold several
roles. On regular operation the previous weekly checkpoint is approximately 7–14 days old; outages
can make it older. This is not daily history. Uploads and SHA-256 readback must succeed before the
commit and reference switch; only then can unprotected managed objects be pruned. Allow temporary
space for another full set and staging. Orphan multipart uploads are aborted within the managed
payload namespace on the next delivery. Objects outside that namespace are preserved.

A delayed snapshot already superseded by a verified newer snapshot drains from the pending queue
without replacing `latest` or refreshing the success timestamp. This also covers a lost delivery
acknowledgement after retention has pruned the old commit. Failed status rereads preserve verified
set metadata and report degraded health. Manifests share a 64 MiB bound across delivery, download
and local verification.

The state, pending payloads and safe projection have separate persistent volumes. Pending storage
has a configurable byte budget; overflow evicts the oldest undelivered sets and records a delivery
gap. It never deletes protected remote checkpoints. Removing a destination from configuration does
not delete its remote data. Adding/changing destinations requires reviewing their access and the
existing delivery state. External endpoints require TLS; before enabling any external destination,
choose and verify client-side encryption and independent recovery-key storage. No external store is
configured by this change.

## Download and restore

Select a retained `run_id` from `make backup-status`. Download into a new, explicitly selected
directory; existing directories and symlink ancestors are rejected. For the running worker:

```bash
docker compose --file infra/compose/stop.yml exec --no-TTY backup \
  python -m infra_backup.cli download --destination internal \
  --destinations /etc/backup/destinations.json --run-id <retained-run-id> \
  --output-dir /work/recovery-<retained-run-id>
docker compose --file infra/compose/stop.yml exec --no-TTY backup \
  python -m infra_backup.cli verify --directory /work/recovery-<retained-run-id>
```

The output is private and contains a verified manifest and source artifacts. Copy it to the chosen
recovery host securely. A download racing with pruning can fail; select an available retained set
and retry. A failed download cannot be treated as a verified recovery directory.

Restore in an isolated environment before opening traffic. Use PostgreSQL 18 clients and compatible
Valkey 9.1.2 tools. Inspect globals SQL, resolve bootstrap-role conflicts and recreate needed roles
without importing obsolete passwords. Obtain current passwords from SOPS using the off-host age
key. After preparing the selected target databases, use `pg_restore --exit-on-error` for each
custom dump. Verify rows, permissions, foreign keys and sequences using SQL queries; listing a
dump alone does not prove recovery. Do not restore directly over live database volumes.

For files, read each source's `objects.json`; upload its indexed payload into the chosen recovery
bucket under the original key, with recorded content type, metadata and headers. Verify bytes and
SHA-256 after upload. Source version IDs identify the saved version, not a version ID to assign in
the new store. Never convert untrusted object keys into local file paths.

Load RDBs only into isolated instances and verify persistent keys and expiration semantics.
Do not automatically replay stale TaskIQ queues, leases, caches or locks. Auth sessions and
revocations need a coordinated invalidation procedure before traffic opens; simply importing an
old auth RDB can revive invalid sessions or lose subsequent revocations. Review the intended
application behavior and invalidate/rebuild operational state before enabling schedulers or login.

The smoke gate verifies PostgreSQL roles/FK/sequences, file metadata and bytes, RDB keys/TTL,
three-week retention, optional endpoint outage/retry, pending-budget gaps, interrupted multipart
uploads and ENOSPC. Its containers, networks and images are uniquely owned and cleaned up; it reads
no production config or secrets. For limited-memory machines, run checks serially:

```bash
bash infra/scripts/sequential_check.sh quality
```

This invokes the same Make gate with one BuildKit job (768 MiB limit); temporary fixture containers
also have explicit CPU/memory limits. It does not start the integrated development stack.

Verified on 2026-10-12: the full infra `make quality` gate (190 tests, strict typing of 21 files,
MinIO policy isolation and backup/restore/failure smoke), frontend `make quality` (1566 tests)
and `make ssr-smoke`, i18n `make quality` (77 tests), and `make secrets-verify` for all six SOPS
documents passed. The frontend card was tested against an actual authenticated fixture API response.
The private dashboard route did not fetch or serialize backup metadata during SSR.

## Reader boundary

`backup-status` mounts only the safe projection read-only. It has no source secrets, private state,
payloads, Docker socket, database connection or published ports. nginx exposes only GET
`/api/infra/healthcheck` and `/api/infra/backups/sets`; both require an owner session through the
shared SDK. Personal access tokens are denied, role/token caching is disabled and all diagnostics
use `Cache-Control: no-store`. Auth outages return 503; unhealthy backup diagnostics return 200 with
their health status. Docker-only port 18085 forwards token verification to the active auth upstream,
including after rollback. Reader/edge failures do not stop the worker.

The versioned `systems` envelope supports future explicitly registered providers. A new provider
must define its own sanitized detail contract, owner API coverage and frontend mapper/presentation;
it must not expose arbitrary private worker state.
