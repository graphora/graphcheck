"""Consent, closed allowlists, coarse environment data, and privacy assertions."""

from __future__ import annotations

import os
import platform
import re
import sys
from collections.abc import Iterable, Mapping
from typing import Annotated

from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from graphcheck.telemetry.consent import (
    disable_telemetry as disable_telemetry,
)
from graphcheck.telemetry.consent import (
    enable_telemetry as enable_telemetry,
)
from graphcheck.telemetry.consent import (
    reset_installation_id as reset_installation_id,
)
from graphcheck.telemetry.consent import (
    resolve_consent as resolve_consent,
)
from graphcheck.telemetry.consent import (
    user_config_path as user_config_path,
)
from graphcheck.telemetry.events import (
    Pattern,
    SafeExceptionType,
    Template,
)
from graphcheck.telemetry.types import (
    CONSENT_VERSION as CONSENT_VERSION,
)
from graphcheck.telemetry.types import (
    TELEMETRY_SCHEMA_VERSION as TELEMETRY_SCHEMA_VERSION,
)
from graphcheck.telemetry.types import (
    ArtifactOutcome,
    CliFailureStage,
    CommandAction,
    CommandName,
    EventOutcome,
    OsFamily,
    OutputMode,
    ProcessOutcome,
    ProfileOutcome,
    ProfilePartialReason,
    ProfilerStage,
    SafeErrorCode,
    safe_action,
)
from graphcheck.telemetry.types import (
    ConsentSource as ConsentSource,
)
from graphcheck.telemetry.types import (
    ConsentState as ConsentState,
)
from graphcheck.telemetry.types import (
    safe_command as safe_command,
)

_CI_INDICATORS = frozenset(
    {
        "CI",
        "BUILD_ID",
        "BUILD_NUMBER",
        "GITHUB_ACTIONS",
        "GITLAB_CI",
        "JENKINS_URL",
        "TEAMCITY_VERSION",
        "TF_BUILD",
        "TRAVIS",
        "CIRCLECI",
        "BUILDKITE",
    }
)

NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


class _StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CommandCompleted(_StrictPayload):
    command: CommandName
    action: CommandAction | None
    process_outcome: ProcessOutcome
    failure_stage: CliFailureStage | None
    duration_ms: NonNegativeInt
    setup_ms: NonNegativeInt | None
    artifact_write_ms: NonNegativeInt | None
    render_ms: NonNegativeInt | None
    output_mode: OutputMode
    results_artifact: ArtifactOutcome
    report_artifact: ArtifactOutcome
    baseline_artifact: ArtifactOutcome
    generated_artifact: ArtifactOutcome
    telemetry_command_id: UUID4
    telemetry_run_id: UUID4 | None
    probe_outcome: EventOutcome | None
    probe_duration_ms: NonNegativeInt | None
    server_version_major: NonNegativeInt | None
    server_version_minor: NonNegativeInt | None
    apoc_available: bool | None
    count_store_available: bool | None
    interactive: bool
    ci: bool
    os_family: OsFamily
    os_version: str
    python_minor: str
    graphcheck_version: str
    safe_error_code: SafeErrorCode | None

    @field_validator("python_minor")
    @classmethod
    def python_minor_is_coarse(cls, value: str) -> str:
        parts = value.split(".")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("python_minor must contain only major.minor")
        return value

    @field_validator("os_version")
    @classmethod
    def os_version_is_coarse(cls, value: str) -> str:
        parts = value.split(".")
        if value != "unknown" and (
            len(parts) not in {1, 2}
            or not all(part.isdigit() and 1 <= len(part) <= 4 for part in parts)
        ):
            raise ValueError("os_version must be unknown, major, or major.minor")
        return value

    @model_validator(mode="after")
    def fields_are_consistent(self) -> CommandCompleted:
        if safe_action(self.command, self.action) is not self.action:
            raise ValueError("action is not allowlisted for command")
        success = self.process_outcome is ProcessOutcome.SUCCESS
        if success != (self.failure_stage is None):
            raise ValueError("failure_stage is set if and only if process_outcome is not success")
        if success != (self.safe_error_code is None):
            raise ValueError("safe_error_code is set if and only if process_outcome is not success")
        probe_details = (
            self.server_version_major,
            self.server_version_minor,
            self.apoc_available,
            self.count_store_available,
        )
        if self.probe_outcome is None:
            if self.probe_duration_ms is not None or any(
                value is not None for value in probe_details
            ):
                raise ValueError("probe details require probe_outcome")
        elif self.probe_duration_ms is None:
            raise ValueError("probe_outcome requires probe_duration_ms")
        if self.results_artifact is ArtifactOutcome.ERROR and self.failure_stage not in {
            CliFailureStage.ARTIFACT_WRITE,
            CliFailureStage.REPORT_RENDER,
        }:
            raise ValueError("a failed results artifact requires artifact/report failure_stage")
        if self.report_artifact is ArtifactOutcome.ERROR and self.failure_stage not in {
            CliFailureStage.ARTIFACT_WRITE,
            CliFailureStage.REPORT_RENDER,
        }:
            raise ValueError("a failed report artifact requires an artifact/report failure stage")
        if (
            self.baseline_artifact is ArtifactOutcome.ERROR
            and self.failure_stage is not CliFailureStage.BASELINE_WRITE
        ):
            raise ValueError("a failed baseline artifact requires baseline_write failure_stage")
        if (
            self.generated_artifact is ArtifactOutcome.ERROR
            and self.failure_stage is not CliFailureStage.ARTIFACT_WRITE
        ):
            raise ValueError("a failed generated artifact requires artifact_write failure_stage")
        if (
            self.generated_artifact is not ArtifactOutcome.NOT_REQUESTED
            and self.artifact_write_ms is None
        ):
            raise ValueError("a requested generated artifact requires artifact_write_ms")
        return self


