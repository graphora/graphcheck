# GraphCheck user guide

This guide preserves the detailed operational documentation for GraphCheck. For the project
overview and shortest path to a first run, start with the [README](../README.md).

GraphCheck is semantic observability for property graphs: define what should be true in YAML,
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
- Neo4j Python driver 5.20 through 6.x
- Neo4j Server 5.26 LTS or a tested calendar-version release
- Cypher 5, or Cypher 25 on the tested calendar-version server
- [`uv`](https://docs.astral.sh/uv/) only when using the development workflow shown below

Neo4j Server 4.4 is legacy and unsupported. The exact tested combinations and the temporary
Cypher 5 sampling path are documented in the [compatibility matrix](compatibility.md).

APOC is probed and reported by `graphcheck init` and `graphcheck debug`. A missing optional
capability blocks only checks that declare it; those checks are recorded as unsupported instead of
silently passing.

## Install

Install the core GraphCheck CLI from PyPI:

```console
pip install graphcheck
graphcheck --version
```

The core install provides project initialization, connection diagnostics, profiling, deterministic
check execution, and report generation. AI-assisted authoring and the MCP server are separate
optional add-ons; neither is required for the core CLI, and installing one does not install the
other.

Install the `generate` add-on to use `graphcheck generate` and its supported model providers:

```console
pip install "graphcheck[generate]"
```

Install the `mcp` add-on to expose GraphCheck through `graphcheck mcp serve`:

```console
pip install "graphcheck[mcp]"
```

See [Generate check suggestions](#generate-check-suggestions) for the authoring workflow and the
[agent guide](agents.md) for the MCP tools and agent workflow.

### Development install

Clone the repository and create its locked development environment:

```console
git clone https://github.com/graphora/graphcheck.git
cd graphcheck
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

First check the edition of the running DBMS:

```cypher
CALL dbms.components()
YIELD versions, edition
RETURN versions, edition;
```

The credential requirement depends on that result:

| Neo4j edition | Credential GraphCheck accepts | Protection model |
| --- | --- | --- |
| Enterprise, including Desktop Developer Edition | A user assigned only the built-in `reader` role (plus `PUBLIC`) | Neo4j RBAC plus GraphCheck's query guard |
| Community | The configured Community user, which is necessarily admin-equivalent | GraphCheck's query guard; Community cannot enforce read-only roles |

For Enterprise/Developer, an administrator assigns Neo4j's built-in `reader` role to the account
used by GraphCheck. That account must have no other assigned role except the automatic `PUBLIC`
role. GraphCheck therefore rejects `admin`, `architect`, `publisher`, `editor`, and custom roles.

Edit `profiles.yml`. This Enterprise/Developer example uses an account assigned the built-in
`reader` role:

```yaml
default: local
profiles:
  local:
    uri: bolt://localhost:7687
    user: graphcheck
    password_env: GRAPHCHECK_NEO4J_PASSWORD
    database: neo4j
```

`password_env` overrides a literal `password` when the variable is set. If the environment variable
is absent, GraphCheck falls back to `password` when one is configured. For the quickest local
setup, edit the generated inline `password` value. For CI or shared environments, remove the inline
value, keep `password_env`, and export that variable in the process that runs GraphCheck.

On Neo4j Enterprise/Developer, the account must have only the built-in `reader` role and the
automatic `PUBLIC` role. During `init`, `debug`, and the CLI run preflight, GraphCheck reads the
current user's roles with `SHOW CURRENT USER`. A missing `reader` role or any additional role is
rejected as `neo4j.credential_not_read_only`. If Enterprise cannot return the roles, GraphCheck
fails closed as `neo4j.credential_read_only_unverified`.

Neo4j Community has no roles and gives every user implied administrator privileges, so it cannot
provide a server-enforced read-only credential. GraphCheck explicitly supports Community by
skipping the unavailable Enterprise RBAC gate. In both editions, every customer-authored query is
separately planned with `EXPLAIN`; GraphCheck executes only Neo4j query type `r`, so a write-capable
query is rejected without modifying the graph. Driver read routing alone is not an authorization
boundary.

In Desktop 2, open the instance's **Query** tool as the existing `neo4j` administrator. On Neo4j
Enterprise/Developer, grant the built-in role to the user configured in `profiles.yml`:

```cypher
GRANT ROLE reader TO graphcheck;
```

Revoke any `admin`, `architect`, `publisher`, `editor`, or custom role from that user; `reader` and
the automatic `PUBLIC` role must be its complete role set. GraphCheck no longer requires custom
roles or individually granted privileges.

Set the matching password in the same shell that starts GraphCheck:

```powershell
$env:GRAPHCHECK_NEO4J_PASSWORD = "replace-with-a-strong-password"
graphcheck debug
graphcheck run
```

For Community Edition, keep the generated `user: neo4j` (or another Community user) and configure
its password normally. Community users are admin-equivalent by design, so GraphCheck skips the
unavailable RBAC audit and relies on its planner guard described above.

Use `bolt://host:7687` for a direct non-TLS local server. Use `neo4j+s://host:7687` for routing with
CA-validated TLS (including the URI supplied by Aura), or `neo4j+ssc://host:7687` only when the
deployment intentionally uses a self-signed certificate. The URI scheme must match the server.

Verify connectivity, server metadata, visibility, graph counts, and check capability requirements:

```console
graphcheck debug
graphcheck debug --json
```

On failure, both forms print a stable error code, a plain-language diagnostic, and an exact `Fix:`;
`graphcheck run` preserves the same diagnostic in `results.json` and the HTML report.

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
graphcheck run --redact
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

See two real examples: a [findings run](https://graphora.github.io/graphcheck/docs/samples/report-findings.html) and a [clean run](https://graphora.github.io/graphcheck/docs/samples/report-clean.html).

Use `graphcheck run --redact` (`--redacted` remains an alias) when the generated artifacts will be
shared. Mask mode preserves
verdicts, scores, run-level counts, keys, and container structure while replacing query text,
parameter, expected, and measured literals; check names and provenance; partial reasons; diagnostic
messages and fixes; source hashes and target identifiers; and evidence messages/element values with
`[REDACTED]`. Suite, check, and tag identifiers receive consistent ordered aliases so their
relationships remain intact. Redacted artifacts use a target-neutral `redacted_<timestamp>` run ID.
Redaction also compares the final artifact with its collected source literals, allowing collisions
only in explicitly safe structural fields such as timestamps, versions, enums, and error codes.
Every mask-mode JSON and HTML write verifies the mask, alias, and neutral-ID policy before export.

Redacted HTML reports omit all target metadata and graph counts. Check cards show the pattern under
the check name and omit the details/evidence toggle and its Expected, Measured, and Compiled Cypher
sections.

To create a safe sidecar from an existing run:

```console
graphcheck redact .graphcheck/runs/<run-id>/results.json
graphcheck redact .graphcheck/runs/<run-id> --output export/results.json
```

Without `--output`, the command writes `results.redacted.json` beside the source and never
overwrites the original.

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

`graphcheck profile` captures timestamped baselines in that directory. A missing, invalid, or
incomplete requested measurement is an explicit check error, never a pass. See
[SPEC-04](specs/SPEC-04%20Engine.md#baselines) for the accepted baseline shapes and resolution
rules.

## Generate check suggestions

This feature requires the separately installed `generate` add-on:

```console
pip install "graphcheck[generate]"
```

`graphcheck generate` turns the latest baseline profile and optional, explicitly named domain
documents into non-deterministic check suggestions. The command discloses the destination and exact
data categories before calling the configured provider. It never sends graph records, property
values, credentials, target metadata, fingerprints, or profiler failure text.

Generation is opt-in. Add one of these strict blocks to `graphcheck.yml`:

```yaml
generate:
  provider: google
  model: gemini-2.5-flash
  api_key_env: GEMINI_API_KEY
  temperature: 0
```

```yaml
generate:
  provider: google
  model: gemma-4-26b-a4b-it
  api_key_env: GEMINI_API_KEY
  temperature: 0
```

```yaml
generate:
  provider: openai
  model: gpt-5-mini
  api_key_env: OPENAI_API_KEY
  temperature: 0
```

```yaml
generate:
  provider: ollama
  model: qwen3:8b
  api_key_env: null
  base_url: http://localhost:11434/v1
  temperature: 0
```

Google, Anthropic, and OpenAI require a populated environment variable named by `api_key_env`.
Google's [Gemini structured-output models](https://ai.google.dev/gemini-api/docs/generate-content/structured-output#model-support)
and [hosted Gemma models](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api) use the
[Gemini API](https://ai.google.dev/gemini-api/docs/pricing) subject to Google's current quotas and
data terms. Models named `gemini-*` use native structured output and the full GraphCheck proposal
contract. Other Google model names retain the conservative Gemma tool path: conformance targets
completeness/uniqueness, competency expectations target returned columns, and drift targets
labeled node counts. Gemma publishes a valid partial first batch without a slower correction, so
`written` may be below `requested`. Ollama requires an explicit `base_url` and may omit the key.
Then run:

```console
graphcheck generate
graphcheck generate --from .graphcheck/baselines/20260724T120000.000000.json \
  --docs docs/domain-rules.md --count 5
graphcheck generate --json
```

Documents must be regular UTF-8 files, are sent verbatim, and are limited to 256 KiB each and 1 MiB
in total. Generated suites carry `generated: true` at both file and check level, so the engine
validates but skips them without querying Neo4j. Review identifiers, Cypher, expectations,
thresholds, and cost before removing both applicable markers to activate a check.

## Reliability and safety

- Neo4j execution is read-only and fails closed unless the planner classifies a statement as read.
- The Enterprise connection preflight fails unless Neo4j reports only the built-in `reader` role
  and `PUBLIC`, or if GraphCheck cannot inspect the current user's roles.
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

## Optional anonymous telemetry

Telemetry is **off by default**. GraphCheck does not create a telemetry client or send events until
a user explicitly runs `graphcheck telemetry enable`; use `graphcheck telemetry disable` to turn it
off again.

When enabled and delivery is configured, GraphCheck sends only structural and aggregate signals
such as command/run occurrence, timings, execution counts, operational outcomes, and coarse
runtime information. It never sends queries, graph schema names or values, database or project
identity, credentials, check identities, check results or verdicts, command arguments, paths, or
free-form errors. Delivery is asynchronous and best-effort, so telemetry failures never change CLI
behavior. See [the telemetry disclosure](telemetry.md) for the complete event and field
inventory.

## Configuration reference

`graphcheck.yml` has three required strict fields plus optional `concurrency` and `generate`
settings:

```yaml
project: graphcheck
checks: checks
artifacts: .graphcheck
concurrency: 2
```

Relative `checks` and `artifacts` paths are resolved from the project root. Suite discovery is
recursive and includes `.yml` and `.yaml` files. Every discovered suite is read and validated
directly on each command before suite-id filtering; GraphCheck does not create a suite-discovery
cache file. `concurrency` is a positive worker limit; the default is `2`, and
`graphcheck run --concurrency N` overrides the project value.

For agent integration, result consumption, and programmatic check authoring, see the
[GraphCheck agent guide](agents.md).

The complete frozen contracts and generated schemas live in [`docs/specs`](specs/):

- [SPEC-01 — results.json](specs/SPEC-01-results-json.md)
- [SPEC-02 — check YAML](specs/SPEC-02-check-yaml.md)
- [SPEC-03 — Neo4j connector](specs/SPEC-03-connector.md)
- [SPEC-04 — engine and CLI](specs/SPEC-04%20Engine.md)
- [SPEC-09 — built-in packs](specs/SPEC-09-packs.md)

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
The repeatable performance gates and measurement helpers live under
[`tests/performance`](../tests/performance/).

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the branch workflow, definition of done, and decision
rights.

## License

Apache-2.0. See [LICENSE](../LICENSE).
