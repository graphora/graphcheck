# Contributing to GraphCheck

## Branches

`development` is the default/integration branch — open PRs against it. `main` holds release tags (v0.1.0). Never push directly to either.

## Dev setup

Requires Python 3.12+ and `uv` (https://docs.astral.sh/uv/).

```bash
uv sync --group dev
```

This installs GraphCheck plus the dev dependencies (pytest, pytest-cov, hypothesis, ruff, pre-commit, testcontainers[neo4j]).

Install the pre-commit hooks once, so lint/format run automatically on every commit:

```bash
uv run pre-commit install
```

## Tests and lint

Run the test suite:

```bash
uv run pytest
```

Coverage is enforced in CI, not locally, so focused `pytest -k ...` runs during development won't trip the package-wide threshold.

Lint and format:

```bash
uv run ruff check --fix .
uv run ruff format .
```

## Canonical sample reports

The canonical report artifacts are `docs/samples/reports/report-findings.html` and
`docs/samples/reports/report-clean.html`. Docker Engine or Docker Desktop must be running, and the
pinned `tests/fixtures/external/fraud-ring` submodule must be initialized at the committed gitlink.

```bash
git submodule update --init --recursive
```

The generator creates and removes its own disposable Neo4j 5.26.28 Testcontainers instance. It
does not use `profiles.yml` or the persistent Docker Compose database. Regenerate the artifacts:

```bash
uv run python tools/generate_sample_reports.py
```

Verify them byte-for-byte without writing:

```bash
uv run python tools/generate_sample_reports.py --check
```

Hosting or serving the reports is outside the scope of this workflow.

## Hostile graph certification

The fast hostile set runs the real `debug`, `profile`, and `run` command boundary against empty,
APOC-less, and noisy LLM Graph Builder-shaped databases in a supported Neo4j Testcontainer:

```bash
uv run python tools/run_hostile_graphs.py --case fast
```

The complete set also starts a three-member Neo4j 4.4 Enterprise cluster and downloads the
checksum-pinned Stanford SNAP EU email graph. It is intentionally slower and requires Docker,
network access, and acceptance of Neo4j's Enterprise license:

```bash
uv run python tools/run_hostile_graphs.py --case all
```

Each distinct product defect found by this matrix must receive its own issue and focused regression
test; link those issues from the hostile-graph parent issue.

## PR flow

1. Branch off `development` (never off `main`, and never push directly to either).
2. Make your changes, keeping commits scoped and the working tree clean.
3. Open a PR against `development` using the PR template - every box in the Definition of Done is required before merge.
4. Address review feedback from the named CODEOWNER.
5. Once approved and CI is green, the PR is merged into `development`.

## Changelog

Every PR that changes user-facing behavior adds an entry to `CHANGELOG.md` under the current `## [Unreleased]` section, in the matching category (`### Added`, `### Changed`, or `### Fixed`). Skip the entry only for changes with no user-facing effect (internal refactors, test-only changes, CI tooling).

Write the entry as a plain, one- or two-sentence description of what changed, in the same voice as the existing entries - not a copy of the commit message or PR title.

Release work (not part of a normal PR) turns `[Unreleased]` into a dated version section and starts a fresh `[Unreleased]` heading, tied to the release flow in #41.

## Definition of done

See the pull request template. Every box is required before merge.

## Decision rights (§13)

Reversible in under half a day → decide yourself. Reversible in over two days, or any cross-contract change (`results.json`, check YAML, exit codes, CLI surface) → escalate to Ezhil.

## Anti-slop

No abstractions without three callers. No "just in case" params. No comments restating code. No swallowed exceptions. No un-issued TODOs.

## Attribution

No AI attribution anywhere — commits, PRs, issues, docs, comments. Write in a plain human voice.
