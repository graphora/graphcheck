# SPEC-10 — opt-in telemetry events

*Proposed for v0.* GraphCheck may collect anonymous product telemetry through PostHog only after
an explicit user opt-in. Telemetry is disabled by default. This specification defines the minimum
structured engine events needed to measure reliability, performance, and feature adoption without
collecting graph contents, check outcomes, project identity, credentials, or source material.

The event system is a telemetry boundary, not a general-purpose event bus and not a foundation for a
future UI. The engine exposes one optional event sink; a telemetry adapter aggregates and sanitizes
events before any PostHog call.

## Goals

Telemetry should answer:

1. Do opted-in users reach a completed GraphCheck run?
2. Where do runs fail, become partial, or spend time?
3. Which check families and engine features are used?
4. Which GraphCheck versions are slow or unreliable?
5. Does sampling reduce query time enough to justify its overhead?

Telemetry must not answer:

- What is in a user's graph?
- Which specific project, suite, check, database, repository, or person produced an event?
- Whether an individual check passed, warned, or failed.
- What query, parameter, expected value, measured value, or evidence GraphCheck processed.

Run-level terminal status (`complete`, `partial`, `failed`) and early termination *are* collected as
aggregate reliability signals. These reveal that a run — never a named check — failed or stopped
early. Per-check verdicts, the identity of any check, and all measured or expected values remain
excluded.

Opt-in telemetry is subject to selection bias. PostHog metrics describe opted-in installations, not
the entire user base.

## Event layers

GraphCheck has two event layers:

| Layer | Events | Destination |
| --- | --- | --- |
| Engine events | `RunStarted`, `TargetProbeFinished`, `QueryFinished`, `CheckProcessed`, `RunFinished`, `EngineFaulted` | In-process telemetry collector |
| PostHog events | `graphcheck_run_started`, `graphcheck_check_processed`, `graphcheck_run_completed`, `graphcheck_engine_faulted`, `graphcheck_command_completed`, `graphcheck_profile_completed` | PostHog, when opted in |

`TargetProbeFinished` and `QueryFinished` are internal measurement events. They are folded into
check and run aggregates and are not uploaded separately in v0.

`graphcheck_command_completed` is produced by the CLI orchestration layer rather than the engine. It
is required because project discovery, configuration loading, profile loading, connection setup,
and artifact writing can fail outside the engine boundary.

## Common engine-event envelope

Every engine event has the following fields:

| Field | Type | Rule |
| --- | --- | --- |
| `schema_version` | string | Event schema version; initially `"1.0"`. Versioned independently of GraphCheck. |
| `event_id` | UUID | Random UUID v4 generated for this event. |
| `telemetry_run_id` | UUID | Random UUID v4 generated for this engine invocation. Never the artifact run ID. |
| `sequence` | integer | Monotonically increasing within one telemetry run, beginning at `1`. |
| `occurred_at` | UTC datetime | Time the engine event was constructed. |
| `kind` | enum | The structured engine-event name. |

All models forbid unknown fields. Event payloads use strict enums and allowlists rather than
arbitrary strings.

`telemetry_run_id` provides short-lived correlation within one run. It must not be derived from a
project path, database, profile, machine identifier, result artifact, or check definition.

For cross-layer correlation, engine-derived PostHog events forward this `telemetry_run_id`, and the
CLI attaches a separate `telemetry_command_id` to every PostHog event of one invocation (see
*Common PostHog properties*). Together they join a command to its engine run and expose pre/post-engine
overhead without any stable identity.

## Engine event catalog

### 1. `RunStarted`

Emitted exactly once after the selected check universe is known and immediately before engine work
begins.

| Field | Type | Notes |
| --- | --- | --- |
| `graphcheck_version` | string | Released GraphCheck version. |
| `pack_version` | string | Released built-in pack version. |
| `suite_count` | non-negative integer | Number of loaded suites in the selected universe. |
| `selected_check_count` | non-negative integer | Number of selected checks, including checks later skipped. |
| `conformance_count` | non-negative integer | Selected conformance checks. |
| `competency_count` | non-negative integer | Selected competency checks. |
| `drift_count` | non-negative integer | Selected drift checks. |
| `uses_sampling` | boolean | At least one selected check may use sampling. |
| `uses_baselines` | boolean | At least one selected check requires a baseline. |
| `fail_fast_enabled` | boolean | Fail-fast was requested. |
| `suite_filter_used` | boolean | A suite selector was supplied; never include its value. |
| `tag_filter_used` | boolean | A tag selector was supplied; never include its value. |
| `time_budget_ms` | non-negative integer or null | Configured run budget. |

`RunStarted` enables start-to-terminal funnels and exposes hard crashes or forced termination as
runs without a terminal event. Such missing-terminal measurements are approximate because
best-effort telemetry may itself be lost.

### 2. `TargetProbeFinished`

Emitted once after Neo4j target and capability probing.

