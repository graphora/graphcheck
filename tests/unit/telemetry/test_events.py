from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from graphcheck.telemetry.events import (
    CheckProcessed,
    EngineEventKind,
    EventOutcome,
    PartialReasonCode,
    Pattern,
    ProcessingOutcome,
    QueryFinished,
    QueryRole,
    ReadGuardOutcome,
    RunFinished,
    RunOutcome,
    RunStarted,
    SafeErrorCode,
    SkipReason,
    Template,
)

RUN_ID = UUID("00000000-0000-4000-8000-000000000001")
EVENT_ID = UUID("00000000-0000-4000-8000-000000000002")


def _envelope(sequence: int = 1) -> dict[str, object]:
    return {
        "event_id": EVENT_ID,
        "telemetry_run_id": RUN_ID,
        "sequence": sequence,
        "occurred_at": datetime(2026, 7, 23, tzinfo=UTC),
    }


def test_models_are_strict_immutable_and_reject_unknown_fields():
    event = RunStarted(
        **_envelope(),
        graphcheck_version="0.1.0",
        pack_version="0.1.0",
        suite_count=1,
        selected_check_count=1,
        conformance_count=1,
        competency_count=0,
        drift_count=0,
        uses_sampling=False,
        uses_baselines=False,
        fail_fast_enabled=False,
        suite_filter_used=False,
        tag_filter_used=False,
        time_budget_ms=1000,
    )

    assert event.kind is EngineEventKind.RUN_STARTED
    with pytest.raises(ValidationError, match="frozen"):
        event.suite_count = 2
    with pytest.raises(ValidationError, match="Extra inputs"):
        RunStarted(**{**event.model_dump(), "query": "MATCH (n) RETURN n"})


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_nonnegative_integer_fields_reject_invalid_values(value):
    with pytest.raises(ValidationError):
        QueryFinished(
            **_envelope(),
            check_sequence=1,
            pattern=Pattern.CONFORMANCE,
            template=Template.EXISTENCE,
            query_role=QueryRole.CHECK_MEASUREMENT,
            outcome=EventOutcome.SUCCESS,
            duration_ms=value,
            server_available_after_ms=None,
            server_consumed_after_ms=None,
            read_guard_outcome=ReadGuardOutcome.ALLOWED,
            notification_count=0,
            error_code=None,
        )


def test_query_and_check_outcome_combinations_are_enforced():
    with pytest.raises(ValidationError, match="require error_code"):
        QueryFinished(
            **_envelope(),
            check_sequence=1,
            pattern=Pattern.CONFORMANCE,
            template=Template.EXISTENCE,
            query_role=QueryRole.CHECK_MEASUREMENT,
            outcome=EventOutcome.ERROR,
            duration_ms=1,
            server_available_after_ms=None,
            server_consumed_after_ms=None,
            read_guard_outcome=ReadGuardOutcome.ERROR,
            notification_count=None,
            error_code=None,
        )

    with pytest.raises(ValidationError, match="require skip_reason"):
        CheckProcessed(
            **_envelope(),
            check_sequence=1,
            pattern=Pattern.CONFORMANCE,
            template=Template.EXISTENCE,
            processing_outcome=ProcessingOutcome.SKIPPED,
            skip_reason=None,
            duration_ms=None,
            compile_ms=None,
            parameter_resolution_ms=None,
            sampling_population_ms=None,
            baseline_resolution_ms=None,
            read_guard_ms=None,
            query_ms=None,
            evaluation_ms=None,
            query_count=0,
            sampled=False,
            error_code=None,
        )


def test_run_finished_reconciles_counts_queries_and_partial_codes():
    event = RunFinished(
        **_envelope(),
        outcome=RunOutcome.PARTIAL,
        duration_ms=10,
        selected_check_count=2,
        executed_check_count=1,
        engine_error_count=0,
        skipped_generated_count=0,
        skipped_unsupported_count=0,
        skipped_not_run_count=1,
        query_count=1,
        query_total_ms=4,
        query_max_ms=4,
        probe_ms=0,
        budget_remaining_ms=90,
        early_stopped=True,
        deadline_exhausted=True,
        partial_reason_codes=(PartialReasonCode.DEADLINE_EXHAUSTED,),
        run_error_code=None,
    )
    assert event.selected_check_count == 2

    with pytest.raises(ValidationError, match="must reconcile"):
        RunFinished(
            **{**event.model_dump(), "selected_check_count": 3},
        )


def test_skipped_check_accepts_only_safe_skip_reason():
    event = CheckProcessed(
        **_envelope(),
        check_sequence=1,
        pattern=Pattern.DRIFT,
        template=Template.DRIFT,
        processing_outcome=ProcessingOutcome.SKIPPED,
        skip_reason=SkipReason.NOT_RUN,
        duration_ms=None,
        compile_ms=None,
        parameter_resolution_ms=None,
        sampling_population_ms=None,
        baseline_resolution_ms=None,
        read_guard_ms=None,
        query_ms=None,
        evaluation_ms=None,
        query_count=0,
        sampled=False,
        error_code=None,
    )
    assert event.error_code is None
    assert SafeErrorCode.UNKNOWN.value == "unknown"