class ProfileCompleted(_StrictPayload):
    outcome: ProfileOutcome
    duration_ms: NonNegativeInt
    schema_ms: NonNegativeInt | None
    property_coverage_ms: NonNegativeInt | None
    degree_distribution_ms: NonNegativeInt | None
    deadline_exhausted: bool
    last_completed_stage: ProfilerStage | None
    partial_reason: ProfilePartialReason | None
    probe_outcome: EventOutcome | None
    probe_duration_ms: NonNegativeInt | None
    server_version_major: NonNegativeInt | None
    server_version_minor: NonNegativeInt | None
    apoc_available: bool | None
    count_store_available: bool | None
    safe_error_code: SafeErrorCode | None

    @model_validator(mode="after")
    def fields_are_consistent(self) -> ProfileCompleted:
        if (self.outcome is ProfileOutcome.PARTIAL) != (self.partial_reason is not None):
            raise ValueError("partial_reason is set if and only if profile outcome is partial")
        if (self.outcome is ProfileOutcome.ERROR) != (self.safe_error_code is not None):
            raise ValueError("safe_error_code is set if and only if profile outcome is error")
        probe_details = (
            self.server_version_major,
            self.server_version_minor,
            self.apoc_available,
            self.count_store_available,
        )
        if self.probe_outcome is None:
            if self.probe_duration_ms is not None or any(
                value is not None for value in probe_details
            ):
                raise ValueError("probe details require probe_outcome")
        elif self.probe_duration_ms is None:
            raise ValueError("probe_outcome requires probe_duration_ms")
        return self


_TEMPLATE_MAP: dict[str, Template] = {
    "completeness": Template.EXISTENCE,
    "uniqueness": Template.UNIQUENESS,
    "cardinality": Template.CARDINALITY,
    "hub_outlier": Template.CARDINALITY,
    "label_cooccurrence": Template.RELATIONSHIP_SHAPE,
    "rel_direction": Template.RELATIONSHIP_SHAPE,
    "property_type": Template.VALUE_DOMAIN,
    "property_format": Template.VALUE_DOMAIN,
    "value_in_set": Template.VALUE_DOMAIN,
    "temporal_sanity": Template.VALUE_DOMAIN,
    "dangling_rels": Template.REFERENTIAL_INTEGRITY,
    "no_orphans": Template.CONNECTIVITY,
    "pii_name_match": Template.PII,
    "pii_value_match": Template.PII,
    "competency-shape": Template.COMPETENCY_SHAPE,
    "competency-regression": Template.COMPETENCY_REGRESSION,
    "drift": Template.DRIFT,
}

