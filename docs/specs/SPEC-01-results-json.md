# SPEC-01 — `results.json`

*Frozen for v0.* `results.json` is the machine-readable output of a GraphCheck run and the contract every other artifact (HTML report, MCP responses, future cloud) renders from. Its `schema_version` is `"1.1"`, versioned independently of `graphcheck_version`. Version 1.1 adds aggregate measurement-scope evidence for drift findings that cannot honestly identify removed graph elements.

**Source of truth:** the Pydantic model at `src/graphcheck/contracts/results.py`. `docs/specs/results.schema.json` is generated from it and is **structural only** — the derived invariants below live in the model's validators, so schema-valid ≠ fully-valid; external consumers must not rely on the schema alone.

**Machine-valid examples:** `tests/contracts/fixtures/results.{complete,partial,generated-only,failed}.json`.

## Top-level shape

```
{ schema_version, run, score, totals, suites[], checks[] }
```

- `run` — `id`, `started_at`/`finished_at` (ISO 8601 UTC), `graphcheck_version`, `pack_version`, `status`, `partial_reason`, `exit_code`, `selection`, `redaction`, `target`, `error`.
- `score` — `{ value, method: "weighted-by-severity", weights }` or `null`.
- `totals` — a tally of `checks[]`: `checks`, `pass`, `fail`, `warn`, `errored`, `skipped`.
- `suites[]` — `{ id, source_sha, score, totals }` per suite.
- `checks[]` — one flat record per check, keyed by `(suite_id, id)`.

Every model forbids unknown keys (`extra="forbid"`).

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
   | 2 | any `verdict:fail`, or (`errored` and `severity:error`) | **1** |
   | 3 | `run.status:partial`; or nothing evaluated (empty universe, or all `skipped`); or any `verdict:warn`, or (`errored` and `severity:warn`) | **2** |
   | 4 | otherwise (`complete`, ≥ 1 executed, all `pass`/`skipped`) | **0** |

2. **Evidence is mandatory on `fail` and `warn`.** `compiled_query` is present once compiled, `null` if the check errored before compiling; it keeps `$param` placeholders — literal values live only in `params`.
3. **`errored` carries the fix** (`{code, message, fix}`); a `failed` run carries the same at `run.error`.
4. **Estimates are labeled** (`estimate:false` = exact, else `{sample_size, population, confidence, ci}`). `errored`/`skipped` are never estimates.
5. **`checks[]` is the selected universe.** It contains exactly the checks matching the active `--suite`/tag selection; non-matching checks are absent, not skipped. `totals` is a pure tally of `checks[]`; check identity `(suite_id, id)` and suite ids are unique.
6. **Score:** `round(100 × Σ w(pass) / Σ w(pass|fail|warn|errored))` with `w(error)=3, w(warn)=1` (hard-coded), computed per run **and per suite**; empty denominator ⇒ `null`. `round` is **half-to-even** (Python's `round()`); re-implementations must match. Weights are locked. Also: `verdict:fail` requires `severity:error` and `verdict:warn` requires `severity:warn` (rule 1) — mismatches are rejected, so a malformed record can't downgrade the exit code.
7. **Redaction** enum `none | mask | hash` is frozen; v0 emits `none` only. `params` is the only literal-value surface; `evidence.elements` carry graph element IDs or aggregate measurement-scope IDs plus labels/types only; `compiled_query` keeps placeholders.

Evidence pointer `kind` is `node`, `rel`, or `aggregate`. An `aggregate` pointer identifies a
canonical metric/target scope such as `node_count:label=Customer`; it is not a Neo4j element ID.
This is required for aggregate count-drift decreases because deleted elements cannot be selected
from the current graph. Aggregate pointers must never be substituted for row-level
node/relationship or property-coverage evidence.
8. **Coverage-status invariant:** any `skip_reason ∈ {unsupported, not_run}` ⇒ `run.status:partial` (a `partial` run never exits 0). `generated` skips do not force partial.

## Deliverables

- `src/graphcheck/contracts/results.py` — the Pydantic model (source of truth) with `model_validator`s for the status shape, `partial_reason` iff, derived invariants (score incl. per-suite null, totals, exit code), field-presence table, coverage-status, and identity/suite uniqueness.
- `docs/specs/results.schema.json` — generated JSON Schema (structural).
- `tests/contracts/fixtures/results.{complete,partial,generated-only,failed}.json` — machine-valid artifacts.
- `tests/contracts/test_results.py` — validates fixtures against the schema, round-trips them, and asserts every invariant directly.
