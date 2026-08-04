from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from graphcheck.contracts.results import (
    CheckResult,
    Results,
    RunStatus,
    Severity,
    Verdict,
    parse_utc_timestamp,
)
from graphcheck.reporting.writer import load_results

SUMMARY_FILENAME = "summary.json"


class ReportHistoryError(ValueError):
    """Raised when report artifacts cannot satisfy a history operation."""


@dataclass(frozen=True)
class ReportSummary:
    id: str
    finished_at: str
    status: RunStatus
    suite_scores: tuple[tuple[str, int | None], ...]


@dataclass(frozen=True, init=False)
class ReportRun:
    directory: Path
    results_path: Path
    report_path: Path
    summary: ReportSummary
    modified_ns: int
    _results: Results | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        directory: Path,
        results_path: Path,
        report_path: Path,
        results: Results | None = None,
        modified_ns: int = 0,
        *,
        summary: ReportSummary | None = None,
    ) -> None:
        if summary is None:
            if results is None:
                raise ValueError("ReportRun requires results or a summary")
            summary = report_summary(results)
        object.__setattr__(self, "directory", directory)
        object.__setattr__(self, "results_path", results_path)
        object.__setattr__(self, "report_path", report_path)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "modified_ns", modified_ns)
        object.__setattr__(self, "_results", results)

    @property
    def id(self) -> str:
        return self.summary.id

    @property
    def results(self) -> Results:
        if self._results is None:
            try:
                loaded = load_results(self.results_path)
                if report_summary(loaded) != self.summary:
                    raise ValueError("results.json does not match summary.json")
                object.__setattr__(self, "_results", loaded)
            except (OSError, ValueError) as exc:
                raise ReportHistoryError(
                    f"Could not read report history from {self.results_path}: {exc}"
                ) from exc
        assert self._results is not None
        return self._results


def discover_report_runs(runs_dir: Path) -> list[ReportRun]:
    """Discover compact run summaries and lazily load full selected artifacts."""
    if not runs_dir.is_dir():
        return []

    by_id: dict[str, ReportRun] = {}
    for record in _direct_report_runs(runs_dir):
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
            record.summary.finished_at,
            record.summary.status.value,
            _summary_suite_scores(record.summary),
        )
        for record in records
    ]
    headers = ("REPORT NAME", "FINISHED AT", "STATUS", "SUITE SCORES")
    widths = [max(len(headers[index]), *(len(row[index]) for row in rows)) for index in range(4)]
    lines = [
        _format_row(headers, widths),
        _format_row(tuple("-" * width for width in widths), widths),
    ]
    lines.extend(_format_row(row, widths) for row in rows)
    return "\n".join(lines)


def report_name(results: Results) -> str:
    """Return the filesystem-safe target and basic-ISO report identifier."""
    database = results.run.target.database if results.run.target is not None else "unknown"
    target = re.sub(r"[^A-Za-z0-9._-]+", "-", database).strip("._-") or "unknown"
    timestamp = parse_utc_timestamp(results.run.finished_at).strftime("%Y%m%dT%H%M%SZ")
    return f"{target}_{timestamp}"


def display_run_status(results: Results) -> RunStatus:
    """Map machine run outcomes to user-facing statuses."""
    if results.run.error is not None and results.run.error.code == "neo4j.unreachable":
        return RunStatus.FAILED
    return (
        RunStatus.PARTIAL
        if results.run.status is not RunStatus.COMPLETE or results.totals.errored > 0
        else RunStatus.COMPLETE
    )


def format_report_comparison(first: ReportRun, second: ReportRun) -> str:
    """Render suite-score and outcome changes from the first report to the second report."""
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
        f"Status: {display_run_status(first.results).value} -> "
        f"{display_run_status(second.results).value}",
        "Suite scores:",
        *_suite_score_changes(first.results, second.results),
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
        if (
            not directory.is_dir()
            or directory.name.casefold() == "latest"
            or directory.name.startswith(".")
        ):
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


def delete_report_runs(runs_dir: Path, run_ids: list[str]) -> list[ReportRun]:
    """Delete selected logical reports and safely repoint the ``latest`` alias."""
    requested = tuple(dict.fromkeys(run_id for run_id in run_ids if run_id))
    if not requested:
        raise ReportHistoryError("Select at least one report to delete.")

    records = discover_report_runs(runs_dir)
    by_id = {record.id: record for record in records}
    missing = [run_id for run_id in requested if run_id not in by_id]
    if missing:
        raise ReportHistoryError(f"Report {missing[0]!r} was not found.")

    selected_ids = set(requested)
    direct_records = _direct_report_runs(runs_dir)
    targets = [record.directory for record in direct_records if record.id in selected_ids]
    if not targets:
        raise ReportHistoryError("No selected report directories were found.")

    resolved_runs = runs_dir.resolve()
    _validate_removal_targets(resolved_runs, targets)
    trash = resolved_runs / f".delete-{uuid.uuid4().hex}"
    trash.mkdir()
    moved: list[tuple[Path, Path]] = []
    latest_staging: Path | None = None
    try:
        for target in targets:
            destination = trash / target.name
            target.replace(destination)
            moved.append((target, destination))
        remaining = discover_report_runs(resolved_runs)
        if remaining and not (resolved_runs / "latest").exists():
            latest_staging = _stage_latest_alias(resolved_runs, remaining[0])
            latest_staging.replace(resolved_runs / "latest")
            latest_staging = None
    except Exception as exc:
        if latest_staging is not None and latest_staging.exists():
            shutil.rmtree(latest_staging)
        latest = resolved_runs / "latest"
        if latest.exists() and any(target.name.casefold() == "latest" for target, _ in moved):
            shutil.rmtree(latest)
        for target, destination in reversed(moved):
            if destination.exists():
                destination.replace(target)
        if trash.exists():
            shutil.rmtree(trash)
        raise ReportHistoryError(f"Could not delete selected reports: {exc}") from exc

    try:
        shutil.rmtree(trash)
    except OSError as exc:
        raise ReportHistoryError(f"Could not finish deleting selected reports: {exc}") from exc
    return [by_id[run_id] for run_id in requested]


