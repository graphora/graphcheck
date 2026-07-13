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

The debug path reports total node and relationship counts:

```cypher
MATCH (n) RETURN count(n) AS count
MATCH ()-[r]->() RETURN count(r) AS count
```

The human output also reports what the credentials can and cannot see from the successful probe:
connectivity, read access, and procedure visibility.

When a loaded check suite declares a capability requirement that the target does not satisfy, debug
reports the blocked check id and a fix. In v0 this is a preflight report only; C1 owns turning the
same condition into skipped or errored check results during an actual run.

Capability requirements come from `graphcheck.packs.PACK_REQUIREMENTS`, not from the CLI. The
supported requirement vocabulary is:

- `read`
- `show_procedures`
- `apoc`
- `count_store`

Debug scans both `*.yml` and `*.yaml` files in the configured checks directory. Checks whose
effective `generated` flag is `true` are validated by the loader but are not reported as active
blockers.

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
      "check_id": "apoc-backed-check",
      "check": "future_apoc_check",
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