| Field | Type | Notes |
| --- | --- | --- |
| `outcome` | `success`, `error`, or `timeout` | Probe result. |
| `duration_ms` | non-negative integer | Total probe duration. |
| `target_source` | `provided` or `probed` | Whether target metadata was already available. |
| `server_version_major` | integer or null | Major version only. |
| `server_version_minor` | integer or null | Minor version only. |
| `apoc_available` | boolean or null | Null when not determined. |
| `count_store_available` | boolean or null | Null when not determined. |
| `error_code` | safe error code or null | Stable allowlisted code; never an error message. |

The event must not contain the database name, URI, user, edition, graph fingerprint, exact counts,
server address, or full server version string.

This event remains internal in v0. Its duration and outcome are copied into
`graphcheck_run_completed`.

### 3. `QueryFinished`

Emitted after each database operation known to the engine. There is intentionally no
`QueryStarted` event in v0.

| Field | Type | Notes |
| --- | --- | --- |
| `check_sequence` | non-negative integer or null | Ephemeral position in this run; null for run-level queries. |
| `pattern` | safe pattern enum or null | Check family; null for target probes. |
| `template` | safe template enum or null | Allowlisted built-in template, otherwise `custom`. |
| `query_role` | enum | `target_probe`, `parameter_resolution`, `sampling_population`, `check_measurement`, or `evidence_collection`. |
| `outcome` | `success`, `error`, or `timeout` | Database operation result. |
| `duration_ms` | non-negative integer | End-to-end adapter duration. |
| `server_available_after_ms` | non-negative integer or null | Neo4j result timing, when available. |
| `server_consumed_after_ms` | non-negative integer or null | Neo4j result timing, when available. |
| `read_guard_outcome` | `allowed`, `rejected`, `error`, or `not_run` | Result of the read-only guard. |
| `notification_count` | non-negative integer or null | Count only. |
| `error_code` | safe error code or null | Stable allowlisted code. |

Coarse result-size buckets are intentionally omitted in v0. The goals forbid telemetry from
answering *what is in the graph*, and a bucketed row count is coarse graph cardinality. Any
cardinality signal (row, sample, or population size) requires an expanded consent tier before it may
be added — the same bar as verdict telemetry.

The event must not contain Cypher text, compiled queries, parameters, returned columns, records,
notification text, notification positions, plans, evidence, labels, relationship types, property
names, or property values.

This event remains internal in v0. The collector aggregates it into per-check query timing and
run-level query timing.

### 4. `CheckProcessed`

Emitted exactly once for every selected check, including checks that error or are skipped. It is
emitted when no further engine work will occur for that check.

| Field | Type | Notes |
| --- | --- | --- |
| `check_sequence` | non-negative integer | Ephemeral position in this run. |
| `pattern` | safe pattern enum | Stable check family, such as `conformance`, `competency-shape`, `competency-regression`, or `drift`. |
| `template` | safe template enum | Allowlisted built-in template, otherwise `custom`. |
| `processing_outcome` | `completed`, `engine_error`, or `skipped` | Engine processing state, not the check verdict. |
| `skip_reason` | `generated`, `unsupported`, `not_run`, or null | Set only when skipped. |
| `duration_ms` | non-negative integer or null | End-to-end check processing time. |
| `compile_ms` | non-negative integer or null | Compilation time. |
| `parameter_resolution_ms` | non-negative integer or null | Parameter resolution time. |
| `sampling_population_ms` | non-negative integer or null | Sampling population-query time. |
| `baseline_resolution_ms` | non-negative integer or null | Baseline lookup time. |
| `read_guard_ms` | non-negative integer or null | Read-guard time. |
| `query_ms` | non-negative integer or null | Sum of this check's database-operation durations. |
| `evaluation_ms` | non-negative integer or null | Result evaluation time. |
| `query_count` | non-negative integer | Database operations attributable to this check. |
| `sampled` | boolean | Whether sampling was used. |
| `error_code` | safe error code or null | Stable allowlisted engine error code. |

`processing_outcome:completed` means the engine compiled, executed, and evaluated the check. It does
not reveal whether the resulting verdict was `pass`, `fail`, or `warn`.

The following result fields are explicitly excluded:

- check ID, check name, suite ID, severity, tags, question, and provenance;
- verdict, measured value, expected value, tolerance, baseline value, and evidence;
- compiled query, parameters, exact sample size, and exact population size;
- coarse sample-size or population buckets (removed in v0 as graph cardinality);
- arbitrary error messages, fixes, stack traces, or exception representations.

Check verdict telemetry would reveal graph quality and is outside v0 consent. Adding it requires a
separately documented consent tier; it must not be added as an ordinary schema revision.

### 5. `RunFinished`

Emitted once after a valid `Results` object has been constructed for an expected terminal outcome.
Expected failed runs use `outcome:failed`; they do not emit `EngineFaulted`.

