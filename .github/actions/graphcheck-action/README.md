# GraphCheck Action

Run GraphCheck checks against your graph on every pull request, and get
a pass/fail summary posted straight to the PR's checks tab.

This is a thin wrapper around the GraphCheck CLI (graphcheck run) - it
adds no new checking behaviour of its own.

## Usage

    - uses: graphora/graphcheck-action@v1
      with:
        profile: ci
        fail-fast: false
        version: 0.1.0
      env:
        NEO4J_PASSWORD: ${{ secrets.NEO4J_PASSWORD }}

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| profile | yes | - | Connection profile name, as defined in graphcheck.yml |
| fail-fast | no | false | Stop after the first error-severity failure |
| version | no | 0.1.0 | Exact GraphCheck version to install from PyPI |

## What it does

1. Installs the pinned GraphCheck version from PyPI.
2. Runs graphcheck run using the given profile. The Neo4j password is
   read from the environment variable named in the profile's
   password_env - set it via a repo secret, never a plaintext input.
3. Captures the run's exit code. The job's final status matches this
   exit code exactly (0 green; 1/2/3 red) - this is preserved even
   though later steps always run.
4. Uploads results.json and the HTML report as build artifacts,
   whenever they were produced. If an early failure produced no
   artifacts, the summary says so explicitly.
5. Writes a pass/fail/errored/warn breakdown, read directly from
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
- Not yet published to the GitHub Marketplace. Pin to a commit SHA or
  branch until a tagged v1 release exists.
