from uuid import UUID

import pytest
from pydantic import ValidationError

from graphcheck.telemetry import runtime as runtime_module
from graphcheck.telemetry.events import EventOutcome, SafeErrorCode
from graphcheck.telemetry.policy import (
    ArtifactOutcome,
    CliFailureStage,
    CommandCompleted,
    CommandName,
    ConsentSource,
    ConsentState,
    OsFamily,
    OutputMode,
    ProcessOutcome,
    ProfileCompleted,
    ProfileOutcome,
    ProfilePartialReason,
    ProfilerStage,
)
from graphcheck.telemetry.runtime import CommandTelemetryRuntime

COMMAND_ID = UUID("00000000-0000-4000-8000-000000000001")
RUN_ID = UUID("00000000-0000-4000-8000-000000000002")


def _command(**updates):
    values = {
        "command": CommandName.RUN,
        "action": None,
        "process_outcome": ProcessOutcome.SUCCESS,
        "failure_stage": None,
        "duration_ms": 100,
        "setup_ms": 20,
        "artifact_write_ms": 10,
        "render_ms": 5,
        "output_mode": OutputMode.HUMAN,
        "results_artifact": ArtifactOutcome.WRITTEN,
        "report_artifact": ArtifactOutcome.WRITTEN,
        "baseline_artifact": ArtifactOutcome.NOT_REQUESTED,
        "generated_artifact": ArtifactOutcome.NOT_REQUESTED,
        "telemetry_command_id": COMMAND_ID,
        "telemetry_run_id": RUN_ID,
        "probe_outcome": None,
        "probe_duration_ms": None,
        "server_version_major": None,
        "server_version_minor": None,
        "apoc_available": None,
        "count_store_available": None,
        "interactive": False,
        "ci": True,
        "os_family": OsFamily.LINUX,
        "os_version": "6.8",
        "python_minor": "3.12",
        "graphcheck_version": "0.1.0",
        "safe_error_code": None,
    }
    values.update(updates)
    return CommandCompleted(**values)


def test_completed_run_is_command_success_independent_of_result_exit_code():
    # There is intentionally no exit-code or verdict field on this event. A correlated engine
    # run can exit 1/2 while the command remains operationally successful.
    event = _command()
    assert event.process_outcome is ProcessOutcome.SUCCESS
    assert event.telemetry_run_id == RUN_ID
    assert "exit_code" not in event.model_dump()
    assert "verdict" not in event.model_dump()


def test_command_environment_versions_reject_exact_build_details():
    with pytest.raises(ValidationError, match="os_version"):
        _command(os_version="6.8.12")


def test_post_run_artifact_failure_is_non_success_with_run_correlation():
    event = _command(
        process_outcome=ProcessOutcome.UNEXPECTED_ERROR,
        failure_stage=CliFailureStage.REPORT_RENDER,
        results_artifact=ArtifactOutcome.ERROR,
        report_artifact=ArtifactOutcome.ERROR,
        safe_error_code=SafeErrorCode.REPORT_RENDER_FAILED,
    )
    assert event.telemetry_run_id == RUN_ID
    assert event.failure_stage is CliFailureStage.REPORT_RENDER

    with pytest.raises(ValidationError, match="failure_stage"):
        _command(
            process_outcome=ProcessOutcome.UNEXPECTED_ERROR,
            safe_error_code=SafeErrorCode.UNKNOWN,
        )


def test_artifact_write_timing_excludes_separately_reported_render_time(monkeypatch):
    runtime = CommandTelemetryRuntime.start(
        CommandName.RUN,
        consent=ConsentState(False, ConsentSource.DEFAULT),
    )
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: 10.1)

    runtime.mark_artifacts(
        10.0,
        results=ArtifactOutcome.WRITTEN,
        report=ArtifactOutcome.WRITTEN,
        exclude_ms=40,
    )

    assert runtime.artifact_write_ms == 60


def test_generated_artifact_error_requires_artifact_write_stage(monkeypatch):
    runtime = CommandTelemetryRuntime.start(
        CommandName.GENERATE,
        consent=ConsentState(False, ConsentSource.DEFAULT),
    )
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: 10.1)

    runtime.mark_generated_artifact(10.0, ArtifactOutcome.ERROR)

    assert runtime.artifact_write_ms == 100
    assert runtime.generated_artifact is ArtifactOutcome.ERROR
    with pytest.raises(ValidationError, match="generated artifact"):
        _command(generated_artifact=ArtifactOutcome.ERROR)
    with pytest.raises(ValidationError, match="artifact_write_ms"):
        _command(
            command=CommandName.GENERATE,
            generated_artifact=ArtifactOutcome.WRITTEN,
            artifact_write_ms=None,
        )


def test_per_label_degree_timing_does_not_complete_the_aggregate_stage():
    runtime = CommandTelemetryRuntime.start(
        CommandName.PROFILE,
        consent=ConsentState(False, ConsentSource.DEFAULT),
    )

    runtime.record_profile_stage("probe", "success", 5)
    runtime.record_profile_stage("degree_distribution", "success", 10)
    runtime.record_profile_stage("labels", "error", 20)

    assert runtime.profile_degree_distribution_ms == 10
    assert runtime.profile_last_completed_stage is ProfilerStage.PROBE


@pytest.mark.parametrize(
    ("outcome", "partial_reason", "error_code", "last_stage"),
    [
        (ProfileOutcome.COMPLETE, None, None, ProfilerStage.DEGREE_DISTRIBUTION),
        (
            ProfileOutcome.PARTIAL,
            ProfilePartialReason.DEADLINE_EXHAUSTED,
            None,
            ProfilerStage.PROPERTY_COVERAGE,
        ),
        (ProfileOutcome.ERROR, None, SafeErrorCode.PROFILE_INVALID, None),
    ],
)
def test_profile_boundary_covers_complete_partial_and_setup_error(
    outcome,
    partial_reason,
    error_code,
    last_stage,
):
    event = ProfileCompleted(
        outcome=outcome,
        duration_ms=500,
        schema_ms=100 if last_stage is not None else None,
        property_coverage_ms=200
        if last_stage in {ProfilerStage.PROPERTY_COVERAGE, ProfilerStage.DEGREE_DISTRIBUTION}
        else None,
        degree_distribution_ms=100 if last_stage is ProfilerStage.DEGREE_DISTRIBUTION else None,
        deadline_exhausted=outcome is ProfileOutcome.PARTIAL,
        last_completed_stage=last_stage,
        partial_reason=partial_reason,
        probe_outcome=EventOutcome.SUCCESS if last_stage is not None else None,
        probe_duration_ms=20 if last_stage is not None else None,
        server_version_major=5 if last_stage is not None else None,
        server_version_minor=18 if last_stage is not None else None,
        apoc_available=False if last_stage is not None else None,
        count_store_available=True if last_stage is not None else None,
        safe_error_code=error_code,
    )
    dumped = event.model_dump(mode="json")
    assert "labels" not in dumped
    assert "relationship_types" not in dumped
    assert "property_coverage" not in dumped
