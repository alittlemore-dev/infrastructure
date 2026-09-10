# alittlemore.dev infrastructure

[🇷🇺 Russian version](./README_RU.md)

The unified Docker Compose runtime and deployment repository for
[Personal Workspace](https://github.com/alittlemore-dev/personal-workspace) and
[Competency Trainer](https://github.com/alittlemore-dev/competency-trainer), with the shared
[frontend](https://github.com/alittlemore-dev/frontend). Application source code lives in its own
repositories and is published as container images; this repository owns the production topology,
configuration, secrets, TLS edge, release lifecycle, and the integrated local development
entrypoint.

## Features

- One public application origin and one nginx edge for both applications.
- Namespaced API routing: `/api/personal-workspace/*` and `/api/competency/*` are translated to
  each backend's existing `/api/*` contract.
- Separate PostgreSQL, Valkey, credentials, volumes, and private networks for each application.
- Shared MinIO object storage and Databasus backups with scoped identities and buckets.
- Synchronized blue/green application rollouts with health checks and automatic routing rollback.
- Service-scoped configuration with native variable names and SOPS/age-encrypted secrets.
- Public HTTPS APIs with operational tools and the Agent API restricted to the VPN.
- One shared frontend image serving every non-API route through the nginx edge.

## Local development

Keep `infra`, `frontend`, `personal-workspace`, and `competency-trainer` next to each other. Trust
the local CA once, then start the complete stack:

```bash
make dev-trust
make dev
```

The shared edge and frontend are available at `https://alittlemore.localhost`. Personal Workspace
APIs start at `/api/personal-workspace/`, and Competency Trainer APIs at `/api/competency/`.
Generated logins are stored in `.dev-state/credentials`.

## Production deployment

Prepare the production configuration, age identity, encrypted secrets, DNS, and registry login as
described in [Production deployment](../docs/production-deploy.md). On the first deployment, issue
the shared certificate and start the stack:

```bash
make certbot-issue
make deploy
```

Later deployments and configuration changes use the same `make deploy` command. `make run` remains
as a compatibility alias. To stop containers without deleting named volumes:

```bash
make stop
```

## Checks

Check system prerequisites, then run the complete local quality gate:

```bash
make doctor
make quality
```

`make quality` finds SOPS 3.13.3 and age-keygen 1.3.2 on `PATH` or installs checksum-verified
binaries in an ignored local cache. Callers do not pass binary paths. The command runs the same
complete gate as CI, including the real SOPS/age round trip.

Dependabot checks GitHub Actions, Compose images, and Dockerfile base images every week. A few pins
need coordinated manual edits: SOPS and age releases include per-platform checksums, while the
cert-sync OpenSSL package follows the selected Alpine branch. Check those without changing files:

```bash
make dependencies-status
```

The command reports both current pins and available updates, and fails only when an upstream lookup
or local pin cannot be read. It uses the GitHub Releases API and the official Alpine aports mirror,
so it requires network access; it is intentionally separate from `make quality`.

The application and infrastructure image scan is separate because it requires registry access:

```bash
make security-images
```

`make status` reports the active slot/release and project containers without changing state. Run
`make` or `make help` for the full target list.

## Documentation

- [Production deployment and operations](../docs/production-deploy.md)
- [Encrypted secrets layout](../secrets/README.md)
