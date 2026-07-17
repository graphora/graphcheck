# SPEC-04 — Engine

*Frozen for v0.* The GraphCheck engine loads SPEC-02 suites, compiles selected checks to
parameterized Cypher, executes them through the read-only SPEC-03 connector, evaluates their
verdicts, and emits a validated SPEC-01 result. The `graphcheck run` command is the project-facing
orchestrator for this pipeline and writes both machine-readable and offline human-readable
artifacts.

## Source of truth

The implementation is split by responsibility:

| Responsibility | Source |
| --- | --- |
| Suite YAML model and validation | `src/graphcheck/contracts/check.py` (SPEC-02) |
| Compiled check boundary and drift compilers | `src/graphcheck/engine/compiler.py` |
| Built-in conformance compiler callbacks | `src/graphcheck/engine/core_pack.py` |
| Read-only connector execution | `src/graphcheck/engine/executor.py` and SPEC-03 |
| Parameter-token resolution | `src/graphcheck/engine/parameters.py` |
| Pure verdict evaluation and evidence extraction | `src/graphcheck/engine/evaluator.py` |
| Seeded sampling policy | `src/graphcheck/engine/sampling.py` |
| C4-compatible baseline resolution | `src/graphcheck/engine/baseline.py` |
| Run isolation, deadlines, metadata, score inputs, and result assembly | `src/graphcheck/engine/runner.py` |
| Project-facing `graphcheck run` command | `src/graphcheck/cli.py` |
| SPEC-01 JSON and offline HTML serialization | `src/graphcheck/reporting/` |

SPEC-01 remains the authority for result shape, scoring, verdict field presence, and exit-code
precedence. SPEC-02 remains the authority for suite/check YAML. SPEC-03 remains the authority for
profiles, target probing, error mapping, and driver-enforced read access.

## Responsibilities and boundaries

The engine SHALL:

- validate every loaded suite through SPEC-02 before executing it;
- select checks without representing non-matches as skipped results;
- compile conformance, competency-shape, competency-regression, and drift patterns;
- keep data values in Cypher parameters rather than interpolating them into query text;
- execute only through C2's read API and propagate per-query deadlines where supported;
- distinguish assertion findings from compile, query, timeout, schema, and evaluation errors;
- isolate one check's error from later checks unless `--fail-fast` is active;
- require real node/relationship pointers for row-level `fail` and `warn` findings and deterministic
  measurement-scope pointers for aggregate drift findings;
- label every sampled result as an estimate with reproducibility metadata;
- produce a fully validated SPEC-01 `Results` model; and
- preserve partial coverage explicitly instead of silently truncating a run.

The engine SHALL NOT:

- write to the customer graph;
- rely on a Cypher keyword blocklist for write protection;
- silently treat a missing label, missing relationship type, broken query, or timeout as a pass;
- manufacture graph-element pointers from domain identifiers such as `customer_id` or
  `account_id`;
- create C4 profiles/baselines; or
- own C3 pack configuration schemas.

## `graphcheck run`

### Command surface

```console
graphcheck run
graphcheck run --profile staging
graphcheck run --suite customer-360
graphcheck run --select tag:production
graphcheck run --suite customer-360 --select tag:production --fail-fast
```

`--profile`, when supplied, selects a SPEC-03 profile. Otherwise the `default` profile from
`profiles.yml` is used.

`--suite` and `--select` are repeatable. The only v0 selector syntax is `tag:<name>`; an unknown or
blank selector is a configuration failure. Repeated suite identifiers preserve the requested
selection after duplicate values are removed. Repeated tag selectors use OR semantics: a check is
selected if it has any requested tag.

### Project and suite discovery

The command locates the project by walking upward to `graphcheck.yml`, then resolves its `checks`
and `artifacts` paths relative to that project root unless either configured path is absolute.

Suite discovery recursively reads regular files below the configured checks directory whose suffix
is `.yml` or `.yaml`, case-insensitively. Files are loaded in sorted path order. All discovered suites
are validated before suite-id filtering; therefore malformed YAML, duplicate YAML keys, unknown
keys, unknown check types, or invalid check payloads are loud configuration failures even if a
later `--suite` filter would not select that file.