def _direct_report_runs(runs_dir: Path) -> list[ReportRun]:
    if not runs_dir.is_dir():
        return []
    resolved_runs = runs_dir.resolve()
    records: list[ReportRun] = []
    try:
        directories = sorted(runs_dir.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise ReportHistoryError(
            f"Could not enumerate report history in {runs_dir}: {exc}"
        ) from exc
    for directory in directories:
        if not _safe_report_directory(resolved_runs, directory):
            continue
        results_path = directory / "results.json"
        if (
            not results_path.is_file()
            or results_path.is_symlink()
            or results_path.resolve().parent != directory.resolve()
        ):
            continue
        summary_path = results_path.with_name(SUMMARY_FILENAME)
        record = (
            _load_summary_run(summary_path, results_path)
            if summary_path.is_file()
            and not summary_path.is_symlink()
            and summary_path.resolve().parent == directory.resolve()
            else _load_report_run(results_path)
        )
        records.append(record)
    return records


def _safe_report_directory(resolved_runs: Path, directory: Path) -> bool:
    is_junction = getattr(directory, "is_junction", lambda: False)
    return (
        directory.is_dir()
        and not directory.name.startswith(".")
        and not directory.is_symlink()
        and not is_junction()
        and directory.resolve().parent == resolved_runs
    )


def _validate_removal_targets(resolved_runs: Path, targets: list[Path]) -> None:
    for target in targets:
        if not _safe_report_directory(resolved_runs, target):
            raise ReportHistoryError(f"Refusing to delete unexpected path: {target}")


def _stage_latest_alias(resolved_runs: Path, source: ReportRun) -> Path:
    staging = resolved_runs / f".latest.staging-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        for filename in ("results.json", "report.html", SUMMARY_FILENAME):
            source_path = source.directory / filename
            if (
                source_path.is_file()
                and not source_path.is_symlink()
                and source_path.resolve().parent == source.directory.resolve()
            ):
                shutil.copy2(source_path, staging / filename)
    except Exception:
        shutil.rmtree(staging)
        raise
    return staging


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


def _load_summary_run(summary_path: Path, results_path: Path) -> ReportRun:
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        summary = _parse_summary(payload)
        modified_ns = results_path.stat().st_mtime_ns
    except (KeyError, OSError, TypeError, ValueError):
        return _load_report_run(results_path)
    return ReportRun(
        directory=results_path.parent,
        results_path=results_path,
        report_path=results_path.with_name("report.html"),
        summary=summary,
        modified_ns=modified_ns,
    )


def report_summary(results: Results) -> ReportSummary:
    return ReportSummary(
        id=results.run.id,
        finished_at=results.run.finished_at,
        status=display_run_status(results),
        suite_scores=tuple(
            (suite.id, suite.score) for suite in sorted(results.suites, key=lambda suite: suite.id)
        ),
    )


def report_summary_json(results: Results) -> str:
    summary = report_summary(results)
    return (
        json.dumps(
            {
                "schema_version": "1.0",
                "id": summary.id,
                "finished_at": summary.finished_at,
                "status": summary.status.value,
                "suite_scores": [
                    {"id": suite_id, "score": score} for suite_id, score in summary.suite_scores
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _parse_summary(payload: object) -> ReportSummary:
    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
        raise ValueError("invalid report summary schema")
    run_id = payload["id"]
    finished_at = payload["finished_at"]
    if not isinstance(run_id, str) or not isinstance(finished_at, str):
        raise ValueError("invalid report summary identity")
    parse_utc_timestamp(finished_at)
    raw_scores = payload["suite_scores"]
    if not isinstance(raw_scores, list):
        raise ValueError("invalid report summary suite scores")
    scores: list[tuple[str, int | None]] = []
    for item in raw_scores:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("invalid report summary suite score")
        score = item.get("score")
        if score is not None and (isinstance(score, bool) or not isinstance(score, int)):
            raise ValueError("invalid report summary score")
        scores.append((item["id"], score))
    return ReportSummary(
        id=run_id,
        finished_at=finished_at,
        status=RunStatus(payload["status"]),
        suite_scores=tuple(sorted(scores)),
    )


def _preferred_record(record: ReportRun) -> tuple[bool, bool, int, str]:
    return (
        _safe_artifact_file(record.directory, record.report_path),
        record.directory.name.casefold() != "latest",
        record.modified_ns,
        str(record.directory),
    )


def _safe_artifact_file(directory: Path, path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and path.resolve().parent == directory.resolve()


def _recency(record: ReportRun) -> tuple[datetime, int, str]:
    return (parse_utc_timestamp(record.summary.finished_at), record.modified_ns, record.id)


def _summary_suite_scores(summary: ReportSummary) -> str:
    if not summary.suite_scores:
        return "n/a"
    return ", ".join(
        f"{suite_id}={'n/a' if score is None else score}"
        for suite_id, score in summary.suite_scores
    )


def _suite_score_changes(first: Results, second: Results) -> list[str]:
    before = {suite.id: suite.score for suite in first.suites}
    after = {suite.id: suite.score for suite in second.suites}
    suite_ids = sorted(before.keys() | after.keys())
    if not suite_ids:
        return ["  none"]
    return [
        f"  {suite_id}: {_score_change(before.get(suite_id), after.get(suite_id))}"
        for suite_id in suite_ids
    ]


def _score_change(before: int | None, after: int | None) -> str:
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
