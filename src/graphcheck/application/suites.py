from __future__ import annotations

from pathlib import Path

from graphcheck.errors import GraphCheckError
from graphcheck.engine.runner import SuiteInput


def load_suite_inputs(checks_dir: Path, requested_suites: list[str]) -> list[SuiteInput]:
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
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
            loaded.append(SuiteInput.from_yaml(text, source=str(path)))
        except Exception as exc:
            raise GraphCheckError(
                "run.suite_invalid",
                f"Suite {path} is invalid: {type(exc).__name__}: {exc}",
                "Fix the suite YAML and remove unknown keys, then run it again.",
            ) from exc

    if not requested_suites:
        return loaded
    requested = set(requested_suites)
    return [item for item in loaded if item.suite.suite in requested]