`--suite` matches the resolved SPEC-02 suite id, not merely the filename. An explicit `suite:` field
wins over the filename-stem fallback. A requested suite that matches nothing produces a complete
run over an empty selected universe: `checks: []`, `score: null`, exit 2. The requested suite ids
remain present in `run.selection.suites` so the empty scope is auditable.

Non-matching checks are absent from `checks[]`; they are not `skipped`. The selected universe is
recorded in `run.selection` as `{suites, tags, fail_fast}`.

### Fail-fast

`--fail-fast` stops after the first hard result:

- `verdict: fail`; or
- `verdict: errored` with `severity: error`.

Warn-severity findings and warn-severity errors do not trigger fail-fast. Every later check in the
selected universe is emitted as `skipped` with `skip_reason: not_run`. If at least one later check is
skipped this way, the run is `partial` and its `partial_reason` identifies the check after which
execution stopped. A hard result on the final selected check does not make the run partial because
no coverage was lost.

### Artifacts

A prepared run writes:

```text
<artifacts>/runs/latest/results.json
<artifacts>/runs/latest/report.html
```

With the default project configuration these resolve to:

```text
.graphcheck/runs/latest/results.json
.graphcheck/runs/latest/report.html
```

The JSON writer first normalizes through the SPEC-01 Pydantic model, then validates the structural
JSON Schema, retains every frozen nullable key, and writes deterministic indented/sorted JSON with a
trailing newline. The JSON and HTML writers share one JSON-compatible value normalizer: YAML/Python
dates and datetimes use Pydantic's ISO representation, binary values use URL-safe base64, and sets
are ordered by their canonical JSON representation before becoming arrays.

The HTML report is rendered only from a validated SPEC-01 result. It contains inline CSS and no
JavaScript, CDN, external font, image, stylesheet, or link dependency. It opens offline and shows
run metadata, score, target fingerprint/version, partial/failed banners, suite totals and source
SHAs, compiled Cypher, expected/measured values, estimates, errors, and evidence pointers. Checks
are ordered `fail`, `warn`, `errored`, `skipped`, `pass`, then by severity, suite id, and check id.

Configuration and connection failures also produce failed-run artifacts when the project artifact
path can be resolved. A missing project root cannot produce an artifact because no authoritative
artifact path exists. Failure to write either artifact returns process exit 3 and prints a
filesystem fix.

### Console summary and exit codes

The command prints run id/status, verdict totals, score, exit code, partial reason or structured
error when present, and both artifact paths. The process returns the already-validated
`run.exit_code`; the CLI does not derive a second result.

Exit precedence is the frozen SPEC-01 contract:

| Order | Condition | Exit |
| --- | --- | --- |
| 1 | Run could not start/complete because of configuration or connection failure | `3` |
| 2 | Any error-severity `fail`, or error-severity `errored` | `1` |
| 3 | Partial run, nothing evaluated, `warn`, or warn-severity `errored` | `2` |
| 4 | Complete run, at least one executed check, every executed check passed | `0` |

Exit 2 is the warning/incomplete-coverage gate. CI may choose whether to accept it, but GraphCheck
always reports it distinctly from both success and a hard failure.

## Programmatic engine interface

`Engine` accepts a SPEC-03-compatible client plus optional baseline provider, compiler, evaluator,
parameter-token resolver, and `EngineConfig`. Convenience entry points accept one loaded suite, one
YAML string, multiple independent YAML strings, or a sequence of `SuiteInput` objects.

`SuiteInput.from_yaml(text, source=...)` records `source_sha` as SHA-256 over the exact UTF-8 YAML
bytes. A directly supplied loaded suite without source text uses a canonical JSON serialization of
the normalized suite for its SHA. Suite SHA participates in both result reproducibility metadata and
sampling seed derivation.

The default engine configuration is:

