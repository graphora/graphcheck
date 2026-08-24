# Running GraphCheck in CI

GraphCheck is published as the reusable composite Action `graphora/graphcheck-action@v1`. It
installs a pinned GraphCheck version, connects to your graph, runs your checks, and reports
pass/fail/error results directly in the PR's checks tab. See the [CI/CD guide](ci-cd.md) for
complete pull-request, scheduled, staging, and production workflows.

## Usage

```yaml
- uses: actions/checkout@v4
- uses: graphora/graphcheck-action@v1
  with:
    profile: ci
    uri: bolt://localhost:7687
    user: neo4j
    database: neo4j
    fail-fast: false
    concurrency: 2
    upload-artifacts: on-failure
  env:
    NEO4J_PASSWORD: ${{ secrets.NEO4J_PASSWORD }}
```

## Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `profile` | no | `ci` | Profile name to generate and use, if `profiles.yml` does not already exist |
| `uri` | yes | - | Neo4j Bolt URI |
| `user` | yes | - | Neo4j username |
| `database` | no | `neo4j` | Neo4j database name |
| `fail-fast` | no | `false` | Stop after the first error-severity failure |
| `suite` | no | - | Suite name to run via `--suite`; skipped if empty |
| `concurrency` | no | - | Maximum concurrent checks; empty uses `graphcheck.yml` |
| `upload-artifacts` | no | `always` | Upload `always`, `on-failure`, or `never` |
| `version` | no | `0.1.0` | Exact GraphCheck version to install from PyPI. Empty skips the install and uses whatever GraphCheck is already on PATH |

Leaving `version` empty is the escape hatch for installing GraphCheck from source earlier in the
job (for example, `pip install .`) and reusing that install for the smoke run, decoupled from the
PyPI release.

## Exit codes and job status

The job's final status is the run's original exit code, preserved through the upload/summary
steps - those steps always run (`if: always()`), but never mask or overwrite the run's result.

| Exit | Meaning | Job status |
| --- | --- | --- |
| 0 | Complete run with at least one evaluated check, where every evaluated check passed; generated skips may also be present | green |
| 1 | A check failed, or an error-severity check errored | red |
| 2 | Incomplete coverage, or a warning | red |
| 3 | The run could not execute (bad config, no connection, setup failure) | red |

The pass/fail/warn/error breakdown shown in the PR's Step Summary is read from `results.json`, not
inferred from the exit code - the two are independent, since exit 1 covers both a failed check and
an error-severity errored check.

## What it does

1. Installs the pinned GraphCheck wheel in an isolated Python 3.12 environment with cached `uv`,
   unless `version` is empty.
2. Resolves the artifacts directory from `graphcheck.yml` (defaults to `.graphcheck`).
3. If `profiles.yml` does not already exist, generates one from the `uri`/`user`/`database`
   inputs. Only `password_env: NEO4J_PASSWORD` is written - the real password is never in the
   generated file, and is read from the `NEO4J_PASSWORD` environment variable at runtime.
4. Runs `graphcheck run` using the given profile.
5. Removes the generated `profiles.yml`, only if this Action created it.
6. Uploads `results.json` and the HTML report according to `upload-artifacts`. If an early failure
   produced none, the summary says so explicitly rather than uploading nothing silently.
7. Emits GitHub error/warning annotations for failed, warned, and errored checks. The Action points
   annotations at YAML check lines when available, includes stable graph element identities, and
   reports any annotations dropped beyond GitHub's per-step cap of 10 errors and 10 warnings.
8. Writes a pass/fail/errored/warn breakdown to the GitHub Step Summary; annotations are additive.

## Notes

- This Action requires a graph reachable from the CI runner.
- The install step runs on a pinned Python 3.12 via `astral-sh/setup-uv`, but only when installing
  from PyPI (`version` is non-empty). A source install earlier in the job is expected to have
  already set up the interpreter it needs.

See [`.github/actions/graphcheck-action/README.md`](../.github/actions/graphcheck-action/README.md)
for the full Action reference.
