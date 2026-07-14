from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from graphcheck.contracts.profile import BaselineProfile
from graphcheck.errors import GraphCheckError

BASELINES_DIR = Path(".graphcheck") / "baselines"
CURRENT_BASELINE_FILE = Path(".graphcheck") / "current-baseline.json"
_BASELINE_NAME = re.compile(r"\d{8}T\d{6}\.json")


def write_baseline(profile: BaselineProfile) -> Path:
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    path = BASELINES_DIR / f"{datetime.now(UTC):%Y%m%dT%H%M%S}.json"
    path.write_text(
        profile.model_dump_json(
            by_alias=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def list_baselines() -> list[Path]:
    if not BASELINES_DIR.is_dir():
        return []
    return sorted(
        path
        for path in BASELINES_DIR.iterdir()
        if path.is_file() and _BASELINE_NAME.fullmatch(path.name)
    )


def latest_baseline() -> Path | None:
    baselines = list_baselines()
    return baselines[-1] if baselines else None


def set_current_baseline(filename: str | None = None) -> Path:
    if filename is None:
        selected = latest_baseline()
        if selected is None:
            raise GraphCheckError(
                "baseline.missing",
                "No timestamped baseline snapshots were found.",
                "Run `graphcheck profile` to create a baseline snapshot first.",
            )
    else:
        selected = {path.name: path for path in list_baselines()}.get(filename)
        if selected is None:
            raise GraphCheckError(
                "baseline.not_found",
                f"Baseline snapshot {filename!r} was not found in {BASELINES_DIR}.",
                "Choose an existing timestamped snapshot, or run `graphcheck profile`.",
            )

    CURRENT_BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_BASELINE_FILE.write_text(
        json.dumps({"baseline": selected.name}, indent=2) + "\n",
        encoding="utf-8",
    )
    return selected


def get_current_baseline() -> Path | None:
    if not CURRENT_BASELINE_FILE.exists():
        return None
    try:
        payload = json.loads(CURRENT_BASELINE_FILE.read_text(encoding="utf-8"))
        filename = payload["baseline"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GraphCheckError(
            "baseline.current_invalid",
            f"{CURRENT_BASELINE_FILE} is not valid baseline metadata.",
            "Run `graphcheck baseline set` to select an active baseline again.",
        ) from exc
    if not isinstance(filename, str):
        raise GraphCheckError(
            "baseline.current_invalid",
            f"{CURRENT_BASELINE_FILE} is not valid baseline metadata.",
            "Run `graphcheck baseline set` to select an active baseline again.",
        )
    selected = {path.name: path for path in list_baselines()}.get(filename)
    if selected is None:
        raise GraphCheckError(
            "baseline.current_missing",
            f"The active baseline snapshot {filename!r} does not exist.",
            "Run `graphcheck baseline set` to select an existing baseline.",
        )
    return selected
