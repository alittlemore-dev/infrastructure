# Production deployment

## Deployment flow

The manual `.github/workflows/deploy.yml` workflow preserves the existing rsync/SSH deployment
model while making this repository the only deploy payload:

1. GitHub renders `.env` from `infra/deploy/runtime-env.manifest.json` using protected Environment
   variables and secrets.
2. The workflow copies only `.dockerignore`, `Makefile`, `docker-compose.yml`, `infra/`, and the
   rendered `.env` into a deployment payload.
3. rsync uploads the payload into a run-specific directory below remote `.deploy-state` without
   touching the active runtime tree.
4. One SSH transaction acquires the same host-side lock used by local lifecycle commands and moves
   the complete staging directory under `.deploy-state/releases/`. No live directory is updated
   file by file.
5. While still holding that lock, SSH optionally runs `make certbot-issue` and then `make run`
   directly from the candidate release. A successful runtime switch atomically records both its
   blue/green slot and release ID in `.deploy-state/active-slot`.
6. Only after that runtime commit does the workflow atomically switch `current` to the candidate;
   it then updates the best-effort `previous` pointer to the former payload.

If either command fails before the runtime commit, `current` stays on the former payload. On a first
failed deployment there is no `current` link, but the complete failed release is retained for
diagnosis. If SSH is interrupted after the runtime commit, the workflow reads the owner-only commit
marker and finishes publishing `current`; a later deployment also reconciles `current` from that
marker before starting another rollout. Runtime-release retention is bounded to three payloads:
the current payload, its previous fallback, and—after a failed attempt—the diagnostic candidate.
Older release directories, including their generated `.env` files, are removed under the runtime
lock. A final workflow step removes a run-specific upload directory when rsync or activation stops
before promotion. If the runtime lock is busy, cleanup is deferred and the next successful
activation removes every other validated `incoming-<run>-<attempt>` directory.

Deploy runs are serialized and never cancel an in-progress stateful rollout. The deploy workflow
does not clone or synchronize either application repository. Their build and
publish pipelines own the four `latest` tags documented in the root README. A deploy therefore
uses whatever immutable image digests those tags resolve to when `make run` executes. Each of the
four references is pulled once, and all services in that run start from that locally resolved tag
without another pull.

Create a protected GitHub Environment named `production`, restrict it to `main`, and require a
reviewer. Configure these deploy connection values:

- Variables: `REMOTE_HOST`, `REMOTE_USER`, `REMOTE_PATH`, `SSH_HOST_KEY_FINGERPRINT`
- Secret: `SSH_PRIVATE_KEY`

`SSH_HOST_KEY_FINGERPRINT` must be the pinned `SHA256:...` fingerprint of the server host key. The
workflow obtains the presented keys with `ssh-keyscan`, retains only key lines whose fingerprint
exactly matches the pin, and then enables strict host-key checking for preflight, rsync, and both
SSH commands. `REMOTE_HOST`, `REMOTE_USER`, and `REMOTE_PATH` are restricted to shell-safe forms
before their first use. Rotate the fingerprint deliberately when the server host key changes.

Bootstrap the remote path once before enabling deployment. It must be a real, non-symlinked,
deploy-user-owned absolute directory whose final component is `alittlemore-infra`:

```bash
install -d -m 700 /srv/alittlemore-infra
printf 'alittlemore-infra\n' > /srv/alittlemore-infra/.alittlemore-infra-deploy-root
chmod 600 /srv/alittlemore-infra/.alittlemore-infra-deploy-root
```

Set `REMOTE_PATH=/srv/alittlemore-infra` for this example. The workflow verifies the path and
sentinel ownership before creating this layout:

```text
/srv/alittlemore-infra/
├── current -> .deploy-state/releases/release-<run>-<attempt>
├── previous -> .deploy-state/releases/release-<run>-<attempt>
├── certificates/
└── .deploy-state/
    ├── active-slot
    ├── compose-secrets/
    ├── minio-credentials.sha256
    ├── releases/
    └── runtime.lock
```

