# PR 12 — Replace the global read-guard cache with a bounded per-client cache

- Category: connector correctness and performance
- Roadmap source: Step 6, cache phase
- Prerequisites: PR 01
- Suggested PR title: `fix: scope read classifications to a bounded Neo4j client cache`

## Goal

Prevent read-classification decisions from crossing server/client identities or growing without
limit while preserving single-flight behavior for concurrent identical queries.

## Problem

Successful `EXPLAIN` classifications are stored in a process-global set keyed only by query and
database. That key does not distinguish URIs, credentials, server versions, or procedure catalogs.

## Scope

- Per-`Neo4jClient` classification cache.
- Documented LRU capacity.
- Existing concurrent single-flight behavior.
- No caching of failures, timeouts, or unknown classifications.
- Cache metrics/tests.

## Non-goals

- Skipping read verification for built-in queries.
- Consolidating target probe queries.
- Persisting classifications across commands/processes.
- Using read routing as an authorization boundary.

## Files expected to change

- `src/graphcheck/neo4j_adapter.py`
- connector unit and concurrency tests
- SPEC-03 cache wording if cache behavior is documented

## Cache design

Because the cache lives on one client, URI/profile identity is implicit. Key entries by the
database and exact query text, plus any server/catalog discriminator needed if one client can
change targets.

The cache must:

- have a fixed maximum;
- use LRU or equivalent predictable eviction;
- protect shared state with the existing lock discipline;
- let one owner perform a preflight while peers wait within their deadlines;
- wake every waiter on owner success or failure;
- clear when the client closes.

Parameters need not be part of the key when query-type classification depends only on query
structure, but this assumption must be covered by server integration tests.

## Implementation

1. Add tests proving two clients never share classifications.
2. Encapsulate cache and in-flight events in `Neo4jClient`.
3. Add bounded eviction with a small documented default.
4. Preserve deadline-aware waits for concurrent identical queries.
5. Cache only a successful read-only classification.
6. Clear in-flight events safely on every exception path.
7. Remove module-global cache state and tests that mutate it directly.
8. Record read-guard cache hit/miss timing without including query text in telemetry.

## Tests

Run:

```console
uv run pytest tests/test_neo4j_adapter.py -q
```

Required cases:

- first query preflights, second identical query hits;
- LRU eviction;
- separate clients/URIs/users;
- timeout while waiting for an in-flight owner;
- owner failure wakes waiters;
- failed/write/unknown classifications are never cached;
- close clears state;
- custom Cypher always preflights on first use.

## Acceptance criteria

- No process-global classification set remains.
- Cache size is bounded and eviction is tested.
- No classification crosses a client identity.
- Security behavior remains fail-closed.
- Single-flight behavior and deadlines remain correct.

## Rollback

Disable caching so every query preflights. Do not restore the process-global cache.

## PR checklist

- [ ] Cache cannot cross clients.
- [ ] Failure paths wake waiters.
- [ ] Capacity/eviction are explicit.
- [ ] No trusted-built-in bypass was introduced.
