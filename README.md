# alittlemore.dev infrastructure

This repository is the single runtime and deployment entrypoint for the `personal-workspace` and
`competency-trainer` applications. Application source code is not part of this repository and is
never built by its Compose project.

## Runtime image contract

`IMAGE_REGISTRY` is the registry/repository prefix, for example `ghcr.io/alittlemore-dev`. The two
application repositories are expected to build and publish these four images:

- `${IMAGE_REGISTRY}/personal-workspace-backend:latest`
- `${IMAGE_REGISTRY}/personal-workspace-frontend:latest`
- `${IMAGE_REGISTRY}/competency-trainer-backend:latest`
- `${IMAGE_REGISTRY}/competency-trainer-frontend:latest`

Every application service declares `pull_policy: always`. `make run` resolves and pulls each of the
four references once, then starts every process with `--pull never`, so one deployment cannot mix
different digests if a moving `latest` tag changes midway. Infrastructure dependencies keep fixed
tags:

- PostgreSQL `18.4-alpine`
- Valkey `9.0.1`
- MinIO `RELEASE.2025-09-07T16-13-09Z`
- MinIO Client `RELEASE.2025-08-13T08-35-41Z`
- Databasus `v3.47.1`
- nginx-unprivileged `1.31.3-alpine`
- Certbot `v5.2.2`
- Certificate-sync helper: Alpine `3.22.2` with OpenSSL `3.5.7-r0`

## Architecture

The applications share one Compose lifecycle and one public nginx edge. Stateful boundaries are
chosen per dependency:

- Personal Workspace and Competency Trainer each have their own PostgreSQL and Valkey instances,
  named volumes, credentials, and Docker network.
- One MinIO instance is attached to both application networks. A bootstrap job creates fixed IAM
  users and scoped policies for both applications and Databasus, plus the `media`,
  `knowledge-private`, and `database-backups` buckets.
- One Databasus instance is attached to both networks and manages backups for both PostgreSQL
  instances.
- nginx joins both private networks and routes by hostname.
- Backend, frontend, worker, and scheduler containers use synchronized blue/green slots. The old
  scheduler is stopped before its replacement starts, so exactly one scheduler exists per
  application; old workers remain available until the new slot is verified.
- PostgreSQL, Valkey, MinIO, backends, frontends, workers, and Databasus have no direct
  host-published ports.

Both current application images hard-code the bucket name `media`, so that bucket is a deliberately
shared namespace even though the applications authenticate as different MinIO users. Personal
Workspace alone additionally receives access to `knowledge-private`; Databasus alone receives
access to `database-backups`. See the production guide for the exact access and routing boundaries.

Public routes:

- `https://personal-workspace.alittlemore.dev`
- `https://competency.alittlemore.dev`
- `https://s3.alittlemore.dev` (shared MinIO API)

`https://agent.competency.alittlemore.dev` returns `404` publicly. Its seven-operation Agent API is
available only through the VPN-bound `18083` mTLS listener.

VPN-only tools:

- `18081`: shared MinIO Console
- `18082`: shared Databasus
- `18083`: Competency Trainer Agent API over TLS and mTLS

## Start and stop

Open production configuration is tracked per scope:

```text
config/
├── platform/production.env
├── personal-workspace/production.env
└── competency-trainer/production.env
```

Application files use the variables' native names, so both applications can have their own
`APP_DEBUG`, `DB_NAME`, and similar settings without prefixes. Only the small set of values needed
by Compose itself is rendered into owner-only internal aliases below `.deploy-state/`.

Secrets use the same service-scoped layout under `secrets/`, but the tracked documents are
encrypted with SOPS and age. Before the first start, create the production and recovery age
identities and run the local secret bootstrap described in the
[production guide](docs/production-deploy.md). Install SOPS on the runtime host and keep its private
age identity at the absolute owner-only path configured by `SOPS_AGE_KEY_FILE`.

The Docker client on the machine that runs the stack must already be authenticated to
`IMAGE_REGISTRY` when the application images are private.

Issue the initial shared certificate after all four DNS names point to the host:

```bash
make certbot-issue
```

Start or update everything with one command:

```bash
make run
```

The command chooses the inactive slot, pulls application images, prepares the shared MinIO volume,
starts fixed-version infrastructure, provisions MinIO buckets/users/policies, runs both backend
initializers, starts the new application slot, validates and atomically syncs certificates, builds
and syntax-checks a slot-specific nginx
image, switches nginx, verifies restart policies and the locally served certificate, checks both
applications through `127.0.0.1` with their production hostnames, records the active slot, and
drains the old one.

Stop containers without deleting named volumes. This emergency path does not parse or validate
tracked configuration, encrypted secrets, or application PKI, so it remains usable when runtime
configuration is damaged. A successful full stop clears the active-slot marker, and the next
`make run` performs a fresh blue-slot start:

```bash
make stop
```

See [Production deployment](docs/production-deploy.md) for the one-time local SOPS bootstrap,
age-key setup, TLS renewal, security boundaries, and rollback behavior.

## Checks prepared for a separate run

```bash
make tests
make check
make lint-dockerfiles
make security-trivy-config
make security-trivy-images
make quality
```

`make tests` checks the environment/manifest/exposure, nginx security, PKI path, and deploy payload
contracts. In CI it also performs a real SOPS/age encrypt-decrypt round trip with checksum-pinned
tools. `make check` adds shell syntax, manifest parsing, and a fully rendered
`docker compose config`. The lint target uses pinned Hadolint and ShellCheck images. Trivy config
and image scans are separate because image scans need registry access and build the three small
infrastructure wrappers.
