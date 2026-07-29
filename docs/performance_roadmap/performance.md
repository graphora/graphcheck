# Performance measurements

GraphCheck records performance baselines before enforcing regression budgets. These measurements
are diagnostic artifacts, not universal limits: compare records from the same machine, runtime,
driver, server, Cypher generation, graph, and concurrency.

## Local measurements

Run the measurement helpers and cold CLI baselines with:

```console
uv run pytest tests/performance -q
```

The CLI benchmark launches a fresh Python process for every sample. Each command has one discarded
fresh-process warm-up followed by ten measured fresh processes. It covers `graphcheck --version`,
`--help`, `telemetry status`, and invalid-command handling.

The 10-million-node benchmark remains opt-in:

```powershell
$env:GRAPHCHECK_PERFORMANCE_URI = "bolt://localhost:7687"
$env:GRAPHCHECK_PERFORMANCE_PASSWORD = "<password>"
$env:GRAPHCHECK_PERFORMANCE_OUTPUT = "C:\temp\graphcheck-10m.json"
uv run pytest tests/performance/test_engine_budget.py -q
```

It verifies the 30-check run is correct and complete, then reports overall wall time,
per-check-family time, per-query client wall time, server-reported available/consumed timings, and
configured concurrency. A slower timing alone does not fail the test.

Capture query plans against every supported testcontainer image with:

```powershell
$env:GRAPHCHECK_NEO4J_INTEGRATION = "1"
uv run pytest tests/integration/test_performance_plans.py -v
```

Plans cover label counts, relationship counts, completeness, uniqueness, hub sampling, and PII
sampling. The recursive extractor retains operator names and selected planner arguments while
tolerating both mapping- and object-shaped driver plan trees.

## Record format

JSON records include the benchmark name, Git commit, operating system, architecture, Python and
Neo4j driver versions, server and Cypher versions when applicable, sample count, median, p95, and
maximum milliseconds. Extra benchmark-specific details document warm-ups, concurrency, query
timings, or plan operators. Records are written only to pytest temporary directories or the
explicit `GRAPHCHECK_PERFORMANCE_OUTPUT` artifact path; developer-machine results are not committed
as cross-platform thresholds.
