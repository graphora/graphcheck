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

## PR flow

1. Branch off `development` (never off `main`, and never push directly to either).
2. Make your changes, keeping commits scoped and the working tree clean.
3. Open a PR against `development` using the PR template - every box in the Definition of Done is required before merge.
4. Address review feedback from the named CODEOWNER.
5. Once approved and CI is green, the PR is merged into `development`.

## Definition of done

See the pull request template. Every box is required before merge.

## Decision rights (§13)

Reversible in under half a day → decide yourself. Reversible in over two days, or any cross-contract change (`results.json`, check YAML, exit codes, CLI surface) → escalate to Ezhil.

## Anti-slop

No abstractions without three callers. No "just in case" params. No comments restating code. No swallowed exceptions. No un-issued TODOs.

## Attribution

No AI attribution anywhere — commits, PRs, issues, docs, comments. Write in a plain human voice.
