# PR 15 — Enforce performance regression gates

- Category: performance verification
- Roadmap source: Step 10, enforcement phase
- Prerequisites: PR 02 and the optimization PRs whose metrics will be gated
- Suggested PR title: `ci: enforce CLI, plan, memory, and large-graph regression budgets`

## Goal

Convert stable measurement-only benchmarks into targeted regression gates after their variance and
reference environments are understood.

## Scope

- Cold CLI relative/absolute budgets in named environments.
- Query-plan anti-regression assertions.
- Bounded client-memory/retention gates.
- Per-check-family large-graph timing budgets.
- Machine-readable failure diagnostics.

## Non-goals

- Enforcing a developer laptop's timings on every platform.
- Failing CI from one noisy sample.
- Adding profiler quick/full modes.
- Replacing correctness assertions with speed assertions.

## Gate A: CLI cold start

Use fresh processes and report median/p95. Prefer:

- platform-specific reference jobs; or
- a relative regression percentage against a baseline produced in the same environment.

Start with a generous threshold such as a 20% median regression limit, then tighten only after CI
variance is measured. Retain raw sample output on failure.

Commands:

- `graphcheck --version`;
- `graphcheck --help`;
- `graphcheck telemetry status`;
- an invalid command that exits during parsing.

## Gate B: query plans

For statically scoped built-ins:

- reject `AllNodesScan` when a native label is supplied;
- reject generic relationship scanning when a native type and supported typed operator exist;
- require count-store plans for eligible count queries;
- require index-family plans for selected indexed fixtures;
- include query text, operator tree, server version, and Cypher version in failure output.

Avoid exact whole-plan snapshots because plan layout changes across server releases.

## Gate C: client memory and retention

With a large lazy result:

- retained rows never exceed the result policy;
- evidence elements never exceed the evidence cap;
- decisive assertions do not over-consume;
- full-result assertions stop at the safety ceiling;
- peak allocation remains within a version-tolerant bound.

Use both logical retention assertions and `tracemalloc`/process-level measurements. Logical bounds
should be hard gates; byte thresholds should tolerate Python-version variance.

## Gate D: customer-scale execution

Strengthen the existing 10-million-node benchmark:

- report per-check-family p50/p95;
- include selective and scan-heavy queries;
- avoid relying mainly on identical query text;
- test concurrency 1, 2, and 4 where relevant;
- separate read-guard, server, and client times;
- validate exact result correctness;
- attach plan summaries for representative checks.

Keep the full-size test opt-in. Add a smaller stable graph to regular CI for plan and retention
regressions.

## Files expected to change

- `tests/performance/`
- `tests/integration/`
- `.github/workflows/ci.yml`
- benchmark schema/baseline data
- contributor documentation

## Implementation

1. Review at least several runs from PR 02 on each reference job.
2. Exclude metrics whose variance is not yet understood.
3. Add plan assertions first because they are less timing-sensitive.
4. Add logical memory/retention gates.
5. Add CLI relative budgets with raw diagnostic output.
6. Add per-family large-graph budgets rather than one five-minute total only.
7. Run new gates in report-only mode for an initial observation period.
8. Promote stable metrics to required CI checks.
9. Define an emergency process for temporarily relaxing a flaky threshold without deleting the
   measurement.

## Test commands

Normal gate:

```console
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=graphcheck --cov-report=term-missing --cov-fail-under=80
```

Integration:

```powershell
$env:GRAPHCHECK_NEO4J_INTEGRATION = "1"
uv run pytest tests/integration -v
```

Customer scale:

```powershell
$env:GRAPHCHECK_PERFORMANCE_URI = "<uri>"
$env:GRAPHCHECK_PERFORMANCE_PASSWORD = "<secret>"
uv run pytest tests/performance -v -m performance
```

## Acceptance criteria

- Every hard threshold has repeatability evidence.
- Plan gates protect native-token optimizations.
- Logical memory bounds are enforced independently of timing.
- CLI failures show samples, median, p95, and reference metadata.
- Large-graph failures identify the regressing check family.
- Correctness remains asserted in every performance scenario.

## Rollback

If a threshold is flaky, return that metric to report-only mode while keeping data collection.
Remove a measurement only if it is invalid, not merely because it reports a regression.

## PR checklist

- [ ] Report-only observation data is linked.
- [ ] Each threshold names its reference environment.
- [ ] Failure output is actionable.
- [ ] Full-size benchmark remains opt-in.
- [ ] Correctness assertions remain mandatory.
