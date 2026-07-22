# GraphCheck

GraphCheck is semantic observability for Neo4j property graphs: define what should be true in YAML,
run the checks against a live database, and get a scored report with evidence pointing back to the
affected graph elements.

It is designed to feel like pytest for a knowledge graph. A run validates the suite, probes the
target's capabilities, compiles parameterized read-only Cypher, evaluates each result in isolation,
and writes both machine-readable JSON and a self-contained offline HTML report.

> **Status:** v0.1.0 is implemented and under active development. The CLI, Neo4j connector, core
> engine, built-in core and PII packs, deterministic sampling, baseline lookup, and report writers
> are available. The public contracts are frozen for v0.

## What GraphCheck can check

GraphCheck supports three kinds of checks in the same suite:

- **Conformance** checks apply reusable built-in rules to graph structure and properties.
- **Competency** checks run an authored read-only Cypher query and assert its rows, columns,
  uniqueness, emptiness, or exact/contained results.
- **Drift** checks compare current node counts, relationship counts, or property coverage with a
  stored baseline.

The built-in conformance catalog currently includes:

| Pack | Checks |
| --- | --- |
| Core | `completeness`, `cardinality`, `no_orphans`, `property_type`, `property_format`, `value_in_set`, `uniqueness`, `hub_outlier`, `label_cooccurrence`, `rel_direction`, `temporal_sanity` |
| PII | `pii_name_match`, `pii_value_match` |
| Store consistency | `dangling_rels` is declared but is reported as unsupported unless the connector can provide the required store-consistency capability |

PII checks are explicitly heuristic and sampled. Reports include confidence metadata and graph
locations, but never include matched property values or claim complete PII discovery.

## Requirements

