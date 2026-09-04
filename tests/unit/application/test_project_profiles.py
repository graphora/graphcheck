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
    assert profile.password == "graphora"
    help_text = (tmp_path / "profiles.yml").read_text(encoding="utf-8")
    assert "built-in reader role" in help_text
    assert "neo4j+s://" in help_text


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


def test_project_config_defaults_to_two_workers():
    from graphcheck.project import ProjectConfig

    assert ProjectConfig(project="x", checks="checks", artifacts=".graphcheck").concurrency == 2


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "2"])
def test_project_config_rejects_invalid_concurrency(invalid):
    from graphcheck.project import ProjectConfig

    with pytest.raises(ValidationError, match="concurrency"):
        ProjectConfig(
            project="x",
            checks="checks",
            artifacts=".graphcheck",
            concurrency=invalid,
        )


def test_find_project_root_accepts_file_path(tmp_path: Path):
    write_default_project(tmp_path)
    suite = tmp_path / "checks" / "suite.yml"
    suite.write_text("suite: x\n", encoding="utf-8")

    assert find_project_root(suite) == tmp_path


def test_find_project_root_missing_is_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("graphcheck.project.PROJECT_FILE", f".missing-{tmp_path.name}.yml")

    with pytest.raises(GraphCheckError) as caught:
        find_project_root(tmp_path)

    assert caught.value.error.code == "project.missing"


def test_load_project_config_reads_yaml(tmp_path: Path):
    from graphcheck.project import load_project_config

    write_default_project(tmp_path)

    config = load_project_config(tmp_path)

    assert config.project == "graphcheck"
    assert config.checks == "checks"
    assert config.artifacts == ".graphcheck"
    assert config.concurrency == 2


def test_load_project_config_rejects_invalid_yaml(tmp_path: Path):
    from graphcheck.project import load_project_config

    (tmp_path / "graphcheck.yml").write_text("project: [\n", encoding="utf-8")

    with pytest.raises(GraphCheckError) as caught:
        load_project_config(tmp_path)

    assert caught.value.error.code == "profile.invalid"


def test_gitignore_entries_are_not_duplicated(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("profiles.yml\n.graphcheck/\n", encoding="utf-8")

    ensure_gitignore_entries(tmp_path)

    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "profiles.yml\n.graphcheck/\n"


def test_write_example_suite_does_not_overwrite_existing_file(tmp_path: Path):
    checks = tmp_path / "checks"
    checks.mkdir()
    example = checks / "example.yml"
    example.write_text("suite: custom\n", encoding="utf-8")

    write_example_suite(tmp_path)

    assert example.read_text(encoding="utf-8") == "suite: custom\n"


def test_profile_without_password_or_env_is_loud(tmp_path: Path):
    (tmp_path / "profiles.yml").write_text(
        "default: local\nprofiles:\n  local:\n    uri: bolt://x\n"
        "    user: neo4j\n    database: neo4j\n",
        encoding="utf-8",
    )

    with pytest.raises(GraphCheckError) as caught:
        select_profile(load_profiles(tmp_path))

    assert caught.value.error.code == "profile.password_missing"
    assert "password_env" in caught.value.error.fix


@pytest.mark.parametrize(
    "uri",
    [
        "bolt://localhost:7687",
        "bolt+s://db.example:7687",
        "bolt+ssc://db.example:7687",
        "neo4j://db.example:7687",
        "neo4j+s://db.example:7687",
        "neo4j+ssc://db.example:7687",
    ],
)
def test_profile_accepts_supported_bolt_uri_schemes(tmp_path: Path, uri: str):
    (tmp_path / "profiles.yml").write_text(
        "default: local\nprofiles:\n  local:\n"
        f"    uri: {uri}\n    user: neo4j\n    password: pw\n    database: neo4j\n",
        encoding="utf-8",
    )

    assert select_profile(load_profiles(tmp_path))[1].uri == uri


@pytest.mark.parametrize("uri", ["http://localhost:7474", "localhost:7687", "bolt://"])
def test_profile_rejects_wrong_or_incomplete_uri_with_fix(tmp_path: Path, uri: str):
    (tmp_path / "profiles.yml").write_text(
        "default: local\nprofiles:\n  local:\n"
        f"    uri: {uri}\n    user: neo4j\n    password: pw\n    database: neo4j\n",
        encoding="utf-8",
    )

    with pytest.raises(GraphCheckError) as caught:
        select_profile(load_profiles(tmp_path))

    assert caught.value.error.code == "profile.uri_invalid"
    assert "bolt://" in caught.value.error.fix
    assert "neo4j+s://" in caught.value.error.fix
