"""Offline, deterministic outcome, profile, and bounded evidence comparisons."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from graphcheck.contracts.profile import BaselineProfile
from graphcheck.contracts.results import Evidence
from graphcheck.diff import DiffReport, compare, render_human, render_json
from graphcheck.errors import GraphCheckError
from graphcheck.reporting.history import (
    ReportComparison,
    ReportHistoryError,
    ReportRun,
    _discover_report_runs,
    compare_reports,
    find_report_run,
    format_report_comparison,
)
from graphcheck.reporting.writer import load_results


@dataclass(frozen=True)
class EvidenceDelta:
    suite_id: str
    check_id: str
    appeared: list[dict[str, str]]
    disappeared: list[dict[str, str]]
    cap: int
    dropped: int
    before_truncated: bool
    after_truncated: bool


def _elements(evidence: Evidence | None) -> set[tuple[str, str]]:
    return (
        {(item.kind, item.id) for item in evidence.elements if item.kind != "aggregate"}
        if evidence
        else set()
    )


def compare_evidence(first: ReportRun, second: ReportRun) -> list[EvidenceDelta]:
    """Diff retained element identities, sharing the smaller input cap across directions.

    Truncation in an input means graph-wide changes are unknown; `dropped` counts only
    known differences between the retained evidence, never inferred unseen identities.
    """
    if first.results.run.redaction.applied or second.results.run.redaction.applied:
        return []
    before = {(check.suite_id, check.id): check.evidence for check in first.results.checks}
    after = {(check.suite_id, check.id): check.evidence for check in second.results.checks}
    deltas = []
    for identity in sorted(before.keys() | after.keys()):
        old, new = before.get(identity), after.get(identity)
        sources = [source for source in (old, new) if source is not None]
        if not sources:
            continue
        cap = max(0, min(source.cap for source in sources))
        a, b = _elements(old), _elements(new)
        appeared, disappeared = sorted(b - a), sorted(a - b)
        shown_added = appeared[:cap]
        shown_removed = disappeared[: max(0, cap - len(shown_added))]
        deltas.append(
            EvidenceDelta(
                *identity,
                [{"kind": kind, "id": element_id} for kind, element_id in shown_added],
                [{"kind": kind, "id": element_id} for kind, element_id in shown_removed],
                cap,
                len(appeared) + len(disappeared) - len(shown_added) - len(shown_removed),
                bool(old and old.truncated),
                bool(new and new.truncated),
            )
        )
    return deltas


@dataclass(frozen=True)
class ChangesReport:
    first: ReportRun
    second: ReportRun
    outcomes: ReportComparison
    profile: DiffReport | None
    profile_unavailable: str | None
    evidence: list[EvidenceDelta]

    @property
    def exit_code(self) -> int:
        return int(self.outcomes.regressed)

    def json(self) -> str:
        return json.dumps(
            {
                "schema_version": "1.0",
                "previous_run_id": self.first.id,
                "run_id": self.second.id,
                "regressed": self.outcomes.regressed,
                "outcomes": asdict(self.outcomes),
                "profile": json.loads(render_json(self.profile)) if self.profile else None,
                "profile_unavailable": self.profile_unavailable,
                "evidence": [asdict(delta) for delta in self.evidence],
                "evidence_unavailable": self.evidence_unavailable,
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )

    @property
    def evidence_unavailable(self) -> str | None:
        return (
            "Element IDs are masked in a redacted run."
            if (
                self.first.results.run.redaction.applied
                or self.second.results.run.redaction.applied
            )
            else None
        )

    def text(self) -> str:
        lines = [
            format_report_comparison(self.first, self.second, comparison=self.outcomes),
            "",
            "Profile deltas:",
            render_human(self.profile)
            if self.profile
            else f"  Unavailable: {self.profile_unavailable}",
            "",
            "Evidence deltas (retained element IDs):",
        ]
        if self.evidence_unavailable:
            lines.append(f"  Unavailable: {self.evidence_unavailable}")
        elif not self.evidence:
            lines.append("  none")
        for delta in self.evidence:
            lines.append(
                f"  {delta.suite_id}::{delta.check_id}: "
                f"{len(delta.appeared)} appeared, {len(delta.disappeared)} disappeared; "
                f"{delta.dropped} dropped at cap {delta.cap}"
            )
            for direction, elements in (("+", delta.appeared), ("-", delta.disappeared)):
                lines.extend(f"    {direction} {item['kind']} {item['id']}" for item in elements)
            if delta.before_truncated or delta.after_truncated:
                lines.append("    Input evidence was truncated; graph-wide deltas are unknown.")
        return "\n".join(lines)


def _profile(record: ReportRun, directory: Path) -> BaselineProfile | None:
    reference = record.results.run.baseline_ref
    if reference is None:
        return None
    path = directory / reference
    if (
        Path(reference).name != reference
        or path.is_symlink()
        or path.resolve().parent != directory.resolve()
    ):
        raise ReportHistoryError(f"Invalid baseline_ref in run {record.id!r}.")
    profile = BaselineProfile.model_validate_json(path.read_text(encoding="utf-8"))
    target = record.results.run.target
    if target is not None and target.database != profile.target.database:
        raise ReportHistoryError(f"Run {record.id!r} references a profile for another database.")
    return profile


def _materialize(record: ReportRun) -> ReportRun:
    """Load a selected record under the caller's publication lock."""
    loaded = ReportRun(
        record.directory,
        record.results_path,
        record.report_path,
        results=load_results(record.results_path),
        modified_ns=record.modified_ns,
    )
    if loaded.summary != record.summary:
        raise ReportHistoryError("results.json does not match the selected summary")
    return loaded


