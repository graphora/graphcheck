# Troubleshooting

GraphCheck fails closed: every error has a stable `code`, a `message`, and a `fix`. Run
`graphcheck debug` first for almost any problem below - it loads and validates your check suites
and probes the connection, without executing your checks.

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
| `neo4j.permission_denied` | Neo4j denied a read or probe query for the configured user | Verify the configured `database` in `profiles.yml` is correct, and that the user has the built-in `reader` role (plus `PUBLIC`) on it - see Read-only enforcement below |
| `neo4j.unsupported_version` | The Neo4j Server version is outside GraphCheck's supported lines | Upgrade to Neo4j Server 5.26 LTS or a documented calendar-version target |
| `neo4j.query_failed` | A probe or setup query failed for a reason not covered above | Run `graphcheck debug --json` and check the message for the specific query that failed |

## Read-only enforcement

GraphCheck refuses to run anything Neo4j classifies as write-capable, and on Enterprise it also
checks the configured user's actual role before any checks run. The required setup is Neo4j's
built-in `reader` role (plus the default `PUBLIC` role) and nothing else - not a custom role
with `ACCESS`/`MATCH` grants.

| Code | What happened | Fix |
| --- | --- | --- |
| `neo4j.write_rejected` | A query was classified as write-capable and was blocked | Replace it with read-only Cypher, and use a credential without write privileges |
| `neo4j.credential_not_read_only` | The configured user has roles other than exactly `reader` plus `PUBLIC` | Grant the built-in `reader` role to the configured user, revoke every other role except `reader` and `PUBLIC`, then run `graphcheck debug` again |
| `neo4j.credential_read_only_unverified` | Neo4j didn't return the user's roles for inspection | Use Neo4j Enterprise with a user assigned only the built-in `reader` role (plus `PUBLIC`), then run `graphcheck debug` again |
| `neo4j.read_guard_unavailable` | The driver/server didn't return what GraphCheck needs to classify a query as read-only | Use the supported Neo4j driver version and a dedicated read-only credential |

## Running checks

| Code | What happened | Fix |
| --- | --- | --- |
| `run.invalid_selector` | `--select` was given something other than `tag:<name>` | Use `--select tag:<name>` (repeatable); use `--suite <id>` to run a specific suite by id |
| `checks.invalid` | A suite file under your checks path failed to load | Fix the check YAML named in the message, then run `graphcheck debug` again |
| `engine.timeout` | A check was still running when the run's wall-clock budget ran out | Narrow the check selection with `--suite`/`--select`, or enable sampling on the check if it supports it |

`engine.timeout` on its own does not fail the run: the timed-out check is marked partial rather
than erroring the whole run. But it does not override an unrelated error - if an earlier
error-severity `fail` or `errored` check already occurred, the run still exits `1` for that
reason. A run affected only by `engine.timeout` (nothing else wrong) exits `2`. There is currently
no CLI flag or `graphcheck.yml` field to raise the time budget directly; narrowing what you select
or run, or sampling, are the only user-facing levers today.

## Where to look next

- `graphcheck debug` (or `graphcheck debug --json` for machine-readable output) re-runs the same
  connection and capability probes GraphCheck uses internally, and validates your check suites,
  without executing any checks.
- Errors show up in different places depending on what failed. A run that couldn't start at all
  (`run_status: failed`) carries its error in `run.error`. A run that completed or went partial
  (`run_status: complete`/`partial`) carries per-check errors in that check's own `checks[].error`
  instead. `run.partial_reason` describes a partial execution/run state; coverage can still be
  `partial` when `run_status` is `complete`, because generated, skipped, or errored checks were not
  all evaluated, while `run.partial_reason` remains null. Use `coverage_status` for check coverage.
- Every run's `results.json` and offline HTML report expose all of the above.
- See [CI setup](ci-setup.md) for how these map onto exit codes in a pipeline, and
  [Check reference](check-reference.md) for check-specific `catches`/`does not catch` behavior.
