# Contributing to GraphCheck

## Branches

`development` is the default/integration branch — open PRs against it. `main` holds release tags (v0.1.0). Never push directly to either.

## Definition of done

See the pull request template. Every box is required before merge.

## Decision rights (§13)

Reversible in under half a day → decide yourself. Reversible in over two days, or any cross-contract change (`results.json`, check YAML, exit codes, CLI surface) → escalate to Ezhil.

## Anti-slop

No abstractions without three callers. No "just in case" params. No comments restating code. No swallowed exceptions. No un-issued TODOs.

## Attribution

No AI attribution anywhere — commits, PRs, issues, docs, comments. Write in a plain human voice.
