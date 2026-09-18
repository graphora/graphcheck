# Neo4j compatibility

GraphCheck keeps the Python driver, Neo4j Server, and Cypher language versions separate. The
tested release matrix for the current release is:

| Dimension | Tested target | Policy |
| --- | --- | --- |
| Python | 3.12, 3.13, and 3.14 | All run the unit gate |
| Neo4j Python driver | 5.20.0 | Minimum supported driver |
| Neo4j Python driver | latest 6.x | Primary driver line |
| Neo4j Python driver | 7.x | Excluded until tested |
| Neo4j Server | 5.26.28 Community and Enterprise | Neo4j 5 LTS target |
| Neo4j Server | 2026.06.0 Community and Enterprise | Selected calendar-version target |
| Cypher | 5 | Tested on 5.26.28 and 2026.06.0 |
| Cypher | 25 | Tested on 2026.06.0 |

The production dependency is bounded to `neo4j>=5.20,<7`. Pull-request CI pairs driver 5.20.0
with Python 3.12 and the latest available driver 6.x with Python 3.14, then runs the focused
driver-facing tests. The separate unit matrix runs the full suite on Python 3.12, 3.13, and 3.14.
This tests both supported edges without repeating every combination, and the upper bound prevents
a future untested driver major from entering dependency resolution. The driver APIs used at the
compatibility floor are `GraphDatabase.driver`, read-access sessions, explicit transactions,
`neo4j.Query` timeouts, result summaries and plans, query-type classification, notifications, and
GQL status objects with compatibility fallbacks.

Server images are exact rather than floating: `neo4j:5.26.28` and `neo4j:2026.06.0`, with matching
`-enterprise` images for the built-in `reader` role gate, effective privileges, HOME database, and
restricted-credential checks. The current server runs separate database-default Cypher 5 and
Cypher 25 lanes. See Neo4j's
[server archive](https://neo4j.com/docs/reference/docs-archive/),
[driver API](https://neo4j.com/docs/api/python-driver/current/), and
[Cypher version configuration](https://neo4j.com/docs/operations-manual/current/configuration/cypher-version-configuration/).

## Supported graph size

**The published audit ceiling is 10,000,000 nodes.** `graphcheck run` rejects a larger
database before dispatching checks. Relationship counts have no separate hard limit.
Density, properties, indexes, server memory and check selection still affect whether
an audit can finish within its budget.

The ceiling is an upper support boundary, **not a claim that the full PII pack completes
at 10M with a 2 GiB transaction-memory limit**. Both PII checks can exceed that shared
limit and return `neo4j.query_failed`. Size eligibility does not guarantee completion or
establish a minimum server-memory configuration. Use the default two workers and
295-second execution budget as starting points when sizing your own workload.

`dangling_rels` requires the separate `store_consistency` capability, which the standard
adapter does not expose. Selecting it still produces `skipped: unsupported` and partial
coverage. Count-store support provides counts rather than verification of broken
relationship records; the diagnostic points to Neo4j's offline consistency checker.

### Which checks sample

| Check | Default transition | Default sample |
| --- | --- | ---: |
| `hub_outlier` | More than 100,000 nodes with its configured label | 1,000 nodes |
| `pii_name_match` | More than 1,000 node-property occurrences in scope | 1,000 occurrences |
| `pii_value_match` | More than 1,000 eligible string-property occurrences in scope | 1,000 occurrences |
| Other executable core checks | Always exhaustive | None |

Explicit check-level sample sizes can lower the transition/sample size. The global 10,000-sample
cap does not override the packs' smaller default. Sampling bounds evaluated evidence;
these Cypher plans still scan the population and do not promise constant runtime or
server memory. PII findings remain heuristic, not proof of complete PII discovery.

### Beyond the ceiling

After the bounded target probe, a count above 10,000,000 produces a failed run with exit
code `3`, no dispatched checks, and this actionable diagnostic:

```text
engine.graph_size_exceeded: Graph size (10000005 nodes, 9500000 relationships) exceeds the supported ceiling of 10,000,000 nodes.
Fix: Audit a smaller database or partition within the node limit. Selecting fewer checks or enabling sampling does not bypass the graph-size ceiling. See docs/reference/compatibility.md#supported-graph-size.
```

The boundary is inclusive: exactly 10,000,000 nodes is permitted. This is a database
count limit, not a per-label or selected-check limit. In-limit expensive runs retain the
295-second budget and timeout diagnostics; passing the size gate does not guarantee completion.

## Neo4j 4.4 policy

Neo4j 4.4 is legacy and unsupported by GraphCheck 0.1. A dedicated hostile-graph lane starts a
three-member 4.4 Enterprise cluster and verifies that `debug`, `profile`, and `run` return
`neo4j.unsupported_version` with a 5.26 LTS upgrade direction and no traceback. It remains absent
from the supported compatibility matrix. GraphCheck evidence identity now uses `elementId()`,
avoiding the deprecated numeric `id()` API.

## Cypher 25 and sampling

Evidence identity, deterministic evidence ordering, uniqueness comparison, drift evidence, and
profile type sampling use opaque `elementId()` strings without parsing their format.

Hub and PII bottom-k sampling deliberately retain numeric `id()` only inside queries explicitly
prefixed with `CYPHER 5`. Their established seeded cubic hash requires a numeric input, while Neo4j
does not guarantee a parseable `elementId()` format. This explicit compatibility path works on a
Cypher 25-default database without silently changing sample selection. A future string-hash
sampling algorithm must be versioned and distribution-tested before those two `CYPHER 5` prefixes
can be removed.

## Support output

`graphcheck debug` reports four distinct values: GraphCheck version, Neo4j Python driver version,
Neo4j Server version, and the configured database's Cypher version. `debug --json` exposes the same
values under `versions`. On calendar-version servers, Cypher mode detection is best effort for
restricted credentials; if database metadata is hidden, it reports `unknown` rather than claiming
Cypher 5 or 25.

## Installed-wheel gate

CI builds the wheel, installs it into a clean environment, runs `graphcheck --version` and
`graphcheck --help`, and verifies the installed core and PII pack resources. This catches source
tree assumptions that the regular development environment would hide.
