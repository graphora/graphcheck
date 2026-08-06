# GraphCheck Action

Run GraphCheck checks against your graph on every pull request, and get
a pass/fail summary posted straight to the PR's checks tab.

This is a thin wrapper around the GraphCheck CLI (graphcheck run) - it
adds no new checking behaviour of its own.

Not yet published to the GitHub Marketplace. Use the in-repo path shown
below; switch to graphora/graphcheck-action@v1 once it is extracted and
tagged.

## Usage

### Within this repository

    - uses: ./.github/actions/graphcheck-action
      with:
        profile: ci
        uri: bolt://localhost:7687
        user: neo4j
        database: neo4j
        fail-fast: false
        version: 0.1.0
      env:
        NEO4J_PASSWORD: ${{ secrets.NEO4J_PASSWORD }}

### From another repository

Not yet extracted to its own repo, so pin to a commit SHA on this one:

    - uses: graphora/graphcheck/.github/actions/graphcheck-action@COMMIT_SHA
      with:
        profile: ci
        uri: bolt://localhost:7687
        user: neo4j
        database: neo4j
        fail-fast: false
        version: 0.1.0
      env:
        NEO4J_PASSWORD: ${{ secrets.NEO4J_PASSWORD }}

Switch to `graphora/graphcheck-action@v1` once this Action is extracted and tagged.

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| profile | no | ci | Profile name to generate and use, if profiles.yml does not already exist |
| uri | yes | - | Neo4j Bolt URI |
| user | yes | - | Neo4j username |
| database | no | neo4j | Neo4j database name |
| fail-fast | no | false | Stop after the first error-severity failure |
| version | no | 0.1.0 | Exact GraphCheck version to install from PyPI |

## What it does

1. Installs the pinned GraphCheck version from PyPI.
2. Resolves the artifacts directory from graphcheck.yml (defaults to
   .graphcheck if not configured or the file is absent).
3. If profiles.yml does not already exist, generates one using the
   uri/user/database inputs. Only password_env: NEO4J_PASSWORD is
   written - the real password is never in the generated file.
4. Runs graphcheck run using the given profile. The Neo4j password is
   read from the NEO4J_PASSWORD environment variable at runtime - set
   it via a repo secret, never a plaintext input.
5. Removes the generated profiles.yml (only if this Action created it).
6. Captures the run's exit code. The job's final status matches this
   exit code exactly (0 green; 1/2/3 red) - this is preserved even
   though later steps always run.
7. Uploads results.json and the HTML report as build artifacts, from
   the resolved artifacts directory, whenever they were produced. If
   an early failure produced none, the summary says so explicitly.
8. Writes a pass/fail/errored/warn breakdown, read directly from
   results.json (not inferred from the exit code), to the GitHub
   Step Summary, including one evidence line per failing check.

## Exit codes

| Exit | Meaning |
|---|---|
| 0 | Run completed, all checks passed or were skipped |
| 1 | A check failed, or an error-severity check errored |
| 2 | Incomplete coverage, or a warning |
| 3 | The run could not execute (bad config, no connection, setup failure) |

## Notes

- This Action requires a graph reachable from the CI runner. Spinning
  up a disposable Neo4j service for self-contained demo runs is not
  yet supported.
- Not yet released: graphcheck itself is not published to PyPI, so the
  install step will fail until [Release] ships it.