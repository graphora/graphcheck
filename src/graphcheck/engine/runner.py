from __future__ import annotations

import hashlib
import inspect
import json
import math
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from graphcheck import __version__
from graphcheck.contracts.check import CompetencyCheck, ConformanceCheck, DriftCheck, load_suite
from graphcheck.contracts.check import Suite as LoadedSuite
from graphcheck.contracts.results import (
    SCHEMA_VERSION,
    WEIGHTS,
    CheckError,
    CheckResult,
    RedactionPolicy,
    Results,
    ResultsTarget,
    RunStatus,
    RunTarget,
    Severity,
    SkipReason,
    Verdict,
    exit_code,
    totals,
)
from graphcheck.engine.baseline import (
    BaselineProvider,
    BaselineValue,
    MappingBaselineProvider,
    require_baseline,
)
from graphcheck.engine.compiler import (
    CompiledCheck,
    CypherCompiler,
    expected_for,
    name_for,
)
from graphcheck.engine.evaluator import CompetencyConsumption, Evaluation, VerdictEvaluator
from graphcheck.engine.executor import ExecutionResult, ReadOnlyExecutor
from graphcheck.engine.parameters import (
    GraphTokenResolver,
    ParameterTokenResolver,
    resolve_parameters,
)
from graphcheck.engine.sampling import SamplingPolicy
from graphcheck.errors import GraphCheckError, GraphCheckTimeoutError
from graphcheck.packs import PACK_VERSION
from graphcheck.packs.catalog import builtin_pack_catalog
from graphcheck.scoring import calculate_score, calculate_suite_scores
from graphcheck.telemetry.events import (
    CheckProcessed,
    EngineEventEmitter,
    EngineEventSink,
    EngineFaulted,
    EngineStage,
    EventOutcome,
    PartialReasonCode,
    ProcessingOutcome,
    QueryFinished,
    QueryRole,
    ReadGuardOutcome,
    RunFinished,
    RunStarted,
    SafeErrorCode,
    TargetProbeFinished,
    TargetSource,
)
from graphcheck.telemetry.events import (
    SkipReason as TelemetrySkipReason,
)
from graphcheck.telemetry.policy import (
    safe_error_code,
    safe_exception_type,
    safe_pattern,
    safe_template,
    version_major_minor,
)

_ProgressCallback = Callable[[int, int, str], None]

_CAPABILITY_FIXES = {
    "apoc": "Install APOC for this Neo4j DBMS, restart Neo4j, then rerun GraphCheck.",
    "count_store": "Enable count-store support for this check, then rerun GraphCheck.",
    "store_consistency": (
        "Run Neo4j's offline consistency checker or install a connector that exposes a "
        "read-only relationship-store consistency probe."
    ),
}


def _deadline_reason(seconds: float) -> str:
    return (
        f"the {seconds:g}-second run budget was exhausted. Fix: select fewer checks, narrow "
        "expensive queries, or enable sampling, then rerun."
    )


@dataclass(frozen=True)
class SuiteInput:
    suite: LoadedSuite
    source_sha: str

    @classmethod
    def from_yaml(cls, text: str, *, source: str | None = None) -> SuiteInput:
        return cls(
            suite=load_suite(text, source=source),
            source_sha=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )


@dataclass(frozen=True)
class YamlSuiteInput:
    """One independently loadable suite source for partial multi-suite runs."""

    text: str
    source: str | None = None


@dataclass(frozen=True)
class EngineConfig:
    # Leave a small serialization/reporting margin inside the user-facing five-minute budget.
    time_budget_s: float = 295.0
    evidence_cap: int = 100
    result_row_limit: int = 100_000
    eager_competency_evaluation: bool = False
    max_concurrency: int = 1
    sampling: SamplingPolicy = field(
        default_factory=lambda: SamplingPolicy(
            exhaustive_limit=100_000,
            sample_size=10_000,
            seed=0,
        )
    )

    def __post_init__(self) -> None:
        if (
            isinstance(self.time_budget_s, bool)
            or not isinstance(self.time_budget_s, (int, float))
            or not math.isfinite(self.time_budget_s)
            or self.time_budget_s <= 0
        ):
            raise ValueError("time_budget_s must be finite and positive")
        if (
            isinstance(self.evidence_cap, bool)
            or not isinstance(self.evidence_cap, int)
            or self.evidence_cap < 1
        ):
            raise ValueError("evidence_cap must be a positive integer")
        if (
            isinstance(self.max_concurrency, bool)
            or not isinstance(self.max_concurrency, int)
            or self.max_concurrency < 1
        ):
            raise ValueError("max_concurrency must be a positive integer")
        if (
            isinstance(self.result_row_limit, bool)
            or not isinstance(self.result_row_limit, int)
            or self.result_row_limit < 1
        ):
            raise ValueError("result_row_limit must be a positive integer")
        if not isinstance(self.eager_competency_evaluation, bool):
            raise ValueError("eager_competency_evaluation must be boolean")


@dataclass
class _CheckTimings:
    compile_ms: int | None = None
    parameter_resolution_ms: int | None = None
    sampling_population_ms: int | None = None
    baseline_resolution_ms: int | None = None
    read_guard_ms: int | None = None
    evaluation_ms: int | None = None