The `current` and `previous` links are workflow-managed. Each release contains an
`.alittlemore-runtime-root` marker, so lifecycle scripts always use the stable root's shared lock,
slot/release commit marker, and materialized secrets. The release-local `infra/nginx/certs` path is
a symlink to the stable `certificates/` directory; certificate rotations therefore survive payload
changes. A manual `make run` remains supported; outside the deployment workflow it records only the
blue/green slot because `current` already identifies the operator-selected payload.

Add every `vars` entry from `infra/deploy/runtime-env.manifest.json` as a GitHub Environment
variable. Add every `secrets` entry as a GitHub Environment secret. The manifest is authoritative;
`.env.example` documents representative values. `PERSONAL_WORKSPACE_SENTRY_DSN` and
`COMPETENCY_SENTRY_DSN` are the only values allowed to be empty.

`IMAGE_REGISTRY` contains only the registry/repository prefix and must not end in `/`. Registry
credentials do not belong in runtime `.env`; authenticate the deploy user's Docker client on the
server using the registry-specific login mechanism.

Keep `MINIO_ROOT_ACCESS_KEY`, `PERSONAL_WORKSPACE_MINIO_ACCESS_KEY`,
`COMPETENCY_MINIO_ACCESS_KEY`, and `DATABASUS_MINIO_ACCESS_KEY` equal to the fixed identities in
`.env.example`; `make run` rejects other names so credential rotation cannot leave unmanaged old
users behind. Generate distinct random values of at least eight characters for the four
corresponding `*_SECRET_KEY` entries; `make run` rejects reused values.

After the first successful MinIO bootstrap, `make run` stores only SHA-256 fingerprints of those
four secret keys in the stable, owner-only `.deploy-state/minio-credentials.sha256` file. Every
later run compares the configured credentials before pulling images or touching Docker. A changed
secret is rejected instead of updating a live IAM user and breaking the old application slot or
the credential saved in Databasus during a failed rollout. MinIO credential rotation is therefore
a separate coordinated maintenance operation, not part of ordinary deployment; update Databasus'
saved S3 destination credential as part of that maintenance.

## Secrets

The deploy renderer quotes and escapes values before writing the host-side `.env`. At startup,
`infra/scripts/compose_secrets.sh` writes application secrets into
`.deploy-state/compose-secrets/`, restricts the directory to the deploy user, and exposes individual
files through Compose secrets. Non-root containers receive only the files they need. Secret values
are not copied into service `environment` entries and therefore are not exposed by `docker inspect`.
The MinIO root identity is mounted only into MinIO and its one-shot bootstrap. Application MinIO
identities are mounted into the bootstrap and their respective backend processes. The dedicated
Databasus identity is created by the bootstrap; its credentials are entered into Databasus when
the S3 destination is configured in the VPN-only UI.

The Competency Trainer PASETO public/private key pair and Agent Access issuing material are parsed
with OpenSSL before Compose changes the running stack. The PASETO public key must match its private
key. The issuing certificate must be the first certificate in the two-certificate issuing/root
chain and must match the issuing private key.

No standalone PEM files are tracked or deployed by rsync. Multiline application and Agent PKI
values travel only inside the protected generated `.env` and are materialized into owner-only
runtime secret files. On the deployed host, nginx server certificates live in
`REMOTE_PATH/certificates/` and are exposed to Compose through the release-local
`infra/nginx/certs` link, while Certbot state lives in the `letsencrypt` named volume. Certificate
sync keeps the current certificate release and at most two older releases.

## TLS

The stack uses one certificate lineage named by `TLS_CERTIFICATE_NAME`, covering:

- `personal-workspace.alittlemore.dev`
- `competency.alittlemore.dev`
- `s3.alittlemore.dev`
- `agent.competency.alittlemore.dev`

