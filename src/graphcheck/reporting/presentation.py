"""Pure human-facing projections of validated run results."""

from __future__ import annotations

from dataclasses import dataclass

from graphcheck.contracts.results import CheckResult, CoverageStatus, Results, SkipReason, Verdict
from graphcheck.reporting.history import calculate_coverage_status


@dataclass(frozen=True, slots=True)
class SkipReasonPresentation:
    code: str
    label: str
    explanation: str


@dataclass(frozen=True, slots=True)
class CheckPresentation:
    verdict: str
    verdict_label: str
    evaluated: bool
    evaluation_label: str
    skip_reason: SkipReasonPresentation | None


_VERDICT_LABELS = {
    Verdict.PASS: "Pass",
    Verdict.FAIL: "Fail",
    Verdict.WARN: "Warn",
    Verdict.ERRORED: "Errored",
    Verdict.SKIPPED: "Skipped",
}
_SKIP_REASONS = {
    SkipReason.GENERATED: ("Generated", "Generated check awaiting review or approval."),
    SkipReason.UNSUPPORTED: (
        "Unsupported",
        "A capability required by this check was unavailable.",
    ),
    SkipReason.NOT_RUN: ("Not run", "The run ended before this check started."),
}


@dataclass(frozen=True, slots=True)
class ResultPresentation:
    selected: int
    evaluated: int
    findings: int
    execution_errors: int
    fully_clean: bool
    primary_sentence: str
    coverage_incomplete: bool
    skipped_suites: tuple[str, ...]

    @property
    def not_evaluated(self) -> int:
        return self.selected - self.evaluated

    @property
    def coverage_sentence(self) -> str:
        suffix = f" · {self.not_evaluated} not evaluated" if self.not_evaluated else ""
        return f"{self.evaluated}/{self.selected} selected checks evaluated{suffix}"

    @property
    def result_sentence(self) -> str:
        if not self.coverage_incomplete:
            return self.primary_sentence
        if not self.skipped_suites:
            return f"{self.primary_sentence} Coverage is incomplete."
        suites = ", ".join(self.skipped_suites)
        return (
            f"{self.primary_sentence} Coverage is incomplete due to skipped check(s) from {suites}."
        )


def present_check(check: CheckResult) -> CheckPresentation:
    """Project a validated check into shared verdict and evaluation language."""

    skip_reason = None
    if check.skip_reason is not None:
        label, explanation = _SKIP_REASONS[check.skip_reason]
        skip_reason = SkipReasonPresentation(check.skip_reason.value, label, explanation)
    return CheckPresentation(
        verdict=check.verdict.value,
        verdict_label=_VERDICT_LABELS[check.verdict],
        evaluated=check.executed,
        evaluation_label="Evaluated" if check.executed else "Not evaluated",
        skip_reason=skip_reason,
    )


def present_results(results: Results) -> ResultPresentation:
    """Project one validated Results object into deterministic outcome language."""

    totals = results.totals
    selected = totals.checks
    evaluated = selected - totals.skipped
    findings = totals.fail + totals.warn
    skipped_suites = tuple(
        sorted({check.suite_id for check in results.checks if check.verdict is Verdict.SKIPPED})
    )
    coverage_status = calculate_coverage_status(results)
    fully_clean = (
        coverage_status is CoverageStatus.COMPLETE
        and selected > 0
        and evaluated == selected
        and totals.passed == selected
    )
    incomplete = coverage_status is CoverageStatus.PARTIAL

    if coverage_status is CoverageStatus.FAILED:
        sentence, coverage_incomplete = "Run failed before checks could complete.", False
    elif selected == 0:
        sentence, coverage_incomplete = "No checks were selected or evaluated.", False
    elif evaluated == 0:
        sentence, coverage_incomplete = "No checks were evaluated.", incomplete
    elif findings or totals.errored:
        sentence = f"{_outcome_counts(totals.fail, totals.warn, totals.errored)}."
        coverage_incomplete = incomplete
    elif incomplete:
        noun = "check" if evaluated == 1 else "checks"
        sentence = f"No failures in the {evaluated} {noun} evaluated."
        coverage_incomplete = True
    elif fully_clean:
        noun = "check" if selected == 1 else "checks"
        sentence = f"No failures. All {selected} selected {noun} passed."
        coverage_incomplete = False
    else:  # pragma: no cover - Results validation makes this state unreachable.
        raise ValueError("validated results do not map to a presentation state")

    return ResultPresentation(
        selected=selected,
        evaluated=evaluated,
        findings=findings,
        execution_errors=totals.errored,
        fully_clean=fully_clean,
        primary_sentence=sentence,
        coverage_incomplete=coverage_incomplete,
        skipped_suites=skipped_suites,
    )


def _outcome_counts(failures: int, warnings: int, errors: int) -> str:
    parts = [
        _count_label(failures, "failure", "failures"),
        _count_label(warnings, "warning", "warnings"),
        _count_label(errors, "execution error", "execution errors"),
    ]
    present = [part for part in parts if part]
    if len(present) < 3:
        return " and ".join(present)
    return f"{', '.join(present[:-1])}, and {present[-1]}"


def _count_label(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}" if count else ""