| Setting | Default | Contract |
| --- | --- | --- |
| Internal run budget | `295` seconds | Leaves serialization/reporting margin inside five minutes |
| Evidence cap | `100` unique pointers | Must be a positive integer |
| Exhaustive sampling limit | `100,000` elements | At or below this population, policy execution is exact |
| Default sample size | `10,000` elements | Used above the exhaustive limit unless a check overrides it |
| Sampling seed | `0` | Non-negative integer or non-blank string |

## Run lifecycle

For each run the engine:

1. normalizes input suites and applies OR-based tag selection;
2. rejects duplicate loaded suite ids before target probing;
3. starts the monotonic run deadline and records UTC start time/run id;
4. probes C2 for database, version, edition, capabilities, and graph fingerprint unless a validated
   `RunTarget` was supplied directly;
5. iterates the selected checks in suite/file order;
6. skips effective `generated:true` checks as `skipped:generated` without querying C2;
7. compiles the check and resolves any graph-relative competency parameters;
8. performs the population preflight and deterministic sample decision when the compiler marks the
   check sampled;
9. resolves the requested C4 baseline before executing drift Cypher;
10. executes the parameterized query through the read-only executor with the remaining deadline;
11. evaluates rows/column metadata, maps the boolean evaluation through declared severity, and
    constructs the frozen SPEC-01 check result; and
12. derives per-suite/run totals, score, status, partial reason, finish time, and exit code.

Every check is wrapped at the compile/resolve/execute/evaluate boundary. A structured
`GraphCheckError` becomes `verdict: errored`; an unexpected exception becomes
`engine.internal_error`. The runner then continues unless fail-fast or the wall-clock budget prevents
further work.

The programmatic `run_yamls` interface treats independently unloadable sources as lost coverage:
valid suites still run and the result is partial. The CLI is intentionally stricter and treats an
invalid project suite as a configuration failure before opening the database connection.

## Cypher compilation contract

### `CompiledCheck`

Compilation produces an immutable boundary containing:

```text
check
query
params
expected
name
evidence_cap
sampled
population_query / population_params / sample_population
```

`query` is the executable Cypher retained in `results.json`; it keeps `$parameter` placeholders.
`params` holds literal values. `expected` is the normalized assertion rendered into SPEC-01.
Sampling-only fields are null/false for exhaustive non-sampled checks.

### Parameter safety

Built-in templates do not interpolate labels, relationship types, property names, regexes, allowed
values, thresholds, or pinned values. Dynamic schema tokens are compared through expressions such
as `$label IN labels(n)`, `type(r) = $relationship_type`, and `n[$property]`. The only syntax chosen
by a conformance callback is relationship direction, selected from C3's closed
`out | in | any` enum and mapped to fixed query fragments.

Competency Cypher is customer-authored and preserved after surrounding whitespace is removed. The
compiler lexically identifies `$name` parameters outside quoted strings, backtick identifiers, line
comments, and block comments. Every identified parameter must exist in the check's `params`; extra
declared params are allowed and passed to Neo4j.

Strings in competency `params` beginning with `$` are graph-relative tokens, not literal strings.
The v0 built-in resolver supports `$first-active-customer`. Repeated uses of the same token in one
check are resolved once and cached; distinct tokens receive a newly calculated remaining timeout.
Unknown or unresolved tokens are errors, never silently treated as literals.

### Schema visibility

Built-in conformance and drift queries inventory `db.labels()` and `db.relationshipTypes()` and
return `schema_ok`, `missing_labels`, and `missing_relationship_types` in their single summary row.
The evaluator rejects a missing/invalid schema marker and turns a referenced missing token into
`engine.schema_reference_missing`.

C2 additionally inspects Neo4j query notifications for missing labels/relationship types on
customer-authored competency queries. A typo therefore cannot become an empty-result pass.

### Conformance compiler registry

SPEC-02's C3 registry owns validation models. C1 owns a separate callback registry mapping the same
check names to Cypher templates, allowing pack schema and engine compilation to evolve without
changing the frozen YAML envelope. Loading a check still requires its C3 model to be installed;
having only a compiler callback is insufficient.

The engine provides callbacks for the twelve core C3 identifiers:

