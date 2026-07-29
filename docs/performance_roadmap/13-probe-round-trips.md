# PR 13 — Reduce Neo4j target-probe round trips

- Category: connector performance
- Roadmap source: Step 6, probe phase
- Prerequisites: PRs 02 and 12
- Suggested PR title: `perf: cache one-command probe state and consolidate metadata reads`

## Goal

Reduce sequential target-probe latency without losing capability, visibility, restricted-user, or
sampling-fingerprint semantics.

## Scope

- One completed probe cached for one client/command.
- Consolidation of compatible metadata/count reads.
- Lazy capability checks when selected work does not require them.
- Round-trip instrumentation.
- Restricted-credential parity.

## Non-goals

- Cross-command or persistent probe caching.
- Changing fingerprint inputs silently.
- Skipping arbitrary query read classification.
- Assuming administrator privileges.

## Files expected to change

- `src/graphcheck/neo4j_adapter.py`
- CLI/debug/profile/runner callers that repeat probe work
- connector and integration tests
- telemetry timing fields only if existing approved fields can represent the measurements
- SPEC-03 probe description

## Invariants

- `RunTarget`, visibility, and counts retain their current meanings.
- HOME database and restricted-user behavior remain correct.
- APOC and count-store capability remain explicit booleans.
- The sampling fingerprint stays deterministic for the same observed target state.
- One command does not reuse another command's stale graph counts.

## Implementation

1. Instrument the current number and duration of probe requests.
2. Cache one complete probe result on the client for its lifetime or explicit command scope.
3. Route all callers in one command through that result.
4. Combine server metadata and compatible count queries only where response/permission behavior
   remains distinguishable.
5. Evaluate schema-token consolidation separately from graph counts.
6. Probe APOC/count-store lazily when the selected command/checks do not require them.
7. Keep independent failures mapped to the same visibility/capability output.
8. Compare full, restricted, HOME-granted, and HOME-denied results before and after.

Do not parallelize probe queries until consolidation and caching have been measured. Fewer requests
is preferable to sending the same number simultaneously.

## Tests

Run:

```console
uv run pytest tests/test_neo4j_adapter.py tests/test_cli.py -q
```

Live integration:

```powershell
$env:GRAPHCHECK_NEO4J_INTEGRATION = "1"
uv run pytest tests/integration/test_integration_neo4j_adapter.py -v
```

Required assertions:

- repeated probe callers on one client execute the live probe once;
- separate clients/commands do not share probe state;
- all credential visibility cases remain equivalent;
- graph-count changes are observed by a new command/client;
- capability laziness does not report a false capability;
- round-trip count and elapsed time are reported.

## Acceptance criteria

- One command performs no duplicate complete probe.
- Round trips are reduced with measured before/after evidence.
- Restricted credentials retain the same output and errors.
- Fingerprint/sampling behavior is unchanged or explicitly versioned.
- No cross-command staleness is introduced.

## Rollback

Disable the one-command probe cache or revert individual query consolidation. Keep instrumentation
to diagnose the regression.

## PR checklist

- [ ] All permission profiles were tested live.
- [ ] Before/after request count is attached.
- [ ] Cache lifetime is command/client bounded.
- [ ] Fingerprint behavior is documented.
