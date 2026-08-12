from __future__ import annotations

from dataclasses import asdict
from typing import Any

from graphcheck.application.paths import project_path
from graphcheck.application.run import (
    RunRequest,
    execute_run,
)
from graphcheck.errors import GraphCheckError
from graphcheck.packs.catalog import builtin_pack_catalog
from graphcheck.project import (
    find_project_root,
    load_project_config,
)
from graphcheck.reporting import load_results


def list_checks() -> dict[str, list[dict[str, Any]]]:
    """
    Return all available GraphCheck checks as structured JSON.
    """

    catalog = builtin_pack_catalog()

    checks = []

    for check in catalog.checks.values():
        item = asdict(check)

        item["requires"] = list(item["requires"])
        item["evidence_elements"] = list(item["evidence_elements"])
        item["evidence_id_fields"] = list(item["evidence_id_fields"])

        checks.append(item)

    return {"checks": checks}


def get_results(run_id: str) -> Any:
    """
    Load a GraphCheck results.json file safely.
    """
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise GraphCheckError(
            code="mcp.invalid_run_id",
            message="The supplied run ID is invalid.",
            fix="Provide a run ID returned by GraphCheck without path separators or '..'.",
        )

    root = find_project_root()
    config = load_project_config(root)
    artifacts = project_path(root, config.artifacts)
    runs_dir = (artifacts / "runs").resolve()

    results_path = (runs_dir / run_id / "results.json").resolve()

    try:
        results_path.relative_to(runs_dir)
    except ValueError as exc:
        raise GraphCheckError(
            code="mcp.invalid_run_id",
            message="The supplied run ID resolves outside the GraphCheck runs directory.",
            fix="Provide a valid GraphCheck run ID.",
        ) from exc

    return load_results(results_path)


def run_suite(
    suite: str,
    profile: str | None = None,
) -> Any:
    """
    Run a GraphCheck suite.
    """

    outcome = execute_run(
        RunRequest(
            profile=profile,
            suite_ids=[suite],
            tags=[],
            fail_fast=False,
        )
    )

    return outcome.results
