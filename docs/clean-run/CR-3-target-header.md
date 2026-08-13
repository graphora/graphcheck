# CR-3 — Target information in the run-report header

## Ticket

Render trustworthy target context at the top of Graph Health Overview: database, Neo4j version,
edition, node and relationship totals, label and relationship-type inventory summary, and recorded
capabilities.

## Dependencies

No other clean-run ticket is required. This ticket should land before the final clean/findings
fixture updates because it changes the canonical results schema and fixture shape.

## Contract fact that must be resolved

The current `ResultsTarget` persists:

- database;
- server version and edition;
- fingerprint;
- APOC and count-store capabilities;
- node and relationship totals.

It does **not** persist labels or relationship types. The Neo4j connector reads those names during
the target probe to calculate the fingerprint, then discards them. A renderer cannot reconstruct
the inventory from a hash.

Therefore, the node/relationship and version work is rendering-only, but the exact label/type
requirement needs a results-contract extension. It must not be implemented by scraping check
queries, evidence, or fingerprints.

## Approved results contract change

Revise SPEC-01 additively from schema 1.1 to 1.2. Add these required keys to `ResultsTarget`:

```json
{
  "labels": ["Account", "Customer"],
  "relationship_types": ["CONTROLS", "OWNS"]
}
```

Semantics:

- for a new 1.2 run, both values are arrays;
- `[]` means the inventory was probed and is empty;
- `null` is reserved for the in-memory compatibility representation of a pre-1.2 artifact whose
  schema version did not record inventory;
- a non-empty list contains exact Neo4j token names, sorted ascending and case-sensitively by
  Unicode code point, with no duplicates;
- these fields contain names only, never property names or values.

The compatibility loader accepts schema 1.0 and 1.1 artifacts, upgrades them in memory to the 1.2
model, and injects `labels: null` and `relationship_types: null` when a non-null historical target
lacks those keys. Reading report history must not rewrite the source artifact. A new run must never
use `null` to mean that a probe failed, permissions were insufficient, or a supplied target omitted
required data; it must produce both arrays or follow the existing explicit run failure/coverage
semantics.

Populate the fields from the existing `_schema_tokens()` probe result without adding another
database round trip. A programmatically supplied target used to produce a new run must include both
inventories or be completed through the same connector-probe path. It must not silently produce
`null` or infer names from the fingerprint, checks, evidence, or baseline data.

The Pydantic validators enforce canonical order and uniqueness instead of silently accepting a
noncanonical 1.2 payload. Producers should sort and deduplicate before model construction.

## HTML design

The Graph Health Overview header should contain a compact target block. Capabilities are rendered
as green pills with white text when available and grey pills with white text when unavailable. A
hover/focus tooltip states `Available` or `Unavailable`:

```text
Target Graph       neo4j
Database           Neo4j 5.18.0 community
Size               1,250 nodes · 3,480 relationships
Schema Inventory   2 labels · 2 relationship types
Capabilities       APOC, Count Store
```

The inventory summary expands without network access to show the exact persisted names:

```text
Labels: Account, Customer
Relationship types: CONTROLS, OWNS
```

For long inventories, the compact row shows counts and a native `<details>` disclosure contains the
full escaped list. Do not discard names from the document merely to truncate the visual summary.

Inventory absence is possible only when rendering a migrated pre-1.2 artifact and is explicit:

```text
Size               Counts unavailable
Schema inventory   Inventory not recorded
```

Do not render `0` for historical `null`, and do not render `Inventory not recorded` for a probed
empty list.

## CLI boundary

The final CR-1 target line may include database, version, edition, and node/relationship totals.
Do not print the full label/type inventory or capability list in the default CLI summary; those
belong in the report.

## Implementation tasks

- Extend `ResultsTarget` and its validators in `src/graphcheck/contracts/results.py`.
- Bump the results schema constant and literal to 1.2.
- Require sorted, unique arrays on newly produced 1.2 results; retain nullable inventory only in
  the compatibility path for older artifacts.
- Extend the loader's historical compatibility upgrade for 1.0 and 1.1 inputs by injecting null
  inventory on non-null targets before 1.2 validation.
- Update SPEC-01, then regenerate `docs/specs/results.schema.json` from the Pydantic source of truth.
- Preserve schema token names from `src/graphcheck/neo4j_adapter.py` in the target returned to the
  engine without another query.
- Ensure `src/graphcheck/engine/runner.py` writes both inventories into every new non-failed run and
  refuses or completes a supplied target that lacks them.
- Update every results contract fixture to 1.2; give clean/findings fixtures concrete inventories
  and use arrays—including `[]` for a probed empty schema—in every current fixture.
- Update every MCP tool that returns results, plus its declared output schema, to expose the SPEC-01
  1.2 target shape. Derive or reference the canonical results schema rather than maintaining a
  divergent MCP-only copy.
- Refactor the target portion of `src/graphcheck/reporting/html.py::_status_overview()` into a
  focused renderer.
- Update summary/header CSS for compact wrapping and mobile stacking.
- Update connector, contract, writer, CLI-run, report, history, MCP, and telemetry-boundary
  snapshots affected by the version/shape change.

## Acceptance tests

### CR-3.A — Persisted target facts

For both clean and findings fixtures, the report renders the exact database, server version,
edition, node count, relationship count, label count, relationship-type count, names, and
capability booleans stored in `run.target`.

### CR-3.B — Connector propagation

A connector probe returning known schema tokens produces a written `results.json` whose sorted
inventories exactly match those tokens. Assert that the probe still uses the existing schema-token
round trip rather than issuing an additional inventory query.

### CR-3.C — New-producer invariant

Every new complete or partial 1.2 run contains non-null `labels` and `relationship_types` arrays.
A new programmatically supplied target without inventory cannot serialize a result with null
inventory. Contract tests reject unsorted or duplicate arrays.

### CR-3.D — Empty versus historical not-recorded

A probed empty inventory renders `0 labels · 0 relationship types`. A migrated pre-1.2 null
inventory renders `Inventory not recorded`. The renderer never treats the latter as a probe result.
Counts retain their existing zero-versus-null distinction.

### CR-3.E — Historical compatibility

Schema 1.0 and 1.1 artifacts load in memory with `labels == null` and
`relationship_types == null`, render `Inventory not recorded`, and remain byte-for-byte unchanged
on disk. New runs use schema 1.2 and non-null arrays.

### CR-3.F — MCP contract propagation

Every MCP result-returning response and declared output schema contains the 1.2 target keys. A new
run returns arrays; a loaded historical artifact may return null with the same `not recorded`
semantics. MCP and file/HTML consumers use the same canonical SPEC-01 schema.

### CR-3.G — No inference

A target with an inventory hash but null inventory does not render labels/types found in checks,
evidence, suite names, or other report fields.

### CR-3.H — Offline and escaping guarantees

Inventory names are escaped, the full report remains self-contained, and expanding the inventory
does not perform a network request.

## Non-goals

- Per-label or per-relationship-type entity counts.
- Property, constraint, or index summaries.
- Schema coverage percentages.
- Inferring inventory from configured checks or the graph fingerprint.
- Adding new profiling queries solely for this report.
- Property keys, property coverage, and automated recommendations.

## Definition of done

- The exact target facts needed by the header exist in the canonical artifact.
- Historical artifacts remain readable without mutation.
- Every new run records arrays, and all SPEC-01/MCP consumers share their semantics.
- The renderer clearly distinguishes probed empty, historical not-recorded, and non-empty
  inventories.
