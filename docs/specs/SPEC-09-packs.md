# SPEC-09 — Packs

*Frozen for v0.* Packs define reusable check types, their strict suite payloads, executable
template bindings, capability requirements, sampling declarations, and evidence contracts. They
are data-first artifacts consumed through SPEC-02 check YAML and the C1 engine.

## Scope

This spec covers the built-in core conformance pack and the separate executable PII heuristic
pack. C1 still owns Cypher implementation, read-only execution, verdict construction, result
scoring, and fixture-graph acceptance.

## Status

This is the complete built-in C3 contract. Both packs load through the public suite registry and
the validated runtime catalog. C1 compiles and evaluates all observable core templates plus the
PII name/value templates, including Luhn and Verhoeff validation. Both `graphcheck debug` and
`graphcheck run` consume capability declarations from the same manifests. Fixture-graph assertions
remain deferred until the fixture lands.

## Relationship To SPEC-02

SPEC-02 owns the user-authored suite envelope under `checks/`.

```yaml
conformance:
  - id: customer-tax-id-present
    check: completeness
    with: { label: Customer, property: tax_id, threshold: 1.0 }
```

The `check` value selects a pack-owned `with` schema from
`graphcheck.packs.REGISTRY`. The SPEC-02 loader validates the selected `with`
payload, normalizes defaults, and keeps the validated payload on
`LoadedCheck.spec.with_`.

Pack metadata files do not replace user check YAML. They declare the built-in check types and bind
each check name to a registered C1 template. At runtime, `graphcheck.packs.catalog` discovers both
`.yml` and `.yaml` manifests, validates them through `graphcheck.packs.metadata`, rejects duplicate
check declarations, and exposes one catalog to compilation and capability preflight. A suite model
without manifest metadata, or a manifest template without a compiler callback, fails loudly.

## Metadata Contract

The strict Pydantic models in `src/graphcheck/packs/metadata.py` are the source of
truth for pack metadata. `docs/specs/pack.schema.json` is generated from their
discriminated `PackMetadata` union and stamped with `x-pack-version`.

The contract validates the complete nested shape of both built-in packs. Every
model rejects unknown fields, all required fields are explicit, pack and
confidence values are literals, core capabilities are limited to `read`,
`show_procedures`, `apoc`, and `count_store`, and PII checksum values are limited
to `luhn` and `verhoeff`.
Check metadata uses distinct sampled and unsampled variants: `sampled: true`
requires `estimate.required_when_sampled: true`, while unsampled entries reject
`estimate` metadata. These boolean literals require actual JSON/YAML booleans;
integer `0`/`1` values are never coerced to `false`/`true`. The same rule applies
to PII `sample_required: true`.

The generated schema also publishes the typed cross-field and collection
invariants: every built-in check's `template` must match its key, capabilities and
evidence entries are unique, the PII report fields are the exact frozen set, and
PII pattern IDs are unique. PII patterns are ID-keyed objects rather than arrays,
so ID uniqueness follows from standard JSON object-key semantics without a
custom keyword. The published contract declares the standard Draft 2020-12
dialect. Because Draft 2020-12 treats `format` as an annotation by default,
consumers must enable its standard format checker to assert `format: regex`;
Python consumers can use `validate_pack_metadata_schema`.

Python consumers must parse pack YAML through `load_pack_metadata_yaml`, which
uses the same safe, duplicate-key-rejecting loader as suite YAML. Consumers in
other runtimes must reject duplicate mapping keys equivalently. This matters for
the ID-keyed PII pattern objects: permissive YAML loaders can otherwise overwrite
an earlier pattern before typed or JSON Schema validation sees it.

The same contract is used at build/test time and by C1's capability catalog at
runtime. The catalog discovers packaged `.yml` and `.yaml` manifests and parses each
through `load_pack_metadata_yaml`; it does not introduce a second, permissive
metadata shape. Invalid manifests and missing capability declarations fail loudly
rather than making a check appear runnable.

## Pack Invariants

1. Packs are data-only YAML metadata plus strict metadata and Python `with`
   schemas. Pack YAML must not contain executable code or arbitrary Python
   objects.
2. Built-in conformance check schemas are registered in
   `graphcheck.packs.REGISTRY`.
3. Registered `with` models are strict (`extra="forbid"`) and flat/ref-free so the
   generated SPEC-02 combined schema can inline them.
4. `docs/specs/check.schema.json` is regenerated whenever the registry changes.
   `docs/specs/check.envelope.schema.json` remains the frozen envelope schema;
   `docs/specs/pack.schema.json` publishes the generated metadata contract.
5. Every executable check metadata entry declares what it catches, what it does not
   catch, required capabilities, whether it is sampled, and evidence pointer
   fields.
