# Fix: return graph identities with competency findings

All three models wrote a logically correct multiple-owner query for intent 02, then returned
`a.id AS account_id` without the Account entity. Each query found the planted account, but the
engine returned `engine.evidence_missing`, so all three failed the execution and correctness gates.
The business ID is not Neo4j element identity. The old diagnostic incorrectly recommended `*_id`
columns, reinforcing exactly this mistake.

Return `a` alongside any business IDs and counts, or project
`elementId(a) AS node_element_id`. Accepted relationship aliases are `rel_element_id` and
`relationship_element_id`. Returning a real graph entity is generally the clearest choice.
Do not relabel a business ID as `node_element_id` or invent an aggregate pointer.

Implemented in the [agent guide](../../guides/agents.md), [llms.txt](../../llms.txt), the generated
schema's `CompetencyCheck.query` description, and the `engine.evidence_missing` error fix. The
diagnostic now names accepted aliases, rejects arbitrary `*_id` aliases explicitly, and preserves
the failed assertion. Existing evaluator tests verify that domain IDs still cannot become evidence.

The `owner-evidence` check in
[remediations.yml](../../../tools/agent-authoring-benchmark/remediations.yml) returns the offending
Account. Its expected verdict is `fail`, with one graph element identifying `ACC-CARD-0001`.
The three orphan accounts remain outside this rule: an upper bound of one allows zero owners.
