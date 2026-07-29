# PR 04 — Migrate built-in queries to native schema tokens

- Category: Neo4j query performance
- Roadmap source: Step 2, migration phase
- Prerequisites: PR 03
- Suggested PR title: `perf: migrate core, PII, and drift queries to planner-visible schema tokens`

## Goal

Apply the shared identifier foundation to the remaining built-in queries whose labels,
relationship types, or properties are known during compilation.

## Scope

- Core conformance checks.
- Drift counts and property coverage.
- PII checks when a label/property is configured.
- The fixed graph-token resolver if it remains in the product.
- Query-shape variants for optional labels/types.
- Live plan verification across supported servers.

## Non-goals

- Modifying arbitrary competency queries.
- Scan/evidence query separation.
- New sampling algorithms.
- Changing pack registration.

## Files expected to change

- `src/graphcheck/engine/core_pack.py`
- `src/graphcheck/engine/pii_pack.py`
- `src/graphcheck/engine/compiler.py`
- `src/graphcheck/engine/parameters.py`
- pack/compiler/integration tests
- SPEC-04 and SPEC-09 generated-query descriptions where needed

## Query rules

- Configured label → `MATCH (n:\`Label\`)`.
- Configured relationship type → `-[r:\`TYPE\`]->`.
- Configured property → `n.\`property\``.
- Missing optional type → compile a generic relationship variant.
- Never retain `$type IS NULL OR type(r) = $type` in the typed variant.
- Keep data values, thresholds, IDs, evidence caps, and sample parameters parameterized.

## Implementation

1. Inventory all `labels(n)`, `type(r)`, and dynamic property predicates in compiler-owned Cypher.
2. Classify each as compile-time static or genuinely runtime dynamic.
3. Migrate one check family at a time, retaining focused result fixtures.
4. Compile distinct optional-token variants so configured cases are planner-specialized.
5. Keep schema-catalog/missing-schema behavior intact.
6. Remove obsolete schema-token query parameters without removing diagnostic metadata.
7. Recursively inspect plans for all migrated representative queries.
8. Run deterministic sampling tests because query text changes can affect cache keys and ordering.

## Tests

Run:

```console
uv run pytest tests/engine tests/test_packs.py -q
```

Required live cases:

- node conformance with label and property;
- relationship conformance with source label, type, and target label;
- label-specific drift count;
- label-scoped PII;
- optional relationship type configured and omitted;
- unusual escaped identifiers.

## Acceptance criteria

- Every statically known built-in schema token is planner-visible.
- Generic scans remain only where the token is genuinely unspecified.
- Existing verdict, measurement, estimate, and evidence fixtures remain equivalent.
- Missing labels/types still fail or error according to frozen specs.
- Plan assertions pass on the declared compatibility matrix.

## Rollback

Keep changes grouped by check family so one regressing family can return to its previous query shape
without reverting the identifier foundation.

## PR checklist

- [ ] Static/dynamic token inventory is included in the PR description.
- [ ] Every changed query has result and plan coverage.
- [ ] Values remain parameters.
- [ ] Optional-token variants are separately tested.
