# PR 02 — Establish performance measurement foundations

- Category: performance verification
- Roadmap source: Step 10, measurement-only phase
- Prerequisites: none
- Suggested PR title: `test: add repeatable CLI, plan, memory, and query timing baselines`

## Goal

Create repeatable measurements before changing query generation, result consumption, imports, or
connector round trips. This PR reports baselines; it does not enforce hard thresholds.

## Scope

- Cold CLI subprocess timing.
- Query-plan extraction helpers for live Neo4j tests.
- Client allocation/retention measurement helpers.
- Per-query and per-check timing in the existing customer-scale benchmark.
- Machine-readable benchmark records with environment metadata.

## Non-goals

- Optimizing production code.
- Failing CI on performance thresholds.
- Adding profiler quick/full modes.
- Claiming cross-machine absolute timings are directly comparable.

## Files expected to change

- `tests/performance/`
- `tests/integration/`
- optional `tests/performance/helpers.py`
- `.github/workflows/ci.yml` only for a reporting job that cannot fail on timing
- project documentation for running benchmarks

## Measurement record

Every record should include:

```json
{
  "benchmark": "cli-version-cold",
  "commit": "<sha>",
  "os": "<name-version>",
  "architecture": "<arch>",
  "python": "<version>",
  "driver": "<version-or-null>",
  "server": "<version-or-null>",
  "cypher": "<version-or-null>",
  "samples": 10,
  "median_ms": 0,
  "p95_ms": 0,
  "maximum_ms": 0
}
```

Do not commit a developer-machine timing as a universal limit.

## Implementation

1. Add a subprocess benchmark for `graphcheck --version`, `--help`, `telemetry status`, and an
   invalid command.
2. Use fresh processes, a warm-up policy documented in the output, and at least ten measured runs.
3. Add a recursive plan walker that returns operator names and selected arguments without assuming
   one exact plan serialization.
4. Capture representative pre-optimization plans for label counts, relationship counts,
   completeness, uniqueness, hub sampling, and PII sampling.
5. Add a lazy high-cardinality fake result for later bounded-memory tests.
6. Record server-reported available/consumed timings separately from client wall time.
7. Extend the 10-million-node benchmark output with per-check-family timings and concurrency.
8. Emit JSON records to a temporary or CI artifact path; do not write into source directories.

## Tests

Run:

```console
uv run pytest tests/performance -q
```

When a live integration target is available:

```powershell
$env:GRAPHCHECK_NEO4J_INTEGRATION = "1"
uv run pytest tests/integration -v
```

The measurement tests should validate their output schema and units but should not fail merely
because one run is slower.

## Acceptance criteria

- Cold-start results report median and p95 from fresh processes.
- Plan extraction works across every currently tested server image.
- Timing records distinguish client wall time from server timing.
- Benchmark output records Python driver, server, and Cypher versions separately.
- No production behavior changes.

## Rollback

The PR contains tests/helpers only. Remove a noisy measurement without reverting other benchmarks.

## PR checklist

- [ ] Baseline JSON is schema-validated.
- [ ] No absolute cross-platform gate was introduced.
- [ ] Plan helper tolerates version-specific plan layout.
- [ ] Existing 10M test still verifies correctness as well as time.
