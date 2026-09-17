# Agent authoring findings

The [fixed fraud-ring trial](../../tools/agent-authoring-benchmark/RESULTS.md) evaluated ten intents
per model at max effort. Every submission validated and loaded; only 21 of 30 returned the expected
verdict. These are authoring outcomes from the original frozen guide, before the changes below.

| Rank | Recurring failure | Observed submissions | Implemented fix |
| --- | --- | --- | --- |
| 1 | Wrong projection for single-column regression values | 4: Luna/Sol, intents 07 and 08 | [Regression values](agent-authoring-fixes/regression-values.md): guide examples and schema field descriptions |
| 2 | Domain IDs returned as the only finding identity | 3: all models, intent 02 | [Finding identities](agent-authoring-fixes/finding-identities.md): exact element aliases in guide/schema and corrected error hint |
| 3 | Aggregate count assertion cannot produce evidence on failure | 3: all models, intent 07 | [Aggregate evidence](agent-authoring-fixes/aggregate-evidence.md): witness/row-count alternatives and assertion-preserving diagnostic |

Counts describe root causes, not disjoint score rows: Luna/Sol intent 07 has both the projection
and aggregate-evidence defects. Intent 08's wrong projection also triggers missing evidence,
but is classified under projection rather than counted again under finding identity.

The fourth observed mistake, Luna's reversed cardinality population, occurred once. It and latent
weaknesses in passing checks are recorded in
[remaining authoring failures](agent-authoring-fixes/remaining-authoring-failures.md).

The changes clarify existing contracts; they do not alter which YAML or verdicts are accepted.
`check.schema.json` and its envelope schema are regenerated from the Pydantic field descriptions.
Single-column scalar values can themselves be lists or maps, so narrowing those schema types would
reject legitimate queries. `engine.evidence_missing` keeps its error code and now names the exact
element aliases and the failed assertion instead of recommending arbitrary `*_id` columns.

The original YAML and original results remain unchanged. The separate
[remediation examples](../../tools/agent-authoring-benchmark/remediations.yml) demonstrate the
proposed repairs against the same fixture. This verifies the repairs, not an improvement in model
performance; a new trial with fresh sessions and the revised guide is needed to measure that.
