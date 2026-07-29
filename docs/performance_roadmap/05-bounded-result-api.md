# PR 05 — Add a bounded Neo4j result API

- Category: memory safety and execution performance
- Roadmap source: Step 3, connector phase
- Prerequisites: PR 01
- Suggested PR title: `feat: add bounded Neo4j result consumption with explicit completeness`

## Goal

Allow engine callers to stop retaining rows after an explicit limit while accurately representing
whether the Neo4j result was completely consumed.

## Problem

`Neo4jClient.run_read_result()` currently materializes every record into a list. A large competency
query can therefore grow Python memory in proportion to its full result before the evaluator can
apply evidence caps or expectation logic.

## Scope

- A bounded result-consumption policy and result shape.
- Explicit complete/truncated metadata.
- Correct session cleanup on early stop.
- A configurable result-row safety ceiling.
- Compatibility retention for the existing eager API.

## Non-goals

- Rewriting competency evaluation.
- Changing query text by automatically wrapping arbitrary Cypher.
- Silently truncating an exact assertion.
- Skipping read classification.

## Files expected to change

- `src/graphcheck/neo4j_adapter.py`
- `src/graphcheck/engine/executor.py`
- connector/executor unit tests
- connector integration tests
- SPEC-03 if the public connector boundary changes

## Proposed internal model

The bounded result needs at least:

```python
rows: list[dict[str, object]]
columns: tuple[str, ...]
complete: bool
observed_rows: int
limit: int | None
notifications: tuple[dict[str, object], ...]
server_available_after_ms: int | None
server_consumed_after_ms: int | None
read_guard_ms: int | None
```

`observed_rows` is not an exact total when `complete` is false. No public measurement may present it
as exact.

## Resource behavior

The result iterator must stay inside the session context. On early completion:

- stop retaining rows immediately;
- cancel/reset/close the result without draining all remaining records;
- close the session deterministically;
- allow consumed timing or notifications to be absent if obtaining them requires draining;
- preserve the original GraphCheck deadline.

Verify actual driver behavior with a live server; do not infer it solely from a mock.

## Implementation

1. Add a lazy fake result that can detect over-consumption.
2. Introduce a result policy with `max_rows` and `require_complete`.
3. Implement bounded iteration behind a new API while preserving `run_read_result()`.
4. Represent completeness and observed count explicitly.
5. Add `engine.result_limit_exceeded` for callers requiring completeness beyond the safety ceiling.
6. Map timeout, cancellation, session-close, and driver errors through the existing taxonomy.
7. Add live tests proving early stop does not fetch/drain the full server result.
8. Document a configurable ceiling without changing the default until evaluator integration lands.

## Tests

Run:

```console
uv run pytest tests/test_neo4j_adapter.py tests/engine/test_executor.py -q
```

Integration cases:

- empty result;
- result below limit;
- result exactly at limit;
- result exceeding limit;
- timeout during iteration;
- early stop followed by another query on the same client;
- notifications available on complete results and safely absent on truncated results.

## Acceptance criteria

- Retained rows never exceed the declared bound.
- Truncated results cannot masquerade as complete.
- Early termination does not drain the complete result.
- Sessions and results close under all outcomes.
- The existing eager API remains compatible for unmigrated callers.
- Read classification remains mandatory.

## Rollback

No caller should be forced onto the bounded API in this PR. Revert the new API without changing
existing eager behavior.

## PR checklist

- [ ] Fake-result over-consumption test passes.
- [ ] Real-driver cancellation behavior is documented.
- [ ] New error uses `{code, message, fix}`.
- [ ] No contract reports an inexact count as exact.
