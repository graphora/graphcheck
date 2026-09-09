from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StrictInt,
    ValidationError,
    field_validator,
)

from graphcheck.errors import GraphCheckError
from graphcheck.generation.config import GenerateConfig
from graphcheck.yaml_loader import load_yaml_mapping

PROJECT_FILE = "graphcheck.yml"
PROFILES_FILE = "profiles.yml"
CHECKS_DIR = "checks"
ARTIFACTS_DIR = ".graphcheck"


class ProjectEngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    result_row_limit: Annotated[StrictInt, Field(gt=0)] = 100_000


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    project: str
    checks: str
    artifacts: str
    concurrency: PositiveInt = 2
    engine: ProjectEngineConfig = Field(default_factory=ProjectEngineConfig)
    generate: GenerateConfig | None = None

    @field_validator("concurrency", mode="before")
    @classmethod
    def _positive_integer_concurrency(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("concurrency must be a positive integer")
        return value


def default_project_config() -> ProjectConfig:
    return ProjectConfig(
        project="graphcheck",
        checks=CHECKS_DIR,
        artifacts=ARTIFACTS_DIR,
        concurrency=2,
    )


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for path in (current, *current.parents):
        if (path / PROJECT_FILE).exists():
            return path
    raise GraphCheckError(
        "project.missing",
        "graphcheck.yml was not found.",
        "Run `graphcheck init` from the project directory first.",
    )


def load_project_config(root: Path) -> ProjectConfig:
    path = root / PROJECT_FILE
    try:
        raw = load_yaml_mapping(path.read_text(encoding="utf-8"), description="graphcheck.yml")
        return ProjectConfig.model_validate(raw)
    except (OSError, yaml.YAMLError, ValueError, TypeError) as exc:
        detail = (
            str(exc)
            if isinstance(exc, ValidationError)
            else "Use valid UTF-8 YAML with unique mapping keys."
        )
        raise GraphCheckError(
            "profile.invalid",
            f"Invalid graphcheck.yml: {detail}",
            "Fix graphcheck.yml, then run `graphcheck debug` again.",
        ) from exc if isinstance(exc, ValidationError) else None


def write_default_project(root: Path) -> None:
    config = default_project_config()
    (root / PROJECT_FILE).write_text(
        yaml.safe_dump(config.model_dump(exclude_none=True), sort_keys=False),
        encoding="utf-8",
    )
    (root / config.checks).mkdir(exist_ok=True)
    (root / config.artifacts).mkdir(exist_ok=True)


def ensure_gitignore_entries(root: Path) -> None:
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    entries = ["profiles.yml", ".graphcheck/"]
    missing = [entry for entry in entries if entry not in existing.splitlines()]
    if not missing:
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    block = prefix + "\n".join(missing) + "\n"
    path.write_text(existing + block, encoding="utf-8")


def write_example_suite(root: Path) -> None:
    path = root / CHECKS_DIR / "example.yml"
    if path.exists():
        return
    path.write_text(
        """suite: example
defaults: { severity: error, tags: [example] }

conformance:
  - id: customer-name-present
    check: completeness
    with: { label: Customer, property: name }

competency:
  - id: customers-can-be-counted
    question: "Can customers be counted?"
    query: "MATCH (c:Customer) RETURN count(c) AS count"
    expect: { rows: { min: 1 }, columns: [count] }

# Drift checks require a baseline. Create one with `graphcheck profile`,
# then uncomment the drift check below.
#
# drift:
#   - id: customer-count-stable
#     metric: node_count
#     target: { label: Customer }
#     tolerance: { max_drop_pct: 10 }
#     severity: warn
""",
        encoding="utf-8",
    )