For the first deployment, enable the workflow's `issue_certificates` input; it runs issuance from
the candidate release before `current` exists. After the first successful deployment, issue or
expand the certificate manually with:

```bash
make -C /srv/alittlemore-infra/current certbot-issue
```

If nginx is running, the command uses its ACME webroot. On the first deployment it uses Certbot's
standalone listener, so host port 80 must be free. Routine renewal should be scheduled by the host:

```bash
make -C /srv/alittlemore-infra/current certbot-renew
```

To validate and activate an existing renewed certificate in the unprivileged nginx bind mount,
syntax-check nginx, reload it, and verify the certificate it actually serves on loopback:

```bash
make -C /srv/alittlemore-infra/current certbot-sync
```

## Blue/green behavior and recovery

`.deploy-state/active-slot` stores one global `blue` or `green` value. Deploy workflow runs append
the validated `release-<run>-<attempt>` ID on the same atomic line so payload recovery can identify
the runtime commit. Both projects move together; nginx never points one application at the new slot
while the other remains on the old slot. The active containers continue using their original image
digests even though their references end in `:latest`.

Before the edge switch, `make run` must successfully:

- pull all application images;
- make both PostgreSQL and Valkey pairs plus the shared MinIO healthy, complete the MinIO bootstrap,
  and start the shared Databasus container (Databasus has no container health probe);
- run both backend initializers;
- make both target backends and frontends healthy;
- start both TaskIQ workers and schedulers.

The certificate helper stages a new release, parses the key and certificate, checks their match,
checks expiry and all four hostnames, applies restrictive permissions, and only then atomically
moves the `current` symlink. Before replacing the edge, `make run` builds a slot-specific nginx
image and runs its complete render plus `nginx -t` path in an isolated one-off container. Only then
is nginx force-recreated with the target service names. The four HTTPS health checks use
`--resolve ...:127.0.0.1`, so they always exercise the just-started local edge rather than an
external DNS target.

The state file is updated and previous application containers are stopped only after restart-policy,
served-certificate, and application checks succeed. A pre-switch failure leaves the old edge
untouched. A post-switch failure automatically recreates nginx from the previous slot's preserved
image and upstream names. On the first deployment there is no previous edge to restore, so a failed
post-switch verification stops nginx and the target web containers instead of leaving an
unverified public edge running. Routing rollback does not undo database migrations.

There is one public nginx container, so its force-recreation can cause a short edge interruption.
It uses graceful `SIGQUIT` shutdown with a 30-second grace period, but requests exceeding that
window can still be terminated. This is application blue/green switching, not redundant edge high
availability. Backend initializers and target workers also run against the shared databases
before the edge switch while the old slot may still serve traffic. The old scheduler is stopped
before the target scheduler starts, preventing duplicate scheduling; rollback stops target
background processes and restarts the preserved old scheduler. This repository intentionally does
not enforce backward-compatible or expand/contract migrations; releases with incompatible
migrations must accept that cutover risk or arrange a maintenance window.

Application `latest` is intentionally not a rollback identifier. For a controlled rollback, first
retag the desired backend and frontend digests as `latest` in the registry, then rerun `make run`.

## Data and backup boundaries

Each application owns separate named volumes and credentials for PostgreSQL and Valkey. MinIO and
Databasus are shared infrastructure services with one named volume each. This is a new deployment
topology: it does not import or attach the old repositories' Compose volumes.
Before the first unified start, stop both legacy Compose projects so their fixed container names and
host ports do not conflict. Disable manual production deploy workflows in both application
repositories and remove their access to the production GitHub Environment/SSH credentials before
cutover; otherwise an accidental legacy workflow dispatch can recreate the old stacks. This
intentionally starts with fresh unified-project volumes; migration or restoration of legacy data
is outside this no-backward-compatibility cutover.
Configure the single Databasus instance with both PostgreSQL sources:

