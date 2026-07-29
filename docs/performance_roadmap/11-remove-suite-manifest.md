# PR 11 — Remove the suite-manifest cache

- Category: simplification
- Roadmap source: Step 5, cache cleanup
- Prerequisites: none
- Suggested PR title: `refactor: remove the low-value suite manifest cache`

## Goal

Remove `.graphcheck-suite-manifest.json` and its maintenance logic while preserving suite discovery,
validation, selection, and ordering exactly.

## Rationale

The manifest caches suite identity metadata but does not avoid parsing all suites in the common
unfiltered case. It adds invalidation logic and can leave an unignored generated file in the user's
checks directory. The retained benefit does not justify the extra state.

## Scope

- Remove manifest reads, writes, invalidation, and serialization.
- Remove manifest-specific tests and replace them with direct discovery behavior tests.
- Stop creating the cache file.
- Document that suite loading reads the configured YAML files directly.

## Non-goals

- Redesigning suite discovery.
- Changing recursive ordering.
- Ignoring malformed unselected suites.
- Adding a replacement cache.

## Files expected to change

- `src/graphcheck/cli.py`
- suite-loading helper modules if applicable
- CLI/suite discovery tests
- README or SPEC-04 cache wording if present

## Implementation

1. Capture tests for recursive sorted discovery, explicit/fallback suite IDs, duplicate IDs,
   malformed YAML, filtered selection, and empty selection.
2. Remove `.graphcheck-suite-manifest.json` constants and data models.
3. Remove read/write/invalidation paths from suite loading.
4. Load and validate discovered YAML directly using the existing authoritative loader.
5. Remove cache-only exception handling and tests.
6. Verify no init/run/debug command writes the manifest.
7. Search docs and `.gitignore` for obsolete references.

Do not delete an existing user manifest from disk; simply stop reading and writing it. Deletion is
unnecessary and would add a destructive migration.

## Tests

Run:

```console
uv run pytest tests/test_cli.py tests/contracts/test_check_validation.py -q
```

Required assertions:

- discovery order is unchanged;
- all discovered suites are validated before filtering;
- duplicate suite IDs still fail;
- no manifest file is written;
- a stale pre-existing manifest is ignored;
- command results match the no-cache baseline.

## Acceptance criteria

- No production code references the manifest filename or model.
- No GraphCheck command creates or updates the manifest.
- Suite discovery and validation behavior are unchanged.
- A pre-existing manifest does not affect results.

## Rollback

Reintroduce the cache only with benchmark evidence showing a material benefit and store any future
cache under the artifact/cache directory rather than the checks directory.

## PR checklist

- [ ] No destructive cleanup of user files.
- [ ] Discovery behavior tests cover filtered and unfiltered runs.
- [ ] Repository search finds no obsolete manifest references.
- [ ] CLI output and exit codes are unchanged.
