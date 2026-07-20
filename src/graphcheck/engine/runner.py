from __future__ import annotations

import hashlib
import inspect
import json
import math
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
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
    RunStatus,
    RunTarget,
    Severity,
    SkipReason,
    Verdict,
    exit_code,
    score_value,
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
from graphcheck.engine.evaluator import Evaluation, VerdictEvaluator
from graphcheck.engine.executor import ReadOnlyExecutor
from graphcheck.engine.parameters import (
    GraphTokenResolver,
    ParameterTokenResolver,
    resolve_parameters,
)
from graphcheck.engine.sampling import SamplingPolicy
from graphcheck.errors import GraphCheckError
from graphcheck.packs import PACK_VERSION


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
        run_id = str(self._id_factory())
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

        try:
            resolved_target = target or self._probe_target(deadline)
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

        check_results: list[CheckResult] = []
        partial_reasons: list[str] = list(dict.fromkeys(_initial_partial_reasons))
        fail_fast_after: str | None = None
        for suite_input in inputs:
            for check in suite_input.suite.checks:
                if fail_fast_after is not None:
                    check_results.append(
                        _skipped_result(
                            check,
                            suite_input.suite.suite,
                            SkipReason.NOT_RUN,
                        )
                    )
                    _append_once(
                        partial_reasons,
                        f"fail-fast stopped the run after {fail_fast_after}",
                    )
                    continue
                if check.generated:
                    check_results.append(
                        _skipped_result(
                            check,
                            suite_input.suite.suite,
                            SkipReason.GENERATED,
                        )
                    )
                    continue
                capability_check = getattr(self.compiler, "missing_capabilities", None)
                missing_capabilities = (
                    tuple(capability_check(check, resolved_target))
                    if callable(capability_check)
                    else ()
                )
                if missing_capabilities:
                    check_results.append(
                        _skipped_result(
                            check,
                            suite_input.suite.suite,
                            SkipReason.UNSUPPORTED,
                        )
                    )
                    rendered = ", ".join(missing_capabilities)
                    _append_once(
                        partial_reasons,
                        f"check {suite_input.suite.suite}/{check.id} requires "
                        f"missing capability: {rendered}",
                    )
                    continue
                if self._monotonic() >= deadline:
                    check_results.append(
                        _skipped_result(
                            check,
                            suite_input.suite.suite,
                            SkipReason.NOT_RUN,
                        )
                    )
                    _append_once(
                        partial_reasons,
                        f"the {self.config.time_budget_s:g}-second run budget was exhausted",
                    )
                    continue
                result, partial_reason = self._run_check(
                    check,
                    suite_id=suite_input.suite.suite,
                    suite_sha=suite_input.source_sha,
                    target=resolved_target,
                    deadline=deadline,
                )
                check_results.append(result)
                if partial_reason is not None:
                    _append_once(partial_reasons, partial_reason)
                if fail_fast and _is_hard_result(result):
                    fail_fast_after = f"{suite_input.suite.suite}/{check.id}"
                if self._monotonic() >= deadline:
                    _append_once(
                        partial_reasons,
                        f"the {self.config.time_budget_s:g}-second run budget was exhausted",
                    )

        status = RunStatus.PARTIAL if partial_reasons else RunStatus.COMPLETE
        partial_reason = "; ".join(partial_reasons) if partial_reasons else None
        return self._results(
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

    def _run_check(
        self,
        check,
        *,
        suite_id: str,
        suite_sha: str,
        target: RunTarget,
        deadline: float,
    ) -> tuple[CheckResult, str | None]:
        check_started_at = _timestamp(self._clock())
        check_started_perf = self._monotonic()
        compiled: CompiledCheck | None = None
        resolved_params: dict[str, object] | None = None
        baseline: BaselineValue | None = None
        partial_reason: str | None = None
        try:
            sample_seed = self.config.sampling.check_seed(
                graph_fingerprint=target.fingerprint,
                suite_sha=suite_sha,
                check_id=check.id,
            )
            compiled = self.compiler.compile(check, sample_seed=sample_seed)
            if isinstance(check.spec, CompetencyCheck):
                resolved_params = resolve_parameters(
                    compiled.params,
                    self.client,
                    resolver=self.parameter_resolver,
                    timeout_factory=lambda: _remaining(deadline, self._monotonic()),
                )
            else:
                resolved_params = dict(compiled.params)
            if compiled.sampled:
                compiled, resolved_params = self._apply_sampling(
                    compiled,
                    resolved_params,
                    check=check,
                    suite_sha=suite_sha,
                    target=target,
                    deadline=deadline,
                )
            if isinstance(check.spec, DriftCheck):
                baseline = require_baseline(
                    self.baselines,
                    check.spec.baseline,
                    check.spec.metric,
                    check.spec.target,
                )
                if baseline.partial:
                    partial_reason = (
                        f"check {suite_id}/{check.id} used partial baseline {check.spec.baseline!r}"
                    )
            execution = self.executor.execute(
                compiled.query,
                resolved_params,
                timeout_s=_remaining(deadline, self._monotonic()),
            )
            evaluation = self.evaluator.evaluate(
                # Evidence extraction must see executed literals, never unresolved graph tokens.
                replace(compiled, params=resolved_params),
                execution.rows,
                columns=execution.columns,
                baseline=baseline,
            )
            if not isinstance(evaluation, Evaluation):
                raise GraphCheckError(
                    "engine.invalid_evaluation",
                    f"Evaluator returned {type(evaluation).__name__}, not an Evaluation.",
                    "Fix the evaluator implementation to return the frozen C1 Evaluation shape.",
                )
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
            return result, partial_reason
        except GraphCheckError as exc:
            error = exc.error
            if error.code == "engine.baseline_partial_missing":
                partial_reason = (
                    f"check {suite_id}/{check.id} used a partial baseline missing its measurement"
                )
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
        )

    def _apply_sampling(
        self,
        compiled: CompiledCheck,
        params: dict[str, object],
        *,
        check,
        suite_sha: str,
        target: RunTarget,
        deadline: float,
    ) -> tuple[CompiledCheck, dict[str, object]]:
        if compiled.population_query is None:
            raise GraphCheckError(
                "engine.sampling_invalid",
                f"Sampled check {check.id!r} has no population query.",
                "Fix the pack compiler to provide a deterministic population query.",
            )
        population_execution = self.executor.execute(
            compiled.population_query,
            compiled.population_params or {},
            timeout_s=_remaining(deadline, self._monotonic()),
        )
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
        requested = (
            check.spec.with_.get("sample_size")
            if isinstance(check.spec, ConformanceCheck)
            else None
        )
        if requested is None:
            requested = compiled.params.get("sample_size")
        sample_size = (
            decision.sample_size if requested is None else min(decision.sample_size, int(requested))
        )
        resolved = {**params, "sample_size": sample_size}
        compiled_params = {**compiled.params, "sample_size": sample_size}
        return (
            replace(
                compiled,
                params=compiled_params,
                expected={**compiled.expected, "sample_size": sample_size},
                sample_population=population,
            ),
            resolved,
        )

    def _probe_target(self, deadline: float) -> RunTarget:
        probe = getattr(self.client, "probe", None)
        if not callable(probe):
            raise GraphCheckError(
                "engine.target_missing",
                "No RunTarget was supplied and the connector cannot probe one.",
                "Pass `target=` or use the Neo4jClient from the C2 connector.",
            )
        timeout_s = _remaining(deadline, self._monotonic())
        result = probe(timeout_s=timeout_s) if _accepts_timeout(probe) else probe()
        _remaining(deadline, self._monotonic())
        target = result[0] if isinstance(result, tuple) else result
        return RunTarget.model_validate(target)

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
        score = score_value(checks)
        run_totals = totals(checks)
        suites = []
        for suite_input in inputs:
            members = [c for c in checks if c.suite_id == suite_input.suite.suite]
            suites.append(
                {
                    "id": suite_input.suite.suite,
                    "source_sha": suite_input.source_sha,
                    "score": score_value(members),
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
                if score is None
                else {
                    "value": score,
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
        return Results(
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
