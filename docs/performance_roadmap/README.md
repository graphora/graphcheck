# GraphCheck implementation roadmap

Status: proposed
Prepared: 2026-07-29

This directory breaks the audit roadmap into independently reviewable commit/PR briefs. Each brief
contains its own purpose, boundaries, file-level implementation plan, tests, acceptance criteria,
and rollback notes.

## Version terminology

Keep these version dimensions separate:

| Component | Version line |
| --- | --- |
| Neo4j Server | `5.26` LTS and calendar versions such as `2025.x`/`2026.x` |
| Neo4j Python driver | Driver `5.x`/`6.x` |
| Cypher | Cypher 5 and Cypher 25 |

## PR sequence

| Order | PR brief | Category | Prerequisites |
| --- | --- | --- | --- |
| 1 | [Hermetic telemetry/profile tests](01-hermetic-telemetry-tests.md) | Correctness | None |
| 2 | [Performance measurement foundation](02-performance-measurement-foundation.md) | Verification | None |
| 3 | [Native Cypher identifier foundation](03-native-cypher-identifiers.md) | Neo4j performance | PR 2 preferred |
| 4 | [Native-token query migration](04-native-token-query-migration.md) | Neo4j performance | PR 3 |
| 5 | [Bounded Neo4j result API](05-bounded-result-api.md) | Memory/performance | PR 1 |
| 6 | [Bounded competency evaluation and evidence](06-bounded-evaluation.md) | Memory/performance | PR 5 |
| 7 | [Measurement/evidence query separation](07-measurement-evidence-separation.md) | Neo4j performance | PR 4 |
| 8 | [Sampling and concurrency tuning](08-sampling-and-concurrency.md) | Neo4j performance | PRs 2, 4 |
| 9 | [Remove runtime JSON Schema validation](09-remove-runtime-jsonschema.md) | CLI/dependencies | PR 1 |
| 10 | [Lazy CLI and telemetry imports](10-lazy-cli-telemetry.md) | CLI startup | PRs 1, 2 |
| 11 | [Remove the suite-manifest cache](11-remove-suite-manifest.md) | Simplification | None |
| 12 | [Bounded per-client read-guard cache](12-read-guard-cache.md) | Connector | PR 1 |
| 13 | [Reduce probe round trips](13-probe-round-trips.md) | Connector | PRs 2, 12 |
| 14 | [Driver/server/Cypher compatibility matrix](14-neo4j-compatibility.md) | Compatibility | PR 3 preferred |
| 15 | [Enforce performance regression gates](15-performance-regression-gates.md) | Verification | PRs 2–14 as applicable |

The order is recommended, not a requirement for unrelated briefs. A PR with prerequisites should
target a branch containing those prerequisites or wait until they merge.

## Rules shared by every PR

1. GraphCheck must not write to the inspected graph.
2. Arbitrary user Cypher must retain server-side read classification.
3. Values remain parameters; only validated schema identifiers may be escaped into query grammar.
4. Missing schema, timeout, partial results, and query failures must never become passes.
5. Frozen specs and committed schemas must change in the same PR as any contract change. SPEC-01 and SPEC-02 are frozen and cannot be changed.
6. Every optimization needs a before/after plan, timing, allocation, or round-trip measurement.
7. Every PR must be independently revertible.

## Standard quality gate

```console
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=graphcheck --cov-report=term-missing --cov-fail-under=80
```

Run the integration and performance commands specified by a brief in addition to this gate.

## Changelog

### 2026-07-29

- Implemented PR 01, hermetic telemetry/profile tests:
  - isolated telemetry consent and installation identity under each test's temporary directory;
  - cleared inherited process telemetry and delivery-key configuration;
  - normalized the profile injection seam so observer keywords are always supplied, using `None`
    when telemetry is inactive;
  - added enabled/disabled consent coverage, prevented real transport construction, and proved an
    external enabled consent file cannot influence profile tests;
  - stabilized setup-failure coverage so an unrelated parent project cannot affect the result;
  - verified the focused CLI/telemetry suite with 126 passing tests.
