# PR 01 — Make telemetry and profile tests hermetic

- Category: correctness and test reliability
- Roadmap source: Step 7
- Prerequisites: none
- Suggested PR title: `test: isolate telemetry consent and stabilize profile injection`

## Goal

Make the full test result independent of the developer's persisted telemetry consent and guarantee
that tests cannot deliver real telemetry.

## Problem

The global test fixture removes the PostHog key but does not isolate the telemetry consent file.
With persisted consent enabled, profile commands attach telemetry observer keyword arguments.
Several profile test doubles accept only a positional client, which produces environment-dependent
failures.

## Scope

- Isolate telemetry configuration and installation identity for every test.
- Stabilize the `build_profile` test seam so enabled and disabled telemetry use one call signature.
- Add explicit enabled/disabled consent coverage.
- Preserve all existing telemetry policy and payload behavior.

## Non-goals

- Redesigning telemetry policy or event schemas.
- Splitting CLI responsibilities.
- Changing production consent defaults.

## Files expected to change

- `tests/conftest.py`
- `tests/test_cli.py`
- `tests/telemetry/test_cli_boundary_integration.py`
- `src/graphcheck/cli.py` only if needed to stabilize the invocation signature
- `src/graphcheck/profiler.py` only for type/interface consistency

## Implementation

1. Point `GRAPHCHECK_TELEMETRY_CONFIG` at a test-owned temporary path in an autouse fixture.
2. Clear `GRAPHCHECK_TELEMETRY` unless an individual test sets it.
3. Continue deleting `GRAPHCHECK_POSTHOG_API_KEY` and replacing any configured delivery key.
4. Ensure installation ID and consent state cannot be read from the user's real config directory.
5. Call `build_profile` with the observer keyword arguments in both states, using `None` when
   telemetry is inactive.
6. Update monkeypatched profile callables to accept the stable typed signature.
7. Add one CLI profile test with isolated consent enabled and one with it disabled.
8. Add a subprocess or fixture test proving an external consent file cannot affect the suite.

## Tests

Run:

```console
uv run pytest tests/test_cli.py tests/telemetry -q
uv run pytest -q
```

Required assertions:

- enabled and disabled states call the same profile seam;
- no transport is constructed without an injected fake;
- no test reads or writes the real user telemetry config;
- profile output and artifact behavior are unchanged;
- the previously environment-dependent profile tests pass in both consent states.

## Acceptance criteria

- Full-suite results are identical regardless of real user consent.
- No test can deliver telemetry over the network.
- The profile injection seam has one documented call signature.
- No production telemetry semantics change.

## Rollback

Revert the isolated fixture and signature normalization together. If one test genuinely needs the
default config path, opt that test out explicitly rather than weakening global isolation.

## PR checklist

- [ ] Focused tests pass.
- [ ] Full quality gate passes.
- [ ] Test run succeeds with a real external consent file enabled.
- [ ] No generated consent/config files remain in the repository.
