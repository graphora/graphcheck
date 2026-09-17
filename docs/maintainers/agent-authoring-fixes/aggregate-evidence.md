# Fix: retain evidence when a count assertion fails

All three intent-07 queries returned only `count(c)`. The fixture contains 1,507 Customers, so
the requested 1,500 assertion should fail. Instead, each returned `engine.evidence_missing`.
Terra's `equals: [1500]` was correctly shaped and still errored, isolating the evidence defect
from Luna/Sol's separate [regression projection error](regression-values.md).

For small graphs, `MATCH (c:Customer) RETURN c` with `expect.rows.exactly: 1500` checks the population
and retains evidence. For a scalar comparison, compare the actual count in Cypher and return a
real witness only on mismatch:

```cypher
MATCH (c:Customer)
WITH count(c) AS actual_count
WHERE actual_count <> $expected_count
OPTIONAL MATCH (witness:Customer)
RETURN witness, actual_count
LIMIT 1
```

Use `params: {expected_count: 1500}` and `expect: {empty: true}`. This is a value comparison inside
the query with a shape assertion over violations, rather than an `equals` regression overlay.
The witness is a member of the measured population, not necessarily the cause of the mismatch.
The `aggregate-witness` check in
[remediations.yml](../../../tools/agent-authoring-benchmark/remediations.yml) uses this approach and
must return `fail` on the frozen fixture.

An empty Customer population yields a null witness and remains `errored`, not a false pass.
Competency queries cannot manufacture aggregate pointers. Supporting a pointer-free aggregate
finding requires a separate engine/contract design; it is not silently enabled by this fix.

Implemented in the [agent guide](../../guides/agents.md), [llms.txt](../../llms.txt), the
`CompetencyCheck.query`/`Expect.equals` schema descriptions, and the missing-evidence diagnostic.
The diagnostic retains the underlying failed assertion and suggests rows or a real graph witness.
