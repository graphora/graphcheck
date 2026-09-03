import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from graphcheck.baselines import (
    get_current_baseline,
    latest_baseline,
    list_baselines,
    resolve_diff_baselines,
    set_current_baseline,
    write_baseline,
)
from graphcheck.contracts.profile import BaselineProfile
from graphcheck.errors import GraphCheckError

FIXTURE = Path(__file__).parents[1] / "contracts" / "fixtures" / "baseline.json"


def _profile() -> BaselineProfile:
    return BaselineProfile.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def test_write_baseline_creates_timestamped_canonical_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    profile = _profile()

    path = write_baseline(profile)

    assert path.parent == tmp_path / ".graphcheck" / "baselines"
    assert re.fullmatch(r"\d{8}T\d{6}\.\d{6}\.json", path.name)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == profile.model_dump_json(
        by_alias=True,
        indent=2,
    )
    assert json.loads(path.read_text(encoding="utf-8"))["schema"]


def test_write_baseline_does_not_overwrite_snapshot_from_same_instant(
    tmp_path, monkeypatch
) -> None:
    fixed = datetime(2026, 7, 21, 12, 34, 56, 123456, tzinfo=UTC)

    class FixedDateTime:
        @classmethod
        def now(cls, timezone):
            assert timezone is UTC
            return fixed

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    monkeypatch.setattr("graphcheck.baselines.datetime", FixedDateTime)
    profile = _profile()

    first = write_baseline(profile)
    second = write_baseline(profile)

    assert first.name == "20260721T123456.123456.json"
    assert second.name == "20260721T123456.123457.json"
    assert first != second
    assert first.exists()
    assert second.exists()
    assert len(list_baselines()) == 2
    assert first.read_text(encoding="utf-8") == profile.model_dump_json(
        by_alias=True,
        indent=2,
    )


def _snapshot(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text("{}", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("filenames", "expected_current"),
    [
        (["20260716T170000.json"], "20260716T170000.json"),
        (
            ["20260716T170000.json", "20260716T171000.json"],
            "20260716T170000.json",
        ),
        (
            [
                "20260716T170000.json",
                "20260716T171000.json",
                "20260716T172000.json",
            ],
            "20260716T171000.json",
        ),
    ],
)
def test_set_current_baseline_selects_previous_snapshot_by_default(
    tmp_path,
    monkeypatch,
    filenames: list[str],
    expected_current: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    directory = tmp_path / ".graphcheck" / "baselines"
    snapshots = [_snapshot(directory, filename) for filename in filenames]
    _snapshot(directory, "notes.json")

    selected = set_current_baseline()

    assert [path.name for path in list_baselines()] == filenames
    assert latest_baseline() == snapshots[-1]
    assert selected == directory / expected_current
    assert json.loads(Path(".graphcheck/current-baseline.json").read_text(encoding="utf-8")) == {
        "baseline": expected_current
    }
    assert get_current_baseline() == selected


def test_set_current_baseline_selects_specific_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    directory = tmp_path / ".graphcheck" / "baselines"
    requested = _snapshot(directory, "20260714T120000.json")
    _snapshot(directory, "20260714T143522.json")

    selected = set_current_baseline("20260714T120000.json")

    assert selected.resolve() == requested
    assert json.loads(Path(".graphcheck/current-baseline.json").read_text(encoding="utf-8")) == {
        "baseline": "20260714T120000.json"
    }


def test_set_current_baseline_rejects_missing_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)

    with pytest.raises(GraphCheckError, match="No timestamped baseline snapshots") as caught:
        set_current_baseline()

    assert caught.value.error.code == "baseline.missing"


def test_set_current_baseline_rejects_missing_requested_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    _snapshot(
        tmp_path / ".graphcheck" / "baselines",
        "20260714T120000.json",
    )

    with pytest.raises(GraphCheckError, match="was not found") as caught:
        set_current_baseline("20260714T143522.json")

    assert caught.value.error.code == "baseline.not_found"
    assert not Path(".graphcheck/current-baseline.json").exists()


def test_baseline_paths_use_discovered_project_root(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    nested = project_root / "nested" / "directory"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: project_root)

    written = write_baseline(_profile())
    selected = set_current_baseline(written.name)

    assert written.parent == project_root / ".graphcheck" / "baselines"
    assert not (nested / ".graphcheck").exists()
    assert (project_root / ".graphcheck" / "current-baseline.json").exists()
    assert get_current_baseline() == selected


def test_resolve_diff_baselines_uses_previous_and_latest(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    directory = tmp_path / ".graphcheck" / "baselines"
    previous = _snapshot(directory, "20260714T120000.json")
    latest = _snapshot(directory, "20260714T143522.json")

    assert resolve_diff_baselines() == (previous, latest)


def test_resolve_diff_baselines_honours_current_selection(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    directory = tmp_path / ".graphcheck" / "baselines"
    selected = _snapshot(directory, "20260710T120000.json")
    _snapshot(directory, "20260714T120000.json")
    latest = _snapshot(directory, "20260714T143522.json")
    set_current_baseline(selected.name)

    assert resolve_diff_baselines() == (selected, latest)


def test_resolve_diff_baselines_resolves_explicit_names_and_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    directory = tmp_path / ".graphcheck" / "baselines"
    current = _snapshot(directory, "20260714T120000.json")
    latest = tmp_path / "latest.json"
    latest.write_text("{}", encoding="utf-8")

    assert resolve_diff_baselines(current.name, str(latest)) == (current, latest)


def test_resolve_diff_baselines_requires_two_snapshots(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    _snapshot(tmp_path / ".graphcheck" / "baselines", "20260714T120000.json")

    with pytest.raises(GraphCheckError) as caught:
        resolve_diff_baselines()

    assert caught.value.error.code == "baseline.missing"
