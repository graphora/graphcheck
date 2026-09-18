from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

from graphcheck.contracts.results import Results

RenderObserver = Callable[[int, bool], None]
RenderedArtifacts = tuple[bytes, bytes, bytes]

# Readers, publishers, and retention operations share a non-reentrant lock per runs
# directory. The file lock extends that protocol to separate CLI/MCP processes.
_LATEST_PUBLISH_LOCK = threading.Lock()
_DIRECTORY_LOCKS: dict[Path, threading.Lock] = {}


@contextmanager
def latest_publication_lock(runs_dir: Path) -> Iterator[None]:
    """Coordinate managed report reads and mutations across threads and processes.

    Callers already holding this lock must use unlocked history helpers. Rendering and
    HTTP response transmission stay outside this critical section.
    """
    runs_dir = runs_dir.resolve()
    with _LATEST_PUBLISH_LOCK:
        thread_lock = _DIRECTORY_LOCKS.setdefault(runs_dir, threading.Lock())
    file_lock = FileLock(str(runs_dir / ".latest.lock"))
    with thread_lock, file_lock:
        yield


def render_run_artifacts(
    results: Results,
    *,
    render_observer: RenderObserver | None = None,
    changes: dict[str, object] | None = None,
) -> RenderedArtifacts:
    """Render the results.json, report.html, and summary.json bytes exactly once.

    Rendering once and publishing the bytes to both the history directory and `latest`
    keeps the two directories byte-identical and avoids re-rendering the HTML report twice.
    """
    from graphcheck.reporting.history import report_summary_json
    from graphcheck.reporting.html import render_validated_html_report
    from graphcheck.reporting.writer import validated_results_json

    model, rendered_json = validated_results_json(results)

    render_started = time.monotonic()
    try:
        rendered_html = render_validated_html_report(model)
    except Exception:
        if render_observer is not None:
            render_observer(max(0, round((time.monotonic() - render_started) * 1000)), False)
        raise
    if render_observer is not None:
        render_observer(max(0, round((time.monotonic() - render_started) * 1000)), True)

    rendered_summary = report_summary_json(model, changes=changes)
    return (
        rendered_json.encode("utf-8"),
        rendered_html.encode("utf-8"),
        rendered_summary.encode("utf-8"),
    )


def write_run_artifacts(
    results: Results,
    runs_dir: Path,
    *,
    render_observer: RenderObserver | None = None,
) -> tuple[Path, Path]:
    """Publish a run's history directory and refresh the shared `latest` alias.

    This is the single artifact writer used by both `graphcheck run` and the MCP server
    (through execute_run), so every surface produces identical artifacts: a report_name-based
    history id, an atomically swapped results/report/summary triple, and a serialized `latest`
    refresh.
    """
    from graphcheck.reporting.history import report_name

    runs_dir.mkdir(parents=True, exist_ok=True)
    resolved_runs = runs_dir.resolve()
    results.run.id = report_name(results)
    historical_dir = runs_dir / results.run.id
    if (
        historical_dir.name.casefold() == "latest"
        or historical_dir.resolve().parent != resolved_runs
    ):
        raise ValueError(f"run id cannot be used as an artifact directory: {results.run.id!r}")

    latest_dir = runs_dir / "latest"
    while True:
        with latest_publication_lock(runs_dir):
            results.run.previous_run_id = _previous_run_id(results, runs_dir)
            changes = _run_changes(results, runs_dir)
        artifacts = render_run_artifacts(results, render_observer=render_observer, changes=changes)
        with latest_publication_lock(runs_dir):
            # Another publisher may have finished during rendering; bind to its run before
            # publishing. Existing history keeps its original link for idempotent retries.
            if results.run.previous_run_id != _previous_run_id(results, runs_dir):
                continue
            publish_run_directory(artifacts, historical_dir)
            publish_run_directory(artifacts, latest_dir)
            break
    return latest_dir / "results.json", latest_dir / "report.html"


def _previous_run_id(results: Results, runs_dir: Path) -> str | None:
    """Read lineage while holding the publication lock, without locking again."""
    from graphcheck.reporting.writer import load_results

    if results.run.redaction.applied:
        return None
    existing = runs_dir / results.run.id / "results.json"
    if existing.is_file():
        return load_results(existing).run.previous_run_id
    try:
        previous = load_results(runs_dir / "latest" / "results.json").run
    except (OSError, ValueError):
        return None  # A missing/corrupt latest alias must not prevent publishing a healthy run.
    return previous.previous_run_id if previous.id == results.run.id else previous.id


def _run_changes(results: Results, runs_dir: Path) -> dict[str, object] | None:
    """Bind the optional summary to the same predecessor under the publication lock."""
    from graphcheck.reporting.history import (
        _safe_artifact_file,
        _safe_report_directory,
        report_changes_summary,
    )
    from graphcheck.reporting.writer import load_results

    previous_id = results.run.previous_run_id
    if results.run.redaction.applied or previous_id is None:
        return None
    existing = runs_dir / results.run.id
    if existing.is_dir():
        # Keep retries byte-identical even if the predecessor has since been pruned.
        try:
            saved = json.loads((existing / "summary.json").read_text(encoding="utf-8"))
            changes = saved.get("changes") if isinstance(saved, dict) else None
            return changes if isinstance(changes, dict) else None
        except (OSError, ValueError):
            return None
    for directory in (runs_dir / previous_id, runs_dir / "latest"):
        path = directory / "results.json"
        if not _safe_report_directory(runs_dir.resolve(), directory) or not _safe_artifact_file(
            directory, path
        ):
            continue
        try:
            previous = load_results(path)
        except (OSError, ValueError):
            continue
        if previous.run.id == previous_id:
            return report_changes_summary(previous, results)
    return None


def publish_run_directory(artifacts: RenderedArtifacts, directory: Path) -> None:
    """Stage and swap a complete results/report/summary triple without exposing a mixed set."""

    parent = directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    staging = parent / f".{directory.name}.staging-{token}"
    backup = parent / f".{directory.name}.backup-{token}"
    staging.mkdir()
    previous_moved = False

    try:
        for name, content in zip(
            ("results.json", "report.html", "summary.json"), artifacts, strict=True
        ):
            (staging / name).write_bytes(content)

        if directory.exists():
            if not directory.is_dir() or directory.is_symlink() or directory.is_junction():
                raise OSError(f"refusing to replace linked or non-directory artifact: {directory}")
            if directory.name.casefold() != "latest":
                if all(
                    (directory / name).is_file()
                    and not (directory / name).is_symlink()
                    and (directory / name).read_bytes() == content
                    for name, content in zip(
                        ("results.json", "report.html", "summary.json"), artifacts, strict=True
                    )
                ):
                    return
                raise FileExistsError(f"Historical report already exists: {directory.name}")
            directory.replace(backup)
            previous_moved = True

        staging.replace(directory)

    except Exception:
        if previous_moved and backup.exists():
            if directory.exists():
                shutil.rmtree(directory)
            backup.replace(directory)
        raise

    else:
        if backup.exists():
            shutil.rmtree(backup)

    finally:
        if staging.exists():
            shutil.rmtree(staging)