| Field | Type | Notes |
| --- | --- | --- |
| `outcome` | `complete`, `partial`, or `failed` | Mirrors the run status without result details. |
| `duration_ms` | non-negative integer | Total engine duration. |
| `selected_check_count` | non-negative integer | Selected universe. |
| `executed_check_count` | non-negative integer | Checks with `processing_outcome:completed`. |
| `engine_error_count` | non-negative integer | Checks with `processing_outcome:engine_error`. |
| `skipped_generated_count` | non-negative integer | Generated skips. |
| `skipped_unsupported_count` | non-negative integer | Unsupported skips. |
| `skipped_not_run_count` | non-negative integer | Checks not run after an early stop. |
| `query_count` | non-negative integer | All engine database operations. |
| `query_total_ms` | non-negative integer | Sum of query durations. |
| `query_max_ms` | non-negative integer or null | Slowest query duration. |
| `probe_ms` | non-negative integer or null | Target-probe duration. |
| `probe_outcome` | `success`, `error`, `timeout`, or null | Target-probe outcome. |
| `budget_remaining_ms` | non-negative integer or null | Engine budget remaining at completion. |
| `early_stopped` | boolean | The run terminated before all selected checks were processed. Combined with `fail_fast_enabled` and `deadline_exhausted` this permits inferring a fail-fast stop at the run level (invariant 18); it never identifies the check or its verdict. |
| `deadline_exhausted` | boolean | Engine budget was exhausted. |
| `partial_reason_codes` | array of safe reason codes | Codes only; never the human-readable `partial_reason`. |
| `run_error_code` | safe error code or null | Set for expected failed runs. |

Allowed partial-reason codes are:

```
suite_input_invalid
unsupported_check
partial_baseline
baseline_measurement_missing
deadline_exhausted
```

There is no dedicated `fail_fast` reason code. Early termination is reported through the
`early_stopped` and `deadline_exhausted` flags; at the run level this permits inferring a fail-fast
stop (invariant 18), which is accepted as aggregate reliability data. `fail_fast_enabled` on
`RunStarted` records adoption. Telemetry still never identifies the stopping check or any verdict.

New codes require a schema update and privacy review. Unknown reasons map to `unknown`; arbitrary
reason strings must not be forwarded.

### 6. `EngineFaulted`

Emitted when an unexpected exception escapes the engine boundary after `RunStarted`. It is a
terminal event and is mutually exclusive with `RunFinished`.

| Field | Type | Notes |
| --- | --- | --- |
| `engine_stage` | safe stage enum | Last known engine stage; closed set in *Safe allowlists*. |
| `exception_type` | safe exception enum | Allowlisted standard-library type; unknown types map to `unknown`. Closed set in *Safe allowlists*. |
| `safe_error_code` | safe error code | Stable classification, defaulting to `engine.unexpected`. Closed set in *Safe allowlists*. |
| `elapsed_ms` | non-negative integer | Time since the matching `RunStarted`. |

The event must not contain an exception message, traceback, local variables, file path, query,
parameters, driver details, or the serialized exception object.

## PostHog event mapping

The telemetry adapter uploads only the following events:

### `graphcheck_run_started`

One event per engine run. It contains the sanitized `RunStarted` payload plus the common PostHog
properties defined below.

### `graphcheck_check_processed`

One event per selected check. It contains the sanitized `CheckProcessed` payload plus:

- the check's aggregated `QueryFinished` counts and timings;
- no stable check identity and no result verdict.

Successful events may be sampled in a later schema version if volume requires it. v0 does not
sample them. Error, timeout, skipped, and slow-check events must never be selectively dropped by a
future sampling policy.

### `graphcheck_run_completed`

One terminal event for either `RunFinished` or `EngineFaulted`:

- `RunFinished` maps to `terminal_kind:finished` and its aggregate fields;
- `EngineFaulted` also emits `graphcheck_engine_faulted`, then maps to
  `terminal_kind:faulted` with only safe aggregate fields.

Using one completion event preserves the run funnel while the dedicated fault event supports error
analysis.

### `graphcheck_engine_faulted`

One event per unexpected engine fault. It contains only the sanitized `EngineFaulted` payload.
Expected connection, configuration, compilation, query, validation, and deadline outcomes must use
stable error codes on normal check or run events rather than this event.

### `graphcheck_command_completed`

One event emitted at the outermost CLI boundary for every opted-in command invocation.

