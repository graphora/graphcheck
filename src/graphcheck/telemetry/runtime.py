"""CLI orchestration helpers for SPEC-10 command correlation."""

from __future__ import annotations

import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field

from graphcheck import __version__
from graphcheck.telemetry.collector import TelemetryCollector
from graphcheck.telemetry.events import EventOutcome, SafeErrorCode
from graphcheck.telemetry.policy import (
    ArtifactOutcome,
    CliFailureStage,
    CommandAction,
    CommandCompleted,
    CommandName,
    ConsentSource,
    ConsentState,
    OutputMode,
    ProcessOutcome,
    ProfileCompleted,
    ProfileOutcome,
    ProfilePartialReason,
    ProfilerStage,
    is_ci,
    os_family,
    python_minor,
    resolve_consent,
    safe_action,
    safe_command,
    safe_error_code,
    version_major_minor,
)
from graphcheck.telemetry.posthog import (
    PostHogAdapter,
    TelemetrySession,
    create_posthog_adapter,
)


@dataclass
class CommandTelemetryRuntime:
    """Mutable command measurements; only ``finish`` constructs the frozen payload."""

    command: CommandName
    action: CommandAction | None
    output_mode: OutputMode
    consent: ConsentState
    started_perf: float
    collector: TelemetryCollector | None = None
    session: TelemetrySession | None = None
    adapter: PostHogAdapter | None = None
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
    probe_outcome: EventOutcome | None = None
    probe_duration_ms: int | None = None
    server_version_major: int | None = None
    server_version_minor: int | None = None
    apoc_available: bool | None = None
    count_store_available: bool | None = None
    profile_outcome: ProfileOutcome | None = None
    profile_partial_reason: ProfilePartialReason | None = None
    profile_deadline_exhausted: bool = False
    profile_schema_ms: int | None = None
    profile_property_coverage_ms: int | None = None
    profile_degree_distribution_ms: int | None = None
    profile_last_completed_stage: ProfilerStage | None = None
    _profile_result_recorded: bool = field(default=False, init=False)
    _engine_captured: bool = field(default=False, init=False)
    _finished: bool = field(default=False, init=False)
    _callback_entered: bool = field(default=False, init=False)

    @classmethod
    def start(
        cls,
        command: CommandName | str,
        *,
        action: CommandAction | str | None = None,
        output_mode: OutputMode = OutputMode.HUMAN,
        consent: ConsentState | None = None,
    ) -> CommandTelemetryRuntime:
        started_perf = time.monotonic()
        try:
            state = consent or resolve_consent()
        except Exception:
            state = ConsentState(False, source=ConsentSource.DEFAULT)
        command_name = safe_command(command)
        runtime = cls(
            command=command_name,
            action=safe_action(command_name, action),
            output_mode=output_mode,
            consent=state,
            started_perf=started_perf,
        )
        if state.enabled:
            try:
                runtime.collector = TelemetryCollector()
                runtime.session = TelemetrySession.create(state)
                runtime.adapter = create_posthog_adapter(state, session=runtime.session)
            except Exception:
                # Consent and telemetry setup are best-effort and cannot block a command.
                runtime.collector = None
                runtime.session = None
                runtime.adapter = None
        return runtime

    @property
    def enabled(self) -> bool:
        return self.collector is not None

    @property
    def event_sink(self):
        return self.collector

    @property
    def telemetry_command_id(self):
        return self.session.telemetry_command_id if self.session is not None else None

    @property
    def telemetry_run_id(self):
        return self.collector.telemetry_run_id if self.collector is not None else None

    def set_action(self, action: CommandAction | str | None) -> None:
        self.action = safe_action(self.command, action)

    @property
    def callback_entered(self) -> bool:
        return self._callback_entered

    def mark_callback_entered(self) -> None:
        self._callback_entered = True

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

    def record_probe(
        self,
        *,
        started_perf: float,
        outcome: EventOutcome,
        target: object | None = None,
    ) -> None:
        self.probe_outcome = outcome
        self.probe_duration_ms = _elapsed_ms(started_perf)
        if target is None:
            return
        self.server_version_major, self.server_version_minor = version_major_minor(
            getattr(target, "server_version", None)
        )
        capabilities = getattr(target, "capabilities", None)
        self.apoc_available = getattr(capabilities, "apoc", None)
        self.count_store_available = getattr(capabilities, "count_store", None)

    def record_profile_stage(
        self,
        stage: str,
        outcome: str,
        duration_ms: int,
        target: object | None = None,
    ) -> None:
        """Record only coarse profiler stage timing and capability metadata."""

        try:
            profiler_stage = ProfilerStage(stage)
            event_outcome = EventOutcome(outcome)
        except ValueError:
            return
        duration_ms = max(0, duration_ms)
        if profiler_stage is ProfilerStage.PROBE:
            self.probe_outcome = event_outcome
            self.probe_duration_ms = duration_ms
            if target is not None and event_outcome is EventOutcome.SUCCESS:
                self.server_version_major, self.server_version_minor = version_major_minor(
                    getattr(target, "server_version", None)
                )
                capabilities = getattr(target, "capabilities", None)
                self.apoc_available = getattr(capabilities, "apoc", None)
                self.count_store_available = getattr(capabilities, "count_store", None)
        elif profiler_stage in {
            ProfilerStage.LABELS,
            ProfilerStage.RELATIONSHIP_TYPES,
            ProfilerStage.CONSTRAINTS,
            ProfilerStage.INDEXES,
        }:
            self.profile_schema_ms = (self.profile_schema_ms or 0) + duration_ms
        elif profiler_stage is ProfilerStage.PROPERTY_COVERAGE:
            self.profile_property_coverage_ms = (
                self.profile_property_coverage_ms or 0
            ) + duration_ms
        elif profiler_stage is ProfilerStage.DEGREE_DISTRIBUTION:
            self.profile_degree_distribution_ms = (
                self.profile_degree_distribution_ms or 0
            ) + duration_ms
        if (
            event_outcome is EventOutcome.SUCCESS
            and profiler_stage is not ProfilerStage.DEGREE_DISTRIBUTION
        ):
            self.profile_last_completed_stage = profiler_stage

    def record_profile_result(
        self,
        status: object,
        partial_reason_code: object | None,
        deadline_exhausted: bool = False,
    ) -> None:
        self._profile_result_recorded = True
        try:
            outcome = ProfileOutcome(str(status))
        except ValueError:
            outcome = ProfileOutcome.ERROR
        self.profile_outcome = outcome
        if outcome is ProfileOutcome.COMPLETE:
            self.profile_outcome = ProfileOutcome.COMPLETE
            self.profile_partial_reason = None
            self.profile_deadline_exhausted = False
            return
        if outcome is ProfileOutcome.PARTIAL:
            try:
                self.profile_partial_reason = ProfilePartialReason(str(partial_reason_code))
            except ValueError:
                self.profile_partial_reason = ProfilePartialReason.UNKNOWN
            self.profile_deadline_exhausted = bool(deadline_exhausted)
            return
        self.profile_partial_reason = None
        self.profile_deadline_exhausted = bool(deadline_exhausted)

    @property
    def profile_result_recorded(self) -> bool:
        return self._profile_result_recorded

    def fail(
        self,
        outcome: ProcessOutcome,
        stage: CliFailureStage,
        code: object | None,
    ) -> None:
        self.process_outcome = outcome
        self.failure_stage = stage
        self.error_code = safe_error_code(code) or SafeErrorCode.UNKNOWN

    def capture_engine_events(self) -> None:
        if self._engine_captured or self.adapter is None or self.collector is None:
            return
        self.adapter.capture_collector(self.collector)
        self._engine_captured = True

    def finish(self) -> CommandCompleted | None:
        if self._finished:
            return None
        self._finished = True
        if not self.enabled or self.session is None:
            return None
        with suppress(Exception):
            self.capture_engine_events()
        try:
            duration_ms = _elapsed_ms(self.started_perf)
            capture_profile = (
                self.command is CommandName.PROFILE
                and self.adapter is not None
                and (self.callback_entered or self.process_outcome is not ProcessOutcome.SUCCESS)
            )
            if capture_profile:
                self.adapter.capture_profile(self._profile_completed(duration_ms))
            event = CommandCompleted(
                command=self.command,
                action=self.action,
                process_outcome=self.process_outcome,
                failure_stage=self.failure_stage,
                duration_ms=duration_ms,
                setup_ms=self.setup_ms,
                artifact_write_ms=self.artifact_write_ms,
                render_ms=self.render_ms,
                output_mode=self.output_mode,
                results_artifact=self.results_artifact,
                report_artifact=self.report_artifact,
                baseline_artifact=self.baseline_artifact,
                generated_artifact=self.generated_artifact,
                telemetry_command_id=self.session.telemetry_command_id,
                telemetry_run_id=self.telemetry_run_id,
                probe_outcome=self.probe_outcome,
                probe_duration_ms=self.probe_duration_ms,
                server_version_major=self.server_version_major,
                server_version_minor=self.server_version_minor,
                apoc_available=self.apoc_available,
                count_store_available=self.count_store_available,
                interactive=_interactive(),
                ci=is_ci(),
                os_family=os_family(),
                python_minor=python_minor(),
                graphcheck_version=__version__,
                safe_error_code=self.error_code,
            )
            if self.adapter is not None:
                self.adapter.capture_command(event)
        except Exception:
            return None
        finally:
            if self.adapter is not None:
                with suppress(Exception):
                    self.adapter.close()
        return event

    def _profile_completed(self, duration_ms: int) -> ProfileCompleted:
        failed = self.process_outcome is not ProcessOutcome.SUCCESS
        outcome = ProfileOutcome.ERROR if failed else self.profile_outcome
        if outcome is None:
            outcome = ProfileOutcome.ERROR
        partial_reason = self.profile_partial_reason if outcome is ProfileOutcome.PARTIAL else None
        error_code = (
            (self.error_code or SafeErrorCode.UNKNOWN) if outcome is ProfileOutcome.ERROR else None
        )
        schema_ms = self.profile_schema_ms
        if schema_ms is not None and self.profile_degree_distribution_ms is not None:
            # Degree probes run while labels are collected, so remove their nested time from the
            # broader schema measurement before reporting the two categories independently.
            schema_ms = max(0, schema_ms - self.profile_degree_distribution_ms)
        return ProfileCompleted(
            outcome=outcome,
            duration_ms=duration_ms,
            schema_ms=schema_ms,
            property_coverage_ms=self.profile_property_coverage_ms,
            degree_distribution_ms=self.profile_degree_distribution_ms,
            deadline_exhausted=self.profile_deadline_exhausted,
            last_completed_stage=self.profile_last_completed_stage,
            partial_reason=partial_reason,
            probe_outcome=self.probe_outcome,
            probe_duration_ms=self.probe_duration_ms,
            server_version_major=self.server_version_major,
            server_version_minor=self.server_version_minor,
            apoc_available=self.apoc_available,
            count_store_available=self.count_store_available,
            safe_error_code=error_code,
        )


def _elapsed_ms(started_perf: float) -> int:
    return max(0, round((time.monotonic() - started_perf) * 1000))


def _interactive() -> bool:
    stdin_tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
    stdout_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    return stdin_tty and stdout_tty
