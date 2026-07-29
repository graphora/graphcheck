# PR 09 — Remove duplicate runtime JSON Schema validation

- Category: CLI responsiveness and dependency bloat
- Roadmap source: Step 5, schema-validation phase
- Prerequisites: PR 01
- Suggested PR title: `perf: keep JSON Schema validation in development only`

## Goal

Validate results once through the canonical Pydantic contracts during normal artifact writing while
retaining JSON Schema generation and compatibility tests for development and external consumers.

## Problem

The writer currently rebuilds a validated `Results` model, converts it to JSON-compatible values,
generates a schema from the same model, and validates that payload with `jsonschema`. The second pass
adds runtime latency and production dependencies without independently enforcing the semantic
invariants already checked by Pydantic.

## Scope

- Remove `jsonschema.validate()` from normal results serialization.
- Split schema generation from optional schema-instance validation.
- Move `jsonschema` to development dependencies or an explicit optional schema extra.
- Preserve committed schema drift and fixture compatibility tests.

## Non-goals

- Removing committed JSON Schemas.
- Removing Pydantic validation.
- Changing result JSON bytes or field semantics.
- Redacting result artifacts.

## Files expected to change

- `src/graphcheck/reporting/writer.py`
- `src/graphcheck/contracts/schemas.py`
- `pyproject.toml`
- lockfile
- contract/reporting tests
- packaging smoke tests

## Implementation

1. Add a regression fixture asserting current `results_json()` output bytes.
2. Remove the runtime `jsonschema.validate(payload, results_schema())` call.
3. Keep `load_results()` as the canonical semantic validation boundary.
4. Refactor `contracts/schemas.py` so schema generation imports no `jsonschema` classes.
5. Move optional instance validators into development/test support or lazily import an optional
   dependency with a clear error.
6. Move `jsonschema` from `[project].dependencies` to the development group unless a public optional
   extra is required.
7. Regenerate the lock and verify the built wheel's dependency metadata.
8. Benchmark artifact writing before and after using the same complete result fixture.

## Tests

Run:

```console
uv run pytest tests/contracts tests/test_reporting.py -q
```

Required assertions:

- invalid semantic results still fail through Pydantic;
- committed schemas equal generated schemas;
- JSON Schema fixtures still validate in the dev test environment;
- serialized output is byte-for-byte unchanged;
- a wheel installed without dev dependencies can write JSON and HTML;
- production metadata no longer requires `jsonschema`.

## Acceptance criteria

- Normal artifact writing performs one semantic model validation.
- JSON Schema remains generated, committed, and tested.
- `jsonschema`, `referencing`, and `rpds` are not required solely by GraphCheck's production
  dependency set.
- Artifact-writing benchmark improves without output changes.

## Rollback

Re-add the writer validation call and production dependency without touching committed schemas.

## PR checklist

- [ ] Wheel dependency metadata was inspected.
- [ ] Artifact bytes are unchanged.
- [ ] Schema drift tests still fail on model changes.
- [ ] Before/after serialization timing is attached.