- Implemented PR 02, performance measurement foundations:
  - added schema-validated JSON benchmark records with commit, OS, architecture, Python, Neo4j
    driver, server, and Cypher version dimensions;
  - added ten-sample fresh-process baselines for CLI version, help, telemetry status, and invalid
    command handling, with an explicit discarded warm-up policy;
  - added recursive mapping/object Neo4j plan extraction and live plan capture for label counts,
    relationship counts, completeness, uniqueness, hub sampling, and PII sampling;
  - added lazy high-cardinality result, allocation-retention, and separate client/server query
    timing helpers;
  - extended the opt-in 10-million-node benchmark with concurrency, per-check-family timings, and
    per-query client/server timings without adding a cross-machine timing threshold;
  - documented benchmark commands, output locations, record interpretation, and the
    measurement-only policy in `docs/performance.md`;
  - verified the local performance suite with 6 passing tests and the live-only 10M case skipped;
    live Neo4j plan cases remain opt-in through `GRAPHCHECK_NEO4J_INTEGRATION=1`.
- Implemented PR 03, native Cypher identifier foundations:
  - added one shared identifier validator/escaper plus typed node, relationship, and property
    fragments;
  - rejected blank and control-containing identifiers while preserving spaces, Unicode, reserved
    words, punctuation, and embedded backticks as single escaped tokens;
  - migrated completeness and drift count/property-coverage queries to planner-visible schema
    tokens while retaining separate required-schema diagnostic metadata;
  - removed obsolete schema-token parameters and added injection-shaped, Unicode, reserved-word,
    backtick, query-shape, and parameter-contract coverage;
  - verified the focused identifier/compiler/runner suite with 75 passing tests.
- Implemented PR 04, built-in native-token query migration:
  - migrated core conformance labels, relationship types, and property accesses to native escaped
    tokens without interpolating regexes, allowed values, thresholds, sample controls, or IDs;
  - compiled separate typed and generic variants for optional relationship types, eliminating
    nullable runtime type predicates;
  - specialized label-scoped PII scans and configured PII property lists while retaining dynamic
    access only for graph-discovered property keys;
  - migrated the fixed graph-token resolver, preserved missing-schema behavior, and updated the
    frozen engine compilation contract;
  - aligned unknown-schema notifications with `engine.schema_reference_missing` and normalized
    Neo4j 4.4/5 provider-suffixed plan operators such as `NodeCountFromCountStore@neo4j`;
  - added native count-store/scan plan assertions to the opt-in Neo4j integration suite and
    verified the engine/pack suite with 374 passing tests.
- Implemented PR 05, bounded Neo4j result consumption:
  - added an explicit `ResultPolicy` plus complete/truncated metadata, observed-row lower bounds,
    configured limits, and optional server metadata on rich results;
  - retained the eager connector API while routing bounded callers through planner-verified reads;
  - capped retained rows, raised `engine.result_limit_exceeded` for completeness-required overflow,
    and preserved the original shared deadline and error taxonomy;
  - verified Neo4j driver 6.2 cleanup behavior and used exceptional session cleanup so early stop
    skips the driver's automatic result drain, while complete results still expose summaries;
  - added lazy over-consumption, exact-limit, overflow, cleanup, executor-routing, and opt-in live
    follow-up-query coverage;
  - verified the focused connector/executor suite with 62 passing tests; 17 live connector cases
    remain opt-in through `GRAPHCHECK_NEO4J_INTEGRATION=1`.
- Implemented PR 06, bounded competency evaluation and evidence:
  - derived one strict combined consumption policy for row bounds, emptiness, columns, uniqueness,
    `contains`, and duplicate-preserving bag `equals`;
  - stopped on the first decisive row, required exhaustion for full-result assertions, and omitted
    exact `measured.rows` from early-decision results in favor of `observed_rows_at_least`;
  - added the configurable `result_row_limit` safety ceiling and temporary
    `eager_competency_evaluation` rollback switch;
  - limited row hashing and regression projection to assertions that request them and collected
    deduplicated evidence without retaining more than the configured evidence cap;
  - added large lazy-result retention, combined-policy, early duplicate/max/min/contains,
    safety-ceiling, and unrequested-work coverage;
  - verified the focused evaluator/property/runner suite with 118 passing tests and the full
    repository gate with 909 passing, 30 opt-in skips, and 91.34% coverage.
