import tomllib
from pathlib import Path


def test_optional_feature_dependencies_are_not_in_the_base_install():
    project = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())

    dependencies = project["project"]["dependencies"]
    assert not any(dependency.startswith(("instructor", "mcp")) for dependency in dependencies)
    assert project["project"]["optional-dependencies"] == {
        "generate": ["instructor[anthropic,google-genai]==1.15.4"],
        "mcp": ["mcp>=2.0.0,<3"],
    }
    assert {"instructor[anthropic,google-genai]==1.15.4", "mcp>=2.0.0,<3"} <= set(
        project["dependency-groups"]["dev"]
    )
