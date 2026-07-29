# GraphCheck telemetry

GraphCheck telemetry is disabled by default. It starts only after a user explicitly runs
`graphcheck telemetry enable`, and it can be stopped with `graphcheck telemetry disable`.
Telemetry delivery is best-effort and never changes command output, artifacts, or exit behavior.

GraphCheck sends structural and aggregate product signals only. The PostHog project key is a
public ingestion identifier; builds without that key store consent but do not send events.

## Events

| Event | When it is captured |
| --- | --- |
| `graphcheck_run_started` | An opted-in engine run starts. |
| `graphcheck_check_processed` | One selected check finishes processing or is skipped. |
| `graphcheck_run_completed` | An engine run reaches either its normal or faulted terminal state. |
| `graphcheck_engine_faulted` | An unexpected engine boundary failure occurs. |
| `graphcheck_command_completed` | An opted-in CLI command finishes. |
| `graphcheck_profile_completed` | Profiling finishes, partially finishes, or fails. |

`graphcheck_run_completed` has two reviewed field shapes: one for a normal terminal event and one
for a faulted terminal event. Every event also receives the common properties listed below.

The block below is both the human-readable field inventory and the CI contract. Field lists are
event-specific; `common_properties` are added to every outgoing event. CI fails if an event name or
field in the code allowlist differs from this document.

<!-- telemetry-allowlist:start -->
```json
{
  "common_properties": [
    "$geoip_disable",
    "$process_person_profile",
    "consent_version",
    "distinct_id",
    "geoip_enrichment",
    "graphcheck_version",
    "process_person_profile",
    "session_id",
    "telemetry_command_id",
    "telemetry_schema_version"
  ],
  "events": {
    "graphcheck_check_processed": [
      [
        "aggregated_query_count",
        "aggregated_query_max_ms",
        "aggregated_query_total_ms",
        "baseline_resolution_ms",
        "check_sequence",
        "compile_ms",
        "duration_ms",
        "engine_event_id",
        "engine_event_kind",
        "engine_event_occurred_at",
        "engine_event_schema_version",
        "engine_event_sequence",
        "error_code",
        "evaluation_ms",
        "notification_count_total",
        "parameter_resolution_ms",
        "pattern",
        "processing_outcome",
        "query_count",
        "query_error_count",
        "query_ms",
        "query_success_count",
        "query_timeout_count",
        "read_guard_ms",
        "read_guard_rejected_count",
        "sampled",
        "sampling_population_ms",
        "server_available_total_ms",
        "server_consumed_total_ms",
        "skip_reason",
        "telemetry_run_id",
        "template"
      ]
    ],
    "graphcheck_command_completed": [
      [
        "action",
        "apoc_available",
        "artifact_write_ms",
        "baseline_artifact",
        "ci",
        "command",
        "count_store_available",
        "duration_ms",
        "failure_stage",
        "graphcheck_version",
        "interactive",
        "os_family",
        "output_mode",
        "probe_duration_ms",
        "probe_outcome",
        "process_outcome",
        "python_minor",
        "render_ms",
        "report_artifact",
        "results_artifact",
        "safe_error_code",
        "server_version_major",
        "server_version_minor",
        "setup_ms",
        "telemetry_run_id"
      ]
    ],
    "graphcheck_engine_faulted": [
      [
        "elapsed_ms",
        "engine_event_id",
        "engine_event_kind",
        "engine_event_occurred_at",
        "engine_event_schema_version",
        "engine_event_sequence",
        "engine_stage",
        "exception_type",
        "safe_error_code",
        "telemetry_run_id"
      ]
    ],
    "graphcheck_profile_completed": [
      [
        "apoc_available",
        "count_store_available",
        "deadline_exhausted",
        "degree_distribution_ms",
        "duration_ms",
        "last_completed_stage",
        "outcome",
        "partial_reason",
        "probe_duration_ms",
        "probe_outcome",
        "property_coverage_ms",
        "safe_error_code",
        "schema_ms",
        "server_version_major",
        "server_version_minor"
      ]
    ],
    "graphcheck_run_completed": [
      [
        "budget_remaining_ms",
        "deadline_exhausted",
        "duration_ms",
        "early_stopped",
        "engine_error_count",
        "engine_event_id",
        "engine_event_kind",
        "engine_event_occurred_at",
        "engine_event_schema_version",
        "engine_event_sequence",
        "executed_check_count",
        "outcome",
        "partial_reason_codes",
        "probe_ms",
        "probe_outcome",
        "query_count",
        "query_max_ms",
        "query_total_ms",
        "run_error_code",
        "selected_check_count",
        "skipped_generated_count",
        "skipped_not_run_count",
        "skipped_unsupported_count",
        "telemetry_run_id",
        "terminal_kind"
      ],
      [
        "elapsed_ms",
        "engine_event_id",
        "engine_event_kind",
        "engine_event_occurred_at",
        "engine_event_schema_version",
        "engine_event_sequence",
        "engine_stage",
        "exception_type",
        "probe_ms",
        "processed_check_count",
        "query_count",
        "query_max_ms",
        "query_total_ms",
        "safe_error_code",
        "selected_check_count",
        "telemetry_run_id",
        "terminal_kind"
      ]
    ],
    "graphcheck_run_started": [
      [
        "competency_count",
        "conformance_count",
        "drift_count",
        "engine_event_id",
        "engine_event_kind",
        "engine_event_occurred_at",
        "engine_event_schema_version",
        "engine_event_sequence",
        "fail_fast_enabled",
        "graphcheck_version",
        "pack_version",
        "selected_check_count",
        "suite_count",
        "suite_filter_used",
        "tag_filter_used",
        "telemetry_run_id",
        "time_budget_ms",
        "uses_baselines",
        "uses_sampling"
      ]
    ]
  }
}
```
<!-- telemetry-allowlist:end -->

## Explicitly excluded

No telemetry payload contains:

- graph labels, relationship types, property names or values, records, or exact graph counts;
- query text, plans, parameters, result columns, evidence, baselines, expected values, or measured
  values;
- check IDs or names, suite IDs or names, tags, questions, descriptions, or provenance;
- database names, URIs, credentials, profile names, target fingerprints, or server addresses;
- generation provider names, model names, destinations, prompts, or document contents;
- project or repository names, branches, remotes, commit hashes, paths, filenames, file contents, or
  artifact run IDs;
- command-line arguments, environment-variable names or values, hostnames, usernames, emails, IP
  addresses, hardware identifiers, or stable machine-derived identifiers;
- free-form errors, driver notifications, stack traces, exception representations, or local
  variables;
- check verdicts or raw CLI exit codes.

`process_outcome` reports whether GraphCheck operated successfully. A completed run remains
`success` when its checks produce exit code 1 or 2, so telemetry cannot be used to infer check
results.

## Delivery and identity

Events are queued on a daemon worker and sent with short timeouts. Full queues, network failures,
timeouts, rate limits, and malformed responses cause event loss only. GraphCheck does not display
telemetry transport errors.

An opted-in installation uses a random UUID. GraphCheck disables PostHog person-profile processing
and geographic enrichment. A process-only `GRAPHCHECK_TELEMETRY=1` override uses a non-persistent
process UUID when no stored opt-in exists.

For the complete field semantics and consent rules, see
[SPEC-10](specs/SPEC-10-telemetry-events.md).
