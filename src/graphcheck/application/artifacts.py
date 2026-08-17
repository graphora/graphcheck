from __future__ import annotations

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

# The `latest` alias is the only artifact target multiple runs contend for (historical run
# directories are unique per run id). MCP 2.0 dispatches synchronous tools through worker
# threads, so two run_suite calls can publish concurrently within one process; separate CLI
# processes can also publish at once. This in-process thread lock is shared by every code
# path that publishes `latest`.
_LATEST_PUBLISH_LOCK = threading.Lock()


@contextmanager
def latest_publication_lock(runs_dir: Path) -> Iterator[None]:
    """Serialize publication of the shared `latest` alias across threads and processes.

    Every writer that swaps `<runs_dir>/latest` must hold this lock so the exists/move/swap
    sequence in publish_run_directory can never interleave with another publisher.
    """
    file_lock = FileLock(str(runs_dir / ".latest.lock"))
    with _LATEST_PUBLISH_LOCK, file_lock:
        yield


def render_run_artifacts(
    results: Results,
    *,
    render_observer: RenderObserver | None = None,
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

    rendered_summary = report_summary_json(model)
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

    artifacts = render_run_artifacts(results, render_observer=render_observer)
    publish_run_directory(artifacts, historical_dir)

    latest_dir = runs_dir / "latest"
    with latest_publication_lock(runs_dir):
        publish_run_directory(artifacts, latest_dir)
    return latest_dir / "results.json", latest_dir / "report.html"


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
            is_junction = getattr(directory, "is_junction", lambda: False)
            if not directory.is_dir() or directory.is_symlink() or is_junction():
                raise OSError(f"refusing to replace linked or non-directory artifact: {directory}")
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