| Check | Evaluated rule |
| --- | --- |
| `completeness` | Property coverage ratio is at least `threshold` (`0..1`) |
| `cardinality` | Each source node has exactly the configured directed relationship count to the target label |
| `no_orphans` | Selected nodes have at least one configured/any relationship in the selected direction |
| `dangling_rels` | Fails closed as unobservable; no optimistic Cypher result is produced |
| `property_type` | Non-null property values match the configured portable type |
| `property_format` | Non-null property values satisfy the configured regular expression |
| `value_in_set` | Non-null property values belong to the configured allowed values |
| `uniqueness` | Non-null property values occur on no more than one node |
| `hub_outlier` | Sampled node degree is not above the sample mean plus `z_threshold × stDevP(sampled degree)` |
| `label_cooccurrence` | No node simultaneously carries both configured labels |
| `rel_direction` | The configured relationship does not appear with source/target labels reversed |
| `temporal_sanity` | End-property values are not earlier than start-property values |

All observable conformance templates return exactly one summary row with a non-negative
`violation_count`, a population, scalar measurements where applicable, and capped pointer evidence.
`completeness` additionally returns `conforming_count` and a ratio `coverage`. Internally inconsistent
summary arithmetic is `engine.invalid_query_result`, never a finding or pass.

Neo4j Cypher cannot expose a relationship whose backing-store endpoint cannot be resolved: such a
relationship is absent before a `MATCH` row exists. For that reason `dangling_rels` raises
`engine.check_unobservable` rather than returning a misleading zero violations.

### Competency compilation

Competency-shape and competency-regression use the authored query directly. The pattern is derived
by SPEC-02: `contains` or `equals` selects regression; otherwise the check is shape-only. All
declared assertions are retained and evaluated together.

### Drift compilation

The engine compiles these metrics:

| Metric | Valid target |
| --- | --- |
| `node_count` | `{}` for the whole graph or `{label: <label>}` |
| `relationship_count` | `{}` for the whole graph or `{type: <relationship type>}` |
| `property_coverage` | Exactly one of `label`/`type`, plus a non-blank `property` |

Count metrics return a current aggregate and population. Property coverage returns a percentage in
the closed interval `0..100` and pointer evidence to elements missing the property. Unknown target
keys and unsupported metrics are compile errors. Every node/relationship-count drift finding gets
one deterministic aggregate-scope pointer from the metric and the target sorted by key, for example
`node_count:label=Customer` or `relationship_count:type=OWNS`. Property-coverage drift does not use
this fallback because its compiled query can identify the concrete elements missing the property.

## Read-only execution

`ReadOnlyExecutor` prefers C2's rich `run_read_result(query, params, timeout_s=...)` interface because
it preserves raw Neo4j nodes, relationships, paths, result columns, and notifications. It falls back
to SPEC-03 `run_read` for compatible connectors. Missing both APIs produces
`engine.connector_invalid` for the attempted check.

C1 does not attempt to parse or block write keywords. C2 creates every session with
`neo4j.READ_ACCESS`, so Neo4j rejects a customer-authored `CREATE`, `MERGE`, `DELETE`, `SET`, or other
write at the driver/database level. The resulting query error is an errored check and no write is
committed.

Every target probe, token lookup, population preflight, and check query receives the current
remaining run budget when the connector method accepts `timeout_s`. Timeout, broken Cypher, auth,
permission, missing database, and query exceptions retain C2's structured `{code,message,fix}`
shape.

## Verdict evaluation

`VerdictEvaluator` is pure over the compiled check, result rows, result-column metadata, and optional
baseline. It does not inspect severity. The runner maps `Evaluation.passed` through declared
severity:

| Evaluation | Severity | Verdict |
| --- | --- | --- |
| Passed | `error` or `warn` | `pass` |
| Failed | `error` | `fail` |
| Failed | `warn` | `warn` |
| Compile/resolve/execute/evaluate error | `error` or `warn` | `errored` with original severity |

### Conformance

Observable conformance checks must return one summary row. `completeness` passes when calculated
coverage meets its threshold; all other conformance callbacks pass when `violation_count == 0`.
Counts must be non-negative integral numbers, measurement values must be finite, and completeness
population/conforming/violation/coverage values must agree exactly within numerical tolerance.