| Field | Type | Notes |
| --- | --- | --- |
| `command` | `init`, `debug`, `run`, `report`, `profile`, `diff`, `baseline`, `telemetry`, or `other` | Command name only. `profile`, `diff`, and `baseline` are first-class and are never folded into `other`. |
| `action` | safe action enum or null | Per-command action from the *Safe allowlists* set; null for commands with no sub-action. Arbitrary arguments are never included. |
| `process_outcome` | `success`, `user_error`, `engine_error`, or `unexpected_error` | CLI boundary result, defined by operational failure — not by exit code (see the semantic rule below). |
| `failure_stage` | safe stage enum or null | Set if and only if `process_outcome` is not `success`; the pipeline stage that failed. Null on success. |
| `duration_ms` | non-negative integer | Total command duration. |
| `setup_ms` | non-negative integer or null | Time before the engine ran (discovery, config, suite/profile load, client setup). |
| `artifact_write_ms` | non-negative integer or null | Time spent writing result or baseline artifacts. |
| `render_ms` | non-negative integer or null | Time spent rendering the HTML report. |
| `output_mode` | `human` or `json` | Selected output mode. |
| `results_artifact` | `not_requested`, `written`, or `error` | Outcome of the `results.json` write. |
| `report_artifact` | `not_requested`, `written`, or `error` | Outcome of the HTML report write. |
| `baseline_artifact` | `not_requested`, `written`, or `error` | Outcome of the baseline write. |
| `telemetry_command_id` | UUID | Random UUID v4 for this invocation; correlates every PostHog event from this command. |
| `telemetry_run_id` | UUID or null | Equal to the engine run's `telemetry_run_id` when a run occurred; null otherwise. A non-null value is the definitive signal that the engine ran. |
| `probe_outcome` | `success`, `error`, `timeout`, or null | For `init` and `debug`: connection-probe result outside a run; null when no probe occurred. |
| `probe_duration_ms` | non-negative integer or null | Probe duration for `init`/`debug`. |
| `server_version_major` | integer or null | Neo4j major version from the probe. |
| `server_version_minor` | integer or null | Neo4j minor version from the probe. |
| `apoc_available` | boolean or null | Coarse capability from the probe. |
| `count_store_available` | boolean or null | Coarse capability from the probe. |
| `interactive` | boolean | Whether standard input/output were interactive. |
| `ci` | boolean | Derived from a fixed allowlist of common CI indicator variables; variable values are excluded. |
| `os_family` | `windows`, `macos`, `linux`, or `other` | Coarse operating-system family. |
| `os_version` | string | OS major or major/minor only, for example `"11"` or `"6.8"`; `"unknown"` when unavailable. macOS uses the product version, Linux uses the kernel version, and exact builds, distribution details, suffixes, and architecture are excluded. |
| `python_minor` | string | Major and minor only, for example `"3.12"`. |
| `graphcheck_version` | string | Released GraphCheck version. |
| `safe_error_code` | safe error code or null | Stable classification; null on success. |

**`process_outcome` measures operational failure, not exit code.** It answers "did GraphCheck
operate correctly?", never "what did the checks find?".

- `success` means **no operational failure occurred**: every requested stage — setup, the engine
  run (if any), and all requested artifact writes and report rendering — completed. A completed run
  is `success` regardless of exit code: exit 1 (checks failed) and exit 2 (partial or warn) are
  *completed runs*, not CLI errors. Reporting them as `*_error` would indirectly encode check
  verdicts in telemetry.
- A non-success outcome means an **operational failure** occurred at some stage, classified by
  cause: bad input → `user_error`, engine fault → `engine_error`, anything else →
  `unexpected_error`. `failure_stage` records where, and `safe_error_code` classifies it.

Crucially, an operational failure can occur **after** the engine returns a valid result — for
example, the run completes but the `results.json` or report write fails. Such a command is **not**
`success` even though `telemetry_run_id` is present: the run outcome is captured by the engine
events, while `process_outcome` reflects that the invocation failed to write its artifact
(`failure_stage:artifact_write` or `report_render`, with the matching `*_artifact:error`). The
completed-run-is-success rule applies only to exit codes 1/2; it does not override an independent
post-run operational failure.

Whether the engine ran is read from `telemetry_run_id` (non-null ⇒ ran), which replaces a separate
`engine_started` flag. It is independent of `process_outcome`: a run can complete (non-null
`telemetry_run_id`) while the command still fails operationally on a later stage.

This event captures failures during project discovery, configuration parsing, suite loading,
profile loading, client construction, engine setup, artifact writing, and report rendering. It
must not contain command-line arguments, working directory, project name, profile name, paths,
filenames, shell, environment-variable names or values, artifact IDs, or report contents.

### `graphcheck_profile_completed`

One event emitted by the CLI when `graphcheck profile` finishes. Profiling has meaningful partial
states and stage timings that a generic command event cannot express, so it gets a dedicated event.
`graphcheck profile` also emits `graphcheck_command_completed` (correlated by `telemetry_command_id`);
`diff`, `baseline`, and `report` remain command actions and do not get dedicated events.

| Field | Type | Notes |
| --- | --- | --- |
| `outcome` | `complete`, `partial`, or `error` | Profiler result. Never a graph content signal. |
| `duration_ms` | non-negative integer | Total invocation duration (CLI entry to exit), so it is always defined — including a setup failure before profiling starts. The per-stage `*_ms` fields below are null when their stage did not run. |
| `schema_ms` | non-negative integer or null | Time collecting schema (labels, relationship types, constraints, indexes). |
| `property_coverage_ms` | non-negative integer or null | Time collecting property coverage. |
| `degree_distribution_ms` | non-negative integer or null | Time collecting degree distribution. |
| `deadline_exhausted` | boolean | The wall-clock budget was exhausted. |
| `last_completed_stage` | safe profiler stage enum or null | Last profiler stage that completed (see *Safe allowlists → Profiler stages*). Null when the profiler never started (setup failure). |
| `partial_reason` | safe profile partial-reason code or null | Set only when `outcome:partial`; null otherwise. |
| `probe_outcome` | `success`, `error`, `timeout`, or null | Connection-probe result. |
| `probe_duration_ms` | non-negative integer or null | Probe duration. |
| `server_version_major` | integer or null | Neo4j major version. |
| `server_version_minor` | integer or null | Neo4j minor version. |
| `apoc_available` | boolean or null | Coarse capability. |
| `count_store_available` | boolean or null | Coarse capability. |
| `safe_error_code` | safe error code or null | Set when `outcome:error`. |

