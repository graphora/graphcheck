# SPEC-01 — `results.json`

*Frozen per schema revision.* `results.json` is the machine-readable output of a GraphCheck run
and the contract every other artifact (HTML report, MCP responses, future cloud) renders from. Its
current `schema_version` is `"1.2"`, versioned independently of `graphcheck_version`.

Version history:

- **1.2** adds canonical target `labels` and `relationship_types` inventory.
- **1.1** added aggregate measurement-scope evidence for drift findings that cannot honestly
  identify removed graph elements.
- **1.0** was the original results contract.

**Source of truth:** the Pydantic model at `src/graphcheck/contracts/results.py`. `docs/schemas/results.schema.json` is generated from it and is **structural only** — the derived invariants below live in the model's validators, so schema-valid ≠ fully-valid; external consumers must not rely on the schema alone.

**Required machine-valid 1.2 examples:**
`tests/unit/contracts/fixtures/results.{clean,complete,partial,generated-only,failed}.json`, delivered
with the 1.2 implementation.

## Top-level shape

```
{ schema_version, run, score, totals, suites[], checks[] }
```

- `run` — `id`, `started_at`/`finished_at` (ISO 8601 with an explicit UTC offset, with finish
  not preceding start), `graphcheck_version`, `pack_version`, `status`, `partial_reason`,
  `exit_code`, `selection`, `redaction`, `target`, `error`.
- `score` — `{ value, method: "weighted-by-severity", weights }` or `null`.
- `totals` — a tally of `checks[]`: `checks`, `pass`, `fail`, `warn`, `errored`, `skipped`.
- `suites[]` — `{ id, source_sha, score, totals }` per suite.
- `checks[]` — one flat record per check, keyed by `(suite_id, id)`.

Every model forbids unknown keys (`extra="forbid"`).

## Target inventory

For a non-null `run.target`, schema 1.2 adds the required keys `labels` and
`relationship_types` alongside database, server version, edition, fingerprint, capabilities, and
node/relationship totals.

```json
{
  "database": "neo4j",
  "server_version": "5.18.0",
  "edition": "community",
  "fingerprint": "sha256:abc123",
  "capabilities": {"apoc": true, "count_store": true},
  "nodes": 1250,
  "relationships": 3480,
  "labels": ["Account", "Customer"],
  "relationship_types": ["CONTROLS", "OWNS"]
}
```

The accuracy contract for these fields is:

1. Every new non-failed schema 1.2 run populates both fields with arrays. Failed runs may have
   `run.target:null` under the existing status rules.
2. Arrays contain exact Neo4j token names, are sorted ascending and case-sensitively by Unicode
   code point, and contain no duplicates. Noncanonical input is rejected rather than silently
   normalized at a consumer boundary.
3. `[]` means the connector probed that inventory category and found no tokens.
4. `null` is reserved for the compatibility-loaded representation of a schema 1.0 or 1.1 artifact
   that did not record the inventory. It does not mean that a new probe found an empty schema,
   failed, or lacked permission.
5. New producers reuse the schema tokens already collected by the connector probe. They must not
   add a Neo4j query, derive names from the fingerprint, or infer them from checks, evidence, or
   baseline data.
6. Labels and relationship types are structural inventory, not findings. Their presence must not
   generate concerns or automated recommendations.

The compatibility loader upgrades 1.0 and 1.1 artifacts in memory by adding
`labels:null` and `relationship_types:null` to a non-null historical target before validating
against the current model. It does not rewrite the source artifact. A re-exposed historical result,
including through MCP, preserves null as `not recorded by that schema version`.

## Shape by run status

`score` is a present-but-nullable key in every status — a number when ≥ 1 check executed, `null` when none did. `run.partial_reason` is non-null **iff** `run.status` is `partial`.

- **`complete`** — `run.target`, `totals`, `suites`, `checks` present; `run.error` null.
- **`partial`** — as complete, plus `partial_reason`. `totals` is derived from `checks[]`, so resolved-but-unexecuted checks are emitted as `skipped` (`skip_reason:"not_run"`); coverage lost to unloadable suites is described in `partial_reason`.
- **`failed`** — `run.error` is `{ code, message, fix }`; `target` may be null; `score` null; `suites`/`checks` empty. Exit 3.

## Field presence by verdict

**Every key is present in every record** — the model requires all of them, so a producer that omits one fails validation; the nullable/`false` keys below carry `null`/`false` when unused. Always non-null: `id`, `suite_id`, `pattern`, `name`, `severity`, `verdict`, `expected`. `provenance` and the rest are populated by `verdict`:

| Field | pass | fail / warn | errored | skipped |
| --- | --- | --- | --- | --- |
| `skip_reason` | null | null | null | **set** |
| `started_at`, `duration_ms` | set | set | set | null |
| `compiled_query` | set | set | set if compiled, else null | null |
| `params` | set | set | set if resolved, else null | null |
| `measured` | set | set | null | null |
| `estimate` | set | set | `false` | `false` |
| `evidence` | null | **set** | null | null |
| `error` | null | null | **set** | null |

## Semantic rules (the accuracy contract)

