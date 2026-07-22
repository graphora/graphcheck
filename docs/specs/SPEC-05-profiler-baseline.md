*Frozen for v0.*

The Profiler inspects a Neo4j graph and produces a canonical baseline snapshot. A baseline captures graph identity, schema, structural statistics, and a deterministic fingerprint. It serves as the source artifact for drift detection, `graphcheck generate`, reports, and future cloud features.

The baseline format version is identified by `schema_version`, which is frozen to `"1.0"` for v0 and versioned independently of `graphcheck_version`.

---

## Source of Truth

The source of truth is the Pydantic model at

```
src/graphcheck/contracts/profile.py
```

The generated JSON Schema is

```
docs/specs/profile.schema.json
```

The generated schema is structural only. Semantic invariants are enforced by the Pydantic model validators.

Schema-valid ≠ contract-valid.

Machine-valid fixture:

```
tests/contracts/fixtures/baseline.json
```

---

# Responsibilities

The Profiler SHALL

- inspect graph schema
- collect structural statistics
- collect graph metadata
- compute a deterministic fingerprint
- produce canonical baseline files
- operate in read-only mode

The Profiler SHALL NOT

- execute graph quality checks
- evaluate PASS / FAIL / WARN
- compare baselines
- compute drift
- modify the graph

---

# CLI

The Profiler is invoked using

```bash
graphcheck profile
```

The command produces a canonical, sorted JSON baseline at

```
.graphcheck/
    baselines/
        <timestamp>.json
```

Profiling SHOULD complete within the configured wall-clock budget (default 60 seconds).

---

# Top-Level Shape

```text
{
    schema_version,
    status,
    partial_reason,
    target,
    metadata,
    schema,
    statistics,
    fingerprint
}
```

Every model forbids unknown keys (`extra="forbid"`).

---

# schema_version

Identifies the baseline format version.

- Fixed to `"1.0"` for v0.
- Versioned independently of `graphcheck_version`.
- Incremented only when the baseline contract changes.

---

# Status

`status` describes whether the baseline is complete.

Allowed values:

```
complete
partial
```

A `complete` baseline means every required profiler collection succeeded within the wall-clock
budget.

A `partial` baseline means the profiler established the target identity and collected the core graph
counts, but one or more non-fatal schema or statistics probes did not complete. Partial baselines are
allowed because the briefing requires incomplete profiles to be labeled rather than silently
truncated.

`partial_reason` is present in every baseline. It is `null` when `status` is `complete` and a
non-empty human-readable string when `status` is `partial`.

Partial baselines keep the canonical top-level shape. Successfully collected collections and
statistics are emitted normally. Collections that could not be collected are emitted as empty arrays.
Optional or expensive measurements that could not be collected are emitted as `null` where the model
allows nullable measurements.

Consumers MUST NOT treat empty arrays or nullable measurements in a `partial` baseline as complete
graph truth. `graphcheck profile` still writes partial baselines so incomplete collection remains
explicit. Partial baselines are not comparable in v0. If either input baseline is partial,
`graphcheck diff` returns a controlled `diff.partial_baseline` comparison-inconclusive error with
exit code 2 and does not perform drift detection.

---

# Target

`target` is the **RunTarget** supplied by the Connector (SPEC-03).

The Profiler consumes this object unchanged and MUST NOT derive or modify target metadata.

---

# Metadata

Contains metadata describing the profiling run.

```
generated_at
graphcheck_version
```

---

# Schema

Represents the graph inventory.

Contains

```
labels[]

relationship_types[]

constraints[]

indexes[]
```

---

## Label

Each label is represented as

```text
{
    name,
    count,
    properties[],
    degree_distribution
}
```

where

- **name** — node label
- **count** — number of nodes with this label
- **properties** — property inventory for the label
- **degree_distribution** — degree distribution for nodes with this label

For complete profiles, each label MUST contain a non-null `degree_distribution`.
For partial profiles, a label's `degree_distribution` MAY be `null`.

---

## Property

Each property contains

```text
{
    name,
    type
}
```

where

- **name** — property key
- **type** — observed property type

---

## Relationship Type

Each relationship type contains

```text
{
    name,
    count
}
```

where

- **name** — relationship type
- **count** — number of relationships of this type

---

## Constraints

Canonical representation of Neo4j constraints.

---

## Indexes

Canonical representation of Neo4j indexes.

---

# Statistics

Statistics describe graph measurements only.

They include

```
node_count

relationship_count

property_coverage
```

---

## Property Coverage

Property coverage represents the percentage of nodes (or relationships, where applicable) containing a given property.

Coverage values are represented as percentages.

---

## Degree Distribution

Degree distribution contains

```
median

p95

p99

maximum
```

Degree distribution is recorded per label. It MAY be `null` in a partial baseline when the
degree probe exceeds the wall-clock budget or fails after core counts have been collected.

Statistics are descriptive only.

They never imply graph quality and never produce PASS, FAIL or WARN verdicts.

---

# Fingerprint

The fingerprint uniquely identifies the profiled graph.

It MUST be deterministic.

Identical graph structure and statistics MUST produce identical fingerprint values.

Different graph structure or statistics SHOULD produce different fingerprint values.

The v0 fingerprint algorithm is SHA-256.

The serialized fingerprint value is formatted as

```text
sha256:<64 lowercase hex characters>
```

The v0 fingerprint input is canonical JSON containing:

- labels
- relationship types
- node_count
- relationship_count

The canonical JSON input uses sorted object keys and compact separators. The ordered arrays in the
baseline are used as-is after canonical-order validation.

Constraints and indexes are included in the baseline schema, but they are not part of the v0
fingerprint input. A later baseline schema version may add them to the fingerprint input.

---

# Semantic Rules

1. Profiling is read-only and never modifies the target graph.

2. The Profiler consumes the RunTarget supplied by the Connector (SPEC-03) and MUST NOT derive or modify target metadata.

3. Unknown keys are forbidden (`extra="forbid"`).

4. Collections MUST be canonically ordered so identical graphs produce identical baseline files.

5. Counts MUST be non-negative.

6. Coverage values MUST lie within the closed interval `[0,100]`.

7. The baseline JSON MUST be deterministic and Git-diff friendly.

8. The Profiler produces a graph inventory only. It never evaluates graph quality.

9. Constraints and indexes are part of the graph schema and MUST be included in the baseline.

10. `partial_reason` MUST be `null` iff `status` is `complete`, and non-empty iff `status` is `partial`.

11. A partial baseline MAY be produced only after the Connector target and core counts
    (`node_count`, `relationship_count`) have been collected.

12. Failure to connect, failure to obtain the Connector target, failure to collect core counts, or a
    profiler failure before any usable graph inventory is collected SHALL result in no baseline being
    produced.

13. The Profiler MUST NOT silently truncate output. Any omitted collection or nullable measurement
    caused by timeout, permission, or query failure requires `status: partial`.

---

# Deferred Decisions

The following fingerprint inputs are deferred to a later baseline schema version:

- constraints
- indexes

---

# Deliverables

```
src/graphcheck/contracts/profile.py
```

Pydantic model (source of truth).

```
docs/specs/profile.schema.json
```

Generated JSON Schema.

```
tests/contracts/fixtures/baseline.json
```

Machine-valid baseline fixture.

```
tests/contracts/test_profile.py
```

Validation and invariant tests.
