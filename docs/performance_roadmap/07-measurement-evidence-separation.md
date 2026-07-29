# PR 07 — Separate measurement and evidence queries

- Category: Neo4j query performance
- Roadmap source: Step 4, scan-reduction phase
- Prerequisites: PR 04
- Suggested PR title: `perf: execute bounded evidence queries only for failing checks`

## Goal

Avoid running evidence scans for passing checks and remove unbounded server-side evidence
collections, beginning with uniqueness and the shared conformance helpers.

## Problem

Several built-ins perform population/violation and evidence subqueries in every execution.
Uniqueness performs three passes and collects every node in duplicate groups before applying the
evidence limit.

## Scope

- A compiled representation with a measurement query and optional evidence query.
- Evidence execution only when the exact measurement indicates a finding.
- Uniqueness without unbounded `collect(n)`.
- Migration of shared node, relationship, and degree helpers where beneficial.

## Non-goals

- Streaming arbitrary competency results.
- Changing sampling probability.
- Profiler quick/full modes.
- Running evidence and measurement in different graph snapshots without acknowledging it.

## Files expected to change

- `src/graphcheck/engine/compiler.py`
- `src/graphcheck/engine/core_pack.py`
- `src/graphcheck/engine/executor.py`
- `src/graphcheck/engine/runner.py`
- `src/graphcheck/engine/evaluator.py`
- core pack and integration tests
- SPEC-04/SPEC-09 execution semantics

## Snapshot decision

Two separate auto-commit queries can observe different graph states. Prefer to execute measurement and conditional evidence within one read transaction when the connector can retain deadline, read classification, and isolation
semantics safely. Do not silently introduce cross-snapshot evidence.

## Compiled shape

A check may carry:

- measurement query and parameters;
- optional evidence query and parameters;
- the condition that requires evidence;
- evidence cap;
- schema metadata shared by both queries.

The evaluator still owns verdict semantics; the executor must not infer pass/fail beyond deciding
whether evidence is required from a typed aggregate result.

## Uniqueness approach

1. Compute exact population and violation count from grouped frequencies.
2. If violations exist, select a bounded number of duplicate values.
3. Fetch at most `evidence_cap` nodes matching those values.
4. Use native label/property tokens so a supporting index can help.
5. Never collect all duplicate nodes into one list.

Benchmark high-cardinality mostly-unique data and low-cardinality heavily-duplicated data.

## Tests

Run:

```console
uv run pytest tests/engine/test_core_pack_compiler.py tests/engine/test_runner.py -q
```

Required assertions:

- passing checks execute measurement only;
- failing checks execute one bounded evidence path;
- violation count remains exact;
- evidence remains deterministic and capped;
- timeout budget is shared across both phases;
- query failure in evidence produces an error, not a finding without evidence;
- no migrated query contains unbounded evidence collection.

## Acceptance criteria

- Passing migrated checks avoid their evidence scan.
- Uniqueness never materializes every duplicate node in a server-side list.
- Measurement and evidence snapshot semantics are explicit and tested.
- Exact violation counts and current evidence contracts are preserved.
- Before/after database hits and elapsed times are attached.

## Rollback

Migrate one check family per commit. A family can return to its single-query implementation without
reverting the compiled representation used by other families.

## PR checklist

- [ ] Snapshot semantics are documented.
- [ ] Evidence query is bounded in Cypher, not only in Python.
- [ ] Passing checks prove evidence was not queried.
- [ ] Deadline propagation covers both phases.