def load_changes(runs_dir: Path, since: str = "previous") -> ChangesReport:
    from graphcheck.application.artifacts import latest_publication_lock

    if not runs_dir.is_dir():
        raise ReportHistoryError("At least two runs are required. Run `graphcheck run` twice.")
    with latest_publication_lock(runs_dir):
        records = _discover_report_runs(runs_dir)
        if not records:
            raise ReportHistoryError("No report history found. Run `graphcheck run` twice.")
        latest_path = runs_dir / "latest" / "results.json"
        latest = next((record for record in records if record.directory.name == "latest"), None)
        if (
            latest is None
            and latest_path.is_file()
            and not latest_path.is_symlink()
            and (latest_path.resolve().parent == (runs_dir.resolve() / "latest"))
        ):
            latest = find_report_run(records, load_results(latest_path).run.id)
        second = _materialize(latest or records[0])
        if since == "previous":
            since = second.results.run.previous_run_id
            if since is None and "previous_run_id" not in second.results.run.model_fields_set:
                since = next((record.id for record in records if record.id != second.id), None)
            if since is None:
                raise ReportHistoryError(f"Run {second.id!r} has no previous run.")
        first = _materialize(find_report_run(records, since))
        if first.id == second.id:
            raise ReportHistoryError("--since must identify a run other than the latest run.")
        # Materialize under the publication lock before pruning/deletion can intervene.
        first_results, second_results = first.results, second.results
    targets = (first_results.run.target, second_results.run.target)
    if all(targets) and targets[0].database != targets[1].database:
        raise ReportHistoryError("Cannot compare runs from different databases.")
    before, after = (
        _profile(first, runs_dir.parent / "baselines"),
        _profile(second, runs_dir.parent / "baselines"),
    )
    profile, unavailable = None, None
    if before is None or after is None:
        unavailable = "A run has no baseline_ref; create a profile before each run."
    else:
        try:
            profile = compare(
                before, after, first.results.run.baseline_ref, second.results.run.baseline_ref
            )
        except GraphCheckError as exc:
            unavailable = exc.error.message
    return ChangesReport(
        first,
        second,
        compare_reports(first, second),
        profile,
        unavailable,
        compare_evidence(first, second),
    )
