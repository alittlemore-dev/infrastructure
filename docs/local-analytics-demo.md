# Local analytics fixtures

After starting the isolated stack with `make dev`, run `make dev-seed-analytics`.
The command adds 120 UTC days of question views, engagement, submissions by sheet,
and generic site traffic. It uses existing published `demo-*` questions and `demo-*`
sheets; it fails explicitly if demo matrix content is absent.

The runner selects the local Compose project and development configuration only.
The generator refuses non-local or non-debug runtimes, never stores visitor identities,
and preserves existing aggregate rows. Repeating it does not duplicate a daily counter.
