"""No-op command runtime used before any telemetry model or delivery import."""

from __future__ import annotations

import time
from dataclasses import dataclass

from graphcheck.telemetry.types import (
    ArtifactOutcome,
    CliFailureStage,
    CommandAction,
    CommandName,
    ConsentState,
    OutputMode,
    ProcessOutcome,
    SafeErrorCode,
    safe_action,
    safe_command,
)


@dataclass
class InactiveCommandTelemetryRuntime:
    command: CommandName
    output_mode: OutputMode
    consent: ConsentState
    started_perf: float
    action: CommandAction | None = None
    process_outcome: ProcessOutcome = ProcessOutcome.SUCCESS
    failure_stage: CliFailureStage | None = None
    error_code: SafeErrorCode | None = None
    setup_ms: int | None = None
    artifact_write_ms: int | None = None
    render_ms: int | None = None
    results_artifact: ArtifactOutcome = ArtifactOutcome.NOT_REQUESTED
    report_artifact: ArtifactOutcome = ArtifactOutcome.NOT_REQUESTED
    baseline_artifact: ArtifactOutcome = ArtifactOutcome.NOT_REQUESTED
    generated_artifact: ArtifactOutcome = ArtifactOutcome.NOT_REQUESTED
    _callback_entered: bool = False
    _profile_result_recorded: bool = False

    @classmethod
    def start(
        cls,
        command: CommandName | str,
        *,
        output_mode: OutputMode,
        consent: ConsentState,
        action: CommandAction | str | None = None,
    ) -> InactiveCommandTelemetryRuntime:
        name = safe_command(command)
        return cls(name, output_mode, consent, time.monotonic(), safe_action(name, action))

    @property
    def enabled(self) -> bool:
        return False

    @property
    def event_sink(self):
        return None

    @property
    def telemetry_run_id(self):
        return None

    @property
    def callback_entered(self) -> bool:
        return self._callback_entered

    @property
    def profile_result_recorded(self) -> bool:
        return self._profile_result_recorded

    def mark_callback_entered(self) -> None:
        self._callback_entered = True

    def set_action(self, action: CommandAction | str | None) -> None:
        self.action = safe_action(self.command, action)

    def mark_setup(self, started_perf: float) -> None:
        self.setup_ms = _elapsed_ms(started_perf)

    def mark_artifacts(
        self,
        started_perf: float,
        *,
        results: ArtifactOutcome,
        report: ArtifactOutcome,
        exclude_ms: int = 0,
    ) -> None:
        self.artifact_write_ms = max(0, _elapsed_ms(started_perf) - max(0, exclude_ms))
        self.results_artifact = results
        self.report_artifact = report

    def mark_generated_artifact(
        self,
        started_perf: float,
        outcome: ArtifactOutcome,
    ) -> None:
        self.artifact_write_ms = _elapsed_ms(started_perf)
        self.generated_artifact = outcome

    def fail(
        self,
        outcome: ProcessOutcome,
        stage: CliFailureStage,
        code: object | None,
    ) -> None:
        self.process_outcome = outcome
        self.failure_stage = stage
        try:
            self.error_code = SafeErrorCode(str(code))
        except ValueError:
            self.error_code = SafeErrorCode.UNKNOWN

    def record_profile_result(self, *args, **kwargs) -> None:
        self._profile_result_recorded = True

    def record_probe(self, *args, **kwargs) -> None:
        return None

    def record_profile_stage(self, *args, **kwargs) -> None:
        return None

    def finish(self) -> None:
        return None


def _elapsed_ms(started_perf: float) -> int:
    return max(0, round((time.monotonic() - started_perf) * 1000))