### Competency shape

Every declared predicate is evaluated:

- `rows.exactly`, `rows.min`, and `rows.max` constrain row count;
- `columns` is an ordered exact comparison against C2 result metadata;
- `unique:true` requires every full result row to be distinct;
- `unique:false` requires at least one duplicate full result row; and
- `empty:true|false` constrains whether the result has rows.

Row uniqueness treats maps as value objects, so mapping insertion order does not change a verdict.
For a zero-row result, the rich connector's column metadata is authoritative; columns are not
inferred only from a nonexistent first row.

### Competency regression

For a single-column result, regression values are that column's values. For multiple columns, each
regression value is a complete `{column: value}` mapping.

`contains` requires every pinned value to occur. `equals` compares the complete result as an
order-independent, duplicate-preserving bag because Neo4j does not guarantee row order without
`ORDER BY`. Shape assertions and regression assertions apply together; a regression overlay does
not replace row/column/uniqueness constraints.

### Drift

The evaluator compares `current` with the resolved baseline and records current, baseline, numeric
delta, and percentage change (`null` when the baseline is zero). Supported tolerance keys are:

| Key | Rule |
| --- | --- |
| `max_drop_pct` | Limits percentage decrease |
| `max_increase_pct` | Limits percentage increase |
| `max_change_pct` | Limits absolute percentage change in either direction |
| `max_delta` / `absolute` | Aliases limiting absolute numeric delta |
| `min` | Requires current value at or above the bound |
| `max` | Requires current value at or below the bound |

Percentage/delta limits must be finite and non-negative; `min` and `max` may be finite signed
numbers. A non-zero percentage change from a zero baseline is treated as infinite and therefore
exceeds any finite percentage limit. Property coverage values use C4 percent units `0..100`, unlike
conformance completeness's `0..1` ratio.

## Evidence accuracy contract

Every `fail` and `warn` SHALL contain at least one honest evidence pointer. Row-level findings require
real node or relationship pointers. Aggregate count drift instead carries a logical aggregate
pointer whose ID is the canonical metric/target measurement scope. Evidence is deduplicated by
`(kind,id)`, capped at the configured evidence cap, and labeled with `truncated`, `cap`, and
`total_count`.

Accepted pointer sources are:

- raw Neo4j Node and Relationship objects;
- graph values nested in mappings, lists, sets, tuples, and path-like `nodes`/`relationships`;
- typed query-result mappings `{kind: node|rel, id, labels?|type?}`;
- explicit `node_element_id`, `rel_element_id`, or `relationship_element_id` aliases; and
- validated node/relationship evidence supplied by a baseline provider, plus aggregate baseline
  evidence for node/relationship-count drift.

Query results cannot self-declare `kind: aggregate`; only the count-drift evaluator or a validated
baseline provider can create aggregate evidence. This prevents a pointerless conformance,
competency, or property-coverage finding from bypassing the graph-pointer requirement.

Arbitrary fields ending in `_id` are not element pointers. Domain identifiers are never promoted to
Neo4j identity.

If an assertion fails but none of the accepted sources yields a pointer, evaluation raises
`engine.evidence_missing`; the runner emits `errored`, never a pointerless finding. Authors of
competency checks intended to fail on returned rows should therefore project a node, relationship,
path, typed pointer, or explicit element-id alias.

Aggregate count-drift decreases have no honest current element to blame: deleted elements cannot be
selected from the current graph. If neither the compiled result nor baseline provides concrete
identity evidence, the evaluator emits a deterministic `aggregate` pointer such as
`node_count:label=Customer`. Its `total_count` is `1` because the pointer describes one measurement
scope, not a truncated sample of the population. It must not be used to rescue pointerless
conformance or competency findings; those remain `engine.evidence_missing`.

## Sampling policy

Sampling applies only to compiler plans explicitly marked sampled (currently `hub_outlier`). Before
the main query, the engine executes a population query that must return exactly one non-negative
integer `population`.

The per-check seed is SHA-256 over domain-separated, length-prefixed components:

```text
configured seed
graph fingerprint
suite source SHA
check id
```

