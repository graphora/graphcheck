from __future__ import annotations

import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from graphcheck.contracts.results import Results
from graphcheck.reporting import (
    write_html_report,
    write_results,
)

RenderObserver = Callable[[int, bool], None]


def write_run_artifacts(
    results: Results,
    runs_dir: Path,
    *,
    render_observer: RenderObserver | None = None,
) -> tuple[Path, Path]:
    runs_dir.mkdir(parents=True, exist_ok=True)
    resolved_runs = runs_dir.resolve()
    historical_dir = runs_dir / results.run.id
    if (
        historical_dir.name.casefold() == "latest"
        or historical_dir.resolve().parent != resolved_runs
    ):
        raise ValueError(f"run id cannot be used as an artifact directory: {results.run.id!r}")

    publish_run_directory(
        results,
        historical_dir,
        render_observer=render_observer,
    )

    latest_dir = runs_dir / "latest"

    publish_run_directory(
        results,
        latest_dir,
        render_observer=render_observer,
    )
    return latest_dir / "results.json", latest_dir / "report.html"


def publish_run_directory(
    results: Results,
    directory: Path,
    *,
    render_observer: RenderObserver | None = None,
) -> None:
    """Stage and swap a complete results/report pair without exposing a mixed pair."""

    parent = directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    staging = parent / f".{directory.name}.staging-{token}"
    backup = parent / f".{directory.name}.backup-{token}"
    staging.mkdir()
    previous_moved = False

    try:
        write_results(results, staging / "results.json")

        render_started = time.monotonic()

        try:
            write_html_report(results, staging / "report.html")
        except Exception:
            if render_observer is not None:
                render_observer(
                    max(0, round((time.monotonic() - render_started) * 1000)),
                    False,
                )
            raise

        if render_observer is not None:
            render_observer(
                max(0, round((time.monotonic() - render_started) * 1000)),
                True,
            )

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
