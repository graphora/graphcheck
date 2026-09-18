import tomllib
from pathlib import Path


def test_optional_feature_dependencies_are_not_in_the_base_install():
    project = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())

    dependencies = project["project"]["dependencies"]
    assert not any(dependency.startswith(("instructor", "mcp")) for dependency in dependencies)
    extras = project["project"]["optional-dependencies"]
    assert any(dependency.startswith("instructor[") for dependency in extras["generate"])
    assert any(dependency.startswith("mcp") for dependency in extras["mcp"])
    assert {dependency for extra in extras.values() for dependency in extra} <= set(
        project["dependency-groups"]["dev"]
    )
