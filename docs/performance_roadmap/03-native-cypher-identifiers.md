# PR 03 — Add a native Cypher identifier foundation

- Category: Neo4j query performance and safety
- Roadmap source: Step 2, foundation phase
- Prerequisites: PR 02 is preferred for before/after plans
- Suggested PR title: `perf: compile validated schema identifiers into native Cypher tokens`

## Goal

Introduce one safe, tested way to place validated labels, relationship types, and property names in
Cypher grammar, then migrate a small set of simple count/completeness queries.

## Problem

Patterns such as `MATCH (n) WHERE $label IN labels(n)` obscure the label from Neo4j's planner.
Parameters cannot occupy label/type/property grammar positions, so statically known schema tokens
must be escaped into the query text to enable specialized plans.

## Scope

- Shared identifier validation and escaping.
- Native label/type/property query fragments.
- Migration of simple node counts, relationship counts, and completeness queries.
- Unit and live-plan assertions.

## Non-goals

- Migrating every pack query.
- Changing arbitrary competency Cypher.
- Interpolating data values.
- Refactoring pack registration.

## Files expected to change

- a shared engine query/identifier utility
- `src/graphcheck/engine/compiler.py`
- compiler and integration tests
- SPEC-04 only if generated-query guarantees need clarification

## Safety contract

Only identifiers already validated as check configuration may be escaped. Data values remain
parameters.

The escaping operation is:

```text
"`" + identifier.replace("`", "``") + "`"
```

Reject empty strings and disallowed control characters. Do not reject spaces, Unicode, punctuation,
or reserved words solely because they require escaping.

## Implementation

1. Add table-driven tests for ordinary, spaced, Unicode, reserved-word, and backtick-containing
   identifiers.
2. Add injection-shaped cases proving an input remains one escaped identifier.
3. Introduce typed helpers for node patterns, relationship patterns, and property access.
4. Migrate simple drift/count queries with statically known labels/types.
5. Migrate completeness queries with known labels/properties.
6. Remove only parameters that are no longer referenced by migrated query text.
7. Preserve separate schema metadata used for diagnostics and missing-schema checks.
8. Capture `EXPLAIN` plans before and after on the supported integration targets.

## Tests

Run:

```console
uv run pytest tests/engine/test_compiler.py tests/engine/test_runner.py -q
```

Live tests must assert:

- label-specific count avoids `AllNodesScan`;
- eligible counts use a count-store operator where supported;
- property predicates can use a label/property index family operator where an index exists;
- result values and evidence semantics are unchanged.

## Acceptance criteria

- There is one shared identifier escape implementation.
- No migrated query uses a runtime label/type predicate when the token is compile-time known.
- No data literal is interpolated into generated Cypher.
- Injection-shaped identifiers remain a single token.
- Migrated query plans improve or remain intentionally version-compatible.

## Rollback

Revert the small migrated query set while retaining the well-tested escape utility if it has no
behavioral side effects.

## PR checklist

- [ ] Identifier tests include backticks and Unicode.
- [ ] Plan evidence is attached to the PR.
- [ ] Query parameters contain no obsolete schema-token entries.
- [ ] Frozen specs remain accurate.
