from __future__ import annotations

from pathlib import Path

import yaml

from graphcheck.engine.runner import SuiteInput
from graphcheck.errors import GraphCheckError
from graphcheck.packs.graphrag import GRAPHRAG_CHECK_NAMES
from graphcheck.project import ProjectPacksConfig
from graphcheck.yaml_loader import load_yaml_mapping


def load_suite_inputs(
    checks_dir: Path,
    requested_suites: list[str],
    packs: ProjectPacksConfig | None = None,
) -> list[SuiteInput]:
    if not checks_dir.is_dir():
        raise GraphCheckError(
            "run.checks_missing",
            f"Configured checks directory was not found: {checks_dir}",
            "Create the directory or fix `checks` in graphcheck.yml.",
        )
    try:
        paths = sorted(
            path
            for path in checks_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}
        )
    except OSError as exc:
        raise GraphCheckError(
            "run.checks_unreadable",
            f"Could not enumerate check suites in {checks_dir}: {exc}",
            "Check the configured checks path and its filesystem permissions.",
        ) from exc

    loaded: list[SuiteInput] = []
    graphrag = packs.graphrag if packs is not None else None
    model = graphrag.model.model_dump() if graphrag is not None and graphrag.model else {}
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
            if model:
                raw = load_yaml_mapping(text, description="suite")
                bound = False
                for check in raw.get("conformance", []):
                    if check.get("check") in GRAPHRAG_CHECK_NAMES and "with" in check:
                        check["with"] = {**model, **check["with"]}
                        bound = True
                if bound:
                    text = yaml.safe_dump(raw, sort_keys=False)
            loaded.append(SuiteInput.from_yaml(text, source=str(path)))
        except Exception as exc:
            raise GraphCheckError(
                "run.suite_invalid",
                f"Suite {path} is invalid: {type(exc).__name__}: {exc}",
                "Fix the suite YAML and remove unknown keys, then run it again.",
            ) from exc

    if graphrag is not None and graphrag.enabled:
        raw = {
            "suite": "graphrag",
            "defaults": {"tags": ["graphrag"]},
            "conformance": [
                {
                    "id": name,
                    "check": name,
                    "with": {
                        **model,
                        **(
                            graphrag.near_duplicate_entities.model_dump()
                            if name == "near_duplicate_entities"
                            else {}
                        ),
                    },
                }
                for name in GRAPHRAG_CHECK_NAMES
            ],
        }
        loaded.append(SuiteInput.from_yaml(yaml.safe_dump(raw), source="graphcheck.yml"))

    if not requested_suites:
        return loaded
    requested = set(requested_suites)
    return [item for item in loaded if item.suite.suite in requested]