The event must not contain label or relationship counts, property coverage, fingerprints, database
identity, or any profiled graph content. It records that a profile ran and how it went — never what
it found.

`graphcheck_profile_completed` is emitted exactly once per `graphcheck profile` invocation, at the
CLI boundary. It is emitted even when profiling never starts: a setup failure (project discovery,
config or profile load, client setup, or a failed probe) is reported as `outcome:error` with the
matching `safe_error_code`, `last_completed_stage:null`, and null stage timings. A `partial` outcome
carries a `partial_reason` and whatever stages completed before the budget or a non-fatal probe ran
out. The invocation also emits `graphcheck_command_completed`, correlated by `telemetry_command_id`.

## Common PostHog properties

The adapter adds:

| Field | Rule |
| --- | --- |
| `telemetry_schema_version` | `"1.1"` after adding coarse `os_version`; versioned independently of engine events and consent. |
| `consent_version` | Version of the consent text accepted by the user. |
| `graphcheck_version` | Released GraphCheck version. |
| `distinct_id` | Persisted installation UUID after `telemetry enable`. Under process-only `GRAPHCHECK_TELEMETRY=1` with no stored opt-in, a fresh per-process UUID is generated and never persisted. |
| `session_id` | Random process UUID; not persisted. |
| `telemetry_command_id` | Random UUID v4 per CLI invocation. Correlates every PostHog event emitted by one command; not persisted and not derived from any identity. |
| `process_person_profile` | Disabled. GraphCheck must not create or update PostHog person profiles. |
| `geoip_enrichment` | Disabled. No location properties are added to payloads. |

Engine-derived events (`graphcheck_run_started`, `graphcheck_check_processed`,
`graphcheck_run_completed`, `graphcheck_engine_faulted`) also carry the engine `telemetry_run_id`.
`graphcheck_command_completed` carries `telemetry_run_id` when a run occurred. Joining on
`telemetry_command_id` (whole invocation) and `telemetry_run_id` (the engine run) lets analysis
connect setup/artifact failures to their run and measure pre/post-engine overhead — with no stable
identity involved.

GraphCheck never calls PostHog `identify`, aliasing, autocapture, session replay, surveys, feature
flags, or automatic exception capture in v0.

An HTTP receiver necessarily observes a source IP while handling a request. GraphCheck must disable
PostHog GeoIP enrichment, must not copy an IP into event properties, and should configure the
PostHog project not to retain or derive location data from it.

## Consent and controls

1. Telemetry is disabled until a user-level opt-in is recorded; that opt-in applies to all projects
   for the user. A recorded opt-in **persists across ordinary upgrades**; GraphCheck must not
   silently re-disable telemetry on upgrade.
2. Opt-in must be an explicit user action. A repository or project configuration file cannot enable
   telemetry on behalf of a user.
3. `DO_NOT_TRACK=1` and `GRAPHCHECK_TELEMETRY=0` always disable telemetry.
4. `GRAPHCHECK_TELEMETRY=1` may enable telemetry for the **current process only** and is treated as
   explicit operator consent for that process. It does not persist consent and does not change the
   stored user-wide opt-in state. Whenever there is no **active** stored opt-in, the process uses a
   fresh process-scoped `distinct_id` (a random UUID generated at start and discarded at exit) — even
   if an **inactive** installation UUID remains on disk from a prior, since-disabled opt-in. A
   disabled installation's identifier is never reused. This keeps process-only runs unlinkable to
   each other and to any earlier installation.
5. The CLI should expose:

   ```
   graphcheck telemetry enable
   graphcheck telemetry disable
   graphcheck telemetry status
   graphcheck telemetry preview
   graphcheck telemetry reset-id
   ```

6. `preview` prints representative sanitized payloads without sending them.
7. `graphcheck telemetry enable` stores a random installation UUID and the accepted `consent_version`
   in the **user-level** GraphCheck configuration — a single user-wide opt-in, not per-project. It
   does not use a MAC address, hostname, OS account, repository identity, cloud identity, or hash of
   any machine attribute.
8. Disabling telemetry stops collection before engine event models are constructed. Resetting the
   ID breaks linkage to earlier events.
9. Renewed consent is required **only when `consent_version` changes** — that is, when a materially
   expanded data category is introduced. Ordinary upgrades and schema evolution within the existing
   allowlisted categories keep the stored opt-in and do not silently expand consent. Schema `1.1`
   adds only a major/minor `os_version` within the already disclosed coarse runtime-environment
   category; exact build, distribution, suffix, and architecture data remain prohibited.
10. The telemetry control command must not itself emit telemetry for `preview`, `disable`, `status`,
    or `reset-id`. Only `enable` may emit a single `graphcheck_command_completed` — the invocation at
    which consent is first granted.

## Safe allowlists

