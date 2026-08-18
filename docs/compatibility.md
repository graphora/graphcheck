# Neo4j compatibility

GraphCheck keeps the Python driver, Neo4j Server, and Cypher language versions separate. The
tested release matrix for GraphCheck 0.1 is:

| Dimension | Tested target | Policy |
| --- | --- | --- |
| Python | 3.12 and 3.13 | Both run the unit gate |
| Neo4j Python driver | 5.20.0 | Minimum supported driver |
| Neo4j Python driver | latest 6.x | Primary driver line |
| Neo4j Python driver | 7.x | Excluded until tested |
| Neo4j Server | 5.26.28 Community and Enterprise | Neo4j 5 LTS target |
| Neo4j Server | 2026.06.0 Community and Enterprise | Selected calendar-version target |
| Cypher | 5 | Tested on 5.26.28 and 2026.06.0 |
| Cypher | 25 | Tested on 2026.06.0 |

The production dependency is bounded to `neo4j>=5.20,<7`. CI installs 5.20.0 and the latest
available 6.x release independently, so a future untested driver major cannot enter dependency
resolution. The driver APIs used at the compatibility floor are `GraphDatabase.driver`,
read-access sessions, explicit transactions, `neo4j.Query` timeouts, result summaries and plans,
query-type classification, notifications, and GQL status objects with compatibility fallbacks.

Server images are exact rather than floating: `neo4j:5.26.28` and `neo4j:2026.06.0`, with matching
`-enterprise` images for the built-in `reader` role gate, effective privileges, HOME database, and
restricted-credential checks. The current server runs separate database-default Cypher 5 and
Cypher 25 lanes. See Neo4j's
[server archive](https://neo4j.com/docs/reference/docs-archive/),
[driver API](https://neo4j.com/docs/api/python-driver/current/), and
[Cypher version configuration](https://neo4j.com/docs/operations-manual/current/configuration/cypher-version-configuration/).

## Neo4j 4.4 policy

Neo4j 4.4 is legacy and unsupported by GraphCheck 0.1. It is absent from CI, and the connector
returns `neo4j.unsupported_version` during the target probe with a 5.26 LTS upgrade direction.
GraphCheck evidence identity now uses `elementId()`, avoiding the deprecated numeric `id()` API.

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
