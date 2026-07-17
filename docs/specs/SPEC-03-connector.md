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
```

Unknown keys are rejected when the config is loaded.

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

## Driver wrapper

The adapter uses the official Neo4j Python driver. It owns the connect / verify / close lifecycle
and exposes:

```python
run_read(query: str, params: dict | None = None) -> list[dict]
```

All sessions use Neo4j read access mode. GraphCheck does not parse Cypher to detect writes.

## Error taxonomy

Adapter errors use the same `{ code, message, fix }` shape as SPEC-01 `CheckError`.

| Code | Meaning |
| --- | --- |
| `profile.missing` | `profiles.yml` was not found in the project root. |
| `profile.invalid` | `profiles.yml` or `graphcheck.yml` is malformed or has unknown keys. |
| `profile.not_found` | The selected profile name does not exist. |
| `profile.password_missing` | The selected profile has no resolved password. |
| `checks.invalid` | A check suite could not be loaded while building the debug trace. |
| `neo4j.unreachable` | The Bolt endpoint cannot be reached. |
| `neo4j.auth_failed` | Credentials were rejected. |
| `neo4j.database_not_found` | The configured database does not exist or is unavailable. |
| `neo4j.permission_denied` | Credentials do not permit the requested read/probe. |
| `neo4j.query_failed` | A read query failed after connection succeeded. |

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

`count_store` is `true` only when GraphCheck can verify that a simple count query is planned with
a count-store operator. The v0 probe uses `EXPLAIN MATCH (n) RETURN count(n) AS count` and looks
for `NodeCountFromCountStore` in the plan.

When read visibility is available, the debug path reports total node and relationship counts:

```cypher
MATCH (n) RETURN count(n) AS count
MATCH ()-[r]->() RETURN count(r) AS count
```

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
connectivity, read access, and procedure visibility.

When a loaded check suite references a check whose pack declares a capability requirement that the
target does not satisfy, debug reports the suite id, check id, check type, missing capability, and a
fix. Each missing capability produces a separate blocker. The run path consumes the same catalog
and turns a missing declared `apoc` or `count_store` capability into `skipped:unsupported`, marks
the run partial, and does not submit the blocked query. Failures after a query is attempted remain
`errored`.

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
