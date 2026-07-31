from __future__ import annotations

from dataclasses import asdict
from typing import Any

from graphcheck.application.paths import project_path
from graphcheck.application.run import (
    RunRequest,
    execute_run,
)
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
    Load a GraphCheck results.json file.
    """
    root = find_project_root()
    config = load_project_config(root)
    artifacts = project_path(root, config.artifacts)

    results_path = artifacts / "runs" / run_id / "results.json"

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
