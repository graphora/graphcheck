from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from graphcheck.contracts.results import Results
from graphcheck.reporting import (
    write_html_report,
    write_results,
)


def write_run_artifacts(results: Results, runs_dir: Path) -> tuple[Path, Path]:
    runs_dir.mkdir(parents=True, exist_ok=True)
    resolved_runs = runs_dir.resolve()
    historical_dir = runs_dir / results.run.id
    if (
        historical_dir.name.casefold() == "latest"
        or historical_dir.resolve().parent != resolved_runs
    ):
        raise ValueError(f"run id cannot be used as an artifact directory: {results.run.id!r}")

    publish_run_directory(results, historical_dir)
    latest_dir = runs_dir / "latest"
    publish_run_directory(results, latest_dir)
    return latest_dir / "results.json", latest_dir / "report.html"


def publish_run_directory(results: Results, directory: Path) -> None:
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
        write_html_report(results, staging / "report.html")

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