The same graph, exact suite bytes, check id, and configured seed therefore produce the same sample.
Changing any component changes the derived seed. The policy is exact when population is at or below
the exhaustive limit or configured sample size; otherwise it selects the configured sample size.
A check-level sample size may request a smaller sample and is capped at population.

The core `hub_outlier` query orders candidates by a stable seed-derived Cypher key plus node id. The
sampling module also exposes a deterministic uniform Floyd selector using `O(sample_size)` memory
for callers with a canonical indexed population.

Exhaustive outcomes serialize `estimate:false`. A strict subset serializes
`{sample_size,population,confidence:0.95,ci:[lo,hi]}` using a two-sided 95% Wilson proportion
interval. The interval is bounded to `[0,1]`; sampling errors never fall back to unlabeled exact
results.

## Baselines

`MappingBaselineProvider` accepts compact numeric mappings and C4 baseline/profile-shaped Pydantic
models or dictionaries. It resolves:

- whole-graph or label-scoped node count;
- whole-graph or type-scoped relationship count; and
- node/relationship property coverage.

Measurements must be finite, numeric, and non-negative. Missing, invalid, or partial-but-uncollected
measurements are distinct structured errors. Successfully using a partial C4 baseline keeps the
check result but marks the overall run partial so incomplete baseline visibility is never hidden.

`graphcheck run` uses `DirectoryBaselineProvider` over `<artifacts>/baselines/*.json`. A pinned
baseline name matches a filename stem. `latest` selects the lexicographically newest JSON filename,
matching C4's timestamp-sortable filename convention. Invalid referenced JSON is
`engine.baseline_invalid`; an absent reference/metric is `engine.baseline_missing`.

## Isolation, partial status, and time budget

| Condition | Check outcome | Run effect |
| --- | --- | --- |
| Assertion passes | `pass` | None |
| Assertion fails with evidence | `fail` or `warn` | Exit severity only |
| Compile/query/timeout/schema/evaluation failure | `errored` | Later checks continue; not partial by itself |
| Effective `generated:true` | `skipped:generated` | Excluded from score; not partial by itself |
| Deadline or fail-fast prevents attempt | `skipped:not_run` | Run is partial |
| Partial baseline is used | Normal check verdict | Run is partial with reason |
| Independently unloadable programmatic YAML source | No records for unknown universe | Other suites run; run is partial with reason |
| Duplicate suite id or target-probe failure | No checks execute | Run status `failed`, exit 3 |

The engine's monotonic deadline includes target probing, token resolution, sampling preflight,
baseline work, and query execution. When the deadline is exhausted, the active check is errored if
it was attempted and every later selected check is `skipped:not_run`. Checks remaining after the
budget are never silently omitted.

## Reproducibility metadata

Every completed or partial run records:

- graph fingerprint from the SPEC-03 target probe;
- Neo4j database name, version, edition, and probed capabilities;
- exact suite source SHA for each loaded suite;
- GraphCheck and pack versions;
- UTC start/finish timestamps;
- run id;
- selected suite ids/tags/fail-fast mode;
- compiled Cypher with placeholders;
- resolved parameter values;
- per-check start time/duration; and
- sample population/size/confidence interval when applicable.

## Error taxonomy

All structured errors contain `{code,message,fix}`. Principal engine/command codes are:

