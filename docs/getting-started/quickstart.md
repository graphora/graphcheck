# Install and quickstart

Installation and the first-run quickstart (`graphcheck init` through your first `graphcheck run`)
are documented in the [top-level README](../../README.md#quickstart), which stays the single
source of truth for these steps so they don't drift out of sync with two copies.

In short:

```console
git clone https://github.com/graphora/graphcheck.git
cd graphcheck
uv tool install .
graphcheck --version
```

Then, in the directory that should contain your checks:

```console
mkdir graph-health
cd graph-health
graphcheck init
```

This scaffolds `graphcheck.yml`, `profiles.yml`, an example checks file, and the `.graphcheck/`
artifacts directory. The generated project runs up to two checks concurrently by default; change
`concurrency` in `graphcheck.yml` or pass `graphcheck run --concurrency N` to override it. See the
README's [Quickstart](../../README.md#quickstart) section for configuring `profiles.yml`, then
[Check reference](../reference/checks.md) for what to put in your checks file and
[CI setup](../guides/github-actions.md)
for running GraphCheck in a pipeline.
