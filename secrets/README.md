# Encrypted production secrets

This directory contains only SOPS-encrypted production documents:

```text
secrets/
├── platform/production.sops.yaml
├── personal-workspace/production.sops.yaml
└── competency-trainer/production.sops.yaml
```

Create the initial files locally from owner-only, service-scoped dotenv sources with
`infra/scripts/bootstrap_sops_secrets.sh`. Verify decryption with the recovery age identity, then
commit the encrypted documents together with `.sops.yaml`. Plaintext source files stay outside the
repository and are never sourced as shell code.

Keys inside each application document use that application's native names, such as
`APP_SECRET_KEY`, `DB_PASSWORD`, and `MINIO_ACCESS_KEY`. Directory scope distinguishes identical
names. `infra/deploy/runtime-secrets.manifest.json` contains only these native names and their
service-scoped runtime targets.

Never store decrypted documents, age private identities, or materialized runtime secret files in
this repository.

For routine updates, adding new secret fields, rotation constraints, and the mandatory protocol for
AI agents, see the **Updating an existing secret**, **Adding a new secret**, **Rotation
constraints**, and **Instructions for AI agents** sections in `docs/production-deploy.md`.
