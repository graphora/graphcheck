# Fix: regression values must match the query projection

Luna and Sol each misencoded intents 07 and 08: four submissions in the
[original matrix](../../../tools/agent-authoring-benchmark/RESULTS.md). Luna wrapped scalar values
in row lists (`[[1500]]`); Sol wrapped them in maps (`[{customer_count: 1500}]`). Their Cypher returned
a single integer or string column. Both representations validate and load but compare unequal to
the actual scalar. Intent 08 should pass, so its resulting error cannot be confused with a genuine
fixture defect. Terra used scalar lists correctly.

For a single scalar column, use `equals: [1500]` or
`contains: [ACC-2401, ACC-2408]`. For multiple columns, provide a complete map per expected row.
Use nested lists/maps only when the column itself returns that type. `contains` permits extra rows;
`equals` requires the complete duplicate-preserving bag of values.

Implemented in the [agent guide](../../guides/agents.md), [llms.txt](../../llms.txt), and the
`Expect.contains`/`Expect.equals` field descriptions that generate
[check.schema.json](../../schemas/check.schema.json). The schema remains structurally permissive
because it cannot infer the query's column count or scalar type.

The `regression-projection` check in
[remediations.yml](../../../tools/agent-authoring-benchmark/remediations.yml) uses the original
membership query with a flat scalar list. Its expected verdict on the unchanged fixture is `pass`.
Correcting only the projection for intent 07 is insufficient: the aggregate also needs
[failure evidence](aggregate-evidence.md).