_ERROR_CODE_MAP: dict[str, SafeErrorCode] = {
    code.value: code for code in SafeErrorCode if code is not SafeErrorCode.UNKNOWN
}
_ERROR_CODE_MAP.update(
    {
        "profile.not_found": SafeErrorCode.PROFILE_INVALID,
        "profile.password_missing": SafeErrorCode.PROFILE_INVALID,
        "profile.uri_invalid": SafeErrorCode.PROFILE_INVALID,
        "profile.counts_unavailable": SafeErrorCode.PROFILE_COLLECTION_FAILED,
        "baseline.not_found": SafeErrorCode.BASELINE_MISSING,
        "baseline.current_missing": SafeErrorCode.BASELINE_MISSING,
        "baseline.current_invalid": SafeErrorCode.BASELINE_INVALID,
        "diff.partial_baseline": SafeErrorCode.BASELINE_PARTIAL,
        "run.suite_invalid": SafeErrorCode.SUITE_INVALID,
        "run.checks_missing": SafeErrorCode.SUITE_INVALID,
        "run.checks_unreadable": SafeErrorCode.SUITE_INVALID,
        "run.invalid_selector": SafeErrorCode.CONFIG_INVALID,
        "run.configuration": SafeErrorCode.CONFIG_INVALID,
        "engine.baseline_missing": SafeErrorCode.BASELINE_MISSING,
        "engine.baseline_invalid": SafeErrorCode.BASELINE_INVALID,
        "engine.baseline_partial_missing": SafeErrorCode.BASELINE_PARTIAL,
        "neo4j.write_rejected": SafeErrorCode.READ_GUARD_REJECTED,
        "neo4j.read_guard_unavailable": SafeErrorCode.READ_GUARD_REJECTED,
        "engine.timeout": SafeErrorCode.NEO4J_QUERY_FAILED,
        "generate.config_missing": SafeErrorCode.CONFIG_INVALID,
        "generate.config_invalid": SafeErrorCode.CONFIG_INVALID,
        "generate.api_key_missing": SafeErrorCode.CONFIG_INVALID,
        "generate.provider_unsupported": SafeErrorCode.CONFIG_INVALID,
        "generate.baseline_missing": SafeErrorCode.BASELINE_MISSING,
        "generate.baseline_not_found": SafeErrorCode.BASELINE_MISSING,
        "generate.baseline_invalid": SafeErrorCode.BASELINE_INVALID,
        "generate.doc_not_found": SafeErrorCode.CONFIG_INVALID,
        "generate.doc_invalid": SafeErrorCode.CONFIG_INVALID,
        "generate.doc_too_large": SafeErrorCode.CONFIG_INVALID,
        "generate.write_failed": SafeErrorCode.ARTIFACT_WRITE_FAILED,
        "generate.write_invalid": SafeErrorCode.ARTIFACT_WRITE_FAILED,
    }
)

_COMPILE_ERROR_PREFIXES = (
    "engine.compiler",
    "engine.empty_query",
    "engine.invalid_check",
    "engine.invalid_target",
    "engine.metric_",
    "engine.unsupported_pattern",
    "engine.sampling_invalid",
    "packs.",
)
_PARAMETER_ERROR_PREFIXES = ("engine.parameter_",)
_EVALUATION_ERROR_PREFIXES = (
    "engine.evaluation_",
    "engine.evidence_",
    "engine.invalid_evaluation",
    "engine.invalid_query_result",
    "engine.schema_reference_",
    "engine.tolerance_",
)

_SAFE_EXCEPTION_TYPES: dict[type[BaseException], SafeExceptionType] = {
    TimeoutError: SafeExceptionType.TIMEOUT_ERROR,
    ConnectionError: SafeExceptionType.CONNECTION_ERROR,
    OSError: SafeExceptionType.OS_ERROR,
    ValueError: SafeExceptionType.VALUE_ERROR,
    KeyError: SafeExceptionType.KEY_ERROR,
    TypeError: SafeExceptionType.TYPE_ERROR,
    RuntimeError: SafeExceptionType.RUNTIME_ERROR,
    MemoryError: SafeExceptionType.MEMORY_ERROR,
}

