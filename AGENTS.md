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
