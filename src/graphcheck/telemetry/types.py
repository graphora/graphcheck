"""Standard-library-only telemetry enums shared by CLI and payload models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

TELEMETRY_SCHEMA_VERSION = "1.1"
CONSENT_VERSION = "1.0"


class ConsentSource(StrEnum):
    DEFAULT = "default"
    STORED = "stored"
    ENVIRONMENT = "environment"
    DO_NOT_TRACK = "do_not_track"


@dataclass(frozen=True)
class ConsentState:
    enabled: bool
    source: ConsentSource
    consent_version: str | None = None
    distinct_id: UUID | None = None
    persistent: bool = False
    renewal_required: bool = False

    def __post_init__(self) -> None:
        if self.enabled and (self.distinct_id is None or self.consent_version is None):
            raise ValueError("enabled consent requires a distinct ID and consent version")
        if not self.enabled and self.distinct_id is not None:
            raise ValueError("disabled consent cannot expose a distinct ID")
        if self.distinct_id is not None and (
            not isinstance(self.distinct_id, UUID) or self.distinct_id.version != 4
        ):
            raise ValueError("distinct_id must be a random UUID v4")


class CommandName(StrEnum):
    INIT = "init"
    DEBUG = "debug"
    RUN = "run"
    REPORT = "report"
    PROFILE = "profile"
    GENERATE = "generate"
    DIFF = "diff"
    BASELINE = "baseline"
    TELEMETRY = "telemetry"
    OTHER = "other"


class CommandAction(StrEnum):
    OPEN = "open"
    LIST = "list"
    COMPARE = "compare"
    PRUNE = "prune"
    FAILURES_ONLY = "failures-only"
    SET = "set"
    ENABLE = "enable"
    DISABLE = "disable"
    STATUS = "status"
    PREVIEW = "preview"
    RESET_ID = "reset-id"
    UNKNOWN = "unknown"


class ProcessOutcome(StrEnum):
    SUCCESS = "success"
    USER_ERROR = "user_error"
    ENGINE_ERROR = "engine_error"
    UNEXPECTED_ERROR = "unexpected_error"


class CliFailureStage(StrEnum):
    PROJECT_DISCOVERY = "project_discovery"
    CONFIG_LOAD = "config_load"
    SUITE_LOAD = "suite_load"
    PROFILE_LOAD = "profile_load"
    CLIENT_SETUP = "client_setup"
    PROBE = "probe"
    ENGINE = "engine"
    PROFILE_COLLECTION = "profile_collection"
    BASELINE_LOAD = "baseline_load"
    DOCUMENT_LOAD = "document_load"
    PROVIDER_REQUEST = "provider_request"
    GENERATION_VALIDATION = "generation_validation"
    BASELINE_WRITE = "baseline_write"
    DIFF_COMPARE = "diff_compare"
    ARTIFACT_WRITE = "artifact_write"
    REPORT_RENDER = "report_render"
    REPORT_OPEN = "report_open"


class OutputMode(StrEnum):
    HUMAN = "human"
    JSON = "json"


class ArtifactOutcome(StrEnum):
    NOT_REQUESTED = "not_requested"
    WRITTEN = "written"
    ERROR = "error"


class OsFamily(StrEnum):
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    OTHER = "other"


class ProfileOutcome(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    ERROR = "error"


class ProfilerStage(StrEnum):
    PROBE = "probe"
    LABELS = "labels"
    RELATIONSHIP_TYPES = "relationship_types"
    CONSTRAINTS = "constraints"
    INDEXES = "indexes"
    PROPERTY_COVERAGE = "property_coverage"
    DEGREE_DISTRIBUTION = "degree_distribution"


class ProfilePartialReason(StrEnum):
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    PROPERTY_COVERAGE_INCOMPLETE = "property_coverage_incomplete"
    DEGREE_DISTRIBUTION_INCOMPLETE = "degree_distribution_incomplete"
    SCHEMA_INCOMPLETE = "schema_incomplete"
    PROBE_INCOMPLETE = "probe_incomplete"
    UNKNOWN = "unknown"


class EventOutcome(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


class SafeErrorCode(StrEnum):
    NEO4J_UNREACHABLE = "neo4j.unreachable"
    NEO4J_AUTH_FAILED = "neo4j.auth_failed"
    NEO4J_PERMISSION_DENIED = "neo4j.permission_denied"
    NEO4J_DATABASE_NOT_FOUND = "neo4j.database_not_found"
    NEO4J_TLS_MISMATCH = "neo4j.tls_mismatch"
    NEO4J_CREDENTIAL_NOT_READ_ONLY = "neo4j.credential_not_read_only"
    NEO4J_CREDENTIAL_READ_ONLY_UNVERIFIED = "neo4j.credential_read_only_unverified"
    NEO4J_QUERY_FAILED = "neo4j.query_failed"
    PROJECT_MISSING = "project.missing"
    CONFIG_INVALID = "config.invalid"
    SUITE_INVALID = "suite.invalid"
    PROFILE_MISSING = "profile.missing"
    PROFILE_INVALID = "profile.invalid"
    PROFILE_COLLECTION_FAILED = "profile.collection_failed"
    BASELINE_MISSING = "baseline.missing"
    BASELINE_INVALID = "baseline.invalid"
    BASELINE_PARTIAL = "baseline.partial"
    BASELINE_LOAD_FAILED = "baseline.load_failed"
    BASELINE_WRITE_FAILED = "baseline.write_failed"
    DIFF_INCOMPARABLE = "diff.incomparable"
    DIFF_FAILED = "diff.failed"
    ENGINE_COMPILE_FAILED = "engine.compile_failed"
    ENGINE_PARAMETER_RESOLUTION_FAILED = "engine.parameter_resolution_failed"
    ENGINE_EVALUATE_FAILED = "engine.evaluate_failed"
    ENGINE_UNEXPECTED = "engine.unexpected"
    READ_GUARD_REJECTED = "read_guard.rejected"
    ARTIFACT_WRITE_FAILED = "artifact.write_failed"
    REPORT_RENDER_FAILED = "report.render_failed"
    REPORT_OPEN_FAILED = "report.open_failed"
    GENERATE_PROVIDER_AUTH_FAILED = "generate.provider_auth_failed"
    GENERATE_PROVIDER_UNREACHABLE = "generate.provider_unreachable"
    GENERATE_PROVIDER_RATE_LIMITED = "generate.provider_rate_limited"
    GENERATE_PROVIDER_TIMEOUT = "generate.provider_timeout"
    GENERATE_PROVIDER_FAILED = "generate.provider_failed"
    GENERATE_OUTPUT_INVALID = "generate.output_invalid"
    GENERATE_NO_VALID_CANDIDATES = "generate.no_valid_candidates"
    UNKNOWN = "unknown"


_ACTION_ALLOWLIST = {
    CommandName.REPORT: frozenset(
        {
            CommandAction.OPEN,
            CommandAction.LIST,
            CommandAction.COMPARE,
            CommandAction.PRUNE,
            CommandAction.FAILURES_ONLY,
        }
    ),
    CommandName.BASELINE: frozenset({CommandAction.SET, CommandAction.LIST}),
    CommandName.TELEMETRY: frozenset(
        {
            CommandAction.ENABLE,
            CommandAction.DISABLE,
            CommandAction.STATUS,
            CommandAction.PREVIEW,
            CommandAction.RESET_ID,
        }
    ),
}


def safe_command(value: object) -> CommandName:
    try:
        return CommandName(str(value))
    except ValueError:
        return CommandName.OTHER


def safe_action(command: CommandName | str, value: object | None) -> CommandAction | None:
    allowed = _ACTION_ALLOWLIST.get(safe_command(command))
    if allowed is None or value is None:
        return None
    try:
        action = CommandAction(str(value))
    except ValueError:
        return CommandAction.UNKNOWN
    return action if action in allowed else CommandAction.UNKNOWN