1. **verdict encodes outcome + severity.** A failing `severity:error` check → `verdict:fail`; a failing `severity:warn` → `verdict:warn`. A check **attempted but failed to compile/run** → `errored` (keeps its severity, carries `error`). A check that **could not be attempted** for lack of a capability (preflight) → `skipped` with `skip_reason:"unsupported"`.

   Exit code is the first matching row:

   | Order | Condition | Exit |
   | --- | --- | --- |
   | 1 | `run.status:failed` | **3** |
   | 2 | any `verdict:fail`, or (`errored` and `severity:error`) except an `engine.timeout` on a partial run | **1** |
   | 3 | `run.status:partial` (including `engine.timeout`); or nothing evaluated (empty universe, or all `skipped`); or any `verdict:warn`, or (`errored` and `severity:warn`) | **2** |
   | 4 | otherwise (`complete`, ≥ 1 executed, all `pass`/`skipped`) | **0** |

2. **Evidence is mandatory on `fail` and `warn`.** `compiled_query` is present once compiled, `null` if the check errored before compiling; it keeps `$param` placeholders — literal values live only in `params`.
3. **`errored` carries the fix** (`{code, message, fix}`); a `failed` run carries the same at `run.error`.
4. **Estimates are labeled** (`estimate:false` = exact, else `{sample_size, population, confidence, ci}`). `errored`/`skipped` are never estimates.
5. **`checks[]` is the selected universe.** It contains exactly the checks matching the active `--suite`/tag selection; non-matching checks are absent, not skipped. `totals` is a pure tally of `checks[]`; check identity `(suite_id, id)` and suite ids are unique.
6. **Score:** `round(100 × Σ w(pass) / Σ w(pass|fail|warn|errored))` with `w(error)=3, w(warn)=1` (hard-coded), computed per run **and per suite**; empty denominator ⇒ `null`. Rounding applies to the exact rational value using **half-to-even**, without an intermediate floating-point value. Weights are locked. The overall score is computed directly from all checks, never by averaging rounded suite scores. Also: `verdict:fail` requires `severity:error` and `verdict:warn` requires `severity:warn` (rule 1) — mismatches are rejected, so a malformed record can't downgrade the exit code.
7. **Redaction** enum `none | mask | hash` is frozen. Normal runs emit
   `{policy:"none", applied:false}`. `graphcheck run --redact` (alias `--redacted`) and
   `graphcheck redact` emit
   `{policy:"mask", applied:true}` after replacing compiled query text, all `params`, `expected`,
   and `measured` leaves, evidence messages and element IDs/labels/types, check names and
   provenance, partial reasons, error messages/fixes, source hashes, and target identifiers with
   `[REDACTED]`. Suite, check, and selected-tag identifiers use consistent ordered aliases that
   preserve cross-field relationships. Redacted run IDs are target-neutral and derived only from
   the finish timestamp. Keys, containers, error codes, verdicts, scores, and run-level counts are
   preserved. Redaction collects the original strings from every masked or aliased surface and
   rejects a final artifact that repeats one outside the explicit structural allowlist of schema
   and version metadata, timestamps, enums, server metadata, and error codes. The canonical JSON
   and HTML writers also verify every artifact's mask, alias, and neutral-ID policy. `hash` remains
   reserved.

Evidence pointer `kind` is `node`, `rel`, or `aggregate`. An `aggregate` pointer identifies a
canonical metric/target scope such as `node_count:label=Customer`; it is not a Neo4j element ID.
This is required for aggregate count-drift decreases because deleted elements cannot be selected
from the current graph. Aggregate pointers must never be substituted for row-level
node/relationship or property-coverage evidence.
8. **Coverage-status invariant:** any `skip_reason ∈ {unsupported, not_run}` ⇒ `run.status:partial` (a `partial` run never exits 0). `generated` skips do not force partial.
9. **Target-inventory invariant:** results produced by a new non-failed 1.2 run carry sorted,
   unique, non-null `run.target.labels` and `run.target.relationship_types`. Nullable inventory
   exists only at a compatibility boundary for pre-1.2 input. An empty array is recorded evidence
   of an empty probed category and is never interchangeable with null.

## Downstream and MCP contract

The HTML report, CLI artifact readers, report history, MCP tools, and future cloud surfaces consume
the same validated `Results` model. Any MCP tool that returns a run result must:

- expose `labels` and `relationship_types` in its declared output schema;
- return arrays for new 1.2 runs and preserve null for compatibility-loaded pre-1.2 artifacts;
- use or derive from the canonical SPEC-01 schema rather than maintain a divergent inventory
  definition; and
- preserve canonical order without adding target-specific interpretation or recommendations.

## Deliverables

- `src/graphcheck/contracts/results.py` — the Pydantic model (source of truth) with `model_validator`s for the status shape, `partial_reason` iff, derived invariants (score incl. per-suite null, totals, exit code), field-presence table, coverage-status, and identity/suite uniqueness.
- `src/graphcheck/scoring.py` — the pure deterministic weighted scorer shared by the engine,
  result validator, and report renderer.
- `docs/schemas/results.schema.json` — generated JSON Schema (structural).
- `tests/unit/contracts/fixtures/results.{clean,complete,partial,generated-only,failed}.json` —
  machine-valid 1.2 artifacts with non-null target inventory on every non-failed current result.
- `tests/unit/contracts/test_results.py` — validates fixtures against the schema, round-trips them, and asserts every invariant directly.
- MCP output schemas and contract tests — validate new-run arrays and historical null semantics
  against the same SPEC-01 1.2 shape.
