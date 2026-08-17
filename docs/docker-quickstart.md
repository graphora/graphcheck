# Local Docker quickstart

This guide starts a purely local Neo4j demo, loads the canonical reproducible fraud-ring fixture,
and runs GraphCheck's baseline-free demo checks. It requires no Aura, hosted database, or VM.

For general installation and project usage, see the [main README](../README.md).

## Prerequisites

- Docker Engine or Docker Desktop with Docker Compose.
- GraphCheck installed through the normal repository workflow. From a development checkout, use
  `uv sync --group dev` and prefix GraphCheck commands with `uv run`.
- Local ports `7474` and `7687` available.

The demo uses Neo4j 5.26.28 with database `graphcheck-demo`. Its disposable local credentials are
`neo4j` / `Password@123`, and Bolt is available only on `bolt://localhost:7687`.

## Start and load the fixture

Run these commands from the repository root:

```console
docker compose up -d
docker compose wait fixture-seed
```

The wait command is **required**. Detached `docker compose up -d` starts the services
asynchronously; it does not wait for the one-shot fixture loader. Only a successful
`fixture-seed` completion means the demo graph is ready for GraphCheck. Download, checksum,
connection, and Cypher errors produce a non-zero status instead of silently continuing.

Compose anonymously downloads only `fixtures/fraud-ring/seed.cypher` from the public canonical
`graphora/graphcheck-fraud-ring-fixture` repository at immutable commit
`d2b8c76c2d2940f53f71491703619961a699c293`. It verifies SHA-256
`d955dbf08a3821a53e3b39f4f5234c16d13eb08c33b2a573fe422c85d5dcd90a` before seeding. GraphCheck
does not maintain a copy of the fixture data.

The seeded graph contains 5,011 nodes and 5,872 relationships.

## Run GraphCheck

After `fixture-seed` completes successfully, run:

```console
graphcheck run
```

For a development installation, use `uv run graphcheck run` instead.

The expected results are:

- `fraud-ring-conformance/account-no-orphans`: 3 violations.
- `fraud-ring-conformance/account-owner-cardinality`: 4 violations.
- `graphcheck-action-smoke/smoke-connection-alive`: passes.

The two conformance failures are intentional planted defects. The overall GraphCheck exit code is
therefore expected to be non-zero; this does not mean the Docker setup failed. Results and the
offline HTML report are written under `.graphcheck/runs/latest/`.

No baseline is created or required by the default quickstart. Drift and baseline testing is a
separate follow-on workflow using [`examples/checks/example.yml`](../examples/checks/example.yml)
with a genuine before-state baseline; that example is outside default check discovery.

## Teardown

Remove the containers, network, downloaded fixture, and database volumes with:

```console
docker compose down -v
```

The next startup creates and seeds a fresh `graphcheck-demo` database.

## Troubleshooting

- If startup reports that port `7687` or `7474` is already in use, stop the local service using
  that port before retrying.
- If fixture loading fails, inspect `docker compose logs fixture-fetch fixture-seed`; do not run
  GraphCheck until `docker compose wait fixture-seed` succeeds.