Every enumerated field is a closed set. A value outside its allowlist maps to `unknown` (or `custom`
for templates); arbitrary strings are never forwarded. Allowlists grow only through a schema update
and privacy review.

### Command actions (`action`)

| Command | Allowed actions |
| --- | --- |
| `report` | `open`, `list`, `compare`, `prune`, `failures-only` |
| `baseline` | `set`, `list` |
| `telemetry` | `enable`, `disable`, `status`, `preview`, `reset-id` |
| `init`, `debug`, `run`, `diff`, `profile` | none — `action` is null |

### Check templates (`template`)

`template` is a **broad family**, never a one-to-one identity for a specific built-in or user check.
Built-in checks are grouped so the value cannot act as a check identifier:

```
existence | uniqueness | cardinality | relationship-shape | value-domain |
referential-integrity | connectivity | pii | competency-shape |
competency-regression | drift | custom
```

Any check that does not map to a built-in family is `custom`. A finer template that could identify
an individual check is prohibited without a consent review.

### Safe error codes (`error_code`, `safe_error_code`, `run_error_code`)

```
neo4j.unreachable | neo4j.auth_failed | neo4j.permission_denied |
neo4j.database_not_found | neo4j.query_failed |
project.missing | config.invalid | suite.invalid |
profile.missing | profile.invalid | profile.collection_failed |
baseline.missing | baseline.invalid | baseline.partial |
baseline.load_failed | baseline.write_failed |
diff.incomparable | diff.failed |
engine.compile_failed | engine.parameter_resolution_failed |
engine.evaluate_failed | engine.unexpected |
read_guard.rejected | artifact.write_failed |
report.render_failed | report.open_failed | unknown
```

### Exception types (`exception_type`)

Standard-library types only; any other type maps to `unknown`:

```
TimeoutError | ConnectionError | OSError | ValueError |
KeyError | TypeError | RuntimeError | MemoryError | unknown
```

### Stages

Engine stages (`engine_stage`):

```
probe | compile | resolve_params | sample | baseline | query | evaluate | finalize
```

Profiler stages (`last_completed_stage`) — profiling has its own stages, distinct from engine
execution:

```
probe | labels | relationship_types | constraints | indexes |
property_coverage | degree_distribution
```

CLI failure stages (`failure_stage`):

```
project_discovery | config_load | suite_load | profile_load | client_setup |
probe | engine | profile_collection | baseline_load | baseline_write |
diff_compare | artifact_write | report_render | report_open
```

### Related closed enums (defined at their events)

- `query_role`: `target_probe`, `parameter_resolution`, `sampling_population`, `check_measurement`, `evidence_collection`.
- `pattern`: `conformance`, `competency-shape`, `competency-regression`, `drift`, `unknown`.
- `skip_reason`: `generated`, `unsupported`, `not_run`.
- `partial_reason_codes` (run): `suite_input_invalid`, `unsupported_check`, `partial_baseline`, `baseline_measurement_missing`, `deadline_exhausted`, `unknown`.
- `partial_reason` (profile): `deadline_exhausted`, `property_coverage_incomplete`, `degree_distribution_incomplete`, `schema_incomplete`, `probe_incomplete`, `unknown`.
- Artifact outcomes (`results_artifact`, `report_artifact`, `baseline_artifact`): `not_requested`, `written`, `error`.

## Privacy denylist

No telemetry payload may contain:

- graph labels, relationship types, property names, property values, records, or exact graph counts;
- query text, query plans, parameters, result columns, evidence, baselines, expected values, or
  measured values;
- check IDs, check names, suite IDs, suite names, tags, questions, descriptions, or provenance;
- database names, URIs, usernames, passwords, profile names, target fingerprints, or server
  addresses;
- project names, repository names, branches, remotes, commit hashes, working directories, paths,
  filenames, file contents, or artifact run IDs;
- command-line arguments, environment-variable names or values, hostnames, OS usernames, emails,
  IP addresses, hardware IDs, or stable machine-derived identifiers;
- free-form error messages, Neo4j notifications, stack traces, exception representations, or local
  variables.

Payload construction is allowlist-only. A denylist assertion is defense in depth, not the primary
sanitization mechanism. The telemetry adapter must never serialize `Results`, `CheckResult`,
`RunTarget`, exceptions, driver records, configuration models, or arbitrary dictionaries directly.

## Runtime architecture

The engine accepts one optional, synchronous sink:

```python
class EngineEventSink(Protocol):
    def emit(self, event: EngineEvent) -> None: ...
```

Recommended module boundaries:

```
src/graphcheck/telemetry/
    events.py       # strict immutable engine-event models
    collector.py    # run/check aggregation and reconciliation
    policy.py       # consent, allowlists, bucketing, redaction
    posthog.py      # optional PostHog transport adapter
```

The engine must not import or instantiate the PostHog SDK. It constructs structured events only
when a sink is present. The collector receives events synchronously and performs no network or disk
I/O from `emit()`.

At the CLI boundary:

