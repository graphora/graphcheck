# SPEC-03 — connector

*Frozen for v0.* The Neo4j adapter is the Week 1 connection layer. It supports project-local
profiles, read-only query execution, connection health checks, capability probing, and the stable
`graphcheck debug --json` trace. A GraphCheck project is a directory containing `graphcheck.yml`.
Commands discover the project root by walking upward from the current working directory until
`graphcheck.yml` is found.

## `graphcheck init`

`graphcheck init` scaffolds a project in the current directory:

- `graphcheck.yml` - project config.
- `profiles.yml` - connection credentials; gitignored.
- `checks/example.yml` - an example suite.
- `.graphcheck/` - run artifacts; gitignored.

It also ensures `profiles.yml` and `.graphcheck/` are present in `.gitignore`.

The init command attempts to connect to `bolt://localhost:7687` using the generated local profile.
If a local Neo4j instance is reachable, it reports the server version and performs the APOC
capability check. If not, init still writes the project files and tells the user to edit
`profiles.yml` and run `graphcheck debug`.

## `graphcheck.yml`

The v0 project config is intentionally small:

```yaml
project: graphcheck
checks: checks
artifacts: .graphcheck
concurrency: 1
```

Unknown keys and non-positive/non-integer concurrency values are rejected when the config is
loaded. `graphcheck run --concurrency N` has precedence over the project value.

## `profiles.yml`

`profiles.yml` lives next to `graphcheck.yml` and is never committed.

```yaml
default: local
profiles:
  local:
    uri: bolt://localhost:7687
    user: neo4j
    password: graphora
    password_env: NEO4J_PASSWORD
    database: neo4j
```

Rules:

1. The selected profile is `--profile` when provided, otherwise `default`.
2. A missing `profiles.yml`, missing selected profile, malformed YAML file, or unknown key is a loud
   error.
3. `password` is allowed because `profiles.yml` is gitignored.
4. `password_env` is optional. If present and the environment variable exists, it overrides
   `password`. If present but unset, GraphCheck falls back to `password`. If neither resolves to a
   value, loading the selected profile raises `profile.password_missing`.
5. `uri`, `user`, and `database` are required for every profile.
6. The URI scheme is one of `bolt`, `bolt+s`, `bolt+ssc`, `neo4j`, `neo4j+s`, or `neo4j+ssc`, and
   the URI must include a host. `bolt://` is the generated direct/local default; `neo4j+s://` is
   the CA-validated TLS/routing form.
7. The selected credential is a dedicated, server-enforced read-only audit credential. Init,
   debug, and CLI run inspect `SHOW USER PRIVILEGES` and reject graph-write grants or write-capable
   built-in roles as `neo4j.credential_not_read_only`. Missing privilege evidence fails closed as
   `neo4j.credential_read_only_unverified`.

## Driver wrapper

The adapter uses the official Neo4j Python driver. It owns the connect / verify / close lifecycle
and exposes:

```python
run_read(query: str, params: dict | None = None) -> list[dict]
run_read_result(query: str, params: dict | None = None, *, timeout_s: float | None = None)
run_read_result_bounded(
    query: str,
    params: dict | None = None,
    *,
    policy: ResultPolicy,
    timeout_s: float | None = None,
    stop_when: Callable[[dict], bool] | None = None,
)
read_transaction(*, timeout_s: float | None = None)
```

Read sessions use Neo4j read access mode for routing. Driver access mode is not an access-control
boundary. The CLI connection preflight therefore inspects the supplied audit credential's reported
privileges, and `run_read_result` separately asks the server to plan `EXPLAIN <query>` before it
executes customer-authored Cypher. Only query type `r` executes; write, read/write, schema, missing,
or unknown classifications fail closed. GraphCheck does not parse Cypher or use a keyword blocklist.

`ResultPolicy(max_rows, require_complete)` bounds retained rows. Bounded results expose `rows`,
`columns`, `complete`, `observed_rows`, `limit`, notifications, server timings, and read-guard
timing. `observed_rows` is exact only when `complete` is true; otherwise it is a lower bound.
Reaching `max_rows` while completeness is required raises `engine.result_limit_exceeded`. A
caller-supplied `stop_when` may end an already-decisive read earlier.

`read_transaction` yields the same planner-verified result interface over one explicit read
transaction. Conditional measurement/evidence plans use it so both queries observe one graph
snapshot and share the original monotonic deadline.