class Engine:
    def __init__(
        self,
        client: object,
        *,
        baselines: BaselineProvider | Mapping[str, object] | None = None,
        config: EngineConfig | None = None,
        compiler: CypherCompiler | None = None,
        evaluator: VerdictEvaluator | None = None,
        parameter_resolver: ParameterTokenResolver | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        id_factory: Callable[[], object] | None = None,
        progress_callback: _ProgressCallback | None = None,
        event_sink: EngineEventSink | None = None,
        telemetry_clock: Callable[[], datetime] | None = None,
        telemetry_id_factory: Callable[[], uuid.UUID] | None = None,
    ) -> None:
        self.client = client
        self.config = config or EngineConfig()
        self.compiler = compiler or CypherCompiler(evidence_cap=self.config.evidence_cap)
        self.evaluator = evaluator or VerdictEvaluator()
        self.executor = ReadOnlyExecutor(client)
        if isinstance(baselines, Mapping):
            self.baselines: BaselineProvider | None = MappingBaselineProvider(baselines)
        else:
            self.baselines = baselines
        self.parameter_resolver = parameter_resolver or GraphTokenResolver()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._id_factory = id_factory or uuid.uuid4
        self._progress_callback = progress_callback
        probe = getattr(client, "probe", None)
        self._probe_accepts_timeout = callable(probe) and _accepts_timeout(probe)
        self._event_sink = event_sink
        self._telemetry_clock = telemetry_clock
        self._telemetry_id_factory = telemetry_id_factory
        self._telemetry: EngineEventEmitter | None = None
        self._telemetry_started_perf: float | None = None
        self._telemetry_stage = EngineStage.PROBE
        self._telemetry_checks: list[tuple[int, object]] = []
        self._telemetry_processed: set[int] = set()
        self._telemetry_query_durations: list[int] = []
        self._telemetry_query_durations_by_check: dict[int, list[int]] = {}
        self._telemetry_read_guard_ms_by_check: dict[int, list[int]] = {}
        self._telemetry_sampled_checks: set[int] = set()
        self._telemetry_probe_ms: int | None = None
        self._telemetry_deadline: float | None = None
        self._telemetry_partial_codes: list[PartialReasonCode] = []
        self._active_check_context: ContextVar[tuple[int, object] | None] = ContextVar(
            f"graphcheck_active_check_{id(self)}", default=None
        )
        self._telemetry_state_lock = threading.Lock()

    def run_yaml(
        self,
        text: str,
        *,
        source: str | None = None,
        target: RunTarget | None = None,
        tags: Sequence[str] = (),
        fail_fast: bool = False,
    ) -> Results:
        return self.run(
            [SuiteInput.from_yaml(text, source=source)],
            target=target,
            tags=tags,
            fail_fast=fail_fast,
        )

    def run_suite(
        self,
        suite: LoadedSuite,
        *,
        source_text: str | None = None,
        source_sha: str | None = None,
        target: RunTarget | None = None,
        tags: Sequence[str] = (),
        fail_fast: bool = False,
    ) -> Results:
        sha = source_sha or (
            hashlib.sha256(source_text.encode("utf-8")).hexdigest()
            if source_text is not None
            else _canonical_suite_sha(suite)
        )
        return self.run(
            [SuiteInput(suite=suite, source_sha=sha)],
            target=target,
            tags=tags,
            fail_fast=fail_fast,
        )

    def run_yamls(
        self,
        suites: Sequence[YamlSuiteInput],
        *,
        target: RunTarget | None = None,
        tags: Sequence[str] = (),
        fail_fast: bool = False,
    ) -> Results:
        loaded: list[SuiteInput] = []
        unloadable: list[str] = []
        for index, item in enumerate(suites, start=1):
            label = item.source or f"suite input {index}"
            try:
                loaded.append(SuiteInput.from_yaml(item.text, source=item.source))
            except Exception as exc:
                unloadable.append(f"{label} could not be loaded ({type(exc).__name__}: {exc})")
        return self.run(
            loaded,
            target=target,
            tags=tags,
            fail_fast=fail_fast,
            _initial_partial_reasons=unloadable,
        )

    def run(
        self,
        suites: Sequence[SuiteInput | LoadedSuite],
        *,
        target: RunTarget | None = None,
        tags: Sequence[str] = (),
        fail_fast: bool = False,
        selection_suites: Sequence[str] | None = None,
        _initial_partial_reasons: Sequence[str] = (),
    ) -> Results:
        """Run checks and contain all event-sink failures at the engine boundary."""

        self._reset_telemetry_state()
        try:
            return self._run_with_events(
                suites,
                target=target,
                tags=tags,
                fail_fast=fail_fast,
                selection_suites=selection_suites,
                _initial_partial_reasons=_initial_partial_reasons,
            )
        except Exception as exc:
            if (
                self._telemetry is not None
                and self._telemetry.enabled
                and self._telemetry_started_perf is not None
            ):
                self._emit_unprocessed_as_not_run()
                self._telemetry.emit(
                    EngineFaulted,
                    engine_stage=self._telemetry_stage,
                    exception_type=safe_exception_type(exc),
                    safe_error_code=SafeErrorCode.ENGINE_UNEXPECTED,
                    elapsed_ms=_duration_ms(
                        self._telemetry_started_perf,
                        self._monotonic(),
                    ),
                )
            raise
        finally:
            self._active_check_context.set(None)

    def _run_with_events(
        self,
        suites: Sequence[SuiteInput | LoadedSuite],
        *,
        target: RunTarget | None = None,
        tags: Sequence[str] = (),
        fail_fast: bool = False,
        selection_suites: Sequence[str] | None = None,
        _initial_partial_reasons: Sequence[str] = (),
    ) -> Results:
        requested_tags = list(dict.fromkeys(tags))
        inputs = [
            item if isinstance(item, SuiteInput) else SuiteInput(item, _canonical_suite_sha(item))
            for item in suites
        ]
        if requested_tags:
            inputs = [
                SuiteInput(
                    suite=item.suite.model_copy(
                        update={
                            "checks": [
                                check
                                for check in item.suite.checks
                                if any(tag in check.tags for tag in requested_tags)
                            ]
                        }
                    ),
                    source_sha=item.source_sha,
                )
                for item in inputs
            ]
        started_at = _timestamp(self._clock())
        started_perf = self._monotonic()
        deadline = started_perf + self.config.time_budget_s
        self._telemetry_deadline = deadline
        run_id = str(self._id_factory())
        selected_checks = [check for item in inputs for check in item.suite.checks]
        self._telemetry_checks = list(enumerate(selected_checks, start=1))
        if self._event_sink is not None:
            self._telemetry = EngineEventEmitter(
                self._event_sink,
                clock=self._telemetry_clock,
                id_factory=self._telemetry_id_factory,
            )
            self._telemetry_started_perf = started_perf
            conformance_count = sum(
                isinstance(check.spec, ConformanceCheck) for check in selected_checks
            )
            competency_count = sum(
                isinstance(check.spec, CompetencyCheck) for check in selected_checks
            )
            drift_count = sum(isinstance(check.spec, DriftCheck) for check in selected_checks)
            self._telemetry.emit(
                RunStarted,
                graphcheck_version=__version__,
                pack_version=PACK_VERSION,
                suite_count=len(inputs),
                selected_check_count=len(selected_checks),
                conformance_count=conformance_count,
                competency_count=competency_count,
                drift_count=drift_count,
                uses_sampling=any(_check_may_sample(check) for check in selected_checks),
                uses_baselines=bool(drift_count),
                fail_fast_enabled=fail_fast,
                suite_filter_used=selection_suites is not None,
                tag_filter_used=bool(requested_tags),
                time_budget_ms=max(0, round(self.config.time_budget_s * 1000)),
            )
            if _initial_partial_reasons:
                self._add_partial_code(PartialReasonCode.SUITE_INPUT_INVALID)
        suite_ids = [item.suite.suite for item in inputs]
        recorded_suite_ids = (
            suite_ids if selection_suites is None else list(dict.fromkeys(selection_suites))
        )
        if len(suite_ids) != len(set(suite_ids)):
            return self._failed_run(
                run_id,
                started_at,
                recorded_suite_ids,
                CheckError(
                    code="engine.duplicate_suite",
                    message="A run cannot contain the same suite id more than once.",
                    fix="Rename or remove the duplicate suite before running it again.",
                ),
                tags=requested_tags,
                fail_fast=fail_fast,
            )

        self._telemetry_stage = EngineStage.PROBE
        try:
            resolved_target = self._resolve_target_with_events(target, deadline)
        except GraphCheckError as exc:
            return self._failed_run(
                run_id,
                started_at,
                recorded_suite_ids,
                exc.error,
                tags=requested_tags,
                fail_fast=fail_fast,
            )
        except Exception as exc:  # connector boundary: always return the frozen error shape
            return self._failed_run(
                run_id,
                started_at,
                recorded_suite_ids,
                _unexpected_error("target probe", exc),
                tags=requested_tags,
                fail_fast=fail_fast,
            )

        tasks = [
            (suite_input, check) for suite_input in inputs for check in suite_input.suite.checks
        ]
        total_checks = len(tasks)
        result_slots: list[CheckResult | None] = [None] * total_checks
        completed_checks = 0

        def record_result(
            index: int,
            result: CheckResult,
            suite_id: str,
            check_id: str,
            *,
            timings: _CheckTimings | None = None,
        ) -> None:
            nonlocal completed_checks
            result_slots[index] = result
            completed_checks += 1
            self._emit_check_processed(
                index + 1,
                tasks[index][1],
                result,
                timings or _CheckTimings(),
            )
            if self._progress_callback is not None:
                self._progress_callback(
                    completed_checks,
                    total_checks,
                    f"{suite_id}/{check_id}",
                )

        partial_reasons: list[str] = list(dict.fromkeys(_initial_partial_reasons))
        capability_check = getattr(self.compiler, "missing_capabilities", None)

        def prepare(
            index: int, suite_input: SuiteInput, check, *, check_deadline: bool = True
        ) -> bool:
            suite_id = suite_input.suite.suite
            if check.generated:
                record_result(
                    index,
                    _skipped_result(check, suite_id, SkipReason.GENERATED),
                    suite_id,
                    check.id,
                )
                return False
            missing_capabilities = (
                tuple(capability_check(check, resolved_target))
                if callable(capability_check)
                else ()
            )
            if missing_capabilities:
                record_result(
                    index,
                    _skipped_result(check, suite_id, SkipReason.UNSUPPORTED),
                    suite_id,
                    check.id,
                )
                rendered = ", ".join(missing_capabilities)
                fixes = list(
                    dict.fromkeys(
                        _CAPABILITY_FIXES.get(
                            capability,
                            f"Enable the `{capability}` capability, then rerun GraphCheck.",
                        )
                        for capability in missing_capabilities
                    )
                )
                _append_once(
                    partial_reasons,
                    f"check {suite_id}/{check.id} requires missing capability: {rendered}. "
                    f"Fix: {' '.join(fixes)}",
                )
                self._add_partial_code(PartialReasonCode.UNSUPPORTED_CHECK)
                return False
            if check_deadline and self._monotonic() >= deadline:
                record_result(
                    index,
                    _skipped_result(check, suite_id, SkipReason.NOT_RUN),
                    suite_id,
                    check.id,
                )
                _append_once(
                    partial_reasons,
                    _deadline_reason(self.config.time_budget_s),
                )
                self._add_partial_code(PartialReasonCode.DEADLINE_EXHAUSTED)
                return False
            return True

        if fail_fast:
            fail_fast_after: str | None = None
            for index, (suite_input, check) in enumerate(tasks):
                suite_id = suite_input.suite.suite
                if fail_fast_after is not None:
                    record_result(
                        index,
                        _skipped_result(
                            check,
                            suite_id,
                            SkipReason.NOT_RUN,
                        ),
                        suite_id,
                        check.id,
                    )
                    _append_once(
                        partial_reasons,
                        f"fail-fast stopped the run after {fail_fast_after}",
                    )
                    continue
                if not prepare(index, suite_input, check):
                    continue
                result, partial_reason, timings = self._run_check_if_time(
                    check,
                    check_sequence=index + 1,
                    suite_id=suite_id,
                    suite_sha=suite_input.source_sha,
                    target=resolved_target,
                    deadline=deadline,
                )
                record_result(
                    index,
                    result,
                    suite_input.suite.suite,
                    check.id,
                    timings=timings,
                )
                if partial_reason is not None:
                    _append_once(partial_reasons, partial_reason)
                if _is_hard_result(result):
                    fail_fast_after = f"{suite_id}/{check.id}"
                if self._monotonic() >= deadline:
                    _append_once(
                        partial_reasons,
                        _deadline_reason(self.config.time_budget_s),
                    )
                    self._add_partial_code(PartialReasonCode.DEADLINE_EXHAUSTED)
        else:
            runnable = [
                (index, suite_input, check)
                for index, (suite_input, check) in enumerate(tasks)
                if prepare(index, suite_input, check, check_deadline=False)
            ]
            outcomes: dict[int, str | None] = {}
            if self.config.max_concurrency == 1:
                for index, suite_input, check in runnable:
                    result, reason, timings = self._run_check_if_time(
                        check,
                        check_sequence=index + 1,
                        suite_id=suite_input.suite.suite,
                        suite_sha=suite_input.source_sha,
                        target=resolved_target,
                        deadline=deadline,
                    )
                    outcomes[index] = reason
                    record_result(
                        index,
                        result,
                        suite_input.suite.suite,
                        check.id,
                        timings=timings,
                    )
            elif runnable:
                with ThreadPoolExecutor(
                    max_workers=min(self.config.max_concurrency, len(runnable)),
                    thread_name_prefix="graphcheck",
                ) as pool:
                    futures = {
                        pool.submit(
                            self._run_check_if_time,
                            check,
                            check_sequence=index + 1,
                            suite_id=suite_input.suite.suite,
                            suite_sha=suite_input.source_sha,
                            target=resolved_target,
                            deadline=deadline,
                        ): (index, suite_input.suite.suite, check.id)
                        for index, suite_input, check in runnable
                    }
                    for future in as_completed(futures):
                        index, suite_id, check_id = futures[future]
                        result, reason, timings = future.result()
                        outcomes[index] = reason
                        record_result(index, result, suite_id, check_id, timings=timings)
            for index in sorted(outcomes):
                if outcomes[index] is not None:
                    _append_once(partial_reasons, outcomes[index])
            if runnable and self._monotonic() >= deadline:
                _append_once(
                    partial_reasons,
                    _deadline_reason(self.config.time_budget_s),
                )
                self._add_partial_code(PartialReasonCode.DEADLINE_EXHAUSTED)

        check_results = [result for result in result_slots if result is not None]
        status = RunStatus.PARTIAL if partial_reasons else RunStatus.COMPLETE
        partial_reason = "; ".join(partial_reasons) if partial_reasons else None
        self._telemetry_stage = EngineStage.FINALIZE
        results = self._results(
            run_id=run_id,
            started_at=started_at,
            finished_at=_timestamp(self._clock()),
            status=status,
            partial_reason=partial_reason,
            suite_ids=recorded_suite_ids,
            target=resolved_target,
            inputs=inputs,
            checks=check_results,
            tags=requested_tags,
            fail_fast=fail_fast,
        )
        self._emit_run_finished(results, started_perf)
        return results

    def _run_check(
        self,
        check,
        *,
        suite_id: str,
        suite_sha: str,
        target: RunTarget,
        deadline: float,
    ) -> tuple[CheckResult, str | None, _CheckTimings]:
        check_started_at = _timestamp(self._clock())
        check_started_perf = self._monotonic()
        timings = _CheckTimings()
        compiled: CompiledCheck | None = None
        resolved_params: dict[str, object] | None = None
        baseline: BaselineValue | None = None
        partial_reason: str | None = None
        try:
            self._telemetry_stage = EngineStage.COMPILE
            stage_started = self._timing_start()
            sample_seed = self.config.sampling.check_seed(
                graph_fingerprint=target.fingerprint,
                suite_sha=suite_sha,
                check_id=check.id,
            )
            compiled = self.compiler.compile(check, sample_seed=sample_seed)
            timings.compile_ms = self._timing_finish(stage_started)
            if isinstance(check.spec, CompetencyCheck):
                self._telemetry_stage = EngineStage.RESOLVE_PARAMS
                stage_started = self._timing_start()
                resolved_params = self._resolve_parameters_with_events(
                    compiled,
                    deadline,
                )
                timings.parameter_resolution_ms = self._timing_finish(stage_started)
            else:
                resolved_params = dict(compiled.params)
            if compiled.sampled:
                self._telemetry_stage = EngineStage.SAMPLE
                compiled, resolved_params, timings.sampling_population_ms = self._apply_sampling(
                    compiled,
                    resolved_params,
                    check=check,
                    suite_sha=suite_sha,
                    target=target,
                    deadline=deadline,
                )
            if isinstance(check.spec, DriftCheck):
                self._telemetry_stage = EngineStage.BASELINE
                stage_started = self._timing_start()
                baseline = require_baseline(
                    self.baselines,
                    check.spec.baseline,
                    check.spec.metric,
                    check.spec.target,
                )
                timings.baseline_resolution_ms = self._timing_finish(stage_started)
                if baseline.partial:
                    partial_reason = (
                        f"check {suite_id}/{check.id} used partial baseline {check.spec.baseline!r}"
                    )
                    self._add_partial_code(PartialReasonCode.PARTIAL_BASELINE)
            self._telemetry_stage = EngineStage.QUERY
            consumption = (
                CompetencyConsumption(compiled, self.config.result_row_limit)
                if isinstance(check.spec, CompetencyCheck)
                and not self.config.eager_competency_evaluation
                else None
            )
            if compiled.evidence_query is None:
                execution = self._execute_query_with_event(
                    compiled.query,
                    resolved_params,
                    role=QueryRole.CHECK_MEASUREMENT,
                    timeout_s=_remaining(deadline, self._monotonic()),
                    policy=consumption.policy if consumption is not None else None,
                    stop_when=consumption.stop_when if consumption is not None else None,
                )
            else:
                with self.executor.transaction(
                    timeout_s=_remaining(deadline, self._monotonic())
                ) as transaction:
                    execution = self._execute_query_with_event(
                        compiled.query,
                        resolved_params,
                        role=QueryRole.CHECK_MEASUREMENT,
                        timeout_s=_remaining(deadline, self._monotonic()),
                        executor=transaction,
                    )
                    if _evidence_required(compiled, execution.rows):
                        evidence_execution = self._execute_query_with_event(
                            compiled.evidence_query,
                            compiled.evidence_params or resolved_params,
                            role=QueryRole.EVIDENCE_COLLECTION,
                            timeout_s=_remaining(deadline, self._monotonic()),
                            executor=transaction,
                        )
                        execution = _merge_evidence(compiled, execution, evidence_execution)
            if consumption is not None and not execution.complete and not consumption.decisive:
                raise GraphCheckError(
                    "engine.result_limit_exceeded",
                    "The competency query reached the configured result-row safety ceiling "
                    "before its assertions became decisive.",
                    "Narrow the query or increase result_row_limit after reviewing "
                    "its memory cost.",
                )
            timings.read_guard_ms = execution.read_guard_ms
            self._telemetry_stage = EngineStage.EVALUATE
            stage_started = self._timing_start()
            evaluator_kwargs = {
                "columns": execution.columns,
                "baseline": baseline,
            }
            if isinstance(self.evaluator, VerdictEvaluator):
                evaluator_kwargs.update(
                    complete=execution.complete,
                    observed_rows=execution.observed_rows,
                    graph_empty=(
                        getattr(target, "nodes", None) == 0
                        and getattr(target, "relationships", None) == 0
                    ),
                )
            evaluation = self.evaluator.evaluate(
                # Evidence extraction must see executed literals, never unresolved graph tokens.
                replace(compiled, params=resolved_params),
                execution.rows,
                **evaluator_kwargs,
            )
            if not isinstance(evaluation, Evaluation):
                raise GraphCheckError(
                    "engine.invalid_evaluation",
                    f"Evaluator returned {type(evaluation).__name__}, not an Evaluation.",
                    "Fix the evaluator implementation to return the frozen C1 Evaluation shape.",
                )
            timings.evaluation_ms = self._timing_finish(stage_started)
            verdict = (
                Verdict.PASS
                if evaluation.passed
                else Verdict.FAIL
                if check.severity is Severity.ERROR
                else Verdict.WARN
            )
            result = CheckResult(
                id=check.id,
                suite_id=suite_id,
                pattern=check.pattern,
                name=compiled.name,
                provenance=check.provenance,
                severity=check.severity,
                verdict=verdict,
                skip_reason=None,
                started_at=check_started_at,
                duration_ms=_duration_ms(check_started_perf, self._monotonic()),
                compiled_query=compiled.query,
                params=resolved_params,
                measured=evaluation.measured,
                expected=compiled.expected,
                estimate=evaluation.estimate,
                evidence=evaluation.evidence,
                error=None,
            )
            return result, partial_reason, timings
        except GraphCheckError as exc:
            error = exc.error
            if error.code == "engine.baseline_partial_missing":
                partial_reason = (
                    f"check {suite_id}/{check.id} used a partial baseline missing its measurement"
                )
                self._add_partial_code(PartialReasonCode.BASELINE_MEASUREMENT_MISSING)
        except Exception as exc:  # isolate a broken pack/evaluator from every later check
            error = _unexpected_error(f"check {suite_id}/{check.id}", exc)

        return (
            CheckResult(
                id=check.id,
                suite_id=suite_id,
                pattern=check.pattern,
                name=compiled.name if compiled is not None else name_for(check),
                provenance=check.provenance,
                severity=check.severity,
                verdict=Verdict.ERRORED,
                skip_reason=None,
                started_at=check_started_at,
                duration_ms=_duration_ms(check_started_perf, self._monotonic()),
                compiled_query=compiled.query if compiled is not None else None,
                params=resolved_params,
                measured=None,
                expected=compiled.expected if compiled is not None else expected_for(check),
                estimate=False,
                evidence=None,
                error=error,
            ),
            partial_reason,
            timings,
        )

    def _run_check_if_time(
        self,
        check,
        *,
        check_sequence: int,
        suite_id: str,
        suite_sha: str,
        target: RunTarget,
        deadline: float,
    ) -> tuple[CheckResult, str | None, _CheckTimings]:
        token = self._active_check_context.set((check_sequence, check))
        try:
            if self._monotonic() >= deadline:
                return (
                    _skipped_result(check, suite_id, SkipReason.NOT_RUN),
                    _deadline_reason(self.config.time_budget_s),
                    _CheckTimings(),
                )
            return self._run_check(
                check,
                suite_id=suite_id,
                suite_sha=suite_sha,
                target=target,
                deadline=deadline,
            )
        finally:
            self._active_check_context.reset(token)

    def _apply_sampling(
        self,
        compiled: CompiledCheck,
        params: dict[str, object],
        *,
        check,
        suite_sha: str,
        target: RunTarget,
        deadline: float,
    ) -> tuple[CompiledCheck, dict[str, object], int | None]:
        requested = (
            check.spec.with_.get("sample_size")
            if isinstance(check.spec, ConformanceCheck)
            else None
        )
        check_requested = requested
        if requested is None:
            requested = compiled.params.get("sample_size")
        if not compiled.sampling_preflight:
            sample_size = self.config.sampling.sample_size
            if requested is not None:
                sample_size = min(sample_size, int(requested))
            exhaustive_limit = self.config.sampling.exhaustive_limit
            if check_requested is not None:
                exhaustive_limit = min(exhaustive_limit, int(check_requested))
            resolved = {
                **params,
                "sample_size": sample_size,
                **(
                    {"exhaustive_limit": exhaustive_limit}
                    if "exhaustive_limit" in compiled.params
                    else {}
                ),
            }
            return (
                replace(
                    compiled,
                    params=resolved,
                    expected={**compiled.expected, "sample_size": sample_size},
                ),
                resolved,
                None,
            )
        if compiled.population_query is None:
            raise GraphCheckError(
                "engine.sampling_invalid",
                f"Sampled check {check.id!r} has no population query.",
                "Fix the pack compiler to provide a deterministic population query.",
            )
        stage_started = self._timing_start()
        population_execution = self._execute_query_with_event(
            compiled.population_query,
            compiled.population_params or {},
            role=QueryRole.SAMPLING_POPULATION,
            timeout_s=_remaining(deadline, self._monotonic()),
        )
        sampling_population_ms = self._timing_finish(stage_started)
        if len(population_execution.rows) != 1:
            raise GraphCheckError(
                "engine.sampling_invalid",
                f"Sampled check {check.id!r} did not return one population row.",
                "Fix the pack's population query to return one non-negative count.",
            )
        population = population_execution.rows[0].get("population")
        if (
            isinstance(population, bool)
            or not isinstance(population, (int, float))
            or int(population) != population
            or population < 0
        ):
            raise GraphCheckError(
                "engine.sampling_invalid",
                f"Sampled check {check.id!r} returned an invalid population {population!r}.",
                "Fix the pack's population query to return a non-negative integer.",
            )
        population = int(population)
        decision = self.config.sampling.decide(
            population,
            graph_fingerprint=target.fingerprint,
            suite_sha=suite_sha,
            check_id=check.id,
        )
        sample_size = (
            decision.sample_size if requested is None else min(decision.sample_size, int(requested))
        )
        resolved = {**params, "sample_size": sample_size}
        return (
            replace(
                compiled,
                params={**compiled.params, "sample_size": sample_size},
                expected={**compiled.expected, "sample_size": sample_size},
                sample_population=population,
            ),
            resolved,
            sampling_population_ms,
        )

    def _reset_telemetry_state(self) -> None:
        self._telemetry = None
        self._telemetry_started_perf = None
        self._telemetry_stage = EngineStage.PROBE
        self._telemetry_checks = []
        self._telemetry_processed = set()
        self._telemetry_query_durations = []
        self._telemetry_query_durations_by_check = {}
        self._telemetry_read_guard_ms_by_check = {}
        self._telemetry_sampled_checks = set()
        self._telemetry_probe_ms = None
        self._telemetry_deadline = None
        self._telemetry_partial_codes = []
        self._active_check_context.set(None)

    def _resolve_target_with_events(
        self,
        target: RunTarget | None,
        deadline: float,
    ) -> ResultsTarget:
        if target is not None:
            major, minor = version_major_minor(target.server_version)
            if self._telemetry is not None and self._telemetry.enabled:
                self._telemetry.emit(
                    TargetProbeFinished,
                    outcome=EventOutcome.SUCCESS,
                    duration_ms=0,
                    target_source=TargetSource.PROVIDED,
                    server_version_major=major,
                    server_version_minor=minor,
                    apoc_available=target.capabilities.apoc,
                    count_store_available=target.capabilities.count_store,
                    error_code=None,
                )
            self._telemetry_probe_ms = 0
            return ResultsTarget.model_validate(target.model_dump())

        probe_started = self._timing_start()
        try:
            resolved = self._probe_target(deadline)
        except Exception as exc:
            duration_ms = self._timing_finish(probe_started) or 0
            self._telemetry_probe_ms = duration_ms
            raw_code = exc.error.code if isinstance(exc, GraphCheckError) else None
            code = safe_error_code(raw_code) or SafeErrorCode.UNKNOWN
            outcome = _telemetry_outcome(exc, raw_code)
            self._emit_query(
                role=QueryRole.TARGET_PROBE,
                outcome=outcome,
                duration_ms=duration_ms,
                error_code=code,
                read_guard_outcome=ReadGuardOutcome.NOT_RUN,
            )
            if self._telemetry is not None and self._telemetry.enabled:
                self._telemetry.emit(
                    TargetProbeFinished,
                    outcome=outcome,
                    duration_ms=duration_ms,
                    target_source=TargetSource.PROBED,
                    server_version_major=None,
                    server_version_minor=None,
                    apoc_available=None,
                    count_store_available=None,
                    error_code=code,
                )
            raise

        duration_ms = self._timing_finish(probe_started) or 0
        self._telemetry_probe_ms = duration_ms
        self._emit_query(
            role=QueryRole.TARGET_PROBE,
            outcome=EventOutcome.SUCCESS,
            duration_ms=duration_ms,
            error_code=None,
            read_guard_outcome=ReadGuardOutcome.NOT_RUN,
        )
        major, minor = version_major_minor(resolved.server_version)
        if self._telemetry is not None and self._telemetry.enabled:
            self._telemetry.emit(
                TargetProbeFinished,
                outcome=EventOutcome.SUCCESS,
                duration_ms=duration_ms,
                target_source=TargetSource.PROBED,
                server_version_major=major,
                server_version_minor=minor,
                apoc_available=resolved.capabilities.apoc,
                count_store_available=resolved.capabilities.count_store,
                error_code=None,
            )
        return resolved

    def _resolve_parameters_with_events(
        self,
        compiled: CompiledCheck,
        deadline: float,
    ) -> dict[str, object]:
        has_tokens = any(
            isinstance(value, str) and value.startswith("$") for value in compiled.params.values()
        )
        started = self._timing_start() if has_tokens else None
        try:
            resolved = resolve_parameters(
                compiled.params,
                self.client,
                resolver=self.parameter_resolver,
                timeout_factory=lambda: _remaining(deadline, self._monotonic()),
            )
        except Exception as exc:
            if has_tokens:
                duration_ms = self._timing_finish(started) or 0
                raw_code = exc.error.code if isinstance(exc, GraphCheckError) else None
                code = safe_error_code(raw_code) or SafeErrorCode.UNKNOWN
                self._emit_query(
                    role=QueryRole.PARAMETER_RESOLUTION,
                    outcome=_telemetry_outcome(exc, raw_code),
                    duration_ms=duration_ms,
                    error_code=code,
                    read_guard_outcome=_read_guard_error_outcome(code),
                )
            raise
        if has_tokens:
            self._emit_query(
                role=QueryRole.PARAMETER_RESOLUTION,
                outcome=EventOutcome.SUCCESS,
                duration_ms=self._timing_finish(started) or 0,
                error_code=None,
                read_guard_outcome=(
                    ReadGuardOutcome.ALLOWED
                    if callable(getattr(self.client, "run_read_result", None))
                    else ReadGuardOutcome.NOT_RUN
                ),
            )
        return resolved

    def _execute_query_with_event(
        self,
        query: str,
        params: Mapping[str, object],
        *,
        role: QueryRole,
        timeout_s: float,
        policy=None,
        stop_when=None,
        executor: ReadOnlyExecutor | None = None,
    ):
        started = self._timing_start()
        active_executor = executor or self.executor
        try:
            execution = active_executor.execute(
                query,
                params,
                timeout_s=timeout_s,
                policy=policy,
                stop_when=stop_when,
            )
        except Exception as exc:
            duration_ms = self._timing_finish(started) or 0
            raw_code = exc.error.code if isinstance(exc, GraphCheckError) else None
            code = safe_error_code(raw_code) or SafeErrorCode.UNKNOWN
            self._emit_query(
                role=role,
                outcome=_telemetry_outcome(exc, raw_code),
                duration_ms=duration_ms,
                error_code=code,
                read_guard_outcome=_read_guard_error_outcome(code),
            )
            raise
        self._emit_query(
            role=role,
            outcome=EventOutcome.SUCCESS,
            duration_ms=self._timing_finish(started) or 0,
            error_code=None,
            read_guard_outcome=(
                ReadGuardOutcome.ALLOWED
                if callable(getattr(active_executor.client, "run_read_result", None))
                else ReadGuardOutcome.NOT_RUN
            ),
            server_available_after_ms=execution.server_available_after_ms,
            server_consumed_after_ms=execution.server_consumed_after_ms,
            read_guard_ms=execution.read_guard_ms,
            read_guard_cache_hit=execution.read_guard_cache_hit,
            notification_count=execution.notification_count,
        )
        active_check = self._active_check_context.get()
        if active_check is not None and execution.read_guard_ms is not None:
            with self._telemetry_state_lock:
                self._telemetry_read_guard_ms_by_check.setdefault(active_check[0], []).append(
                    execution.read_guard_ms
                )
        return execution

    def _emit_query(
        self,
        *,
        role: QueryRole,
        outcome: EventOutcome,
        duration_ms: int,
        error_code: SafeErrorCode | None,
        read_guard_outcome: ReadGuardOutcome,
        server_available_after_ms: int | None = None,
        server_consumed_after_ms: int | None = None,
        read_guard_ms: int | None = None,
        read_guard_cache_hit: bool | None = None,
        notification_count: int | None = None,
    ) -> None:
        if self._telemetry is None or not self._telemetry.enabled:
            return
        active_check = None if role is QueryRole.TARGET_PROBE else self._active_check_context.get()
        check_sequence = None if active_check is None else active_check[0]
        check = None if active_check is None else active_check[1]
        with self._telemetry_state_lock:
            if check_sequence is not None:
                self._telemetry_query_durations_by_check.setdefault(check_sequence, []).append(
                    duration_ms
                )
                if role is QueryRole.SAMPLING_POPULATION:
                    self._telemetry_sampled_checks.add(check_sequence)
            self._telemetry_query_durations.append(duration_ms)
            self._telemetry.emit(
                QueryFinished,
                check_sequence=check_sequence,
                pattern=None if check is None else safe_pattern(check.pattern),
                template=None if check is None else _telemetry_template(check),
                query_role=role,
                outcome=outcome,
                duration_ms=duration_ms,
                server_available_after_ms=server_available_after_ms,
                server_consumed_after_ms=server_consumed_after_ms,
                read_guard_outcome=read_guard_outcome,
                read_guard_ms=read_guard_ms,
                read_guard_cache_hit=read_guard_cache_hit,
                notification_count=notification_count,
                error_code=error_code,
            )

    def _emit_check_processed(
        self,
        check_sequence: int,
        check,
        result: CheckResult,
        timings: _CheckTimings,
    ) -> None:
        if self._telemetry is None or not self._telemetry.enabled:
            return
        query_durations = self._telemetry_query_durations_by_check.get(check_sequence, [])
        read_guard_durations = self._telemetry_read_guard_ms_by_check.get(check_sequence, [])
        if result.verdict is Verdict.SKIPPED:
            processing_outcome = ProcessingOutcome.SKIPPED
            skip_reason = TelemetrySkipReason(result.skip_reason.value)
            error_code = None
            duration_ms = None
        elif result.verdict is Verdict.ERRORED:
            processing_outcome = ProcessingOutcome.ENGINE_ERROR
            skip_reason = None
            error_code = (
                safe_error_code(result.error.code if result.error else None)
                or SafeErrorCode.UNKNOWN
            )
            duration_ms = result.duration_ms
        else:
            processing_outcome = ProcessingOutcome.COMPLETED
            skip_reason = None
            error_code = None
            duration_ms = result.duration_ms
        self._telemetry.emit(
            CheckProcessed,
            check_sequence=check_sequence,
            pattern=safe_pattern(check.pattern),
            template=_telemetry_template(check),
            processing_outcome=processing_outcome,
            skip_reason=skip_reason,
            duration_ms=duration_ms,
            compile_ms=timings.compile_ms,
            parameter_resolution_ms=timings.parameter_resolution_ms,
            sampling_population_ms=timings.sampling_population_ms,
            baseline_resolution_ms=timings.baseline_resolution_ms,
            read_guard_ms=(
                sum(read_guard_durations) if read_guard_durations else timings.read_guard_ms
            ),
            query_ms=sum(query_durations) if query_durations else None,
            evaluation_ms=timings.evaluation_ms,
            query_count=len(query_durations),
            sampled=check_sequence in self._telemetry_sampled_checks,
            error_code=error_code,
        )
        self._telemetry_processed.add(check_sequence)

    def _emit_unprocessed_as_not_run(self) -> None:
        if self._telemetry is None or not self._telemetry.enabled:
            return
        for check_sequence, check in self._telemetry_checks:
            if check_sequence in self._telemetry_processed:
                continue
            self._telemetry.emit(
                CheckProcessed,
                check_sequence=check_sequence,
                pattern=safe_pattern(check.pattern),
                template=_telemetry_template(check),
                processing_outcome=ProcessingOutcome.SKIPPED,
                skip_reason=TelemetrySkipReason.NOT_RUN,
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
            self._telemetry_processed.add(check_sequence)

    def _emit_run_finished(self, results: Results, started_perf: float) -> None:
        if self._telemetry is None or not self._telemetry.enabled:
            return
        self._emit_unprocessed_as_not_run()
        finished_perf = self._monotonic()
        status = results.run.status
        if status is RunStatus.FAILED:
            executed = engine_errors = generated = unsupported = 0
            not_run = len(self._telemetry_checks)
        else:
            executed = sum(
                check.verdict in {Verdict.PASS, Verdict.FAIL, Verdict.WARN}
                for check in results.checks
            )
            engine_errors = sum(check.verdict is Verdict.ERRORED for check in results.checks)
            generated = sum(check.skip_reason is SkipReason.GENERATED for check in results.checks)
            unsupported = sum(
                check.skip_reason is SkipReason.UNSUPPORTED for check in results.checks
            )
            not_run = sum(check.skip_reason is SkipReason.NOT_RUN for check in results.checks)
        run_error_code = (
            safe_error_code(results.run.error.code)
            if status is RunStatus.FAILED and results.run.error is not None
            else None
        )
        partial_codes = list(self._telemetry_partial_codes)
        if status is RunStatus.PARTIAL and not partial_codes and not_run == 0:
            partial_codes.append(PartialReasonCode.UNKNOWN)
        self._telemetry.emit(
            RunFinished,
            outcome=status.value,
            duration_ms=_duration_ms(started_perf, finished_perf),
            selected_check_count=len(self._telemetry_checks),
            executed_check_count=executed,
            engine_error_count=engine_errors,
            skipped_generated_count=generated,
            skipped_unsupported_count=unsupported,
            skipped_not_run_count=not_run,
            query_count=len(self._telemetry_query_durations),
            query_total_ms=sum(self._telemetry_query_durations),
            query_max_ms=(
                max(self._telemetry_query_durations) if self._telemetry_query_durations else None
            ),
            probe_ms=self._telemetry_probe_ms,
            budget_remaining_ms=(
                None
                if self._telemetry_deadline is None
                else max(0, round((self._telemetry_deadline - finished_perf) * 1000))
            ),
            early_stopped=not_run > 0,
            deadline_exhausted=PartialReasonCode.DEADLINE_EXHAUSTED
            in self._telemetry_partial_codes,
            partial_reason_codes=tuple(partial_codes) if status is RunStatus.PARTIAL else (),
            run_error_code=run_error_code
            or (SafeErrorCode.UNKNOWN if status is RunStatus.FAILED else None),
        )

    def _add_partial_code(self, code: PartialReasonCode) -> None:
        if code not in self._telemetry_partial_codes:
            self._telemetry_partial_codes.append(code)

    def _timing_start(self) -> float | None:
        return (
            self._monotonic() if self._telemetry is not None and self._telemetry.enabled else None
        )

    def _timing_finish(self, started: float | None) -> int | None:
        if started is None or self._telemetry is None or not self._telemetry.enabled:
            return None
        return _duration_ms(started, self._monotonic())

    def _probe_target(self, deadline: float) -> ResultsTarget:
        probe = getattr(self.client, "probe", None)
        if not callable(probe):
            raise GraphCheckError(
                "engine.target_missing",
                "No RunTarget was supplied and the connector cannot probe one.",
                "Pass `target=` or use the Neo4jClient from the C2 connector.",
            )
        timeout_s = _remaining(deadline, self._monotonic())
        result = probe(timeout_s=timeout_s) if self._probe_accepts_timeout else probe()
        _remaining(deadline, self._monotonic())
        target = result[0] if isinstance(result, tuple) else result
        validated = ResultsTarget.model_validate(
            target.model_dump() if isinstance(target, RunTarget) else target
        )
        if isinstance(result, tuple) and len(result) > 2:
            counts = result[2]
            validated = validated.model_copy(
                update={
                    "nodes": getattr(counts, "nodes", validated.nodes),
                    "relationships": getattr(counts, "relationships", validated.relationships),
                }
            )
        return validated

    def _results(
        self,
        *,
        run_id: str,
        started_at: str,
        finished_at: str,
        status: RunStatus,
        partial_reason: str | None,
        suite_ids: list[str],
        target: RunTarget,
        inputs: Sequence[SuiteInput],
        checks: list[CheckResult],
        tags: list[str],
        fail_fast: bool,
    ) -> Results:
        score = calculate_score(checks)
        suite_scores = calculate_suite_scores(checks)
        run_totals = totals(checks)
        suites = []
        for suite_input in inputs:
            members = [c for c in checks if c.suite_id == suite_input.suite.suite]
            suites.append(
                {
                    "id": suite_input.suite.suite,
                    "source_sha": suite_input.source_sha,
                    "score": (
                        None
                        if suite_input.suite.suite not in suite_scores
                        else suite_scores[suite_input.suite.suite].value
                    ),
                    "totals": totals(members),
                }
            )
        return Results(
            schema_version=SCHEMA_VERSION,
            run={
                "id": run_id,
                "started_at": started_at,
                "finished_at": finished_at,
                "graphcheck_version": __version__,
                "pack_version": PACK_VERSION,
                "status": status,
                "partial_reason": partial_reason,
                "exit_code": exit_code(status, checks),
                "selection": {
                    "suites": suite_ids,
                    "tags": tags,
                    "fail_fast": fail_fast,
                },
                "redaction": {"policy": RedactionPolicy.NONE, "applied": False},
                "target": target,
                "error": None,
            },
            score=(
                None
                if score.value is None
                else {
                    "value": score.value,
                    "method": "weighted-by-severity",
                    "weights": {severity.value: weight for severity, weight in WEIGHTS.items()},
                }
            ),
            totals=run_totals,
            suites=suites,
            checks=checks,
        )

    def _failed_run(
        self,
        run_id: str,
        started_at: str,
        suite_ids: list[str],
        error: CheckError,
        *,
        tags: Sequence[str] = (),
        fail_fast: bool = False,
    ) -> Results:
        status = RunStatus.FAILED
        results = Results(
            schema_version=SCHEMA_VERSION,
            run={
                "id": run_id,
                "started_at": started_at,
                "finished_at": _timestamp(self._clock()),
                "graphcheck_version": __version__,
                "pack_version": PACK_VERSION,
                "status": status,
                "partial_reason": None,
                "exit_code": exit_code(status, []),
                "selection": {
                    "suites": suite_ids,
                    "tags": list(tags),
                    "fail_fast": fail_fast,
                },
                "redaction": {"policy": RedactionPolicy.NONE, "applied": False},
                "target": None,
                "error": error,
            },
            score=None,
            totals=totals([]),
            suites=[],
            checks=[],
        )
        if self._telemetry_started_perf is not None:
            self._telemetry_stage = EngineStage.FINALIZE
            self._emit_run_finished(results, self._telemetry_started_perf)
        return results


def failed_results(
    error: CheckError,
    *,
    suite_ids: Sequence[str] = (),
    tags: Sequence[str] = (),
    fail_fast: bool = False,
) -> Results:
    """Build a frozen failed-run artifact for failures before C1 can start."""

    now = _timestamp(datetime.now(UTC))
    status = RunStatus.FAILED
    return Results(
        schema_version=SCHEMA_VERSION,
        run={
            "id": str(uuid.uuid4()),
            "started_at": now,
            "finished_at": now,
            "graphcheck_version": __version__,
            "pack_version": PACK_VERSION,
            "status": status,
            "partial_reason": None,
            "exit_code": exit_code(status, []),
            "selection": {
                "suites": list(suite_ids),
                "tags": list(tags),
                "fail_fast": fail_fast,
            },
            "redaction": {"policy": RedactionPolicy.NONE, "applied": False},
            "target": None,
            "error": error,
        },
        score=None,
        totals=totals([]),
        suites=[],
        checks=[],
    )


def run_suite(
    suite: LoadedSuite,
    *,
    client: object,
    source_text: str | None = None,
    source_sha: str | None = None,
    target: RunTarget | None = None,
    baselines: BaselineProvider | Mapping[str, object] | None = None,
    config: EngineConfig | None = None,
    tags: Sequence[str] = (),
    fail_fast: bool = False,
) -> Results:
    return Engine(client, baselines=baselines, config=config).run_suite(
        suite,
        source_text=source_text,
        source_sha=source_sha,
        target=target,
        tags=tags,
        fail_fast=fail_fast,
    )


def run_suite_yaml(
    text: str,
    *,
    client: object,
    source: str | None = None,
    target: RunTarget | None = None,
    baselines: BaselineProvider | Mapping[str, object] | None = None,
    config: EngineConfig | None = None,
    tags: Sequence[str] = (),
    fail_fast: bool = False,
) -> Results:
    return Engine(client, baselines=baselines, config=config).run_yaml(
        text,
        source=source,
        target=target,
        tags=tags,
        fail_fast=fail_fast,
    )


def _telemetry_template(check) -> object:
    if isinstance(check.spec, ConformanceCheck):
        return safe_template(check.spec.check)
    return safe_template(check.pattern.value)


def _check_may_sample(check) -> bool:
    if not isinstance(check.spec, ConformanceCheck):
        return False
    try:
        definition = builtin_pack_catalog().checks.get(check.spec.check)
    except Exception:
        return False
    return bool(definition and definition.sampled)


def _read_guard_error_outcome(code: SafeErrorCode) -> ReadGuardOutcome:
    if code is SafeErrorCode.READ_GUARD_REJECTED:
        return ReadGuardOutcome.REJECTED
    return ReadGuardOutcome.ERROR


def _telemetry_outcome(exc: Exception, raw_code: str | None) -> EventOutcome:
    if raw_code == "engine.timeout" or isinstance(
        exc,
        (TimeoutError, GraphCheckTimeoutError),
    ):
        return EventOutcome.TIMEOUT
    return EventOutcome.ERROR


def _skipped_result(check, suite_id: str, reason: SkipReason) -> CheckResult:
    return CheckResult(
        id=check.id,
        suite_id=suite_id,
        pattern=check.pattern,
        name=name_for(check),
        provenance=check.provenance,
        severity=check.severity,
        verdict=Verdict.SKIPPED,
        skip_reason=reason,
        started_at=None,
        duration_ms=None,
        compiled_query=None,
        params=None,
        measured=None,
        expected=expected_for(check),
        estimate=False,
        evidence=None,
        error=None,
    )


def _is_hard_result(result: CheckResult) -> bool:
    return result.verdict is Verdict.FAIL or (
        result.verdict is Verdict.ERRORED and result.severity is Severity.ERROR
    )


def _canonical_suite_sha(suite: LoadedSuite) -> str:
    payload = json.dumps(
        suite.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _duration_ms(started: float, finished: float) -> int:
    return max(0, int(round(1000 * (finished - started))))


def _remaining(deadline: float, now: float) -> float:
    remaining = deadline - now
    if remaining <= 0:
        raise GraphCheckError(
            "engine.timeout",
            "The run time budget was exhausted while executing a check.",
            "Narrow the selection, enable sampling, or increase the external job budget.",
        )
    return remaining


def _evidence_required(compiled: CompiledCheck, rows: Sequence[Mapping[str, object]]) -> bool:
    condition = compiled.evidence_condition
    if condition is None:
        raise GraphCheckError(
            "engine.evidence_plan_invalid",
            f"Check {compiled.check.id!r} has an evidence query without a typed condition.",
            "Fix the compiler so conditional evidence is driven by an exact aggregate field.",
        )
    if len(rows) != 1:
        raise GraphCheckError(
            "engine.invalid_query_result",
            f"Check {compiled.check.id!r} returned {len(rows)} measurement rows, expected 1.",
            "Fix the measurement query so it returns one typed aggregate row.",
        )
    value = rows[0].get(condition.field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise GraphCheckError(
            "engine.invalid_query_result",
            f"Check {compiled.check.id!r} returned an invalid {condition.field!r} aggregate.",
            "Fix the measurement query so the evidence condition receives a finite number.",
        )
    return value > condition.value if condition.operator == "gt" else value < condition.value


def _merge_evidence(
    compiled: CompiledCheck,
    measurement: ExecutionResult,
    evidence: ExecutionResult,
) -> ExecutionResult:
    if len(evidence.rows) != 1 or "evidence" not in evidence.rows[0]:
        raise GraphCheckError(
            "engine.invalid_query_result",
            f"Check {compiled.check.id!r} returned an invalid bounded evidence result.",
            "Fix the evidence query so it returns one row containing `evidence`.",
        )
    rows = [{**measurement.rows[0], "evidence": evidence.rows[0]["evidence"]}]
    columns = tuple(dict.fromkeys((*measurement.columns, "evidence")))
    read_guard_values = [
        value for value in (measurement.read_guard_ms, evidence.read_guard_ms) if value is not None
    ]
    return replace(
        measurement,
        rows=rows,
        columns=columns,
        notification_count=(
            None
            if measurement.notification_count is None and evidence.notification_count is None
            else (measurement.notification_count or 0) + (evidence.notification_count or 0)
        ),
        read_guard_ms=sum(read_guard_values) if read_guard_values else None,
    )


def _unexpected_error(stage: str, exc: Exception) -> CheckError:
    return CheckError(
        code="engine.internal_error",
        message=f"Unexpected error during {stage}: {type(exc).__name__}: {exc}",
        fix="Run the same check with debug logging and report this engine error.",
    )


def _append_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _accepts_timeout(method: object) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "timeout_s" or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
