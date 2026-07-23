"""In-memory engine event aggregation and invariant reconciliation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from graphcheck.telemetry.events import (
    CheckProcessed,
    EngineEvent,
    EngineFaulted,
    EventOutcome,
    ProcessingOutcome,
    QueryFinished,
    RunFinished,
    RunStarted,
    SkipReason,
    TargetProbeFinished,
)
from graphcheck.telemetry.policy import assert_allowlisted_posthog_payload


@dataclass(frozen=True)
class PostHogEvent:
    name: str
    properties: Mapping[str, object]

    def __post_init__(self) -> None:
        snapshot = {
            key: tuple(value) if isinstance(value, list) else value
            for key, value in self.properties.items()
        }
        assert_allowlisted_posthog_payload(self.name, snapshot)
        object.__setattr__(self, "properties", MappingProxyType(snapshot))


@dataclass
class _QueryAggregate:
    count: int = 0
    total_ms: int = 0
    max_ms: int | None = None
    success_count: int = 0
    error_count: int = 0
    timeout_count: int = 0
    server_available_total_ms: int = 0
    server_consumed_total_ms: int = 0
    notification_count: int = 0
    read_guard_rejected_count: int = 0

    def add(self, event: QueryFinished) -> None:
        self.count += 1
        self.total_ms += event.duration_ms
        self.max_ms = (
            event.duration_ms if self.max_ms is None else max(self.max_ms, event.duration_ms)
        )
        if event.outcome is EventOutcome.SUCCESS:
            self.success_count += 1
        elif event.outcome is EventOutcome.ERROR:
            self.error_count += 1
        else:
            self.timeout_count += 1
        self.server_available_total_ms += event.server_available_after_ms or 0
        self.server_consumed_total_ms += event.server_consumed_after_ms or 0
        self.notification_count += event.notification_count or 0
        if event.read_guard_outcome.value == "rejected":
            self.read_guard_rejected_count += 1

    def properties(self) -> dict[str, object]:
        return {
            "aggregated_query_count": self.count,
            "aggregated_query_total_ms": self.total_ms,
            "aggregated_query_max_ms": self.max_ms,
            "query_success_count": self.success_count,
            "query_error_count": self.error_count,
            "query_timeout_count": self.timeout_count,
            "server_available_total_ms": self.server_available_total_ms,
            "server_consumed_total_ms": self.server_consumed_total_ms,
            "notification_count_total": self.notification_count,
            "read_guard_rejected_count": self.read_guard_rejected_count,
        }


class TelemetryCollector:
    """Synchronous, side-effect-free collector used directly as the engine sink."""

    def __init__(self) -> None:
        self._events: list[EngineEvent] = []
        self._run_started: RunStarted | None = None
        self._terminal: RunFinished | EngineFaulted | None = None
        self._probe: TargetProbeFinished | None = None
        self._queries: list[QueryFinished] = []
        self._queries_by_check: dict[int, _QueryAggregate] = defaultdict(_QueryAggregate)
        self._checks: dict[int, CheckProcessed] = {}

    @property
    def telemetry_run_id(self):
        return self._run_started.telemetry_run_id if self._run_started is not None else None

    @property
    def events(self) -> tuple[EngineEvent, ...]:
        return tuple(self._events)

    def emit(self, event: EngineEvent) -> None:
        expected_sequence = len(self._events) + 1
        if event.sequence != expected_sequence:
            raise ValueError(
                f"engine event sequence must be contiguous: expected {expected_sequence}, "
                f"got {event.sequence}"
            )
        if not self._events:
            if not isinstance(event, RunStarted):
                raise ValueError("RunStarted must be the first engine event")
            self._run_started = event
        else:
            assert self._run_started is not None
            if event.telemetry_run_id != self._run_started.telemetry_run_id:
                raise ValueError("all engine events must share telemetry_run_id")
            if isinstance(event, RunStarted):
                raise ValueError("RunStarted may be emitted only once")
            if self._terminal is not None:
                raise ValueError("no engine events may follow a terminal event")

        if isinstance(event, TargetProbeFinished):
            if self._probe is not None:
                raise ValueError("TargetProbeFinished may be emitted only once")
            self._probe = event
        elif isinstance(event, QueryFinished):
            self._record_query(event)
        elif isinstance(event, CheckProcessed):
            self._record_check(event)
        elif isinstance(event, (RunFinished, EngineFaulted)):
            self._record_terminal(event)

        self._events.append(event)

    def _record_query(self, event: QueryFinished) -> None:
        if event.check_sequence is not None:
            if self._run_started is None:
                raise ValueError("query arrived before RunStarted")
            if not 1 <= event.check_sequence <= self._run_started.selected_check_count:
                raise ValueError("query references an invalid check_sequence")
            if event.check_sequence in self._checks:
                raise ValueError("query arrived after its CheckProcessed event")
            self._queries_by_check[event.check_sequence].add(event)
        self._queries.append(event)

    def _record_check(self, event: CheckProcessed) -> None:
        if self._run_started is None:
            raise ValueError("check arrived before RunStarted")
        if not 1 <= event.check_sequence <= self._run_started.selected_check_count:
            raise ValueError("CheckProcessed has an invalid check_sequence")
        if event.check_sequence in self._checks:
            raise ValueError("each check_sequence may be processed only once")
        aggregate = self._queries_by_check[event.check_sequence]
        attributed_queries = (
            query for query in self._queries if query.check_sequence == event.check_sequence
        )
        if any(
            query.pattern is not event.pattern or query.template is not event.template
            for query in attributed_queries
        ):
            raise ValueError("query pattern/template attribution changed within a check")
        if event.query_count != aggregate.count:
            raise ValueError("CheckProcessed.query_count does not reconcile with queries")
        expected_ms = aggregate.total_ms if aggregate.count else 0
        if (event.query_ms or 0) != expected_ms:
            raise ValueError("CheckProcessed.query_ms does not reconcile with queries")
        self._checks[event.check_sequence] = event

    def _record_terminal(self, event: RunFinished | EngineFaulted) -> None:
        if self._terminal is not None:
            raise ValueError("a run may have at most one terminal event")
        if isinstance(event, RunFinished):
            if self._run_started is None:
                raise ValueError("terminal event arrived before RunStarted")
            if event.selected_check_count != self._run_started.selected_check_count:
                raise ValueError("terminal selected_check_count changed during the run")
            if len(self._checks) != event.selected_check_count:
                raise ValueError("normal terminal event requires one CheckProcessed per check")
            if event.query_count != len(self._queries):
                raise ValueError("RunFinished.query_count does not reconcile")
            total_ms = sum(query.duration_ms for query in self._queries)
            maximum = max((query.duration_ms for query in self._queries), default=None)
            if event.query_total_ms != total_ms or event.query_max_ms != maximum:
                raise ValueError("RunFinished query timings do not reconcile")
            if event.probe_ms != (self._probe.duration_ms if self._probe else None):
                raise ValueError("RunFinished.probe_ms does not reconcile")
            expected = _check_counts(self._checks.values())
            actual = {
                "executed_check_count": event.executed_check_count,
                "engine_error_count": event.engine_error_count,
                "skipped_generated_count": event.skipped_generated_count,
                "skipped_unsupported_count": event.skipped_unsupported_count,
                "skipped_not_run_count": event.skipped_not_run_count,
            }
            if actual != expected:
                raise ValueError("RunFinished check counters do not reconcile")
        elif self._run_started is not None and len(self._checks) != (
            self._run_started.selected_check_count
        ):
            raise ValueError("fault terminal requires one CheckProcessed per selected check")
        self._terminal = event

    def posthog_events(self) -> tuple[PostHogEvent, ...]:
        """Build only the four engine-derived outbound event names."""

        output: list[PostHogEvent] = []
        for event in self._events:
            if isinstance(event, RunStarted):
                output.append(PostHogEvent("graphcheck_run_started", _event_properties(event)))
            elif isinstance(event, CheckProcessed):
                properties = _event_properties(event)
                properties.update(self._queries_by_check[event.check_sequence].properties())
                output.append(PostHogEvent("graphcheck_check_processed", properties))
            elif isinstance(event, RunFinished):
                properties = _event_properties(event)
                properties["terminal_kind"] = "finished"
                output.append(PostHogEvent("graphcheck_run_completed", properties))
            elif isinstance(event, EngineFaulted):
                fault = _event_properties(event)
                output.append(PostHogEvent("graphcheck_engine_faulted", fault))
                completion = {
                    **_envelope_properties(event),
                    "terminal_kind": "faulted",
                    "engine_stage": event.engine_stage.value,
                    "exception_type": event.exception_type.value,
                    "safe_error_code": event.safe_error_code.value,
                    "elapsed_ms": event.elapsed_ms,
                    "selected_check_count": (
                        self._run_started.selected_check_count if self._run_started else 0
                    ),
                    "processed_check_count": len(self._checks),
                    "query_count": len(self._queries),
                    "query_total_ms": sum(query.duration_ms for query in self._queries),
                    "query_max_ms": max(
                        (query.duration_ms for query in self._queries), default=None
                    ),
                    "probe_ms": self._probe.duration_ms if self._probe else None,
                }
                output.append(PostHogEvent("graphcheck_run_completed", completion))
        return tuple(output)


def _check_counts(checks) -> dict[str, int]:
    counts = {
        "executed_check_count": 0,
        "engine_error_count": 0,
        "skipped_generated_count": 0,
        "skipped_unsupported_count": 0,
        "skipped_not_run_count": 0,
    }
    for check in checks:
        if check.processing_outcome is ProcessingOutcome.COMPLETED:
            counts["executed_check_count"] += 1
        elif check.processing_outcome is ProcessingOutcome.ENGINE_ERROR:
            counts["engine_error_count"] += 1
        elif check.skip_reason is SkipReason.GENERATED:
            counts["skipped_generated_count"] += 1
        elif check.skip_reason is SkipReason.UNSUPPORTED:
            counts["skipped_unsupported_count"] += 1
        elif check.skip_reason is SkipReason.NOT_RUN:
            counts["skipped_not_run_count"] += 1
    return counts


def _event_properties(event: EngineEvent) -> dict[str, object]:
    properties = _envelope_properties(event)
    dumped = event.model_dump(
        mode="json",
        exclude={
            "schema_version",
            "event_id",
            "telemetry_run_id",
            "sequence",
            "occurred_at",
            "kind",
        },
    )
    properties.update(dumped)
    return properties


def _envelope_properties(event: EngineEvent) -> dict[str, object]:
    return {
        "engine_event_schema_version": event.schema_version,
        "engine_event_id": str(event.event_id),
        "telemetry_run_id": str(event.telemetry_run_id),
        "engine_event_sequence": event.sequence,
        "engine_event_occurred_at": event.occurred_at.isoformat(),
        "engine_event_kind": event.kind.value,
    }
