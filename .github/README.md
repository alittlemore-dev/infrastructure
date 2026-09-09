# alittlemore.dev infrastructure

[🇷🇺 Russian version](./README_RU.md)

The unified Docker Compose runtime and deployment repository for
[Personal Workspace](https://github.com/alittlemore-dev/personal-workspace) and
[Competency Trainer](https://github.com/alittlemore-dev/competency-trainer). Application source
code lives in its own repositories and is published as container images; this repository owns the
production topology, configuration, secrets, TLS edge, and release lifecycle.

## Features

- One Compose lifecycle and one nginx edge for both applications.
- Separate PostgreSQL, Valkey, credentials, volumes, and private networks for each application.
- Shared MinIO object storage and Databasus backups with scoped identities and buckets.
- Synchronized blue/green application rollouts with health checks and automatic routing rollback.
- Service-scoped configuration with native variable names and SOPS/age-encrypted secrets.
- Public HTTPS application routes with operational tools and the Agent API restricted to the VPN.

## Run

Prepare the production configuration, age identity, encrypted secrets, DNS, and registry login as
described in [Production deployment](../docs/production-deploy.md). On the first deployment, issue
the shared certificate and start the stack:

```bash
make certbot-issue
make run
```

Later deployments and configuration changes use the same `make run` command. To stop containers
without deleting named volumes:

```bash
make stop
```

## Checks

Run the complete local quality gate:

```bash
make quality
```

The application and infrastructure image scan is separate because it requires registry access:

```bash
make security-trivy-images
```

## Documentation

- [Production deployment and operations](../docs/production-deploy.md)
- [Encrypted secrets layout](../secrets/README.md)

