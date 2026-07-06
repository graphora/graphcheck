from __future__ import annotations

from collections import Counter
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    ERRORED = "errored"
    SKIPPED = "skipped"


class Severity(str, Enum):
    ERROR = "error"
    WARN = "warn"


class Pattern(str, Enum):
    CONFORMANCE = "conformance"
    DRIFT = "drift"
    COMPETENCY_SHAPE = "competency-shape"
    COMPETENCY_REGRESSION = "competency-regression"


class SkipReason(str, Enum):
    GENERATED = "generated"
    UNSUPPORTED = "unsupported"
    NOT_RUN = "not_run"


class RunStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class RedactionPolicy(str, Enum):
    NONE = "none"
    MASK = "mask"
    HASH = "hash"


WEIGHTS: dict[Severity, int] = {Severity.ERROR: 3, Severity.WARN: 1}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceElement(_Strict):
    kind: Literal["node", "rel"]
    id: str
    labels: list[str] | None = None
    type: str | None = None


class Evidence(_Strict):
    message: str
    elements: list[EvidenceElement]
    truncated: bool
    cap: int
    total_count: int


class Estimate(_Strict):
    sample_size: int
    population: int
    confidence: float
    ci: tuple[float, float] | None = None


class CheckError(_Strict):
    code: str
    message: str
    fix: str


class CheckResult(_Strict):
    # SPEC-01 freezes the shape: every key is present. The nullable/false keys carry
    # null/false when unused (no defaults), so a producer that omits one fails validation.
    id: str
    suite_id: str
    pattern: Pattern
    name: str
    provenance: str | None
    severity: Severity
    verdict: Verdict
    skip_reason: SkipReason | None
    started_at: str | None
    duration_ms: int | None
    compiled_query: str | None
    params: dict[str, object] | None
    measured: dict[str, object] | None
    expected: dict[str, object]
    estimate: Estimate | Literal[False]
    evidence: Evidence | None
    error: CheckError | None

    @property
    def executed(self) -> bool:
        return self.verdict is not Verdict.SKIPPED

    @model_validator(mode="after")
    def _field_presence(self) -> CheckResult:
        v = self.verdict
        run_outcomes = (Verdict.PASS, Verdict.FAIL, Verdict.WARN)

        # An assertion failure encodes its severity in the verdict (SPEC-01 rule 1): a fail is an
        # error-severity failure, a warn is a warn-severity failure. Reject mismatches — otherwise
        # exit_code could downgrade (e.g. severity:error + verdict:warn would exit 2, not 1).
        if v is Verdict.FAIL and self.severity is not Severity.ERROR:
            raise ValueError(f"fail check {self.id!r} must have severity:error")
        if v is Verdict.WARN and self.severity is not Severity.WARN:
            raise ValueError(f"warn check {self.id!r} must have severity:warn")

        # Only an attempted, measured check can be sampled; errored/skipped are never estimates.
        if v not in run_outcomes and self.estimate is not False:
            raise ValueError(f"{v.value} check {self.id!r} must have estimate=false")

        if v in (Verdict.FAIL, Verdict.WARN):
            if self.evidence is None:
                raise ValueError(f"{v.value} check {self.id!r} must carry evidence")
        elif self.evidence is not None:
            raise ValueError(f"{v.value} check {self.id!r} must not carry evidence")

        if v is Verdict.ERRORED:
            if self.error is None:
                raise ValueError(f"errored check {self.id!r} must carry error")
        elif self.error is not None:
            raise ValueError(f"non-errored check {self.id!r} must not carry error")

        if v is Verdict.SKIPPED:
            if self.skip_reason is None:
                raise ValueError(f"skipped check {self.id!r} must carry skip_reason")
            for field in ("started_at", "duration_ms", "compiled_query", "params", "measured"):
                if getattr(self, field) is not None:
                    raise ValueError(f"skipped check {self.id!r} must have null {field}")
            return self  # nothing executed; the checks below are for attempted checks

        if self.skip_reason is not None:
            raise ValueError(f"non-skipped check {self.id!r} must not carry skip_reason")

        # Every attempted check (pass/fail/warn/errored) has timing.
        if self.started_at is None or self.duration_ms is None:
            raise ValueError(f"attempted check {self.id!r} must carry started_at and duration_ms")

        if v in run_outcomes:
            for field in ("compiled_query", "params", "measured"):
                if getattr(self, field) is None:
                    raise ValueError(f"{v.value} check {self.id!r} must carry {field}")
        elif self.measured is not None:  # v is ERRORED here
            raise ValueError(
                f"errored check {self.id!r} must not carry measured (it did not measure)"
            )
        return self


def score_value(checks: list[CheckResult]) -> int | None:
    denominator = sum(WEIGHTS[c.severity] for c in checks if c.executed)
    if denominator == 0:
        return None
    numerator = sum(WEIGHTS[c.severity] for c in checks if c.verdict is Verdict.PASS)
    return round(100 * numerator / denominator)


def totals(checks: list[CheckResult]) -> dict[str, int]:
    counts = Counter(c.verdict for c in checks)
    return {
        "checks": len(checks),
        "pass": counts[Verdict.PASS],
        "fail": counts[Verdict.FAIL],
        "warn": counts[Verdict.WARN],
        "errored": counts[Verdict.ERRORED],
        "skipped": counts[Verdict.SKIPPED],
    }