_POSTHOG_COMMON_PROPERTY_KEYS = frozenset(
    {
        "telemetry_schema_version",
        "consent_version",
        "graphcheck_version",
        "distinct_id",
        "session_id",
        "telemetry_command_id",
        "process_person_profile",
        "geoip_enrichment",
        "$process_person_profile",
        "$geoip_disable",
    }
)
_ENGINE_ENVELOPE_PROPERTY_KEYS = frozenset(
    {
        "engine_event_schema_version",
        "engine_event_id",
        "telemetry_run_id",
        "engine_event_sequence",
        "engine_event_occurred_at",
        "engine_event_kind",
    }
)
_RUN_STARTED_PROPERTY_KEYS = _ENGINE_ENVELOPE_PROPERTY_KEYS | {
    "graphcheck_version",
    "pack_version",
    "suite_count",
    "selected_check_count",
    "conformance_count",
    "competency_count",
    "drift_count",
    "uses_sampling",
    "uses_baselines",
    "fail_fast_enabled",
    "suite_filter_used",
    "tag_filter_used",
    "time_budget_ms",
}
_CHECK_PROCESSED_PROPERTY_KEYS = _ENGINE_ENVELOPE_PROPERTY_KEYS | {
    "check_sequence",
    "pattern",
    "template",
    "processing_outcome",
    "skip_reason",
    "duration_ms",
    "compile_ms",
    "parameter_resolution_ms",
    "sampling_population_ms",
    "baseline_resolution_ms",
    "read_guard_ms",
    "query_ms",
    "evaluation_ms",
    "query_count",
    "sampled",
    "error_code",
    "aggregated_query_count",
    "aggregated_query_total_ms",
    "aggregated_query_max_ms",
    "query_success_count",
    "query_error_count",
    "query_timeout_count",
    "server_available_total_ms",
    "server_consumed_total_ms",
    "notification_count_total",
    "read_guard_rejected_count",
}
_RUN_FINISHED_PROPERTY_KEYS = _ENGINE_ENVELOPE_PROPERTY_KEYS | {
    "terminal_kind",
    "outcome",
    "duration_ms",
    "selected_check_count",
    "executed_check_count",
    "engine_error_count",
    "skipped_generated_count",
    "skipped_unsupported_count",
    "skipped_not_run_count",
    "query_count",
    "query_total_ms",
    "query_max_ms",
    "probe_ms",
    "probe_outcome",
    "budget_remaining_ms",
    "early_stopped",
    "deadline_exhausted",
    "partial_reason_codes",
    "run_error_code",
}
_ENGINE_FAULT_PROPERTY_KEYS = _ENGINE_ENVELOPE_PROPERTY_KEYS | {
    "engine_stage",
    "exception_type",
    "safe_error_code",
    "elapsed_ms",
}
_FAULT_COMPLETION_PROPERTY_KEYS = _ENGINE_FAULT_PROPERTY_KEYS | {
    "terminal_kind",
    "selected_check_count",
    "processed_check_count",
    "query_count",
    "query_total_ms",
    "query_max_ms",
    "probe_ms",
}
_COMMAND_COMPLETED_PROPERTY_KEYS = frozenset(
    {
        "command",
        "action",
        "process_outcome",
        "failure_stage",
        "duration_ms",
        "setup_ms",
        "artifact_write_ms",
        "render_ms",
        "output_mode",
        "results_artifact",
        "report_artifact",
        "baseline_artifact",
        "generated_artifact",
        "telemetry_run_id",
        "probe_outcome",
        "probe_duration_ms",
        "server_version_major",
        "server_version_minor",
        "apoc_available",
        "count_store_available",
        "interactive",
        "ci",
        "os_family",
        "os_version",
        "python_minor",
        "graphcheck_version",
        "safe_error_code",
    }
)
_PROFILE_COMPLETED_PROPERTY_KEYS = frozenset(
    {
        "outcome",
        "duration_ms",
        "schema_ms",
        "property_coverage_ms",
        "degree_distribution_ms",
        "deadline_exhausted",
        "last_completed_stage",
        "partial_reason",
        "probe_outcome",
        "probe_duration_ms",
        "server_version_major",
        "server_version_minor",
        "apoc_available",
        "count_store_available",
        "safe_error_code",
    }
)
_POSTHOG_EVENT_PROPERTY_SCHEMAS: dict[str, tuple[frozenset[str], ...]] = {
    "graphcheck_run_started": (frozenset(_RUN_STARTED_PROPERTY_KEYS),),
    "graphcheck_check_processed": (frozenset(_CHECK_PROCESSED_PROPERTY_KEYS),),
    "graphcheck_run_completed": (
        frozenset(_RUN_FINISHED_PROPERTY_KEYS),
        frozenset(_FAULT_COMPLETION_PROPERTY_KEYS),
    ),
    "graphcheck_engine_faulted": (frozenset(_ENGINE_FAULT_PROPERTY_KEYS),),
    "graphcheck_command_completed": (frozenset(_COMMAND_COMPLETED_PROPERTY_KEYS),),
    "graphcheck_profile_completed": (frozenset(_PROFILE_COMPLETED_PROPERTY_KEYS),),
}

