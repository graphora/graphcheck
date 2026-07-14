import json
import re
from pathlib import Path

import pytest

from graphcheck.baselines import (
    get_current_baseline,
    latest_baseline,
    list_baselines,
    set_current_baseline,
    write_baseline,
)
from graphcheck.contracts.profile import BaselineProfile
from graphcheck.errors import GraphCheckError

FIXTURE = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"


def _profile() -> BaselineProfile:
    return BaselineProfile.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def test_write_baseline_creates_timestamped_canonical_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    profile = _profile()

    path = write_baseline(profile)

    assert path.parent == Path(".graphcheck/baselines")
    assert re.fullmatch(r"\d{8}T\d{6}\.json", path.name)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == profile.model_dump_json(
        by_alias=True,
        indent=2,
    )
    assert json.loads(path.read_text(encoding="utf-8"))["schema"]


def _snapshot(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text("{}", encoding="utf-8")
    return path


def test_set_current_baseline_selects_latest_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / ".graphcheck" / "baselines"
    _snapshot(directory, "20260714T120000.json")
    latest = _snapshot(directory, "20260714T143522.json")
    _snapshot(directory, "notes.json")

    selected = set_current_baseline()

    assert [path.name for path in list_baselines()] == [
        "20260714T120000.json",
        "20260714T143522.json",
    ]
    assert latest_baseline() == Path(".graphcheck/baselines/20260714T143522.json")
    assert selected == Path(".graphcheck/baselines/20260714T143522.json")
    assert selected.resolve() == latest
    assert json.loads(Path(".graphcheck/current-baseline.json").read_text(encoding="utf-8")) == {
        "baseline": "20260714T143522.json"
    }
    assert get_current_baseline() == selected


def test_set_current_baseline_selects_specific_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
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

    with pytest.raises(GraphCheckError, match="No timestamped baseline snapshots") as caught:
        set_current_baseline()

    assert caught.value.error.code == "baseline.missing"


def test_set_current_baseline_rejects_missing_requested_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _snapshot(
        tmp_path / ".graphcheck" / "baselines",
        "20260714T120000.json",
    )

    with pytest.raises(GraphCheckError, match="was not found") as caught:
        set_current_baseline("20260714T143522.json")

    assert caught.value.error.code == "baseline.not_found"
    assert not Path(".graphcheck/current-baseline.json").exists()
