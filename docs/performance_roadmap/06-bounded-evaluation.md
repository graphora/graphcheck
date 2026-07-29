# PR 06 — Bound competency evaluation and evidence collection

- Category: memory safety and execution performance
- Roadmap source: Step 3, evaluator phase
- Prerequisites: PR 05
- Suggested PR title: `perf: evaluate competency results incrementally and bound evidence`

## Goal

Move competency execution onto the bounded result API and avoid unconditional duplicate,
regression, and evidence processing.

## Scope

- Expectation-specific row-consumption policies.
- Conditional uniqueness and regression computation.
- Bounded, deduplicating evidence collection.
- Explicit failure when a full-result assertion exceeds the safety ceiling.
- Preservation of current result semantics for normal-size complete results.

## Non-goals

- Changing competency YAML syntax.
- Treating an incomplete result as a pass.
- Removing the requirement for graph evidence pointers.
- Optimizing built-in conformance query scans.

## Files expected to change

- `src/graphcheck/engine/evaluator.py`
- `src/graphcheck/engine/executor.py`
- `src/graphcheck/engine/runner.py` only to pass policy/completeness metadata
- evaluator property/unit tests
- engine integration tests
- SPEC-04 completeness semantics

## Consumption policy

| Expectation | Early decision |
| --- | --- |
| `empty: true` | fail after the first row |
| `empty: false` | pass after the first row if nothing else needs completeness |
| `rows.max: N` | fail after `N + 1` |
| `rows.exactly: N` | fail above `N`; success requires exhaustion |
| `rows.min: N` | may pass after `N` if no other assertion needs completeness |
| `unique: true` | fail after the first duplicate; success requires exhaustion |
| `contains` | may pass after every target is found if no other assertion needs completeness |
| `equals` | always requires complete bounded consumption |

If multiple expectations are present, use the strictest combined policy.

## Evaluator changes

1. Derive a result policy from the loaded expectation.
2. Compute frozen-row hashes only when uniqueness or bag equality requires them.
3. Compute regression projections only for `contains` or `equals`.
4. Feed rows into an evidence collector that deduplicates pointers while retaining no more than the
   configured cap.
5. Track total pointer count without retaining every pointer.
6. Preserve `engine.evidence_missing` when a failing row-level check returns no pointer.
7. Return `engine.result_limit_exceeded` when a complete assertion reaches the safety ceiling.
8. Never put an observed partial row count into `measured.rows` as an exact value.

## Tests

Run:

```console
uv run pytest tests/engine/test_evaluator.py tests/property/test_evaluator_properties.py tests/engine/test_runner.py -q
```

Required cases:

- every row-bound combination;
- multiple simultaneous expectations;
- duplicate detected early;
- unique success after exhaustion;
- contains success early and missing-value failure after exhaustion;
- equals with duplicates as a bag;
- evidence cap, deduplication, and total count;
- no evidence pointer;
- safety-ceiling error;
- timeout before and after an assertion becomes decisive.

Add an allocation/retention test with a result much larger than the configured cap.

## Acceptance criteria

- Decisive expectations stop at the minimum safe row.
- Full-result assertions either consume a complete bounded result or error loudly.
- Duplicate/regression work occurs only when requested.
- Evidence storage is bounded during collection, not after collection.
- Existing normal-size fixtures produce the same verdicts and measurements.
- Property tests cover combined expectation semantics.

## Rollback

Keep a temporary configuration switch for eager competency evaluation during rollout. Remove the
switch after live-driver and high-cardinality tests have been stable for one release.

## PR checklist

- [ ] Incomplete results cannot pass.
- [ ] `measured.rows` remains exact.
- [ ] Evidence memory is cap-bounded.
- [ ] Existing frozen result fixtures remain semantically equivalent.
