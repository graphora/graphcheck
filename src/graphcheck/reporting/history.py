from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from graphcheck.contracts.results import CheckResult, Results, Severity, Verdict
from graphcheck.reporting.writer import load_results


class ReportHistoryError(ValueError):
    """Raised when report artifacts cannot satisfy a history operation."""


@dataclass(frozen=True)
class ReportRun:
    directory: Path
    results_path: Path
    report_path: Path
    results: Results
    modified_ns: int

    @property
    def id(self) -> str:
        return self.results.run.id


def discover_report_runs(runs_dir: Path) -> list[ReportRun]:
    """Load and de-duplicate validated run artifacts, newest first."""
    if not runs_dir.is_dir():
        return []

    by_id: dict[str, ReportRun] = {}
    for results_path in runs_dir.rglob("results.json"):
        record = _load_report_run(results_path)
        current = by_id.get(record.id)
        if current is None or _preferred_record(record) > _preferred_record(current):
            by_id[record.id] = record

    return sorted(by_id.values(), key=_recency, reverse=True)


def find_report_run(records: list[ReportRun], run_id: str) -> ReportRun:
    for record in records:
        if record.id == run_id or record.directory.name == run_id:
            return record
    raise ReportHistoryError(
        f"Run {run_id!r} was not found. Run `graphcheck report --list` to see available IDs."
    )


def format_report_history(records: list[ReportRun]) -> str:
    if not records:
        return "No report history found."

    rows = [
        (
            record.id,
            record.results.run.finished_at,
            record.results.run.status.value,
            _score(record.results),
        )
        for record in records
    ]
    headers = ("RUN ID", "FINISHED AT", "STATUS", "SCORE")
    widths = [max(len(headers[index]), *(len(row[index]) for row in rows)) for index in range(4)]
    lines = [
        _format_row(headers, widths),
        _format_row(tuple("-" * width for width in widths), widths),
    ]
    lines.extend(_format_row(row, widths) for row in rows)
    return "\n".join(lines)


def format_report_comparison(first: ReportRun, second: ReportRun) -> str:
    """Render outcome changes from the first report to the second report."""
    first_checks = {_identity(check): check for check in first.results.checks}
    second_checks = {_identity(check): check for check in second.results.checks}
    shared = sorted(first_checks.keys() & second_checks.keys())

    regressions: list[str] = []
    improvements: list[str] = []
    other_changes: list[str] = []
    for identity in shared:
        before = first_checks[identity]
        after = second_checks[identity]
        if before.verdict is after.verdict:
            continue
        change = f"{_display_identity(identity)}: {before.verdict.value} -> {after.verdict.value}"
        before_rank = _outcome_rank(before)
        after_rank = _outcome_rank(after)
        if after_rank > before_rank:
            regressions.append(change)
        elif after_rank < before_rank:
            improvements.append(change)
        else:
            other_changes.append(change)

    added = [
        f"{_display_identity(identity)}: {second_checks[identity].verdict.value}"
        for identity in sorted(second_checks.keys() - first_checks.keys())
    ]
    removed = [
        f"{_display_identity(identity)}: {first_checks[identity].verdict.value}"
        for identity in sorted(first_checks.keys() - second_checks.keys())
    ]

    lines = [
        f"Comparing {first.id} -> {second.id}",
        f"Status: {first.results.run.status.value} -> {second.results.run.status.value}",
        f"Score: {_score_change(first.results, second.results)}",
        "",
    ]
    _append_section(lines, "Regressions", regressions)
    _append_section(lines, "Improvements", improvements)
    _append_section(lines, "Other verdict changes", other_changes)
    _append_section(lines, "Added checks", added)
    _append_section(lines, "Removed checks", removed)
    return "\n".join(lines).rstrip()


def prune_report_runs(runs_dir: Path, keep: int) -> list[ReportRun]:
    """Remove old immediate run directories while always preserving ``latest``."""
    if keep < 1:
        raise ReportHistoryError("--keep must be at least 1.")
    if not runs_dir.is_dir():
        return []

    candidates: list[ReportRun] = []
    for directory in runs_dir.iterdir():
        if not directory.is_dir() or directory.name.casefold() == "latest":
            continue
        results_path = directory / "results.json"
        if results_path.is_file():
            candidates.append(_load_report_run(results_path))

    candidates.sort(key=_recency, reverse=True)
    removed = candidates[keep:]
    resolved_runs = runs_dir.resolve()
    resolved_directories: list[Path] = []
    for record in removed:
        if record.directory.is_symlink():
            raise ReportHistoryError(f"Refusing to prune linked path: {record.directory}")
        resolved_directory = record.directory.resolve()
        if resolved_directory.parent != resolved_runs:
            raise ReportHistoryError(f"Refusing to prune unexpected path: {record.directory}")
        resolved_directories.append(resolved_directory)
    for record, resolved_directory in zip(removed, resolved_directories, strict=True):
        try:
            shutil.rmtree(resolved_directory)
        except OSError as exc:
            raise ReportHistoryError(f"Could not prune {record.directory}: {exc}") from exc
    return removed


def _load_report_run(results_path: Path) -> ReportRun:
    try:
        results = load_results(results_path)
        modified_ns = results_path.stat().st_mtime_ns
    except (OSError, ValueError) as exc:
        raise ReportHistoryError(
            f"Could not read report history from {results_path}: {exc}"
        ) from exc
    return ReportRun(
        directory=results_path.parent,
        results_path=results_path,
        report_path=results_path.with_name("report.html"),
        results=results,
        modified_ns=modified_ns,
    )


def _preferred_record(record: ReportRun) -> tuple[bool, bool, int, str]:
    return (
        record.report_path.is_file(),
        record.directory.name.casefold() != "latest",
        record.modified_ns,
        str(record.directory),
    )


def _recency(record: ReportRun) -> tuple[str, int, str]:
    return (record.results.run.finished_at, record.modified_ns, record.id)


def _score(results: Results) -> str:
    return "n/a" if results.score is None else str(results.score.value)


def _score_change(first: Results, second: Results) -> str:
    before = None if first.score is None else first.score.value
    after = None if second.score is None else second.score.value
    if before is None or after is None:
        return f"{'n/a' if before is None else before} -> {'n/a' if after is None else after}"
    delta = after - before
    return f"{before} -> {after} ({delta:+d})"


def _format_row(values: tuple[str, ...], widths: list[int]) -> str:
    return "  ".join(value.ljust(widths[index]) for index, value in enumerate(values)).rstrip()


def _identity(check: CheckResult) -> tuple[str, str]:
    return (check.suite_id, check.id)


def _display_identity(identity: tuple[str, str]) -> str:
    return f"{identity[0]}::{identity[1]}"


def _outcome_rank(check: CheckResult) -> int:
    if check.verdict is Verdict.PASS:
        return 0
    if check.verdict is Verdict.SKIPPED:
        return 1
    if check.verdict is Verdict.WARN:
        return 2
    if check.verdict is Verdict.ERRORED:
        return 4 if check.severity is Severity.ERROR else 3
    return 4


def _append_section(lines: list[str], heading: str, changes: list[str]) -> None:
    lines.append(f"{heading} ({len(changes)}):")
    lines.extend(f"  {change}" for change in changes)
    if not changes:
        lines.append("  none")
    lines.append("")
