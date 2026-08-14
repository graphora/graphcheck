# Check reference

GraphCheck suites are YAML files under `checks/` (or wherever `graphcheck.yml`'s `checks` path
points). A suite groups checks into three kinds — conformance, competency, and drift — plus a
shared `defaults` block for severity and tags.

Examples below are drawn from the fraud-ring fixture (`graphcheck-fraud-ring-fixture`) where a
real, planted scenario exists; a few core checks the fixture doesn't exercise use generic
illustrative data instead.

## Conformance checks

Conformance checks assert a structural rule against the graph using a built-in template. The
`check` field selects the template; `with` supplies its parameters.

| Check | Catches | Does not catch |
| --- | --- | --- |
| `completeness` | Required or high-coverage properties missing from nodes with a target label | Whether the property value is valid, unique, or semantically correct |
| `cardinality` | Nodes whose expected relationship count is not exactly the configured value | Whether the related node is otherwise valid, or whether alternate paths satisfy the model |
| `no_orphans` | Nodes with a required label that have no matching relationship | Isolated nodes that are valid by design, or missing relationships of unrelated types |
| `dangling_rels` | Relationship records whose endpoints cannot be resolved by the backing store | Semantically wrong relationships whose endpoints exist |
| `property_type` | Property values whose observed runtime type differs from the declared type | Values of the correct type but invalid format or domain |
| `property_format` | String properties that do not match the configured regular expression | Missing properties, unless combined with `completeness` |
| `value_in_set` | Property values outside an allowed finite set | Values inside the set that are stale or contextually wrong |
| `uniqueness` | Duplicate values for a property expected to be unique within a label | Missing values, or duplicates across labels unless configured separately |
| `hub_outlier` | Nodes whose relationship degree is far above the configured population norm | Legitimate hubs or low-degree anomalies |
| `label_cooccurrence` | Nodes that carry two labels declared mutually exclusive | Label pairs not explicitly configured |
| `rel_direction` | Relationships whose endpoints imply the relationship is pointing the wrong way | Incorrect relationship types where the direction is otherwise valid |
| `temporal_sanity` | Records where an end timestamp is earlier than its start timestamp | Missing timestamps, or timestamps that are plausible but inaccurate |

`hub_outlier` is sampled and supports an optional `sample_size`; every other core check is
unsampled and requires only `read` access, except `dangling_rels`, which additionally requires
the `store_consistency` connector probe.

### No orphans and cardinality — fraud-ring fixture

The fixture plants 3 orphan accounts and 1 cardinality violation (an account with two owners):

```yaml
conformance:
  - id: account-no-orphans
    check: no_orphans
    with:
      label: Account
  - id: account-owner-cardinality
    check: cardinality
    with:
      from_label: Account
      rel_type: OWNS
      to_label: Customer
      direction: in
      exactly: 1
```

### Completeness — fraud-ring fixture

Every base `Customer` carries `tax_id`, `email`, and `national_id`:

```yaml
conformance:
  - id: customer-tax-id-present
    check: completeness
    with: { label: Customer, property: tax_id, threshold: 1.0 }
```

### Hub outlier — fraud-ring fixture

The fixture's `Transaction` nodes have a real degree distribution suited to hub-outlier detection:

```yaml
conformance:
  - id: transaction-hub-outlier
    check: hub_outlier
    with:
      label: Transaction
      sample_size: 500
```

### Checks without a fixture example

The fraud-ring fixture doesn't plant scenarios for `dangling_rels`, `property_type`,
`property_format`, `value_in_set`, `uniqueness`, `label_cooccurrence`, `rel_direction`, or
`temporal_sanity`. A generic example:

```yaml
conformance:
  - id: account-balance-is-numeric
    check: property_type
    with: { label: Account, property: balance, type: integer }
```

## Competency checks

Competency checks run a Cypher query against the graph and assert the shape of its result — for
questions the graph should be able to answer, rather than structural rules.

```yaml
competency:
  - id: customers-can-be-counted
    question: "Can customers be counted?"
    query: "MATCH (c:Customer) RETURN count(c) AS count"
    expect: { rows: { min: 1 }, columns: [count] }
```

## Drift checks

Drift checks compare a metric against a previously captured baseline and flag deviation beyond a
configured tolerance. The fraud-ring fixture's `seed-drifted.cypher` represents a documented 12%
Customer-count reduction (1,500 to 1,320) from the baseline:

```yaml
drift:
  - id: customer-count-stable
    metric: node_count
    target: { label: Customer }
    tolerance: { max_drop_pct: 10 }
    severity: warn
```

## PII pack

The PII pack is a separate, executable heuristic pack. Findings are sampled and heuristic —
this pack never claims complete PII discovery. The fraud-ring fixture plants both a
name-alias field (`national_id`) and value-pattern-matchable data (Singapore NRIC-style and
Indian Aadhaar-style, alternating per customer).

| Check | Catches | Does not catch |
| --- | --- | --- |
| `pii_name_match` | Sampled property occurrences whose keys match known personal-data aliases (for example `ssn`, `dob`, `email`, `aadhaar`, `passport`) | Personal data stored under unknown or ambiguous property names |
| `pii_value_match` | Sampled string property values matching known PII formats and required checksums (email, E.164 phone, NRIC, Aadhaar with Verhoeff checksum, credit card with Luhn checksum) | Encoded, encrypted, unrecognized, or unsampled personal-data values |

Both PII checks are sampled and require an `estimate` for the sampled population, same as
`hub_outlier`.

## Severity and defaults

`defaults.severity` sets the suite-wide severity (`error` or `warn`); individual checks can
override it. Suite-wide `tags` let you select subsets of checks at run time with
`graphcheck run --select tag:<name>`.

See [SPEC-02](specs/SPEC-02-check-yaml.md) for the full check YAML contract, and
[SPEC-09](specs/SPEC-09-packs.md) for how built-in packs are registered and validated.