- Personal Workspace: host `personal-workspace-postgres`, port `5432`, database/user/password from
  `PERSONAL_WORKSPACE_DB_NAME`, `PERSONAL_WORKSPACE_DB_USER`, and
  `PERSONAL_WORKSPACE_DB_PASSWORD`.
- Competency Trainer: host `competency-postgres`, port `5432`, database/user/password from
  `COMPETENCY_DB_NAME`, `COMPETENCY_DB_USER`, and `COMPETENCY_DB_PASSWORD`.

For an S3 backup destination use endpoint `http://minio:9000`, bucket `database-backups`, region
from `MINIO_REGION`, and the `DATABASUS_MINIO_ACCESS_KEY` / `DATABASUS_MINIO_SECRET_KEY`
credentials. The MinIO bootstrap creates that private bucket and restricts the Databasus identity
to it.

The application identities are separate: Personal Workspace can use `media` and
`knowledge-private`; Competency Trainer can use `media`; neither can use `database-backups`.
Databasus can use only `database-backups`. Both current application images hard-code the bucket
name `media`, so their public media objects intentionally occupy one shared namespace. Complete
bucket-level isolation would require changing the application code to make that bucket name
configurable. MinIO CORS allows both application origins. The shared public S3 endpoint rejects
`knowledge-private` and `database-backups` before a request reaches MinIO.

## Private network boundaries

Only nginx publishes normal runtime ports. `80` and `443` are public. Ports `18081` through `18083`
must bind to `VPN_BIND_ADDRESS`; do not use `0.0.0.0` or a public interface address. PostgreSQL,
Valkey, MinIO, Databasus, backend, frontend, and TaskIQ processes remain on per-application bridge
networks.

The Agent API on `18083` requires a client certificate chained to the configured Competency Trainer
Agent CA. nginx forwards only the seven explicit method/path combinations and strips any
caller-supplied certificate header from the public application listener. Create offline root and
issuing material outside this repository with:

```bash
make agent-ca-init OFFLINE_ROOT_DIR=/absolute/offline/path ISSUING_DIR=/absolute/issuing/path
make agent-client-csr AGENT_ID=agent-name CLIENT_OUTPUT_DIR=/absolute/client/path
```

The helper resolves symlinks and refuses relative paths, repository-local output, overlapping root
and issuing trees, and overwriting existing PKI files.

## Host requirements

The server needs Linux/GNU coreutils (`readlink -f`, `stat -c`, and `mv -T`), Docker Engine with
Docker Compose v2.24.0 or newer, `make`, Python 3, `curl`, OpenSSL, rsync, `flock` (normally from
util-linux), SSH access, public DNS for all certificate names, and registry credentials when the
application images are private. Docker should be enabled at boot so the configured restart
policies take effect after a host reboot.

`make run`, every TLS mutation, and `make stop` share an exclusive host-side runtime lock. This
prevents an SSH timeout or a manually started command from racing a later deployment. `make stop`
uses a non-secret minimal Compose environment, so it remains available even if `.env` or PKI is
missing or invalid. Normal commands reject a symlinked, foreign-owned, group-readable, or
world-readable `.env`; create it with mode `0600`. On the server, always invoke operational targets
through the active payload, for example:

```bash
make -C /srv/alittlemore-infra/current run
make -C /srv/alittlemore-infra/current stop
```

The `nginx` container uses `restart: always`; active application and dependency containers use
`unless-stopped`. nginx's local liveness probe terminates PID 1 after 12 consecutive failures so
Docker can recover the edge without a Docker socket mount or privileged watchdog.

## Quality gates

The CI workflow runs these independently of deployment:

```bash
make tests
make check
make lint-dockerfiles
make security-trivy-config
```

Run `make security-trivy-images` separately with a configured `.env` and registry login. It pulls
the four application images, builds the pinned nginx, MinIO, and certificate-sync wrappers, and
scans all twelve unique application and infrastructure runtime images for fixed high/critical OS
and library vulnerabilities.