6. Runtime fail/warn results must include evidence pointers. This spec declares
   the evidence contract; C1 enforces it during evaluation.
7. Runtime execution failures must produce `errored` results, not silent skips or
   optimistic passes. C1 owns that behavior.
8. A missing declared target capability prevents execution and produces an explicit
   `skipped:unsupported` result plus partial-run metadata. It is not a pass and is not an attempted
   query error.
9. The manifest `sampled` declaration must agree with the compiled plan. A disagreement is
   `packs.runtime_mismatch`.

## Core Conformance Pack

The core pack lives at `src/graphcheck/packs/core.yml`. Its `with` schemas live in
`src/graphcheck/packs/__init__.py`.

The built-in core conformance checks are:

| Check | Purpose |
| --- | --- |
| `completeness` | Required or high-coverage properties are missing from nodes with a target label. |
| `cardinality` | A relationship expected exactly N times exists zero, too few, or too many times. |
| `no_orphans` | Nodes that should be connected have no matching relationship. |
| `dangling_rels` | Relationship records have endpoints that cannot be resolved by the backing store. |
| `property_type` | Property values have a runtime type different from the declared type. |
| `property_format` | String properties do not match the configured regex. |
| `value_in_set` | Property values fall outside an allowed finite set. |
| `uniqueness` | A property expected to be unique is duplicated within a label. |
| `hub_outlier` | A node degree is far above the configured population norm. |
| `label_cooccurrence` | Two mutually exclusive labels appear on the same node. |
| `rel_direction` | A relationship's endpoints imply the relationship points the wrong way. |
| `temporal_sanity` | An end timestamp is earlier than a start timestamp. |

Each entry in `core.yml` has this shape:

```yaml
check_name:
  catches: Human-readable description of what this check detects.
  does_not_catch: Human-readable boundary of what this check does not prove.
  requires: [read]
  sampled: false
  evidence:
    elements: [node]
    id_fields: [node_id]
  template: check_name
```

`sampled: true` checks must also declare an estimate contract:

```yaml
estimate:
  required_when_sampled: true
```

Currently `hub_outlier` is the only sampled core check.

## Core `with` Schemas

The registry exposes these required payload shapes:

```yaml
completeness:
  label: Customer
  property: tax_id
  threshold: 1.0

cardinality:
  from_label: Customer
  rel_type: OWNS
  to_label: Account
  direction: out
  exactly: 1

no_orphans:
  label: Account
  rel_type: OWNS
  direction: any

dangling_rels:
  rel_type: OWNS

property_type:
  label: Customer
  property: age
  type: integer

property_format:
  label: Customer
  property: tax_id
  regex: "^\\d{9}$"

value_in_set:
  label: Customer
  property: status
  values: [active, closed]

uniqueness:
  label: Customer
  property: customer_id

hub_outlier:
  label: Customer
  rel_type: TRANSFERS_TO
  direction: any
  z_threshold: 3.0
  sample_size: 1000

label_cooccurrence:
  label_a: Person
  label_b: Company

rel_direction:
  from_label: Customer
  rel_type: OWNS
  to_label: Account

temporal_sanity:
  label: Employment
  start_property: start_at
  end_property: end_at
```

Defaults and constraints are defined by the registered Pydantic models and appear
in `docs/specs/check.schema.json`.

## PII Pack

The PII pack lives at `src/graphcheck/packs/pii.yml`. It is separate from the core
conformance pack. Its two executable checks use SPEC-02's existing `conformance` collection so the
frozen suite envelope and SPEC-01 pattern enum do not change.

PII findings are heuristic and sampled. Output must never claim complete PII
discovery; the pack metadata carries an explicit completeness notice.

```yaml
conformance:
  - id: likely-pii-property-names
    check: pii_name_match
    with:
      label: Customer                 # optional
      patterns: [email, phone]        # optional; defaults to all installed name patterns
      sample_size: 1000               # optional; positive integer

  - id: likely-pii-property-values
    check: pii_value_match
    with:
      label: Customer                 # optional
      properties: [notes, external_id] # optional; restricts property keys
      patterns: [email, credit_card]  # optional; defaults to all installed value patterns
      sample_size: 1000               # optional; positive integer
```

All `with` models are strict. Pattern identifiers are closed literals from the installed PII
manifest, list entries must be unique, and explicitly selected properties are checked against
`db.propertyKeys()`. A missing label/property is an errored schema reference, never an empty pass.

### Name-Match Heuristic

Name-match flags property keys that look like personal data. Findings are labeled
with `confidence: name-match`. `patterns` is an object whose keys are the pattern
IDs and whose values declare the property-key aliases.

The current name-match coverage includes at least:

- `ssn`
- `dob`
- `email`
- `phone`
- `nric`
- `aadhaar`
- `address`
- `passport`
- `credit_card`
- `tax_id`
- `driver_license`
- `bank_account`
- `ip_address`
- `geolocation`
- `biometric`

### Value-Match Heuristic

Value-match samples property values and matches known formats. Findings are
labeled with `confidence: value-match`. Its `patterns` object is likewise keyed
by pattern ID; each value declares a regex and optional checksum.

The current value-match declarations are:

| Pattern | Requirement |
| --- | --- |
| `email` | Regex match. |
| `e164_phone` | E.164 regex match. |
| `nric` | NRIC-format regex match. |
| `aadhaar` | Regex match plus `verhoeff` checksum. |
| `credit_card` | Regex match plus `luhn` checksum. |

Value-match reports must include location, exposure count, and confidence.

### Runtime and privacy contract

Both PII checks first execute a population query, derive the per-check seed defined by SPEC-04,
and select property occurrences in a stable seed-derived order. The engine runs the exact
population when policy/configuration permits; a strict subset carries
`estimate:{sample_size,population,confidence,ci}` using the 95% Wilson interval. Even a sampled
zero-match pass retains estimate metadata and the completeness notice.

Name matching is case-insensitive over the installed alias catalog. Value matching uses full regex
matches and applies a declared Luhn or Verhoeff checksum after the regex succeeds. A finding groups
the pattern, node labels, and property key and reports projected `exposure_count` plus the declared
categorical confidence. Raw matched property values must not appear in `measured`, `expected`,
evidence, console output, JSON, or HTML artifacts.

Every failing PII result contains node evidence pointers. Missing/malformed candidates, a broken
query, timeout, schema warning, population/result disagreement, invalid checksum metadata, or
missing pointer produces `errored`; none can become a pass or a silent skip.

## Runtime consumption

C1 consumes packs in this order:

1. Load user suite YAML through SPEC-02.
2. For each conformance `LoadedCheck`, read `spec.check` and normalized
   `spec.with_`.
3. Look up pack metadata by `spec.check`.
4. Compile the metadata `template` and `with` payload into a read-only query or
   runtime operation.
5. Execute through the read-only graph adapter.
6. Evaluate rows into SPEC-01 `CheckResult` objects.
7. For fail/warn results, use the metadata evidence declaration to construct
   evidence pointers.
8. For runtime failures, emit `verdict: errored` with a structured error.
9. For sampled checks/findings, emit estimate metadata with sample size and
   confidence interval.

The debug preflight performs steps 1–3 for every active conformance check and
compares the metadata `requires` list with the live SPEC-03 capability probe. It
reports the suite/check identity for each missing capability. Effective
`generated:true` checks remain validated but are not reported as active blockers.

The run path performs the same lookup before compilation. Missing `apoc` or `count_store`
capabilities produce `skipped` / `skip_reason: unsupported`, name the affected suite/check in the
partial reason, and do not submit the query. Once a query is attempted, connector/compiler/timeout
failures are `errored` under SPEC-04.

## Deferred Work

Only fixture-graph assertions for planted defects remain deferred because the fixture has not
landed on this branch. No built-in C3 loader, compiler binding, PII evaluator, checksum, sampling,
capability, or evidence path is deferred.

## Verification

The current test coverage for this spec lives in `tests/test_packs.py`,
`tests/contracts/test_check_validation.py`, `tests/engine/test_core_pack_compiler.py`,
`tests/engine/test_pii_pack_runtime.py`, and the opt-in Neo4j integration suite.

The tests assert:

- Both metadata files pass the complete typed and generated JSON Schema contracts.
- Unknown or missing metadata fields and invalid capability/confidence values fail.
- Sampled metadata requires an estimate contract and unsampled metadata rejects it.
- All 12 core and both PII conformance checks are registered and declared by the executable catalog.
- Registered `with` models are strict.
- Whitespace-only identifiers fail through both `load_suite()` and the generated
  combined check schema.
- SPEC-02 `load_suite()` accepts all 14 built-in conformance checks.
- Core and PII pack metadata match the registry and their compiler bindings.
- Runtime capability requirements are read from both `.yml` and `.yaml` pack metadata.
- Missing APOC reports the affected suite/check identity and install action in human and JSON debug
  output.
- Invalid pack manifests fail debug loudly instead of suppressing blockers.
- Every core check declares evidence pointer fields.
- Sampled checks declare an estimate contract.
- The PII pack is separate and declares heuristic limits.
- PII name-match and value-match patterns cover the required categories.
- PII regex/checksum matching, raw-value redaction, evidence, malformed-result errors, exact and
  sampled estimates, missing schema, timeouts, and seed determinism are exercised at engine level.
- Capability gaps become unsupported partial skips without executing a query.
- The generated combined SPEC-02 schema contains all built-in checks.
