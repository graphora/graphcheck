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

1. Bump `__version__` in `src/graphcheck/__init__.py`. This is the single source of truth:
   `pyproject.toml` declares the version as `dynamic` and `[tool.hatch.version]` reads the literal
   at build time, and `--version` prints the same literal, so the wheel and the CLI never drift.
   Do not add a `version` key to `pyproject.toml` — the build fails if the field is both dynamic
   and static.
2. Update `CHANGELOG.md`: move the `[Unreleased]` entries under `## [<version>] - <date>`.
3. Merge to `development` through the normal PR gate.
4. Build once locally and read the filename before you tag. This catches a forgotten version bump
   while it still costs nothing:

   ```console
   uv build
   ls dist/graphcheck-*-py3-none-any.whl
   ```

   The filename must contain the version you are about to tag. If it does not, step 1 was missed.
5. Create a GitHub Release whose tag is `v<version>` (for example `v0.1.0`), targeting the merge
   commit from step 3. Publishing the release triggers the workflow.

The workflow then:

- builds the sdist and the `py3-none-any` wheel,
- fails if the release tag does not match the built version,
- runs `twine check` and a clean-environment install smoke test (`graphcheck --version`),
- publishes to PyPI from the protected `pypi` environment via Trusted Publishing.

### If the release fails its version guard

The tag cannot be reused. `[tool.hatch.version]` reads the literal from the tree at the tagged
commit, so re-running the workflow on the same tag rebuilds the same wrong version. Correct the
literal on `development` through the normal PR gate, then recreate the release on the new merge
commit:

```console
gh release view v<version> --json body --jq .body > /tmp/release-notes.md   # keep the notes first
gh release delete v<version> --yes --cleanup-tag
gh release create v<version> --target <new merge commit> \
  --title "GraphCheck <version>" --notes-file /tmp/release-notes.md
```

A failed guard uploads nothing, so no filename is burned. That is what the guard is for: PyPI never
allows a filename to be reused, so a wheel uploaded under the wrong version would make that version
unpublishable for good.

## After the release publishes

1. Verify from the index rather than from a green workflow:

   ```console
   pipx install "graphcheck==<version>"
   graphcheck --version
   ```

2. Promote `development` to `main` with a pull request titled
   `Promote development to main (v<version>)`.
3. Update the GitHub Action, or its users stay on the previous release. In
   `graphora/graphcheck-action`:
   - bump the `version` input default in `action.yml` to `<version>`,
   - re-read the other input descriptions for behaviour this release changed,
   - merge, cut a `v1.0.x` release, and move the `v1` tag to it.

   Merging alone does not move `v1`, and `v1` is the ref real workflows use. The `v1.0.x` tags are
   immutable under that repository's `Immutable semantic release` ruleset; `v1` is deliberately
   outside the pattern so it can be repointed.
