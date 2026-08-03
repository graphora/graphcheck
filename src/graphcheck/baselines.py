from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from graphcheck.contracts.profile import BaselineProfile
from graphcheck.errors import GraphCheckError
from graphcheck.project import find_project_root

_BASELINE_NAME = re.compile(r"\d{8}T\d{6}(?:\.\d{6})?\.json")


def baseline_directory(
    project_root: Path | None = None,
    artifacts: str | Path = ".graphcheck",
) -> Path:
    """Return the configured timestamped-baseline directory."""

    root = find_project_root() if project_root is None else project_root
    if project_root is None and Path(artifacts) == Path(".graphcheck"):
        artifacts = _discovered_artifacts(root)
    configured = Path(artifacts)
    artifacts_dir = configured if configured.is_absolute() else root / configured
    return artifacts_dir / "baselines"


def current_baseline_file(
    project_root: Path | None = None,
    artifacts: str | Path = ".graphcheck",
) -> Path:
    root = find_project_root() if project_root is None else project_root
    if project_root is None and Path(artifacts) == Path(".graphcheck"):
        artifacts = _discovered_artifacts(root)
    configured = Path(artifacts)
    artifacts_dir = configured if configured.is_absolute() else root / configured
    return artifacts_dir / "current-baseline.json"


def _discovered_artifacts(root: Path) -> str | Path:
    """Use configured artifacts when a real project file exists; aid legacy callers/tests."""

    from graphcheck.project import PROJECT_FILE, load_project_config

    if not (root / PROJECT_FILE).is_file():
        return ".graphcheck"
    return load_project_config(root).artifacts


def write_baseline(
    profile: BaselineProfile,
    project_root: Path | None = None,
    artifacts: str | Path = ".graphcheck",
) -> Path:
    baselines_dir = baseline_directory(project_root, artifacts)
    baselines_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC)
    content = profile.model_dump_json(
        by_alias=True,
        indent=2,
    )
    while True:
        path = baselines_dir / f"{timestamp:%Y%m%dT%H%M%S.%f}.json"
        try:
            with path.open("x", encoding="utf-8") as snapshot:
                snapshot.write(content)
            return path
        except FileExistsError:
            timestamp += timedelta(microseconds=1)


def list_baselines(
    project_root: Path | None = None,
    artifacts: str | Path = ".graphcheck",
) -> list[Path]:
    baselines_dir = baseline_directory(project_root, artifacts)
    if not baselines_dir.is_dir():
        return []
    return sorted(
        path
        for path in baselines_dir.iterdir()
        if path.is_file() and _BASELINE_NAME.fullmatch(path.name)
    )


def latest_baseline(
    project_root: Path | None = None,
    artifacts: str | Path = ".graphcheck",
) -> Path | None:
    baselines = list_baselines(project_root, artifacts)
    return baselines[-1] if baselines else None


def set_current_baseline(
    filename: str | None = None,
    project_root: Path | None = None,
    artifacts: str | Path = ".graphcheck",
) -> Path:
    baselines_dir = baseline_directory(project_root, artifacts)
    baselines = list_baselines(project_root, artifacts)

    if filename is None:
        if not baselines:
            raise GraphCheckError(
                "baseline.missing",
                "No timestamped baseline snapshots were found.",
                "Run `graphcheck profile` to create a baseline snapshot first.",
            )

        # Default to the previous run.
        # If only one baseline exists, use that.
        selected = baselines[0] if len(baselines) == 1 else baselines[-2]

    else:
        selected = {path.name: path for path in baselines}.get(filename)
        if selected is None:
            raise GraphCheckError(
                "baseline.not_found",
                f"Baseline snapshot {filename!r} was not found in {baselines_dir}.",
                "Choose an existing timestamped snapshot, or run `graphcheck profile`.",
            )

    selected_file = current_baseline_file(project_root, artifacts)
    selected_file.parent.mkdir(parents=True, exist_ok=True)
    selected_file.write_text(
        json.dumps({"baseline": selected.name}, indent=2) + "\n",
        encoding="utf-8",
    )

    return selected


def get_current_baseline(
    project_root: Path | None = None,
    artifacts: str | Path = ".graphcheck",
) -> Path | None:
    selected_file = current_baseline_file(project_root, artifacts)
    if not selected_file.exists():
        return None
    try:
        payload = json.loads(selected_file.read_text(encoding="utf-8"))
        filename = payload["baseline"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GraphCheckError(
            "baseline.current_invalid",
            f"{selected_file} is not valid baseline metadata.",
            "Run `graphcheck baseline set` to select an active baseline again.",
        ) from exc
    if not isinstance(filename, str):
        raise GraphCheckError(
            "baseline.current_invalid",
            f"{selected_file} is not valid baseline metadata.",
            "Run `graphcheck baseline set` to select an active baseline again.",
        )
    baselines = list_baselines(project_root, artifacts)
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
    *,
    project_root: Path | None = None,
    artifacts: str | Path = ".graphcheck",
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
            _resolve_baseline_path(
                current_baseline_name,
                project_root=project_root,
                artifacts=artifacts,
            ),
            _resolve_baseline_path(
                latest_baseline_name,
                project_root=project_root,
                artifacts=artifacts,
            ),
        )

    baselines = list_baselines(project_root, artifacts)
    if len(baselines) < 2:
        raise GraphCheckError(
            "baseline.missing",
            "At least two timestamped baseline snapshots are required for a diff.",
            "Run `graphcheck profile` at least twice to create baseline snapshots.",
        )

    current_baseline = get_current_baseline(project_root, artifacts)
    if current_baseline is None:
        current_baseline = baselines[-2]
    return current_baseline, baselines[-1]


def _resolve_baseline_path(
    name: str,
    *,
    project_root: Path | None = None,
    artifacts: str | Path = ".graphcheck",
) -> Path:
    requested = Path(name)
    if requested.is_file():
        return requested

    selected = {path.name: path for path in list_baselines(project_root, artifacts)}.get(name)
    if selected is not None:
        return selected

    raise GraphCheckError(
        "baseline.not_found",
        f"Baseline snapshot {name!r} was not found.",
        "Choose an existing timestamped snapshot or valid baseline file path.",
    )
