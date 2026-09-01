<div align="center">

# GraphCheck

[![Release](https://img.shields.io/badge/release-v0.1.0-5b5bd6)](https://github.com/graphora/graphcheck/releases)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-3776ab)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-2ea44f)](LICENSE)

**Semantic observability for property graphs.**

</div>

<p align="center">
  <img src="docs/graphcheck-demo.gif" width="900" alt="GraphCheck command-line demo">
</p>

<p align="center">
  See a real report: <a href="https://graphora.github.io/graphcheck/docs/samples/report-findings.html">findings</a> and
  <a href="https://graphora.github.io/graphcheck/docs/samples/report-clean.html">clean run</a>
</p>

## The problem

Property graphs can stay queryable while silently losing required properties, cardinalities,
relationship direction, or the answers an application depends on. Those failures are difficult to
review consistently because schema expectations, business questions, and drift thresholds usually
live in separate tools and ad hoc queries. GraphCheck turns those expectations into version-controlled
YAML and produces deterministic, evidence-backed JSON and offline HTML reports for local runs and CI.

## Quickstart

For complete setup, credential, authoring, redaction, baseline, and configuration instructions, see
the **[full user guide](docs/user-guide.md)**.

GraphCheck requires Python 3.12, 3.13, or 3.14 and a supported Neo4j server. See the
[compatibility matrix](docs/compatibility.md) for the tested Neo4j and Cypher versions.

Install the published CLI and scaffold a project:

```console
pip install graphcheck

mkdir graph-health
cd graph-health
graphcheck init
```

Install either optional add-on separately if you need its commands:

```console
pip install "graphcheck[generate]"
pip install "graphcheck[mcp]"
```

The `generate` add-on enables AI-assisted check authoring, while `mcp` enables the MCP server.

`graphcheck init` creates the following local project:

```text
graphcheck.yml          Project paths and runtime settings
profiles.yml            Neo4j connection profiles; ignored by Git
checks/example.yml      Example check suite
.graphcheck/            Baselines, run history, JSON, and HTML reports
```

New projects run up to two checks concurrently by default. Set `concurrency` in `graphcheck.yml`
or pass `graphcheck run --concurrency N` to choose a different positive worker limit.

Edit `profiles.yml` with the URI, database, and credential for your Neo4j instance. Enterprise and
Developer deployments must use a user assigned only Neo4j's built-in `reader` role plus the
automatic `PUBLIC` role; GraphCheck rejects missing, additional, or custom roles. Community
deployments cannot enforce read-only roles, so GraphCheck instead rejects every customer-authored
query unless Neo4j plans it as read-only.

Verify the connection, replace the generated suite with the baseline-free example below, and run it:

```console
graphcheck debug
graphcheck run
```

Each prepared run writes immutable history plus convenient `latest` copies:

```text
.graphcheck/runs/<run-id>/results.json
.graphcheck/runs/<run-id>/summary.json
.graphcheck/runs/<run-id>/report.html
.graphcheck/runs/latest/results.json
.graphcheck/runs/latest/summary.json
.graphcheck/runs/latest/report.html
```

The HTML report is self-contained and works offline. Exit codes are stable for CI: `0` means all
executed checks passed, `1` means an error-severity finding or execution error, `2` means a warning or
incomplete evaluation, and `3` means the run could not be prepared or completed.
See the **[CI/CD guide](docs/ci-cd.md)** for copy-paste pull-request, scheduled, staging, and
production workflows using the published GitHub Action.

For development, use the locked environment instead:

```console
uv sync --group dev
uv run graphcheck --version
```

## Example

Replace `checks/example.yml` with a suite that matches labels in your graph:

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

Then select it directly or by tag:

```console
graphcheck run --suite customer-health
graphcheck run --select tag:production
```

A suite can combine three kinds of checks:

- **Conformance** applies reusable rules for completeness, uniqueness, types, formats, cardinality,
  relationship direction, temporal sanity, outliers, and sampled PII signals.
- **Competency** runs authored read-only Cypher and asserts its rows, columns, uniqueness, emptiness,
  or returned values.
- **Drift** compares node counts, relationship counts, or property coverage with a stored baseline.

Suite YAML is strict: duplicate keys, unknown fields, invalid payloads, and inconsistent expectations
fail before execution. Runtime evaluation is rule-based; generated check suggestions remain inert
until a person reviews and activates them.

For the full contracts, see the [check YAML specification](docs/specs/SPEC-02-check-yaml.md),
[`results.json` specification](docs/specs/SPEC-01-results-json.md), and
[engine and CLI specification](docs/specs/SPEC-04%20Engine.md). The
[agent guide](docs/agents.md), [telemetry disclosure](docs/telemetry.md),
and [contributor guide](CONTRIBUTING.md) cover integration and operational workflows.

## Non-goals

- GraphCheck does not mutate, repair, or migrate the graph.
- GraphCheck does not decide which business rules are sufficient for a domain or claim complete
  schema coverage.
- GraphCheck does not use an LLM as the judge for check results; execution and evaluation are
  deterministic.
- Sampled PII checks are heuristics, not a guarantee of complete PII discovery.
- GraphCheck's read-query guard is defense in depth, not a replacement for Neo4j authorization where
  server-enforced read-only roles are available.

## License

Apache-2.0. See [LICENSE](LICENSE).
