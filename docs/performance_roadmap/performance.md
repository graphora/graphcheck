# Performance measurements and regression gates

GraphCheck compares timings only within a named reference environment. Logical retention and query
plan requirements are platform-independent hard gates. Timing records remain diagnostic outside a
named reference: compare only the same machine, runtime, driver, server, Cypher generation, graph,
and concurrency.

## Local measurements

Run the measurement helpers and cold CLI baselines with:

```console
uv run pytest tests/performance -q
```

The CLI benchmark launches a fresh Python process for every sample. Each command has one discarded
fresh-process warm-up followed by ten measured fresh processes. It covers `graphcheck --version`,
`--help`, `telemetry status`, and invalid-command handling. Normal local runs collect records
without applying a developer-machine threshold.

CI sets `GRAPHCHECK_PERFORMANCE_GATE=windows-amd64-python-3.12` and enforces the committed
three-run reference in `tests/performance/budgets.json`. The initial required limit is a 20%
median regression. Raw samples, median, p95, maximum, commit, OS, architecture, and Python
metadata are retained in the `performance-gates-windows-python-3.12` artifact. An initial failure
triggers one confirmation batch, and the combined samples decide the gate so one noisy batch
cannot fail CI.

Logical client-memory gates run both in the normal suite and the named performance job. They prove
that decisive competency assertions stop consuming, retained rows remain bounded, evidence never
exceeds its cap, full-result assertions stop at the safety ceiling, and peak traced allocation
stays below a version-tolerant ceiling.

The 10-million-node benchmark remains opt-in:

```powershell
$env:GRAPHCHECK_PERFORMANCE_URI = "bolt://localhost:7687"
$env:GRAPHCHECK_PERFORMANCE_PASSWORD = "<password>"
$env:GRAPHCHECK_PERFORMANCE_OUTPUT = "C:\temp\graphcheck-10m.json"
$env:GRAPHCHECK_PERFORMANCE_CYPHER = "25"
uv run pytest tests/performance/test_engine_budget.py -q
```

It runs the 30-check workload at concurrency 1, 2, and 4, verifies every run is correct and
complete, then reports overall wall time, per-family median/p95/maximum time, per-query client wall
time, server-reported available/consumed timings, representative plan summaries, and configured
concurrency. The workload includes distinct node/relationship count-store queries, several
scan-heavy property aggregates, and selective built-ins.

Customer graphs need their own observed baseline. To promote stable family metrics to required
gates, provide a budget file using the same schema as `tests/performance/budgets.json`, with one
budget named `customer-10m-concurrency-<n>-<family>` for every emitted family:

```powershell
$env:GRAPHCHECK_PERFORMANCE_BUDGETS = "C:\benchmarks\customer-10m-budgets.json"
$env:GRAPHCHECK_PERFORMANCE_GATE = "customer-reference-2026-07"
uv run pytest tests/performance/test_engine_budget.py -v -m performance
```

Each family policy names its reference environment, baseline median, allowed regression
percentage, observation count, and either `required` or `report-only` mode. A required failure
identifies the family and concurrency and includes raw timings, p50/p95, server/Cypher metadata,
query text, and its representative operator tree. Without a customer budget file the full-size
test remains measurement-only and opt-in.

Capture query plans against every supported testcontainer image with:

```powershell
$env:GRAPHCHECK_NEO4J_INTEGRATION = "1"
uv run pytest tests/integration/test_performance_plans.py -v
```

Plans cover label and relationship count stores, completeness, uniqueness, hub/PII sampling, a
native typed relationship, and an indexed lookup on a stable 1,000-node fixture. CI rejects
all-node/generic-relationship scans where native operators are expected and requires an
index-family operator for the indexed fixture. Failures include query text, the full extracted
operator tree, Neo4j Server, and Cypher versions. Exact whole-plan snapshots are intentionally not
used.

## Record format

JSON records include the benchmark name, Git commit, operating system, architecture, Python and
Neo4j driver versions, server and Cypher versions when applicable, sample count, median, p95, and
maximum milliseconds. Extra benchmark-specific details document warm-ups, concurrency, query
timings, or plan operators. Records are written only to pytest temporary directories or the
explicit `GRAPHCHECK_PERFORMANCE_OUTPUT` artifact path.

## Threshold changes and emergency fallback

Never delete a measurement merely because it regresses. Tighten or relax a threshold only in a
reviewed budget-file change backed by repeated runs from the named reference environment. If a
required threshold becomes flaky, temporarily change only that policy to `report-only`; data
collection and CI artifacts stay active while the cause is investigated. Restore `required` after
the reference is stable.