- Python 3.12 or 3.13
- A reachable Neo4j database
- [`uv`](https://docs.astral.sh/uv/) for the repository workflow shown below

APOC is probed and reported by `graphcheck init` and `graphcheck debug`. A missing optional
capability blocks only checks that declare it; those checks are recorded as unsupported instead of
silently passing.

## Install from source

Clone the repository and install the CLI as a uv tool:

```console
git clone https://github.com/graphora/graphcheck.git
cd graphcheck
uv tool install .
graphcheck --version
```

For development, create the locked environment instead:

```console
uv sync --group dev
uv run graphcheck --version
```

The examples below use the installed `graphcheck` command. Prefix them with `uv run` when working
from the development environment.

## Quickstart

### 1. Create a project

Run the initializer in the directory that should contain your checks:

```console
mkdir graph-health
cd graph-health
graphcheck init
```

This creates:

```text
graphcheck.yml          Project paths
profiles.yml            Neo4j connection profiles; added to .gitignore
checks/example.yml      Example conformance, competency, and drift checks
.graphcheck/            Run artifacts; added to .gitignore
```

Commands can be run from the project root or any child directory; GraphCheck walks upward until it
finds `graphcheck.yml`.

### 2. Configure Neo4j

Edit `profiles.yml`. Prefer an environment variable over a committed or shell-history password:

```yaml
default: local
profiles:
  local:
    uri: bolt://localhost:7687
    user: neo4j
    password_env: NEO4J_PASSWORD
    database: neo4j
```

`password_env` overrides a literal `password` when the variable is set. If the environment variable
is absent, GraphCheck falls back to `password` when one is configured.

Verify connectivity, server metadata, visibility, graph counts, and check capability requirements:

```console
graphcheck debug
graphcheck debug --json
```

### 3. Add a first suite

Replace `checks/example.yml` with a baseline-free suite that matches labels in your graph:

```yaml
suite: customer-health
defaults:
  severity: error
  tags: [production]

conformance:
  - id: customer-name-present
    check: completeness
    with:
      label: Customer
      property: name
      threshold: 0.98

competency:
  - id: customers-can-be-counted
    question: Can customers be counted?
    query: MATCH (c:Customer) RETURN count(c) AS count
    expect:
      rows: { exactly: 1 }
      columns: [count]
```

Suite YAML is strict: duplicate keys, unknown fields, unknown check types, invalid payloads, and
inconsistent expectations fail loudly before execution.

### 4. Run the checks

```console
graphcheck run
```

Interactive terminals show a per-check progress bar while the run is in flight. The bar is omitted
when output is redirected or captured, keeping CI logs and shell pipelines clean.

Useful selections include:

```console
graphcheck run --profile staging
graphcheck run --suite customer-health
graphcheck run --select tag:production
graphcheck run --suite customer-health --select tag:production --fail-fast
```

`--suite` and `--select` are repeatable. Repeated tag selectors use OR semantics. `--fail-fast`
stops after the first error-severity failure or error, while retaining later selected checks as
explicitly not run.

Every prepared run writes:

```text
.graphcheck/runs/<run-id>/results.json
.graphcheck/runs/<run-id>/report.html
.graphcheck/runs/latest/results.json
.graphcheck/runs/latest/report.html
```

`results.json` follows the versioned SPEC-01 contract. `report.html` embeds its styling and
interaction script and has no external assets or network calls, so it can be opened and shared
offline. The run-id directory preserves history; `latest` is a consistently published convenience
copy of the newest run.

## Exit codes

GraphCheck uses stable CI-oriented exit semantics:

| Exit | Meaning |
| --- | --- |
| `0` | Complete run with at least one executed check and all executed checks passing |
| `1` | An error-severity check failed or errored |
| `2` | Warning, partial coverage, unsupported/not-run checks, or nothing evaluated |
| `3` | The run could not be prepared or completed because of configuration, connection, or artifact failure |

Exit `2` deliberately distinguishes incomplete or warning-level outcomes from both success and a
hard failure.

## Drift baselines

Drift checks support `node_count`, `relationship_count`, and `property_coverage`. The CLI resolves
baseline JSON from `<artifacts>/baselines/`, which is `.graphcheck/baselines/` by default:

- `baseline: release-2026-07` resolves `release-2026-07.json`.
- `baseline: latest` selects the lexicographically newest `.json` filename.

The current CLI consumes compatible baseline/profile JSON but does not capture baselines itself. A
missing, invalid, or incomplete requested measurement is an explicit check error, never a pass. See
[SPEC-04](docs/specs/SPEC-04%20Engine.md#baselines) for the accepted baseline shapes and resolution
rules.

## Reliability and safety

- Neo4j execution is read-only and fails closed unless the planner classifies a statement as read.
- Built-in Cypher keeps labels, relationship types, property names, regexes, thresholds, and values
  in parameters rather than interpolating user data into query text.
- One broken query or evaluator error is isolated to its check unless fail-fast or the run deadline
  stops later work.
- Fail and warning verdicts require graph-element or aggregate-scope evidence.
- Sampled checks use deterministic per-check selection and report a 95% Wilson confidence interval;
  exhaustive runs are labeled exact.
- Missing labels, relationship types, or explicitly selected properties are errors rather than
  empty passes.
- Result JSON is validated against its Pydantic contract and JSON Schema before it is written.

## Configuration reference

`graphcheck.yml` has three strict fields:

```yaml
project: graphcheck
checks: checks
artifacts: .graphcheck
```

Relative `checks` and `artifacts` paths are resolved from the project root. Suite discovery is
recursive and includes `.yml` and `.yaml` files.

The complete frozen contracts and generated schemas live in [`docs/specs`](docs/specs/):

- [SPEC-01 — results.json](docs/specs/SPEC-01-results-json.md)
- [SPEC-02 — check YAML](docs/specs/SPEC-02-check-yaml.md)
- [SPEC-03 — Neo4j connector](docs/specs/SPEC-03-connector.md)
- [SPEC-04 — engine and CLI](docs/specs/SPEC-04%20Engine.md)
- [SPEC-09 — built-in packs](docs/specs/SPEC-09-packs.md)

## Development

Install the locked development environment and run the same checks as CI:

```console
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=graphcheck --cov-report=term-missing --cov-fail-under=80
```

Real-Neo4j integration tests are opt-in with `GRAPHCHECK_NEO4J_INTEGRATION=1`; the testcontainers
suite covers the connector and engine against supported Neo4j versions. The customer-scale
performance test is separately opt-in and requires a preloaded graph of at least 10 million nodes.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the branch workflow, definition of done, and decision
rights.

## License

Apache-2.0. See [LICENSE](LICENSE).
