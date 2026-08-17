# Install and quickstart

Installation and the first-run quickstart (`graphcheck init` through your first `graphcheck run`)
are documented in the [top-level README](../README.md#install-from-source), which stays the single
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
artifacts directory. See the README's [Quickstart](../README.md#quickstart) section for configuring
`profiles.yml`, then [Check reference](check-reference.md) for what to put in your checks file and
[CI setup](ci-setup.md) for running GraphCheck in a pipeline.
