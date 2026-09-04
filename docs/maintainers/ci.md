# Maintaining CI

GraphCheck uses two levels of continuous integration (CI):

- **CI** and **GraphCheck** run on every pull request. They cover formatting, unit tests,
  supported Python and Neo4j combinations, packaging, secrets, and the published GraphCheck
  Action.
- **Extended CI** runs every Wednesday at 03:17 UTC and can also be started manually. It covers
  slower adoption benchmarks, Windows performance gates, and graceful rejection of Neo4j 4.4.

Keeping Extended CI out of the pull-request gate makes normal contributions faster without
deleting the deeper checks.

## One-time GitHub setup

You need repository administrator access for these steps.

### 1. Let the checks register

Push these workflow files to GitHub and let one pull request finish. GitHub normally offers a
status check in branch rules only after that check has run at least once.

### 2. Choose the required pull-request checks

1. Open the repository on GitHub.
2. Select **Settings → Rules → Rulesets**.
3. Open the active ruleset that targets `main` and `development`, or create a branch ruleset for
   those branches.
4. Enable **Require a pull request before merging** and **Require status checks to pass**.
5. Add these checks. GitHub displays matrix values in parentheses:

   - `secret-scan`
   - `lint`
   - `test (3.12)`
   - `test (3.13)`
   - `test (3.14)`
   - `driver-compat (3.12, min)`
   - `driver-compat (3.14, latest-6)`
   - `integration (lts-cypher-5)`
   - `integration (current-cypher-5)`
   - `integration (current-cypher-25)`
   - `installed-wheel`
   - `graphcheck`

6. Remove any old required entries for `hostile-neo4j44`, `first-run-*`,
   `first-run-summary`, `performance-gates`, and the other old `driver-compat` combinations.
7. Save the ruleset and keep its enforcement status **Active**.

Do **not** require any Extended CI job in the branch ruleset. Those jobs do not run on pull
requests, so requiring one would leave every pull request waiting forever.

GitHub identifies workflow checks by job name, not by workflow name. If the spelling shown in a
completed pull request differs slightly from this list, choose the completed check with the same
job and matrix values. GitHub's
[ruleset documentation](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository)
explains the same Settings path.

### 3. Add the GraphCheck smoke-test password

The GraphCheck workflow starts a temporary Neo4j container. Its password exists only for that CI
run, but the workflow reads it from a GitHub Actions secret so it is not printed or committed.

1. Open **Settings → Secrets and variables → Actions**.
2. Select **New repository secret**.
3. Enter `NEO4J_PASSWORD` as the name.
4. Enter a strong temporary-test password as the value, then select **Add secret**.

No extra secrets are needed for CI or Extended CI. GitHub explains repository secrets in
[Secrets](https://docs.github.com/en/actions/concepts/security/secrets).

### 4. Allow the actions used by the workflows

Most repositories already allow public actions. If this repository uses an allowlist, open
**Settings → Actions → General → Actions permissions** and allow:

- `actions/checkout@v4`
- `actions/upload-artifact@v4`
- `actions/download-artifact@v4`
- `astral-sh/setup-uv@v5`
- `Vampire/setup-wsl@v7.0.0`
- `graphora/graphcheck-action@v1`

The release workflow additionally needs `pypa/gh-action-pypi-publish@release/v1`.

## Running the deeper checks yourself

1. Open the repository's **Actions** tab.
2. Select **Extended CI** in the left sidebar.
3. Select **Run workflow**, choose the default branch, and select the green **Run workflow**
   button.
4. Open the new run to follow progress. When it finishes, download timing and performance files
   from the run's **Artifacts** section if you need to investigate a result.

The manual button appears because the workflow uses `workflow_dispatch`; GitHub documents it in
[Managing workflow runs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs).
Scheduled runs use the version of the workflow on the default branch, so the weekly run starts
only after `extended-ci.yml` has been merged there.
