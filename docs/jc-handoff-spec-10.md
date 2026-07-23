# jc handoff — SPEC-10 event models and telemetry policy

You have two isolated starter modules:

- `src/graphcheck/telemetry/events_jc.py`
- `src/graphcheck/telemetry/policy_jc.py`

Neither file is imported by GraphCheck. You can work on them without changing production behavior.
Do not rename them over the production files; submit them for review as `_jc` files.

## Workstream 1: `events_jc.py`

Implement only the strict event-model boundary:

1. Require timezone-aware UTC `occurred_at` values.
2. Add the cross-field validators called out by each `TODO(jc)`.
3. Define the six-type `EngineEvent` union.
4. Define the synchronous `EngineEventSink` protocol.
5. Add `tests/telemetry/test_events_jc.py`, based on the SPEC-10 testing requirements.

Acceptance checks:

- unknown fields, negative/bool durations, invalid enums, and inconsistent outcomes fail;
- models are frozen;
- target-probe queries cannot carry check attribution;
- no model can represent a query, parameter, check ID, verdict, message, traceback, or result;
- `RunFinished` counters reconcile, while fail-fast partial runs may have no dedicated partial code.

Out of scope: collector ordering, engine instrumentation, PostHog mapping, network transport, and CLI.

## Workstream 2: `policy_jc.py`

Implement only consent resolution and privacy allowlists:

1. Implement explicit enable, disable, status resolution, and ID reset using atomic JSON writes.
2. Apply precedence: `DO_NOT_TRACK=1` and `GRAPHCHECK_TELEMETRY=0` disable; a stored current
   consent enables; `GRAPHCHECK_TELEMETRY=1` is process-only when there is no active stored opt-in.
3. Never reuse an inactive stored ID for a process-only run.
4. Require renewed consent only when `CONSENT_VERSION` changes.
5. Implement broad template, safe error-code, and exception-type mappings.
6. Implement recursive privacy assertions.
7. Add `tests/telemetry/test_policy_jc.py`.

Acceptance checks:

- default-off resolution creates no ID and writes no file;
- environment-only IDs are not persisted;
- disable/status/reset do not produce telemetry events;
- an unknown template becomes `custom`; unknown errors/exceptions become `unknown`;
- checked-in project configuration cannot enable telemetry;
- sensitive values and forbidden keys are rejected at any nesting depth.

Out of scope: OS/CI properties, PostHog transport, CLI decorators, engine events, and profile event
construction.

## Suggested review sequence

Run:

```text
ruff check src/graphcheck/telemetry/events_jc.py src/graphcheck/telemetry/policy_jc.py tests/telemetry
pytest -q tests/telemetry/test_events_jc.py tests/telemetry/test_policy_jc.py
```

Then compare behavior with the production tests, not implementation style. Privacy failures should
fail closed: forward a safe `unknown`/`custom` value or disable telemetry, never forward the raw
input.