Successful read classifications are cached only on the owning `Neo4jClient`, keyed by exact query
text and database. The per-client LRU holds at most 256 entries, shares one in-flight preflight
between concurrent identical reads, never caches rejected, unknown, timed-out, or failed
classifications, and is cleared when the client closes. Query parameters are excluded because
Neo4j classifies the query structure; live connector coverage verifies that changing parameter
values does not change this behavior. Query-free hit, miss, size, and in-flight metrics are
available through `read_guard_cache_info`.

The CLI sizes `max_connection_pool_size` to effective concurrency. The driver uses explicit
10-second connection and acquisition timeouts, fetch size `1000`, and retry budget `0`; query
timeouts continue to use the engine's shorter remaining deadline.

Early termination never calls `Result.consume()`. Neo4j Python driver 6.2 consumes an outstanding
auto-commit result during a normal `Session.close()`, so the adapter exits the session through its
exceptional cleanup path after capturing the bounded result. This marks the session failed, skips
the driver's auto-result consume step, fetches only already-pending protocol messages, and
disconnects deterministically. Complete reads still consume their summary so notifications and
server-consumed timing remain available. The original eager methods retain their existing behavior.

## Error taxonomy

Adapter errors use the same `{ code, message, fix }` shape as SPEC-01 `CheckError`.

| Code | Meaning |
| --- | --- |
| `profile.missing` | `profiles.yml` was not found in the project root. |
| `profile.invalid` | `profiles.yml` or `graphcheck.yml` is malformed or has unknown keys. |
| `profile.not_found` | The selected profile name does not exist. |
| `profile.password_missing` | The selected profile has no resolved password. |
| `profile.uri_invalid` | The selected profile URI has a missing/unsupported scheme or host. |
| `checks.invalid` | A check suite could not be loaded while building the debug trace. |
| `neo4j.unreachable` | The Bolt endpoint cannot be reached. |
| `neo4j.auth_failed` | Credentials were rejected. |
| `neo4j.database_not_found` | The configured database does not exist or is unavailable. |
| `neo4j.tls_mismatch` | The endpoint TLS/certificate mode does not match the URI scheme. |
| `neo4j.credential_not_read_only` | Reported privileges include graph writes or an elevated built-in role. |
| `neo4j.credential_read_only_unverified` | Reported current-user privileges were unavailable. |
| `neo4j.unsupported_version` | The server predates the supported Neo4j 5/CalVer lines. |
| `neo4j.permission_denied` | Credentials do not permit the requested read/probe. |
| `neo4j.query_failed` | A read query failed after connection succeeded. |
| `neo4j.write_rejected` | Neo4j's planner classified the submitted query as write-capable. |
| `neo4j.read_guard_unavailable` | The server/driver did not provide a usable query-type classification. |
| `engine.schema_reference_missing` | Neo4j reports an unknown label, relationship type, or property key. |

## Capability probe

The probe returns a `RunTarget` compatible with SPEC-01:

```json
{
  "database": "neo4j",
  "server_version": "5.18.0",
  "edition": "community",
  "fingerprint": "...",
  "capabilities": {
    "apoc": true,
    "count_store": true
  }
}
```

APOC is binary: `true` only when an APOC procedure can be called successfully. The CLI performs
this APOC procedure check during both `graphcheck init` and `graphcheck debug` so setup feedback
and the stable debug trace report the same live capability.

One `Neo4jClient` represents one command scope. Its first successful complete probe is cached for
the rest of that client's lifetime, and concurrent callers share that in-flight work. Failed probes
are not cached. A new command constructs a new client and therefore observes current graph counts;
probe state is never shared across clients or persisted.

Neo4j Server 4.4 is a legacy, unsupported target. The probe rejects it with
`neo4j.unsupported_version` and directs the user to Neo4j 5.26 LTS or a documented calendar-version
target. The tested Python driver range is `neo4j>=5.20,<7`; driver 7 is excluded until tested.

`count_store` is `true` only when GraphCheck can verify that a simple count query is planned with
a count-store operator. The v0 probe uses `EXPLAIN MATCH (n) RETURN count(n) AS count` and looks
for `NodeCountFromCountStore` in the plan.

When read visibility is available, the debug path reports total node and relationship counts:

```cypher
CALL { MATCH (n) RETURN count(n) AS nodes }
CALL { MATCH ()-[r]->() RETURN count(r) AS relationships }
RETURN nodes, relationships
```

