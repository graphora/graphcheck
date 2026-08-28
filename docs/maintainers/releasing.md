# Releasing GraphCheck to PyPI

GraphCheck publishes to PyPI with GitHub's Trusted Publishing (OIDC). No API token is stored
anywhere: `.github/workflows/release.yml` mints a short-lived OIDC token that PyPI trusts because
the workflow, repository, and environment match a Trusted Publisher registered once on PyPI.

## One-time setup

### 1. GitHub `pypi` environment

Create a repository environment named `pypi` (**Settings → Environments → New environment**):

- Add protection rules — required reviewers and/or a deployment-branch rule — so only an approved
  release can publish.
- No secrets are needed; publishing uses OIDC, not a stored token.

### 2. PyPI Trusted Publisher

Register a Trusted Publisher for the project on PyPI. A *pending* publisher is fine before the
first upload — the name `graphcheck` is claimed only on the first successful publish.

- PyPI → **Publishing → Add a pending publisher** (or the project's *Publishing* settings once it
  exists).
- **PyPI Project Name:** `graphcheck`
- **Owner:** `graphora`
- **Repository name:** `graphcheck`
- **Workflow name:** `release.yml`
- **Environment name:** `pypi`

## Cutting a release

1. Bump `version` in `pyproject.toml`. This is the single source of truth; `--version` reads it
   from the installed distribution metadata (`importlib.metadata`), so the two never drift.
2. Update `CHANGELOG.md`.
3. Merge to `development` through the normal PR gate.
4. Create a GitHub Release whose tag is `v<version>` (for example `v0.1.0`), targeting the merge
   commit. Publishing the release triggers the workflow.

The workflow then:

- builds the sdist and the `py3-none-any` wheel,
- fails if the release tag does not match the built version,
- runs `twine check` and a clean-environment install smoke test (`graphcheck --version`),
- publishes to PyPI from the protected `pypi` environment via Trusted Publishing.

## Verifying a release

After the workflow succeeds:

```bash
pipx install "graphcheck==<version>"
graphcheck --version
```
