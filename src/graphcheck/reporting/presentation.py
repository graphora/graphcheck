"""Pure human-facing projections of validated run results."""

from __future__ import annotations

from dataclasses import dataclass

from graphcheck.contracts.results import Results, RunStatus, Verdict


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


def present_results(results: Results) -> ResultPresentation:
    """Project one validated Results object into deterministic outcome language."""

    totals = results.totals
    selected = totals.checks
    evaluated = selected - totals.skipped
    findings = totals.fail + totals.warn
    skipped_suites = tuple(
        sorted({check.suite_id for check in results.checks if check.verdict is Verdict.SKIPPED})
    )
    fully_clean = (
        results.run.status is RunStatus.COMPLETE
        and selected > 0
        and evaluated == selected
        and totals.passed == selected
    )
    incomplete = (
        results.run.status is not RunStatus.COMPLETE or evaluated < selected or totals.errored > 0
    )

    if results.run.status is RunStatus.FAILED:
        sentence, coverage_incomplete = "Run failed before checks could complete.", False
    elif selected == 0:
        sentence, coverage_incomplete = "No checks were selected or evaluated.", False
    elif evaluated == 0:
        sentence, coverage_incomplete = "No checks were evaluated.", False
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
