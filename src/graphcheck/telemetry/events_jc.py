"""jc starter for SPEC-10 engine event models.

Scope:
1. Add the cross-field validators described on each model.
2. Add UTC validation for ``occurred_at``.
3. Define the ``EngineEvent`` union and ``EngineEventSink`` protocol.
4. Add focused tests that import this module, not the production module.

Do not add query text, check identity, result verdicts, messages, arbitrary dictionaries, or an
``unknown`` escape hatch to closed event enums.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from graphcheck.telemetry.events import (
    ENGINE_EVENT_SCHEMA_VERSION,
    EngineEventKind,
    EngineStage,
    EventOutcome,
    PartialReasonCode,
    Pattern,
    ProcessingOutcome,
    QueryRole,
    ReadGuardOutcome,
    RunOutcome,
    SafeErrorCode,
    SafeExceptionType,
    SkipReason,
    TargetSource,
    Template,
)

NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


class _StarterEventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = ENGINE_EVENT_SCHEMA_VERSION
    event_id: UUID
    telemetry_run_id: UUID
    sequence: Annotated[StrictInt, Field(ge=1)]
    occurred_at: datetime
    kind: EngineEventKind

    # TODO(jc): reject naive datetimes and non-UTC offsets.


class RunStarted(_StarterEventBase):
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

    # TODO(jc): the three family counts must sum to selected_check_count.


class TargetProbeFinished(_StarterEventBase):
    kind: Literal[EngineEventKind.TARGET_PROBE_FINISHED] = EngineEventKind.TARGET_PROBE_FINISHED
    outcome: EventOutcome
    duration_ms: NonNegativeInt
    target_source: TargetSource
    server_version_major: NonNegativeInt | None
    server_version_minor: NonNegativeInt | None
    apoc_available: bool | None
    count_store_available: bool | None
    error_code: SafeErrorCode | None

    # TODO(jc): error requires a code; success forbids one.


class QueryFinished(_StarterEventBase):
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

    # TODO(jc): target probes have null check fields; check queries require all three.
    # TODO(jc): enforce the outcome/error-code invariant.


class CheckProcessed(_StarterEventBase):
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

    # TODO(jc): implement skipped/completed/engine_error consistency rules.


class RunFinished(_StarterEventBase):
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

    # TODO(jc): reconcile check counts and query_count/query_max_ms.
    # TODO(jc): failed runs require run_error_code; other outcomes forbid it.


class EngineFaulted(_StarterEventBase):
    kind: Literal[EngineEventKind.ENGINE_FAULTED] = EngineEventKind.ENGINE_FAULTED
    engine_stage: EngineStage
    exception_type: SafeExceptionType
    safe_error_code: SafeErrorCode
    elapsed_ms: NonNegativeInt


# TODO(jc): define EngineEvent as a union of the six concrete event types.
# TODO(jc): define EngineEventSink(Protocol) with emit(event: EngineEvent) -> None.