- Implemented PR 07, measurement/evidence query separation:
  - added compiled measurement queries, typed evidence conditions, optional bounded evidence
    queries, and separate parameter contracts;
  - executed conditional evidence through one explicit planner-verified read transaction so both
    phases share a graph snapshot and the original monotonic deadline;
  - migrated completeness plus the shared node, relationship, and degree helpers so passing checks
    avoid evidence scans while failing checks execute one bounded evidence path;
  - rewrote uniqueness evidence around an indexed duplicate-existence predicate, eliminating the
    unbounded duplicate-node collection while retaining exact violation counts and deterministic
    capped pointers;
  - made evidence-query failures and malformed evidence results errored checks and added distinct
    measurement/evidence telemetry roles;
  - corrected the explicit-transaction connector boundary to pass plain Cypher strings, as required
    by the Neo4j driver, while retaining the shared transaction-level deadline;
  - verified the focused core compiler/runner suite with 89 passing tests.
- Implemented PR 08, sampling and concurrency tuning:
  - removed the hub population-preflight round trip by computing exact population and selection in
    one query snapshot;
  - added a deterministic oversampling gate ahead of hub ordering and moved the PII gate to the
    property-occurrence key so eligible occurrences have independent, auditable selection;
  - preserved exact versus estimated result metadata, seed determinism/sensitivity, and added
    many-seed distribution coverage;
  - exposed positive concurrency through `graphcheck.yml` and `graphcheck run --concurrency`, with
    documented CLI-over-project-over-engine precedence and a conservative default of one worker;
  - matched the Neo4j connection pool to effective concurrency and set explicit connection,
    acquisition, fetch-size, and retry settings;
  - made both hub-sampling UNION branches Neo4j 4.4/5 compatible by separating correlated variable
    import from the subsequent population predicate;
  - expanded the opt-in 10M benchmark to concurrency 1, 2, and 4 and verified the focused sampling,
    runner, PII, project, connector, and CLI suite with 229 passing tests;
  - verified the full repository gate with 926 passing, 32 opt-in skips, and 91.20% coverage.
- Implemented PR 09, development-only JSON Schema validation:
  - removed the duplicate `jsonschema` validation pass from normal result serialization while
    retaining Pydantic as the canonical semantic artifact boundary;
  - separated schema generation from optional instance validation by lazily importing the
    development validator only inside explicit validation helpers;
  - moved `jsonschema` and its transitive validator dependencies out of production metadata and
    added a byte-for-byte serialized result regression fixture;
  - built and installed an isolated wheel without development dependencies, inspected its
    `Requires-Dist` metadata, and verified that JSON and HTML artifacts write without importing
    `jsonschema`;
  - measured 200 complete-result serializations after one warm-up: median improved from 77.745 ms
    to 1.701 ms and p95 from 129.759 ms to 2.900 ms with identical bytes;
  - verified the focused contract/reporting suite with 192 passing tests.
- Implemented PR 10, lazy CLI and telemetry imports:
  - added a standard-library-only console bootstrap whose exact `--version` fast path imports
    neither Typer, Pydantic, Neo4j, command implementations, nor telemetry;
  - split standard-library consent storage and shared telemetry enums from Pydantic payload models,
    and added a no-op inactive runtime for disabled commands;
  - deferred event, collector, runtime, and PostHog imports until enabled consent is established,
    while retaining process-enabled, persisted-enabled, and telemetry-management behavior;
  - added fresh-process import-boundary coverage for top-level and per-command help, process and
    persisted consent, and verified the installed-wheel console entry point;
  - measured 20 cold `--version` samples after one discarded warm-up: median improved from
    311.50 ms to 72.49 ms and p95 from 457.27 ms to 86.17 ms;
  - verified the combined CLI, run, report-history, telemetry, import-boundary, and startup suite
    with 175 passing tests;
  - verified the full repository gate with 938 passing, 32 opt-in skips, and 90.95% coverage.
