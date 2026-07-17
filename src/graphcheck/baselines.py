from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from graphcheck.contracts.profile import BaselineProfile
from graphcheck.errors import GraphCheckError
from graphcheck.project import find_project_root

_BASELINE_NAME = re.compile(r"\d{8}T\d{6}\.json")


def _baselines_dir() -> Path:
    return find_project_root() / ".graphcheck" / "baselines"


def _current_baseline_file() -> Path:
    return find_project_root() / ".graphcheck" / "current-baseline.json"


def write_baseline(profile: BaselineProfile) -> Path:
    baselines_dir = _baselines_dir()
    baselines_dir.mkdir(parents=True, exist_ok=True)
    path = baselines_dir / f"{datetime.now(UTC):%Y%m%dT%H%M%S}.json"
    path.write_text(
        profile.model_dump_json(
            by_alias=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def list_baselines() -> list[Path]:
    baselines_dir = _baselines_dir()
    if not baselines_dir.is_dir():
        return []
    return sorted(
        path
        for path in baselines_dir.iterdir()
        if path.is_file() and _BASELINE_NAME.fullmatch(path.name)
    )


def latest_baseline() -> Path | None:
    baselines = list_baselines()
    return baselines[-1] if baselines else None


def set_current_baseline(filename: str | None = None) -> Path:
    baselines_dir = _baselines_dir()
    baselines = list_baselines()

    if filename is None:
        if not baselines:
            raise GraphCheckError(
                "baseline.missing",
                "No timestamped baseline snapshots were found.",
                "Run `graphcheck profile` to create a baseline snapshot first.",
            )

        # Default to the previous run.
        # If only one baseline exists, use that.
        if len(baselines) == 1:
            selected = baselines[0]
        else:
            selected = baselines[-2]

    else:
        selected = {path.name: path for path in baselines}.get(filename)
        if selected is None:
            raise GraphCheckError(
                "baseline.not_found",
                f"Baseline snapshot {filename!r} was not found in {baselines_dir}.",
                "Choose an existing timestamped snapshot, or run `graphcheck profile`.",
            )

    current_baseline_file = _current_baseline_file()
    current_baseline_file.parent.mkdir(parents=True, exist_ok=True)
    current_baseline_file.write_text(
        json.dumps({"baseline": selected.name}, indent=2) + "\n",
        encoding="utf-8",
    )

    return selected

def get_current_baseline() -> Path | None:
    current_baseline_file = _current_baseline_file()
    if not current_baseline_file.exists():
        return None
    try:
        payload = json.loads(current_baseline_file.read_text(encoding="utf-8"))
        filename = payload["baseline"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GraphCheckError(
            "baseline.current_invalid",
            f"{current_baseline_file} is not valid baseline metadata.",
            "Run `graphcheck baseline set` to select an active baseline again.",
        ) from exc
    if not isinstance(filename, str):
        raise GraphCheckError(
            "baseline.current_invalid",
            f"{current_baseline_file} is not valid baseline metadata.",
            "Run `graphcheck baseline set` to select an active baseline again.",
        )
    baselines = list_baselines()
    selected = {path.name: path for path in baselines}.get(filename)
    if selected is None:
        raise GraphCheckError(
            "baseline.current_missing",
            f"The active baseline snapshot {filename!r} does not exist.",
            "Run `graphcheck baseline set` to select an existing baseline.",
        )
    return selected


def resolve_diff_baselines(
    current_baseline_name: str | None = None,
    latest_baseline_name: str | None = None,
) -> tuple[Path, Path]:
    """Resolve the Current and Latest Baseline snapshots for a diff."""
    if (current_baseline_name is None) != (latest_baseline_name is None):
        raise GraphCheckError(
            "baseline.not_found",
            "Both Current Baseline and Latest Baseline must be specified together.",
            "Provide two baseline snapshots, or omit both to use the defaults.",
        )

    if current_baseline_name is not None and latest_baseline_name is not None:
        return (
            _resolve_baseline_path(current_baseline_name),
            _resolve_baseline_path(latest_baseline_name),
        )

    baselines = list_baselines()
    if len(baselines) < 2:
        raise GraphCheckError(
            "baseline.missing",
            "At least two timestamped baseline snapshots are required for a diff.",
            "Run `graphcheck profile` at least twice to create baseline snapshots.",
        )

    current_baseline = get_current_baseline()
    if current_baseline is None:
        current_baseline = baselines[-2]
    return current_baseline, baselines[-1]


def _resolve_baseline_path(name: str) -> Path:
    requested = Path(name)
    if requested.is_file():
        return requested

    selected = {path.name: path for path in list_baselines()}.get(name)
    if selected is not None:
        return selected

    raise GraphCheckError(
        "baseline.not_found",
        f"Baseline snapshot {name!r} was not found.",
        "Choose an existing timestamped snapshot or valid baseline file path.",
    )
