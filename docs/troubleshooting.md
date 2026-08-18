# Troubleshooting

GraphCheck fails closed: every error has a stable `code`, a `message`, and a `fix`. Run
`graphcheck debug` first for almost any problem below - it surfaces the same diagnostics without
running your checks.

## Project and profile setup

| Code | What happened | Fix |
| --- | --- | --- |
| `profile.missing` | `profiles.yml` was not found in the project root | Run `graphcheck init`, or create `profiles.yml` next to `graphcheck.yml` |
| `profile.invalid` | `profiles.yml` failed to parse or validate | Fix `profiles.yml`, then run `graphcheck debug` again |
| `profile.not_found` | The named profile isn't in `profiles.yml` | Use `graphcheck debug --profile <name>`, or update the `default` profile |
| `profile.password_missing` | Neither `password` nor a working `password_env` is set | Set the referenced environment variable, or add a `password` fallback, then run `graphcheck debug` again |
| `profile.uri_invalid` | The profile's `uri` has an unsupported or incomplete scheme | Use a complete Bolt URI: `bolt://` for a direct local connection, `neo4j+s://` for CA-signed TLS/routing |

## Connecting to Neo4j

These come from the connector and mostly map onto everyday setup mistakes.

| Code | What happened | Fix |
| --- | --- | --- |
| `neo4j.unreachable` | Neo4j isn't reachable at the configured Bolt URI | Start Neo4j, verify the host and port in `uri`, then run `graphcheck debug` again |
| `neo4j.auth_failed` | Neo4j rejected the configured credentials | Update `user` and `password`/`password_env` in `profiles.yml`, then run `graphcheck debug` again |
| `neo4j.tls_mismatch` | The endpoint's TLS mode or certificate doesn't match the configured URI scheme | Use `bolt://` for direct non-TLS, `neo4j+s://` for CA-signed TLS, or `neo4j+ssc://` for a trusted self-signed endpoint |
| `neo4j.database_not_found` | The configured database doesn't exist or isn't online | Set `database` in `profiles.yml` to an existing online database (often `neo4j`), or create/start it |
| `neo4j.permission_denied` | Neo4j denied a read or probe query for the configured user | Grant the user `ACCESS` plus `MATCH` (or `READ`/`TRAVERSE`) on the configured database |
| `neo4j.unsupported_version` | The Neo4j Server version is outside GraphCheck's supported lines | Upgrade to Neo4j Server 5.26 LTS or a documented calendar-version target |
| `neo4j.query_failed` | A probe or setup query failed for a reason not covered above | Run `graphcheck debug --json` and check the message for the specific query that failed |

## Read-only enforcement

GraphCheck refuses to run anything Neo4j classifies as write-capable, and on Enterprise it also
checks the configured user's actual privileges before any checks run.

| Code | What happened | Fix |
| --- | --- | --- |
| `neo4j.write_rejected` | A query was classified as write-capable and was blocked | Replace it with read-only Cypher, and use a credential without write privileges |
| `neo4j.credential_not_read_only` | The Enterprise credential has privileges outside GraphCheck's read-only model | Create a dedicated user with only `ACCESS` and `MATCH` (or `READ`/`TRAVERSE`) on the target database |
| `neo4j.credential_read_only_unverified` | Neo4j didn't return the user's privileges for inspection | Use Enterprise with a native user that can inspect its own privileges, restricted to `ACCESS` and `MATCH` |
| `neo4j.read_guard_unavailable` | The driver/server didn't return what GraphCheck needs to classify a query as read-only | Use the supported Neo4j driver version and a dedicated read-only credential |

## Running checks

| Code | What happened | Fix |
| --- | --- | --- |
| `run.invalid_selector` | `--select` was given something other than `tag:<name>` | Use `--select tag:<name>` (repeatable); use `--suite <id>` to run a specific suite by id |
| `checks.invalid` | A suite file under your checks path failed to load | Fix the check YAML named in the message, then run `graphcheck debug` again |
| `engine.timeout` | The run hit its wall-clock time budget mid-check | This is expected under a tight budget, not a bug - the run still completes as `partial` with exit 2. Narrow the check selection, enable sampling, or increase the run's time budget |

`engine.timeout` is handled gracefully: it doesn't crash the run or produce a hard failure. The run
finishes, marks itself partial, and exits `2`, so CI can distinguish "some checks didn't get to run
in time" from a genuine error.

## Where to look next

- `graphcheck debug` (or `graphcheck debug --json` for machine-readable output) re-runs the same
  connection and capability probes GraphCheck uses internally, without touching your checks.
- Every run's `results.json` and offline HTML report include the same `code`/`message`/`fix` for
  whatever stopped the run, in the `run.error` field.
- See [CI setup](ci-setup.md) for how these map onto exit codes in a pipeline, and
  [Check reference](check-reference.md) for check-specific `catches`/`does not catch` behavior.