1. Resolve consent before constructing the engine.
2. If disabled, pass no sink and instantiate no PostHog client.
3. If enabled, create an in-memory collector and pass its sink to the engine.
4. Convert collector output to allowlisted PostHog payloads.
5. Submit telemetry best-effort with a bounded shutdown/flush deadline.
6. Emit `graphcheck_command_completed` after artifact/report handling, then stop waiting when the
   deadline expires.

Telemetry failures are swallowed after an optional local debug log. They must never change a
result, artifact, terminal output, exit code, retry policy, fail-fast behavior, or engine deadline.
GraphCheck accepts event loss rather than delaying or failing the user's command.

The telemetry path should add no database queries. Internal `QueryFinished` events describe queries
that GraphCheck already performs.

## Event invariants

1. `RunStarted` is the first engine event and occurs exactly once per attempted engine run.
2. A run has at most one terminal engine event: `RunFinished` or `EngineFaulted`, never both.
3. A normal engine return has exactly one `RunFinished`.
4. Every selected check produces exactly one `CheckProcessed`, including generated, unsupported,
   and not-run checks.
5. `check_sequence` is unique within a run and is not stable across runs.
6. Every `QueryFinished` belongs to the current `telemetry_run_id`; check-level queries reference a
   valid `check_sequence`.
7. Event `sequence` values are contiguous and strictly increasing.
8. Durations and counts are non-negative integers. Nullable fields use `null`, never sentinel
   negative values.
9. `outcome:error` or `processing_outcome:engine_error` requires a safe error code. A successful
   outcome has no error code.
10. `CheckProcessed.processing_outcome:skipped` requires `skip_reason`; other processing outcomes
    require it to be null.
11. On a normal terminal event, `selected_check_count` equals the number of `CheckProcessed`
    events and equals the sum of executed, engine-error, and skipped counts.
12. `RunFinished.query_count` and query timings reconcile with all preceding `QueryFinished`
    events.
13. Collector or sink failure cannot escape into engine control flow. After the first sink failure,
    the engine may disable that sink for the remainder of the run.
14. Telemetry-disabled execution constructs no telemetry collector, performs no PostHog calls, and
    produces no persistent telemetry identifier.
15. `process_outcome:success` means no operational failure at any stage. Exit codes 1 and 2 with a
    completed run are `success` (never CLI errors), but an independent operational failure after the
    run — such as a failed artifact write or report render — is non-success even though
    `telemetry_run_id` is non-null. `process_outcome` is therefore independent of exit code and
    independent of whether the engine ran.
16. `failure_stage` is non-null if and only if `process_outcome` is not `success`, and when a
    requested artifact fails its corresponding `*_artifact` field is `error`.
17. Every PostHog event from one CLI invocation shares a `telemetry_command_id`. When a run
    occurred, `graphcheck_command_completed.telemetry_run_id` equals the engine events'
    `telemetry_run_id`; when no run occurred it is null.
18. Run-level terminal status (`complete`/`partial`/`failed`) and early termination are collected as
    aggregate reliability data. Because `outcome:failed` already reveals, at the run level, that a
    check failed, combining `fail_fast_enabled`, `early_stopped`, and `deadline_exhausted` permits
    inferring that a fail-fast stop occurred. This run-level inference is **permitted**. Telemetry
    still never reveals which check stopped the run, its verdict, its measured or expected values, or
    its evidence.

## Events deliberately excluded

The following events are not justified for telemetry-only v0:

- `SuiteStarted` and `SuiteFinished`;
- `CheckQueued`, `CheckStarted`, and per-stage start events;
- `SamplingDecided` and `BaselineResolved`;
- `RunDegraded`, `FailFastTriggered`, and `DeadlineExhausted`;
- `QueryStarted` and `QueryNotificationObserved`;
- cancellation, worker, concurrency, cache, observer-delivery, and UI events.

Their useful dimensions are represented as fields or counters on the six engine events. Fail-fast
has no dedicated event or field; at the run level it remains inferable from `early_stopped`,
`deadline_exhausted`, and `fail_fast_enabled`, which is accepted as aggregate reliability data
(invariant 18) — telemetry still never names the stopping check or its verdict. Adding start/end
pairs for every operation would increase code paths, volume, ordering complexity, and privacy risk
without improving the planned PostHog metrics.

## Metrics and PostHog dashboards

### Activation and retention

- installations with at least one `graphcheck_run_completed` event;
- first-run completion rate: `graphcheck_run_started` → `graphcheck_run_completed`;
- opted-in installations active by week and GraphCheck version;
- commands used before the first completed run.

### Reliability

- engine terminal rate and missing-terminal rate;
- complete, partial, failed, and faulted run rates;
- check engine-error and unsupported rates by safe pattern/template and GraphCheck version;
- safe error-code frequency by engine stage and version;
- CLI failure rate by `failure_stage`;
- artifact/report failures from `graphcheck_command_completed`;
- connection-probe outcomes and versions outside runs (`init`, `debug`, `profile`);
- profile completion split (complete/partial/error) from `graphcheck_profile_completed`.

### Performance