The two compatible count-store reads execute as one request and one snapshot. Server metadata,
schema tokens, privileges, APOC, and count-store planning remain independently distinguishable
requests because their permission and fallback behavior differs. Capability checks are deferred
until connectivity is established; count-store planning is omitted when full read visibility is
unavailable. Public complete probes still resolve both capability booleans rather than treating an
unprobed capability as `false`.

On Enterprise Edition, the probe checks the current user's effective graph privileges independently
of these count queries. Full read visibility requires unrestricted access to all properties on both
`NODE(*)` and `RELATIONSHIP(*)`; label-, relationship-type-, property-, or pattern-scoped grants do
not satisfy it, and any applicable scoped denial makes it false. This distinguishes an empty graph
from Neo4j's security-filtered empty view for a user without full graph read privileges. If read
visibility is absent, or permission is denied while loading the counts, debug continues with
`can_read: false` and both count values set to `null`.

Privileges reported with `graph: "HOME"` are resolved using `SHOW HOME DATABASE`. They apply only
when the configured database name or alias resolves to the current user's home database, so home
grants and denials participate in the same full-visibility evaluation as named and wildcard grants.

The human output also reports what the credentials can and cannot see from the successful probe:
connectivity, read access, and procedure visibility. JSON debug output additionally reports the
live probe's `round_trips`, aggregate `elapsed_ms`, and whether the result was a per-client
`cache_hit`. Request durations remain query-free internal diagnostics.

When a loaded check suite references a check whose pack declares a capability requirement that the
target does not satisfy, debug reports the suite id, check id, check type, missing capability, and a
fix. Each missing capability produces a separate blocker. The run path consumes the same catalog
and turns any missing declared capability into `skipped:unsupported`, marks the run partial, and
does not submit the blocked query. Failures after a query is attempted remain `errored`.

Capability requirements come from the validated `requires` entries in packaged check-pack `.yml`
and `.yaml` metadata, not from a CLI-maintained table. The catalog is loaded through SPEC-09's
strict, duplicate-key-rejecting metadata parser. Invalid pack metadata fails debug with
`packs.invalid`; a registered conformance check with no metadata capability declaration fails with
`packs.requirements_missing`. Neither condition may silently produce an empty blocker list. The
supported requirement vocabulary is:

- `read`
- `show_procedures`
- `apoc`
- `count_store`
- `store_consistency` (reserved for a connector/store integrity probe; unavailable through Cypher)

Debug scans both `*.yml` and `*.yaml` files in the configured checks directory and in the packaged
pack-metadata catalog. Checks whose effective `generated` flag is `true` are validated by the
loader but are not reported as active blockers. If, for example, a pack changes a referenced
check's requirements from `[read]` to `[read, apoc]`, an unchanged suite using that check is named
as blocked whenever the live probe reports `apoc: false`.

## Stable debug JSON

`graphcheck debug --json` emits the following trace. Debug verifies connectivity, server metadata,
APOC usability, count-store usability, graph counts, and check capability blockers.

Success:

```json
{
  "ok": true,
  "profile": "local",
  "target": {
    "database": "neo4j",
    "server_version": "5.18.0",
    "edition": "community",
    "fingerprint": "...",
    "capabilities": {
      "apoc": true,
      "count_store": true
    }
  },
  "visibility": {
    "can_connect": true,
    "can_read": true,
    "can_show_procedures": true
  },
  "counts": {
    "nodes": 0,
    "relationships": 0
  },
  "probe": {
    "round_trips": 5,
    "elapsed_ms": 21,
    "cache_hit": false
  },
  "versions": {
    "graphcheck": "0.1.0",
    "neo4j_driver": "6.2.0",
    "neo4j_server": "5.18.0",
    "cypher": "5"
  },
  "blocked_checks": [
    {
      "suite": "example",
      "check_id": "apoc-backed-completeness",
      "check": "completeness",
      "missing_capability": "apoc",
      "fix": "Install APOC for this Neo4j DBMS, restart Neo4j, then run `graphcheck debug` again."
    }
  ]
}
```

Failure:

```json
{
  "ok": false,
  "profile": "local",
  "error": {
    "code": "neo4j.auth_failed",
    "message": "Neo4j rejected the credentials for profile 'local'.",
    "fix": "Edit profiles.yml with the password from Neo4j Desktop, then run `graphcheck debug`."
  }
}
```