_DENIED_FIELD_NAMES = frozenset(
    {
        "args",
        "argv",
        "artifact_run_id",
        "baseline_value",
        "branch",
        "check_id",
        "check_name",
        "command_line_arguments",
        "commit_hash",
        "compiled_query",
        "cwd",
        "database",
        "database_name",
        "description",
        "destination",
        "document",
        "document_contents",
        "edition",
        "email",
        "environment",
        "environment_variable_name",
        "environment_variable_names",
        "environment_variable_value",
        "environment_variable_values",
        "error_message",
        "evidence",
        "exception_repr",
        "exception_representation",
        "expected",
        "expected_value",
        "file",
        "file_contents",
        "filename",
        "fingerprint",
        "fix",
        "hardware_id",
        "hostname",
        "ip",
        "ip_address",
        "label",
        "labels",
        "local_variables",
        "locals",
        "measured",
        "measured_value",
        "message",
        "model",
        "model_name",
        "notification",
        "notification_position",
        "notification_text",
        "notifications",
        "os_username",
        "params",
        "parameters",
        "password",
        "path",
        "plan",
        "profile",
        "profile_name",
        "project",
        "project_name",
        "prompt",
        "property",
        "property_name",
        "property_names",
        "property_value",
        "property_values",
        "provenance",
        "provider",
        "provider_name",
        "question",
        "query",
        "query_plan",
        "query_text",
        "record",
        "records",
        "relationship_type",
        "relationship_types",
        "remote",
        "repository",
        "repository_name",
        "result_columns",
        "sample_size",
        "server_address",
        "shell",
        "stack_trace",
        "stacktrace",
        "severity",
        "suite_id",
        "suite_name",
        "tags",
        "traceback",
        "uri",
        "user",
        "username",
        "verdict",
        "working_directory",
    }
)
_CARDINALITY_FIELD_NAMES = frozenset(
    {
        "exact_count",
        "node_count",
        "population",
        "population_bucket",
        "population_count",
        "relationship_count",
        "row_count",
        "row_count_bucket",
        "sample_size",
        "sample_size_bucket",
    }
)


def safe_pattern(value: object) -> Pattern:
    try:
        return Pattern(str(value))
    except ValueError:
        # Engine pattern inputs are already strict. This fallback can only be reached by an
        # integration bug and must still avoid forwarding the arbitrary value.
        return Pattern.UNKNOWN


def safe_template(value: object) -> Template:
    return _TEMPLATE_MAP.get(str(value), Template.CUSTOM)


def safe_error_code(value: object | None) -> SafeErrorCode | None:
    if value is None:
        return None
    raw = str(value)
    if raw in _ERROR_CODE_MAP:
        return _ERROR_CODE_MAP[raw]
    if raw.startswith(_COMPILE_ERROR_PREFIXES):
        return SafeErrorCode.ENGINE_COMPILE_FAILED
    if raw.startswith(_PARAMETER_ERROR_PREFIXES):
        return SafeErrorCode.ENGINE_PARAMETER_RESOLUTION_FAILED
    if raw.startswith(_EVALUATION_ERROR_PREFIXES):
        return SafeErrorCode.ENGINE_EVALUATE_FAILED
    if raw.startswith("neo4j."):
        return SafeErrorCode.NEO4J_QUERY_FAILED
    if raw.startswith("engine."):
        return SafeErrorCode.ENGINE_UNEXPECTED
    return SafeErrorCode.UNKNOWN


