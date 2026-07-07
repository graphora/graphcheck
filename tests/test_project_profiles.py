from pathlib import Path

import pytest
from pydantic import ValidationError

from graphcheck.connection_profiles import load_profiles, select_profile, write_default_profiles
from graphcheck.errors import GraphCheckError
from graphcheck.project import (
    ensure_gitignore_entries,
    find_project_root,
    write_default_project,
    write_example_suite,
)


def test_project_root_discovered_from_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    write_default_project(tmp_path)
    child = tmp_path / "checks"
    monkeypatch.chdir(child)

    assert find_project_root() == tmp_path


def test_profiles_default_to_local(tmp_path: Path):
    write_default_profiles(tmp_path)

    profiles = load_profiles(tmp_path)
    name, profile = select_profile(profiles)

    assert name == "local"
    assert profile.uri == "bolt://localhost:7687"
    assert profile.password == "neo4j"


def test_missing_profiles_is_loud(tmp_path: Path):
    with pytest.raises(GraphCheckError) as caught:
        load_profiles(tmp_path)

    assert caught.value.error.code == "profile.missing"


def test_unknown_profile_is_loud(tmp_path: Path):
    write_default_profiles(tmp_path)
    profiles = load_profiles(tmp_path)

    with pytest.raises(GraphCheckError) as caught:
        select_profile(profiles, "missing")

    assert caught.value.error.code == "profile.not_found"


def test_profile_rejects_unknown_keys(tmp_path: Path):
    (tmp_path / "profiles.yml").write_text(
        "default: local\nprofiles:\n  local:\n    uri: bolt://x\n    user: neo4j\n"
        "    password: neo4j\n    database: neo4j\n    bogus: true\n",
        encoding="utf-8",
    )

    with pytest.raises(GraphCheckError) as caught:
        load_profiles(tmp_path)

    assert caught.value.error.code == "profile.invalid"


def test_password_env_overrides_literal_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NEO4J_PASSWORD", "from-env")
    (tmp_path / "profiles.yml").write_text(
        "default: local\nprofiles:\n  local:\n    uri: bolt://x\n    user: neo4j\n"
        "    password: literal\n    password_env: NEO4J_PASSWORD\n    database: neo4j\n",
        encoding="utf-8",
    )

    _, profile = select_profile(load_profiles(tmp_path))

    assert profile.password == "from-env"
    assert profile.password_env is None


def test_password_env_falls_back_to_literal_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    (tmp_path / "profiles.yml").write_text(
        "default: local\nprofiles:\n  local:\n    uri: bolt://x\n    user: neo4j\n"
        "    password: literal\n    password_env: NEO4J_PASSWORD\n    database: neo4j\n",
        encoding="utf-8",
    )

    _, profile = select_profile(load_profiles(tmp_path))

    assert profile.password == "literal"
    assert profile.password_env is None


def test_password_env_without_literal_password_is_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    (tmp_path / "profiles.yml").write_text(
        "default: local\nprofiles:\n  local:\n    uri: bolt://x\n    user: neo4j\n"
        "    password_env: NEO4J_PASSWORD\n    database: neo4j\n",
        encoding="utf-8",
    )

    with pytest.raises(GraphCheckError) as caught:
        select_profile(load_profiles(tmp_path))

    assert caught.value.error.code == "profile.password_missing"


def test_init_writes_expected_files(tmp_path: Path):
    write_default_project(tmp_path)
    write_default_profiles(tmp_path)
    write_example_suite(tmp_path)
    ensure_gitignore_entries(tmp_path)

    assert (tmp_path / "graphcheck.yml").exists()
    assert (tmp_path / "profiles.yml").exists()
    assert (tmp_path / "checks" / "example.yml").exists()
    assert (tmp_path / ".graphcheck").is_dir()
    assert "profiles.yml" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_project_config_rejects_unknown_keys():
    from graphcheck.project import ProjectConfig

    with pytest.raises(ValidationError):
        ProjectConfig(project="x", checks="checks", artifacts=".graphcheck", bogus=True)