def exit_code(status: RunStatus, checks: list[CheckResult]) -> int:
    if status is RunStatus.FAILED:
        return 3
    hard = any(
        c.verdict is Verdict.FAIL or (c.verdict is Verdict.ERRORED and c.severity is Severity.ERROR)
        for c in checks
    )
    if hard:
        return 1
    nothing_evaluated = not any(c.executed for c in checks)
    soft = any(
        c.verdict is Verdict.WARN or (c.verdict is Verdict.ERRORED and c.severity is Severity.WARN)
        for c in checks
    )
    if status is RunStatus.PARTIAL or nothing_evaluated or soft:
        return 2
    return 0


class Totals(BaseModel):
    # No populate_by_name: the external key is the frozen `pass` alias only, never `passed`.
    model_config = ConfigDict(extra="forbid")
    checks: int
    passed: int = Field(alias="pass")
    fail: int
    warn: int
    errored: int
    skipped: int


class Score(_Strict):
    value: int
    method: Literal["weighted-by-severity"] = "weighted-by-severity"
    weights: dict[str, int] = Field(default_factory=lambda: {"error": 3, "warn": 1})

    @model_validator(mode="after")
    def _weights_locked(self) -> Score:
        if self.weights != {"error": 3, "warn": 1}:
            raise ValueError("score.weights are hard-coded in v0: {'error': 3, 'warn': 1}")
        return self


class Capabilities(_Strict):
    apoc: bool
    count_store: bool


class RunTarget(_Strict):
    database: str
    server_version: str
    edition: str
    fingerprint: str
    capabilities: Capabilities


class Selection(_Strict):
    suites: list[str]
    tags: list[str]
    fail_fast: bool


class Redaction(_Strict):
    policy: RedactionPolicy
    applied: bool


class Run(_Strict):
    id: str
    started_at: str
    finished_at: str
    graphcheck_version: str
    pack_version: str
    status: RunStatus
    partial_reason: str | None  # present-but-nullable; non-null iff status is partial
    exit_code: int
    selection: Selection
    redaction: Redaction
    target: RunTarget | None  # present-but-nullable; null only for failed runs
    error: CheckError | None  # present-but-nullable; non-null only for failed runs


class Suite(_Strict):
    id: str
    source_sha: str
    score: int | None
    totals: Totals


class Results(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    run: Run
    score: Score | None
    totals: Totals
    suites: list[Suite]
    checks: list[CheckResult]

    @model_validator(mode="after")
    def _consistency(self) -> Results:
        status = self.run.status
        if status is RunStatus.FAILED:
            if self.run.error is None:
                raise ValueError("failed run must carry run.error")
            if self.checks or self.suites or self.score is not None:
                raise ValueError("failed run must have empty checks/suites and null score")
        elif self.run.error is not None:
            raise ValueError("non-failed run must not carry run.error")

        if status in (RunStatus.COMPLETE, RunStatus.PARTIAL) and self.run.target is None:
            raise ValueError("complete/partial run must carry run.target")

        if (self.run.partial_reason is not None) != (status is RunStatus.PARTIAL):
            raise ValueError("partial_reason must be non-null iff status is partial")

        expected_totals = totals(self.checks)
        if self.totals.model_dump(by_alias=True) != expected_totals:
            raise ValueError(f"totals must equal the tally of checks: {expected_totals}")

        expected_score = score_value(self.checks)
        if expected_score is None:
            if self.score is not None:
                raise ValueError("score must be null when no check executed")
        elif self.score is None or self.score.value != expected_score:
            raise ValueError(f"score.value must be {expected_score}")

        expected_exit = exit_code(status, self.checks)
        if self.run.exit_code != expected_exit:
            raise ValueError(f"exit_code must be {expected_exit}")

        has_gap = any(
            c.skip_reason in (SkipReason.UNSUPPORTED, SkipReason.NOT_RUN) for c in self.checks
        )
        if has_gap and status is not RunStatus.PARTIAL:
            raise ValueError("an unsupported/not_run skip requires run.status:partial")

        identities = [(c.suite_id, c.id) for c in self.checks]
        if len(identities) != len(set(identities)):
            raise ValueError("check identity (suite_id, id) must be unique across checks[]")
        if len({s.id for s in self.suites}) != len(self.suites):
            raise ValueError("suite ids must be unique in suites[]")

        by_suite: dict[str, list[CheckResult]] = {}
        for c in self.checks:
            by_suite.setdefault(c.suite_id, []).append(c)
        suite_ids = {s.id for s in self.suites}
        for suite_id in by_suite:
            if suite_id not in suite_ids:
                raise ValueError(f"check suite_id {suite_id!r} has no matching suites[] entry")
        for suite in self.suites:
            members = by_suite.get(suite.id, [])
            if suite.score != score_value(members):
                raise ValueError(f"suite {suite.id!r} score is inconsistent with its checks")
            if suite.totals.model_dump(by_alias=True) != totals(members):
                raise ValueError(f"suite {suite.id!r} totals are inconsistent with its checks")
        return self