| Code | Meaning |
| --- | --- |
| `run.invalid_selector` | Selector is not `tag:<name>` |
| `run.checks_missing` / `run.checks_unreadable` | Configured suite directory cannot be used |
| `run.suite_invalid` / `run.configuration` | Project suite or setup is invalid |
| `run.artifact_failed` | Result/report artifact could not be written (console error, exit 3) |
| `engine.duplicate_suite` | Two loaded suites resolve to the same suite id |
| `engine.target_missing` | No target supplied and connector cannot probe one |
| `engine.unsupported_pattern` / `engine.compiler_missing` | No compiler exists for the loaded check |
| `engine.invalid_check` / `engine.invalid_target` | Normalized check cannot produce a valid plan |
| `engine.empty_query` / `engine.parameter_missing` | Competency Cypher is empty or lacks a declared parameter |
| `engine.parameter_token_unknown` / `engine.parameter_token_unresolved` | Graph-relative parameter cannot resolve |
| `engine.metric_unsupported` | Drift metric has no compiler |
| `engine.baseline_missing` / `engine.baseline_invalid` | Required baseline measurement is absent or invalid |
| `engine.baseline_partial_missing` | Partial baseline did not collect the requested measurement |
| `engine.connector_invalid` | C2-compatible read method is absent |
| `engine.schema_reference_missing` | Label/relationship type does not exist on the target |
| `engine.invalid_query_result` | Query returned a malformed or inconsistent evaluator shape |
| `engine.evidence_missing` | Row-level failed assertion has no real graph pointer |
| `engine.tolerance_unsupported` / `engine.tolerance_invalid` | Drift tolerance cannot be evaluated |
| `engine.sampling_invalid` | Population/sample plan is malformed |
| `engine.check_unobservable` | Requested rule cannot be observed accurately in Cypher |
| `engine.timeout` | Shared run deadline is exhausted |
| `engine.internal_error` | Unexpected component exception was isolated |
| `neo4j.*` | SPEC-03 connection, auth, permission, database, or query error |

An errored check always retains its declared severity for scoring/exit gating, but it never becomes
`pass`, `fail`, or `warn` and carries no measured/evidence payload.

## Acceptance and verification

Automated coverage includes:

- strict SPEC-02 parsing and normalized pack defaults;
- deterministic parameterized compilation and every C3 callback registration;
- driver-enforced read-only execution and write rejection;
- missing-schema, broken-query, timeout, and one-bad-check isolation paths;
- every competency shape/regression predicate;
- every supported drift tolerance and C4 unit rule;
- raw, typed, nested, path, capped, truncated, and missing evidence behavior;
- generated, not-run, partial-baseline, tag-selection, empty-selection, and fail-fast semantics;
- deterministic SHA seed/sample selection and Wilson interval metadata;
- command artifacts, offline HTML, summaries, and exit codes; and
- reproducibility metadata in the frozen SPEC-01 shape.

Hypothesis generates randomized competency, conformance, and drift evaluator inputs and asserts
shape invariants, exact/bag semantics, evidence requirements, and deterministic outcomes for the
same suite/graph rows.

Neo4j integration tests are enabled with `GRAPHCHECK_NEO4J_INTEGRATION=1` and cover parameter
round-trip, broken-query isolation, missing-label errors, and write rejection through a real C2
session.

The opt-in performance test requires a preloaded target of at least 10 million nodes through
`GRAPHCHECK_PERFORMANCE_URI` and `GRAPHCHECK_PERFORMANCE_PASSWORD`. It runs 30 representative
competency/drift checks and requires a complete result in under five minutes. The default 295-second
engine budget reserves the remaining wall time for artifact serialization/reporting.

## Deferred v0 integration

End-to-end assertions against `tests/fixtures/fraud-ring.cypher` remain deferred until that fixture
lands on this branch. SPEC-01 reserves `skipped:unsupported` for capability preflight gaps; the
current engine run does not yet emit capability-based unsupported skips. SPEC-03 debug preflight
does load `requires` from validated pack `.yml`/`.yaml` metadata and names blocked suite/checks;
the installed core pack currently declares only `read`, so it has no live APOC-backed check until
the corresponding pack manifest and implementation land.

## Deliverables

- `src/graphcheck/engine/` — compiler, core-pack bridge, executor, evaluator, sampling, baseline,
  parameter, and run orchestration modules.
- `src/graphcheck/cli.py` — `graphcheck run` project command.
- `src/graphcheck/reporting/` — validated JSON writer and self-contained HTML renderer consumed by
  the run command.
- `tests/engine/` and `tests/property/` — unit and property-based coverage.
- `tests/integration/test_integration_engine.py` — opt-in real-Neo4j engine coverage.
- `tests/performance/test_engine_budget.py` — opt-in 10M-node/30-check budget contract.
- `tests/test_run_cli.py` — selection, artifacts, summary, connection/configuration, and exit-code
  coverage.
