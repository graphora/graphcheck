# GraphCheck Agent Guide

This document provides guidance for AI agents that generate GraphCheck validation suites.

Use this guide together with `llms.txt`, which contains the GraphCheck reference documentation, supported checks, YAML schema, and examples.

---

# Purpose

Generate a valid GraphCheck YAML suite using only the published GraphCheck documentation.

The generated suite should reflect the documented graph structure and business rules while using only supported GraphCheck features.

---

# Inputs

You may be provided with one or more of the following:

- `llms.txt`
- Graph schema
- Graph profile
- Fixture graph documentation
- Business documentation
- Ontology or data model documentation

Use the supplied documentation to understand the graph before generating a validation suite.

Do not rely on GraphCheck source code or undocumented behaviour.

---

# Objective

Generate valid GraphCheck YAML that:

- Uses only documented GraphCheck packs and checks.
- Uses only documented parameters.
- Follows the published YAML schema.
- Represent validation rules that can be inferred from the supplied documentation.
- Can be reviewed and refined by a human.

---

# Generation Strategy

When generating a suite:

1. Understand the graph model.
   - Identify node labels.
   - Identify relationship types.
   - Identify important properties.
   - Identify business constraints described in the documentation.

2. Select the most appropriate GraphCheck checks.

3. Generate clear, readable, and valid YAML.

4. Prefer multiple focused checks over a single large check.

---

# Choosing Checks

Use the documented GraphCheck conformance library.

For example:

| Graph Observation | Suggested Check |
| ----------------- | --------------- |
| Unique identifier | `uniqueness` |
| Required property | `completeness` |
| Property type constraint | `property_type` |
| Allowed values | `value_in_set` |
| Required relationship | `cardinality` |
| Connected nodes | `no_orphans` |
| Relationship direction | `rel_direction` |
| Timestamp consistency | `temporal_sanity` |
| High-degree anomalies | `hub_outlier` |
| Invalid label combinations | `label_cooccurrence` |

Generate only checks that are documented by GraphCheck.

---

# Best Practices

Prefer:

- Checks that can be derived directly from the supplied documentation.
- Small, focused validation rules.
- Descriptive check identifiers.

Avoid:

- Inventing business rules.
- Assuming undocumented constraints.
- Generating duplicate or overlapping checks.

---

# Constraints

The generated suite must not:

- Invent check names.
- Invent parameters.
- Invent packs.
- Produce invalid YAML.
- Use unsupported GraphCheck features.
- Assume behaviour that is not described in the published documentation.

If the documentation is insufficient to infer a validation rule, omit that rule rather than guessing.

---

# Output Requirements

The generated output should:

- Be valid GraphCheck YAML.
- Follow the published YAML schema.
- Use only supported GraphCheck features.
- Be suitable for human review.

If the generation workflow requires AI-generated checks to be identified, include:

```yaml
generated: true
```

---

# Example Request

> Generate a GraphCheck validation suite for the fraud-ring fixture graph using the supplied GraphCheck documentation.

---

# Example Output

```yaml
suite: Fraud Ring

checks:
  - id: unique-person-id
    generated: true
    pack: core
    check: uniqueness
    with:
      label: Person
      property: personId

  - id: account-owner
    generated: true
    pack: core
    check: cardinality
    with:
      from_label: Person
      rel_type: OWNS
      to_label: Account
      exactly: 1
```

---

# Guiding Principle

Generate only what is supported by the published GraphCheck documentation.

When multiple valid interpretations exist, choose the most conservative option and avoid introducing assumptions that are not explicitly documented.

When documentation is ambiguous or incomplete, omit the corresponding check rather than making assumptions.