def safe_exception_type(
    exc_or_type: BaseException | type[BaseException] | object,
) -> SafeExceptionType:
    exception_type = exc_or_type if isinstance(exc_or_type, type) else type(exc_or_type)
    return _SAFE_EXCEPTION_TYPES.get(exception_type, SafeExceptionType.UNKNOWN)


def os_family(system: str | None = None) -> OsFamily:
    name = (platform.system() if system is None else system).lower()
    if name == "windows":
        return OsFamily.WINDOWS
    if name == "darwin":
        return OsFamily.MACOS
    if name == "linux":
        return OsFamily.LINUX
    return OsFamily.OTHER


def os_version(system: str | None = None, version: str | None = None) -> str:
    """Return only the OS major/minor version, excluding builds and distribution details."""

    name = (platform.system() if system is None else system).lower()
    try:
        raw = (
            platform.mac_ver()[0]
            if version is None and name == "darwin"
            else platform.release()
            if version is None
            else version
        )
    except Exception:
        return "unknown"
    match = re.match(r"^(\d{1,4})(?:\.(\d{1,4}))?", raw.strip())
    if match is None:
        return "unknown"
    return ".".join(part for part in match.groups() if part is not None)


def python_minor(version_info: object = sys.version_info) -> str:
    return f"{version_info.major}.{version_info.minor}"


def is_ci(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return any(name in env for name in _CI_INDICATORS)


def version_major_minor(version: object | None) -> tuple[int | None, int | None]:
    if version is None:
        return None, None
    parts = str(version).split(".")
    try:
        major = int(parts[0])
    except (ValueError, IndexError):
        return None, None
    if major < 0:
        return None, None
    try:
        minor = int(parts[1]) if len(parts) > 1 else None
    except ValueError:
        return major, None
    if minor is not None and minor < 0:
        return major, None
    return major, minor


def count_band(value: int | None) -> str:
    """Return the fixed dashboard bucket without exposing a new payload dimension by default."""

    if value is None:
        return "unknown"
    if value == 0:
        return "0"
    if value <= 5:
        return "1-5"
    if value <= 20:
        return "6-20"
    if value <= 100:
        return "21-100"
    return "101+"


def assert_private_payload(
    payload: Mapping[str, object],
    *,
    sensitive_values: Iterable[object] = (),
) -> None:
    """Defense-in-depth assertion for already allowlisted outbound properties."""

    forbidden_values = tuple(
        str(value) for value in sensitive_values if value is not None and str(value)
    )

    def walk(value: object, path: tuple[str, ...]) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = str(key).lower()
                if normalized in _DENIED_FIELD_NAMES or normalized in _CARDINALITY_FIELD_NAMES:
                    dotted = ".".join((*path, str(key)))
                    raise ValueError(f"privacy-denied telemetry field: {dotted}")
                walk(child, (*path, str(key)))
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, (*path, str(index)))
        elif isinstance(value, str):
            for forbidden in forbidden_values:
                if forbidden in value:
                    raise ValueError("representative sensitive value reached telemetry payload")

    walk(payload, ())


def assert_allowlisted_posthog_payload(
    event_name: str,
    payload: Mapping[str, object],
    *,
    includes_common: bool = False,
) -> None:
    """Require one exact, reviewed property shape before an event may leave the process."""

    schemas = _POSTHOG_EVENT_PROPERTY_SCHEMAS.get(event_name)
    if schemas is None:
        raise ValueError("PostHog event name is not allowlisted")
    allowed = (
        tuple(schema | _POSTHOG_COMMON_PROPERTY_KEYS for schema in schemas)
        if includes_common
        else schemas
    )
    if frozenset(payload) not in allowed:
        raise ValueError("PostHog properties do not match the event's allowlisted schema")
    assert_private_payload(payload)
