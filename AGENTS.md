# Infrastructure Repository Instructions

## Script Placement

- Store every multi-line executable script under `infra/scripts/`, regardless of whether it is
  called by GitHub Actions, Compose, a Dockerfile, Make, or another script. Do not place scripts
  beside workflow files, Dockerfiles, or component configuration.
- Keep workflows, Compose files, Dockerfiles, and Makefiles declarative and readable. They may set
  environment variables and invoke a script, but branching, loops, heredocs, pipelines, validation,
  cleanup, and other multi-command logic must be implemented in a named script under
  `infra/scripts/`.
- When changing an existing inline or misplaced multi-line script, move the affected logic into
  `infra/scripts/` instead of extending it in place.

## Robust Validation

- Do not hardcode deployment-specific filesystem paths, hostnames, IP addresses, usernames,
  registry locations, credential identities, or other environment choices into validation logic
  or tests. Validate structural and security properties instead, such as normalized absolute paths,
  valid DNS syntax, uniqueness, ownership, permissions, and explicit sentinel files.
- Tests must exercise observable behavior and durable security invariants. Do not make tests depend
  on one machine's directory layout or assert exact implementation text when a behavioral assertion
  can cover the contract.

## Make Interface

- Developer-facing Make targets must not require callers to discover or pass tool binary paths.
  Resolve exact compatible binaries automatically or bootstrap checksum-pinned tools into a
  repository-local ignored cache.
- Local and CI quality entrypoints must have identical coverage. Required checks must fail with an
  actionable error when their prerequisites cannot be prepared; they must not silently skip.
- Keep the default Make goal read-only and self-documenting. Production mutations and
  security-sensitive paths, identities, and credentials must remain explicit operator choices.