- p50/p95 run duration by selected-check-count band and version;
- p50/p95 check duration by pattern/template;
- query time as a proportion of run and check duration;
- target-probe duration and error rate;
- sampling-population overhead versus sampled check query duration;
- deadline-exhaustion rate by configured budget band;
- setup / artifact-write / render time versus engine duration (pre/post-engine overhead);
- profile duration, per-stage timings (schema, property-coverage, degree-distribution), and
  deadline-exhaustion rate from `graphcheck_profile_completed`.

### Feature adoption

- conformance, competency, and drift usage;
- sampling and baseline usage;
- fail-fast, suite-filter, and tag-filter usage;
- command usage split across `init`, `debug`, `run`, `report`, `profile`, `diff`, and `baseline`;
- output-mode split (human vs JSON);
- Neo4j major/minor version and coarse capability availability.

The dashboards must not calculate or display graph-quality pass/fail rates because those outcomes
are intentionally absent from telemetry.

## Testing requirements

1. Event-model tests reject unknown keys, invalid enums, negative durations, and inconsistent
   outcome/error combinations.
2. Engine tests assert exact event order and cardinality for complete, partial, failed, skipped,
   early-stop, deadline, query-error, and unexpected-fault paths, and assert that no dedicated field
   or event identifies the fail-fast trigger or the stopping check (run-level inference from the
   combination of `fail_fast_enabled`, `early_stopped`, and `deadline_exhausted` is permitted per
   invariant 18).
3. Collector tests reconcile per-query, per-check, and per-run counts and timings.
4. Snapshot tests lock every PostHog payload shape and schema version.
5. Privacy tests recursively reject denylisted field names and representative sensitive values.
6. Property-based tests inject sensitive strings into checks, configs, errors, paths, queries,
   parameters, notifications, and results and assert that none reaches a payload.
7. Default-off tests assert zero PostHog imports/client construction, zero network calls, and no
   persistent installation ID.
8. Consent tests cover explicit enable/disable, `DO_NOT_TRACK`, environment overrides, reset ID,
   opt-in persistence across upgrades, re-consent only on `consent_version` change, and that
   `preview`, `disable`, `status`, and `reset-id` emit no telemetry. A process-only
   `GRAPHCHECK_TELEMETRY=1` test asserts that a fresh `distinct_id` is used whenever there is no
   active stored opt-in — including when an inactive installation UUID remains on disk from a
   since-disabled opt-in — that this `distinct_id` is not persisted, and that it changes between
   processes.
9. Failure-isolation tests make the sink and transport raise exceptions and assert identical
   results, artifacts, output, and exit codes.
10. Transport tests enforce a bounded final flush and tolerate offline, timeout, rate-limit, and
    malformed-response cases.
11. Integration tests use a fake PostHog transport; the test suite never sends real telemetry.
12. Command-boundary tests assert that a completed run exiting 1 or 2 reports
    `process_outcome:success`; that a post-run artifact-write or report-render failure reports a
    non-success outcome with the matching `failure_stage` and `*_artifact:error` even though
    `telemetry_run_id` is non-null; and that `failure_stage` is set exactly when the outcome is not
    success.
13. Correlation tests assert every event of one invocation shares `telemetry_command_id`, and that
    `telemetry_run_id` links `graphcheck_command_completed` to its engine run.
14. Allowlist tests assert that unknown actions, templates, error codes, exception types, and stages
    map to `unknown`/`custom`, that OS/Python versions remain coarse, and that no coarse cardinality
    bucket appears in any payload.
15. A `graphcheck_profile_completed` test covers complete, partial, and error outcomes — including a
    setup failure before profiling starts — asserts profiler-specific stages and `partial_reason`,
    exercises the per-stage timings, and asserts no profiled graph content leaves the process.

## Acceptance criteria

The specification is satisfied when:

- the engine emits the six event types with the invariants above;
- only the six allowlisted PostHog event names can leave the process;
- telemetry is off by default, persists its opt-in across upgrades, and cannot be enabled by a
  checked-in project;
- the PostHog SDK is absent from the engine dependency boundary;
- no graph content, coarse graph cardinality, stable project/check identity, check verdict,
  credentials, path, query, or free-form diagnostic text appears in telemetry;
- `process_outcome` reflects operational failure only: a completed run exiting 1 or 2 is `success`,
  while an independent post-run artifact/report failure is non-success even with a run present;
- telemetry failure has no observable effect except optional local debug output;
- the proposed reliability, performance, adoption, activation, and retention dashboards can be
  built exclusively from the allowlisted fields.

## Deliverables

- `src/graphcheck/telemetry/events.py` — strict, immutable event models and safe enums.
- `src/graphcheck/telemetry/collector.py` — event aggregation and invariant reconciliation.
- `src/graphcheck/telemetry/policy.py` — consent resolution, bucketing, payload allowlists, and
  privacy assertions.
- `src/graphcheck/telemetry/posthog.py` — optional, best-effort PostHog adapter.
- engine instrumentation at the run, probe, query, check, terminal, and unexpected-fault
  boundaries.
- CLI instrumentation for `graphcheck_command_completed`, `graphcheck_profile_completed`, and
  telemetry controls.
- unit, property, snapshot, failure-isolation, and integration tests described above
