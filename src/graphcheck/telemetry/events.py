"""Strict, content-free engine events for SPEC-10 telemetry.

These models are the privacy boundary between the engine and telemetry.  They deliberately
cannot represent checks, queries, results, configuration objects, exception messages, or any
other free-form customer data.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol, TypeVar
from uuid import UUID

from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

ENGINE_EVENT_SCHEMA_VERSION = "1.0"

NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


class EngineEventKind(StrEnum):
    RUN_STARTED = "RunStarted"
    TARGET_PROBE_FINISHED = "TargetProbeFinished"
    QUERY_FINISHED = "QueryFinished"
    CHECK_PROCESSED = "CheckProcessed"
    RUN_FINISHED = "RunFinished"
    ENGINE_FAULTED = "EngineFaulted"


class EventOutcome(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


class TargetSource(StrEnum):
    PROVIDED = "provided"
    PROBED = "probed"


class Pattern(StrEnum):
    CONFORMANCE = "conformance"
    COMPETENCY_SHAPE = "competency-shape"
    COMPETENCY_REGRESSION = "competency-regression"
    DRIFT = "drift"
    UNKNOWN = "unknown"


class Template(StrEnum):
    EXISTENCE = "existence"
    UNIQUENESS = "uniqueness"
    CARDINALITY = "cardinality"
    RELATIONSHIP_SHAPE = "relationship-shape"
    VALUE_DOMAIN = "value-domain"
    REFERENTIAL_INTEGRITY = "referential-integrity"
    CONNECTIVITY = "connectivity"
    PII = "pii"
    COMPETENCY_SHAPE = "competency-shape"
    COMPETENCY_REGRESSION = "competency-regression"
    DRIFT = "drift"
    CUSTOM = "custom"


class QueryRole(StrEnum):
    TARGET_PROBE = "target_probe"
    PARAMETER_RESOLUTION = "parameter_resolution"
    SAMPLING_POPULATION = "sampling_population"
    CHECK_MEASUREMENT = "check_measurement"
    EVIDENCE_COLLECTION = "evidence_collection"


class ReadGuardOutcome(StrEnum):
    ALLOWED = "allowed"
    REJECTED = "rejected"
    ERROR = "error"
    NOT_RUN = "not_run"


class ProcessingOutcome(StrEnum):
    COMPLETED = "completed"
    ENGINE_ERROR = "engine_error"
    SKIPPED = "skipped"


class SkipReason(StrEnum):
    GENERATED = "generated"
    UNSUPPORTED = "unsupported"
    NOT_RUN = "not_run"


class RunOutcome(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class PartialReasonCode(StrEnum):
    SUITE_INPUT_INVALID = "suite_input_invalid"
    UNSUPPORTED_CHECK = "unsupported_check"
    PARTIAL_BASELINE = "partial_baseline"
    BASELINE_MEASUREMENT_MISSING = "baseline_measurement_missing"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    UNKNOWN = "unknown"


class SafeErrorCode(StrEnum):
    NEO4J_UNREACHABLE = "neo4j.unreachable"
    NEO4J_AUTH_FAILED = "neo4j.auth_failed"
    NEO4J_PERMISSION_DENIED = "neo4j.permission_denied"
    NEO4J_DATABASE_NOT_FOUND = "neo4j.database_not_found"
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
    UNKNOWN = "unknown"


class EngineStage(StrEnum):
    PROBE = "probe"
    COMPILE = "compile"
    RESOLVE_PARAMS = "resolve_params"
    SAMPLE = "sample"
    BASELINE = "baseline"
    QUERY = "query"
    EVALUATE = "evaluate"
    FINALIZE = "finalize"


class SafeExceptionType(StrEnum):
    TIMEOUT_ERROR = "TimeoutError"
    CONNECTION_ERROR = "ConnectionError"
    OS_ERROR = "OSError"
    VALUE_ERROR = "ValueError"
    KEY_ERROR = "KeyError"
    TYPE_ERROR = "TypeError"
    RUNTIME_ERROR = "RuntimeError"
    MEMORY_ERROR = "MemoryError"
    UNKNOWN = "unknown"


class _EngineEventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = ENGINE_EVENT_SCHEMA_VERSION
    event_id: UUID4
    telemetry_run_id: UUID4
    sequence: Annotated[StrictInt, Field(ge=1)]
    occurred_at: datetime
    kind: EngineEventKind

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("occurred_at must be a timezone-aware UTC datetime")
        return value


class RunStarted(_EngineEventBase):
    kind: Literal[EngineEventKind.RUN_STARTED] = EngineEventKind.RUN_STARTED
    graphcheck_version: str
    pack_version: str
    suite_count: NonNegativeInt
    selected_check_count: NonNegativeInt
    conformance_count: NonNegativeInt
    competency_count: NonNegativeInt
    drift_count: NonNegativeInt
    uses_sampling: bool
    uses_baselines: bool
    fail_fast_enabled: bool
    suite_filter_used: bool
    tag_filter_used: bool
    time_budget_ms: NonNegativeInt | None

    @model_validator(mode="after")
    def check_families_reconcile(self) -> RunStarted:
        if (
            self.conformance_count + self.competency_count + self.drift_count
            != self.selected_check_count
        ):
            raise ValueError("selected check-family counts must reconcile")
        return self


class TargetProbeFinished(_EngineEventBase):
    kind: Literal[EngineEventKind.TARGET_PROBE_FINISHED] = EngineEventKind.TARGET_PROBE_FINISHED
    outcome: EventOutcome
    duration_ms: NonNegativeInt
    target_source: TargetSource
    server_version_major: NonNegativeInt | None
    server_version_minor: NonNegativeInt | None
    apoc_available: bool | None
    count_store_available: bool | None
    error_code: SafeErrorCode | None

    @model_validator(mode="after")
    def error_code_matches_outcome(self) -> TargetProbeFinished:
        _validate_outcome_error_code(self.outcome, self.error_code)
        return self


class QueryFinished(_EngineEventBase):
    kind: Literal[EngineEventKind.QUERY_FINISHED] = EngineEventKind.QUERY_FINISHED
    check_sequence: NonNegativeInt | None
    pattern: Pattern | None
    template: Template | None
    query_role: QueryRole
    outcome: EventOutcome
    duration_ms: NonNegativeInt
    server_available_after_ms: NonNegativeInt | None
    server_consumed_after_ms: NonNegativeInt | None
    read_guard_outcome: ReadGuardOutcome
    notification_count: NonNegativeInt | None
    error_code: SafeErrorCode | None

    @model_validator(mode="after")
    def fields_are_consistent(self) -> QueryFinished:
        _validate_outcome_error_code(self.outcome, self.error_code)
        check_fields = (self.check_sequence, self.pattern, self.template)
        if self.query_role is QueryRole.TARGET_PROBE:
            if any(value is not None for value in check_fields):
                raise ValueError("target-probe queries cannot be attributed to a check")
        elif self.check_sequence is None:
            raise ValueError("check-level queries require check_sequence")
        elif self.pattern is None or self.template is None:
            raise ValueError("check-level queries require pattern and template")
        return self


class CheckProcessed(_EngineEventBase):
    kind: Literal[EngineEventKind.CHECK_PROCESSED] = EngineEventKind.CHECK_PROCESSED
    check_sequence: NonNegativeInt
    pattern: Pattern
    template: Template
    processing_outcome: ProcessingOutcome
    skip_reason: SkipReason | None
    duration_ms: NonNegativeInt | None
    compile_ms: NonNegativeInt | None
    parameter_resolution_ms: NonNegativeInt | None
    sampling_population_ms: NonNegativeInt | None
    baseline_resolution_ms: NonNegativeInt | None
    read_guard_ms: NonNegativeInt | None
    query_ms: NonNegativeInt | None
    evaluation_ms: NonNegativeInt | None
    query_count: NonNegativeInt
    sampled: bool
    error_code: SafeErrorCode | None

    @model_validator(mode="after")
    def fields_are_consistent(self) -> CheckProcessed:
        if self.processing_outcome is ProcessingOutcome.SKIPPED:
            if self.skip_reason is None:
                raise ValueError("skipped checks require skip_reason")
            if self.duration_ms is not None:
                raise ValueError("skipped checks must not report duration_ms")
            if self.error_code is not None:
                raise ValueError("skipped checks must not report error_code")
            stage_values = (
                self.compile_ms,
                self.parameter_resolution_ms,
                self.sampling_population_ms,
                self.baseline_resolution_ms,
                self.read_guard_ms,
                self.query_ms,
                self.evaluation_ms,
            )
            if any(value is not None for value in stage_values):
                raise ValueError("skipped checks cannot report stage timings")
            if self.query_count != 0 or self.sampled:
                raise ValueError("skipped checks cannot report query or sampling work")
        else:
            if self.skip_reason is not None:
                raise ValueError("non-skipped checks cannot report skip_reason")
            if self.duration_ms is None:
                raise ValueError("processed checks require duration_ms")
            if (
                self.processing_outcome is ProcessingOutcome.ENGINE_ERROR
                and self.error_code is None
            ):
                raise ValueError("engine-error checks require error_code")
            if (
                self.processing_outcome is ProcessingOutcome.COMPLETED
                and self.error_code is not None
            ):
                raise ValueError("completed checks cannot report error_code")
        if self.query_count == 0 and self.query_ms is not None:
            raise ValueError("query_ms must be null when query_count is zero")
        if self.query_count > 0 and self.query_ms is None:
            raise ValueError("query_ms is required when queries ran")
        if self.processing_outcome is ProcessingOutcome.COMPLETED and self.query_count == 0:
            raise ValueError("completed checks require a measurement query")
        return self


class RunFinished(_EngineEventBase):
    kind: Literal[EngineEventKind.RUN_FINISHED] = EngineEventKind.RUN_FINISHED
    outcome: RunOutcome
    duration_ms: NonNegativeInt
    selected_check_count: NonNegativeInt
    executed_check_count: NonNegativeInt
    engine_error_count: NonNegativeInt
    skipped_generated_count: NonNegativeInt
    skipped_unsupported_count: NonNegativeInt
    skipped_not_run_count: NonNegativeInt
    query_count: NonNegativeInt
    query_total_ms: NonNegativeInt
    query_max_ms: NonNegativeInt | None
    probe_ms: NonNegativeInt | None
    budget_remaining_ms: NonNegativeInt | None
    early_stopped: bool
    deadline_exhausted: bool
    partial_reason_codes: tuple[PartialReasonCode, ...]
    run_error_code: SafeErrorCode | None

    @model_validator(mode="after")
    def aggregates_are_consistent(self) -> RunFinished:
        reconciled = (
            self.executed_check_count
            + self.engine_error_count
            + self.skipped_generated_count
            + self.skipped_unsupported_count
            + self.skipped_not_run_count
        )
        if reconciled != self.selected_check_count:
            raise ValueError("terminal check counts must reconcile")
        if self.query_count == 0 and self.query_max_ms is not None:
            raise ValueError("query_max_ms must be null when query_count is zero")
        if self.query_count == 0 and self.query_total_ms != 0:
            raise ValueError("query_total_ms must be zero when query_count is zero")
        if self.query_count > 0 and self.query_max_ms is None:
            raise ValueError("query_max_ms is required when queries ran")
        if self.query_max_ms is not None and self.query_max_ms > self.query_total_ms:
            raise ValueError("query_max_ms cannot exceed query_total_ms")
        if self.outcome is RunOutcome.FAILED and self.run_error_code is None:
            raise ValueError("failed runs require run_error_code")
        if self.outcome is not RunOutcome.FAILED and self.run_error_code is not None:
            raise ValueError("non-failed runs cannot report run_error_code")
        if self.outcome is not RunOutcome.PARTIAL and self.partial_reason_codes:
            raise ValueError("non-partial runs cannot report partial_reason_codes")
        if len(self.partial_reason_codes) != len(set(self.partial_reason_codes)):
            raise ValueError("partial_reason_codes cannot contain duplicates")
        return self


class EngineFaulted(_EngineEventBase):
    kind: Literal[EngineEventKind.ENGINE_FAULTED] = EngineEventKind.ENGINE_FAULTED
    engine_stage: EngineStage
    exception_type: SafeExceptionType
    safe_error_code: SafeErrorCode
    elapsed_ms: NonNegativeInt


EngineEvent = (
    RunStarted | TargetProbeFinished | QueryFinished | CheckProcessed | RunFinished | EngineFaulted
)


class EngineEventSink(Protocol):
    def emit(self, event: EngineEvent) -> None: ...


EventModel = TypeVar("EventModel", bound=_EngineEventBase)


class EngineEventEmitter:
    """Add the common envelope and isolate a synchronous sink from engine control flow."""

    def __init__(
        self,
        sink: EngineEventSink,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._sink: EngineEventSink | None = sink
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid.uuid4
        self._sequence = 0
        self.telemetry_run_id: UUID | None = None
        try:
            self.telemetry_run_id = self._id_factory()
        except Exception:
            # UUID generation is part of telemetry and may never affect engine execution.
            self._sink = None

    @property
    def enabled(self) -> bool:
        return self._sink is not None

    def emit(self, model: type[EventModel], /, **payload: object) -> EventModel | None:
        if self._sink is None or self.telemetry_run_id is None:
            return None
        try:
            self._sequence += 1
            event = model(
                event_id=self._id_factory(),
                telemetry_run_id=self.telemetry_run_id,
                sequence=self._sequence,
                occurred_at=self._clock(),
                **payload,
            )
            self._sink.emit(event)
        except Exception:
            # SPEC-10 accepts event loss. Construction or observer failure cannot alter the run.
            self._sink = None
            return None
        return event


def _validate_outcome_error_code(
    outcome: EventOutcome,
    error_code: SafeErrorCode | None,
) -> None:
    if outcome is EventOutcome.ERROR and error_code is None:
        raise ValueError("error outcomes require error_code")
    if outcome is EventOutcome.SUCCESS and error_code is not None:
        raise ValueError("successful outcomes cannot report error_code")
